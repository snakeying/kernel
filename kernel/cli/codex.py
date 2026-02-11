"""Codex CLI integration.

Runs ``codex -a never exec ... --output-last-message <file> "task"`` as a subprocess.
The ``--output-last-message`` flag ensures we can reliably retrieve the final reply.
Runtime appends ``-C <cwd>`` and ``--output-last-message <output_path>``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kernel.cli.base import CLIAgent

log = logging.getLogger(__name__)


class CodexAgent(CLIAgent):
    """Delegate tasks to Codex via subprocess."""

    name = "codex"

    def build_command(self, task: str, cwd: str, output_path: Path) -> list[str]:
        # Base: command + args (from config, e.g. ["-a", "never", "exec", ...])
        # Runtime appends: -C <cwd> + --output-last-message <file> + task
        return [
            self.command,
            *self.args,
            "-C", cwd,
            "--output-last-message", str(output_path),
            task,
        ]

    def extract_output(self, stdout: str, stderr: str, output_path: Path) -> str:
        # Prefer --output-last-message file if it exists
        if output_path.exists():
            try:
                content = output_path.read_text(encoding="utf-8")
                if content.strip():
                    return content
            except Exception:
                log.warning("Failed to read codex output file %s", output_path, exc_info=True)

        # Fallback to stdout
        return stdout if stdout.strip() else stderr
