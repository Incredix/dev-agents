"""Extract unified diff text from model replies (fenced diff blocks or raw diff --git)."""

from __future__ import annotations

import re


_FENCE = re.compile(
    r"```(?:diff|udiff)?\s*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def extract_diff_blocks(text: str) -> list[str]:
    """Return non-empty diff chunks from assistant markdown or plain unified diff."""
    if not (text or "").strip():
        return []
    out: list[str] = []
    for m in _FENCE.finditer(text):
        chunk = (m.group(1) or "").strip()
        if chunk and _looks_like_unified_diff(chunk):
            out.append(chunk)
    if out:
        return out
    # Whole message might be a raw unified diff
    t = text.strip()
    if _looks_like_unified_diff(t):
        return [t]
    return []


def _looks_like_unified_diff(s: str) -> bool:
    if "diff --git " in s:
        return True
    lines = s.splitlines()
    for line in lines[:40]:
        if line.startswith("--- ") and "/dev/null" not in line:
            return True
        if line.startswith("+++ ") and "/dev/null" not in line:
            return True
        if line.startswith("@@"):
            return True
    return False


def combine_diff_blocks(blocks: list[str]) -> bytes:
    """Join blocks with blank line; UTF-8 for GNU patch stdin."""
    body = "\n\n".join(b.strip() for b in blocks if b.strip())
    if not body.endswith("\n"):
        body += "\n"
    return body.encode("utf-8")
