from __future__ import annotations
import asyncio
import logging
import os
import shutil
import sys
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
log = logging.getLogger(__name__)
CLI_TIMEOUT = 600
OUTPUT_TRUNCATE_CHARS = 50000

def _truncate_output(text: str, max_chars: int=OUTPUT_TRUNCATE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f'\n\n… [truncated {len(text) - max_chars} chars] …\n\n' + text[-half:]

class CLIResult:
    __slots__ = ('ok', 'cli_name', 'cwd', 'exit_code', 'output_path', 'output', 'raw_output')

    def __init__(self, *, ok: bool, cli_name: str, cwd: str, exit_code: int, output_path: str, output: str, raw_output: str='') -> None:
        self.ok = ok
        self.cli_name = cli_name
        self.cwd = cwd
        self.exit_code = exit_code
        self.output_path = output_path
        self.output = output
        self.raw_output = raw_output

    def to_dict(self) -> dict[str, Any]:
        return {'ok': self.ok, 'cli': self.cli_name, 'cwd': self.cwd, 'exit_code': self.exit_code, 'output_path': self.output_path, 'output': self.output}

class CLIAgent(ABC):
    name: str = 'base'

    def __init__(self, command: str, args: list[str], output_dir: Path) -> None:
        self.command = command
        self.args = list(args)
        self.output_dir = output_dir
        self._process: asyncio.subprocess.Process | None = None

    def _make_output_path(self) -> Path:
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        uid = uuid.uuid4().hex[:6]
        return self.output_dir / f'{self.name}_{ts}_{uid}.txt'

    @abstractmethod
    def build_command(self, task: str, cwd: str, output_path: Path) -> list[str]:
        ...

    @abstractmethod
    def extract_output(self, stdout: str, stderr: str, output_path: Path) -> str:
        ...

    async def run(self, task: str, cwd: str) -> CLIResult:
        output_path = self._make_output_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self.build_command(task, cwd, output_path)
        resolved = shutil.which(cmd[0])
        if resolved:
            cmd[0] = resolved
        log.info('CLI [%s] running: %s', self.name, ' '.join(cmd[:5]) + ' ...')
        try:
            use_shell = sys.platform == 'win32' and (not resolved)
            if use_shell:
                self._process = await asyncio.create_subprocess_shell(' '.join(cmd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd, env={**os.environ})
            else:
                self._process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd, env={**os.environ})
            stdout_bytes, stderr_bytes = await asyncio.wait_for(self._process.communicate(), timeout=CLI_TIMEOUT)
            exit_code = self._process.returncode or 0
            self._process = None
        except asyncio.TimeoutError:
            log.warning('CLI [%s] timed out after %ds', self.name, CLI_TIMEOUT)
            await self.kill()
            return CLIResult(ok=False, cli_name=self.name, cwd=cwd, exit_code=-1, output_path=str(output_path), output=f'Error: CLI timed out after {CLI_TIMEOUT}s')
        except asyncio.CancelledError:
            log.info('CLI [%s] cancelled', self.name)
            await self.kill()
            raise
        except Exception as exc:
            log.exception('CLI [%s] failed to start', self.name)
            self._process = None
            return CLIResult(ok=False, cli_name=self.name, cwd=cwd, exit_code=-1, output_path=str(output_path), output=f'Error: failed to start CLI — {exc}')
        stdout = stdout_bytes.decode('utf-8', errors='replace')
        stderr = stderr_bytes.decode('utf-8', errors='replace')
        raw_output = self.extract_output(stdout, stderr, output_path)
        try:
            output_path.write_text(raw_output, encoding='utf-8')
        except Exception:
            log.warning('Failed to write CLI output to %s', output_path, exc_info=True)
        truncated = _truncate_output(raw_output)
        return CLIResult(ok=exit_code == 0, cli_name=self.name, cwd=cwd, exit_code=exit_code, output_path=str(output_path), output=truncated, raw_output=raw_output)

    async def kill(self) -> None:
        proc = self._process
        if proc is None:
            return
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        except Exception:
            log.warning('Error killing CLI [%s] process', self.name, exc_info=True)
        finally:
            self._process = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None
