import asyncio
import logging
import tempfile
from pathlib import Path
import edge_tts
import static_ffmpeg
log = logging.getLogger(__name__)

class TTSClient:

    def __init__(self, voice: str):
        self._voice = voice

    async def synthesize(self, text: str, output_path: Path) -> None:
        if not text.strip():
            raise ValueError('No text to synthesize')
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            tmp_mp3 = Path(tmp.name)
        try:
            communicate = edge_tts.Communicate(text, self._voice)
            await communicate.save(str(tmp_mp3))
            ffmpeg, _ = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
            proc = await asyncio.create_subprocess_exec(ffmpeg, '-i', str(tmp_mp3), '-c:a', 'libopus', '-b:a', '48k', '-y', str(output_path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f'ffmpeg exited with code {proc.returncode}')
        finally:
            tmp_mp3.unlink(missing_ok=True)
