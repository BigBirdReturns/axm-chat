"""
Tests for axm_chat.spoke — format detection and extraction.

These tests verify the chat spoke's ability to detect export formats
and extract conversations without requiring Genesis or any LLM.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

class TestDetectExportType:
    """Test export format detection for Claude, ChatGPT, and generic."""

    def test_claude_format(self):
        from axm_chat.spoke import detect_export_type
        data = [{"uuid": "abc-123", "name": "Test Conv", "chat_messages": []}]
        assert detect_export_type(data) == "claude"

    def test_claude_format_alt(self):
        from axm_chat.spoke import detect_export_type
        data = [{"uuid": "abc-123", "name": "Test Conv"}]
        assert detect_export_type(data) == "claude"

    def test_chatgpt_format_list(self):
        from axm_chat.spoke import detect_export_type
        data = [{"id": "conv-1", "title": "Test", "mapping": {}}]
        assert detect_export_type(data) == "chatgpt"

    def test_chatgpt_format_single(self):
        from axm_chat.spoke import detect_export_type
        data = {"mapping": {"node-1": {}}}
        assert detect_export_type(data) == "chatgpt"

    def test_generic_format(self):
        from axm_chat.spoke import detect_export_type
        data = [{"role": "user", "content": "hello"}]
        assert detect_export_type(data) == "generic"

    def test_unknown_format(self):
        from axm_chat.spoke import detect_export_type
        assert detect_export_type([]) == "unknown"
        assert detect_export_type({"random": "data"}) == "unknown"
        assert detect_export_type("not a list or dict") == "unknown"

    def test_empty_list(self):
        from axm_chat.spoke import detect_export_type
        assert detect_export_type([]) == "unknown"


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

class TestNormalize:
    """Test text normalization (NFC, line endings)."""

    def test_crlf_to_lf(self):
        from axm_chat.spoke import _normalize
        assert _normalize("hello\r\nworld") == "hello\nworld"

    def test_cr_to_lf(self):
        from axm_chat.spoke import _normalize
        assert _normalize("hello\rworld") == "hello\nworld"

    def test_nfc_normalization(self):
        from axm_chat.spoke import _normalize
        # e + combining accent vs precomposed e-accent
        import unicodedata
        decomposed = "e\u0301"  # e + combining acute
        result = _normalize(decomposed)
        assert result == "\u00e9"  # precomposed


# ---------------------------------------------------------------------------
# ISO timestamp
# ---------------------------------------------------------------------------

class TestIso:
    """Test timestamp normalization."""

    def test_string_passthrough(self):
        from axm_chat.spoke import _iso
        assert _iso("2026-01-15T10:00:00Z") == "2026-01-15T10:00:00Z"

    def test_unix_timestamp(self):
        from axm_chat.spoke import _iso
        result = _iso(1700000000)
        assert result.startswith("2023-11-")
        assert result.endswith("Z")

    def test_none(self):
        from axm_chat.spoke import _iso
        assert _iso(None) == ""

    def test_empty_string(self):
        from axm_chat.spoke import _iso
        assert _iso("") == ""


# ---------------------------------------------------------------------------
# Claude conversation extraction
# ---------------------------------------------------------------------------

class TestExtractClaude:
    """Test Claude export extraction."""

    def test_basic_claude_conversation(self):
        from axm_chat.spoke import extract_conversation
        conv = {
            "uuid": "test-uuid-123",
            "name": "Test Conversation",
            "created_at": "2026-01-15T10:00:00Z",
            "chat_messages": [
                {
                    "sender": "human",
                    "created_at": "2026-01-15T10:00:00Z",
                    "text": "Hello, what is AXM?",
                },
                {
                    "sender": "assistant",
                    "created_at": "2026-01-15T10:00:01Z",
                    "text": "AXM is a cryptographic knowledge protocol.",
                },
            ],
        }
        result = extract_conversation(conv, 0, "claude")
        assert result is not None
        assert result["title"] == "Test Conversation"
        assert result["msg_count"] == 2
        assert len(result["candidates"]) > 0  # at least tier 0 claims

    def test_empty_messages(self):
        from axm_chat.spoke import extract_conversation
        conv = {
            "uuid": "test-empty",
            "name": "Empty",
            "created_at": "2026-01-15T10:00:00Z",
            "chat_messages": [],
        }
        result = extract_conversation(conv, 0, "claude")
        # Should either return None or have zero messages
        if result is not None:
            assert result["msg_count"] == 0


# ---------------------------------------------------------------------------
# Server endpoint smoke tests (no server needed, just import check)
# ---------------------------------------------------------------------------

class TestServerImports:
    """Verify server module can be parsed without import errors."""

    def test_server_syntax(self):
        """Server file should be valid Python."""
        import ast
        server_path = Path(__file__).parent.parent / "server" / "axm_server.py"
        if server_path.exists():
            source = server_path.read_text()
            ast.parse(source)  # raises SyntaxError if invalid
