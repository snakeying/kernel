from pathlib import Path
from openai import AsyncOpenAI

class STTClient:

    def __init__(self, api_base: str, api_key: str, model: str, headers: dict[str, str] | None=None):
        self._client = AsyncOpenAI(api_key=api_key, base_url=api_base, default_headers=headers)
        self._model = model

    async def transcribe(self, audio_path: Path) -> str:
        with open(audio_path, 'rb') as f:
            resp = await self._client.audio.transcriptions.create(model=self._model, file=f)
        return resp.text
