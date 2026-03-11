# axm-chat

Turn Claude, ChatGPT, and Gemini conversation exports into cryptographically signed, queryable knowledge shards.

Three-pass pipeline. No cloud. No API keys. Runs on your laptop.

**→ [axm-chat.axiom.tools](https://bigbirdreturns.github.io/axm-chat/)** — visual pipeline demo

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

# Distill: index episodes, run lenses, extract decisions (requires Ollama)
ollama serve
ollama pull mistral
axm-chat distill --dry-run          # preview what would be extracted
axm-chat distill                    # run all three passes

# Query in plain English
axm-chat query "what decisions have we made"
axm-chat query "what did we decide about authentication"
axm-chat query "what failed before we solved the merkle problem"
axm-chat query "what conversations involved axm-genesis"
axm-chat query "show unresolved conversations"
axm-chat query "what tools did we use for signing"
axm-chat query "what changed since february"
axm-chat query "what decisions conflict"
axm-chat query "timeline of the genesis kernel"

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

Three sub-passes run in sequence. Each writes into the shard's `ext/` directory.

#### Sub-pass A — Universal episodic index (runs on everything)

Every conversation gets this pass regardless of content. Extracts flat, queryable
semantic metadata and writes `ext/episodes@1.parquet` into the source shard.

Each episode record contains:

| Field | What it captures |
|---|---|
| `topic_tags` | 3–5 short noun phrases, specific not generic |
| `people` | Named humans only |
| `animals` | Named animals or pets |
| `tools_places_services` | Software names, services, locations — named and specific |
| `projects` | Named repositories, codebases, formal initiatives |
| `question_text` | The primary question verbatim, if one exists |
| `state` | resolved · unresolved · abandoned · ongoing |
| `tone` | positive · neutral · negative · stressed · relieved · mixed |
| `summary` | One literal sentence. No inference. |
| `lens_hints` | Which deep passes to run next: engineering · audit · reflect · general |

#### Sub-pass B — Gated lens extraction (routed by lens_hints)

Deep extraction passes that only run when the episodic pass flags them.

**Engineering lens** (`lens_hints ∋ "engineering"`) → `ext/engineering@1.parquet`

Extracts the problem-solving lifecycle for technical conversations:

| Field | What it captures |
|---|---|
| `problem_statement` | Core technical challenge, one sentence |
| `core_technologies` | Specific named tools and libraries |
| `failed_attempts` | Every approach tried before the solution — the graveyard |
| `solution_adopted` | The fix, or "None" if unresolved |
| `architectural_rule` | Broad design principle if one emerged, or "None" |
| `confidence` | Self-assessed 0.0–1.0 |

The failed attempts field is the most valuable part of this record. It prevents
the same dead ends later.

Additional lenses (audit, reflect) follow the same pattern — episodic pass
flags them, lens pass runs only on flagged episodes.

#### Sub-pass C — Decision extraction

Reads the conversation shard and extracts decisions: things that were actually
committed to, rejected, revised, or confirmed. Produces a decision shard that:

- Has 20–50 claims instead of 400
- Supersedes the conversation shard via `lineage@1`
- Timestamps every decision via `temporal@1`
- Links back to the conversation shard via `references@1`
- Is re-sealed with a new Merkle root after all extensions are written

The conversation shard stays on disk. The decision shard is the durable artifact.

### Query (no LLM)

Natural language → SQL pattern matching. No LLM at query time. Runs against all
mounted shards via DuckDB through Spectra (axm-core).

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
| `what conversations involved X` | Episode topic/project filter |
| `who was involved in X` | Episode people filter |
| `what tools did we use for X` | Episode tools/services filter |
| `what failed before we solved X` | Engineering lens: failed_attempts |
| `what's the rule about X` | Engineering lens: architectural_rule |
| `show unresolved conversations` | Episode state = unresolved |
| `what was the tone of X` | Episode tone filter |

Raw SQL also works: `axm-chat query --sql "SELECT * FROM claims WHERE predicate='decided'"`

## Glass Onion UI

The chat spoke includes a Glass Onion interface — a visual shell that maps AXM's
architecture layers to navigable modes.

**Demo mode (no server):** Open `ui/axm_chat_glass_onion.jsx` in any React sandbox.
Auto-detects that no server is running and shows real AXM architecture decisions as
example data.

**Live mode (with server):**

```bash
pip install -e ".[server]"    # adds Flask + flask-cors
python server/axm_server.py   # starts on http://localhost:8410
```

The Glass Onion checks `localhost:8410/health` every 10 seconds. When the server
is up, it switches to live mode automatically.

**Modes:**
- **Import** (spoke ring) — drag and drop Claude/ChatGPT export files
- **Distill** (core ring) — run all three passes, see decisions extracted live
- **Query** (kernel) — type natural language questions, get structured results with SQL shown

## Server API

| Endpoint | Method | What it does |
|---|---|---|
| `/health` | GET | Liveness check, shard count, Ollama status |
| `/shards` | GET | List all shards with metadata |
| `/import` | POST | Multipart file upload → compile shards |
| `/distill` | POST | Run all three passes on a shard via Ollama |
| `/query` | POST | Natural language → SQL → results |
| `/verify` | POST | Run axm-verify on a shard |

## Shard storage

All shards land in `~/.axm/shards/`. Each shard is a directory:

```
~/.axm/shards/chat_My_Conversation_abc123/
  manifest.json               shard_id, Merkle root, metadata, extensions list
  content/source.txt          canonical source text (conversation transcript)
  graph/claims.parquet        structured claims
  graph/entities.parquet
  graph/provenance.parquet
  evidence/spans.parquet      byte-span evidence for every claim
  sig/manifest.sig            signature over manifest.json
  sig/publisher.pub           public key
  ext/
    episodes@1.parquet        episodic index (written by distill sub-pass A)
    engineering@1.parquet     engineering lens (written if flagged by sub-pass A)
    lineage@1.parquet         supersession chain (decision shards only)
    temporal@1.parquet        decided_at timestamps (decision shards only)
    references@1.parquet      links back to source conversation shard
```

Any shard can be verified offline: `axm-chat verify` or `axm-verify ./shard_dir`.

## Repo structure

```
axm-chat/
  pyproject.toml
  README.md
  LICENSE
  src/axm_chat/
    __init__.py           public API
    spoke.py              export detection, extraction, compilation
    cli.py                Click CLI: import / distill / query / list / verify
    distill.py            three-pass distill pipeline
    episodic.py           universal episodic index (sub-pass A)
    engineering_lens.py   engineering deep extraction (sub-pass B)
  server/
    axm_server.py         Flask HTTP bridge
  ui/
    axm_chat_glass_onion.jsx   Glass Onion interface
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
- **Generic** — any JSON with `messages` array containing `role` + `content` fields

## License

Apache 2.0
