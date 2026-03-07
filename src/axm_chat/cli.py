"""
axm_chat.cli — Command-line interface for the chat spoke.

Entry points:
    axm-chat import ./conversations.json
    axm-chat distill [--shard NAME] [--model mistral] [--dry-run]
    axm-chat query "what did we decide about blake3"
    axm-chat list
    axm-chat verify [SHARD_ID]

Also registers as an axm.spokes plugin so `axm chat ...` works
when axm-core discovers installed spokes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from axm_chat.spoke import (
    DEFAULT_SHARD_DIR,
    DEFAULT_KEY_DIR,
    SUITE,
    import_export,
)

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _c(code: str, msg: str) -> str:
    return f"\033[{code}m{msg}\033[0m"

def info(msg: str) -> None: click.echo(_c("37", msg))
def ok(msg: str) -> None: click.echo(_c("32", msg))
def warn(msg: str) -> None: click.echo(_c("33", msg))
def err(msg: str) -> None: click.echo(_c("31", msg), err=True)
def dim(msg: str) -> None: click.echo(_c("90", msg))
def head(msg: str) -> None: click.echo(_c("36", msg))


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def chat_group():
    """axm chat — turn conversation exports into queryable knowledge."""
    pass


# Standalone entry point (axm-chat)
main = chat_group


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@chat_group.command("import")
@click.argument("source", type=click.Path(exists=True))
@click.option("--out", default=None, help="Output shard directory")
@click.option("--suite", default=SUITE, type=click.Choice(["ed25519", "axm-blake3-mldsa44"]))
@click.option("--limit", default=None, type=int, help="Only import first N conversations")
@click.option("--overwrite", is_flag=True, help="Re-import existing conversations")
def cmd_import(source: str, out: str | None, suite: str, limit: int | None, overwrite: bool):
    """Import a ChatGPT or Claude export file (or directory).

    SOURCE can be conversations.json, a .zip export, or a directory.
    """
    shard_dir = Path(out) if out else DEFAULT_SHARD_DIR

    result = import_export(
        source=Path(source),
        shard_dir=shard_dir,
        suite=suite,
        limit=limit,
        overwrite=overwrite,
    )

    for line in result["log"]:
        if "✓" in line:
            ok(line)
        elif "✗" in line:
            err(line)
        elif "→" in line:
            head(line)
        else:
            dim(line)

    head(f"\n{'=' * 50}")
    ok(f"  Imported: {result['imported']}")
    if result["skipped"]:
        dim(f"  Skipped:  {result['skipped']}")
    if result["errors"]:
        warn(f"  Errors:   {result['errors']}")
    info(f"  Shards:   {shard_dir}")


# ---------------------------------------------------------------------------
# distill
# ---------------------------------------------------------------------------

@chat_group.command("distill")
@click.option("--shard", default=None, help="Shard directory name or prefix")
@click.option("--shards", default=None, help="Shard directory (default: ~/.axm/shards/)")
@click.option("--model", default="mistral", help="Ollama model name")
@click.option("--ollama-url", default="http://localhost:11434", help="Ollama server URL")
@click.option("--suite", default=SUITE, type=click.Choice(["ed25519", "axm-blake3-mldsa44"]))
@click.option("--dry-run", is_flag=True, help="Extract decisions but don't compile")
def cmd_distill(shard: str | None, shards: str | None, model: str,
                ollama_url: str, suite: str, dry_run: bool):
    """Distill conversation shards into decision shards.

    Requires Ollama: ollama serve && ollama pull mistral
    """
    from axm_chat.distill import distill_shard

    shard_dir = Path(shards) if shards else DEFAULT_SHARD_DIR

    if not shard_dir.exists():
        err(f"No shards directory at {shard_dir}")
        sys.exit(1)

    # Find target shards
    if shard:
        targets = sorted(
            p for p in shard_dir.iterdir()
            if p.is_dir() and (p / "manifest.json").exists() and p.name.startswith(shard)
        )
        if not targets:
            err(f"No shard matching '{shard}'")
            sys.exit(1)
    else:
        targets = sorted(
            p for p in shard_dir.iterdir()
            if p.is_dir() and (p / "manifest.json").exists()
            and not p.name.startswith("decisions_")
        )

    if not targets:
        err("No conversation shards found to distill.")
        sys.exit(1)

    head(f"\nDistilling {len(targets)} shard(s) with model={model} "
         f"({'dry run' if dry_run else 'compile'})\n")

    total_ok = total_err = total_empty = 0

    for sp in targets:
        try:
            manifest = json.loads((sp / "manifest.json").read_text())
            title = manifest.get("metadata", {}).get("title", sp.name)[:50]
        except Exception:
            title = sp.name

        info(f"  → {title}")

        result = distill_shard(
            shard_path=sp,
            output_base=shard_dir,
            model=model,
            base_url=ollama_url,
            key_dir=DEFAULT_KEY_DIR,
            suite=suite,
            dry_run=dry_run,
        )

        if result.status == "ok":
            ok(f"    ✓ {len(result.decisions)} decisions → {result.decision_shard_path.name}")
            total_ok += 1
        elif result.status == "dry_run":
            ok(f"    ✓ {len(result.decisions)} decisions found (dry run)")
            for d in result.decisions[:5]:
                dim(f"      {d.predicate}: {d.subject} → {d.object}")
            if len(result.decisions) > 5:
                dim(f"      ... and {len(result.decisions) - 5} more")
            total_ok += 1
        elif result.status == "empty":
            dim(f"    — no decisions found")
            total_empty += 1
        else:
            err(f"    ✗ {result.error}")
            total_err += 1

    head(f"\n{'=' * 50}")
    ok(f"  Distilled: {total_ok}")
    if total_empty:
        dim(f"  Empty:     {total_empty}")
    if total_err:
        warn(f"  Errors:    {total_err}")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

@chat_group.command("query")
@click.argument("question", required=False)
@click.option("--sql", default=None, help="Raw SQL query")
@click.option("--shards", default=None, help="Shard directory")
@click.option("--json-out", is_flag=True, help="Output as JSON")
def cmd_query(question: str | None, sql: str | None, shards: str | None, json_out: bool):
    """Query across all shards. Plain English or raw SQL."""
    try:
        from axiom_runtime.engine import SpectraEngine
        from axiom_runtime.nlquery import natural_language_to_sql
    except ImportError:
        err("Spectra (axm-core) not found. Install it:")
        err("  pip install -e ./axm-core")
        err("or set PYTHONPATH to include axm-core/spectra/")
        sys.exit(1)

    shard_dir = Path(shards) if shards else DEFAULT_SHARD_DIR

    if not shard_dir.exists() or not any(shard_dir.iterdir()):
        err(f"No shards found in {shard_dir}")
        sys.exit(1)

    engine = SpectraEngine()

    shard_paths = sorted(
        p for p in shard_dir.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )
    mounted = 0
    for sp in shard_paths:
        try:
            engine.mount(str(sp), None, verify=False)
            mounted += 1
        except Exception as e:
            warn(f"  Could not mount {sp.name}: {e}")

    if not mounted:
        err("No shards could be mounted.")
        sys.exit(1)

    dim(f"Mounted {mounted} shard(s)")

    if sql:
        query_sql = sql
    elif question:
        query_sql = natural_language_to_sql(question)
        dim(f"SQL: {query_sql.strip()}")
    else:
        query_sql = """
            SELECT object AS title, subject AS conversation_id, shard_id
            FROM claims WHERE predicate = 'has_title'
            ORDER BY title
        """

    try:
        result = engine.query_json(query_sql)
    except Exception as e:
        err(f"Query failed: {e}")
        sys.exit(1)

    cols = result.get("columns", [])
    rows = result.get("rows", [])

    if not rows:
        info("No results.")
        return

    if json_out:
        click.echo(json.dumps([dict(zip(cols, r)) for r in rows], indent=2, default=str))
    else:
        col_widths = [max(len(str(c)), max((len(str(r[i])) for r in rows), default=0))
                      for i, c in enumerate(cols)]
        col_widths = [min(w, 60) for w in col_widths]
        header = "  ".join(str(c).ljust(w) for c, w in zip(cols, col_widths))
        sep = "  ".join("-" * w for w in col_widths)
        click.echo()
        click.echo(header)
        click.echo(sep)
        for row in rows[:50]:
            click.echo("  ".join(str(row[i] or "")[:w].ljust(w) for i, w in enumerate(col_widths)))
        click.echo()
        dim(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@chat_group.command("list")
@click.option("--shards", default=None, help="Shard directory")
def cmd_list(shards: str | None):
    """List all imported conversations."""
    shard_dir = Path(shards) if shards else DEFAULT_SHARD_DIR

    if not shard_dir.exists():
        info(f"No shards directory at {shard_dir}")
        return

    shard_dirs = sorted(
        p for p in shard_dir.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )

    if not shard_dirs:
        info("No shards imported yet.")
        info("Run: axm-chat import ./conversations.json")
        return

    head(f"\n{len(shard_dirs)} shard(s):")
    click.echo()

    for s in shard_dirs:
        try:
            manifest = json.loads((s / "manifest.json").read_text())
            meta = manifest.get("metadata", {})
            stats = manifest.get("statistics", {})
            title = meta.get("title", s.name)
            created = manifest.get("created_at", "")[:10]
            claims = stats.get("claims", "?")
            merkle = manifest.get("integrity", {}).get("merkle_root", "")[:16]
            is_decision = "◇" if meta.get("source_shard") else " "
            ok(f"  {is_decision} {title[:50]:<52}  {created}  {claims:>4} claims  {merkle}…")
        except Exception:
            warn(f"  {s.name}  (unreadable)")

    click.echo()
    info(f"Shards: {shard_dir}")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

@chat_group.command("verify")
@click.argument("shard_id", required=False)
@click.option("--shards", default=None, help="Shard directory")
def cmd_verify(shard_id: str | None, shards: str | None):
    """Verify shard integrity (Merkle root + signature)."""
    from axm_verify.logic import verify_shard

    shard_dir = Path(shards) if shards else DEFAULT_SHARD_DIR
    candidates = sorted(
        p for p in shard_dir.iterdir()
        if p.is_dir() and (p / "manifest.json").exists()
    )

    if shard_id:
        candidates = [p for p in candidates if p.name.startswith(shard_id)]
        if not candidates:
            err(f"No shard matching '{shard_id}'")
            sys.exit(1)

    all_pass = True
    for s in candidates:
        trusted_key = s / "sig" / "publisher.pub"
        try:
            result = verify_shard(s, trusted_key_path=trusted_key)
            manifest = json.loads((s / "manifest.json").read_text())
            title = manifest.get("metadata", {}).get("title", s.name)
            if result.get("status") == "PASS":
                ok(f"  ✓ PASS  {title[:60]}")
            else:
                err(f"  ✗ FAIL  {title[:60]}")
                for e in result.get("errors", []):
                    err(f"         {e}")
                all_pass = False
        except Exception as e:
            err(f"  ✗ ERROR {s.name}: {e}")
            all_pass = False

    sys.exit(0 if all_pass else 1)
