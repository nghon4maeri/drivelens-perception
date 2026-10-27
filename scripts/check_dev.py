"""Run the same backend/frontend checks locally and in CI, without production access.

Input: installed Python/Node dependencies and an explicit test PostgreSQL URL.
Output: check logs and first failing exit code; cache/temp stay in data/output.
Run: python scripts/check_dev.py [--scope backend|frontend|all].
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("backend", "frontend", "all"), default="all")
    parser.add_argument("--ignore-known-windows-hook", action="store_true")
    args = parser.parse_args(argv)
    if args.ignore_known_windows_hook and sys.platform != "win32":
        parser.error("The hook exception is only allowed on Windows; CI must run the hook.")
    if args.scope != "frontend" and not os.environ.get("RAV_PILOT_TEST_DATABASE_URL"):
        parser.error("Set RAV_PILOT_TEST_DATABASE_URL to a dedicated test PostgreSQL cluster (DEV_CI_GUIDE.md).")
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if args.scope != "backend" and not npm:
        parser.error("Install Node/npm and run npm ci in src/web first.")

    env = os.environ.copy()
    for key, relative in {
        "HF_HOME": "data/system-cache/huggingface", "TORCH_HOME": "data/system-cache/torch",
        "PIP_CACHE_DIR": "data/system-cache/pip", "npm_config_cache": "data/system-cache/npm",
        "RAV_CLIP_CACHE_DIR": "data/model-cache/huggingface/hub", "YOLO_CONFIG_DIR": "output/ultralytics",
        "TEMP": "output/tmp", "TMP": "output/tmp", "TMPDIR": "output/tmp",
    }.items():
        path = ROOT / relative
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(APP_ENV="test", OPENAI_API_KEY="test-key")

    commands = []
    if args.scope != "frontend":
        pytest = [sys.executable, "-m", "pytest", "tests", "-q", "-rs", "--basetemp=output/pytest-tmp"]
        if args.ignore_known_windows_hook:
            print("EXCLUDED: tests/test_codex_hook.py (known Windows Unicode failure)", flush=True)
            pytest.append("--ignore=tests/test_codex_hook.py")
        commands.extend([(ROOT, [sys.executable, "-m", "ruff", "check", "src", "tests",
                                 "scripts/check_dev.py", "scripts/smoke_combined.py"]), (ROOT, pytest)])
    if args.scope != "backend":
        commands.extend((ROOT / "src/web", [npm, *command]) for command in (
            ["exec", "--", "tsc", "--noEmit"], ["test"], ["run", "build"], ["run", "build"],
        ))
    for index, (cwd, command) in enumerate(commands):
        command_env = env.copy()
        if cwd != ROOT:
            command_env["VITE_RAV_MODE"] = "pilot" if index == len(commands) - 1 else "local"
        print(f"CHECK: {' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd=cwd, env=command_env)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
