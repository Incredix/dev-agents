"""Minimal Ollama chat model wired from env — shared by all graphs in this repo."""

from langchain_ollama import ChatOllama

from dev_agents.config import ollama_base_url, ollama_model


def make_chat_model(**kwargs):
    """Return a ChatOllama instance; kwargs override defaults (temperature, etc.)."""
    base = kwargs.pop("base_url", None) or ollama_base_url()
    model = kwargs.pop("model", None) or ollama_model()
    return ChatOllama(base_url=base, model=model, **kwargs)
