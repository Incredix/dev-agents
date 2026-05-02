"""Optional git + GitHub CLI workflow: apply a unified diff, commit, push, open PR."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def _run(
    args: list[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
) -> tuple[int, str]:
    r = subprocess.run(
        args,
        cwd=str(cwd.resolve()),
        input=input_bytes,
        capture_output=True,
    )
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    text = (out + ("\n" if out and err else "") + err).strip()
    return r.returncode, text


def git_repo_root(start: Path) -> Path | None:
    code, out = _run(["git", "rev-parse", "--show-toplevel"], cwd=start)
    if code != 0:
        return None
    line = (out or "").strip().splitlines()
    return Path(line[0]).resolve() if line else None


def working_tree_clean(repo: Path) -> tuple[bool, str]:
    code, combined = _run(["git", "status", "--porcelain"], cwd=repo)
    if code != 0:
        return False, combined or "git status failed"
    dirty = bool(combined.strip())
    return not dirty, combined


def sanitize_branch(name: str) -> str:
    s = re.sub(r"[^\w.\-/]", "-", (name or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")[:80]
    return s or "agent-patch"


def apply_patch_commit_push_pr(
    workspace: Path,
    patch_bytes: bytes,
    *,
    branch: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
    strip: int = 1,
    stash_if_dirty: bool = False,
) -> tuple[int, str]:
    """Apply patch in ``workspace``, commit, push, ``gh pr create``. Logs returned as text.

    If ``stash_if_dirty`` is True and the tree has local changes, runs ``git stash push -u``
    before branching; on success returns to the original branch and ``stash pop``.
    """
    logs: list[str] = []
    if not shutil.which("git"):
        return 1, "git not found on PATH"
    if not shutil.which("gh"):
        return 1, "gh (GitHub CLI) not found on PATH — install https://cli.github.com/"
    if not shutil.which("patch"):
        return 1, "GNU patch not found (e.g. sudo apt install patch)"

    repo = git_repo_root(workspace)
    if repo is None:
        return 2, f"Not a git repository (from {workspace})"

    code0, orig_br_out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    if code0 != 0:
        return 2, f"git rev-parse failed:\n{orig_br_out}"
    original_branch = orig_br_out.strip().splitlines()[-1].strip()

    stashed = False

    def unwind_local_branch_and_stash() -> None:
        """Return to original branch, delete feature branch, restore stash."""
        _run(["git", "checkout", original_branch], cwd=repo)
        _run(["git", "branch", "-D", branch_safe], cwd=repo)
        if stashed:
            _, pop_out = _run(["git", "stash", "pop"], cwd=repo)
            logs.append(f"\nstash pop:\n{pop_out}")

    ok, st = working_tree_clean(repo)
    if not ok:
        if stash_if_dirty:
            code_st, stash_out = _run(
                ["git", "stash", "push", "-u", "-m", "dev-agents autopilot"],
                cwd=repo,
            )
            logs.append(f"stash (dirty tree):\n{stash_out}")
            if code_st != 0:
                return 3, "\n".join(logs)
            stashed = True
            ok, st = working_tree_clean(repo)
        if not ok:
            extra = "\n".join(logs) + "\n" if logs else ""
            return 3, (
                extra
                + "Working tree is not clean; commit or stash first.\n"
                + f"git status --porcelain:\n{st}"
            )

    branch_safe = sanitize_branch(branch)
    code, br_out = _run(["git", "checkout", "-b", branch_safe], cwd=repo)
    if code != 0:
        if stashed:
            _, pop_out = _run(["git", "stash", "pop"], cwd=repo)
            return 4, f"git checkout -b failed:\n{br_out}\nstash pop:\n{pop_out}"
        return 4, f"git checkout -b failed:\n{br_out}"

    p = max(0, min(int(strip), 10))
    strip_arg = f"-p{p}"
    dry = subprocess.run(
        ["patch", strip_arg, "--batch", "--forward", "--dry-run"],
        cwd=str(repo),
        input=patch_bytes,
        capture_output=True,
    )
    if dry.returncode != 0:
        err = (dry.stderr or dry.stdout or b"").decode("utf-8", errors="replace")
        unwind_local_branch_and_stash()
        return 5, f"patch dry-run failed:\n{err}\n" + "\n".join(logs)

    real = subprocess.run(
        ["patch", strip_arg, "--batch", "--forward"],
        cwd=str(repo),
        input=patch_bytes,
        capture_output=True,
    )
    if real.returncode != 0:
        err = (real.stderr or real.stdout or b"").decode("utf-8", errors="replace")
        unwind_local_branch_and_stash()
        return 6, f"patch apply failed:\n{err}\n" + "\n".join(logs)

    code, add_out = _run(["git", "add", "-A"], cwd=repo)
    if code != 0:
        unwind_local_branch_and_stash()
        logs.append(add_out)
        return 7, "\n".join(logs)

    code, _qc = _run(["git", "diff", "--cached", "--quiet"], cwd=repo)
    if code == 0:
        unwind_local_branch_and_stash()
        return 8, "No changes staged after patch — nothing to commit.\n" + "\n".join(logs)

    msg = (commit_message or pr_title or "Automated patch").strip()
    code, co_out = _run(["git", "commit", "-m", msg], cwd=repo)
    logs.append(co_out)
    if code != 0:
        unwind_local_branch_and_stash()
        return 9, "\n".join(logs)

    code, pu_out = _run(["git", "push", "-u", "origin", branch_safe], cwd=repo)
    logs.append(pu_out)
    if code != 0:
        unwind_local_branch_and_stash()
        return 10, "\n".join(logs)

    body = (pr_body or "").strip() or "(patch applied via dev-agents UI)"
    title = (pr_title or msg).strip()
    code, pr_out = _run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo,
    )
    logs.append(pr_out)
    if code != 0:
        logs.append(
            "\n(PR failed after push — remote branch may exist; fix gh auth/title and retry "
            "or delete the remote branch manually.)"
        )
        _run(["git", "checkout", original_branch], cwd=repo)
        if stashed:
            _, pop_out = _run(["git", "stash", "pop"], cwd=repo)
            logs.append(f"\nReturned to {original_branch}; stash pop:\n{pop_out}")
        return 11, "\n".join(logs)

    _run(["git", "checkout", original_branch], cwd=repo)
    if stashed:
        _, pop_out = _run(["git", "stash", "pop"], cwd=repo)
        logs.append(f"\nReturned to {original_branch}; stash pop:\n{pop_out}")

    return 0, "\n".join(logs)
