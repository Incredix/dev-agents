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
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _load_repo_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = _REPO / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


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

st.set_page_config(page_title="dev-agents", layout="wide")
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
    model_ov = st.text_input("Per-run model override (optional)", value="", placeholder="e.g. qwen2.5-coder:32b")
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

tabs = st.tabs(["Ollama check", "Hello", "Plan", "Coder"])


def _model_arg() -> str | None:
    m = model_ov.strip() if isinstance(model_ov, str) else ""
    return m or None


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

            def _coder_kwargs(on_step_cb=None):
                kw = dict(
                    model=_model_arg(),
                    thread_id=thread_id or "streamlit",
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
                "**Behind the scenes:** LangGraph cycles **call_model → (optional) tools → model** …"
                "\nEach **step** below is one superstep (`model` node or `tools` node)."
                "\nInside **call_model**, Ollama runs **blocking** until a response — longest silent gap."
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
