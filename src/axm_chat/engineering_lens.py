"""
axm_chat / engineering_lens.py
==============================
Gated Pass 2 — Engineering Lens.

Only runs on episodes where Pass 1 (episodic.py) set lens_hints ∋ "engineering".

Extracts:
  - The core problem
  - The graveyard of failed attempts (often more valuable than the solution)
  - The working solution, if one was reached
  - Any architectural rule that emerged ("no LLMs at query time", etc.)

Output: ext/engineering@1.parquet in the SOURCE shard directory.
Linked back to base_episodes via episode_id (foreign key).

Query pattern:
    SELECT e.summary, eng.problem_statement, eng.solution_adopted
    FROM read_parquet('ext/episodes@1.parquet') e
    JOIN read_parquet('ext/engineering@1.parquet') eng
      ON e.episode_id = eng.episode_id
    WHERE list_contains(e.projects, 'axm-genesis')
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from axm_chat.distill import DEFAULT_OLLAMA_URL, DEFAULT_MODEL
from axm_chat.episodic import Episode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------

@dataclass
class EngineeringRecord:
    """Engineering context extracted for one episode."""
    episode_id:        str          # FK → episodes@1.episode_id
    shard_id:          str
    problem_statement: str
    core_technologies: List[str]
    failed_attempts:   List[str]    # the graveyard
    solution_adopted:  str          # "None" if unresolved — explicit, not null
    architectural_rule: str         # broad rule if one emerged, else "None"
    confidence:        float        # 0.0–1.0, self-assessed by LLM


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_ENGINEERING_SYSTEM_PROMPT = """\
You are the Technical Context Extractor.

You receive a conversation chunk that has been flagged as containing engineering
or technical problem-solving. Your job is to map the problem-solving lifecycle
in that chunk — not to summarise it.

RULES:

1. The graveyard matters most.
   If the human tried three approaches before finding one that worked, list all
   three failed attempts explicitly. The failed attempts are the most valuable
   part of this record — they prevent the same dead ends later.

2. Be specific, not generic.
   "switched to temperature=0.0 for the Ollama call" is correct.
   "fixed the code" is not.
   "used DuckDB list_contains instead of JSON path traversal" is correct.
   "improved the query" is not.

3. Null states are honest.
   If the problem was discussed but not resolved in this chunk, set
   solution_adopted to the string "None". Do not hallucinate a solution.

4. Architectural rules are rare.
   Only populate architectural_rule if a broad design principle was explicitly
   stated or strongly implied (e.g. "no LLMs at query time", "lenses are
   query-time not compile-time", "pass 1 always runs on everything").
   Otherwise, set it to "None".

5. confidence is your own honest self-assessment.
   1.0 = clear problem, clear solution, well-documented in the text.
   0.5 = problem is clear but solution is partial or implicit.
   0.2 = this chunk is engineering-adjacent but the problem/solution are vague.

Return exactly one JSON object. No markdown. No commentary. Pure JSON.\
"""

_ENGINEERING_USER_TEMPLATE = """\
episode_id (pre-assigned, copy into your response): {episode_id}

Extract the engineering context from this conversation chunk:

{conversation_text}

Return JSON matching this schema:
{{
  "episode_id":         "string — copy from above",
  "problem_statement":  "string — core technical challenge, one sentence",
  "core_technologies":  ["string — specific names only"],
  "failed_attempts":    ["string — each failed approach, specific"],
  "solution_adopted":   "string — the fix, or the string None if unresolved",
  "architectural_rule": "string — broad design rule if any, or the string None",
  "confidence":         0.0
}}\
"""

_ENGINEERING_CORRECTION_PROMPT = """\
Your previous response did not match the required JSON schema.

Return exactly one valid JSON object using only the keys above.
No markdown fences. No commentary. Pure JSON only.\
"""


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------

def _ollama_chat(
    system: str,
    user: str,
    model: str,
    base_url: str,
    timeout: int = 120,
) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
            "num_predict": 1024,
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
            return data.get("message", {}).get("content", "")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Ollama not reachable at {base_url}: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Ollama chat failed: {e}") from e


def _parse_response(raw: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip())
    text = re.sub(r"\n?```\s*$", "", text).strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def _coerce_record(raw: Dict[str, Any], episode: Episode) -> EngineeringRecord:
    def _str_list(val: Any) -> List[str]:
        if not isinstance(val, list):
            return []
        return [s.strip() for s in val if isinstance(s, str) and s.strip()]

    confidence = raw.get("confidence", 0.5)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5

    return EngineeringRecord(
        episode_id        = episode.episode_id,
        shard_id          = episode.shard_id,
        problem_statement = str(raw.get("problem_statement", "")).strip() or "unknown",
        core_technologies = _str_list(raw.get("core_technologies", [])),
        failed_attempts   = _str_list(raw.get("failed_attempts",   [])),
        solution_adopted  = str(raw.get("solution_adopted",  "None")).strip() or "None",
        architectural_rule= str(raw.get("architectural_rule","None")).strip() or "None",
        confidence        = confidence,
    )


# ---------------------------------------------------------------------------
# Per-episode extraction
# ---------------------------------------------------------------------------

def _extract_one(
    episode: Episode,
    conversation_text: str,
    model: str,
    base_url: str,
) -> Optional[EngineeringRecord]:
    user = _ENGINEERING_USER_TEMPLATE.format(
        episode_id=episode.episode_id,
        conversation_text=conversation_text,
    )
    raw_text = _ollama_chat(_ENGINEERING_SYSTEM_PROMPT, user, model, base_url)
    result = _parse_response(raw_text)

    if result is None:
        # Retry with correction prompt
        correction = f"{_ENGINEERING_CORRECTION_PROMPT}\n\nOriginal request:\n{user}"
        raw_text2 = _ollama_chat(_ENGINEERING_SYSTEM_PROMPT, correction, model, base_url)
        result = _parse_response(raw_text2)

    if result is None:
        return None

    return _coerce_record(result, episode)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_engineering_lens(
    episodes: List[Episode],
    batches: List[List[Dict[str, str]]],
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    on_progress: Any = None,
) -> List[EngineeringRecord]:
    """
    Run the engineering deep-extraction pass on flagged episodes.

    Args:
        episodes:   Episodes with lens_hints ∋ "engineering" (already filtered).
        batches:    The same turn-batches used in the episodic pass.
                    episodes[i].batch_index indexes into batches[i].
                    Same chunks → episode_id alignment is exact.
        model:      Ollama model name.
        base_url:   Ollama server URL.
        on_progress: Optional callback(done, total, error).

    Returns:
        List of EngineeringRecord — one per successfully extracted episode.
    """
    from axm_chat.distill import _format_batch_for_prompt

    records: List[EngineeringRecord] = []

    for i, ep in enumerate(episodes):
        if ep.batch_index >= len(batches):
            logger.warning(f"batch_index {ep.batch_index} out of range for {ep.episode_id}")
            continue

        conversation_text = _format_batch_for_prompt(batches[ep.batch_index])

        try:
            rec = _extract_one(ep, conversation_text, model, base_url)
        except Exception as e:
            logger.error(f"Engineering lens failed for {ep.episode_id}: {e}")
            if on_progress:
                on_progress(i + 1, len(episodes), str(e))
            continue

        if rec is not None:
            records.append(rec)

        if on_progress:
            on_progress(i + 1, len(episodes), None)

    return records


def engineering_records_to_rows(records: List[EngineeringRecord]) -> List[Dict[str, Any]]:
    """Flat dicts for Parquet serialisation. Arrays stay as Python lists."""
    return [
        {
            "episode_id":         r.episode_id,
            "shard_id":           r.shard_id,
            "problem_statement":  r.problem_statement,
            "core_technologies":  r.core_technologies,
            "failed_attempts":    r.failed_attempts,
            "solution_adopted":   r.solution_adopted,
            "architectural_rule": r.architectural_rule,
            "confidence":         r.confidence,
        }
        for r in records
    ]


def write_engineering_parquet(records: List[EngineeringRecord], ext_dir: Path) -> Path:
    """
    Write ext/engineering@1.parquet into the shard's ext directory.
    Returns the path written.
    """
    import duckdb

    rows = engineering_records_to_rows(records)
    if not rows:
        raise ValueError("No engineering records to write")

    out_path = ext_dir / "engineering@1.parquet"
    ext_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE eng (
            episode_id         VARCHAR,
            shard_id           VARCHAR,
            problem_statement  VARCHAR,
            core_technologies  VARCHAR[],
            failed_attempts    VARCHAR[],
            solution_adopted   VARCHAR,
            architectural_rule VARCHAR,
            confidence         FLOAT
        )
    """)
    for row in rows:
        con.execute(
            "INSERT INTO eng VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row["episode_id"],
                row["shard_id"],
                row["problem_statement"],
                row["core_technologies"],
                row["failed_attempts"],
                row["solution_adopted"],
                row["architectural_rule"],
                row["confidence"],
            ],
        )
    con.execute(f"COPY eng TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()
    return out_path
