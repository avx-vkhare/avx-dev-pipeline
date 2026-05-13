#!/usr/bin/env bash
# One-click installer for BhramASTRA.
#
# Two ways to invoke:
#
#   1. From an existing clone:
#        bash ~/avx-dev-pipeline/setup.sh
#
#   2. Bootstrap from nothing (clones the repo for you):
#        curl -fsSL https://raw.githubusercontent.com/avx-vkhare/avx-dev-pipeline/main/setup.sh | bash
#
# Either way the script:
#   - Clones / updates the avx-dev-pipeline repo at $BHRAMASTRA_HOME
#   - Creates a venv and installs dependencies
#   - Adds the Atlassian-MCP-Server block to ~/.claude/settings.json
#     (OAuth based — first Jira call pops a browser approval, no token paste)
#   - Prints how to launch the UI
#
# Re-runnable. Everything is idempotent. The target codebase you want to work
# on is selected inside the UI; this script is repo-agnostic.

set -e

REPO_URL="${BHRAMASTRA_REPO_URL:-https://github.com/avx-vkhare/avx-dev-pipeline}"
INSTALL_DIR="${BHRAMASTRA_HOME:-$HOME/avx-dev-pipeline}"
PYTHON="${PYTHON:-/usr/bin/python3}"

echo "=== BhramASTRA installer ==="

# ---------------------------------------------------------------------------
# 1. Locate or clone the tool repo
# ---------------------------------------------------------------------------
# BASH_SOURCE[0] is empty or a pseudo path when piped through bash, so we
# fall through to the bootstrap branch in that case.
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
if [ -f "$SCRIPT_PATH" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
else
    SCRIPT_DIR=""
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/preflight.py" ] && [ -f "$SCRIPT_DIR/ui.py" ]; then
    REPO_DIR="$SCRIPT_DIR"
    echo "Running from existing checkout: $REPO_DIR"
    if git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        git -C "$REPO_DIR" fetch --quiet origin || true
    fi
else
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo "Updating existing install at $INSTALL_DIR ..."
        git -C "$INSTALL_DIR" pull --ff-only --quiet || \
            echo "  (could not fast-forward — keeping local state)"
    else
        echo "Cloning $REPO_URL → $INSTALL_DIR ..."
        if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
            gh repo clone "$(echo "$REPO_URL" | sed 's#.*github.com/##')" "$INSTALL_DIR" -- --quiet
        elif ! git clone --quiet "$REPO_URL" "$INSTALL_DIR"; then
            echo
            echo "Could not clone $REPO_URL."
            echo "If the repo is private, run \`gh auth login --web\` first, then re-run this installer."
            exit 1
        fi
    fi
    REPO_DIR="$INSTALL_DIR"
fi

cd "$REPO_DIR"
VENV_DIR="$REPO_DIR/.venv"

# ---------------------------------------------------------------------------
# 2. Python + venv + deps
# ---------------------------------------------------------------------------
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PYTHON ($PY_VERSION)"
if [[ "$PY_VERSION" < "3.10" ]]; then
    echo "ERROR: Python 3.10+ required (found $PY_VERSION)"
    echo "Override with: PYTHON=/path/to/python3.10 $0"
    exit 1
fi

# Probe that venv is fully installed (Debian/Ubuntu ship pythonX.Y-venv as a
# separate package — without it `python3 -m venv` fails with an "ensurepip is
# not available" message).
if ! "$PYTHON" -c "import ensurepip, venv" >/dev/null 2>&1; then
    PY_PKG=$("$PYTHON" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}-venv')")
    echo "Python's venv module is not fully installed (need $PY_PKG)."
    INSTALLED=""
    # Offer to install it via sudo — but only with explicit consent and only
    # if we have a controlling tty (so curl|bash can't silently escalate).
    if command -v apt >/dev/null 2>&1 && [ -e /dev/tty ]; then
        read -r -p "Install $PY_PKG via 'sudo apt install -y $PY_PKG' now? [Y/n] " ANS </dev/tty
        case "${ANS:-Y}" in
            [Yy]*|"")
                if sudo apt install -y "$PY_PKG"; then
                    INSTALLED=1
                fi
                ;;
        esac
    fi
    if [ -z "$INSTALLED" ] || ! "$PYTHON" -c "import ensurepip, venv" >/dev/null 2>&1; then
        echo
        echo "ERROR: venv is still unavailable. On Debian/Ubuntu run:"
        echo "    sudo apt install -y $PY_PKG"
        echo "then re-run this installer."
        exit 1
    fi
fi

if [ ! -x "$VENV_DIR/bin/pip" ]; then
    if [ -d "$VENV_DIR" ]; then
        echo "Removing broken/incomplete venv at $VENV_DIR ..."
        rm -rf "$VENV_DIR"
    fi
    echo "Creating virtualenv at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$REPO_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 3. Atlassian MCP (OAuth — no token paste)
# ---------------------------------------------------------------------------
echo "Configuring Atlassian MCP ..."
"$VENV_DIR/bin/python" - <<PY
import sys
sys.path.insert(0, "$REPO_DIR")
from preflight import write_atlassian_mcp_block, _settings_path
added = write_atlassian_mcp_block()
print(f"  {'added' if added else 'already present'} in {_settings_path()}")
PY

# ---------------------------------------------------------------------------
# 4. Locate the cloudn checkout (auto-detect → prompt if needed → persist)
# ---------------------------------------------------------------------------
echo
echo "Locating your cloudn checkout ..."
CLOUDN_DIR="$(
"$VENV_DIR/bin/python" - <<PY
import sys
sys.path.insert(0, "$REPO_DIR")
from preflight import detect_cloudn_repo
print(detect_cloudn_repo())
PY
)"

if [ -z "$CLOUDN_DIR" ]; then
    # No checkout found anywhere — prompt the user, but only if we have a tty
    # (curl|bash has no controlling tty for stdin, so we fall back to printing
    # instructions in that case).
    if [ -t 0 ] || [ -e /dev/tty ]; then
        echo "  Could not find a cloudn checkout in common locations."
        if [ -e /dev/tty ]; then
            read -r -p "  Enter the absolute path to your cloudn checkout: " CLOUDN_DIR </dev/tty
        else
            read -r -p "  Enter the absolute path to your cloudn checkout: " CLOUDN_DIR
        fi
    else
        echo "⚠  Could not find a cloudn checkout. Re-run setup.sh in an interactive"
        echo "   shell, or pass BHRAMASTRA_REPO=/path/to/your/cloudn."
        PREFLIGHT_FAILED=1
    fi
fi

if [ -n "$CLOUDN_DIR" ]; then
    CLOUDN_DIR="${CLOUDN_DIR/#\~/$HOME}"
    if [ ! -d "$CLOUDN_DIR/.git" ]; then
        echo "⚠  $CLOUDN_DIR is not a git repo. Clone cloudn there and re-run."
        PREFLIGHT_FAILED=1
    else
        # Persist the choice so the UI (and future setup.sh runs) pick it up
        "$VENV_DIR/bin/python" - <<PY
import sys
sys.path.insert(0, "$REPO_DIR")
from preflight import save_config, CONFIG_PATH
save_config(cloudn_repo="$CLOUDN_DIR")
print(f"  cloudn: $CLOUDN_DIR (saved to {CONFIG_PATH})")
PY
        echo
        "$VENV_DIR/bin/python" "$REPO_DIR/preflight.py" "$CLOUDN_DIR" || PREFLIGHT_FAILED=1
    fi
fi

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
echo
echo "=== Setup complete ==="
echo
if [ -n "$PREFLIGHT_FAILED" ]; then
    echo "Fix the items marked ✗ above, then re-run this script (or use the UI's Re-check button)."
    echo
fi
echo "Launch the UI:"
echo "  $VENV_DIR/bin/python $REPO_DIR/ui.py"
echo
echo "Or add an alias to your shell profile:"
echo "  alias bhramastra='$VENV_DIR/bin/python $REPO_DIR/ui.py'"
echo
if [ -n "$CLOUDN_DIR" ]; then
    echo "BhramASTRA will operate on $CLOUDN_DIR by default."
    echo "Change it any time by editing the Target repo field in the UI."
fi
