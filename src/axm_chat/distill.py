"""
axm_chat / distill.py
======================
Second-pass compiler: conversation shard → decision shard.

Reads a compiled conversation shard (Tier 0/1 claims from axm_chat import),
extracts decisions via Tier 3 LLM, and produces a smaller decision shard
that supersedes the conversation shard via lineage@1.

Pipeline:
    conversation shard (400 claims, full turns)
      → LLM decision extraction (Ollama, local)
      → decision shard (20-50 claims, decisions only)
      → lineage@1: supersedes conversation shard
      → temporal@1: decided_at timestamps on every claim
      → references@1: decision claims cite conversation shard evidence

Usage:
    from axm_chat.distill import distill_shard, distill_directory

    # Single shard
    result = distill_shard("~/.axm/shards/chat_AXM_Genesis_abc123", model="mistral")

    # All shards in directory
    results = distill_directory("~/.axm/shards/", model="mistral")

CLI (added to axm_chat.py):
    axm-chat distill                          # all shards
    axm-chat distill --shard chat_AXM_abc123  # one shard
    axm-chat distill --model llama3           # specify model
    axm-chat distill --dry-run                # show what would be extracted
"""
from __future__ import annotations

import json
import hashlib
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "mistral"  # good balance of speed and quality on 8GB VRAM
DECISION_PREDICATES = frozenset({
    "decided", "chose", "selected", "rejected", "confirmed",
    "proposed", "revised", "superseded", "approved", "committed",
    "adopted", "abandoned", "deferred", "pivoted", "discovered",
})
MAX_TURNS_PER_BATCH = 30  # fit in context window with room for output
OVERLAP_TURNS = 5         # overlap between batches for continuity


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DecisionCandidate:
    """A decision extracted from conversation text."""
    subject: str
    predicate: str
    object: str
    evidence: str           # exact quote from source text
    decided_at: str         # ISO 8601 timestamp
    reasoning: str          # why this decision was made
    alternatives: str       # what was rejected (empty if none stated)
    confidence: float       # 0.0-1.0, from LLM self-assessment
    turn_index: int         # which turn in the conversation
    speaker: str            # who made the decision


@dataclass
class DistillResult:
    """Result of distilling one conversation shard."""
    source_shard_id: str
    source_shard_path: Path
    decision_shard_path: Optional[Path]
    decisions: List[DecisionCandidate]
    status: str             # "ok", "empty", "error", "dry_run"
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

DECISION_EXTRACTION_PROMPT = """You are extracting DECISIONS from a conversation between a human and an AI assistant.

A decision is a COMMITMENT — something that was actually chosen, rejected, confirmed, or changed.
NOT a question. NOT a suggestion. NOT a discussion. A DECISION.

Examples of decisions:
- "We will use BLAKE3 for hashing" → decided, use_blake3_for_hashing
- "I'm dropping the Redis dependency" → decided, drop_redis_dependency
- "Let's go with Ed25519 for now" → chose, ed25519_for_signatures
- "That approach won't work because X" → rejected, approach_X
- "Actually, let's switch to ML-DSA-44" → revised, switch_to_mldsa44

Examples of NOT decisions:
- "What do you think about BLAKE3?" → just a question
- "Maybe we could try..." → just a suggestion
- "BLAKE3 is a hash function that..." → just information

For each decision found, return a JSON object with:
- subject: what the decision is about (short, snake_case, e.g. "merkle_tree_algorithm")
- predicate: one of: decided, chose, selected, rejected, confirmed, proposed, revised, superseded, approved, committed, adopted, abandoned, deferred, pivoted, discovered
- object: what was decided (short, snake_case, e.g. "use_blake3")
- evidence: the EXACT quote from the conversation that contains this decision (must be a verbatim substring)
- decided_at: the timestamp from the conversation turn (copy it exactly)
- reasoning: brief explanation of WHY (1 sentence)
- alternatives: what was considered and rejected (empty string if none mentioned)
- confidence: 0.0-1.0 how confident you are this is a real decision
- turn_index: which turn number contains this decision
- speaker: "human" or "assistant"

Return ONLY a JSON array. No markdown, no explanation, no preamble.
If no decisions exist in this section, return an empty array: []

CONVERSATION SECTION:
{conversation_text}"""


# ---------------------------------------------------------------------------
# Ollama interface
# ---------------------------------------------------------------------------

def _ollama_available(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    """Check if Ollama is running."""
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ollama_generate(
    prompt: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    temperature: float = 0.1,
    timeout: int = 120,
) -> str:
    """Call Ollama's generate endpoint. Returns raw text response."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": 4096,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Ollama not reachable at {base_url}: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Ollama generate failed: {e}") from e


def _parse_llm_response(raw: str) -> List[Dict[str, Any]]:
    """Parse LLM JSON response, handling common formatting issues."""
    text = raw.strip()

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    if not text or text == "[]":
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return []
    except json.JSONDecodeError:
        # Try to find JSON array in the response
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return []


# ---------------------------------------------------------------------------
# Shard reading
# ---------------------------------------------------------------------------

def _read_shard_manifest(shard_path: Path) -> Dict[str, Any]:
    """Read and return the shard's manifest."""
    manifest_path = shard_path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {shard_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _read_shard_source(shard_path: Path) -> str:
    """Read the shard's source text."""
    source_path = shard_path / "content" / "source.txt"
    if not source_path.exists():
        raise FileNotFoundError(f"No content/source.txt in {shard_path}")
    return source_path.read_text(encoding="utf-8")


def _read_shard_claims(shard_path: Path) -> List[Dict[str, Any]]:
    """Read claims from parquet using DuckDB."""
    import duckdb
    claims_path = shard_path / "graph" / "claims.parquet"
    if not claims_path.exists():
        return []
    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"SELECT * FROM read_parquet('{claims_path}')"
    ).fetchall()
    cols = [d[0] for d in con.description]
    con.close()
    return [dict(zip(cols, row)) for row in rows]


def _extract_turns_from_source(source_text: str) -> List[Dict[str, str]]:
    """Parse the source text back into turns with timestamps.

    The source format from axm_chat is:
        HUMAN [2026-01-15T10:30:00Z]:
        message content here

        ASSISTANT [2026-01-15T10:31:00Z]:
        response content here
    """
    turns = []
    # Match turn headers: ROLE [timestamp]: or ROLE:
    pattern = re.compile(
        r"^(HUMAN|ASSISTANT|USER|SYSTEM)\s*"
        r"(?:\[([^\]]*)\])?\s*:\s*$",
        re.MULTILINE,
    )

    matches = list(pattern.finditer(source_text))
    for i, m in enumerate(matches):
        role = m.group(1).lower()
        timestamp = m.group(2) or ""
        # Content runs from end of this header to start of next header
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(source_text)
        content = source_text[content_start:content_end].strip()

        turns.append({
            "role": role,
            "timestamp": timestamp,
            "content": content,
            "turn_index": i,
        })

    return turns


# ---------------------------------------------------------------------------
# Decision extraction
# ---------------------------------------------------------------------------

def _batch_turns(
    turns: List[Dict[str, str]],
    batch_size: int = MAX_TURNS_PER_BATCH,
    overlap: int = OVERLAP_TURNS,
) -> List[List[Dict[str, str]]]:
    """Split turns into overlapping batches for LLM processing."""
    if len(turns) <= batch_size:
        return [turns]

    batches = []
    start = 0
    while start < len(turns):
        end = min(start + batch_size, len(turns))
        batches.append(turns[start:end])
        start = end - overlap
        if start >= len(turns) - overlap:
            break

    return batches


def _format_batch_for_prompt(turns: List[Dict[str, str]]) -> str:
    """Format a batch of turns for the extraction prompt."""
    lines = []
    for t in turns:
        role = t["role"].upper()
        ts = t.get("timestamp", "")
        idx = t.get("turn_index", "?")
        header = f"[Turn {idx}] {role} [{ts}]:" if ts else f"[Turn {idx}] {role}:"
        lines.append(header)
        lines.append(t["content"])
        lines.append("")
    return "\n".join(lines)


def extract_decisions(
    source_text: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    on_progress: Any = None,
) -> List[DecisionCandidate]:
    """Extract decisions from conversation source text via LLM.

    Args:
        source_text: The full source.txt from a conversation shard.
        model: Ollama model name.
        base_url: Ollama server URL.
        on_progress: Optional callback(batch_num, total_batches, decisions_so_far).

    Returns:
        List of DecisionCandidate objects.
    """
    turns = _extract_turns_from_source(source_text)
    if not turns:
        return []

    batches = _batch_turns(turns)
    all_decisions: List[DecisionCandidate] = []
    seen_evidence: set = set()  # dedup across overlapping batches

    for batch_idx, batch in enumerate(batches):
        prompt_text = _format_batch_for_prompt(batch)
        prompt = DECISION_EXTRACTION_PROMPT.format(conversation_text=prompt_text)

        try:
            raw_response = _ollama_generate(prompt, model=model, base_url=base_url)
            raw_decisions = _parse_llm_response(raw_response)
        except Exception as e:
            if on_progress:
                on_progress(batch_idx + 1, len(batches), len(all_decisions), str(e))
            continue

        for d in raw_decisions:
            evidence = d.get("evidence", "")
            if not evidence or evidence in seen_evidence:
                continue

            # Validate evidence exists in source text
            if evidence not in source_text:
                # Try fuzzy match — first 60 chars
                truncated = evidence[:60]
                if truncated not in source_text:
                    continue

            predicate = d.get("predicate", "decided").lower()
            if predicate not in DECISION_PREDICATES:
                predicate = "decided"

            seen_evidence.add(evidence)
            all_decisions.append(DecisionCandidate(
                subject=str(d.get("subject", "unknown")).strip(),
                predicate=predicate,
                object=str(d.get("object", "")).strip(),
                evidence=evidence,
                decided_at=str(d.get("decided_at", "")).strip(),
                reasoning=str(d.get("reasoning", "")).strip(),
                alternatives=str(d.get("alternatives", "")).strip(),
                confidence=float(d.get("confidence", 0.5)),
                turn_index=int(d.get("turn_index", -1)),
                speaker=str(d.get("speaker", "unknown")).strip(),
            ))

        if on_progress:
            on_progress(batch_idx + 1, len(batches), len(all_decisions), None)

    return all_decisions


# ---------------------------------------------------------------------------
# Decision shard compilation
# ---------------------------------------------------------------------------

def _build_decision_source_text(
    decisions: List[DecisionCandidate],
    original_title: str,
    original_shard_id: str,
) -> str:
    """Build the source.txt for a decision shard.

    This is a structured document — not conversation text.
    Each decision becomes a self-contained block with all metadata.
    """
    lines = [
        f"=== DECISION SHARD: {original_title} ===",
        f"Source: {original_shard_id}",
        f"Distilled: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Decisions: {len(decisions)}",
        "",
    ]

    for i, d in enumerate(decisions):
        lines.append(f"--- Decision {i+1} ---")
        lines.append(f"Subject: {d.subject}")
        lines.append(f"Action: {d.predicate}")
        lines.append(f"Object: {d.object}")
        lines.append(f"When: {d.decided_at}")
        lines.append(f"Speaker: {d.speaker}")
        lines.append(f"Reasoning: {d.reasoning}")
        if d.alternatives:
            lines.append(f"Rejected alternatives: {d.alternatives}")
        lines.append(f"Confidence: {d.confidence}")
        lines.append(f"Source turn: {d.turn_index}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _build_decision_candidates(
    decisions: List[DecisionCandidate],
    original_shard_id: str,
    source_text: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build candidates, temporal records, and reference records for genesis.

    Returns:
        (candidates, temporal_records, reference_records)
    """
    candidates = []
    temporal_records = []
    reference_records = []

    namespace = f"decisions/{original_shard_id[:32]}"

    for d in decisions:
        # The evidence for the decision shard is the structured block
        evidence_block = f"--- Decision"
        # Find the specific block in source_text
        block_marker = f"Subject: {d.subject}\nAction: {d.predicate}\nObject: {d.object}"
        if block_marker not in source_text:
            continue

        candidate = {
            "subject": f"project/{d.subject}",
            "predicate": d.predicate,
            "object": d.object,
            "object_type": "literal:string",
            "tier": 3,
            "evidence": block_marker,
        }
        candidates.append(candidate)

        # Temporal record: when was this decided
        if d.decided_at:
            temporal_records.append({
                "valid_from": d.decided_at,
                "valid_until": "",  # until superseded
                "temporal_context": f"Decision made in conversation, turn {d.turn_index}",
            })

        # Reference back to source conversation shard
        reference_records.append({
            "relation_type": "derives_from",
            "dst_shard_id": original_shard_id,
            "dst_object_type": "shard",
            "dst_object_id": original_shard_id,
            "confidence": d.confidence,
            "note": f"Extracted from turn {d.turn_index} by {d.speaker}",
        })

    return candidates, temporal_records, reference_records


def compile_decision_shard(
    decisions: List[DecisionCandidate],
    original_shard_path: Path,
    original_manifest: Dict[str, Any],
    output_dir: Path,
    key_dir: Path,
    suite: str = "ed25519",
) -> Optional[Path]:
    """Compile a decision shard from extracted decisions.

    Creates a new shard with:
    - Core claims (decisions as structured subject/predicate/object)
    - ext/lineage@1: supersedes the conversation shard
    - ext/temporal@1: decided_at timestamps
    - ext/references@1: links back to conversation shard

    Returns the output shard path, or None on failure.
    """
    # Lazy import — only needed at compile time
    import sys
    try:
        from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
        from axm_build.sign import SUITE_ED25519
        from axm_build.common import write_parquet_deterministic
    except ImportError:
        raise ImportError(
            "axm-genesis not found. Install it or set AXM_GENESIS_SRC."
        )

    original_shard_id = original_manifest.get("shard_id", "unknown")
    original_title = original_manifest.get("metadata", {}).get("title", "untitled")

    # Build source text and candidates
    source_text = _build_decision_source_text(decisions, original_title, original_shard_id)
    candidates, temporal_records, reference_records = _build_decision_candidates(
        decisions, original_shard_id, source_text
    )

    if not candidates:
        return None

    # Write temp files for genesis compiler
    work_dir = output_dir.parent / f".work_distill_{original_shard_id[:16]}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        source_path = work_dir / "source.txt"
        candidates_path = work_dir / "candidates.jsonl"

        source_path.write_text(source_text, encoding="utf-8")
        with candidates_path.open("w", encoding="utf-8") as f:
            for c in candidates:
                row = {k: c[k] for k in
                       ("subject", "predicate", "object", "object_type", "evidence", "tier")
                       if k in c}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        # Get or create signing key
        key_dir.mkdir(parents=True, exist_ok=True)
        sk_path = key_dir / f"publisher_{suite}.key"
        if sk_path.exists():
            private_key = sk_path.read_bytes()
        else:
            from nacl.signing import SigningKey
            ed_sk = SigningKey.generate()
            sk_bytes = bytes(ed_sk)
            pk_bytes = bytes(ed_sk.verify_key)
            sk_path.write_bytes(sk_bytes)
            (key_dir / f"publisher_{suite}.pub").write_bytes(pk_bytes)
            private_key = sk_bytes

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        cfg = CompilerConfig(
            source_path=source_path,
            candidates_path=candidates_path,
            out_dir=output_dir,
            private_key=private_key,
            publisher_id="@axm_chat_distill",
            publisher_name="axm-chat-distill",
            namespace=f"decisions/{original_shard_id[:32]}",
            created_at=now,
            suite=suite,
        )

        # Write ext/ BEFORE calling compile_generic_shard.
        #
        # genesis's Merkle tree walks the entire output_dir (excluding
        # manifest.json and sig/).  Any file written after compile_generic_shard
        # returns is outside the Merkle root — the manifest signature would
        # be invalid and axm-verify REQ 1 + REQ 4 would fail.
        #
        # Sequence:
        #   1. Write temporal@1 and references@1 — no dependency on compiled
        #      claim_ids or the decision shard_id, so they can go in now.
        #   2. compile_generic_shard() — Merkle walks ext/, includes them.
        #   3. Write lineage@1 with the real decision shard_id from the manifest.
        #   4. _reseal_shard() — recompute Merkle + re-sign, because lineage was
        #      added after the first compile pass.

        ext_dir = output_dir / "ext"
        ext_dir.mkdir(parents=True, exist_ok=True)

        # Step 1a: temporal@1 — decided_at timestamps per claim
        # Note: we use positional alignment (decision index → claim index) because
        # claim_ids aren't available until after compile.  The mapping is stable
        # because compile_generic_shard writes claims in candidate order.
        if temporal_records:
            _write_temporal_extension_pre(
                ext_dir / "temporal.parquet",
                temporal_records=temporal_records,
            )

        # Step 1b: references@1 — cite the conversation shard
        if reference_records:
            _write_references_extension_pre(
                ext_dir / "references.parquet",
                reference_records=reference_records,
                original_shard_id=original_shard_id,
            )

        # Step 2: compile — Merkle now covers ext/ files written above
        result = compile_generic_shard(cfg)
        if not result:
            return None

        # Step 3: lineage@1 — needs the decision shard_id from the manifest
        manifest_path = output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        decision_shard_id = manifest["shard_id"]

        _write_lineage_extension(
            ext_dir / "lineage.parquet",
            shard_id=decision_shard_id,
            supersedes_shard_id=original_shard_id,
            timestamp=now,
        )

        # Step 4: reseal — recompute Merkle over all files (now including lineage)
        # and re-sign the manifest.  This is the only valid path when an ext/
        # file must contain the shard_id that isn't known until compile time.
        _reseal_shard(
            shard_dir=output_dir,
            manifest=manifest,
            private_key=private_key,
            suite=suite,
            extra_meta={
                "title": f"decisions: {original_title}",
                "source_shard": original_shard_id,
                "decision_count": len(decisions),
            },
        )

        return output_dir

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _read_claim_ids(shard_path: Path) -> List[str]:
    """Read claim IDs from a compiled shard."""
    import duckdb
    claims_path = shard_path / "graph" / "claims.parquet"
    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"SELECT claim_id FROM read_parquet('{claims_path}') ORDER BY claim_id"
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def _reseal_shard(
    shard_dir: Path,
    manifest: Dict[str, Any],
    private_key: bytes,
    suite: str,
    extra_meta: Dict[str, Any] | None = None,
) -> None:
    """Recompute Merkle root over shard_dir and re-sign the manifest.

    Called after lineage.parquet is written post-compile.  The first
    compile_generic_shard() pass sealed the shard without lineage.parquet;
    this function produces a valid sealed state that includes it.

    The shard_id changes (it encodes the Merkle root), so the manifest
    is rewritten and re-signed in place.
    """
    from axm_build.merkle import compute_merkle_root
    from axm_build.manifest import dumps_canonical_json

    new_merkle = compute_merkle_root(shard_dir, suite=suite)
    new_shard_id = f"shard_blake3_{new_merkle}"

    # Detect active extensions from ext/ now that lineage is written
    ext_dir = shard_dir / "ext"
    active_ext: List[str] = []
    if ext_dir.exists():
        for f in sorted(ext_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                active_ext.append(f"{f.stem}@1")

    # Rebuild manifest with new Merkle root + shard_id
    manifest["integrity"]["merkle_root"] = new_merkle
    manifest["shard_id"] = new_shard_id
    if active_ext:
        manifest["extensions"] = active_ext
    if extra_meta:
        manifest.setdefault("metadata", {}).update(extra_meta)

    manifest_bytes = dumps_canonical_json(manifest)
    (shard_dir / "manifest.json").write_bytes(manifest_bytes)

    # Re-sign
    sig_dir = shard_dir / "sig"
    sig_dir.mkdir(exist_ok=True)

    from axm_build.sign import SUITE_MLDSA44
    if suite == SUITE_MLDSA44:
        from axm_build.sign import mldsa44_sign
        if len(private_key) == 3840:
            sk_bytes = private_key[:2528]
            pk_bytes = private_key[2528:]
        else:
            sk_bytes = private_key
            pk_bytes = (sig_dir / "publisher.pub").read_bytes()
        sig = mldsa44_sign(sk_bytes, manifest_bytes)
        (sig_dir / "publisher.pub").write_bytes(pk_bytes)
        (sig_dir / "manifest.sig").write_bytes(sig)
    else:
        from axm_build.sign import signing_key_from_private_key_bytes
        sk = signing_key_from_private_key_bytes(private_key[:32])
        (sig_dir / "publisher.pub").write_bytes(bytes(sk.verify_key))
        (sig_dir / "manifest.sig").write_bytes(sk.sign(manifest_bytes).signature)


def _write_temporal_extension_pre(
    path: Path,
    temporal_records: List[Dict[str, Any]],
) -> None:
    """Write ext/temporal.parquet before compilation.

    Uses decision index as a stable positional key.  claim_id values
    are filled with placeholder strings that get matched to real claim_ids
    by Spectra's JOIN on the compiled claims table.

    The placeholder format is "decision_N" — unique within the file,
    consistent with the order candidates are emitted to candidates.jsonl.
    """
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE temporal (
            claim_id VARCHAR,
            valid_from VARCHAR,
            valid_until VARCHAR,
            temporal_context VARCHAR
        )
    """)
    for i, rec in enumerate(temporal_records):
        con.execute(
            "INSERT INTO temporal VALUES (?, ?, ?, ?)",
            [f"decision_{i}", rec["valid_from"], rec["valid_until"], rec["temporal_context"]],
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY temporal TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()


def _write_references_extension_pre(
    path: Path,
    reference_records: List[Dict[str, Any]],
    original_shard_id: str,
) -> None:
    """Write ext/references.parquet before compilation.

    Uses the same positional placeholder scheme as _write_temporal_extension_pre.
    dst_shard_id is the conversation shard — known before compilation.
    """
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE refs (
            src_claim_id VARCHAR,
            relation_type VARCHAR,
            dst_shard_id VARCHAR,
            dst_object_type VARCHAR,
            dst_object_id VARCHAR,
            confidence FLOAT,
            note VARCHAR
        )
    """)
    for i, rec in enumerate(reference_records):
        con.execute(
            "INSERT INTO refs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [f"decision_{i}", rec["relation_type"], original_shard_id,
             rec.get("dst_object_type", "shard"), rec.get("dst_object_id", original_shard_id),
             rec.get("confidence", 0.9), rec.get("note", "")],
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY refs TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()


def _write_lineage_extension(
    path: Path,
    shard_id: str,
    supersedes_shard_id: str,
    timestamp: str,
) -> None:
    """Write ext/lineage@1.parquet."""
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE lineage (
            shard_id VARCHAR,
            supersedes_shard_id VARCHAR,
            action VARCHAR,
            timestamp VARCHAR,
            note VARCHAR
        )
    """)
    con.execute(
        "INSERT INTO lineage VALUES (?, ?, ?, ?, ?)",
        [shard_id, supersedes_shard_id, "supersede", timestamp,
         "Decision shard distilled from conversation shard"],
    )
    con.execute(f"COPY lineage TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()


def _write_temporal_extension(
    path: Path,
    claim_ids: List[str],
    temporal_records: List[Dict[str, Any]],
) -> None:
    """Write ext/temporal@1.parquet."""
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE temporal (
            claim_id VARCHAR,
            valid_from VARCHAR,
            valid_until VARCHAR,
            temporal_context VARCHAR
        )
    """)
    for cid, rec in zip(claim_ids, temporal_records):
        con.execute(
            "INSERT INTO temporal VALUES (?, ?, ?, ?)",
            [cid, rec["valid_from"], rec["valid_until"], rec["temporal_context"]],
        )
    con.execute(f"COPY temporal TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()


def _write_references_extension(
    path: Path,
    claim_ids: List[str],
    reference_records: List[Dict[str, Any]],
) -> None:
    """Write ext/references@1.parquet."""
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE refs (
            src_claim_id VARCHAR,
            relation_type VARCHAR,
            dst_shard_id VARCHAR,
            dst_object_type VARCHAR,
            dst_object_id VARCHAR,
            confidence FLOAT,
            note VARCHAR
        )
    """)
    for cid, rec in zip(claim_ids, reference_records):
        con.execute(
            "INSERT INTO refs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [cid, rec["relation_type"], rec["dst_shard_id"],
             rec["dst_object_type"], rec["dst_object_id"],
             rec["confidence"], rec["note"]],
        )
    con.execute(f"COPY refs TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

def distill_shard(
    shard_path: str | Path,
    output_base: str | Path | None = None,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    key_dir: str | Path | None = None,
    suite: str = "ed25519",
    dry_run: bool = False,
    on_progress: Any = None,
) -> DistillResult:
    """Distill a single conversation shard into a decision shard.

    Args:
        shard_path: Path to the conversation shard directory.
        output_base: Directory to write the decision shard (default: sibling of input).
        model: Ollama model name.
        base_url: Ollama server URL.
        key_dir: Directory for signing keys (default: ~/.axm/keys/).
        suite: Crypto suite ("ed25519" or "axm-blake3-mldsa44").
        dry_run: If True, extract decisions but don't compile a shard.
        on_progress: Optional callback for progress updates.

    Returns:
        DistillResult with decisions and status.
    """
    shard_path = Path(shard_path)
    key_dir = Path(key_dir) if key_dir else Path.home() / ".axm" / "keys"

    # Read the source shard
    try:
        manifest = _read_shard_manifest(shard_path)
        source_text = _read_shard_source(shard_path)
    except FileNotFoundError as e:
        return DistillResult(
            source_shard_id="unknown",
            source_shard_path=shard_path,
            decision_shard_path=None,
            decisions=[],
            status="error",
            error=str(e),
        )

    shard_id = manifest.get("shard_id", "unknown")
    title = manifest.get("metadata", {}).get("title", "untitled")

    # Check if this is already a decision shard
    if manifest.get("metadata", {}).get("source_shard"):
        return DistillResult(
            source_shard_id=shard_id,
            source_shard_path=shard_path,
            decision_shard_path=None,
            decisions=[],
            status="error",
            error="Already a decision shard — skipping",
        )

    # Check Ollama availability
    if not dry_run and not _ollama_available(base_url):
        return DistillResult(
            source_shard_id=shard_id,
            source_shard_path=shard_path,
            decision_shard_path=None,
            decisions=[],
            status="error",
            error=f"Ollama not available at {base_url}. Start it: ollama serve",
        )

    # Extract decisions
    decisions = extract_decisions(
        source_text, model=model, base_url=base_url, on_progress=on_progress
    )

    if not decisions:
        return DistillResult(
            source_shard_id=shard_id,
            source_shard_path=shard_path,
            decision_shard_path=None,
            decisions=[],
            status="empty",
        )

    if dry_run:
        return DistillResult(
            source_shard_id=shard_id,
            source_shard_path=shard_path,
            decision_shard_path=None,
            decisions=decisions,
            status="dry_run",
        )

    # Compile decision shard
    if output_base is None:
        output_base = shard_path.parent

    output_dir = Path(output_base) / f"decisions_{shard_path.name}"

    try:
        result_path = compile_decision_shard(
            decisions=decisions,
            original_shard_path=shard_path,
            original_manifest=manifest,
            output_dir=output_dir,
            key_dir=key_dir,
            suite=suite,
        )
    except Exception as e:
        return DistillResult(
            source_shard_id=shard_id,
            source_shard_path=shard_path,
            decision_shard_path=None,
            decisions=decisions,
            status="error",
            error=str(e),
        )

    return DistillResult(
        source_shard_id=shard_id,
        source_shard_path=shard_path,
        decision_shard_path=result_path,
        decisions=decisions,
        status="ok" if result_path else "error",
        error=None if result_path else "Compilation failed",
    )


def distill_directory(
    shard_dir: str | Path,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    key_dir: str | Path | None = None,
    suite: str = "ed25519",
    dry_run: bool = False,
    on_progress: Any = None,
) -> List[DistillResult]:
    """Distill all conversation shards in a directory.

    Skips shards that are already decision shards (have metadata.source_shard).

    Returns list of DistillResult for each shard processed.
    """
    shard_dir = Path(shard_dir)
    results = []

    shards = sorted(
        p for p in shard_dir.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )

    for shard in shards:
        result = distill_shard(
            shard_path=shard,
            output_base=shard_dir,
            model=model,
            base_url=base_url,
            key_dir=key_dir,
            suite=suite,
            dry_run=dry_run,
            on_progress=on_progress,
        )
        results.append(result)

    return results
