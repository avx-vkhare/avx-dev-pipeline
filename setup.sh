#!/usr/bin/env bash
# One-click installer for BhramASTRA.
#
#   - Creates a virtualenv and installs deps
#   - Writes the Atlassian-MCP-Server block to ~/.claude/settings.json
#     (OAuth-based; no token paste — browser approval happens on first use)
#   - Runs preflight checks and reports what (if anything) the user must fix
#
# Re-runnable; everything is idempotent.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON=${PYTHON:-/usr/bin/python3}
VENV_DIR="$SCRIPT_DIR/.venv"
SETTINGS="$HOME/.claude/settings.json"

echo "=== BhramASTRA setup ==="

# 1. Python version
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PYTHON ($PY_VERSION)"
if [[ "$PY_VERSION" < "3.10" ]]; then
    echo "ERROR: Python 3.10+ required (found $PY_VERSION)"
    echo "Override with: PYTHON=/path/to/python3.10 ./setup.sh"
    exit 1
fi

# 2. Virtualenv + deps
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtualenv at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo "Dependencies installed."

# 3. Auto-write Atlassian-MCP-Server block (OAuth, no token paste)
echo "Configuring Atlassian MCP (OAuth)..."
"$VENV_DIR/bin/python" - <<'PY'
import sys
sys.path.insert(0, ".")
from preflight import write_atlassian_mcp_block, _settings_path
added = write_atlassian_mcp_block()
print(f"  {'added' if added else 'already present'} in {_settings_path()}")
PY

# 4. Run preflight — tells the user exactly what (if anything) still needs fixing
echo
"$VENV_DIR/bin/python" "$SCRIPT_DIR/preflight.py" "$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")" || PREFLIGHT_FAILED=1

echo
echo "=== Setup complete ==="
echo
if [ -n "$PREFLIGHT_FAILED" ]; then
    echo "Fix the items marked ✗ above, then re-run this script (or click Re-check in the UI)."
    echo
fi
echo "Launch the UI:"
echo "  $VENV_DIR/bin/python $SCRIPT_DIR/ui.py"
echo
echo "Or add this alias to your shell profile:"
echo "  alias bhramastra='$VENV_DIR/bin/python $SCRIPT_DIR/ui.py'"
