"""Pre-flight checks for BhramASTRA.

Verifies all external dependencies are available before the pipeline starts,
so devs hit clear, actionable errors at startup instead of cryptic failures
20 minutes into a run.

Each check returns a CheckResult. The UI / installer / pipeline can surface
failures with fix hints; auto-fixable ones (writing MCP config) are handled
in-process.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

OK = "ok"
FAIL = "fail"
WARN = "warn"

ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"


@dataclass
class CheckResult:
    name: str
    status: str                    # ok | fail | warn
    message: str                   # short status line
    fix_hint: str = ""             # what the user can do to fix it
    fix_command: str = ""          # shell command they can copy-paste
    auto_fixable: bool = False     # True if the installer can fix without user action


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _load_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_settings(cfg: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_claude_session() -> CheckResult:
    """Claude Code session OR ANTHROPIC_API_KEY env var."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return CheckResult("Claude session", OK, "ANTHROPIC_API_KEY is set")
    cred = Path.home() / ".claude" / ".credentials.json"
    if cred.exists():
        return CheckResult("Claude session", OK, "Claude Code credentials found")
    return CheckResult(
        "Claude session", FAIL,
        "no Claude credentials found",
        "Run `claude login` (Claude Code) — or export ANTHROPIC_API_KEY",
        "claude login",
    )


def check_gh_auth() -> CheckResult:
    if not shutil.which("gh"):
        return CheckResult(
            "GitHub CLI", FAIL,
            "`gh` not installed",
            "Install from https://cli.github.com — then run `gh auth login --web`",
            "gh auth login --web",
        )
    r = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        line = next(
            (l.strip() for l in r.stderr.splitlines() if "Logged in" in l),
            "authenticated",
        )
        return CheckResult("GitHub CLI", OK, line)
    return CheckResult(
        "GitHub CLI", FAIL,
        "`gh` is not authenticated",
        "Run `gh auth login --web` in a terminal, then click Re-check",
        "gh auth login --web",
    )


def check_git_remote(repo_path: str) -> CheckResult:
    r = subprocess.run(
        ["git", "-C", repo_path, "ls-remote", "--heads", "origin"],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode == 0:
        return CheckResult("git push auth", OK, "origin reachable")
    err = (r.stderr.strip().splitlines() or ["unknown"])[-1]
    return CheckResult(
        "git push auth", FAIL,
        f"git ls-remote failed: {err}",
        "Run `gh auth login --web` (also configures git creds), or set up SSH keys",
        "gh auth login --web -p https",
    )


def check_atlassian_mcp() -> CheckResult:
    """Prefer the official Atlassian-MCP-Server (OAuth, no token paste).
    Token-based `jira` is also accepted as a fallback.
    """
    servers = _load_settings().get("mcpServers", {})
    if "Atlassian-MCP-Server" in servers and \
            servers["Atlassian-MCP-Server"].get("enabled", True):
        return CheckResult(
            "Atlassian MCP", OK,
            "Atlassian-MCP-Server configured (OAuth on first use)",
        )
    if "jira" in servers and servers["jira"].get("enabled", True):
        return CheckResult("Atlassian MCP", OK, "jira MCP configured")
    return CheckResult(
        "Atlassian MCP", FAIL,
        "no Atlassian/Jira MCP server in ~/.claude/settings.json",
        "Click Auto-fix to add the Atlassian-MCP-Server block. "
        "First Jira call will pop a browser for OAuth approval.",
        "",
        auto_fixable=True,
    )


def check_bazel(repo_path: str) -> CheckResult:
    if not shutil.which("bazel"):
        return CheckResult(
            "Bazel", FAIL,
            "`bazel` not on PATH",
            "Run `direnv allow` in the repo (or `nix develop --impure`)",
            "direnv allow",
        )
    r = subprocess.run(
        ["bazel", "version"], cwd=repo_path,
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode == 0:
        ver = next(
            (l.split(":", 1)[-1].strip() for l in r.stdout.splitlines() if "Build label" in l),
            "available",
        )
        return CheckResult("Bazel", OK, f"bazel {ver}")
    return CheckResult(
        "Bazel", WARN,
        "bazel found but `bazel version` failed",
        "Run `direnv allow` in the repo to load the dev environment",
        "direnv allow",
    )


def check_claude_md(repo_path: str) -> CheckResult:
    p = Path(repo_path) / "CLAUDE.md"
    if p.exists() and p.stat().st_size > 0:
        return CheckResult("CLAUDE.md", OK, str(p))
    return CheckResult(
        "CLAUDE.md", WARN,
        f"{p} is missing or empty",
        "Pipeline will run with empty coding guidelines. "
        "Run `/init` inside Claude Code in this repo to generate one.",
    )


# ---------------------------------------------------------------------------
# Auto-fix actions
# ---------------------------------------------------------------------------

def write_atlassian_mcp_block() -> bool:
    """Add the Atlassian-MCP-Server block to ~/.claude/settings.json.

    Idempotent: returns False if the block already exists.
    """
    cfg = _load_settings()
    servers = cfg.setdefault("mcpServers", {})
    if "Atlassian-MCP-Server" in servers:
        return False
    servers["Atlassian-MCP-Server"] = {
        "enabled": True,
        "url": ATLASSIAN_MCP_URL,
    }
    _save_settings(cfg)
    return True


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

def run_preflight(repo_path: str) -> list[CheckResult]:
    return [
        check_claude_session(),
        check_gh_auth(),
        check_git_remote(repo_path),
        check_atlassian_mcp(),
        check_bazel(repo_path),
        check_claude_md(repo_path),
    ]


def all_critical_pass(results: list[CheckResult]) -> bool:
    """True if no FAILs (warnings are tolerated)."""
    return not any(r.status == FAIL for r in results)


def format_results(results: list[CheckResult]) -> str:
    icon = {OK: "✓", FAIL: "✗", WARN: "⚠"}
    lines = []
    for r in results:
        lines.append(f"  {icon[r.status]} {r.name}: {r.message}")
        if r.status != OK and r.fix_hint:
            lines.append(f"      → {r.fix_hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry: `python preflight.py [repo_path]`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    repo = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    results = run_preflight(repo)
    print("BhramASTRA pre-flight checks:")
    print(format_results(results))
    print()
    if all_critical_pass(results):
        print("✓ All critical checks passed — ready to launch.")
        sys.exit(0)
    print("✗ Fix the failing checks above, then re-run.")
    sys.exit(1)
