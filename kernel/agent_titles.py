from __future__ import annotations
import asyncio
import logging
import re
from kernel.config import TitlesConfig
from kernel.models.base import LLM, Message, Role
from kernel.models.claude import ClaudeLLM
from kernel.models.openai_compat import OpenAICompatLLM

log = logging.getLogger(__name__)

TITLE_RETRY_DELAYS = [0, 3, 15, 60]
TITLE_MAX_LEN = 30

_THINK_RE = re.compile("<think>.*?</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile("<think>.*", re.DOTALL)

def _is_rate_limited(exc: Exception) -> bool:
    for attr in ("status_code", "status"):
        try:
            code = getattr(exc, attr)
        except Exception:
            code = None
        if code == 429:
            return True
    resp = getattr(exc, "response", None)
    if getattr(resp, "status_code", None) == 429:
        return True
    exc_str = str(exc).lower()
    return (
        ("429" in exc_str)
        or ("rate limit" in exc_str)
        or ("too many requests" in exc_str)
    )

def _clean_title(raw: str) -> str:
    text = _THINK_RE.sub("", raw)
    text = _THINK_OPEN_RE.sub("", text)
    text = text.strip().strip("\"'")
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line[:TITLE_MAX_LEN]
    return ""

def _make_titles_llm(cfg: TitlesConfig) -> LLM:
    if cfg.type == "claude":
        return ClaudeLLM(
            api_key=cfg.api_key,
            default_model=cfg.model,
            max_tokens=cfg.max_tokens,
            api_base=cfg.api_base,
            headers=cfg.headers,
        )
    return OpenAICompatLLM(
        api_key=cfg.api_key,
        default_model=cfg.model,
        api_base=cfg.api_base,
        max_tokens=cfg.max_tokens,
        headers=cfg.headers,
    )

def _build_title_prompt(rows: list[dict]) -> str:
    parts: list[str] = []
    for r in rows:
        content = r["content"]
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        parts.append(f"{r['role']}: {text}")
    return "根据以下对话生成一个简短的标题（10字以内，不要引号）：\n\n" + "\n".join(parts)

class AgentTitlesMixin:
    async def maybe_generate_title(self) -> None:
        if self._session_id is None:
            return
        session = await self.store.get_session(self._session_id)
        if session and (not session.get("title")):
            asyncio.create_task(self._generate_title(self._session_id))

    def _ensure_titles_llm(self) -> bool:
        if not self.config.titles:
            return False
        if self._titles_llm is None:
            self._titles_llm = _make_titles_llm(self.config.titles)
        return True

    async def _generate_title(self, session_id: int) -> None:
        if not self._ensure_titles_llm():
            return
        rows = await self.store.get_messages(session_id, limit=4)
        if not rows:
            return
        prompt = _build_title_prompt(rows)
        for attempt, delay in enumerate(TITLE_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._titles_llm.chat(
                    [Message(role=Role.USER, content=prompt)]
                )
                title = _clean_title(resp.text_content())
                if title:
                    await self.store.update_session_title(session_id, title)
                    log.info("Session %d titled: %s", session_id, title)
                return
            except Exception as exc:
                if _is_rate_limited(exc):
                    log.warning(
                        "Title generation hit rate limit (attempt %d/%d), retrying",
                        attempt + 1,
                        len(TITLE_RETRY_DELAYS),
                    )
                    continue
                log.warning(
                    "Title generation attempt %d/%d failed",
                    attempt + 1,
                    len(TITLE_RETRY_DELAYS),
                    exc_info=True,
                )
        log.warning(
            "Title generation for session %d failed after all retries", session_id
        )

    async def regenerate_title(self, session_id: int) -> str | None:
        if not self._ensure_titles_llm():
            return None
        rows = await self.store.get_messages(session_id, limit=6)
        if not rows:
            return None
        prompt = _build_title_prompt(rows)
        resp = await self._titles_llm.chat([Message(role=Role.USER, content=prompt)])
        title = _clean_title(resp.text_content())
        if title:
            await self.store.update_session_title(session_id, title)
        return title or None
