"""Two-step graph: assemble workspace + optional file excerpt, then ask Ollama for a plan."""

from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from dev_agents.chat import make_chat_model
from dev_agents.config import workspace_paths
from dev_agents.workspace import read_repo_file, resolve_workspace_root

_SYSTEM = """You are a senior engineer helping iterate on local repositories.
Respond with concise, actionable steps. Prefer bullets. Reference paths relative to repo root where relevant.
If context is insufficient, say what files or commands you still need."""

_GATHER_MISSING_ROOT = "(no workspace root resolved — set AGENT_WORKSPACES or pass --workspace)"


class CodePlanState(TypedDict, total=False):
    instruction: str
    workspace_root_arg: str
    workspace_index: int | None
    read_rel: str | None
    model_override: str

    workspaces_blurb: str
    file_excerpt_label: str
    file_excerpt: str

    reply: str


def _gather(state: CodePlanState) -> CodePlanState:
    paths = workspace_paths()
    blurb = "\n".join(str(p) for p in paths) if paths else "(AGENT_WORKSPACES empty)"

    idx = state.get("workspace_index")
    root_arg = state.get("workspace_root_arg") or None
    root = resolve_workspace_root(root_arg, idx)

    excerpt = ""
    label = ""

    read_rel = (state.get("read_rel") or "").strip()
    if read_rel:
        if root is None:
            excerpt = "(cannot read file without a workspace root)"
        else:
            try:
                excerpt = read_repo_file(root, read_rel)
                label = f"{root}/{read_rel}"
            except OSError:
                excerpt = "(read failed)"
                label = read_rel
            except ValueError as e:
                excerpt = f"(skip file: {e})"
                label = read_rel

    return {
        "workspaces_blurb": blurb,
        "file_excerpt": excerpt,
        "file_excerpt_label": label or "(no single file)",
    }


def _answer(state: CodePlanState) -> CodePlanState:
    root_arg = state.get("workspace_root_arg") or None
    idx = state.get("workspace_index")
    root = resolve_workspace_root(root_arg, idx if isinstance(idx, int) else None)
    root_hint = str(root.resolve()) if root else _GATHER_MISSING_ROOT

    user_parts = [
        f"Effective workspace root: {root_hint}",
        f"All configured workspaces:\n{state['workspaces_blurb']}",
        "",
        f"User instruction:\n{state.get('instruction', '').strip()}",
    ]
    if state.get("file_excerpt"):
        user_parts.extend(
            [
                "",
                f"File excerpt ({state['file_excerpt_label']}):",
                "```",
                state["file_excerpt"],
                "```",
            ]
        )

    mo = (state.get("model_override") or "").strip()
    llm = make_chat_model(**({"model": mo} if mo else {}))
    msg = llm.invoke(
        [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content="\n".join(user_parts)),
        ]
    )
    text = getattr(msg, "content", str(msg))
    return {"reply": text}


def build_graph():
    g = StateGraph(CodePlanState)
    g.add_node("gather", _gather)
    g.add_node("answer", _answer)
    g.set_entry_point("gather")
    g.add_edge("gather", "answer")
    g.add_edge("answer", END)
    return g.compile()


def run(
    instruction: str,
    *,
    read_rel: str | None = None,
    workspace_root: str | None = None,
    workspace_index: int | None = None,
    model: str | None = None,
) -> CodePlanState:
    graph = build_graph()
    payload: CodePlanState = {"instruction": instruction}
    if read_rel:
        payload["read_rel"] = read_rel
    if workspace_root:
        payload["workspace_root_arg"] = workspace_root
    if workspace_index is not None:
        payload["workspace_index"] = workspace_index
    if model:
        payload["model_override"] = model
    return graph.invoke(payload)
