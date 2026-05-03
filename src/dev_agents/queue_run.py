"""Headless multi-task runner: Coder → unified diff → local patch or git/gh PR per task."""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from dev_agents.diff_extract import combine_diff_blocks, extract_diff_blocks
from dev_agents.git_pr import apply_patch_commit_push_pr, sanitize_branch
from dev_agents.graphs.coder_react import run_coder
from dev_agents.patch_apply import run_patch
from dev_agents.workspace import resolve_workspace_root


def parse_queue_text(raw: str) -> list[str]:
    """Split on ``---`` alone on a line; each block is one task instruction."""
    parts = re.split(r"(?m)^---\s*$", (raw or "").strip())
    tasks: list[str] = []
    for p in parts:
        s = p.strip()
        if s:
            tasks.append(s)
    return tasks


def _env_stash_default() -> bool:
    raw = os.environ.get("DEV_AGENTS_AUTOPILOT_STASH", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def run_queue(ns: Namespace) -> int:
    """Execute ``queue`` CLI: run Coder for each task, then autopilot patch/PR."""
    qpath = Path(ns.queue_file).expanduser()
    if not qpath.is_file():
        print(f"queue file not found: {qpath}", file=sys.stderr)
        return 2
    raw = qpath.read_text(encoding="utf-8", errors="replace")
    tasks = parse_queue_text(raw)
    if not tasks:
        print("no tasks in queue file (non-empty blocks separated by --- on its own line)", file=sys.stderr)
        return 2

    root = resolve_workspace_root(ns.workspace, ns.workspace_index)
    if root is None:
        print(
            "No workspace root: set AGENT_WORKSPACES or pass -w / --workspace-index",
            file=sys.stderr,
        )
        return 2

    log_path = Path(ns.log).expanduser() if ns.log else Path.cwd() / "dev-agents-queue.log"
    local_only = bool(getattr(ns, "local_patch_only", False))
    open_pr = not local_only

    model = ns.model if getattr(ns, "model", None) else None
    rec_lim = int(getattr(ns, "recursion_limit", 40) or 40)
    strip = int(getattr(ns, "strip", 1) or 1)
    fail_fast = bool(getattr(ns, "fail_fast", False))
    sleep_s = float(getattr(ns, "sleep", 0) or 0)

    exit_status = 0
    with log_path.open("a", encoding="utf-8") as log_fp:
        hdr = f"\n{'#' * 70}\n# dev-agents queue start {datetime.now().isoformat()}\n# tasks={len(tasks)} workspace={root}\n"
        log_fp.write(hdr)
        log_fp.flush()
        print(hdr, end="", file=sys.stderr)

        for i, instruction in enumerate(tasks):
            tid = f"queue-{uuid.uuid4().hex[:16]}"
            label = (instruction[:120] + ("…" if len(instruction) > 120 else "")).replace("\n", " ")
            banner = f"\n--- TASK {i + 1}/{len(tasks)} — {label}\n"
            log_fp.write(banner)
            log_fp.flush()
            print(banner, file=sys.stderr)

            try:
                reply = run_coder(
                    instruction,
                    workspace_root=root,
                    model=model,
                    thread_id=tid,
                    recursion_limit=rec_lim,
                    use_checkpoint=False,
                    verbose=bool(getattr(ns, "verbose", False)),
                )
            except Exception as e:  # noqa: BLE001
                log_fp.write(f"CODER_ERROR: {e!r}\n")
                log_fp.flush()
                print(f"CODER_ERROR: {e}", file=sys.stderr)
                exit_status = 1
                if fail_fast:
                    break
                continue

            log_fp.write(reply + "\n")
            log_fp.flush()

            blocks = extract_diff_blocks(reply)
            if not blocks:
                log_fp.write("NO_DIFF_IN_REPLY: skipped patch (no unified diff found).\n")
                log_fp.flush()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                continue

            data = combine_diff_blocks(blocks)
            hint = sanitize_branch((instruction or "queue").replace("\n", " ")[:72] or "queue")
            branch = f"agent-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{i + 1:03d}-{hint[:40]}"

            if open_pr:
                code, lg = apply_patch_commit_push_pr(
                    root,
                    data,
                    branch=branch,
                    commit_message=f"queue: {hint[:80]}",
                    pr_title=f"queue: {hint[:80]}",
                    pr_body="Automated by `dev-agents queue`.\n\n_(Review before merge.)_",
                    strip=strip,
                    stash_if_dirty=_env_stash_default(),
                )
                log_fp.write(f"PR_PATH exit={code}\n{lg}\n")
            else:
                with NamedTemporaryFile(mode="wb", suffix=".patch", delete=False) as tf:
                    tf.write(data)
                    tpath = Path(tf.name)
                try:
                    code = run_patch(root, tpath, strip=strip, do_apply=True)
                finally:
                    tpath.unlink(missing_ok=True)
                log_fp.write(f"LOCAL_PATCH exit={code}\n")

            log_fp.flush()
            if code != 0:
                exit_status = 1
                if fail_fast:
                    break

            if sleep_s > 0:
                time.sleep(sleep_s)

        log_fp.write(f"\n# queue end {datetime.now().isoformat()} exit={exit_status}\n")
    return exit_status
