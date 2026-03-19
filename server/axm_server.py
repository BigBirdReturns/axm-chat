"""
axm_server.py — Local HTTP server for the AXM Chat UI
======================================================

Wraps axm_chat.py, distill.py, and Spectra into a simple REST API.
Runs on port 8410. No authentication, no cloud, no external calls.

Start:
    python axm_server.py

Endpoints:
    GET  /health          → {"ok": true, "shards": N, "ollama": bool}
    GET  /shards          → {"shards": [...]}
    POST /import          → multipart file upload → {"imported": N, "log": [...]}
    POST /distill         → {"shard": "name", "model": "mistral", "dry_run": true}
    POST /query           → {"question": "what did we decide"} → {"columns":[], "rows":[], "sql": "..."}
    POST /verify          → {"shard": "name"} → {"status": "PASS"|"FAIL", ...}

Requires:
    pip install flask flask-cors
    axm-genesis on Python path
    axm_chat.py and distill.py in same directory
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any

from flask import Flask, request, jsonify
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Bootstrap — find genesis and local modules
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

# Add axm-chat/src/ so axm_chat package is importable in bare-checkout runs.
# When installed via `pip install -e .` this is a no-op — already on the path.
for _src_candidate in [
    HERE.parent / "src",          # server/ -> axm-chat/src/
    HERE / "src",                 # if server/ == axm-chat/
]:
    if (_src_candidate / "axm_chat").exists():
        if str(_src_candidate) not in sys.path:
            sys.path.insert(0, str(_src_candidate))
        break

# Try to find axm-genesis
for candidate in [
    HERE / "axm-genesis" / "src",
    HERE / "axm-clean-genesis" / "src",
    HERE.parent / "axm-genesis" / "src",
    HERE.parent / "axm-clean-genesis" / "src",
]:
    if (candidate / "axm_build").exists():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break

# Try to find Spectra
for candidate in [
    HERE / "axm-core" / "spectra",
    HERE.parent / "axm-core" / "spectra",
    HERE / "spectra",
]:
    if (candidate / "axiom_runtime").exists():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        break

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SHARD_DIR = Path.home() / ".axm" / "shards"
KEY_DIR = Path.home() / ".axm" / "keys"
PORT = 8410

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)


def _shard_info(shard_path: Path) -> dict:
    """Build a shard summary dict."""
    try:
        manifest = json.loads((shard_path / "manifest.json").read_text())
        meta = manifest.get("metadata", {})
        stats = manifest.get("statistics", {})
        integrity = manifest.get("integrity", {})

        return {
            "name": shard_path.name,
            "title": meta.get("title", shard_path.name),
            "claims": stats.get("claims", 0),
            "entities": stats.get("entities", 0),
            "created": manifest.get("created_at", ""),
            "merkle": integrity.get("merkle_root", ""),
            "shard_id": manifest.get("shard_id", ""),
            "suite": manifest.get("suite", ""),
            "extensions": manifest.get("extensions", []),
            "is_decision": bool(meta.get("source_shard")),
            "source_shard": meta.get("source_shard", ""),
            "verified": None,  # lazy — verify on demand
        }
    except Exception as e:
        return {
            "name": shard_path.name,
            "title": shard_path.name,
            "error": str(e),
        }


def _list_shards() -> list:
    """List all shards in SHARD_DIR."""
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    shards = []
    for p in sorted(SHARD_DIR.iterdir()):
        if p.is_dir() and (p / "manifest.json").exists():
            shards.append(_shard_info(p))
    return shards


def _check_ollama() -> bool:
    """Check if Ollama is running."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    shards = _list_shards()
    return jsonify({
        "ok": True,
        "shards": len(shards),
        "ollama": _check_ollama(),
        "shard_dir": str(SHARD_DIR),
    })


@app.route("/shards")
def list_shards():
    return jsonify({"shards": _list_shards()})


@app.route("/import", methods=["POST"])
def import_files():
    """Import uploaded export files."""
    from axm_chat import load_export_file, extract_conversation, compile_conversation_shard, get_or_create_keypair

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    KEY_DIR.mkdir(parents=True, exist_ok=True)

    log = []
    total_imported = 0
    total_skipped = 0
    total_errors = 0

    for upload in files:
        # Save to temp
        tmp = Path(tempfile.mkdtemp())
        try:
            ext = Path(upload.filename).suffix.lower()
            save_path = tmp / upload.filename
            upload.save(str(save_path))

            convs, export_type = load_export_file(save_path)
            log.append(f"→ {upload.filename}: {len(convs)} conversations ({export_type})")

            from axm_chat import SUITE
            import re

            for idx, conv in enumerate(convs):
                extracted = extract_conversation(conv, idx, export_type)
                if not extracted:
                    total_skipped += 1
                    continue

                conv_id = extracted["conv_id"]
                # Check existing
                existing = list(SHARD_DIR.glob(f"*{conv_id[:16]}*"))
                if existing:
                    total_skipped += 1
                    continue

                safe_id = re.sub(r"[^\w-]", "_", conv_id)[:48]
                safe_title = re.sub(r"[^\w\s-]", "", extracted["title"])[:30].strip().replace(" ", "_")
                shard_name = f"chat_{safe_title}_{safe_id}"
                shard_path = SHARD_DIR / shard_name

                try:
                    ok = compile_conversation_shard(extracted, shard_path, KEY_DIR, SUITE)
                    if ok:
                        total_imported += 1
                        log.append(f"  ✓ {extracted['title'][:50]} ({extracted['turn_count']} turns)")
                    else:
                        total_errors += 1
                        log.append(f"  ✗ {extracted['title'][:50]} — compile failed")
                except Exception as e:
                    total_errors += 1
                    log.append(f"  ✗ {extracted['title'][:50]} — {e}")
                    if shard_path.exists():
                        shutil.rmtree(shard_path, ignore_errors=True)

        except Exception as e:
            total_errors += 1
            log.append(f"  ✗ {upload.filename}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return jsonify({
        "imported": total_imported,
        "skipped": total_skipped,
        "errors": total_errors,
        "log": log,
    })


@app.route("/distill", methods=["POST"])
def distill():
    """Distill a shard into a decision shard."""
    data = request.get_json()
    shard_name = data.get("shard")
    model = data.get("model", "mistral")
    dry_run = data.get("dry_run", True)

    if not shard_name:
        return jsonify({"error": "No shard specified"}), 400

    shard_path = SHARD_DIR / shard_name
    if not shard_path.exists():
        # Try prefix match
        matches = [p for p in SHARD_DIR.iterdir() if p.name.startswith(shard_name)]
        if matches:
            shard_path = matches[0]
        else:
            return jsonify({"error": f"Shard not found: {shard_name}"}), 404

    try:
        from axm_chat.distill import distill_shard

        result = distill_shard(
            shard_path=shard_path,
            output_base=SHARD_DIR,
            model=model,
            key_dir=KEY_DIR,
            dry_run=dry_run,
        )

        decisions = []
        for d in result.decisions:
            decisions.append({
                "subject": d.subject,
                "predicate": d.predicate,
                "object": d.object,
                "decided_at": d.decided_at,
                "reasoning": d.reasoning,
                "alternatives": d.alternatives,
                "confidence": d.confidence,
                "turn_index": d.turn_index,
                "speaker": d.speaker,
            })

        return jsonify({
            "status": result.status,
            "decisions": decisions,
            "error": result.error,
            "source_shard_id": result.source_shard_id,
            "decision_shard_path": str(result.decision_shard_path) if result.decision_shard_path else None,
        })

    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "decisions": []}), 500


@app.route("/query", methods=["POST"])
def query():
    """Query across mounted shards."""
    data = request.get_json()
    question = data.get("question", "")

    if not question:
        return jsonify({"error": "No question provided"}), 400

    # Try Spectra first
    try:
        from axiom_runtime.engine import SpectraEngine
        from axiom_runtime.nlquery import natural_language_to_sql

        engine = SpectraEngine()

        # Mount all shards
        shard_paths = sorted(
            p for p in SHARD_DIR.iterdir()
            if p.is_dir() and (p / "manifest.json").exists()
        )
        for sp in shard_paths:
            try:
                engine.mount(str(sp), None, verify=False)
            except Exception:
                pass

        sql = natural_language_to_sql(question)
        result = engine.query_json(sql)

        return jsonify({
            "columns": result.get("columns", []),
            "rows": result.get("rows", []),
            "sql": sql.strip(),
            "mounted": len(shard_paths),
        })

    except ImportError:
        # Fallback: use DuckDB directly
        return _fallback_query(question)
    except Exception as e:
        return jsonify({"error": str(e), "columns": [], "rows": [], "sql": ""}), 500


def _fallback_query(question: str):
    """Fallback query using DuckDB directly when Spectra isn't available.

    Does NOT import from axiom_runtime — that package is unavailable in
    fallback mode by definition. Uses an inline NL→SQL translator instead.
    """
    try:
        import duckdb
    except ImportError:
        return jsonify({"error": "duckdb not installed: pip install duckdb"}), 500

    # Inline NL→SQL — covers the most common query shapes without axiom_runtime.
    import re as _re
    q = question.lower().strip()
    DECISION_PREDS = (
        "'decided'", "'chose'", "'selected'", "'rejected'", "'confirmed'",
        "'proposed'", "'revised'", "'approved'", "'abandoned'", "'pivoted'",
    )
    IN_CLAUSE = f"({', '.join(DECISION_PREDS)})"

    if any(k in q for k in ["contradict", "conflict", "inconsisten"]):
        sql = f"""
            SELECT a.subject, a.predicate, a.object AS decision_a,
                   b.object AS decision_b, a.shard_id AS shard_a, b.shard_id AS shard_b
            FROM claims a JOIN claims b
                ON a.subject = b.subject AND a.predicate = b.predicate
               AND a.object != b.object AND a.claim_id < b.claim_id
            WHERE a.predicate IN {IN_CLAUSE} LIMIT 50
        """
    elif any(k in q for k in ["all decision", "what decision", "list decision", "every decision"]):
        sql = f"SELECT subject, predicate, object, shard_id FROM claims WHERE predicate IN {IN_CLAUSE} LIMIT 50"
    elif any(k in q for k in ["all conversations", "list all", "show all", "everything"]):
        sql = "SELECT DISTINCT subject, object AS title FROM claims WHERE predicate = 'has_title' ORDER BY subject"
    else:
        STOP = {"what", "when", "where", "which", "have", "from", "with", "about",
                "show", "find", "tell", "give", "list", "know", "does", "your", "were", "there"}
        words = [w for w in _re.split(r"\W+", q) if len(w) > 3 and w not in STOP][:4]
        if words:
            conds = " OR ".join(
                f"lower(object) LIKE '%{w}%' OR lower(subject) LIKE '%{w}%'" for w in words
            )
            sql = f"SELECT DISTINCT subject, predicate, object, shard_id FROM claims WHERE {conds} LIMIT 50"
        else:
            sql = "SELECT DISTINCT subject, object AS title FROM claims WHERE predicate = 'has_title' ORDER BY subject LIMIT 50"

    con = duckdb.connect(":memory:")
    shard_paths = sorted(
        p for p in SHARD_DIR.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )

    # Mount claims from all shards
    unions = []
    ep_unions  = []
    eng_unions = []
    for i, sp in enumerate(shard_paths):
        claims_p = sp / "graph" / "claims.parquet"
        if claims_p.exists():
            con.execute(f"CREATE VIEW s{i} AS SELECT *, '{sp.name}' AS shard_id FROM read_parquet('{claims_p}')")
            unions.append(f"SELECT * FROM s{i}")

        # Mount temporal if exists
        temp_p = sp / "ext" / "temporal.parquet"
        if temp_p.exists():
            con.execute(f"CREATE VIEW t{i} AS SELECT * FROM read_parquet('{temp_p}')")

        # Mount lineage if exists
        lin_p = sp / "ext" / "lineage.parquet"
        if lin_p.exists():
            con.execute(f"CREATE VIEW l{i} AS SELECT * FROM read_parquet('{lin_p}')")

        # Mount episodic index if exists
        ep_p = sp / "ext" / "episodes@1.parquet"
        if ep_p.exists():
            con.execute(f"CREATE VIEW ep{i} AS SELECT * FROM read_parquet('{ep_p}')")
            ep_unions.append(f"SELECT * FROM ep{i}")

        # Mount engineering lens if exists
        eng_p = sp / "ext" / "engineering@1.parquet"
        if eng_p.exists():
            con.execute(f"CREATE VIEW eng{i} AS SELECT * FROM read_parquet('{eng_p}')")
            eng_unions.append(f"SELECT * FROM eng{i}")

    if unions:
        con.execute(f"CREATE VIEW claims AS {' UNION ALL '.join(unions)}")

    if ep_unions:
        con.execute(f"CREATE VIEW episodes AS {' UNION ALL '.join(ep_unions)}")

    if eng_unions:
        con.execute(f"CREATE VIEW engineering AS {' UNION ALL '.join(eng_unions)}")

    # Create temporal/lineage unions if any exist
    temp_views = [f"SELECT * FROM t{i}" for i in range(len(shard_paths))
                  if (shard_paths[i] / "ext" / "temporal.parquet").exists()]
    if temp_views:
        con.execute(f"CREATE VIEW temporal AS {' UNION ALL '.join(temp_views)}")

    lin_views = [f"SELECT * FROM l{i}" for i in range(len(shard_paths))
                 if (shard_paths[i] / "ext" / "lineage.parquet").exists()]
    if lin_views:
        con.execute(f"CREATE VIEW lineage AS {' UNION ALL '.join(lin_views)}")

    try:
        result = con.execute(sql).fetchall()
        cols = [d[0] for d in con.description]
        rows = [[str(c) if c is not None else "" for c in row] for row in result]
        return jsonify({
            "columns": cols,
            "rows": rows,
            "sql": sql.strip(),
            "mounted": len(shard_paths),
            "fallback": True,
        })
    except Exception as e:
        return jsonify({"error": str(e), "columns": [], "rows": [], "sql": sql.strip()})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Shell-compatible endpoints (query_claims + verify_claim)
# ---------------------------------------------------------------------------

@app.route("/query_claims", methods=["POST"])
def query_claims():
    """Query shards and return structured claim objects with evidence.

    The shell renders individual claim cards, so this endpoint returns
    fully joined claim objects (not tabular columns+rows).

    Request:  { "question": "...", "max_tier": null|0|1|2 }
    Response: { "claims": [...], "sql": "...", "mounted": N }
    """
    data = request.get_json()
    question = data.get("question", "")
    max_tier = data.get("max_tier")

    if not question:
        return jsonify({"error": "No question provided"}), 400

    try:
        import duckdb
    except ImportError:
        return jsonify({"error": "duckdb not installed"}), 500

    import re as _re

    con = duckdb.connect(":memory:")
    shard_paths = sorted(
        p for p in SHARD_DIR.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )

    claim_unions = []
    entity_unions = []
    prov_unions = []
    span_unions = []

    for i, sp in enumerate(shard_paths):
        claims_p = sp / "graph" / "claims.parquet"
        entities_p = sp / "graph" / "entities.parquet"
        prov_p = sp / "graph" / "provenance.parquet"
        spans_p = sp / "evidence" / "spans.parquet"

        if claims_p.exists():
            con.execute(
                f"CREATE VIEW c{i} AS SELECT *, '{sp.name}' AS shard_name "
                f"FROM read_parquet('{claims_p}')"
            )
            claim_unions.append(f"SELECT * FROM c{i}")
        if entities_p.exists():
            con.execute(
                f"CREATE VIEW e{i} AS SELECT * "
                f"FROM read_parquet('{entities_p}')"
            )
            entity_unions.append(f"SELECT * FROM e{i}")
        if prov_p.exists():
            con.execute(
                f"CREATE VIEW p{i} AS SELECT * "
                f"FROM read_parquet('{prov_p}')"
            )
            prov_unions.append(f"SELECT * FROM p{i}")
        if spans_p.exists():
            con.execute(
                f"CREATE VIEW sp{i} AS SELECT * "
                f"FROM read_parquet('{spans_p}')"
            )
            span_unions.append(f"SELECT * FROM sp{i}")

    if not claim_unions:
        return jsonify({"claims": [], "sql": "", "mounted": 0})

    con.execute(f"CREATE VIEW claims AS {' UNION ALL '.join(claim_unions)}")
    if entity_unions:
        con.execute(f"CREATE VIEW entities AS {' UNION ALL '.join(entity_unions)}")
    if prov_unions:
        con.execute(f"CREATE VIEW provenance AS {' UNION ALL '.join(prov_unions)}")
    if span_unions:
        con.execute(f"CREATE VIEW spans AS {' UNION ALL '.join(span_unions)}")

    q = question.lower().strip()
    STOP = {
        "what", "when", "where", "which", "have", "from", "with", "about",
        "show", "find", "tell", "give", "list", "know", "does", "your",
        "were", "there", "how", "the", "and", "for", "are", "this", "that",
    }
    words = [w for w in _re.split(r"\W+", q) if len(w) > 2 and w not in STOP][:4]

    tier_clause = ""
    if max_tier is not None:
        tier_clause = f"AND c.tier <= {int(max_tier)}"

    if words:
        conds = " OR ".join(
            f"(LOWER(subj.label) LIKE '%{w}%' "
            f"OR LOWER(c.object) LIKE '%{w}%' "
            f"OR LOWER(c.predicate) LIKE '%{w}%')"
            for w in words
        )
        where = f"WHERE ({conds}) {tier_clause}"
    else:
        where = f"WHERE 1=1 {tier_clause}" if tier_clause else ""

    sql = f"""
        SELECT
            c.claim_id,
            COALESCE(subj.label, c.subject) AS subject,
            c.predicate,
            CASE
                WHEN c.object_type = 'entity' THEN COALESCE(obj.label, c.object)
                ELSE c.object
            END AS object,
            c.tier,
            COALESCE(s.text, '') AS evidence,
            COALESCE(p.source_hash, '') AS source_hash,
            COALESCE(p.byte_start, -1) AS byte_start,
            COALESCE(p.byte_end, -1) AS byte_end,
            c.shard_name
        FROM claims c
        LEFT JOIN entities subj ON c.subject = subj.entity_id
        LEFT JOIN entities obj ON c.object = obj.entity_id
            AND c.object_type = 'entity'
        LEFT JOIN provenance p ON c.claim_id = p.claim_id
        LEFT JOIN spans s ON p.source_hash = s.source_hash
            AND p.byte_start = s.byte_start
            AND p.byte_end = s.byte_end
        {where}
        ORDER BY c.tier ASC
        LIMIT 50
    """

    try:
        result = con.execute(sql).fetchall()
        claims = []
        for row in result:
            claims.append({
                "claim_id": str(row[0]) if row[0] else "",
                "subject": str(row[1]) if row[1] else "",
                "predicate": str(row[2]) if row[2] else "",
                "object": str(row[3]) if row[3] else "",
                "tier": int(row[4]) if row[4] is not None else 2,
                "evidence": str(row[5]) if row[5] else "",
                "source_hash": str(row[6]) if row[6] else "",
                "byte_start": int(row[7]) if row[7] is not None else -1,
                "byte_end": int(row[8]) if row[8] is not None else -1,
                "shard_name": str(row[9]) if row[9] else "",
            })
        return jsonify({
            "claims": claims,
            "sql": sql.strip(),
            "mounted": len(shard_paths),
        })
    except Exception as e:
        return jsonify({"error": str(e), "claims": [], "sql": sql.strip()})
    finally:
        con.close()


@app.route("/verify_claim", methods=["POST"])
def verify_claim():
    """Verify a single claim by comparing evidence to source bytes.

    The green padlock. Reads actual bytes from the content file in the
    shard and compares them to the evidence text.

    Request:  { "shard_name": "...", "source_hash": "...",
                "byte_start": N, "byte_end": N, "evidence": "..." }
    Response: { "verified": bool, "match": bool, "source_text": "..." }
    """
    data = request.get_json()
    shard_name = data.get("shard_name", "")
    source_hash = data.get("source_hash", "")
    byte_start = data.get("byte_start", -1)
    byte_end = data.get("byte_end", -1)
    evidence = data.get("evidence", "")

    if not shard_name or not source_hash or byte_start < 0 or byte_end < 0:
        return jsonify({
            "verified": False, "match": False, "source_text": "",
            "error": "Missing required fields",
        })

    shard_path = SHARD_DIR / shard_name
    if not shard_path.exists():
        return jsonify({
            "verified": False, "match": False, "source_text": "",
            "error": f"Shard not found: {shard_name}",
        })

    # Read manifest to find content file by hash
    manifest_path = shard_path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as e:
        return jsonify({
            "verified": False, "match": False, "source_text": "",
            "error": f"Cannot read manifest: {e}",
        })

    import hashlib as _hashlib

    content_path = None
    for src in manifest.get("sources", []):
        candidate = shard_path / src.get("path", "")
        if candidate.exists():
            file_hash = _hashlib.sha256(candidate.read_bytes()).hexdigest()
            if file_hash == source_hash:
                content_path = candidate
                break

    # Fallback: content/source.txt
    if content_path is None:
        fallback = shard_path / "content" / "source.txt"
        if fallback.exists():
            file_hash = _hashlib.sha256(fallback.read_bytes()).hexdigest()
            if file_hash == source_hash:
                content_path = fallback

    if content_path is None:
        return jsonify({
            "verified": False, "match": False, "source_text": "",
            "error": f"No content file matches hash {source_hash[:16]}...",
        })

    try:
        file_bytes = content_path.read_bytes()
        if byte_end > len(file_bytes):
            return jsonify({
                "verified": False, "match": False, "source_text": "",
                "error": f"Byte range exceeds file size ({len(file_bytes)} bytes)",
            })

        source_text = file_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
        match = (source_text.strip() == evidence.strip())

        return jsonify({
            "verified": True,
            "match": match,
            "source_text": source_text,
        })
    except Exception as e:
        return jsonify({
            "verified": False, "match": False, "source_text": "",
            "error": str(e),
        })


@app.route("/verify", methods=["POST"])
def verify():
    """Verify a shard."""
    data = request.get_json()
    shard_name = data.get("shard")

    if not shard_name:
        return jsonify({"error": "No shard specified"}), 400

    shard_path = SHARD_DIR / shard_name
    if not shard_path.exists():
        return jsonify({"error": f"Shard not found: {shard_name}"}), 404

    try:
        from axm_verify.logic import verify_shard
        trusted_key = shard_path / "sig" / "publisher.pub"
        result = verify_shard(shard_path, trusted_key_path=trusted_key)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n  AXM Server")
    print(f"  Port:      {PORT}")
    print(f"  Shards:    {SHARD_DIR}")
    print(f"  Ollama:    {'✓' if _check_ollama() else '✗ (needed for distill)'}")
    print(f"  UI:        http://localhost:{PORT}  (or open index.html)")
    print()
    app.run(host="0.0.0.0", port=PORT, debug=False)
