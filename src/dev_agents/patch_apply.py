"""Apply a unified diff with GNU ``patch`` (dry-run by default)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def run_patch(
    workspace: Path,
    patch_file: Path,
    *,
    strip: int = 1,
    do_apply: bool = False,
) -> int:
    if not shutil.which("patch"):
        print("GNU `patch` not found (try: sudo apt install patch)", file=sys.stderr)
        return 1
    if not workspace.is_dir():
        print(f"workspace is not a directory: {workspace}", file=sys.stderr)
        return 2
    if not patch_file.is_file():
        print(f"patch file missing: {patch_file}", file=sys.stderr)
        return 2

    data = patch_file.read_bytes()
    p = max(0, min(int(strip), 10))
    strip_arg = f"-p{p}"

    dry = subprocess.run(
        ["patch", strip_arg, "--batch", "--forward", "--dry-run"],
        cwd=str(workspace.resolve()),
        input=data,
        capture_output=True,
    )
    err = (dry.stderr or b"").decode("utf-8", errors="replace")
    out = (dry.stdout or b"").decode("utf-8", errors="replace")
    if dry.returncode != 0:
        sys.stderr.write(err or out or f"patch dry-run exited {dry.returncode}\n")
        return 1

    if not do_apply:
        print("Dry-run succeeded. To modify files:", file=sys.stderr)
        print(
            f"  dev-agents patch-apply --apply -w {workspace} {patch_file}",
            file=sys.stderr,
        )
        return 0

    real = subprocess.run(
        ["patch", strip_arg, "--batch", "--forward"],
        cwd=str(workspace.resolve()),
        input=data,
        capture_output=True,
    )
    if real.returncode != 0:
        sys.stderr.write(
            (real.stderr or b"").decode("utf-8", errors="replace")
            or (real.stdout or b"").decode("utf-8", errors="replace")
            or "patch failed\n"
        )
        return 1
    print("Applied.", file=sys.stderr)
    return 0
