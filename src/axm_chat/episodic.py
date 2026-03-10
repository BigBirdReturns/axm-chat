"""
axm_chat / episodic.py
======================
Universal Base Pass: conversation shard → episodic index.

Every conversation gets this pass regardless of content.
Extracts flat, queryable semantic metadata for the personal knowledge archive.

The episodic index answers:
    - What was this conversation about?
    - Who and what were involved?
    - What did I ask? What was left unresolved?
    - How did it feel?
    - Which specialized lenses should enrich this later?

This is pass 1. It runs on everything.
Engineering / audit / reflect lenses are pass 2, gated by lens_hints.

Output: ext/episodes@1.parquet — one row per episode per shard.
"""
from __future__ import annotations

import json
import re
import hashlib
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse Ollama wiring from distill.py
from axm_chat.distill import (
    DEFAULT_OLLAMA_URL,
    DEFAULT_MODEL,
    _ollama_available,
    _batch_turns,
    _extract_turns_from_source,
    _format_batch_for_prompt,
)


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

VALID_STATES      = frozenset({"resolved", "unresolved", "abandoned", "ongoing"})
VALID_TONES       = frozenset({"positive", "neutral", "negative", "stressed", "relieved", "mixed"})
VALID_LENS_HINTS  = frozenset({"engineering", "audit", "reflect", "general"})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Episode:
    """One episodic unit extracted from a conversation batch."""
    episode_id:          str
    shard_id:            str
    batch_index:         int
    episode_index:       int          # position within batch (usually 0, episodes are per-batch)
    timestamp:           str          # ISO 8601, from export metadata — NOT from LLM
    topic_tags:          List[str]
    people:              List[str]
    animals:             List[str]
    tools_places_services: List[str]
    projects:            List[str]
    question_text:       Optional[str]
    state:               str          # enum: resolved | unresolved | abandoned | ongoing
    tone:                str          # enum: positive | neutral | negative | stressed | relieved | mixed
    summary:             str
    lens_hints:          List[str]    # subset of: engineering | audit | reflect | general


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# System prompt: establishes the indexer role and non-negotiable rules.
# Kept separate so it can be versioned independently of the user template.
EPISODIC_SYSTEM_PROMPT = """\
You are an indexing engine, not a summarizer.

Your job is to extract structured episode metadata from a human-AI conversation
so the conversation can be retrieved later from a local personal knowledge archive.

RULES — follow exactly:

1. Index everything with equal rigor.
   A conversation about a dog's dental appointment is indexed with the same
   precision as a conversation about cryptographic protocol design.
   Named entities are the primary retrieval seeds. Do not omit them because
   they seem trivial or personal.

2. Extract only what is explicitly present in the text.
   Do not infer hidden motives, emotional diagnoses, or unstated context.
   If something is not written, it does not exist for this pass.

3. Return exactly one JSON object per call.
   Use only the keys listed in the schema. No extra keys.
   No markdown fences. No preamble. No commentary. Pure JSON.

4. Field rules:
   - topic_tags: 3 to 5 short noun phrases. Specific, not generic.
     "Docker port mapping" is correct. "software problem" is not.
   - people: named humans only. No generic roles like "the user" or "a developer".
   - animals: named animals or pets only. "Fig" is correct. "a dog" is not.
   - tools_places_services: software names, service names, business names,
     physical locations. Named and specific.
   - projects: named projects, repositories, codebases, formal initiatives.
   - question_text: if the human asked one clear primary question,
     copy it verbatim. Otherwise return null.
   - state: one of exactly: resolved, unresolved, abandoned, ongoing
   - tone: one of exactly: positive, neutral, negative, stressed, relieved, mixed
   - summary: one sentence. Grounded and literal. No drama. No inference.
   - lens_hints: array, may contain multiple values from:
     engineering, audit, reflect, general
     Use "engineering" if technical problem-solving occurred.
     Use "audit" if professional commitments or decisions were made.
     Use "reflect" if the human processed emotions or personal situations.
     Use "general" for everyday life, logistics, or mixed content.

5. Empty arrays are correct.
   If no animals are named, return []. Do not return null for arrays.\
"""

# User-turn template. episode_id is injected by Python (deterministic).
# The LLM sees it but does not generate it — purely context for the model.
EPISODIC_USER_TEMPLATE = """\
Extract one episode record from the conversation text below.

episode_id (pre-assigned, copy into your response): {episode_id}

Conversation text:
{conversation_text}

Return only JSON matching this schema:
{{
  "episode_id":             "string",
  "topic_tags":             ["string"],
  "people":                 ["string"],
  "animals":                ["string"],
  "tools_places_services":  ["string"],
  "projects":               ["string"],
  "question_text":          "string or null",
  "state":                  "resolved | unresolved | abandoned | ongoing",
  "tone":                   "positive | neutral | negative | stressed | relieved | mixed",
  "summary":                "string",
  "lens_hints":             ["engineering | audit | reflect | general"]
}}\
"""


# ---------------------------------------------------------------------------
# Ollama call — chat endpoint (system + user, temperature 0)
# ---------------------------------------------------------------------------

def _ollama_chat(
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout: int = 120,
) -> str:
    """
    Call Ollama's /api/chat endpoint with a system + user message pair.
    Returns raw text response.

    Uses temperature=0.0 for deterministic extraction.
    Same text → same index, every time.
    """
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system",  "content": system},
            {"role": "user",    "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
            "num_predict": 1024,  # episodes are compact; 1k tokens is enough
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # /api/chat wraps the assistant reply in message.content
            return data.get("message", {}).get("content", "")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Ollama not reachable at {base_url}: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Ollama chat failed: {e}") from e


# ---------------------------------------------------------------------------
# Response parsing and validation
# ---------------------------------------------------------------------------

def _parse_episode_response(raw: str) -> Optional[Dict[str, Any]]:
    """
    Parse and lightly validate the LLM's JSON response.
    Returns None if the response cannot be salvaged.
    """
    text = raw.strip()
    # Strip markdown fences if the model ignored instructions
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    if not text:
        return None

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object in the response
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    if not isinstance(obj, dict):
        return None

    return obj


def _coerce_episode(raw: Dict[str, Any], episode_id: str, shard_id: str,
                    batch_index: int, episode_index: int, timestamp: str) -> Episode:
    """
    Coerce a raw LLM dict into a validated Episode.
    Fills defaults, normalises enums, deduplicates arrays.
    Never raises — always returns something usable.
    """
    def _str_list(val: Any) -> List[str]:
        if not isinstance(val, list):
            return []
        return list(dict.fromkeys(            # deduplicate, preserve order
            s.strip() for s in val
            if isinstance(s, str) and s.strip()
        ))

    state = raw.get("state", "unresolved")
    if state not in VALID_STATES:
        state = "unresolved"

    tone = raw.get("tone", "neutral")
    if tone not in VALID_TONES:
        tone = "neutral"

    lens_hints = [
        h.lower().strip() for h in _str_list(raw.get("lens_hints", []))
        if h.lower().strip() in VALID_LENS_HINTS
    ]
    if not lens_hints:
        lens_hints = ["general"]

    question_text = raw.get("question_text")
    if not isinstance(question_text, str) or not question_text.strip():
        question_text = None

    summary = raw.get("summary", "")
    if not isinstance(summary, str):
        summary = ""
    summary = summary.strip()

    return Episode(
        episode_id            = episode_id,
        shard_id              = shard_id,
        batch_index           = batch_index,
        episode_index         = episode_index,
        timestamp             = timestamp,
        topic_tags            = _str_list(raw.get("topic_tags",            [])),
        people                = _str_list(raw.get("people",                [])),
        animals               = _str_list(raw.get("animals",               [])),
        tools_places_services = _str_list(raw.get("tools_places_services", [])),
        projects              = _str_list(raw.get("projects",              [])),
        question_text         = question_text,
        state                 = state,
        tone                  = tone,
        summary               = summary,
        lens_hints            = lens_hints,
    )


# ---------------------------------------------------------------------------
# Retry with correction prompt
# ---------------------------------------------------------------------------

_CORRECTION_PROMPT = """\
Your previous response did not match the required JSON schema.

Return exactly one valid JSON object.
Use only the keys specified. No markdown. No commentary. Pure JSON only.\
"""

def _ollama_chat_with_retry(
    episode_id: str,
    conversation_text: str,
    model: str,
    base_url: str,
) -> Optional[Dict[str, Any]]:
    """
    Attempt extraction, retry once with a correction prompt if parsing fails.
    Returns the raw dict or None.
    """
    user_prompt = EPISODIC_USER_TEMPLATE.format(
        episode_id=episode_id,
        conversation_text=conversation_text,
    )

    raw = _ollama_chat(EPISODIC_SYSTEM_PROMPT, user_prompt, model=model, base_url=base_url)
    result = _parse_episode_response(raw)

    if result is not None:
        return result

    # One retry — give the model the correction prompt and the original user turn
    correction_user = f"{_CORRECTION_PROMPT}\n\nOriginal request:\n{user_prompt}"
    raw2 = _ollama_chat(EPISODIC_SYSTEM_PROMPT, correction_user, model=model, base_url=base_url)
    return _parse_episode_response(raw2)


# ---------------------------------------------------------------------------
# Episode ID generation
# ---------------------------------------------------------------------------

def _make_episode_id(shard_id: str, batch_index: int, episode_index: int) -> str:
    """
    Deterministic episode ID.
    Format: ep_{first8ofshard}_{batch:04d}_{ep:02d}
    Short enough to be readable, unique enough for a personal archive.
    """
    shard_short = hashlib.blake2b(
        shard_id.encode(), digest_size=4
    ).hexdigest()
    return f"ep_{shard_short}_{batch_index:04d}_{episode_index:02d}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_episodes(
    source_text: str,
    shard_id: str,
    shard_timestamp: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    on_progress: Any = None,
) -> List[Episode]:
    """
    Universal base pass: extract episodic metadata from a conversation shard.

    Args:
        source_text:      Full source.txt from the conversation shard.
        shard_id:         Shard identifier (used for episode_id generation).
        shard_timestamp:  ISO 8601 timestamp from shard manifest (deterministic).
        model:            Ollama model name.
        base_url:         Ollama server URL.
        on_progress:      Optional callback(batch_num, total, episodes_so_far, error).

    Returns:
        List of Episode objects, one per conversation batch.
    """
    turns = _extract_turns_from_source(source_text)
    if not turns:
        return []

    batches = _batch_turns(turns)
    episodes: List[Episode] = []

    for batch_idx, batch in enumerate(batches):
        episode_index = 0  # one episode per batch in base pass
        episode_id = _make_episode_id(shard_id, batch_idx, episode_index)
        conversation_text = _format_batch_for_prompt(batch)

        try:
            raw = _ollama_chat_with_retry(
                episode_id=episode_id,
                conversation_text=conversation_text,
                model=model,
                base_url=base_url,
            )
        except Exception as e:
            if on_progress:
                on_progress(batch_idx + 1, len(batches), len(episodes), str(e))
            continue

        if raw is None:
            if on_progress:
                on_progress(batch_idx + 1, len(batches), len(episodes),
                            "parse failed after retry")
            continue

        ep = _coerce_episode(
            raw=raw,
            episode_id=episode_id,
            shard_id=shard_id,
            batch_index=batch_idx,
            episode_index=episode_index,
            timestamp=shard_timestamp,
        )
        episodes.append(ep)

        if on_progress:
            on_progress(batch_idx + 1, len(batches), len(episodes), None)

    return episodes


def episodes_to_records(episodes: List[Episode]) -> List[Dict[str, Any]]:
    """
    Convert Episode dataclasses to flat dicts for Parquet/DataFrame serialisation.

    Arrays are kept as Python lists — Parquet LIST columns, DuckDB list_contains().
    """
    return [
        {
            "episode_id":             ep.episode_id,
            "shard_id":               ep.shard_id,
            "batch_index":            ep.batch_index,
            "timestamp":              ep.timestamp,
            "topic_tags":             ep.topic_tags,
            "people":                 ep.people,
            "animals":                ep.animals,
            "tools_places_services":  ep.tools_places_services,
            "projects":               ep.projects,
            "question_text":          ep.question_text,
            "state":                  ep.state,
            "tone":                   ep.tone,
            "summary":                ep.summary,
            "lens_hints":             ep.lens_hints,
        }
        for ep in episodes
    ]


def has_lens_hint(episode: Episode, lens: str) -> bool:
    """Check if an episode carries a specific lens hint. Used by gated pass 2."""
    return lens in episode.lens_hints


def episodes_needing_lens(episodes: List[Episode], lens: str) -> List[Episode]:
    """Filter episodes that should receive a specific deep lens pass."""
    return [ep for ep in episodes if has_lens_hint(ep, lens)]
