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


def build_coder_documentation_block(
    root: Path,
    *,
    max_total_chars: int = 14_000,
    max_per_file: int = 7_000,
) -> str:
    """
    Load excerpts from standard + env-configured doc files for injection into the Coder system prompt.

    Skips missing paths. Enforces total size so prompts stay bounded.
    """
    root = root.resolve()
    parts: list[str] = []
    used = 0
    max_bytes = max(1024, min(max_per_file, 120_000))
    for rel in _coder_documentation_rel_paths():
        if used >= max_total_chars:
            break
        try:
            text = read_repo_file(root, rel, max_bytes=max_bytes)
        except (OSError, ValueError):
            continue
        remaining = max_total_chars - used
        if remaining <= 0:
            break
        header = f"### `{rel}`\n\n"
        budget = remaining - len(header)
        if budget <= 0:
            break
        if len(text) > budget:
            text = text[:budget].rstrip() + "\n\n[truncated]"
        block = header + text
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
