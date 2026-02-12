from __future__ import annotations
import asyncio
import logging
from kernel.agent_content import _json_to_message
from kernel.config import ProviderConfig
from kernel.models.base import LLM
from kernel.models.claude import ClaudeLLM
from kernel.models.openai_compat import OpenAICompatLLM

log = logging.getLogger(__name__)

def _make_llm(provider: ProviderConfig) -> LLM:
    if provider.type == "claude":
        return ClaudeLLM(
            api_key=provider.api_key,
            default_model=provider.default_model,
            max_tokens=provider.max_tokens or 4096,
            api_base=provider.api_base,
            headers=provider.headers,
        )
    return OpenAICompatLLM(
        api_key=provider.api_key,
        default_model=provider.default_model,
        api_base=provider.api_base,
        max_tokens=provider.max_tokens,
        headers=provider.headers,
    )

class AgentSessionsMixin:
    @property
    def current_provider_name(self) -> str:
        return self._current_provider_name

    @property
    def current_model(self) -> str:
        prov = self.config.providers[self._current_provider_name]
        return self._current_model or prov.default_model

    @property
    def available_providers(self) -> list[str]:
        return [
            name
            for name, p in self.config.providers.items()
            if p.api_key and (not p.api_key.endswith("..."))
        ]

    def switch_provider(self, name: str) -> str:
        if name not in self.config.providers:
            raise ValueError(f"Unknown provider: {name}")
        prov = self.config.providers[name]
        if not prov.api_key or prov.api_key.endswith("..."):
            raise ValueError(f"Provider '{name}' has no API key configured.")
        self._current_provider_name = name
        self._current_model = None
        asyncio.create_task(self._persist_provider_model())
        return name

    def switch_model(self, model: str) -> str:
        prov = self.config.providers[self._current_provider_name]
        if model not in prov.models:
            allowed = ", ".join(prov.models)
            raise ValueError(
                f"Model '{model}' not in allowed models for {self._current_provider_name}: [{allowed}]"
            )
        self._current_model = model
        asyncio.create_task(self._persist_provider_model())
        return model

    async def _persist_provider_model(self) -> None:
        try:
            await self.store.set_setting("last_provider", self._current_provider_name)
            model = self._current_model or ""
            await self.store.set_setting("last_model", model)
        except Exception:
            log.debug("Failed to persist provider/model", exc_info=True)

    async def restore_provider_model(self) -> None:
        try:
            provider = await self.store.get_setting("last_provider")
            if provider and provider in self.config.providers:
                prov = self.config.providers[provider]
                if prov.api_key and (not prov.api_key.endswith("...")):
                    self._current_provider_name = provider
                    model = await self.store.get_setting("last_model")
                    if model and model in prov.models:
                        self._current_model = model
                    else:
                        self._current_model = None
                    log.info(
                        "Restored provider: %s, model: %s",
                        self._current_provider_name,
                        self.current_model,
                    )
        except Exception:
            log.debug("Failed to restore provider/model", exc_info=True)

    def _get_llm(self) -> LLM:
        name = self._current_provider_name
        if name not in self._llms:
            prov = self.config.providers[name]
            self._llms[name] = _make_llm(prov)
        return self._llms[name]

    @property
    def session_id(self) -> int | None:
        return self._session_id

    async def new_session(self) -> int:
        if self._session_id is not None:
            await self.store.archive_session(self._session_id)
        self._session_id = await self.store.create_session()
        self._history = []
        return self._session_id

    async def resume_session(self, session_id: int) -> int:
        session = await self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found.")
        if self._session_id is not None and self._session_id != session_id:
            await self.store.archive_session(self._session_id)
        self._session_id = session_id
        rows = await self.store.get_messages(session_id)
        self._history = [_json_to_message(r) for r in rows]
        return session_id

    async def ensure_session(self) -> int:
        if self._session_id is None:
            self._session_id = await self.store.create_session()
            self._history = []
        return self._session_id
