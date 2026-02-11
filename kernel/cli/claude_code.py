"""Claude Code CLI integration.

Runs ``claude -p --output-format text "task"`` as a subprocess.
"""

from __future__ import annotations

from pathlib import Path

from kernel.cli.base import CLIAgent


class ClaudeCodeAgent(CLIAgent):
    """Delegate tasks to Claude Code via subprocess."""

    name = "claude_code"

    def build_command(self, task: str, cwd: str, output_path: Path) -> list[str]:
        # command + args + [task]
        # e.g. ["claude", "-p", "--output-format", "text", task]
        return [self.command, *self.args, task]

    def extract_output(self, stdout: str, stderr: str, output_path: Path) -> str:
        # Claude Code writes output to stdout
        return stdout if stdout.strip() else stderr
