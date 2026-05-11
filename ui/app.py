"""
Minimal browser UI for dev-agents (localhost — do not expose without auth).

  cd dev-agents && source .venv/bin/activate
  pip install -e ".[ui]"
  python -m streamlit run ui/app.py

Use **`python -m streamlit`** from the **same venv** as **`pip install -e .`**. A global
**`streamlit`** on PATH often points at another Python and loads an older **`dev_agents`**
(so **`run_coder(on_step=...)`** disappears and live progress warns).
"""

from __future__ import annotations

import contextlib
import io
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _env_enabled(key: str, *, default: bool) -> bool:
    """True unless env is set to 0/false/no/off (case-insensitive)."""
    raw = os.environ.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _load_repo_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = _REPO / ".env"
    if env_path.is_file():
        # Repo `.env` should win over stray shell exports (stale OLLAMA_* breaks model list).
        load_dotenv(env_path, override=True)


def _agent_workspaces_raw_from_dotenv_file() -> str:
    """Read AGENT_WORKSPACES from repo .env directly.

    Important: ``load_dotenv(override=False)`` does **not** override variables already set
    in the parent shell (e.g. export AGENT_WORKSPACES=/tcp-only). Colon-separated
    multi-repo values in .env were therefore ignored and the picker never appeared.
    """
    env_path = _REPO / ".env"
    if not env_path.is_file():
        return ""
    last = ""
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("AGENT_WORKSPACES="):
            last = s.split("=", 1)[1].strip().strip('"').strip("'")
    return last


def _suggest_workspace_index(paths: list[str], blob: str) -> tuple[int, str]:
    """Keyword routing: which checkout is the user probably asking about."""
    blob = (blob or "").lower()
    if len(paths) <= 1:
        return 0, "single workspace"

    tcp_hits = (
        "django",
        "website/",
        "manage.py",
        "investment_views",
        "live_trades",
        "traderecord",
        "stripe",
        "tailwind",
        "vite",
        "pcs",
        "/trades/",
        "postgresql",
        "migration",
        "template",
        "discord-bot",
        "broker_",
    )
    vanna_hits = (
        "investment_scanner",
        "background_tasks",
        "tradechef_webhook",
        "fastapi",
        "apscheduler",
        "optionsignals",
        "vanna-api",
        "websocket",
        "uvicorn",
        "alembic",
        "/api/v1/",
        "morning brief",
        "middleware",
        "asyncpg",
    )

    scores: list[int] = []
    for p in paths:
        pl = p.rstrip("/").lower()
        is_tcp_like = pl.endswith("/tcp") or "/tcp/" in pl
        is_vanna_like = "vanna-trade" in pl or "optionsignals" in pl
        s = 0
        if is_tcp_like:
            s += sum(2 for kw in tcp_hits if kw in blob)
        if is_vanna_like:
            s += sum(2 for kw in vanna_hits if kw in blob)
        scores.append(s)

    best = max(scores)
    idx = scores.index(best) if best > 0 else 0
    tail = paths[idx].rstrip("/").split("/")[-1]
    reason = f"matched hints → `{tail}`" if best > 0 else f"no keywords — default `{paths[0].rstrip('/').split('/')[-1]}`"
    return idx, reason


def _mask_url(url: str) -> str:
    if not url:
        return "(unset)"
    u = url.rstrip("/")
    if len(u) <= 36:
        return u
    return u[:28] + "…" + u[-8:]


_TREE_SKIP_DIRS = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".next",
        "dist",
        "build",
        ".eggs",
        "*.egg-info",
        "coverage",
        "htmlcov",
        "staticfiles",
        ".tox",
        ".gradle",
        "target",
        "logs",
        ".streamlit",
    }
)


def _dir_tree_lines(root: Path, *, max_depth: int = 3, max_lines: int = 220) -> list[str]:
    """Ascii tree depth-limited; skips bulky / generated dirs."""

    lines: list[str] = []

    def walk(p: Path, prefix: str, depth: int) -> None:
        if depth > max_depth or len(lines) >= max_lines:
            return
        try:
            childs = sorted(
                p.iterdir(),
                key=lambda x: (not x.is_dir(), x.name.lower()),
            )
        except (PermissionError, OSError) as exc:
            lines.append(f"{prefix}[{exc.__class__.__name__}]")
            return
        dirs = []
        files = []
        for c in childs:
            if c.name.startswith(".") and c.name != ".env":
                continue
            if c.is_dir() and (c.name in _TREE_SKIP_DIRS or c.name.endswith(".egg-info")):
                continue
            if c.is_symlink():
                continue
            (dirs if c.is_dir() else files).append(c)
        ordered = dirs + files[: max(0, 80 - len(dirs))]
        total = len(ordered)
        for i, item in enumerate(ordered):
            if len(lines) >= max_lines:
                lines.append(prefix + "… [truncated]")
                return
            branch = "├── " if i < total - 1 else "└── "
            mark = "/" if item.is_dir() else ""
            lines.append(f"{prefix}{branch}{item.name}{mark}")
            if item.is_dir() and depth < max_depth:
                ext = "│   " if i < total - 1 else "    "
                walk(item, prefix + ext, depth + 1)

    try:
        root_res = root.resolve()
    except (OSError, RuntimeError):
        root_res = root
    lines.append(str(root_res) + "/")
    if root_res.is_dir():
        walk(root_res, "", 0)
    elif root_res.exists():
        lines.append("  (single file)")
    else:
        lines.append("  (does not exist or unreadable)")
    return lines


def _workspace_overview_markdown(path_str: str) -> str:
    """Short human map for stacks we recognize; still useful as a checklist."""
    tail = Path(path_str).resolve().name if path_str else ""
    lp = path_str.lower()
    tcp_like = lp.endswith("/tcp") or "/tcp/" in lp
    vn_like = "vanna-trade" in lp or "optionsignals" in lp

    if tcp_like:
        return f"""##### TradeChefPro stack (`{tail}`)

| Area | Where to look |
|------|----------------|
| Django app | **`website/`** — views (`vanna_views`, `investment_views`, …), **`models.py`**, **`templates/`**, **`broker_*`** |
| Settings / URLs | **`tcp/`** |
| PCS / trades API | **`website/trades_engine.py`**, **`website/vanna_client.py`**, SPA in **`trades-ui/`** (Vite) |
| Live alerts ingest | **`website/live_trades_views.py`** (consumer of vanna webhook fan-out) |
| Discord bot | **`discord-bot/`** |
| Scripts & cron wrappers | **`scripts/`** |

_Docs: **`README.md`**, **`docs/`**._

"""
    if vn_like:
        return f"""##### OptionsSignals / vanna-trade (`{tail}`)

| Area | Where to look |
|------|----------------|
| FastAPI entry | **`backend/main.py`** (routers mounted here) |
| Investment desk / scheduler | **`backend/investment_scanner.py`**, job wiring in **`background_tasks`** / similar |
| TradingView webhook | **`backend/tradechef_webhook.py`** (+ discord helpers beside it) |
| Analytics / signals | **`backend/analytics.py`**, **`signals.py`** |
| Data / sync | **`backend/data_sync.py`**, **`cache.py`** |

**Env & ops:** **`README.md`**, **`BIG_PICTURE.md`**, **`ARCHITECTURE.md`** (repo root); production deploy is often **`/opt/OptionsSignals`** on the API host.

"""
    return f"""##### `{tail or path_str}`

_No built-in map for this folder._ Use the shallow tree below, or tell **Plan/Coder** a file path (`-r`) or **`grep`** target.

"""


_load_repo_dotenv()

import streamlit as st  # noqa: E402

MODEL_OVERRIDE_USE_ENV = "— use default (OLLAMA_MODEL) —"


def _ollama_base_normalized() -> str:
    return (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def _extra_model_names_from_env() -> list[str]:
    """Optional comma-separated favorites from ``OLLAMA_EXTRA_MODELS`` (always shown in dropdown)."""
    raw = os.environ.get("OLLAMA_EXTRA_MODELS", "").strip()
    if not raw:
        return []
    return sorted({p.strip() for p in raw.split(",") if p.strip()})


def _fetch_ollama_model_names() -> list[str]:
    """Names from ``GET /api/tags`` for the configured ``OLLAMA_BASE_URL``."""
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur

    base = _ollama_base_normalized()
    url = f"{base}/api/tags"
    req = _ur.Request(url, headers={"Accept": "application/json"})
    try:
        with _ur.urlopen(req, timeout=6.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = _json.loads(raw)
        models = data.get("models") or []
        names = [m.get("name") for m in models if isinstance(m, dict) and m.get("name")]
        return sorted(set(names))
    except (_ue.URLError, _ue.HTTPError, TimeoutError, OSError, ValueError, TypeError):
        return []


def _model_override_options() -> tuple[list[str], list[str]]:
    """Return (selectbox_options, tag_names_from_server)."""
    env_default = (os.environ.get("OLLAMA_MODEL") or "qwen2.5-coder:32b").strip()
    base = _ollama_base_normalized()
    cached_base = st.session_state.get("ollama_tags_cached_base")
    if (
        "ollama_tag_names" not in st.session_state
        or cached_base != base
    ):
        st.session_state["ollama_tag_names"] = _fetch_ollama_model_names()
        st.session_state["ollama_tags_cached_base"] = base
    tags = list(st.session_state.get("ollama_tag_names") or [])
    extras = _extra_model_names_from_env()
    merged = sorted(
        set(tags)
        | ({env_default} if env_default else set())
        | set(extras),
    )
    opts = [MODEL_OVERRIDE_USE_ENV] + merged
    return opts, tags


st.set_page_config(page_title="dev-agents", layout="wide")
if "last_coder_reply" not in st.session_state:
    st.session_state["last_coder_reply"] = ""
if "patch_text_area" not in st.session_state:
    st.session_state["patch_text_area"] = ""


def _autopilot_from_coder(
    reply: str,
    workspace: str,
    *,
    strip: int,
    open_pr: bool,
    instruction: str,
    stash_if_dirty: bool,
) -> None:
    """Extract unified diff from Coder reply → patch apply and/or gh PR."""
    from datetime import datetime as _adt
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    from dev_agents.diff_extract import combine_diff_blocks, extract_diff_blocks
    from dev_agents.git_pr import apply_patch_commit_push_pr
    from dev_agents.patch_apply import run_patch

    root = Path((workspace or "").strip())
    if not root.is_dir():
        st.error("Autopilot: workspace path is not a directory.")
        return
    blocks = extract_diff_blocks(reply)
    if not blocks:
        return
    data = combine_diff_blocks(blocks)
    hint = ((instruction or "autopilot").strip()[:72] or "autopilot").replace("\n", " ")
    branch = f"agent-{_adt.now().strftime('%Y%m%d-%H%M%S')}"
    st.session_state["patch_text_area"] = data.decode("utf-8", errors="replace")

    with st.expander("Autopilot — patch → repo", expanded=True):
        st.caption(f"`{branch}` · patch **-p{strip}** · {'PR (git+gh)' if open_pr else 'local patch only'}")
        if open_pr:
            code, log = apply_patch_commit_push_pr(
                root,
                data,
                branch=branch,
                commit_message=f"autopilot: {hint}",
                pr_title=f"autopilot: {hint}",
                pr_body="Opened by dev-agents Autopilot (Coder → diff → gh).\n\n_(Review before merge.)_",
                strip=strip,
                stash_if_dirty=stash_if_dirty,
            )
            st.code(log or "(no output)", language="text")
            if code == 0:
                st.success("Autopilot finished (exit 0). Check GitHub for the PR.")
            else:
                st.error(f"Autopilot PR path failed (exit {code}). Fix git/gh auth or use local patch only.")
        else:
            with NamedTemporaryFile(mode="wb", suffix=".patch", delete=False) as tf:
                tf.write(data)
                tpath = Path(tf.name)
            try:
                code = run_patch(root, tpath, strip=strip, do_apply=True)
            finally:
                tpath.unlink(missing_ok=True)
            if code == 0:
                st.success("Autopilot: patch applied (no PR). Review with git diff.")
            else:
                st.error("Autopilot: patch apply failed — try Dry-run on the Patch tab.")


st.title("dev-agents")
st.caption(
    "LangGraph + Ollama — **`streamlit`** binds to **`127.0.0.1`** by default. "
    "Not for public internet."
)

with st.sidebar:
    st.subheader("Config")
    ost = os.environ.get("OLLAMA_BASE_URL") or "(unset)"
    model_env = os.environ.get("OLLAMA_MODEL") or "(unset)"
    wsp_raw = (_agent_workspaces_raw_from_dotenv_file().strip()
               or os.environ.get("AGENT_WORKSPACES", "").strip())
    st.text_area(
        "OLLAMA_BASE_URL (masked)",
        _mask_url(ost) if ost != "(unset)" else ost,
        height=68,
        disabled=True,
    )
    st.text_input("OLLAMA_MODEL (from env)", value=model_env, disabled=True)
    ov_opts, _ov_tags = _model_override_options()
    st.selectbox(
        "Per-run model override",
        options=ov_opts,
        index=0,
        key="model_override_select",
        help=(
            "Combines `/api/tags` from **OLLAMA_BASE_URL**, **OLLAMA_MODEL**, and optional **OLLAMA_EXTRA_MODELS**. "
            "If you only see wrong models, your URL may still point at another host (e.g. local Docker)."
        ),
    )
    st.caption(
        f"Tags above come from **`{_mask_url(_ollama_base_normalized())}`** "
        f"({len(_ov_tags)} from server). "
        f"Override `.env` **`OLLAMA_BASE_URL`** to the machine that has **qwen/gemma**, then **Refresh**."
    )
    if st.button("Refresh model list", key="ollama_tags_refresh", help="Re-fetch `/api/tags`"):
        st.session_state["ollama_tag_names"] = _fetch_ollama_model_names()
        st.session_state["ollama_tags_cached_base"] = _ollama_base_normalized()
        st.rerun()
    if not _ov_tags:
        st.caption(
            "Could not reach Ollama `/api/tags` — dropdown shows **OLLAMA_MODEL** + **OLLAMA_EXTRA_MODELS** only."
        )
    st.subheader("Autopilot")
    full_autopilot = _env_enabled("DEV_AGENTS_AUTOPILOT", default=True)
    if full_autopilot:
        st.success(
            "Full autopilot — Coder→diff→stash if needed→apply/PR. "
            "`DEV_AGENTS_AUTOPILOT=0` restores manual checkboxes."
        )
        st.caption(
            "`DEV_AGENTS_AUTOPILOT_LOCAL_ONLY=1` → workspace patch only (no git/gh). "
            "`DEV_AGENTS_AUTOPILOT_STASH=0` → abort if repo is dirty (no auto-stash)."
        )
        autopilot_enabled = True
        autopilot_after_coder = True
        autopilot_open_pr = not _env_enabled("DEV_AGENTS_AUTOPILOT_LOCAL_ONLY", default=False)
        autopilot_strip = st.number_input(
            "Autopilot patch -p",
            min_value=0,
            max_value=10,
            value=1,
            key="sidebar_autopilot_strip",
        )
    else:
        autopilot_enabled = st.checkbox(
            "Autopilot",
            value=False,
            key="sidebar_autopilot",
            help="Skip Patch-tab confirmations; after Coder, auto extract fenced unified diff → apply or open PR.",
        )
        autopilot_after_coder = st.checkbox(
            "After Coder → apply diff (+ PR)",
            value=True,
            key="sidebar_autopilot_chain",
            disabled=not autopilot_enabled,
            help="When Coder returns a fenced unified diff, run patch / gh automatically.",
        )
        autopilot_open_pr = st.checkbox(
            "Use GitHub PR (git+gh)",
            value=True,
            key="sidebar_autopilot_pr",
            disabled=not autopilot_enabled or not autopilot_after_coder,
            help="If off: only GNU patch into workspace (no branch/push). If on: requires clean tree + gh auth.",
        )
        autopilot_strip = st.number_input(
            "Autopilot patch -p",
            min_value=0,
            max_value=10,
            value=1,
            key="sidebar_autopilot_strip",
            disabled=not autopilot_enabled,
        )
    _ws_paths = [p.strip() for p in wsp_raw.split(":") if p.strip()]
    st.caption(
        f"**AGENT_WORKSPACES**: `{len(_ws_paths)}` path(s); workspace picker is **below** (routing + auto-pick)."
    )

    import inspect as _inspect
    import sys as _sys

    import dev_agents as _dev_agents
    from dev_agents.graphs.coder_react import run_coder as _run_coder_for_probe

    _has_on_step = "on_step" in _inspect.signature(_run_coder_for_probe).parameters
    with st.expander("Python / dev_agents (diagnostics)", expanded=not _has_on_step):
        st.code(
            f"sys.executable:\n{_sys.executable}\n\n"
            f"dev_agents:\n{_dev_agents.__file__}\n\n"
            f"run_coder has on_step: {_has_on_step}\n",
            language="text",
        )
        if not _has_on_step:
            st.warning(
                "Live Coder steps need **`on_step`** on **`run_coder`**. "
                "Your **Streamlit process** is loading an old **`dev_agents`**. "
                "From **this repo**: activate the venv, **`pip install -e .`**, then run "
                "**`python -m streamlit run ui/app.py`** (not a random **`streamlit`** on PATH)."
            )


# ─── Workspace (main column: must render after sidebar; uses Plan/Coder hints from session) ─────
wsp_main = (_agent_workspaces_raw_from_dotenv_file().strip() or os.environ.get("AGENT_WORKSPACES", "").strip())
_ws_paths_main = [p.strip() for p in wsp_main.split(":") if p.strip()]

if len(_ws_paths_main) > 1:
    st.divider()
    col_r1, col_r2 = st.columns((3, 1))
    with col_r1:
        st.text_input(
            "Routing hint (optional)",
            value="",
            key="routing_ws_hint",
            placeholder="e.g. morning brief in investment_scanner",
            help="Combined with Plan + Coder text when auto-pick is on.",
        )
    with col_r2:
        auto_ws = st.checkbox("Auto-pick workspace", True, key="workspace_auto_pick")

    _blob = "".join(
        [
            str(st.session_state.get("routing_ws_hint", "") or ""),
            str(st.session_state.get("plan_i", "") or ""),
            str(st.session_state.get("coder_i", "") or ""),
        ]
    ).lower()
    _sidx, _swhy = _suggest_workspace_index(_ws_paths_main, _blob)

    if auto_ws:
        st.session_state["ws_radio"] = _ws_paths_main[_sidx]

    _picked_rad = st.radio(
        "Workspace for Plan / Coder",
        options=_ws_paths_main,
        horizontal=True,
        key="ws_radio",
    )
    st.caption(f"**{_swhy}**" + (" · auto-pick on" if auto_ws else ""))

    wo = st.text_input("Override path (optional)", value="", key="dev_agents_workspace_override")
    workspace_abs = (wo.strip() or _picked_rad)
else:
    workspace_abs = st.text_input(
        "Workspace root (-w)",
        value=_ws_paths_main[0] if _ws_paths_main else "",
        key="workspace_single_path",
        placeholder="/abs/path/to/tcp",
    )

paths_for_trees: list[str] = []
seen_tp: set[str] = set()
for _p in _ws_paths_main:
    px = (_p or "").strip()
    if px and px not in seen_tp:
        seen_tp.add(px)
        paths_for_trees.append(px)
wxa = (workspace_abs or "").strip()
if wxa and wxa not in seen_tp:
    paths_for_trees.append(wxa)

with st.expander("Workspace maps — overview & shallow trees", expanded=False):
    st.caption(
        "One block per **`AGENT_WORKSPACES`** path (plus your override if it differs). "
        "Skips `.git`, `node_modules`, `venv`, `__pycache__`, etc."
    )
    if not paths_for_trees:
        st.info("No workspace paths configured.")
    for ti, wp in enumerate(paths_for_trees):
        label = Path(wp).name or wp
        with st.expander(f"**{label}** — `{wp}`", expanded=False):
            st.markdown(_workspace_overview_markdown(wp))
            c1, c2 = st.columns(2)
            with c1:
                treed = st.slider("Tree depth", 1, 5, 3, key=f"_map_depth_{ti}")
            with c2:
                linecap = st.number_input(
                    "Line cap",
                    min_value=60,
                    max_value=400,
                    value=220,
                    step=20,
                    key=f"_map_lines_{ti}",
                )
            rootp = Path(wp)
            if not rootp.is_dir():
                st.warning("Not a directory or path is missing.")
            else:
                body = "\n".join(
                    _dir_tree_lines(rootp, max_depth=int(treed), max_lines=int(linecap))
                )
                st.code(body, language="text")

tabs = st.tabs(
    ["Ollama check", "Hello", "Plan", "Coder", "Patch & PR", "Queue", "Aider overnight"]
)


def _model_arg() -> str | None:
    sel = st.session_state.get("model_override_select")
    if sel is None or sel == MODEL_OVERRIDE_USE_ENV:
        return None
    s = str(sel).strip()
    return s or None


with tabs[0]:
    if st.button("Fetch /api/tags"):
        from dev_agents.ollama_net import check_ollama_tags

        stderr_buf = io.StringIO()
        stdout_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf), contextlib.redirect_stdout(stdout_buf):
            code = check_ollama_tags()
        text = stderr_buf.getvalue() + stdout_buf.getvalue()
        if code != 0:
            st.error("Failed")
        else:
            st.success("OK")
        st.code(text.strip(), language="text")


with tabs[1]:
    topic = st.text_input("Topic", value="smoke", key="hello_topic")
    if st.button("Run hello", key="hello_btn"):
        from dev_agents.graphs.hello import run as hello_run

        with st.spinner("Calling Ollama…"):
            out = hello_run(topic or None, model=_model_arg())
        st.write(out.get("reply", str(out)))

with tabs[2]:
    instr = st.text_area("Instruction", height=120, key="plan_i")
    rel = st.text_input("Optional `-r` file (repo-relative)", value="", key="plan_r")
    if st.button("Run plan", key="plan_btn"):
        from pathlib import Path as P

        from dev_agents.graphs.code_plan import run as plan_run

        ws = workspace_abs.strip() or None
        if not ws or not P(ws).is_dir():
            st.error("Set a valid workspace root (sidebar `-w`).")
        else:
            with st.spinner("Planning…"):
                res = plan_run(
                    instr,
                    read_rel=rel.strip() or None,
                    workspace_root=ws,
                    model=_model_arg(),
                )
            st.markdown(res.get("reply", str(res)))

with tabs[3]:
    instr_c = st.text_area("Coder instruction", height=160, key="coder_i")
    col_a, col_b = st.columns(2)
    with col_a:
        rec_lim = st.number_input("Recursion limit", min_value=10, max_value=200, value=40)
    with col_b:
        thread_id = st.text_input("Thread id", value="streamlit")
    fresh_coder_thread = st.checkbox(
        "Fresh thread each run",
        value=True,
        key="coder_fresh_thread",
        help=(
            "LangGraph SQLite checkpoints key by thread id. With the same id, every Run **appends** "
            "to the prior conversation — the model still sees old instructions (e.g. an earlier SPX vs SPY ask). "
            "Turn this on for a new checkpoint thread each run; turn off only to **continue** one long session."
        ),
    )
    no_ckpt = st.checkbox("No SQLite checkpoint (--no-checkpoint)", value=False)
    coder_verbose = st.checkbox(
        "Verbose step log (model/tool previews)",
        value=False,
        key="coder_verbose",
        help="Captures LangGraph snapshots after each superstep.",
    )
    coder_live = st.checkbox(
        "Show live progress (each graph step)",
        value=True,
        key="coder_live",
        help=(
            "Streams LangGraph states so you see model vs tool turns. "
            "Turn off for slightly less overhead — you only get the final spinner/text."
        ),
    )
    if st.button("Run coder", key="coder_btn"):
        import inspect
        import time
        from pathlib import Path as P

        from dev_agents.graphs.coder_react import run_coder

        coder_params = inspect.signature(run_coder).parameters
        coder_supports_on_step = "on_step" in coder_params

        ws = workspace_abs.strip()
        if not ws or not P(ws).is_dir():
            st.error("Set a valid workspace root (sidebar `-w`).")
        elif not (instr_c or "").strip():
            st.error("Enter a **Coder instruction** (empty instructions often yield no visible reply).")
        else:
            base_tid = (thread_id or "streamlit").strip() or "streamlit"
            effective_thread_id = (
                f"{base_tid}-{uuid.uuid4().hex[:12]}"
                if fresh_coder_thread
                else base_tid
            )
            if fresh_coder_thread:
                st.caption(f"Checkpoint thread for this run: `{effective_thread_id}`")

            def _coder_kwargs(on_step_cb=None):
                kw = dict(
                    model=_model_arg(),
                    thread_id=effective_thread_id,
                    recursion_limit=int(rec_lim),
                    use_checkpoint=not no_ckpt,
                    step_log=steps if coder_verbose else None,
                )
                if on_step_cb is not None and coder_supports_on_step:
                    kw["on_step"] = on_step_cb
                return kw

            steps: list[str] = []
            txt = ""
            spinner_msg = (
                "**Why it can look frozen:** After you see your **`user:`** line, the next log line only appears "
                "when **Ollama finishes the whole model turn**. There is **no** token-by-token progress in this UI.\n\n"
                "**Large / remote models are slow:** e.g. **`gemma4:31b`** over **`OLLAMA_BASE_URL`** can take "
                "**several minutes** (cold load, queue, network). That silence is normal — not a stuck checkpoint.\n\n"
                "**Quick checks:** **Ollama check** tab → `/api/tags`; sidebar **Per-run model override** with a "
                "smaller tag for a fast sanity test; optional **`OLLAMA_TIMEOUT`** in `.env` (seconds) if you want "
                "requests to fail instead of hanging forever."
            )
            try:
                if coder_live:
                    if not coder_supports_on_step:
                        st.warning(
                            "Installed **`dev_agents`** package is older than this UI (`run_coder` has no **`on_step`**). "
                            "Run **`pip install -e .`** from **`dev-agents/`** or **`pip install -U git+…`** so library and "
                            "**`ui/app.py`** stay in sync, then restart Streamlit. "
                            "**Live progress will be skipped** for this run."
                        )
                    st.markdown(spinner_msg)
                    if hasattr(st, "status"):
                        with st.status("Coder agent — live steps", expanded=True) as status:

                            def _on_step(i: int, line: str) -> None:
                                if i < 0:
                                    status.write(f"**Finalize** · {line}")
                                    return
                                ts = time.strftime("%H:%M:%S")
                                status.write(f"`{ts}` **#{i}** · {line}")

                            txt = run_coder(
                                instr_c,
                                workspace_root=P(ws),
                                **_coder_kwargs(_on_step),
                            )
                    else:
                        st.caption(
                            "Install **`streamlit>=1.33`** for `st.status` UI; "
                            "showing a rolling log below instead."
                        )
                        prog = st.empty()
                        live_buf: list[str] = []

                        def _on_step_roll(i: int, line: str) -> None:
                            if i < 0:
                                live_buf.append("finalize · " + line)
                            else:
                                live_buf.append(f"{time.strftime('%H:%M:%S')} #{i} · {line}")
                            prog.code("\n".join(live_buf[-24:]), language="text")

                        txt = run_coder(
                            instr_c,
                            workspace_root=P(ws),
                            **_coder_kwargs(_on_step_roll),
                        )
                else:
                    st.markdown(spinner_msg)
                    with st.spinner("Coder agent (may take a minute)…"):
                        txt = run_coder(
                            instr_c,
                            workspace_root=P(ws),
                            **_coder_kwargs(None),
                        )
            except Exception as exc:  # noqa: BLE001
                st.exception(exc)
                txt = ""
            if coder_verbose and steps:
                with st.expander("Coder trace (verbose)", expanded=True):
                    st.code("\n".join(steps), language="text")
            if (txt or "").strip():
                st.session_state["last_coder_reply"] = txt
            body = txt or "(empty reply)"
            b = body.strip()
            if b.startswith("{") and "\"name\"" in b and "\"arguments\"" in b:
                st.warning(
                    "The model leaked a tool-call JSON without finishing the turn — "
                    "try again or bump recursion limit."
                )
                st.code(body, language="json")
            elif "<tool_call" in body.lower() or "<tool|" in body.lower():
                st.warning("Model emitted template junk after JSON; refresh and retry, or lower temperature.")
                st.code(body)
            else:
                st.markdown(body)

            if (
                autopilot_enabled
                and autopilot_after_coder
                and (txt or "").strip()
            ):
                _autopilot_from_coder(
                    txt,
                    ws,
                    strip=int(autopilot_strip),
                    open_pr=autopilot_open_pr,
                    instruction=instr_c or "",
                    stash_if_dirty=_env_enabled("DEV_AGENTS_AUTOPILOT_STASH", default=True),
                )

with tabs[4]:
    st.markdown(
        "Apply a **unified diff** to the **same workspace** as Plan/Coder (GNU **`patch`**). "
        "Optional: **`gh`** creates a branch, commit, push, and opens a **GitHub PR**."
    )
    from datetime import datetime as _dt

    from dev_agents.diff_extract import combine_diff_blocks, extract_diff_blocks
    from dev_agents.git_pr import apply_patch_commit_push_pr
    from dev_agents.patch_apply import run_patch

    p_ws = (workspace_abs or "").strip()
    c_load, c_clear = st.columns(2)
    with c_load:
        if st.button("Load diff from last Coder reply", key="patch_load_last"):
            blocks = extract_diff_blocks(st.session_state.get("last_coder_reply", "") or "")
            if not blocks:
                st.warning("No unified diff found — run **Coder** first or paste a diff below.")
            else:
                st.session_state["patch_text_area"] = combine_diff_blocks(blocks).decode("utf-8")
                st.rerun()
    with c_clear:
        if st.button("Clear patch text", key="patch_clear"):
            st.session_state["patch_text_area"] = ""
            st.rerun()

    patch_txt = st.text_area(
        "Unified diff (paste or load)",
        height=220,
        key="patch_text_area",
        placeholder="--- a/foo\n+++ b/foo\n@@ ...",
    )
    ps_col1, ps_col2, ps_col3 = st.columns(3)
    with ps_col1:
        patch_strip = st.number_input("patch -p", min_value=0, max_value=10, value=1)
    with ps_col2:
        st.caption(f"Workspace: `{p_ws or '(unset)'}`")
    with ps_col3:
        fn = f"dev-agents-{_dt.now().strftime('%Y%m%d-%H%M%S')}.patch"
        st.download_button(
            label="Download .patch",
            data=(patch_txt or "").encode("utf-8"),
            file_name=fn,
            mime="text/plain",
            disabled=not (patch_txt or "").strip(),
        )

    def _patch_bytes() -> bytes | None:
        raw = (patch_txt or "").strip()
        if not raw:
            st.error("Paste a unified diff or load from Coder.")
            return None
        b = raw.encode("utf-8")
        if not b.endswith(b"\n"):
            b += b"\n"
        return b

    st.divider()
    st.subheader("Apply locally")
    if autopilot_enabled:
        st.caption("Autopilot on — **Apply** is one click (no safety checkbox).")
        apply_confirm = True
    else:
        apply_confirm = st.checkbox(
            "I want to modify files under the workspace with GNU patch (not dry-run).",
            value=False,
            key="patch_apply_confirm",
        )
    ac1, ac2 = st.columns(2)
    with ac1:
        if st.button("Dry-run patch", key="patch_dry"):
            data = _patch_bytes()
            if data is not None:
                from tempfile import NamedTemporaryFile

                root = Path(p_ws)
                if not p_ws or not root.is_dir():
                    st.error("Set a valid workspace root above.")
                else:
                    with NamedTemporaryFile(mode="wb", suffix=".patch", delete=False) as tf:
                        tf.write(data)
                        tpath = Path(tf.name)
                    try:
                        code = run_patch(root, tpath, strip=int(patch_strip), do_apply=False)
                    finally:
                        tpath.unlink(missing_ok=True)
                    if code == 0:
                        st.success("Dry-run OK — patch would apply.")
                    else:
                        st.error("Dry-run failed (see stderr from patch).")
    with ac2:
        if st.button("Apply patch", key="patch_apply", disabled=not apply_confirm):
            data = _patch_bytes()
            if data is not None:
                from tempfile import NamedTemporaryFile

                root = Path(p_ws)
                if not p_ws or not root.is_dir():
                    st.error("Set a valid workspace root above.")
                else:
                    with NamedTemporaryFile(mode="wb", suffix=".patch", delete=False) as tf:
                        tf.write(data)
                        tpath = Path(tf.name)
                    try:
                        code = run_patch(root, tpath, strip=int(patch_strip), do_apply=True)
                    finally:
                        tpath.unlink(missing_ok=True)
                    if code == 0:
                        st.success("Applied. Review with **git diff** in that repo.")
                    else:
                        st.error("Apply failed.")

    st.divider()
    st.subheader("GitHub PR (git + gh)")
    st.caption(
        "Requires **`gh auth login`**, **`git push`** to **origin**. "
        "Dirty tree: auto **`git stash`** before branching when **`DEV_AGENTS_AUTOPILOT_STASH`** is unset or 1."
    )
    pr_branch = st.text_input("Branch name", value="dev-agents-patch", key="pr_branch")
    pr_commit = st.text_input("Commit message", value="Apply patch from dev-agents UI", key="pr_commit")
    pr_title = st.text_input("PR title", value="Patch from dev-agents", key="pr_title")
    pr_body = st.text_area("PR body", height=100, value="", key="pr_body", placeholder="What changed…")
    if autopilot_enabled:
        st.caption("Autopilot on — **Create PR** is one click.")
        pr_confirm = True
    else:
        pr_confirm = st.checkbox(
            "I confirm clean working tree and GitHub CLI auth — create branch, push, open PR.",
            value=False,
            key="pr_confirm",
        )
    if st.button("Create PR", key="pr_create_btn", disabled=not pr_confirm):
        data = _patch_bytes()
        if data is not None:
            root = Path(p_ws)
            if not p_ws or not root.is_dir():
                st.error("Set a valid workspace root above.")
            else:
                code, log = apply_patch_commit_push_pr(
                    root,
                    data,
                    branch=pr_branch.strip() or "dev-agents-patch",
                    commit_message=pr_commit.strip(),
                    pr_title=pr_title.strip(),
                    pr_body=pr_body.strip(),
                    strip=int(patch_strip),
                    stash_if_dirty=_env_enabled("DEV_AGENTS_AUTOPILOT_STASH", default=True),
                )
                if code == 0:
                    st.success("Done.")
                else:
                    st.error(f"Exit {code}")
                st.code(log or "(no output)", language="text")

with tabs[5]:
    st.markdown(
        "Run **multiple** Coder tasks in order (like `dev-agents queue`). "
        "Separate each task with a line that contains only **`---`**. "
        "Uses the **Workspace** and **model override** from the sidebar. "
        "After each task: extract unified diff → **git + gh PR** (or local **patch** only). "
        "If the first reply has **no** parseable diff, the runner sends **one** automatic follow-up asking for a diff "
        "(unless you opted out with phrases like *read-only* / *analysis only*, or the model says `NO_CODE_CHANGE:`). "
        "Set **`DEV_AGENTS_QUEUE_DIFF_RETRY=0`** to disable."
    )
    st.text_area(
        "Prefix every task (optional)",
        height=120,
        key="queue_task_prefix",
        placeholder=(
            "Stack hint repeated before each --- block, e.g.\n"
            "Workspace is TradeChef Django tcp (website/, trades-ui/). …"
        ),
        help=(
            "Prepended to each task’s instruction for this run only. "
            "Leave empty for no prefix (CLI/overnight runs can use DEV_AGENTS_QUEUE_TASK_PREFIX)."
        ),
    )
    st.text_area(
        "Queue (tasks separated by ---)",
        height=260,
        key="queue_body",
        placeholder="# First task\nDescribe the change…\n\n---\n\n# Second task\n…",
    )
    ex_path = _REPO / "scripts" / "queue.example.txt"
    if st.button("Load example", key="queue_load_example"):
        if ex_path.is_file():
            st.session_state["queue_body"] = ex_path.read_text(encoding="utf-8", errors="replace")
            st.rerun()
        else:
            st.warning("Example file missing — add `scripts/queue.example.txt` in the dev-agents repo.")
    qcol1, qcol2, qcol3 = st.columns(3)
    with qcol1:
        q_rec = st.number_input("Recursion / task", 10, 200, 40, key="queue_rec_lim")
        q_strip = st.number_input("patch -p", 0, 10, 1, key="queue_strip")
    with qcol2:
        q_sleep = st.number_input("Sleep (s) between tasks", 0.0, 3600.0, 0.0, 5.0, key="queue_sleep")
        q_log = st.text_input(
            "Log file (append)",
            value=str(_REPO / "dev-agents-queue.log"),
            key="queue_log_path",
        )
    with qcol3:
        q_local = st.checkbox("Local patch only (no git/gh)", key="queue_local_only")
        q_ff = st.checkbox("Fail fast", key="queue_fail_fast")
        q_verbose = st.checkbox("Verbose (stderr trace)", key="queue_verbose")

    c_save, c_run, c_bg = st.columns(3)
    with c_save:
        if st.button("Save queue to `queue.pending.txt`", key="queue_save_btn"):
            qb = (st.session_state.get("queue_body") or "").strip()
            if not qb:
                st.error("Queue text is empty.")
            else:
                out_p = _REPO / "queue.pending.txt"
                out_p.write_text(qb, encoding="utf-8")
                st.success(f"Wrote `{out_p}`")
    with c_run:
        run_queue_clicked = st.button("Run queue here", type="primary", key="queue_run_blocking")
    with c_bg:
        bg_clicked = st.button("Start queue in background", key="queue_run_bg")

    if run_queue_clicked:
        from pathlib import Path as P

        from dev_agents.queue_run import run_queue_from_text

        qb = (st.session_state.get("queue_body") or "").strip()
        ws = (workspace_abs or "").strip()
        if not ws or not P(ws).is_dir():
            st.error("Set a valid workspace root above.")
        elif not qb:
            st.error("Enter at least one task in the queue.")
        else:
            lp = Path((st.session_state.get("queue_log_path") or "").strip() or (_REPO / "dev-agents-queue.log"))
            holder: dict = {"code": None, "exc": None}

            def _queue_worker() -> None:
                try:
                    holder["code"] = run_queue_from_text(
                        qb,
                        log_path=lp,
                        workspace=ws,
                        workspace_index=None,
                        model=_model_arg(),
                        recursion_limit=int(q_rec),
                        strip=int(q_strip),
                        local_patch_only=bool(q_local),
                        fail_fast=bool(q_ff),
                        sleep=float(q_sleep),
                        verbose=bool(q_verbose),
                        task_prefix=st.session_state.get("queue_task_prefix", ""),
                    )
                except Exception as e:  # noqa: BLE001
                    holder["exc"] = e

            th = threading.Thread(target=_queue_worker, daemon=True, name="dev-agents-queue")
            th.start()
            live = st.empty()
            st.caption(
                "**Live log** — tail refreshes ~2×/s while tasks run. "
                "(Verbose stderr still goes to the Streamlit terminal unless captured.)"
            )
            _max_live = 180_000
            _last = ""
            while th.is_alive():
                try:
                    if lp.is_file():
                        raw = lp.read_text(encoding="utf-8", errors="replace")
                        tail = raw[-_max_live:] if len(raw) > _max_live else raw
                        if tail != _last:
                            _last = tail
                            live.code(tail, language="text")
                except OSError:
                    pass
                time.sleep(0.55)
            th.join(timeout=120.0)

            if holder.get("exc") is not None:
                st.exception(holder["exc"])
                code = 1
            else:
                code = holder.get("code")
                if code is None:
                    code = 1

            if lp.is_file():
                raw_log = lp.read_text(encoding="utf-8", errors="replace")
                st.session_state["queue_run_tail"] = (
                    raw_log[-120000:] if len(raw_log) > 120000 else raw_log
                )
                live.code(
                    raw_log[-_max_live:] if len(raw_log) > _max_live else raw_log,
                    language="text",
                )
            else:
                st.session_state["queue_run_tail"] = ""
                live.warning("Log file was not created.")

            st.session_state["queue_run_code"] = code
            st.session_state["queue_run_log_path"] = str(lp)
            st.session_state["queue_run_err"] = ""

    if "queue_run_code" in st.session_state:
        code = st.session_state.get("queue_run_code")
        lp_show = st.session_state.get("queue_run_log_path", "")
        err_txt = (st.session_state.get("queue_run_err") or "").strip()
        if err_txt:
            with st.expander("Stderr (progress)", expanded=False):
                st.code(err_txt, language="text")
        tail = st.session_state.get("queue_run_tail") or ""
        if tail:
            st.text_area("Last run — log tail", value=tail, height=320, key="queue_out_tail")
        if lp_show:
            st.caption(f"Full log appends to `{lp_show}`")
        if code == 0:
            st.success("Last queue finished (exit 0).")
        elif code == 2:
            st.error("Last run: empty queue or invalid workspace (exit 2).")
        elif code is not None:
            st.warning(f"Last queue exit code: **{code}**")
        if st.button("Clear queue output panel", key="queue_clear_output"):
            for k in ("queue_run_tail", "queue_run_code", "queue_run_err", "queue_run_log_path"):
                st.session_state.pop(k, None)
            st.rerun()

    if bg_clicked:
        qb = (st.session_state.get("queue_body") or "").strip()
        ws = (workspace_abs or "").strip()
        lp = Path((st.session_state.get("queue_log_path") or "").strip() or (_REPO / "dev-agents-queue.log"))
        if not ws or not Path(ws).is_dir():
            st.error("Set a valid workspace root above.")
        elif not qb:
            st.error("Enter at least one task in the queue.")
        else:
            pending = _REPO / "queue.pending.txt"
            pending.write_text(qb, encoding="utf-8")
            venv_ag = _REPO / ".venv" / "bin" / "dev-agents"
            exe = str(venv_ag) if venv_ag.is_file() else shutil.which("dev-agents") or ""
            if not exe:
                st.error("Could not find `dev-agents` CLI — run **`pip install -e .`** from this repo.")
            else:
                cmd = [
                    exe,
                    "queue",
                    str(pending),
                    "--log",
                    str(lp),
                    "-w",
                    ws,
                    "--recursion-limit",
                    str(int(q_rec)),
                    "-p",
                    str(int(q_strip)),
                    "--sleep",
                    str(float(q_sleep)),
                ]
                mo = _model_arg()
                if mo:
                    cmd.extend(["-m", mo])
                env_bg = os.environ.copy()
                qp_bg = (st.session_state.get("queue_task_prefix") or "").strip()
                if qp_bg:
                    env_bg["DEV_AGENTS_QUEUE_TASK_PREFIX"] = qp_bg
                else:
                    env_bg.pop("DEV_AGENTS_QUEUE_TASK_PREFIX", None)
                if q_local:
                    cmd.append("--local-patch-only")
                if q_ff:
                    cmd.append("--fail-fast")
                if q_verbose:
                    cmd.append("--verbose")
                out_append = _REPO / "overnight-nohup.out"
                with open(out_append, "a", encoding="utf-8") as out_f:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(_REPO),
                        env=env_bg,
                        stdout=out_f,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                pid_path = _REPO / "overnight-queue.pid"
                pid_path.write_text(str(proc.pid), encoding="utf-8")
                st.success(
                    f"Started background PID **`{proc.pid}`**. "
                    f"Logs append to **`{lp}`** · combined stdout/stderr **`{out_append}`**. "
                    f"CLI equivalent saved at **`{pending}`**."
                )

with tabs[6]:
    st.markdown(
        "**TradeChefPro Aider** — uses the **workspace root** above (your **`tcp`** checkout). "
        "Edit **`aider/TASKS.md`**, tail **`aider/logs/*`**, start **`./aider/overnight.sh`** in a detached process "
        "(same as non-interactive SSH: full loop runs in the child until the queue is empty). "
        "Requires **`aider`** on **`PATH`** in that environment (e.g. `~/.local/bin`)."
    )
    ws_a = (workspace_abs or "").strip()
    root_a = Path(ws_a) if ws_a else None
    if not root_a or not root_a.is_dir():
        st.error("Set a valid **workspace root** above.")
    else:
        aider_sh = root_a / "aider" / "overnight.sh"
        tasks_md = root_a / "aider" / "TASKS.md"
        logs_dir = root_a / "aider" / "logs"
        if not aider_sh.is_file():
            st.info(
                f"No **`aider/overnight.sh`** under `{root_a}`. "
                "Point the workspace at a **tcp** clone (or any repo that ships the Aider automation folder)."
            )
        else:
            logs_dir.mkdir(parents=True, exist_ok=True)
            prev_ws = st.session_state.get("aider_overnight_ws")
            if prev_ws != ws_a:
                st.session_state["aider_overnight_ws"] = ws_a
                if tasks_md.is_file():
                    st.session_state["aider_tasks_draft"] = tasks_md.read_text(
                        encoding="utf-8", errors="replace"
                    )
                else:
                    st.session_state["aider_tasks_draft"] = (
                        "# Aider overnight task queue\n\n"
                        "# Add tasks per aider/README.md (single lines or --- #N --- blocks).\n"
                    )

            st.subheader("TASKS.md")
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("Reload from disk", key="aider_tasks_reload"):
                    if tasks_md.is_file():
                        st.session_state["aider_tasks_draft"] = tasks_md.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        st.success("Reloaded.")
                    else:
                        st.warning("File does not exist yet — edit below and Save.")
                    st.rerun()
            with c2:
                if st.button("Save to disk", type="primary", key="aider_tasks_save"):
                    try:
                        tasks_md.parent.mkdir(parents=True, exist_ok=True)
                        tasks_md.write_text(
                            str(st.session_state.get("aider_tasks_draft") or ""),
                            encoding="utf-8",
                        )
                        st.success(f"Wrote `{tasks_md}`")
                    except OSError as e:
                        st.error(f"Write failed: {e}")
            with c3:
                st.caption(f"`{tasks_md}`")

            st.text_area(
                "TASKS.md body",
                height=320,
                key="aider_tasks_draft",
                label_visibility="collapsed",
                placeholder="--- #42 ---\nYour task…",
            )

            st.subheader("Overnight run")
            dev_plan = st.checkbox(
                "Prepend **dev-agents plan** to each Aider task (`AIDER_OVERNIGHT_DEV_AGENTS=1`)",
                value=True,
                key="aider_ui_dev_agents_plan",
                help="Matches tcp overnight default. Uncheck to force **`AIDER_OVERNIGHT_DEV_AGENTS=0`** for this spawn. Requires **dev-agents** `.venv` next to tcp or **`DEV_AGENTS_BIN`**.",
            )
            spawn_log = logs_dir / "overnight-ui-spawn.log"
            spawn_pid_path = logs_dir / "overnight-ui.pid"
            if st.button("Start overnight (detached)", type="primary", key="aider_spawn_overnight"):
                env_a = os.environ.copy()
                env_a["PATH"] = f"{Path.home()}/.local/bin{os.pathsep}{env_a.get('PATH', '')}"
                if dev_plan:
                    env_a["AIDER_OVERNIGHT_DEV_AGENTS"] = "1"
                else:
                    env_a["AIDER_OVERNIGHT_DEV_AGENTS"] = "0" 
                dg_root = root_a.parent / "dev-agents"
                if dg_root.is_dir():
                    env_a.setdefault("DEV_AGENTS_ROOT", str(dg_root.resolve()))
                inner = (
                    f"cd {shlex.quote(str(root_a.resolve()))} && "
                    "exec ./aider/overnight.sh"
                )
                cmd_a = ["bash", "-lc", inner]
                try:
                    with open(spawn_log, "ab", buffering=0) as lf:
                        lf.write(
                            f"\n# dev-agents UI spawn {time.strftime('%Y-%m-%dT%H:%M:%S')}\n".encode()
                        )
                        lf.flush()
                        proc_a = subprocess.Popen(
                            cmd_a,
                            stdin=subprocess.DEVNULL,
                            stdout=lf,
                            stderr=subprocess.STDOUT,
                            env=env_a,
                            start_new_session=True,
                        )
                    spawn_pid_path.write_text(str(proc_a.pid), encoding="utf-8")
                    st.success(
                        f"Started **overnight** PID **`{proc_a.pid}`**. "
                        f"Stream: `{spawn_log}` · PID file: `{spawn_pid_path}`"
                    )
                except OSError as e:
                    st.error(f"Could not start: {e}")

            if spawn_pid_path.is_file():
                try:
                    pid_txt = spawn_pid_path.read_text(encoding="utf-8", errors="replace").strip()
                    pid_a = int(pid_txt.split()[0])
                    alive = False
                    try:
                        os.kill(pid_a, 0)
                        alive = True
                    except OSError:
                        alive = False
                    st.caption(
                        f"Last UI-spawn PID **{pid_a}** — "
                        f"{'**running** (or stale PID if process exited)' if alive else 'not running (exited or unknown)'}"
                    )
                except (ValueError, OSError):
                    st.caption(f"PID file: `{spawn_pid_path}`")

            st.subheader("Logs (tail)")
            tail_bytes = st.number_input(
                "Tail (bytes)",
                min_value=4000,
                max_value=900_000,
                value=120_000,
                step=4000,
                key="aider_log_tail_bytes",
            )
            log_choice = st.selectbox(
                "Log file",
                options=[
                    "overnight.log",
                    "overnight.nohup.log",
                    "completed.log",
                    "failed.log",
                    "last-test-output.log",
                    "overnight-ui-spawn.log",
                ],
                index=0,
                key="aider_log_pick",
            )
            log_path = logs_dir / log_choice
            if st.button("Refresh log view", key="aider_log_refresh"):
                st.session_state["aider_log_tick"] = time.time()
            if log_path.is_file():
                try:
                    raw_l = log_path.read_bytes()
                    cap = int(tail_bytes)
                    chunk = raw_l[-cap:] if len(raw_l) > cap else raw_l
                    st.code(chunk.decode("utf-8", errors="replace"), language="text")
                except OSError as e:
                    st.warning(f"Could not read log: {e}")
            else:
                st.caption(f"`{log_path}` — not created yet.")
