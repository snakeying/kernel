from __future__ import annotations
import logging
from pathlib import Path
from kernel.cli.base import CLIAgent
log = logging.getLogger(__name__)

class CodexAgent(CLIAgent):
    name = 'codex'

    def build_command(self, task: str, cwd: str, output_path: Path) -> list[str]:
        return [self.command, *self.args, '-C', cwd, '--output-last-message', str(output_path), task]

    def extract_output(self, stdout: str, stderr: str, output_path: Path) -> str:
        if output_path.exists():
            try:
                content = output_path.read_text(encoding='utf-8')
                if content.strip():
                    return content
            except Exception:
                log.warning('Failed to read codex output file %s', output_path, exc_info=True)
        return stdout if stdout.strip() else stderr
