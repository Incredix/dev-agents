"""Single-node example graph: proves Ollama wiring. Replace with real workflows."""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from dev_agents.chat import make_chat_model


class HelloState(TypedDict, total=False):
    topic: str
    reply: str
    model_override: str


def _draft(state: HelloState) -> HelloState:
    mo = (state.get("model_override") or "").strip()
    llm = make_chat_model(**({"model": mo} if mo else {}))
    topic = state.get("topic") or "your homelab"
    msg = llm.invoke(f"Say one short sentence about {topic}.")
    return {"reply": getattr(msg, "content", str(msg))}


def build_graph():
    g = StateGraph(HelloState)
    g.add_node("draft", _draft)
    g.set_entry_point("draft")
    g.add_edge("draft", END)
    return g.compile()


def run(topic: str | None = None, *, model: str | None = None) -> HelloState:
    graph = build_graph()
    payload: HelloState = {"topic": topic or ""}
    if model:
        payload["model_override"] = model
    return graph.invoke(payload)
