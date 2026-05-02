"""
Minimal browser UI for dev-agents (localhost — do not expose without auth).

  pip install -e ".[ui]"
  cd dev-agents && streamlit run ui/app.py
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


def _mask_url(url: str) -> str:
    if not url:
        return "(unset)"
    u = url.rstrip("/")
    if len(u) <= 36:
        return u
    return u[:28] + "…" + u[-8:]


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
    wsp_raw = os.environ.get("AGENT_WORKSPACES", "").strip()
    st.text_area(
        "OLLAMA_BASE_URL (masked)",
        _mask_url(ost) if ost != "(unset)" else ost,
        height=68,
        disabled=True,
    )
    st.text_input("OLLAMA_MODEL (from env)", value=model_env, disabled=True)
    default_w = wsp_raw.split(":")[0].strip() if wsp_raw else ""
    model_ov = st.text_input("Per-run model override (optional)", value="", placeholder="e.g. qwen2.5-coder:32b")
    workspace_abs = st.text_input("Workspace root (-w)", value=default_w, placeholder="/abs/path/to/tcp")

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
