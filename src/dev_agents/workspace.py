"""Filesystem helpers for workspaces listed in AGENT_WORKSPACES (path traversal guarded)."""

import os
from pathlib import Path

from dev_agents.config import workspace_paths

# Repo-relative paths tried for Coder system-context documentation (env extras first).
_DEFAULT_CODER_DOC_REL_PATHS: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "README.markdown",
    "docs/README.md",
    "docs/index.md",
    "BIG_PICTURE.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "DEVELOPMENT.md",
)

_DEFAULT_MAX_READ = 120_000


def resolve_workspace_root(root_arg: str | None, workspace_index: int | None = None) -> Path | None:
    """Pick a checkout root from explicit path, index into AGENT_WORKSPACES, or first entry."""
    paths = workspace_paths()
    if root_arg:
        p = Path(root_arg).expanduser().resolve()
        return p if p.is_dir() else None
    if workspace_index is not None and paths and 0 <= workspace_index < len(paths):
        return paths[workspace_index]
    return paths[0] if paths else None


def read_repo_file(root: Path, relative: str, *, max_bytes: int = _DEFAULT_MAX_READ) -> str:
    """
    Read text under ``root``. Rejects symlink tricks and ``..`` escaping.

    Raises:
        ValueError: if path escapes ``root``, is missing, or is not a regular file.
    """
    rel = Path(relative)
    if rel.is_absolute():
        raise ValueError("relative path must not be absolute")
    root_res = root.resolve()
    target = (root_res / rel).resolve()
    try:
        target.relative_to(root_res)
    except ValueError:
        raise ValueError("path escapes workspace root") from None
    if not target.is_file():
        raise ValueError(f"not a regular file: {relative}")
    data = target.read_bytes()
    truncated = ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = "\n\n[truncated]"
    text = data.decode("utf-8", errors="replace")
    return text + truncated


def _coder_documentation_rel_paths() -> list[str]:
    """Ordered list: ``DEV_AGENTS_CODER_DOC_PATHS`` first, then defaults (deduped)."""
    raw = os.environ.get("DEV_AGENTS_CODER_DOC_PATHS", "")
    extra = [p.strip() for p in raw.split(",") if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for p in extra + list(_DEFAULT_CODER_DOC_REL_PATHS):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _coder_doc_limits() -> tuple[int | None, int]:
    """``DEV_AGENTS_CODER_DOC_MAX_TOTAL`` / ``DEV_AGENTS_CODER_DOC_MAX_PER_FILE`` from env.

    Returns ``(max_total_chars, max_bytes_per_file)``. ``max_total_chars`` ``None`` means no total cap.
    """
    raw_tot = os.environ.get("DEV_AGENTS_CODER_DOC_MAX_TOTAL", "").strip()
    if not raw_tot:
        max_total: int | None = 5_000_000
    elif raw_tot.lower() in ("0", "none", "unlimited"):
        max_total = None
    else:
        try:
            max_total = max(1, int(raw_tot))
        except ValueError:
            max_total = 5_000_000

    raw_pf = os.environ.get("DEV_AGENTS_CODER_DOC_MAX_PER_FILE", "").strip()
    if not raw_pf:
        max_per_file = 2_000_000
    else:
        try:
            v = int(raw_pf)
        except ValueError:
            max_per_file = 2_000_000
        else:
            max_per_file = 10_000_000 if v <= 0 else max(1024, v)

    return max_total, max_per_file


def build_coder_documentation_block(
    root: Path,
    *,
    max_total_chars: int | None = None,
    max_per_file: int | None = None,
) -> str:
    """
    Load text from standard + env-configured doc files for injection into the Coder system prompt.

    Skips missing paths. Size limits default to **large** (multi‑MB); set env to cap or use
    ``DEV_AGENTS_CODER_DOC_MAX_TOTAL=0`` for no total cap.
    """
    if max_total_chars is None and max_per_file is None:
        max_total_chars, max_per_file = _coder_doc_limits()
    else:
        if max_total_chars is None:
            max_total_chars = 5_000_000
        if max_per_file is None:
            max_per_file = 2_000_000

    root = root.resolve()
    parts: list[str] = []
    used = 0
    for rel in _coder_documentation_rel_paths():
        if max_total_chars is not None and used >= max_total_chars:
            break
        try:
            text = read_repo_file(root, rel, max_bytes=max_per_file)
        except (OSError, ValueError):
            continue
        header = f"### `{rel}`\n\n"
        if max_total_chars is None:
            block = header + text
        else:
            remaining = max_total_chars - used
            if remaining <= 0:
                break
            budget = remaining - len(header)
            if budget <= 0:
                break
            if len(text) > budget:
                text = text[:budget].rstrip() + "\n\n[truncated]"
            block = header + text
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
