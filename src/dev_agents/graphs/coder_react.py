"""Coder agent: cycles model ↔ tools until a free-text answer (Ollama often omits structured tool_calls)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from dev_agents.chat import make_chat_model
from dev_agents.tools_workspace import build_workspace_tools


_CODER_SYSTEM = """You are a coding assistant for a single local git checkout.
You have tools: read_workspace_file, list_workspace_directory, grep_workspace, ripgrep_workspace.

To call a tool, respond with ONLY valid JSON — one single object — and nothing before or after it (no prose, no markdown code fences, never XML-like tags such as <tool_call>):
{"name": "<tool_name>", "arguments": {<argdict>}}

Example:
{"name": "list_workspace_directory", "arguments": {"relative_path": "."}}

When you have enough context to answer the user directly, reply with plain text (no JSON).
Do not claim you edited files — tools are read-only. You may include a suggested ```diff``` at the end."""


def default_checkpoint_path() -> Path:
    raw = os.environ.get("DEV_AGENTS_CHECKPOINT_DB", ".checkpoints/checkpoints.sqlite")
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (Path.cwd() / p)


def _parse_tool_json(content: str) -> tuple[str, dict] | None:
    """Extract first JSON object (tool call). Handles trailing tokenizer junk e.g. ``<tool_call|>`` after ``}``."""
    if not isinstance(content, str):
        return None
    s = content.strip()
    start = s.find("{")
    if start < 0:
        return None
    frag = s[start:]
    decoder = json.JSONDecoder()
    try:
        obj, _end = decoder.raw_decode(frag)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    args = obj.get("arguments")
    if isinstance(name, str) and isinstance(args, dict):
        return name, args
    return None


def _assistant_is_plain_answer(raw: str) -> bool:
    """False if message should be routed to tools or skipped as intermediate tool blob."""
    if not isinstance(raw, str) or not raw.strip():
        return False
    if _parse_tool_json(raw):
        return False
    low = raw.lower()
    return "<tool_call" not in low and "</tool_call" not in low and "<tool|" not in low


class CoderState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _truncate(s: str, max_len: int = 2400) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 20] + "\n… [truncated]"


def _msg_preview(m: AnyMessage, max_len: int = 800) -> str:
    cn = type(m).__name__.replace("Message", "").lower()
    if isinstance(m, ToolMessage):
        return f"tool[{cn}]: {_truncate(str(m.content), max_len)}"
    if isinstance(m, AIMessage):
        raw = m.content if isinstance(m.content, str) else str(m.content)
        if _parse_tool_json(raw):
            return f"assistant[{cn}]: (tool-call JSON) {_truncate(raw, min(600, max_len))}"
        return f"assistant[{cn}]: {_truncate(raw, max_len)}"
    if isinstance(m, HumanMessage):
        return f"user: {_truncate(str(m.content), max_len)}"
    return f"{cn}: {_truncate(str(getattr(m, 'content', m)), max_len)}"


def _extract_final_answer(messages: list[AnyMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            raw = m.content if isinstance(m.content, str) else str(m.content)
            if _parse_tool_json(raw):
                continue
            if not _assistant_is_plain_answer(raw):
                continue
            if raw.strip():
                return raw
    return ""


def _emit_trace(trace_to_stderr: bool, step_log: list[str] | None, msg: str) -> None:
    if step_log is not None:
        step_log.append(msg)
    if trace_to_stderr:
        ts = time.strftime("%H:%M:%S")
        print(f"[dev-agents coder {ts}] {msg}", file=sys.stderr, flush=True)


def _last_ai(state: CoderState) -> AIMessage | None:
    for m in reversed(state.get("messages") or []):
        if isinstance(m, AIMessage):
            return m
    return None


def run_coder(
    instruction: str,
    *,
    workspace_root: Path,
    model: str | None = None,
    thread_id: str = "default",
    recursion_limit: int = 40,
    checkpoint_path: Path | None = None,
    use_checkpoint: bool = True,
    verbose: bool = False,
    step_log: list[str] | None = None,
) -> str:
    tools = build_workspace_tools(workspace_root)
    tool_map = {getattr(t, "name", "?"): t for t in tools}
    llm = make_chat_model(**({"model": model} if model else {}))

    ctx = (
        _CODER_SYSTEM
        + f"\n\nWorkspace root: {workspace_root.resolve()}\n"
        + f"Tool names: {', '.join(sorted(tool_map))}.\n"
    )

    def call_model(state: CoderState) -> dict:
        msgs: list[AnyMessage] = [SystemMessage(ctx)]
        msgs.extend(state["messages"])
        res = llm.invoke(msgs)
        return {"messages": [res]}

    def run_tool(state: CoderState) -> dict:
        last = _last_ai(state)
        if last is None:
            return {"messages": []}
        raw = last.content if isinstance(last.content, str) else str(last.content)
        parsed = _parse_tool_json(raw)
        if not parsed:
            return {"messages": []}
        name, args = parsed
        tool = tool_map.get(name)
        if tool is None:
            return {
                "messages": [
                    ToolMessage(
                        content=f"unknown tool {name!r}; valid: {sorted(tool_map)}",
                        tool_call_id="pseudo",
                    )
                ]
            }
        try:
            out = tool.invoke(args)
        except Exception as e:  # noqa: BLE001
            out = f"error: {e}"
        return {"messages": [ToolMessage(content=str(out), tool_call_id="pseudo")]}

    def route_after_model(state: CoderState) -> str:
        last = _last_ai(state)
        if last is None:
            return END
        raw = last.content if isinstance(last.content, str) else str(last.content)
        if last.tool_calls:
            # Native tool_calls (unlikely with some Ollama builds) → end loop; upstream would need ToolNode
            return END
        if _parse_tool_json(raw):
            return "tools"
        return END

    builder = StateGraph(CoderState)
    builder.add_node("model", call_model)
    builder.add_node("tools", run_tool)
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_after_model, {"tools": "tools", END: END})
    builder.add_edge("tools", "model")

    base_limit = max(10, min(int(recursion_limit), 200))
    invoke_cfg: dict = {"recursion_limit": base_limit}

    human_only: CoderState = {"messages": [HumanMessage(content=instruction)]}
    stream_trace = verbose or step_log is not None

    def _run(graph) -> dict:
        if not stream_trace:
            return dict(graph.invoke(human_only, invoke_cfg))

        ck_info = repr(checkpoint_path or default_checkpoint_path()) if use_checkpoint else "disabled"
        _emit_trace(
            verbose,
            step_log,
            f"streaming steps (checkpoint={bool(use_checkpoint)} db={ck_info} thread={thread_id!r})",
        )
        last: dict | None = None
        for i, st in enumerate(
            graph.stream(human_only, invoke_cfg, stream_mode="values"),
            start=1,
        ):
            last = dict(st)
            msgs = last.get("messages") or []
            if msgs:
                _emit_trace(
                    verbose,
                    step_log,
                    f"step {i} messages={len(msgs)} :: {_msg_preview(msgs[-1], 950)}",
                )
            else:
                _emit_trace(verbose, step_log, f"step {i} (empty messages)")
        _emit_trace(verbose, step_log, "stream complete.")
        return last if last else {}

    if use_checkpoint:
        invoke_cfg["configurable"] = {"thread_id": thread_id}
        checkpoint_path = checkpoint_path or default_checkpoint_path()
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            graph = builder.compile(checkpointer=saver)
            result = _run(graph)
    else:
        graph = builder.compile()
        result = _run(graph)

    messages = result.get("messages") or []
    return _extract_final_answer(messages)
