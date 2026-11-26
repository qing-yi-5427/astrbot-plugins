"""One-way replication to a dedicated Tencent Docs worksheet via official MCP."""

import asyncio
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from .store import display_time

HEADERS = (
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
    def __init__(self, store, file_id, call):
        self.store = store
        self.file_id = file_id
        self.call = call
        self.namespace = f"tencent:{file_id}:"
        self.marker = "抽奖流水·" + self.store.anonymous("sheet-owner")

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

    async def write(self, call, sheet, rows):
        values = [
            {"row": row, "col": col, "value_type": "STRING", "string_value": str(value)}
            for row, columns in rows
            for col, value in enumerate(columns)
        ]
        await call(
            "set_range_value",
            {"file_id": self.file_id, "sheet_id": sheet, "values": values},
        )

    async def ensure_sheet(self, call, started):
        info = await call("get_sheet_info", {"file_id": self.file_id})
        sheets = info["sheets"]
        saved = await self.setting("sheet_id")
        if saved:
            selected = next(
                (item for item in sheets if item["sheet_id"] == saved), None
            )
            if selected is None:
                raise RuntimeError("Managed Tencent Docs worksheet no longer exists")
            return selected
        name = await self.setting("pending_name")
        if not name:
            existing = {item["sheet_name"] for item in sheets}
            name = "抽奖流水" if "抽奖流水" not in existing else self.marker
            if name in existing:
                raise RuntimeError("Managed worksheet name is already occupied")
            await self.save("pending_name", name)
        selected = next((item for item in sheets if item["sheet_name"] == name), None)
        if selected is None:
            created = await call(
                "add_sheet",
                {"file_id": self.file_id, "name": name, "append_index": True},
            )
            selected = {"sheet_id": created["sheet_id"], "row_count": None}
        sheet = selected["sheet_id"]
        header = await self.read(call, sheet, 0, 0, len(HEADERS))
        marker = header.get((0, len(HEADERS)), {}).get("string_value")
        populated = any(
            cell.get("string_value")
            or cell.get("value_type") in ("FORMULA", "NUMBER", "BOOL")
            for cell in header.values()
        )
        if marker != self.marker:
            if (
                started
                or populated
                or (selected.get("used_range") or {}).get("row_count", 0) > 0
            ):
                raise RuntimeError(
                    "Cannot take over an existing Tencent Docs worksheet"
                )
            await self.write(call, sheet, [(0, (*HEADERS, self.marker))])
        await self.save("sheet_id", sheet)
        return selected

    def columns(self, record):
        detail = "，".join(
            f"{key} {value}" for key, value in json.loads(record["detail"]).items()
        )
        group = record["scope"]
        return (
            f"R{record['id']}",
            display_time(record["at"]),
            self.store.anonymous(group),
            f"#{record['raffle']}",
            record["kind"],
            record["name"],
            self.store.anonymous(group + ":" + record["qq"]) if record["qq"] else "",
            detail,
        )

    async def sync(self):
        after = int(await self.setting("cursor") or 0)
        records = await asyncio.to_thread(self.store.unsynced, after)
        if not records and await self.setting("sheet_id"):
            return 0
        async with asyncio.timeout(55):
            call = self.call
            selected = await self.ensure_sheet(call, after > 0)
            sheet = selected["sheet_id"]
            header = await self.read(call, sheet, 0, 0, len(HEADERS))
            if header.get((0, len(HEADERS)), {}).get("string_value") != self.marker:
                raise RuntimeError("Managed worksheet header was changed")
            if not records:
                return 0
            first, last = records[0]["id"], records[-1]["id"]
            count = selected.get("row_count")
            if isinstance(count, int) and 0 < count <= last:
                await call(
                    "insert_dimension",
                    {
                        "file_id": self.file_id,
                        "sheet_id": sheet,
                        "dimension_type": "row",
                        "index": count - 1,
                        "count": max(100, last + 1 - count),
                        "direction": "after",
                    },
                )
            existing = await self.read(call, sheet, max(1, first - 1), last, 0)
            if (
                first > 1
                and existing.get((first - 1, 0), {}).get("string_value")
                != f"R{first - 1}"
            ):
                raise RuntimeError("Managed worksheet rows were moved or removed")
            for row in range(first, last + 1):
                cell = existing.get((row, 0), {})
                if cell and cell.get("string_value") not in (None, "", f"R{row}"):
                    raise RuntimeError("Managed worksheet rows were moved or edited")
                if cell.get("value_type") not in (None, "STRING"):
                    raise RuntimeError("Managed worksheet row identifier was edited")
            await self.write(
                call, sheet, [(row["id"], self.columns(row)) for row in records]
            )
            await self.save("cursor", last)
            return len(records)
