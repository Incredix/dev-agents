import os
from pathlib import Path


def ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:32b")


def ollama_http_timeout_seconds() -> float | None:
    """Optional httpx timeout (seconds) for the Ollama Python client; unset = wait indefinitely."""
    raw = os.environ.get("OLLAMA_TIMEOUT", "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None


def workspace_paths() -> list[Path]:
    raw = os.environ.get("AGENT_WORKSPACES", "").strip()
    if not raw:
        return []
    return [Path(p.strip()).expanduser().resolve() for p in raw.split(":") if p.strip()]
