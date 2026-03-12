# Dev Pipeline

A multi-agent AI development pipeline powered by Claude. Given a Jira ticket and task description, it automatically plans, codes, tests, and reviews changes — with human-in-the-loop checkpoints.

## How it works

```
Planner → (you review & refine plan) → Coder → Test Writer + Reviewer → (fix loop if needed) → Done
```

## Prerequisites

1. **Python 3.10+** — check with `python3 --version`
2. **Claude API key** — get one at https://console.anthropic.com
3. **Jira MCP server** *(optional)* — for automatic Jira ticket fetching

## Setup (one-time)

```bash
git clone <this-repo>
cd dev-pipeline
./setup.sh
```

The script creates a `.venv/`, installs dependencies, and checks your config.

## Configuration

### Claude API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Add to `~/.bashrc` or `~/.zshrc` to make it permanent.

### Jira MCP (optional)

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "jira": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-jira"],
      "env": {
        "JIRA_URL": "https://yourcompany.atlassian.net",
        "JIRA_TOKEN": "your-jira-api-token"
      }
    }
  }
}
```

## Running

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python ui.py
```

Then open **http://127.0.0.1:7860** in your browser.

> **VSCode Remote SSH users:** The port is auto-forwarded. Check the **Ports** panel and click the `7860` link, or use "Open in Browser" from the Ports panel.

## Usage

1. Fill in the config form:
   - **Jira Ticket ID** — e.g. `AVX-73843`
   - **Branch Name** — existing branch to check out, or new branch to create from master
   - **Language** — Go, Python, or both
   - **Task Description** — plain English description of the change
   - **Repo Path** — absolute path to your local repo
   - **Test / Lint Commands** — one per line

2. Click **▶ Start Pipeline**

3. Review the plan in the chat — ask questions or request changes, then type `approve`

4. The pipeline codes, tests, and reviews automatically

5. If review fails, it loops back to the coder with the reviewer's feedback

## Files

| File | Purpose |
|------|---------|
| `ui.py` | Gradio web UI |
| `pipeline.py` | Orchestration logic |
| `context.py` | `ProjectContext` dataclass |
| `agents/prompts.py` | System prompts for each agent |
| `run.py` | CLI entry point (no UI) |
