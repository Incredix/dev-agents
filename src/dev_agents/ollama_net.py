"""Ollama connectivity checks and human-readable errors (stdlib HTTP, no secrets)."""

from __future__ import annotations

import json
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from dev_agents.config import ollama_base_url


def _tags_url(base: str) -> str:
    return base.rstrip("/") + "/api/tags"


def troubleshooting_block(base_url: str, err: BaseException | None = None) -> str:
    """Multi-line hint after a connection failure (printed to stderr)."""
    parsed = urlparse(base_url)
    host = parsed.hostname or "(invalid URL)"

    msg = str(err).lower() if err is not None else ""
    dns_likely = "name or service not known" in msg or "[errno -2]" in msg or "errno -2" in msg

    lines = [
        f"Cannot reach OLLAMA_BASE_URL={base_url!r} (host {host!r}).",
        "",
    ]
    if err is not None:
        lines.append(f"Underlying error: {err}")
        lines.append("")
    if dns_likely:
        lines.append("This usually means DNS: the hostname does not resolve on *this* machine.")
        lines.append("")

    lines.extend(
        [
            "Typical fixes:",
            f"  • getent hosts {host}",
            "  • If unresolved, avoid the public hostname on this host — use LAN IP:",
            "      export OLLAMA_BASE_URL=http://<ollama-lan-ip>:11434",
            "  • Or add a local /etc/hosts line for development.",
            "  • Sanity check:  dev-agents ollama-check",
        ]
    )
    return "\n".join(lines)


def check_ollama_tags(base_url: str | None = None, *, timeout: float = 20.0) -> int:
    """Print /api/tags model names or diagnostics. Returns 0 on success, else 1."""
    base = (base_url or ollama_base_url()).rstrip("/")
    url = _tags_url(base)
    print(f"Trying {url!r} …", file=sys.stderr)

    ctx = ssl.create_default_context()
    try:
        with urlopen(url, timeout=timeout, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError) as e:
        print(troubleshooting_block(base, e), file=sys.stderr)
        return 1

    models = payload.get("models") or []
    if not models:
        print("(no models in response)", file=sys.stderr)
        return 0
    print("Models:")
    for m in models:
        name = m.get("name") or m.get("model")
        if name:
            print(f"  - {name}")
    return 0
