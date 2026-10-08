"""Package tracked plugin files as installable, root-level ZIP archives."""
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"tests", ".grill", ".github", "__pycache__"}
output = ROOT / "dist"
output.mkdir(exist_ok=True)
for plugin in sorted((ROOT / "plugins").iterdir()):
    if not (plugin / "metadata.yaml").is_file():
        continue
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", str(plugin.relative_to(ROOT))], cwd=ROOT
    ).decode().split("\0")
    archive = output / (plugin.name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(filter(None, tracked)):
            source = ROOT / name
            relative = source.relative_to(plugin)
            if SKIP.intersection(relative.parts) or relative.name in {".gitignore", "requirements-dev.txt", "pyproject.toml"}:
                continue
            bundle.write(source, str(relative))
    with zipfile.ZipFile(archive) as bundle:
        assert {"main.py", "metadata.yaml"} <= set(bundle.namelist())
        assert bundle.testzip() is None
    print(archive.name)
