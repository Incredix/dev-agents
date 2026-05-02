"""Single-node example graph: proves Ollama wiring. Replace with real workflows."""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from dev_agents.chat import make_chat_model


class HelloState(TypedDict, total=False):
    topic: str
    reply: str


def _draft(state: HelloState) -> HelloState:
    llm = make_chat_model()
    topic = state.get("topic") or "your homelab"
    msg = llm.invoke(f"Say one short sentence about {topic}.")
    return {"reply": getattr(msg, "content", str(msg))}


def build_graph():
    g = StateGraph(HelloState)
    g.add_node("draft", _draft)
    g.set_entry_point("draft")
    g.add_edge("draft", END)
    return g.compile()


def run(topic: str | None = None) -> HelloState:
    graph = build_graph()
    return graph.invoke({"topic": topic or ""})
