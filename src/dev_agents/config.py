import os
from pathlib import Path


def ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "gemma4:31b")


def workspace_paths() -> list[Path]:
    raw = os.environ.get("AGENT_WORKSPACES", "").strip()
    if not raw:
        return []
    return [Path(p.strip()).expanduser().resolve() for p in raw.split(":") if p.strip()]
