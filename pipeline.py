"""
BhramASTRA: Plan → Code → [Test Writer ‖ Reviewer] → (fix loop if FAIL) → PR → PR Comments → done

State is saved to .pipeline_state.json after every stage so you can resume
at any point by re-running run.py.

ask_fn  : callable(prompt: str) -> str   — defaults to input()
emit_fn : callable(msg: str)             — defaults to print()
"""

import json
import subprocess
import anyio
from pathlib import Path

from claude_agent_sdk import (
    query, ClaudeAgentOptions, ResultMessage,
    AssistantMessage, ToolUseBlock,
)

from context import ProjectContext
from agents.prompts import planner_prompt, coder_prompt, test_writer_prompt, reviewer_prompt, pr_creator_prompt, pr_comments_prompt

STATE_FILE = ".pipeline_state.json"
MAX_ITERATIONS = 3


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def save_state(state: dict, ctx: ProjectContext):
    path = Path(ctx.repo_path) / STATE_FILE
    path.write_text(json.dumps(state, indent=2))


def load_state(ctx: ProjectContext) -> dict | None:
    path = Path(ctx.repo_path) / STATE_FILE
    if path.exists():
        return json.loads(path.read_text())
    return None


def clear_state(ctx: ProjectContext):
    path = Path(ctx.repo_path) / STATE_FILE
    path.unlink(missing_ok=True)


def _extract_test_commands(text: str) -> list[str]:
    """Parse TEST_COMMANDS: section from test writer output."""
    cmds = []
    in_section = False
    for line in text.splitlines():
        if line.strip() == "TEST_COMMANDS:":
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if stripped.startswith("bazel "):
                cmds.append(stripped)
            elif stripped and not stripped.startswith("#"):
                break  # end of section
    return cmds


# ---------------------------------------------------------------------------
# Branch setup
# ---------------------------------------------------------------------------

def setup_branch(ctx: ProjectContext, emit_fn) -> None:
    """Check out existing branch or create it from master."""
    repo = ctx.repo_path
    branch = ctx.branch_name

    def git(*args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)

    exists_local  = branch in git("branch", "--list", branch).stdout
    exists_remote = branch in git("branch", "-r", "--list", f"origin/{branch}").stdout

    if exists_local:
        emit_fn(f"[git] Branch '{branch}' exists locally — checking out.")
        r = git("checkout", branch)
    elif exists_remote:
        emit_fn(f"[git] Branch '{branch}' exists on remote — tracking locally.")
        r = git("checkout", "-b", branch, "--track", f"origin/{branch}")
    else:
        emit_fn(f"[git] Branch '{branch}' not found — creating from master.")
        r = git("checkout", "master")
        if r.returncode != 0:
            emit_fn(f"[git] Warning: could not checkout master: {r.stderr.strip()}")
        r = git("checkout", "-b", branch)

    if r.returncode != 0:
        raise RuntimeError(f"git branch setup failed: {r.stderr.strip()}")

    emit_fn(f"[git] Now on branch: {git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()}")


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

# Jira MCP server config — read from ~/.claude/settings.json credentials
def _jira_mcp_server() -> dict:
    import json, os
    settings_path = os.path.expanduser("~/.claude/settings.json")
    try:
        cfg = json.loads(open(settings_path).read()).get("mcpServers", {}).get("jira", {})
        if cfg:
            return {"jira": cfg}
    except Exception:
        pass
    return {}


def _tool_status(tool_name: str, tool_input: dict) -> str:
    """Convert a tool call into a human-readable status line."""
    name = tool_name.lower()
    # File tools
    if name == "read":
        path = tool_input.get("file_path", "")
        return f"📖 Reading {Path(path).name}"
    if name == "edit":
        path = tool_input.get("file_path", "")
        return f"✏️  Editing {Path(path).name}"
    if name == "write":
        path = tool_input.get("file_path", "")
        return f"💾 Writing {Path(path).name}"
    if name == "glob":
        return f"🔍 Searching files: {tool_input.get('pattern', '')}"
    if name == "grep":
        return f"🔍 Searching code: {tool_input.get('pattern', '')}"
    if name == "bash":
        cmd = tool_input.get("command", "")
        if not cmd.startswith("bazel test"):
            cmd = cmd[:80]
        return f"⚙️  Running: {cmd}"
    # Jira MCP tools
    if "jira" in name or "issue" in name:
        issue = tool_input.get("issueIdOrKey", tool_input.get("issue_key", ""))
        return f"📋 Fetching Jira {issue}"
    if "search" in name:
        return f"🔎 Searching Jira: {tool_input.get('jql', tool_input.get('query', ''))[:60]}"
    # Generic fallback
    return f"🔧 {tool_name}"


async def run_agent(
    label: str,
    prompt: str,
    system_prompt: str,
    tools: list[str],
    ctx: ProjectContext,
    emit_fn,
    extra_mcp: dict | None = None,
    bypass_permissions: bool = False,
) -> str:
    emit_fn(f"\n{'='*60}\n  {label}\n{'='*60}")
    result = ""
    mcp_servers = extra_mcp or {}
    perm_mode = "bypassPermissions" if bypass_permissions else "acceptEdits"
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=ctx.repo_path,
            system_prompt=system_prompt,
            allowed_tools=tools,
            permission_mode=perm_mode,
            max_turns=40,
            model="claude-opus-4-6",
            mcp_servers=mcp_servers,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    status = _tool_status(block.name, block.input)
                    emit_fn(f"__status__{status}")
        elif isinstance(message, ResultMessage):
            result = message.result
    return result


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

async def refine_plan(plan: str, ctx, ask_fn, emit_fn) -> str:
    """Interactive plan refinement loop.

    The user can ask questions or request changes in plain English.
    The planner re-runs with the feedback and shows an updated plan.
    Type 'approve' (or 'y') to proceed, 'abort' to stop.
    """
    emit_fn(f"\n--- PLAN ---\n{plan}\n{'-'*40}")
    current_plan = plan

    while True:
        user_input = ask_fn(
            "Type 'approve' to proceed to coding — or ask a question / request a change:"
        ).strip()

        if not user_input:
            continue
        if user_input.lower() in ("approve", "y", "yes", "ok", "done", "lgtm"):
            emit_fn("Plan approved. Moving to coding.")
            return current_plan
        if user_input.lower() in ("abort", "a"):
            raise SystemExit("Aborted by user.")

        # User asked something — re-invoke planner with the feedback
        emit_fn(f"\n💬 User: {user_input}\nRefining plan...\n")
        current_plan = await run_agent(
            label="PLANNER (refinement)",
            prompt=(
                f"You produced this plan:\n{current_plan}\n\n"
                f"The user says: {user_input}\n\n"
                f"If this is a question, answer it clearly. "
                f"If this is a change request, update the plan accordingly. "
                f"Always output the complete final plan as JSON at the end."
            ),
            system_prompt=planner_prompt(ctx),
            tools=["Read", "Glob", "Grep"],
            ctx=ctx,
            emit_fn=emit_fn,
            extra_mcp=_jira_mcp_server(),
            bypass_permissions=True,
        )
        emit_fn(f"\n--- UPDATED PLAN ---\n{current_plan}\n{'-'*40}")


def checkpoint_review(review: str, iteration: int, ask_fn, emit_fn) -> str:
    emit_fn(f"\n--- REVIEW (iteration {iteration}) ---\n{review}\n{'-'*40}")
    if "PASS" in review:
        return review
    while True:
        action = ask_fn("Review FAILED.  c=send to coder  e=edit feedback  a=abort").strip().lower()
        if action == "a":
            raise SystemExit("Aborted by user.")
        elif action == "c":
            return review
        elif action == "e":
            edited = ask_fn("Paste your edited feedback and send:").strip()
            return edited if edited else review


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    ctx: ProjectContext,
    ask_fn=None,
    emit_fn=None,
):
    if ask_fn is None:
        ask_fn = input
    if emit_fn is None:
        emit_fn = print

    # --- Resume or start fresh ---
    state = load_state(ctx)
    if state:
        emit_fn(f"\nFound saved state at stage: '{state['stage']}' (iteration {state.get('iteration', 0)})")
        choice = ask_fn("r=resume  f=start fresh").strip().lower()
        if choice != "r":
            clear_state(ctx)
            state = None

    if not state:
        state = {
            "stage": "plan",
            "plan": None,
            "review_feedback": None,
            "iteration": 0,
        }

    # --- Branch setup ---
    try:
        setup_branch(ctx, emit_fn)
    except RuntimeError as e:
        emit_fn(f"[git] ERROR: {e}\nAborting.")
        return

    # -----------------------------------------------------------------------
    # STAGE: PLAN
    # -----------------------------------------------------------------------
    if state["stage"] == "plan":
        plan_raw = await run_agent(
            label="PLANNER",
            prompt=(
                f"Jira ticket: {ctx.jira_ticket}\n"
                f"Task: {ctx.task_description}\n\n"
                f"First fetch the Jira ticket using the jira MCP tools to get the full "
                f"description, acceptance criteria, and any linked issues. "
                f"Then explore the codebase and produce the plan."
            ),
            system_prompt=planner_prompt(ctx),
            tools=["Read", "Glob", "Grep"],
            ctx=ctx,
            emit_fn=emit_fn,
            extra_mcp=_jira_mcp_server(),
            bypass_permissions=True,   # read-only + MCP fetch, safe to bypass
        )
        plan = await refine_plan(plan_raw, ctx, ask_fn, emit_fn)
        state.update({"stage": "code", "plan": plan, "iteration": 0})
        save_state(state, ctx)

    # -----------------------------------------------------------------------
    # STAGE: CODE → REVIEW loop
    # -----------------------------------------------------------------------
    while state["stage"] in ("code", "review"):
        if state["stage"] == "code":
            iteration = state["iteration"]
            feedback = state.get("review_feedback")

            if feedback:
                coder_prompt_text = (
                    f"The previous implementation had review failures. Fix them.\n\n"
                    f"REVIEW FEEDBACK:\n{feedback}\n\n"
                    f"ORIGINAL PLAN:\n{state['plan']}"
                )
            else:
                coder_prompt_text = "Implement the plan."

            await run_agent(
                label=f"CODER  (iteration {iteration + 1})",
                prompt=coder_prompt_text,
                system_prompt=coder_prompt(ctx, state["plan"]),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                ctx=ctx,
                emit_fn=emit_fn,
            )
            state.update({"stage": "review", "iteration": iteration + 1})
            save_state(state, ctx)

        # -------------------------------------------------------------------
        # STAGE: TEST WRITER + REVIEWER  (parallel)
        # -------------------------------------------------------------------
        if state["stage"] == "review":
            results = {}

            async def do_tests():
                results["tests"] = await run_agent(
                    label="TEST WRITER",
                    prompt="Write tests covering all acceptance criteria in the plan.",
                    system_prompt=test_writer_prompt(ctx, state["plan"]),
                    tools=["Read", "Write", "Glob", "Grep", "Bash"],
                    ctx=ctx,
                    emit_fn=emit_fn,
                )
                # Extract TEST_COMMANDS section and store in state
                test_cmds = _extract_test_commands(results["tests"])
                if test_cmds:
                    state["test_commands"] = test_cmds
                    emit_fn(f"\n📋 Test commands:\n" + "\n".join(test_cmds))

            async def do_review():
                results["review"] = await run_agent(
                    label="REVIEWER",
                    prompt="Review the implementation against the plan.",
                    system_prompt=reviewer_prompt(ctx, state["plan"]),
                    tools=["Read", "Glob", "Grep", "Bash"],
                    ctx=ctx,
                    emit_fn=emit_fn,
                    bypass_permissions=True,   # read-only, safe to bypass
                )

            async with anyio.create_task_group() as tg:
                tg.start_soon(do_tests)
                tg.start_soon(do_review)

            review_result = results["review"]
            review_result = checkpoint_review(review_result, state["iteration"], ask_fn, emit_fn)

            if "PASS" in review_result:
                emit_fn("\n✓ Review PASSED. Moving to PR creation.")
                state.update({"stage": "pr"})
                save_state(state, ctx)
                break

            if state["iteration"] >= MAX_ITERATIONS:
                emit_fn(f"\n✗ Review failed after {MAX_ITERATIONS} iterations. Manual intervention needed.")
                save_state({**state, "review_feedback": review_result}, ctx)
                return

            state.update({"stage": "code", "review_feedback": review_result})
            save_state(state, ctx)

    # -----------------------------------------------------------------------
    # STAGE: PR CREATION
    # -----------------------------------------------------------------------
    if state["stage"] == "pr":
        pr_result = await run_agent(
            label="PR CREATOR",
            prompt=(
                f"Jira ticket: {ctx.jira_ticket}\n"
                f"Task: {ctx.task_description}\n"
                f"Branch: {ctx.branch_name}\n\n"
                f"Commit all implementation changes, push to remote, and open a GitHub PR. "
                f"Print the PR URL as the very last line of your output."
            ),
            system_prompt=pr_creator_prompt(ctx),
            tools=["Bash", "Read", "Glob"],
            ctx=ctx,
            emit_fn=emit_fn,
            bypass_permissions=True,
        )
        # Extract PR URL — last non-empty line of the result
        pr_url = next(
            (line.strip() for line in reversed(pr_result.splitlines()) if line.strip()),
            "",
        )
        emit_fn(f"\n✓ PR created: {pr_url}")
        state.update({"stage": "pr_comments", "pr_url": pr_url})
        save_state(state, ctx)

    # -----------------------------------------------------------------------
    # STAGE: PR COMMENTS  (interactive loop)
    # -----------------------------------------------------------------------
    if state["stage"] == "pr_comments":
        pr_url = state.get("pr_url", "")
        emit_fn(f"\nPR is open: {pr_url}")

        while True:
            action = ask_fn(
                "PR is open.\n  c = fetch & handle new review comments\n  d = done (PR merged)\n  a = abort"
            ).strip().lower()

            if action == "d":
                emit_fn("Pipeline complete. PR merged.")
                clear_state(ctx)
                return
            if action == "a":
                raise SystemExit("Aborted by user.")
            if action != "c":
                continue

            summary = await run_agent(
                label="PR COMMENTS HANDLER",
                prompt=(
                    f"PR URL: {pr_url}\n"
                    f"Jira: {ctx.jira_ticket}\n\n"
                    f"Fetch all unresolved review comments on this PR, make the requested "
                    f"code changes, reply to each comment, then commit and push."
                ),
                system_prompt=pr_comments_prompt(ctx, state["plan"], pr_url),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                ctx=ctx,
                emit_fn=emit_fn,
                bypass_permissions=True,
            )
            emit_fn(f"\n--- Comments handled ---\n{summary}")
