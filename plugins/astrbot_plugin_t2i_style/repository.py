"""Fetch pinned GitHub theme folders and keep an atomic, last-good local cache."""

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import aiohttp

ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024
MAX_THEMES = 64


class RepositoryError(ValueError):
    """An update failed without replacing the active theme snapshot."""


class ThemeRepository:
    def __init__(self, data_dir, config, validator):
        self.path = Path(data_dir) / "repository.json"
        self.previous = self.path.with_name("repository.previous.json")
        self.enabled = config.get("repository_enabled", False) is True
        repository = str(config.get("theme_repository", "qing-yi-5427/astrbot-plugins"))
        repository = (
            repository.removeprefix("https://github.com/")
            .removesuffix(".git")
            .strip("/")
        )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise RepositoryError("主题仓库请填写 GitHub owner/repo")
        self.repository = repository
        self.ref = str(config.get("theme_repository_ref", "main"))
        self.directory = str(config.get("theme_repository_path", "t2i-themes")).strip(
            "/"
        )
        path = PurePosixPath(self.directory)
        if (
            not path.parts
            or ".." in path.parts
            or "\\" in self.directory
            or str(path) != self.directory
        ):
            raise RepositoryError("主题根目录必须是仓库内的相对目录")
        if not self.ref or len(self.ref) > 128 or any(ord(c) < 32 for c in self.ref):
            raise RepositoryError("主题分支或 commit 无效")
        self.validator = validator
        self.snapshot = {"version": 1, "revision": "", "bundles": {}}
        self.last_error = ""
        self.lock = asyncio.Lock()

    def load(self):
        """Load a bounded cache, leaving built-in themes usable on corruption."""
        try:
            if self.path.exists():
                self.snapshot = self._read_cache(self.path)
        except (OSError, ValueError, UnicodeError) as exc:
            self.last_error = f"主题缓存未加载：{type(exc).__name__}；继续使用内置主题"

    def _read_cache(self, path):
        """Read a regular local snapshot and validate its transport structure."""
        if path.is_symlink() or path.stat().st_size > MAX_CACHE_BYTES:
            raise RepositoryError("主题缓存无效或过大")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not COMMIT_PATTERN.fullmatch(str(data.get("revision", "")))
        ):
            raise RepositoryError("主题缓存格式无效")
        if (
            data.get("repository") != self.repository
            or data.get("directory") != self.directory
        ):
            raise RepositoryError("主题缓存属于其他仓库或目录")
        bundles = data.get("bundles")
        if not isinstance(bundles, dict) or len(bundles) > MAX_THEMES:
            raise RepositoryError("主题缓存目录无效")
        for identity, bundle in bundles.items():
            if (
                not ID_PATTERN.fullmatch(identity)
                or not isinstance(bundle, dict)
                or bundle.get("id") != identity
            ):
                raise RepositoryError("主题缓存 ID 无效")
            if (
                not isinstance(bundle.get("source"), str)
                or "{{CONTENT}}" not in bundle["source"]
            ):
                raise RepositoryError("主题缓存缺少正文入口")
            if not isinstance(bundle.get("meta", {}), dict) or not isinstance(
                bundle.get("files", {}), dict
            ):
                raise RepositoryError("主题缓存信息无效")
        return data

    def names(self):
        if not self.enabled:
            return {}
        return {
            identity: str(bundle.get("meta", {}).get("name", identity))[:64]
            for identity, bundle in self.snapshot["bundles"].items()
        }

    def bundle(self, identity):
        return self.snapshot["bundles"].get(identity) if self.enabled else None

    def describe(self):
        if not self.enabled:
            return "Git 主题同步未启用，使用内置主题。"
        revision = self.snapshot["revision"][:12] or "尚未同步"
        result = f"Git 主题：{len(self.snapshot['bundles'])} 套，版本 {revision}。"
        return result + (
            "\n最近同步失败，保留原主题：" + self.last_error if self.last_error else ""
        )

    async def _get(self, session, url, limit):
        """Read one bounded GitHub response without forwarding through redirects."""
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise RepositoryError(
                    f"GitHub 请求失败（HTTP {response.status}），原主题未替换"
                )
            if response.content_length is not None and response.content_length > limit:
                raise RepositoryError("GitHub 文件超过读取上限")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > limit:
                    raise RepositoryError("GitHub 文件超过读取上限")
            return bytes(data)

    async def _download(self):
        """Resolve a commit, discover theme folders and verify every Git blob."""
        headers = {
            "User-Agent": "AstrBot-T2I-Theme-Sync/1.2",
            "Accept": "application/vnd.github+json",
        }
        timeout = aiohttp.ClientTimeout(total=20, connect=8)
        async with aiohttp.ClientSession(
            timeout=timeout, headers=headers, trust_env=False
        ) as session:
            root = f"https://api.github.com/repos/{self.repository}"
            commit = json.loads(
                await self._get(
                    session, root + "/commits/" + quote(self.ref, safe=""), 1024 * 1024
                )
            )
            revision = commit.get("sha", "")
            if not COMMIT_PATTERN.fullmatch(revision):
                raise RepositoryError("GitHub 未返回有效 commit")
            if revision in {
                self.snapshot["revision"],
                self.snapshot.get("blocked_revision"),
            }:
                return None
            tree_sha = commit["commit"]["tree"]["sha"]
            tree = json.loads(
                await self._get(
                    session,
                    root + f"/git/trees/{tree_sha}?recursive=1",
                    4 * 1024 * 1024,
                )
            )
            if tree.get("truncated") or not isinstance(tree.get("tree"), list):
                raise RepositoryError("GitHub 目录过大或不完整")
            entries = {
                item["path"]: item
                for item in tree["tree"]
                if item.get("type") == "blob"
                and item.get("mode") in {"100644", "100755"}
            }
            prefix = self.directory + "/"
            identities = sorted(
                {
                    name[len(prefix) :].split("/")[0]
                    for name in entries
                    if name.startswith(prefix)
                    and name.count("/") == prefix.count("/") + 1
                    and name.endswith("/theme.typ")
                }
            )
            if not identities or len(identities) > MAX_THEMES:
                raise RepositoryError("主题目录为空或超过 64 套")
            bundles = {}
            for identity in identities:
                if not ID_PATTERN.fullmatch(identity):
                    raise RepositoryError(f"主题目录 ID 无效：{identity[:48]}")
                base = prefix + identity + "/"
                wanted = [
                    name
                    for name in entries
                    if name.startswith(base)
                    and (
                        name[len(base) :]
                        in {
                            "theme.typ",
                            "theme.json",
                            "avatar.webp",
                            "avatar.png",
                            "avatar.jpg",
                            "avatar.jpeg",
                        }
                        or name[len(base) :].startswith("assets/")
                    )
                ]
                if len(wanted) > 66:
                    raise RepositoryError(f"主题 {identity} 的资源过多")
                files = {}
                total = 0
                for name in sorted(wanted):
                    limit = (
                        MAX_SOURCE_BYTES
                        if name.endswith("/theme.typ")
                        else MAX_FILE_BYTES
                    )
                    item = entries[name]
                    if item.get("size", limit + 1) > limit:
                        raise RepositoryError(f"主题 {identity} 的文件过大")
                    raw = await self._get(
                        session,
                        f"https://raw.githubusercontent.com/{self.repository}/{revision}/"
                        + quote(name, safe="/"),
                        limit,
                    )
                    actual = hashlib.sha1(
                        b"blob " + str(len(raw)).encode() + b"\0" + raw
                    ).hexdigest()
                    if actual != item["sha"]:
                        raise RepositoryError(f"主题 {identity} 的文件校验失败")
                    total += len(raw)
                    if total > MAX_FILE_BYTES + MAX_SOURCE_BYTES:
                        raise RepositoryError(f"主题 {identity} 资源总量过大")
                    files[name[len(base) :]] = raw
                source = files.pop("theme.typ").decode("utf-8-sig")
                metadata = json.loads(files.pop("theme.json", b"{}"))
                if not isinstance(metadata, dict) or "{{CONTENT}}" not in source:
                    raise RepositoryError(f"主题 {identity} 信息无效或缺少正文入口")
                bundles[identity] = {
                    "schema": 1,
                    "id": identity,
                    "source": source,
                    "meta": metadata,
                    "files": {
                        name: base64.b64encode(raw).decode()
                        for name, raw in files.items()
                    },
                }
            candidate = {
                "version": 1,
                "revision": revision,
                "bundles": bundles,
                "repository": self.repository,
                "directory": self.directory,
            }
            if len(json.dumps(candidate).encode()) > MAX_CACHE_BYTES:
                raise RepositoryError("主题缓存总量超过 16 MiB")
            return candidate

    def _save(self, candidate):
        """Atomically retain the previous snapshot and write the new one."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for path, value in ((self.previous, self.snapshot), (self.path, candidate)):
            if not value["revision"]:
                continue
            fd, name = tempfile.mkstemp(prefix=".theme-repo-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)

    async def update(self, rollback=False):
        """Validate a complete revision before committing it to disk or memory."""
        if not self.enabled:
            return "请先在插件设置中启用 Git 主题同步。"
        if self.lock.locked():
            return "已有主题同步正在进行，请稍后查看状态。"
        async with self.lock:
            try:
                async with asyncio.timeout(180):
                    candidate = (
                        self._read_cache(self.previous)
                        if rollback
                        else await self._download()
                    )
                    if candidate is None:
                        self.last_error = ""
                        if self.snapshot.get("blocked_revision"):
                            return f"当前保留回退版本 {self.snapshot['revision'][:12]}；仓库出现新 commit 后再更新。"
                        return f"主题已是最新版本：{self.snapshot['revision'][:12]}。"
                    if rollback:
                        candidate["blocked_revision"] = self.snapshot["revision"]
                    for identity, bundle in candidate["bundles"].items():
                        try:
                            catalog = await self.validator(bundle)
                        except Exception as exc:
                            raise RepositoryError(
                                f"主题 {identity} 试渲染未通过：{str(exc)[:1000]}"
                            ) from exc
                        bundle["meta"].setdefault("name", catalog.get("name", identity))
                    task = asyncio.create_task(asyncio.to_thread(self._save, candidate))
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        await task
                        self.snapshot = candidate
                        raise
                    self.snapshot = candidate
                    self.last_error = ""
                    verb = "已回退" if rollback else "已同步"
                    return f"{verb} {len(candidate['bundles'])} 套主题，commit {candidate['revision'][:12]}；试渲染通过。"
            except (
                ValueError,
                OSError,
                KeyError,
                TypeError,
                AttributeError,
                RecursionError,
                aiohttp.ClientError,
                TimeoutError,
                UnicodeError,
            ) as exc:
                self.last_error = str(exc)[:1200] or type(exc).__name__
                return "主题未替换，继续使用原主题。\n" + self.last_error
