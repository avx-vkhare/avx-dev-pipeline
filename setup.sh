#!/usr/bin/env bash
set -e

PYTHON=${PYTHON:-/usr/bin/python3}
VENV_DIR="$(dirname "$0")/.venv"

echo "=== Dev Pipeline Setup ==="

# 1. Check Python version
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PYTHON ($PY_VERSION)"
if [[ "$PY_VERSION" < "3.10" ]]; then
    echo "ERROR: Python 3.10+ required (found $PY_VERSION)"
    echo "Override with: PYTHON=/path/to/python3.10 ./setup.sh"
    exit 1
fi

# 2. Create virtualenv
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtualenv at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "Virtualenv already exists at $VENV_DIR"
fi

# 3. Install dependencies
echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$(dirname "$0")/requirements.txt"
echo "Dependencies installed."

# 4. Check Claude API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    SETTINGS="$HOME/.claude/settings.json"
    if [ ! -f "$SETTINGS" ] || ! grep -q "apiKey\|api_key" "$SETTINGS" 2>/dev/null; then
        echo ""
        echo "WARNING: ANTHROPIC_API_KEY is not set."
        echo "  Export it before running:  export ANTHROPIC_API_KEY=sk-ant-..."
        echo "  Or add it to ~/.claude/settings.json"
    else
        echo "Claude config found at $SETTINGS"
    fi
else
    echo "ANTHROPIC_API_KEY is set."
fi

# 5. Check Jira MCP config
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ] && grep -q '"jira"' "$SETTINGS" 2>/dev/null; then
    echo "Jira MCP config found."
else
    echo ""
    echo "WARNING: Jira MCP server not configured in ~/.claude/settings.json"
    echo "  Add an entry like:"
    echo '  { "mcpServers": { "jira": { "command": "...", "env": { "JIRA_URL": "...", "JIRA_TOKEN": "..." } } } }'
    echo "  (Pipeline still works without it — Jira ticket details won't be fetched.)"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "To run the UI:"
echo "  $VENV_DIR/bin/python ui.py"
echo ""
echo "Or add this alias to your shell profile:"
echo "  alias dev-pipeline='$VENV_DIR/bin/python $(dirname "$0")/ui.py'"
