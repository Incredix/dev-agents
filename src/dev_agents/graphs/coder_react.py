"""Coder agent: cycles model ↔ tools until a free-text answer (Ollama often omits structured tool_calls)."""

from __future__ import annotations

import json
import os
import re
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

To call a tool, respond with ONLY one JSON object and nothing else (no markdown, no prose):
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
    """Detect Ollama-style pseudo tool call embedded in assistant text."""
    if not isinstance(content, str):
        return None
    s = content.strip()
    if not s.startswith("{"):
        m = re.search(r"\{[\s\S]*\"name\"[\s\S]*\"arguments\"[\s\S]*\}\s*$", s)
        if not m:
            return None
        s = m.group(0)
    try:
        o = json.loads(s)
    except json.JSONDecodeError:
        return None
    name = o.get("name")
    args = o.get("arguments")
    if isinstance(name, str) and isinstance(args, dict):
        return name, args
    return None


class CoderState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


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

    if use_checkpoint:
        invoke_cfg["configurable"] = {"thread_id": thread_id}
        checkpoint_path = checkpoint_path or default_checkpoint_path()
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke(human_only, invoke_cfg)
    else:
        graph = builder.compile()
        result = graph.invoke(human_only, invoke_cfg)

    messages = result.get("messages") or []
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            raw = m.content if isinstance(m.content, str) else str(m.content)
            if _parse_tool_json(raw):
                continue
            if raw.strip():
                return raw
    return ""
