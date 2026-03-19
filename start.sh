#!/usr/bin/env bash
set -e

# ── AXM — start.sh ────────────────────────────────────────────────────────────
# Cold-start setup and server launch.
# Run this once from inside the axm-chat directory.
# After that: just `bash start.sh` to start the server.
# ──────────────────────────────────────────────────────────────────────────────

BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

OK="${GREEN}✓${RESET}"
FAIL="${RED}✗${RESET}"
WARN="${YELLOW}!${RESET}"

echo ""
echo -e "${BOLD}AXM  start.sh${RESET}"
echo "────────────────────────────────────────────────"
echo ""

# ── 1. Python version ─────────────────────────────────────────────────────────
echo -e "  checking python..."
PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
  echo -e "  $FAIL python not found"
  echo -e "  ${DIM}→ install Python 3.10+ from https://python.org${RESET}"
  exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
  echo -e "  $FAIL python $PY_VERSION — need 3.10+"
  echo -e "  ${DIM}→ install Python 3.10+ from https://python.org${RESET}"
  exit 1
fi
echo -e "  $OK python $PY_VERSION"

# ── 2. Locate repos ───────────────────────────────────────────────────────────
echo ""
echo -e "  locating repos..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Try: sibling dirs, or subdirs of SCRIPT_DIR's parent
find_repo() {
  local name="$1"
  local candidates=(
    "$SCRIPT_DIR/../$name"
    "$SCRIPT_DIR/$name"
    "$(pwd)/../$name"
    "$(pwd)/$name"
  )
  for c in "${candidates[@]}"; do
    if [ -f "$c/pyproject.toml" ]; then
      echo "$(cd "$c" && pwd)"
      return 0
    fi
  done
  return 1
}

GENESIS_DIR=$(find_repo "axm-genesis") || true
CORE_DIR=$(find_repo "axm-core") || true
CHAT_DIR=$(find_repo "axm-chat") || true

if [ -z "$GENESIS_DIR" ]; then
  echo -e "  $FAIL axm-genesis not found"
  echo -e "  ${DIM}→ git clone https://github.com/BigBirdReturns/axm-genesis${RESET}"
  exit 1
fi
if [ -z "$CORE_DIR" ]; then
  echo -e "  $FAIL axm-core not found"
  echo -e "  ${DIM}→ git clone https://github.com/BigBirdReturns/axm-core${RESET}"
  exit 1
fi
if [ -z "$CHAT_DIR" ]; then
  echo -e "  $FAIL axm-chat not found"
  echo -e "  ${DIM}→ git clone https://github.com/BigBirdReturns/axm-chat${RESET}"
  exit 1
fi

echo -e "  $OK axm-genesis  ${DIM}$GENESIS_DIR${RESET}"
echo -e "  $OK axm-core     ${DIM}$CORE_DIR${RESET}"
echo -e "  $OK axm-chat     ${DIM}$CHAT_DIR${RESET}"

# ── 3. Install packages ───────────────────────────────────────────────────────
echo ""
echo -e "  installing packages..."

install_if_needed() {
  local name="$1"
  local dir="$2"
  if $PYTHON -c "import importlib.util; exit(0 if importlib.util.find_spec('$(echo $name | tr - _)') else 1)" 2>/dev/null; then
    echo -e "  $OK $name ${DIM}(already installed)${RESET}"
  else
    echo -e "  ${DIM}  installing $name...${RESET}"
    pip install -e "$dir" -q
    echo -e "  $OK $name"
  fi
}

# Install server extras for axm-chat
install_genesis() {
  if $PYTHON -c "import axm_build" 2>/dev/null; then
    echo -e "  $OK axm-genesis ${DIM}(already installed)${RESET}"
  else
    echo -e "  ${DIM}  installing axm-genesis...${RESET}"
    pip install -e "$GENESIS_DIR" -q
    echo -e "  $OK axm-genesis"
  fi
}

install_core() {
  if $PYTHON -c "from axiom_runtime.engine import SpectraEngine" 2>/dev/null; then
    echo -e "  $OK axm-core ${DIM}(already installed)${RESET}"
  else
    echo -e "  ${DIM}  installing axm-core...${RESET}"
    pip install -e "$CORE_DIR" -q
    echo -e "  $OK axm-core"
  fi
}

install_chat() {
  if $PYTHON -c "import axm_chat" 2>/dev/null; then
    echo -e "  $OK axm-chat ${DIM}(already installed)${RESET}"
  else
    echo -e "  ${DIM}  installing axm-chat...${RESET}"
    pip install -e "$CHAT_DIR[server]" -q
    echo -e "  $OK axm-chat"
  fi
}

install_genesis
install_core
install_chat

# ── 4. Seed gold shard ────────────────────────────────────────────────────────
echo ""
echo -e "  seeding gold shard..."

SHARD_DIR="$HOME/.axm/shards"
GOLD_SRC="$GENESIS_DIR/shards/gold/fm21-11-hemorrhage-v1"
GOLD_DEST="$SHARD_DIR/fm21-11-hemorrhage-v1"

mkdir -p "$SHARD_DIR"

if [ -d "$GOLD_DEST" ]; then
  echo -e "  $OK fm21-11-hemorrhage-v1 ${DIM}(already seeded)${RESET}"
elif [ -d "$GOLD_SRC" ]; then
  cp -r "$GOLD_SRC" "$GOLD_DEST"
  echo -e "  $OK fm21-11-hemorrhage-v1 ${DIM}seeded to ~/.axm/shards/${RESET}"
else
  echo -e "  $WARN gold shard not found at $GOLD_SRC"
  echo -e "  ${DIM}  query will still work once you import a file${RESET}"
fi

# ── 5. Check Ollama (optional) ────────────────────────────────────────────────
echo ""
echo -e "  checking ollama ${DIM}(optional — needed for distill only)${RESET}..."

OLLAMA_OK=false
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
  OLLAMA_OK=true
  # Check if mistral is pulled
  if curl -s http://localhost:11434/api/tags | grep -q "mistral"; then
    echo -e "  $OK ollama running, mistral ready"
  else
    echo -e "  $WARN ollama running, mistral not pulled"
    echo -e "  ${DIM}  → ollama pull mistral  (run in another terminal)${RESET}"
    echo -e "  ${DIM}  import and query work without it${RESET}"
  fi
else
  echo -e "  ${DIM}  ollama offline — import and query work without it${RESET}"
  echo -e "  ${DIM}  → https://ollama.ai to install${RESET}"
fi

# ── 6. Launch server ──────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────"
echo ""
echo -e "  ${BOLD}starting server on :8410${RESET}"
echo ""
echo -e "  ${DIM}open ui/axm-shell.html in your browser${RESET}"
echo -e "  ${DIM}or visit http://localhost:8410${RESET}"
echo ""

cd "$CHAT_DIR/server"
exec $PYTHON axm_server.py
