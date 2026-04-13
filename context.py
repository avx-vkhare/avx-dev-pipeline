from dataclasses import dataclass, field
from pathlib import Path


_PLANNER_SECTIONS = {
    "Architecture Overview",
    "Critical Go Coding Patterns",
    "Branch Naming",
    "Change Cost Analysis (Required)",
}


def _extract_sections(text: str, sections: set[str]) -> str:
    """Return only the specified ## sections from a markdown document."""
    result = []
    current_lines: list[str] = []
    current_section: str | None = None

    for line in text.splitlines():
        if line.startswith("## "):
            if current_section in sections and current_lines:
                result.append("\n".join(current_lines))
            current_section = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_section in sections and current_lines:
        result.append("\n".join(current_lines))

    return "\n\n".join(result)


@dataclass
class ProjectContext:
    repo_path: str
    language: str               # "Go" | "Python" | "Go and Python"
    jira_ticket: str            # e.g. "AVX-73843"
    task_description: str       # plain English description of the task
    branch_name: str            # e.g. "AVX-73843-smart-gateway"
    test_commands: list[str]    # one or more bazel/make test commands
    lint_commands: list[str]    # one or more lint commands
    coding_guidelines: str      # full CLAUDE.md — used by coder, reviewer, etc.
    planner_guidelines: str     # filtered subset of CLAUDE.md for the planner
    # Customer log bundles for pre-plan RCA (optional)
    # Paths to local .tgz files, e.g. downloaded from Jira attachments / S3
    log_files: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.log_files is None:
            self.log_files = []

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
        log_files: list[str] | None = None,
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
            planner_guidelines=_extract_sections(guidelines, _PLANNER_SECTIONS),
            log_files=log_files or [],
        )
