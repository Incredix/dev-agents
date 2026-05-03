"""Minimal Ollama chat model wired from env — shared by all graphs in this repo."""

from langchain_ollama import ChatOllama

from dev_agents.config import ollama_base_url, ollama_http_timeout_seconds, ollama_model


def make_chat_model(**kwargs):
    """Return a ChatOllama instance; kwargs override defaults (temperature, etc.)."""
    base = kwargs.pop("base_url", None) or ollama_base_url()
    model = kwargs.pop("model", None) or ollama_model()
    client_kwargs = dict(kwargs.pop("client_kwargs", None) or {})
    to = ollama_http_timeout_seconds()
    if to is not None and "timeout" not in client_kwargs:
        client_kwargs["timeout"] = to
    extra = {"client_kwargs": client_kwargs} if client_kwargs else {}
    return ChatOllama(base_url=base, model=model, **extra, **kwargs)
