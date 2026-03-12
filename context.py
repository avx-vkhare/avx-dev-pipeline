from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectContext:
    repo_path: str
    language: str               # "Go" | "Python" | "Go and Python"
    jira_ticket: str            # e.g. "AVX-73843"
    task_description: str       # plain English description of the task
    branch_name: str            # e.g. "AVX-73843-smart-gateway"
    test_commands: list[str]    # one or more bazel/make test commands
    lint_commands: list[str]    # one or more lint commands
    coding_guidelines: str      # auto-loaded from CLAUDE.md — do not set manually

    @classmethod
    def from_repo(
        cls,
        repo_path: str,
        jira_ticket: str,
        task: str,
        branch: str,
        language: str = "Go",
        test_commands: list[str] | None = None,
        lint_commands: list[str] | None = None,
    ):
        # Auto-load CLAUDE.md as coding guidelines
        guidelines = ""
        claude_md = Path(repo_path) / "CLAUDE.md"
        if claude_md.exists():
            guidelines = claude_md.read_text()

        return cls(
            repo_path=repo_path,
            language=language,
            jira_ticket=jira_ticket,
            task_description=task,
            branch_name=branch,
            test_commands=test_commands or [],
            lint_commands=lint_commands or [],
            coding_guidelines=guidelines,
        )
