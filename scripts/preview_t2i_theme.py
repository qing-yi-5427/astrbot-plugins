"""Preview one flexible Typst theme on the local service without LLM calls."""

import argparse
import base64
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

SAMPLE = """     开头旁白与 **重点**。

# 中文标题 English 123 📡

普通正文、**加粗**、*斜体*、~~删除线~~、[链接](https://example.com)、`行内代码`。

> 引用与重点。

- [x] 已完成
- [ ] 待完成

| 项目 | 状态 |
|---|---|
| 中文 | ✅ 正常 |

```python
print("你好，旅行者")
```
"""


def main():
    """Build a package, validate it, and save an actual PNG preview."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("theme", type=Path)
    parser.add_argument("--service", default="http://localhost:8000/text2img")
    parser.add_argument("--output", type=Path, default=Path("theme-preview.png"))
    args = parser.parse_args()
    root = args.theme.resolve()
    meta = root / "theme.json"
    bundle = {
        "schema": 1,
        "id": root.name,
        "source": (root / "theme.typ").read_text(encoding="utf-8-sig"),
        "meta": json.loads(meta.read_text(encoding="utf-8-sig"))
        if meta.exists()
        else {},
        "files": {},
    }
    files = [
        p
        for p in root.glob("avatar.*")
        if p.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg"}
    ]
    if (root / "assets").exists():
        files += [p for p in (root / "assets").rglob("*") if p.is_file()]
    for path in files:
        if path.is_symlink() or root not in path.resolve().parents:
            parser.error(
                "Theme resources must be regular files inside the theme folder"
            )
        bundle["files"][str(path.relative_to(root))] = base64.b64encode(
            path.read_bytes()
        ).decode()
    endpoint = args.service.rstrip("/").removesuffix("/generate")
    opener = build_opener(ProxyHandler({}))
    validate = Request(
        endpoint + "/themes/validate",
        data=json.dumps({"theme": bundle}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(validate, timeout=65) as response:
            result = json.load(response)
        if result.get("status") != "ok":
            raise ValueError(result)
        payload = {
            "tmpl": '<div class="brand-name">Amadeus</div>',
            "tmpldata": {
                "text": SAMPLE,
                "version": "preview",
                "model_name": "preview-model",
                "t2i_style": {"template": bundle["id"]},
                "t2i_theme": bundle,
            },
            "options": {"type": "png"},
        }
        request = Request(
            endpoint + "/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=65) as response:
            raw = response.read(20 * 1024 * 1024 + 1)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > 20 * 1024 * 1024:
            raise ValueError("Renderer did not return a bounded PNG")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(raw)
        print(f"Rendered {bundle['id']}: {args.output}")
    except HTTPError as error:
        raise SystemExit(error.read(16384).decode("utf-8", errors="replace")) from None


if __name__ == "__main__":
    main()
