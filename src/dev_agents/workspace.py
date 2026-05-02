"""Filesystem helpers for workspaces listed in AGENT_WORKSPACES (path traversal guarded)."""

from pathlib import Path

from dev_agents.config import workspace_paths

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
