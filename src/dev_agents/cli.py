"""CLI for local LangGraph workflows (loads no secrets — use shell `set -a && source .env`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


def _add_workspace_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--workspace",
        "-w",
        default=None,
        metavar="ABS_PATH",
        help="Checkout root (defaults first AGENT_WORKSPACES entry)",
    )
    p.add_argument(
        "--workspace-index",
        type=int,
        default=None,
        metavar="N",
        help="Index into AGENT_WORKSPACES (0-based)",
    )


def _add_model_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--model",
        "-m",
        default=None,
        metavar="NAME",
        help="Override OLLAMA_MODEL for this run (e.g. qwen2.5-coder:32b)",
    )


def cmd_ollama_check(ns: argparse.Namespace) -> int:
    from dev_agents.ollama_net import check_ollama_tags

    return check_ollama_tags(ns.url)


def cmd_hello(ns: argparse.Namespace) -> int:
    from dev_agents.graphs.hello import run

    out = run(ns.topic or None, model=ns.model)
    print(out.get("reply", out))
    return 0


def cmd_plan(ns: argparse.Namespace) -> int:
    from dev_agents.graphs.code_plan import run

    if not ns.instruction.strip():
        print("instruction must be non-empty", file=sys.stderr)
        return 2

    idx = ns.workspace_index if ns.workspace_index is not None else None

    result = run(
        ns.instruction,
        read_rel=ns.read,
        workspace_root=ns.workspace,
        workspace_index=idx,
        model=ns.model,
    )
    print(result.get("reply", result))
    return 0


def cmd_coder(ns: argparse.Namespace) -> int:
    from dev_agents.graphs.coder_react import run_coder
    from dev_agents.workspace import resolve_workspace_root

    if not ns.instruction.strip():
        print("instruction must be non-empty", file=sys.stderr)
        return 2

    root = resolve_workspace_root(ns.workspace, ns.workspace_index)
    if root is None:
        print(
            "No workspace root: set AGENT_WORKSPACES or pass -w / --workspace-index",
            file=sys.stderr,
        )
        return 2

    text = run_coder(
        ns.instruction,
        workspace_root=root,
        model=ns.model,
        thread_id=ns.thread_id,
        recursion_limit=ns.recursion_limit,
        use_checkpoint=not ns.no_checkpoint,
    )
    print(text)
    return 0


def cmd_patch_apply(ns: argparse.Namespace) -> int:
    from dev_agents.patch_apply import run_patch
    from dev_agents.workspace import resolve_workspace_root

    root = resolve_workspace_root(ns.workspace, ns.workspace_index)
    if root is None:
        print(
            "No workspace root: set AGENT_WORKSPACES or pass -w / --workspace-index",
            file=sys.stderr,
        )
        return 2
    patch_path = Path(ns.patch_file).expanduser()
    return run_patch(root, patch_path, strip=ns.strip, do_apply=ns.apply)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dev-agents", description="LangGraph + Ollama dev helpers")
    sub = p.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("hello", help="Smoke test Ollama (one-shot graph)")
    ph.add_argument("--topic", default="", metavar="TEXT", help="Short topic phrase")
    _add_model_flag(ph)
    ph.set_defaults(_run=cmd_hello)

    pp = sub.add_parser(
        "plan",
        help="Instruction + optional file excerpt → actionable plan over your workspaces",
    )
    pp.add_argument("--instruction", "-i", required=True, metavar="TEXT", help="What you want done")
    pp.add_argument(
        "--read",
        "-r",
        default=None,
        metavar="REL_PATH",
        help="Repo-relative path to include as excerpt (e.g. website/views.py)",
    )
    _add_workspace_flags(pp)
    _add_model_flag(pp)
    pp.set_defaults(_run=cmd_plan)

    pc = sub.add_parser(
        "ollama-check",
        help="GET /api/tags against OLLAMA_BASE_URL — useful when hello/plan fail with DNS errors",
    )
    pc.add_argument(
        "--url",
        default=None,
        metavar="BASE",
        help="Override env (same format as OLLAMA_BASE_URL, e.g. http://192.168.1.10:11434)",
    )
    pc.set_defaults(_run=cmd_ollama_check)

    pe = sub.add_parser(
        "coder",
        help="ReAct agent with read-only repo tools (uses SQLite checkpoints by default)",
    )
    pe.add_argument("--instruction", "-i", required=True, metavar="TEXT", help="Goal or question")
    _add_workspace_flags(pe)
    _add_model_flag(pe)
    pe.add_argument(
        "--thread-id",
        default="default",
        metavar="ID",
        help="LangGraph thread id for checkpoint resume (default: default)",
    )
    pe.add_argument(
        "--recursion-limit",
        type=int,
        default=40,
        metavar="N",
        help="Max graph steps (tool + model turns)",
    )
    pe.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Disable SQLite checkpointing (ephemeral run)",
    )
    pe.set_defaults(_run=cmd_coder)

    pa = sub.add_parser(
        "patch-apply",
        help="GNU patch dry-run (default) or apply a unified diff at a workspace root",
    )
    pa.add_argument("patch_file", metavar="PATCH", help="Path to a unified diff file")
    _add_workspace_flags(pa)
    pa.add_argument(
        "-p",
        "--strip",
        type=int,
        default=1,
        metavar="N",
        help="patch -pN strip count (default: 1)",
    )
    pa.add_argument(
        "--apply",
        action="store_true",
        help="Actually modify files (without this, only dry-run)",
    )
    pa.set_defaults(_run=cmd_patch_apply)

    ns = p.parse_args(argv)
    runner = getattr(ns, "_run", None)
    if runner is None:
        p.print_help()
        return 2
    try:
        return runner(ns)
    except httpx.ConnectError as e:
        from dev_agents.config import ollama_base_url
        from dev_agents.ollama_net import troubleshooting_block

        print(troubleshooting_block(ollama_base_url(), e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
