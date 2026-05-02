"""LangChain tools bound to a single workspace root (read-only search + list)."""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from dev_agents.workspace import read_repo_file


def _safe_child(root: Path, relative: str) -> Path:
    rel = (relative or ".").strip()
    p = Path(rel)
    if p.is_absolute():
        raise ValueError("path must be relative to workspace root")
    rr = root.resolve()
    out = (rr / p).resolve()
    try:
        out.relative_to(rr)
    except ValueError:
        raise ValueError("path escapes workspace root") from None
    return out


def build_workspace_tools(workspace_root: Path):
    """Return tool callables closed over ``workspace_root``."""

    root = workspace_root.resolve()

    @tool
    def read_workspace_file(relative_path: str) -> str:
        """Read a UTF-8 text file under the workspace. Path is relative to repo root (e.g. website/views.py)."""
        try:
            return read_repo_file(root, relative_path)
        except (OSError, ValueError) as e:
            return f"Error: {e}"

    @tool
    def list_workspace_directory(relative_path: str = ".") -> str:
        """List non-hidden names one level under a directory (relative to repo root). Max 200 entries."""
        try:
            d = _safe_child(root, relative_path)
        except ValueError as e:
            return f"Error: {e}"
        if not d.is_dir():
            return f"Error: not a directory: {relative_path!r}"
        names = sorted(p.name for p in d.iterdir() if not p.name.startswith("."))[:200]
        return "\n".join(names) if names else "(empty)"

    @tool
    def grep_workspace(
        regex: str,
        glob_pattern: str = "**/*.py",
        max_matches: int = 50,
    ) -> str:
        """
        Search the workspace with a Python ``re`` regex (not ripgrep PCRE).
        Only scans regular files; skips hidden dirs. ``glob_pattern`` is fnmatch against relpath.
        """
        max_matches = max(1, min(int(max_matches), 200))
        try:
            compiled = re.compile(regex)
        except re.error as e:
            return f"Invalid regex: {e}"
        lines_out: list[str] = []
        count = 0

        for path in root.rglob("*"):
            if count >= max_matches:
                break
            rel = path.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if not path.is_file():
                continue
            if not fnmatch.fnmatch(str(rel), glob_pattern):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if count >= max_matches:
                    break
                if compiled.search(line):
                    lines_out.append(f"{rel}:{i}:{line[:500]}")
                    count += 1

        return "\n".join(lines_out) if lines_out else "(no matches)"

    @tool
    def ripgrep_workspace(
        pattern: str,
        glob_pattern: str = "*.py",
        max_lines: int = 80,
    ) -> str:
        """
        Run ``rg`` (ripgrep) under the workspace if installed; patterns are ripgrep literals.
        Falls back with a note if ``rg`` is missing.
        """
        rg = shutil.which("rg")
        if not rg:
            return "ripgrep (`rg`) not installed; use grep_workspace instead."
        max_lines = max(1, min(int(max_lines), 200))
        cmd = [
            rg,
            "-n",
            "--glob",
            glob_pattern,
            "--max-count",
            "3",
            pattern,
            ".",
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "Error: ripgrep timed out"
        out = (proc.stdout or "").strip()
        if proc.returncode not in (0, 1):
            return f"rg error (exit {proc.returncode}): {(proc.stderr or proc.stdout or '').strip()}"
        if not out:
            return "(no matches)"
        lines = out.splitlines()[:max_lines]
        body = "\n".join(lines)
        if len(out.splitlines()) > max_lines:
            body += "\n[truncated]"
        return body

    return [read_workspace_file, list_workspace_directory, grep_workspace, ripgrep_workspace]
