"""
axm_chat.spoke — Chat spoke: extraction and compilation.

This module knows how to:
  1. Detect export formats (Claude, ChatGPT, generic)
  2. Extract conversations into structured candidates
  3. Compile conversation shards via Genesis

It does NOT know how to query, mount, or display anything.
That's Spectra's job.
"""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

# Direct imports — these are declared dependencies in pyproject.toml.
# No path searching. No try/except. If these fail, the install is broken.
from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
from axm_build.sign import SUITE_ED25519, mldsa44_keygen

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_SHARD_DIR = Path.home() / ".axm" / "shards"
DEFAULT_KEY_DIR = Path.home() / ".axm" / "keys"
SUITE = SUITE_ED25519


# ---------------------------------------------------------------------------
# Export format detection
# ---------------------------------------------------------------------------

def detect_export_type(data: Any) -> str:
    """Return 'claude', 'chatgpt', 'generic', or 'unknown'."""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            if "chat_messages" in first or ("uuid" in first and "name" in first):
                return "claude"
            if "mapping" in first or ("id" in first and "title" in first):
                return "chatgpt"
            if "role" in first and "content" in first:
                return "generic"
    if isinstance(data, dict):
        if "mapping" in data:
            return "chatgpt"
    return "unknown"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _iso(ts: Any) -> str:
    if not ts:
        return ""
    if isinstance(ts, str):
        return ts
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return str(ts)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _flatten_openai_tree(mapping: dict) -> list[dict]:
    """Walk the canonical branch of a ChatGPT conversation tree."""
    def _ts(node: dict) -> float:
        msg = node.get("message") or {}
        raw = msg.get("create_time")
        try:
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _text(msg: dict) -> str:
        content = msg.get("content") or {}
        if isinstance(content, dict) and "parts" in content:
            return "\n".join(str(p) for p in content["parts"] if p)
        if isinstance(content, str):
            return content
        return ""

    root_id = None
    for nid, node in mapping.items():
        parent = node.get("parent")
        if parent is None or parent not in mapping:
            root_id = nid
            break
    if root_id is None:
        root_id = min(mapping, key=lambda nid: _ts(mapping[nid]))

    messages: list[dict] = []
    current_id: str | None = root_id

    while current_id is not None:
        node = mapping.get(current_id)
        if node is None:
            break
        msg = node.get("message")
        if msg:
            text = _text(msg).strip()
            if text:
                author = (msg.get("author") or {}).get("role", "unknown")
                ts_raw = msg.get("create_time")
                messages.append({
                    "role": author,
                    "content": text,
                    "timestamp": _iso(ts_raw),
                    "id": msg.get("id") or current_id,
                })
        children = node.get("children") or []
        if not children:
            break
        children_sorted = sorted(children, key=lambda cid: _ts(mapping.get(cid, {})))
        current_id = children_sorted[-1]

    return messages


# ---------------------------------------------------------------------------
# Per-conversation extraction
# ---------------------------------------------------------------------------

def extract_conversation(conv: dict, conv_idx: int, export_type: str) -> dict | None:
    """Extract one conversation into {source_text, candidates, meta}.
    Returns None if the conversation has no usable content.
    """
    if export_type == "claude":
        return _extract_claude_conv(conv, conv_idx)
    elif export_type == "chatgpt":
        return _extract_chatgpt_conv(conv, conv_idx)
    else:
        return _extract_generic_conv(conv, conv_idx)


def _make_turn_block(role: str, ts: str, content: str, turn_idx: int, conv_id: str) -> tuple[str, dict]:
    """Build a source text block and a candidate dict for one turn."""
    label = {"human": "HUMAN", "assistant": "ASSISTANT", "user": "HUMAN"}.get(
        role.lower(), role.upper()
    )
    prefix = f"{label} [{ts}]:" if ts else f"{label}:"
    block = f"{prefix}\n{content}"
    candidate = {
        "subject": f"conversation/{conv_id}",
        "predicate": "has_turn",
        "object": f"turn/{turn_idx}",
        "object_type": "entity",
        "tier": 1,
        "evidence": prefix,
        "meta": {
            "kind": "chat",
            "conversation_id": conv_id,
            "turn_index": turn_idx,
            "role": role,
            "timestamp": ts,
        },
    }
    return block, candidate


def _extract_claude_conv(conv: dict, conv_idx: int) -> dict | None:
    conv_id = conv.get("uuid") or conv.get("id") or f"conv_{conv_idx}"
    title = conv.get("name") or conv.get("title") or f"Conversation {conv_idx}"
    created_at = conv.get("created_at") or ""
    messages = conv.get("chat_messages") or []

    if not messages:
        return None

    blocks, candidates = _make_header_and_meta(conv_id, title, created_at, len(messages))

    for turn_idx, msg in enumerate(messages):
        role = str(msg.get("sender") or msg.get("role") or "unknown")
        content = msg.get("text") or msg.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(
                (p.get("text") or p.get("body") or "") if isinstance(p, dict) else str(p)
                for p in content
            )
        content = str(content).strip()
        if not content:
            continue

        ts = _iso(msg.get("created_at") or msg.get("timestamp") or "")
        block, cand = _make_turn_block(role, ts, content, turn_idx, conv_id)
        blocks.append(block)
        candidates.append(cand)

    source_text = _normalize("\n\n".join(blocks))
    return {
        "conv_id": conv_id, "title": title, "created_at": created_at,
        "source_text": source_text, "candidates": candidates,
        "turn_count": len([c for c in candidates if c["predicate"] == "has_turn"]),
    }


def _extract_chatgpt_conv(conv: dict, conv_idx: int) -> dict | None:
    conv_id = conv.get("id") or f"conv_{conv_idx}"
    title = conv.get("title") or f"Conversation {conv_idx}"
    created_at = _iso(conv.get("create_time") or "")
    mapping = conv.get("mapping") or {}
    messages_list = conv.get("messages") or []

    if mapping:
        messages = _flatten_openai_tree(mapping)
    elif messages_list:
        messages = [
            {"role": m.get("role", "unknown"), "content": m.get("content", ""),
             "timestamp": _iso(m.get("create_time") or ""), "id": m.get("id", "")}
            for m in messages_list
        ]
    else:
        return None

    if not messages:
        return None

    blocks, candidates = _make_header_and_meta(conv_id, title, created_at, len(messages))

    for turn_idx, msg in enumerate(messages):
        role = str(msg.get("role", "unknown"))
        content = str(msg.get("content", "")).strip()
        if not content or role == "system":
            continue

        ts = msg.get("timestamp", "")
        block, cand = _make_turn_block(role, ts, content, turn_idx, conv_id)
        cand["meta"]["message_id"] = msg.get("id", "")
        blocks.append(block)
        candidates.append(cand)

    source_text = _normalize("\n\n".join(blocks))
    return {
        "conv_id": conv_id, "title": title, "created_at": created_at,
        "source_text": source_text, "candidates": candidates,
        "turn_count": len([c for c in candidates if c["predicate"] == "has_turn"]),
    }


def _extract_generic_conv(conv: dict, conv_idx: int) -> dict | None:
    conv_id = conv.get("id") or conv.get("conversation_id") or f"conv_{conv_idx}"
    title = conv.get("title") or conv.get("name") or f"Conversation {conv_idx}"
    messages = conv.get("messages") or (conv if isinstance(conv, list) else [])

    if not messages:
        return None

    header = f"=== CONVERSATION: {title} ===\nID: {conv_id}\n"
    blocks = [header]
    candidates = [{
        "subject": f"conversation/{conv_id}", "predicate": "has_title",
        "object": title, "object_type": "literal:string", "tier": 0,
        "evidence": f"=== CONVERSATION: {title} ===",
    }]

    for turn_idx, msg in enumerate(messages):
        role = str(msg.get("role", "unknown"))
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        block, cand = _make_turn_block(role, "", content, turn_idx, conv_id)
        blocks.append(block)
        candidates.append(cand)

    source_text = _normalize("\n\n".join(blocks))
    return {
        "conv_id": conv_id, "title": title, "created_at": "",
        "source_text": source_text, "candidates": candidates,
        "turn_count": len([c for c in candidates if c["predicate"] == "has_turn"]),
    }


def _make_header_and_meta(conv_id: str, title: str, created_at: str, msg_count: int) -> tuple[list[str], list[dict]]:
    """Build the header block and tier-0 metadata candidates."""
    header = f"=== CONVERSATION: {title} ===\nID: {conv_id}\nStarted: {created_at}\n"
    candidates = [
        {"subject": f"conversation/{conv_id}", "predicate": "has_title",
         "object": title, "object_type": "literal:string", "tier": 0,
         "evidence": f"=== CONVERSATION: {title} ==="},
        {"subject": f"conversation/{conv_id}", "predicate": "started_at",
         "object": created_at, "object_type": "literal:string", "tier": 0,
         "evidence": f"Started: {created_at}"},
        {"subject": f"conversation/{conv_id}", "predicate": "message_count",
         "object": str(msg_count), "object_type": "literal:integer", "tier": 0,
         "evidence": header.strip()},
    ]
    return [header], candidates


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

def load_export_file(path: Path) -> tuple[list[dict], str]:
    """Load a ChatGPT or Claude export. Returns (conversations, export_type)."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            json_files = [n for n in names if n.endswith("conversations.json")]
            if not json_files:
                json_files = [n for n in names if n.endswith(".json") and "/" not in n]
            if not json_files:
                raise ValueError(f"No conversations.json found in {path.name}")
            with zf.open(json_files[0]) as f:
                data = json.load(f)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    export_type = detect_export_type(data)
    if export_type == "unknown":
        raise ValueError(f"Unknown export format in {path.name}")

    if isinstance(data, list):
        return data, export_type
    elif isinstance(data, dict) and "mapping" in data:
        return [data], export_type
    else:
        raise ValueError(f"Unexpected data shape in {path.name}")


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

def get_or_create_keypair(key_dir: Path, suite: str = SUITE) -> bytes:
    """Load or generate a keypair. Returns private key bytes."""
    key_dir.mkdir(parents=True, exist_ok=True)
    sk_path = key_dir / "publisher.sk"
    pk_path = key_dir / "publisher.pub"

    if sk_path.exists() and pk_path.exists():
        return sk_path.read_bytes()

    if suite == "axm-blake3-mldsa44":
        try:
            kp = mldsa44_keygen()
            sk_path.write_bytes(kp.secret_key)
            pk_path.write_bytes(kp.public_key)
            return kp.secret_key + kp.public_key
        except Exception:
            pass  # fall through to Ed25519

    from nacl.signing import SigningKey
    ed_sk = SigningKey.generate()
    sk_bytes = bytes(ed_sk)
    pk_bytes = bytes(ed_sk.verify_key)
    sk_path.write_bytes(sk_bytes)
    pk_path.write_bytes(pk_bytes)
    return sk_bytes


def compile_conversation_shard(
    extracted: dict,
    shard_dir: Path,
    key_dir: Path = DEFAULT_KEY_DIR,
    suite: str = SUITE,
) -> bool:
    """Compile one extracted conversation into a signed shard."""
    work_dir = shard_dir.parent / f".work_{extracted['conv_id'][:16]}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        source_bytes = extracted["source_text"].encode("utf-8")
        valid_candidates = []
        for c in extracted["candidates"]:
            ev = c.get("evidence", "")
            if not ev:
                continue
            if source_bytes.count(ev.encode("utf-8")) == 1:
                valid_candidates.append(c)

        if not valid_candidates:
            return False

        source_path = work_dir / "source.txt"
        candidates_path = work_dir / "candidates.jsonl"
        source_path.write_text(extracted["source_text"], encoding="utf-8")
        with candidates_path.open("w", encoding="utf-8") as f:
            for c in valid_candidates:
                row = {k: c[k] for k in
                       ("subject", "predicate", "object", "object_type", "evidence", "tier")
                       if k in c}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        private_key = get_or_create_keypair(key_dir, suite)
        created_at = extracted.get("created_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        cfg = CompilerConfig(
            source_path=source_path,
            candidates_path=candidates_path,
            out_dir=shard_dir,
            private_key=private_key,
            publisher_id="@axm_chat",
            publisher_name="axm-chat",
            namespace="chat/conversation",
            created_at=created_at,
            suite=suite,
        )

        return bool(compile_generic_shard(cfg))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# High-level import
# ---------------------------------------------------------------------------

def import_export(
    source: Path,
    shard_dir: Path = DEFAULT_SHARD_DIR,
    key_dir: Path = DEFAULT_KEY_DIR,
    suite: str = SUITE,
    limit: int | None = None,
    overwrite: bool = False,
) -> dict:
    """Import an export file or directory. Returns {imported, skipped, errors, log}."""
    shard_dir.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        files = sorted(f for f in source.rglob("*") if f.suffix.lower() in (".json", ".zip"))
    else:
        files = [source]

    log = []
    imported = skipped = errors = 0

    for export_file in files:
        try:
            convs, export_type = load_export_file(export_file)
        except Exception as e:
            log.append(f"✗ {export_file.name}: {e}")
            errors += 1
            continue

        log.append(f"→ {export_file.name}: {len(convs)} conversations ({export_type})")

        if limit:
            convs = convs[:limit]

        for idx, conv in enumerate(convs):
            extracted = extract_conversation(conv, idx, export_type)
            if not extracted:
                skipped += 1
                continue

            conv_id = extracted["conv_id"]
            if not overwrite:
                existing = list(shard_dir.glob(f"*{conv_id[:16]}*"))
                if existing:
                    skipped += 1
                    continue

            safe_id = re.sub(r"[^\w-]", "_", conv_id)[:48]
            safe_title = re.sub(r"[^\w\s-]", "", extracted["title"])[:30].strip().replace(" ", "_")
            shard_name = f"chat_{safe_title}_{safe_id}"
            shard_path = shard_dir / shard_name

            try:
                ok = compile_conversation_shard(extracted, shard_path, key_dir, suite)
                if ok:
                    imported += 1
                    log.append(f"  ✓ {extracted['title'][:50]} ({extracted['turn_count']} turns)")
                else:
                    errors += 1
                    log.append(f"  ✗ {extracted['title'][:50]} — compile failed")
            except Exception as e:
                errors += 1
                log.append(f"  ✗ {extracted['title'][:50]} — {e}")
                if shard_path.exists():
                    shutil.rmtree(shard_path, ignore_errors=True)

    return {"imported": imported, "skipped": skipped, "errors": errors, "log": log}
