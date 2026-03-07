"""
axm_chat — AXM chat spoke.

Turns Claude, ChatGPT, and Gemini conversation exports into
cryptographically signed, queryable knowledge shards.

Two-pass pipeline:
  1. import: export.json → conversation shard (deterministic, no LLM)
  2. distill: conversation shard → decision shard (LLM via Ollama)

Query via Spectra (axm-core) or standalone DuckDB fallback.
"""
__version__ = "0.1.0"

# Re-export spoke's public API — required by axm_server.py which does
# `from axm_chat import load_export_file` etc.
from axm_chat.spoke import (  # noqa: E402
    SUITE,
    detect_export_type,
    extract_conversation,
    load_export_file,
    compile_conversation_shard,
    import_export,
    get_or_create_keypair as _get_or_create_keypair,
)

__all__ = [
    "SUITE", "detect_export_type", "extract_conversation",
    "load_export_file", "compile_conversation_shard",
    "import_export", "_get_or_create_keypair",
]
