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

Load env with **`set -a && source .env && set +a`** before running commands if your shell doesn’t export those variables yet. Prefer a **small** `dev-agents/.env` instead of **`source`**-ing Django’s `.env` (shell metacharacters in unrelated keys will break sourcing). **`./.env`** is gitignored — never commit it.

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

The `coder` graph calls Ollama repeatedly: the model emits JSON tool calls (Ollama-compatible), tools run on disk, then the model answers. **No writes** — read-only tools only.

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
