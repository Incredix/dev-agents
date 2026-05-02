"""CLI for local LangGraph workflows (loads no secrets — use shell `set -a && source .env`)."""

from __future__ import annotations

import argparse
import sys

import httpx


def cmd_ollama_check(ns: argparse.Namespace) -> int:
    from dev_agents.ollama_net import check_ollama_tags

    return check_ollama_tags(ns.url)


def cmd_hello(ns: argparse.Namespace) -> int:
    from dev_agents.graphs.hello import run

    out = run(ns.topic or None)
    print(out.get("reply", out))
    return 0


def cmd_plan(ns: argparse.Namespace) -> int:
    from dev_agents.graphs.code_plan import run

    if not ns.instruction.strip():
        print("instruction must be non-empty", file=sys.stderr)
        return 2

    ws_idx = ns.workspace_index
    idx = ws_idx if ws_idx is not None else None

    result = run(
        ns.instruction,
        read_rel=ns.read,
        workspace_root=ns.workspace,
        workspace_index=idx,
    )
    print(result.get("reply", result))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dev-agents", description="LangGraph + Ollama dev helpers")
    sub = p.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("hello", help="Smoke test Ollama (one-shot graph)")
    ph.add_argument("--topic", default="", metavar="TEXT", help="Short topic phrase")
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
    pp.add_argument(
        "--workspace",
        "-w",
        default=None,
        metavar="ABS_PATH",
        help="Checkout root directory (defaults to AGENT_WORKSPACES[0])",
    )
    pp.add_argument(
        "--workspace-index",
        type=int,
        default=None,
        metavar="N",
        help="Index into AGENT_WORKSPACES (0-based)",
    )
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
