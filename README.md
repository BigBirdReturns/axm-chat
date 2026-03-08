# axm-chat

Turn Claude, ChatGPT, and Gemini conversation exports into cryptographically signed, queryable knowledge shards.

Two-pass pipeline. No cloud. No API keys. Runs on your laptop.

## Install

```bash
pip install -e ./axm-genesis    # kernel: compile + verify
pip install -e ./axm-core       # hub: Spectra query engine
pip install -e ./axm-chat       # this
```

Requires Python 3.10+. For distill, you also need [Ollama](https://ollama.ai) running locally.

## Quickstart

```bash
# Export your conversations from Claude or ChatGPT, then:
axm-chat import ./conversations.json

# See what got imported
axm-chat list

# Query in plain English
axm-chat query "what decisions have we made"
axm-chat query "what did we decide about authentication"
axm-chat query "what decisions conflict"
axm-chat query "timeline of the genesis kernel"
axm-chat query "what changed since february"
axm-chat query "what's stale or not reviewed"

# Distill decisions from conversations (requires Ollama)
ollama serve
ollama pull mistral
axm-chat distill --dry-run          # preview what would be extracted
axm-chat distill                    # compile decision shards
axm-chat distill --shard chat_AXM   # one shard by prefix
axm-chat distill --model llama3     # use a different model

# Verify shard integrity (Merkle + signature, no network required)
axm-chat verify
axm-chat verify chat_AXM_abc123
```

## What it does

### Pass 1 — Import (no LLM, deterministic)

Reads a Claude or ChatGPT export JSON and compiles one shard per conversation.

Each shard contains:
- Every conversation turn with role and timestamp
- Tier 0 claims: title, started_at, message_count
- BLAKE3 Merkle tree over all shard files
- Ed25519 signature (or ML-DSA-44 if `--suite axm-blake3-mldsa44`)
- Every claim tied to an exact byte span in source.txt

Shards go to `~/.axm/shards/`. Keys go to `~/.axm/keys/`. No configuration required.

### Pass 2 — Distill (local LLM via Ollama)

Reads a conversation shard and extracts decisions: things that were actually committed to, rejected, revised, or confirmed. Produces a decision shard that:

- Has 20–50 claims instead of 400
- Supersedes the conversation shard via `lineage@1`
- Timestamps every decision via `temporal@1`
- Links back to the conversation shard via `references@1`
- Is re-sealed with a new Merkle root after all extensions are written

The conversation shard stays on disk (archive it or delete it — the decision shard is the durable artifact).

### Query (no LLM)

Natural language → SQL pattern matching. No LLM at query time. Runs against all mounted shards via DuckDB through Spectra (axm-core).

Supported query patterns:

| Question | What it does |
|---|---|
| `what decisions have we made` | All decision-predicate claims, ordered by time |
| `what did we decide about X` | Topic filter on decision claims |
| `what decisions conflict` | Self-join: same subject+predicate, different object |
| `timeline of X` | Decisions ordered by temporal.valid_from |
| `what changed since february` | Decisions after a date |
| `what's stale` | Decisions with no valid_until |
| `what superseded what` | Lineage chain queries |

Raw SQL also works: `axm-chat query --sql "SELECT * FROM claims WHERE predicate='decided'"`

## Glass Onion UI

The chat spoke includes a Glass Onion interface — a visual shell that maps AXM's architecture layers to navigable modes.

**Demo mode (no server):** Open `ui/axm_chat_glass_onion.jsx` in any React sandbox (Claude artifact viewer, CodeSandbox, Vite dev server). It auto-detects that no server is running and shows simulated data. You can click through Import → Distill → Query to see the full workflow.

**Live mode (with server):** Start the local server, and the Glass Onion auto-detects it on load — no configuration, no editing source files.

```bash
pip install -e ".[server]"    # adds Flask + flask-cors
python server/axm_server.py   # starts on http://localhost:8410
```

The Glass Onion checks `localhost:8410/health` every 10 seconds. When the server is up, it switches to live mode automatically. When the server goes down, it falls back to demo mode. The status indicator in the header shows `connected` / `demo` / `offline`.

**Modes:**
- **Import** (spoke ring) — drag and drop Claude/ChatGPT export files, watch shards compile in real time
- **Distill** (core ring) — select a conversation shard, run decision extraction via Ollama, see results
- **Query** (kernel) — type natural language questions, get structured results with the SQL shown

The Glass Onion pattern is shared across AXM spokes. The drone show spoke (`axm-show`) has its own Glass Onion with Plan → Compile → Inspect modes. Same visual language, different domain.

## Server API

The Flask server (`server/axm_server.py`) exposes five endpoints:

| Endpoint | Method | What it does |
|---|---|---|
| `/health` | GET | Liveness check, shard count, Ollama status |
| `/shards` | GET | List all shards with metadata |
| `/import` | POST | Multipart file upload → compile shards |
| `/distill` | POST | Extract decisions from a shard via Ollama |
| `/query` | POST | Natural language → SQL → results |
| `/verify` | POST | Run axm-verify on a shard |

The server has a DuckDB fallback if axm-core (Spectra) isn't installed — import and distill work without Spectra; queries degrade to inline SQL.

## Repo structure

```
axm-chat/
  pyproject.toml
  README.md
  LICENSE
  src/axm_chat/
    __init__.py       public API (re-exports spoke.py for server use)
    spoke.py          export detection, extraction, compilation
    cli.py            Click CLI: import / distill / query / list / verify
    distill.py        second-pass compiler: conversation shard → decision shard
  server/
    axm_server.py     Flask HTTP bridge (import/distill/query/verify as REST)
  ui/
    axm_chat_glass_onion.jsx   Glass Onion interface (auto-detects server)
```

## Dependencies

| Package | Required for |
|---|---|
| `axm-genesis` | Everything — the only path to a signed shard |
| `axm-core` (Spectra) | `query` command — natural language + SQL |
| `click` | CLI |
| `duckdb` | Query runtime |
| Ollama | `distill` command only |
| Flask + flask-cors | `server/axm_server.py` only |

## Supported export formats

- **Claude** — `conversations.json` from Settings → Export Data
- **ChatGPT** — `conversations.json` from Settings → Export Data, or `.zip` export
- **Generic** — any JSON with `messages` array containing `role` + `content` fields (LM Studio, OpenWebUI, Ollama, etc.)

## Shard storage

All shards land in `~/.axm/shards/`. Each shard is a directory:

```
~/.axm/shards/chat_My_Conversation_abc123/
  manifest.json         shard_id, Merkle root, metadata, extensions list
  content/source.txt    canonical source text (conversation transcript)
  graph/claims.parquet  structured claims
  graph/entities.parquet
  graph/provenance.parquet
  evidence/spans.parquet  byte-span evidence for every claim
  sig/manifest.sig      signature over manifest.json
  sig/publisher.pub     public key
  ext/                  extension tables (lineage, temporal, references)
```

Any shard can be verified offline: `axm-chat verify` or `axm-verify ./shard_dir`.

## License

Apache 2.0
