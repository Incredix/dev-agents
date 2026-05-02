# dev-agents

LangGraph workflows that talk to **Ollama** over HTTP — plan work, grep/read local repos, and (optionally) apply unified diffs. **Not** part of TradeChefPro Django, vanna-api, or prod Docker stacks; keep agents and secrets out of production images.

## How to use this

1. **Install** (once): `cd dev-agents && python3 -m venv .venv && source .venv/bin/activate && pip install -e .`
2. **Configure**: copy `.env.example` → `.env` and set **`OLLAMA_BASE_URL`**, **`OLLAMA_MODEL`**, **`AGENT_WORKSPACES`** (colon-separated absolute paths to checkouts such as `/…/tcp`).
3. **`dev-agents ollama-check`** — confirms this machine reaches Ollama and lists pulled models (use your Ollama **LAN** URL if a public hostname does not resolve here).
4. **`dev-agents hello --topic "smoke"`** — quickest LLM round-trip.
5. **`dev-agents plan -i "…"`** — one-shot plan; add **`-r path/in/repo.py`** and **`-w /abs/checkout`** when you want a file excerpt wired in.
6. **`dev-agents coder -i "…" -w /abs/checkout`** — multi-turn **read-only** agent (list / read / grep / `rg`); use **`-m qwen2.5-coder:32b`** (or another coder tag) for better tool adherence. Thread state is persisted under **`DEV_AGENTS_CHECKPOINT_DB`** (see `.env.example`).
7. **`dev-agents patch-apply`** — **`patch`** dry-run at a checkout root; add **`--apply`** only when you mean to alter files.

### Optional browser UI (**Streamlit**)

```bash
pip install -e ".[ui]"
cd dev-agents && source .venv/bin/activate
python -m streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501
```

Opens **`http://localhost:8501`** — Streamlit listens on **localhost** only by default.

**Multiple repos, one UI:** set **`AGENT_WORKSPACES=/path/to/tcp:/path/to/other-repo`** (colon-separated). The Streamlit sidebar shows a **workspace picker** so Plan/Coder run against the chosen checkout — still **one** `streamlit run` process.

**Two tabs / port 8502?** If **`8501` is already in use**, Streamlit silently binds **`8502`**, **`8503`**, … — usually an **older Streamlit you forgot to stop**, still running stale code. Free the port:

```bash
kill "$(lsof -ti :8501)"
```

Then start again (same venv + **`python -m streamlit`** — see **`ui/app.py`** docstring). Tabs: Ollama check, Hello, Plan, Coder. The app loads **`dev-agents/.env`** with **python-dotenv** (still never commit `.env`). In the **Coder** tab, enable **“Verbose step log”** to expand a trace of LangGraph steps after the run completes.

**Patch & PR** tab: **Load diff from last Coder reply** (extracts fenced unified diff), **Dry-run** / **Apply** with GNU **`patch`** (requires **`sudo apt install patch`** on Linux), **Download** `.patch`, and optionally **Create PR** (**`git`** + **`gh`**) with a clean working tree — writes branch → patch → commit → push → **`gh pr create`**.

**Autopilot** (sidebar, **on by default**): skips Patch-tab confirmation checkboxes; **after each successful Coder run**, extracts a fenced unified diff from the reply and either **applies it locally** or runs the full **git → push → `gh pr create`** flow (requires clean tree + **`gh auth`**). Disable Autopilot in the sidebar if you want review gates back.

### Tailscale (reach the UI from your phone / laptop on the tailnet)

This is separate from Cloudflare tunnels. **Tailscale Serve** publishes your **local** Streamlit port to **`https://<machine>.<tailnet>.ts.net`** for nodes on **your tailnet only** (not the public internet). Your host already runs Tailscale (e.g. **`100.84.61.6`**); use **`tailscale serve status`** after setup to see the exact URL.

```bash
# Terminal A — bind Streamlit to loopback only (recommended)
cd dev-agents && source .venv/bin/activate
streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501

# Terminal B — HTTPS proxy onto the tailnet (background)
chmod +x scripts/serve-ui-tailscale.sh
scripts/serve-ui-tailscale.sh

# Done for the day — remove Serve mapping
scripts/serve-ui-tailscale.sh off
```

**Linux ACL note:** Tailscale Serve may refuse without root until you delegate once:

```bash
sudo tailscale set --operator="$USER"
# then `tailscale serve --bg 8501` works without sudo; or keep using sudo for serve
```

Enable **HTTPS / Serve for your tailnet** in the Tailscale admin console if the CLI prompts you (the message includes a **`login.tailscale.com`** link). **`tailscale serve status`** prints the exact **`https://<machine>…ts.net`** URL for your mesh.

**Do not confuse** **`tailscale serve`** (tailnet-only) with **`tailscale funnel`** (can expose publicly). Prefer **Serve** unless you deliberately want the whole internet to hit Streamlit — which you should not without extra auth layers.

Still treat the UI like an internal admin panel: Tailscale spreads access to anyone on the tailnet ACLs.

### Does LangGraph “add features” to my codebase?

**Partly:**

- **`plan`** and **`coder`** help with **architecture, reasoning, grep/read/list**, and drafts of **commands or diffs** — they shorten the iteration loop while you steer.
- **Built‑in tools are read‑only.** They won’t silently edit repos. Applying changes is the Streamlit **Patch & PR** tab, **`dev-agents patch-apply`** from the CLI after dry‑run (`--apply` when intentional), or Cursor/your IDE.
- You can extend LangGraph later with richer flows (reviews, scripted tests before patch, MCP, etc.). Today it’s deliberately **assistive**, not autopilot merges.

Updates in the Streamlit tabs appear when **each action finishes** — there is **no SSE push** UI yet for long coder runs beyond the built‑in spinner; you refresh by running again or waiting for completion.

### Other servers (vanna-api, infra‑db boxes, SSH)

**Run `dev-agents` where the code lives on disk**, because tools read **`AGENT_WORKSPACES`** paths on **that machine**.

| Situation | What to do |
|-----------|-------------|
| **vanna-api** on `192.168.1.29` | SSH in (or Cursor Remote‑SSH). Clone **[Incredix/dev-agents](https://github.com/Incredix/dev-agents)** there once. Set **`dev-agents/.env`**: **`OLLAMA_BASE_URL`** reachable **from that host** (LAN IP or Ollama’s **`100.x` Tailscale** address plus **11434** if your ACLs/firewall allow it), **`AGENT_WORKSPACES`** to its local checkout. Use CLI or **`.[ui]`** plus **`scripts/serve-ui-tailscale.sh`** on that host if you want the browser UI remotely. |
| **infra‑db host** | Usually only Postgres/Redis — nothing to grep as an app checkout. Agents don’t belong there unless you keep **SQL/schema** repos on that VM. Prefer running agents next to application code repos. |
| **One laptop, many repos** | Add multiple paths to **`AGENT_WORKSPACES`** (**colon-separated**) when they share a filesystem with that machine — not typical across separate servers; use **one clone per server** instead. |

**Ollama on another tailnet peer:** `OLLAMA_BASE_URL=http://100.x.y.z:11434` works if **Ollama listens on something reachable over Tailscale** (default often loopback‑only — you may bind or proxy carefully; exposing `0.0.0.0:11434` has security implications).

Load env with **`set -a && source .env && set +a`** before running CLI commands if your shell doesn’t export those variables yet. Prefer a **small** `dev-agents/.env` instead of **`source`**-ing Django’s `.env` (shell metacharacters in unrelated keys will break sourcing). **`./.env`** is gitignored — never commit it.

## Where this repo lives

Use **one clone** on the machine that runs commands (desktop, `ubuntu-server` over Tailscale, etc.). **Same codebase** drives every app repo via **`AGENT_WORKSPACES`** and **`-w` / `--workspace-index`** overrides.

## Setup

```bash
cd dev-agents
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env: OLLAMA_BASE_URL, OLLAMA_MODEL, AGENT_WORKSPACES
```

Sanity check Ollama from this host:

```bash
curl -sS "${OLLAMA_BASE_URL:-http://127.0.0.1:11434}/api/tags" | python3 -c \
  'import sys,json; print("\n".join(m["name"] for m in json.load(sys.stdin).get("models", [])))'
```

(No `jq` needed; use `sudo apt install jq` only if you prefer `jq '.models[].name'`.)

### `Name or service not known` / `Errno -2`

Your **public** Ollama hostname may resolve only on networks that see the same DNS records (or only from Cloudflare proxy paths). Many **inside-LAN Ubuntu boxes** cannot resolve `ollama.tradechefpro.com`. Use a URL that resolves there:

```bash
export OLLAMA_BASE_URL=http://<ollama-box-lan-ip>:11434
dev-agents ollama-check
```

The `ollama-check` command uses the same env and prints model names or the same troubleshooting text.

## Commands

| Command | Purpose |
|--------|---------|
| `hello` | One-shot LLM smoke test |
| `plan` | Static context (optional file excerpt) + plan |
| `coder` | Multi-step **read-only** exploration (list / read / grep / `rg`) with SQLite checkpoints |
| `patch-apply` | GNU `patch` dry-run (default); `--apply` writes files |
| `ollama-check` | `GET /api/tags` |

Use **`--verbose` / `-v`** on **`dev-agents coder`** to stream each LangGraph step (preview of the latest message) to **stderr** while the graph runs.

Use `--model`/`-m` on `hello`, `plan`, and `coder` to override `OLLAMA_MODEL` per run (Qwen coder tags work well for `coder`).

## Run the example graph

```bash
source .venv/bin/activate
set -a && source .env && set +a
dev-agents hello --topic "LangGraph on a homelab"
```

### Plan an iteration (workspace + optional file excerpt)

```bash
set -a && source .env && set +a
dev-agents plan -i "Refactor trades API error handling to return JSON errors" \
  -r website/trades_api_views.py \
  -w /home/you/code/tradechefpro/tcp
```

If `AGENT_WORKSPACES` already includes `tcp`, you can omit `-w` and use `--workspace-index` when you have several roots.

### Coder (tools + checkpoints)

The `coder` graph calls Ollama repeatedly: the model emits JSON tool calls (Ollama-compatible), tools run on disk, then the model answers. **No writes** — read-only tools only. If a model appends junk after the JSON (e.g. `<tool_call|>`), the runner now decodes the leading object with `JSONDecoder.raw_decode` so routing to tools still works; **restart Streamlit** after `git pull` so the UI picks up fixes.

```bash
set -a && source .env && set +a
dev-agents coder -i "How does /trades/api/ideas/ gate Pro CSV export?" \
  -w /home/you/code/tradechefpro/tcp \
  -m qwen2.5-coder:32b \
  --thread-id mysession
```

- **Checkpoints:** SQLite at `DEV_AGENTS_CHECKPOINT_DB` (default `.checkpoints/checkpoints.sqlite` under your current working directory). Run from `dev-agents/` or set an absolute path.
- **`--no-checkpoint`:** ephemeral run.
- **`GNU patch`** (for `patch-apply`): install with `sudo apt install patch` if missing.

### Apply a unified diff (careful)

Dry-run (default):

```bash
dev-agents patch-apply -w /home/you/code/tradechefpro/tcp /path/to/changes.diff
```

Apply:

```bash
dev-agents patch-apply --apply -w /home/you/code/tradechefpro/tcp /path/to/changes.diff
```

Use `-p` / `--strip` to match paths in the diff (often `1` for git-style).

## Layout

| Path | Purpose |
|------|---------|
| `scripts/serve-ui-tailscale.sh` | **Tailscale Serve** helper for the Streamlit UI |
| `ui/app.py` | Streamlit tabs (requires `.[ui]`) |
| `src/dev_agents/config.py` | Env: Ollama URL, model name, workspace paths |
| `src/dev_agents/chat.py` | Shared `ChatOllama` factory |
| `src/dev_agents/graphs/` | `hello`, `code_plan`, `coder_react` |
| `src/dev_agents/tools_workspace.py` | Read-only tools bound to one root |
| `src/dev_agents/patch_apply.py` | `patch` invoke wrapper |
| `src/dev_agents/cli.py` | `dev-agents` console entry point |
| `src/dev_agents/workspace.py` | Safe reads under a repo root |

## Git

This folder is intentionally **outside** `tcp/`. Add your own remote as needed.

## Notes

- **Do not** add this repo to production compose on the TCP / vanna-api hosts unless you are shipping a real product feature.
- **Do not** `source` the full `tcp/.env` into bash for Django secrets if values contain `)`, `*`, etc. — keep a small `dev-agents/.env` for `OLLAMA_*` and `AGENT_WORKSPACES` only.
- For coding agents, **Qwen coder** models in Ollama often outperform Gemma on multi-step tool workflows; try `-m qwen2.5-coder:32b` for `coder`.
