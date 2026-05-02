# dev-agents

Shared **development** agents: LangGraph + Ollama (e.g. Gemma on a homelab box). This is **not** part of TradeChefPro, vanna-api, or any production Docker stack.

## Where this repo lives

Use **one clone** on the machine where you actually run the agent (your laptop with Cursor, or a dev box with checkouts mounted). Point it at Ollama over the LAN via `OLLAMA_BASE_URL`.

**Same tooling for every app repo:** keep LangGraph code only here; set `AGENT_WORKSPACES` to a colon-separated list of absolute paths (`tcp`, OptionsSignals, etc.). Add file/subprocess tools in graphs as needed—they read those paths.

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

## Run the example graph

```bash
source .venv/bin/activate
set -a && source .env && set +a
python -c "from dev_agents.graphs.hello import run; print(run('LangGraph on a homelab'))"
```

## Layout

| Path | Purpose |
|------|---------|
| `src/dev_agents/config.py` | Env: Ollama URL, model name, workspace paths |
| `src/dev_agents/chat.py` | Shared `ChatOllama` factory |
| `src/dev_agents/graphs/` | One module per workflow |

## Git

Initialize or add your own remote (this folder is intentionally **outside** `tcp/`):

```bash
cd dev-agents
git init
git add .
git commit -m "Initial dev-agents scaffolding"
```

## Notes

- **Do not** add this repo to production compose on `.111` / vanna-api unless you are shipping a real product feature.
- For persistent LangGraph checkpoints, add a saver (e.g. SQLite or Postgres) in a graph module when you need multi-step durability.
