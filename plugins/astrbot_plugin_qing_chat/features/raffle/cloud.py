"""One-way replication to a dedicated Tencent Docs worksheet via official MCP."""

import asyncio
import json
import math
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from .store import ZONE, display_time

SHEET_NAME = "bot 抽奖记录"
HEADERS = ("发起人", "内容", "获奖人", "截止时间", "参与通道（请填写群常用网名）")
LEGACY_HEADERS = (
    "流水号",
    "时间（北京时间）",
    "群编号（脱敏）",
    "抽奖编号",
    "动作",
    "昵称",
    "成员编号（脱敏）",
    "详情",
)


def document_id(url):
    parts = urlsplit(url)
    match = re.fullmatch(r"/sheet/([A-Za-z0-9_-]+)", parts.path)
    if parts.scheme != "https" or parts.netloc != "docs.qq.com" or not match:
        raise ValueError("请填写腾讯文档在线表格链接。")
    return match[1]


def group_documents(entries):
    if not isinstance(entries, list):
        raise ValueError("群聊文档绑定应为列表")
    bindings, documents = {}, set()
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError("绑定格式为：群号=腾讯文档链接")
        group, separator, url = entry.partition("=")
        group = group.strip()
        if not separator or not re.fullmatch(r"[1-9][0-9]{4,14}", group):
            raise ValueError("绑定格式为：群号=腾讯文档链接")
        file_id = document_id(url.strip())
        if group in bindings or file_id in documents:
            raise ValueError("每个群号和文档只能绑定一次")
        bindings[group] = file_id
        documents.add(file_id)
    return bindings


def parse_result(result):
    if result.isError:
        raise RuntimeError("Tencent Docs rejected the MCP tool call")
    payload = result.structuredContent
    if payload is None:
        texts = [part.text for part in result.content if part.type == "text"]
        if len(texts) != 1:
            raise ValueError("Unsupported Tencent Docs MCP response")
        payload = json.loads(texts[0])
    if (
        not isinstance(payload, dict)
        or payload.get("error")
        or payload.get("code") not in (None, 0, "0")
    ):
        raise RuntimeError("Tencent Docs returned an error")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Unsupported Tencent Docs result payload")
    return data


class MCPUnavailable(RuntimeError):
    pass


class AstrBotSheetTools:
    """Call the configured server through AstrBot's existing MCP client."""

    def __init__(self, context, server_name):
        self.context = context
        self.server_name = server_name

    async def __call__(self, tool, arguments):
        client = self.context.get_llm_tool_manager().mcp_client_dict.get(
            self.server_name
        )
        if client is None or client.session is None:
            raise MCPUnavailable("腾讯文档 MCP 尚未连接")
        available = {item.name for item in client.tools}
        name = next(
            (
                candidate
                for candidate in (tool, f"sheet.{tool}")
                if candidate in available
            ),
            None,
        )
        if name is None:
            raise MCPUnavailable("该 MCP 服务缺少在线表格工具")
        timeout = timedelta(seconds=15)
        try:
            if tool in {"add_sheet", "insert_dimension"}:
                # These writes are not idempotent. Let the next poll read the
                # remote state before deciding whether to make another request.
                result = await client.session.call_tool(
                    name=name, arguments=arguments, read_timeout_seconds=timeout
                )
            else:
                result = await client.call_tool_with_reconnect(name, arguments, timeout)
        except Exception as exc:
            message = getattr(getattr(exc, "error", None), "message", "")
            if (
                "6086105" in message
                or "没有查看权限" in message
                or "没有编辑权限" in message
            ):
                raise MCPUnavailable("腾讯文档账号没有目标表格权限") from exc
            raise
        return parse_result(result)


class CloudLedger:
    def __init__(self, store, group, file_id, call):
        self.store = store
        self.group = group
        self.file_id = file_id
        self.call = call
        self.namespace = f"tencent:group:{group}:{file_id}:"
        self.marker = SHEET_NAME + "·" + self.store.anonymous("sheet-owner:" + group)

    async def setting(self, name):
        return await asyncio.to_thread(self.store.setting, self.namespace + name)

    async def save(self, name, value):
        await asyncio.to_thread(
            self.store.save_setting, self.namespace + name, str(value)
        )

    async def read(self, call, sheet, start, end, last_col):
        result = await call(
            "get_cell_data",
            {
                "file_id": self.file_id,
                "sheet_id": sheet,
                "start_row": start,
                "end_row": end,
                "start_col": 0,
                "end_col": last_col,
                "return_csv": False,
            },
        )
        cells = result.get("cells", [])
        if not isinstance(cells, list):
            raise ValueError("Unsupported Tencent Docs cell data")
        return {(cell["row"], cell["col"]): cell for cell in cells}

    async def write(self, call, sheet, values):
        for start in range(0, len(values), 400):
            await call(
                "set_range_value",
                {
                    "file_id": self.file_id,
                    "sheet_id": sheet,
                    "values": values[start : start + 400],
                },
            )

    @staticmethod
    def cell(row, col, value):
        if isinstance(value, str):
            return {
                "row": row,
                "col": col,
                "value_type": "STRING",
                "string_value": value,
            }
        return {"row": row, "col": col, "value_type": "NUMBER", "number_value": value}

    @staticmethod
    def matches(cell, value):
        if isinstance(value, str):
            return (
                cell.get("value_type") == "STRING" and cell.get("string_value") == value
            )
        return cell.get("value_type") == "NUMBER" and math.isclose(
            cell.get("number_value", -1), value, rel_tol=0, abs_tol=1e-8
        )

    @staticmethod
    def populated(cell):
        return bool(cell.get("string_value")) or cell.get("value_type") not in (
            None,
            "",
            "STRING",
        )

    async def ensure_sheet(self, call):
        info = await call("get_sheet_info", {"file_id": self.file_id})
        sheets = info["sheets"]
        saved = await self.setting("sheet_id")
        selected = next(
            (item for item in sheets if item["sheet_name"] == SHEET_NAME), None
        )
        if saved and (selected is None or selected["sheet_id"] != saved):
            raise MCPUnavailable(f"找不到原来的「{SHEET_NAME}」标签页")
        if selected is None:
            created = await call(
                "add_sheet",
                {"file_id": self.file_id, "name": SHEET_NAME, "append_index": True},
            )
            selected = {"sheet_id": created["sheet_id"]}
            await self.save("created_sheet_id", selected["sheet_id"])
            refreshed = await call("get_sheet_info", {"file_id": self.file_id})
            selected = next(
                item
                for item in refreshed["sheets"]
                if item["sheet_id"] == created["sheet_id"]
            )
        sheet = selected["sheet_id"]
        header = await self.read(call, sheet, 0, 0, len(LEGACY_HEADERS))
        initializing = not await self.setting("roll_ready")
        if initializing:
            if (
                header.get((0, len(LEGACY_HEADERS)), {}).get("string_value")
                == self.marker
            ):
                if saved != sheet:
                    raise MCPUnavailable("旧抽奖流水的标签页尚未绑定")
                await self.migrate_legacy(call, selected, header)
            elif not any(self.populated(cell) for cell in header.values()):
                created_here = await self.setting("created_sheet_id") == sheet
                used = (selected.get("used_range") or {}).get("row_count")
                if not created_here and (saved or used != 0):
                    raise MCPUnavailable(f"「{SHEET_NAME}」已有数据或无法确认空白")
                await self.save("roll_init_sheet_id", sheet)
                await self.write(
                    call,
                    sheet,
                    [self.cell(0, col, title) for col, title in enumerate(HEADERS)],
                )
            elif await self.setting("roll_init_sheet_id") != sheet:
                raise MCPUnavailable(f"「{SHEET_NAME}」已有数据或表头与抽奖格式不一致")
            header = await self.read(call, sheet, 0, 0, len(LEGACY_HEADERS))
        if any(
            not self.matches(header.get((0, col), {}), title)
            for col, title in enumerate(HEADERS)
        ):
            raise MCPUnavailable("腾讯文档抽奖表的表头被更改")
        if any(
            col >= len(HEADERS) and self.populated(cell)
            for (_, col), cell in header.items()
        ):
            raise MCPUnavailable("腾讯文档抽奖表存在额外表头")
        if initializing:
            await self.save("sheet_id", sheet)
            await self.save("roll_ready", "1")
        return selected

    def legacy_columns(self, record):
        detail = "，".join(
            f"{key} {value}" for key, value in json.loads(record["detail"]).items()
        )
        return (
            f"R{record['id']}",
            display_time(record["at"]),
            self.store.anonymous(self.group),
            f"#{record['raffle']}",
            record["kind"],
            record["name"],
            self.store.anonymous(self.group + ":" + record["qq"])
            if record["qq"]
            else "",
            detail,
        )

    async def migrate_legacy(self, call, selected, header):
        sheet = selected["sheet_id"]
        if any(
            not self.matches(header.get((0, col), {}), title)
            for col, title in enumerate(LEGACY_HEADERS)
        ):
            raise MCPUnavailable("现有流水表头被手动修改，暂停迁移")
        after, count = json.loads(await self.setting("position") or "[0, 0]")
        ledger = await asyncio.to_thread(
            self.store.legacy_roll_events, self.group, after
        )
        if len(ledger) != count:
            raise MCPUnavailable("现有流水同步位置与本地记录不一致，暂停迁移")
        resuming = await self.setting("roll_init_sheet_id") == sheet
        # Only rewrite the verified bot-owned rectangle. Other cells remain untouched.
        for first in range(1, count + 1, 30):
            last = min(count, first + 29)
            existing = await self.read(call, sheet, first, last, len(LEGACY_HEADERS))
            for row in range(first, last + 1):
                for col, value in enumerate(self.legacy_columns(ledger[row - 1])):
                    current = existing.get((row, col), {})
                    if self.matches(current, value):
                        continue
                    if not self.populated(current) and (resuming or value == ""):
                        continue
                    raise MCPUnavailable("现有流水中有手动修改，暂停迁移")
        capacity = selected.get("row_count")
        if isinstance(capacity, int) and count + 1 < capacity:
            next_row = await self.read(
                call, sheet, count + 1, count + 1, len(LEGACY_HEADERS)
            )
            if any(self.populated(cell) for cell in next_row.values()):
                raise MCPUnavailable("旧表中有未核对的额外记录，暂停迁移")
        await self.save("roll_init_sheet_id", sheet)
        # A single clear preserves the existing sheet ID. Its completion is recoverable
        # by the saved initialization marker, even if the request loses its response.
        if count:
            await call(
                "clear_range_cells",
                {
                    "file_id": self.file_id,
                    "sheet_id": sheet,
                    "start_row": 1,
                    "end_row": count,
                    "start_col": 0,
                    "end_col": len(LEGACY_HEADERS) - 1,
                },
            )
        await self.write(
            call,
            sheet,
            [
                self.cell(0, col, HEADERS[col] if col < len(HEADERS) else "")
                for col in range(len(LEGACY_HEADERS) + 1)
            ],
        )

    async def expand(self, call, sheet, selected, last_row, last_col):
        for kind, size, last in (
            ("row", selected.get("row_count"), last_row),
            ("col", selected.get("col_count"), last_col),
        ):
            if isinstance(size, int) and 0 < size <= last:
                await call(
                    "insert_dimension",
                    {
                        "file_id": self.file_id,
                        "sheet_id": sheet,
                        "dimension_type": kind,
                        "index": size - 1,
                        "count": max(25, last + 1 - size),
                        "direction": "after",
                    },
                )

    async def commit_row(self, call, sheet, row, columns):
        creator, prize, winner, deadline, *entrants = columns
        local_deadline = datetime.fromtimestamp(deadline, ZONE).replace(tzinfo=None)
        serial = (local_deadline - datetime(1899, 12, 30)).total_seconds() / 86400
        values = (creator, prize, winner, serial, *entrants)
        existing = await self.read(call, sheet, row, row, len(values) - 1)
        changes = []
        for col, value in enumerate(values):
            current = existing.get((row, col), {})
            if self.matches(current, value):
                continue
            if self.populated(current):
                raise MCPUnavailable("腾讯文档抽奖记录被移动或手动修改")
            if value != "":
                changes.append(self.cell(row, col, value))
        if not changes:
            return
        if any(change["col"] == 3 for change in changes):
            await call(
                "set_cell_style",
                {
                    "file_id": self.file_id,
                    "sheet_id": sheet,
                    "start_row": row,
                    "end_row": row,
                    "start_col": 3,
                    "end_col": 3,
                    "number_format_pattern": "yyyy/m/d hh:mm",
                },
            )
        await self.write(call, sheet, changes)

    async def sync(self):
        after, written = json.loads(await self.setting("roll_position") or "[0, 0]")
        last_event, rows = await asyncio.to_thread(
            self.store.roll_rows, self.group, after
        )
        if not rows and await self.setting("roll_ready"):
            return 0
        async with asyncio.timeout(55):
            call = self.call
            selected = await self.ensure_sheet(call)
            if not rows:
                return 0
            sheet = selected["sheet_id"]
            await self.expand(
                call,
                sheet,
                selected,
                rows[-1][0],
                max(len(values) for _, values in rows) - 1,
            )
            for row, columns in rows:
                await self.commit_row(call, sheet, row, columns)
            await self.save(
                "roll_position", json.dumps([last_event, max(written, rows[-1][0])])
            )
            return len(rows)
