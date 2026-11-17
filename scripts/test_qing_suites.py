"""Run the two offline suites inside an AstrBot 4.29.4 Python environment."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
features = root / "plugins"
first = [root / "tests"]
for suite, names in {
    "astrbot_plugin_qing_chat": ("memory_compat", "context_images", "conversation"),
    "astrbot_plugin_qing_media": ("render", "image_source"),
}.items():
    first += [features / suite / "features" / name / "tests" for name in names]
second = [features / "astrbot_plugin_qing_media/features/imagegen/tests"]

with tempfile.TemporaryDirectory(prefix="qing-suite-tests-") as directory:
    env = {**os.environ, "ASTRBOT_ROOT": directory}
    env["PYTHONPATH"] = str(features) + os.pathsep + env.get("PYTHONPATH", "")
    for paths in (first, second):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--import-mode=importlib",
                *map(str, paths),
            ],
            cwd=root,
            env=env,
            check=True,
        )
