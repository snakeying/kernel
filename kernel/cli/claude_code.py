from __future__ import annotations
from pathlib import Path
from kernel.cli.base import CLIAgent

class ClaudeCodeAgent(CLIAgent):
    name = 'claude_code'

    def build_command(self, task: str, cwd: str, output_path: Path) -> list[str]:
        return [self.command, *self.args, task]

    def extract_output(self, stdout: str, stderr: str, output_path: Path) -> str:
        return stdout if stdout.strip() else stderr
