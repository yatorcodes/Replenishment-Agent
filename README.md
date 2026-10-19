# Replenishment Agent (Watch Straps)

A small, honest portfolio project: a replenishment agent for a fictional online store that sells watch straps. The agent checks which products are running low and prepares **draft** purchase orders. Nothing is ordered until a human approves it.

## What it does

1. Seeds a SQLite database with products, sales (60 days), suppliers, and a few intentional data problems.
2. Runs a plain Python tool loop against Gemini (Flash) with function calling.
3. Drafts purchase orders and saves short notes between runs.
4. Lets you approve or reject drafts from the CLI.
5. Writes every model/tool step to an audit log you can replay by `run_id`.

## Architecture (text sketch)

```
seed.py  -->  SQLite (data/store.db)
                 ^
                 |
python -m app  -->  agent.py  --tool calls-->  tools.py  -->  db.py
                 |                 |
                 |                 +--> audit_log + agent_notes
                 |
                 v
           Gemini (Flash) via google-genai SDK

python -m app.review  -->  approve/reject draft purchase_orders
python -m app.log <run_id>  -->  printable audit timeline
```

No LangChain or agent frameworks. The model proposes tool calls; Python validates inputs, executes tools, and logs everything. The agent can create drafts only — never approve.

Config constants live in `app/config.py` (`MODEL_NAME`, `MAX_TOOL_CALLS`).

## How to run

Requires Python 3.11+ (3.10 works with the current code).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set GEMINI_API_KEY

python seed.py
python -m app                 # run the agent once
python -m app.review --list   # see drafts
python -m app.review          # interactive approve/reject
python -m app.log <run_id>    # timeline for a run

pytest
```

Non-interactive review:

```bash
python -m app.review --approve 1
python -m app.review --reject 2
```

## How memory works

Between runs the agent has no hidden state. Persistence is:

- **`agent_notes`** — short text notes the model writes via `save_note` / reads via `read_notes`.
- **`purchase_orders`** — draft/approved/rejected orders (drafts also act as memory: duplicates are rejected).
- **`audit_log`** — full trace of a run (`run_id`, step, tool, input, output).

Each run should start by reading notes, then inspecting inventory.

## Seeded failure cases

With fixed `RANDOM_SEED = 42`, every seed includes:

| SKU | Issue |
|-----|--------|
| `WS-LTHR-22-TAN` | Negative stock |
| `WS-RUBR-20-BLU` | Supplier row with `lead_time_days = NULL` |
| `WS-NATO-18-NVY` | Low stock + existing draft PO |

## What broke and what I changed

_(Fill this in as you build and demo the project.)_
