"""Markdown → Telegram HTML renderer using mistune 3.x.

Telegram supports a limited subset of HTML: <b>, <i>, <u>, <s>, <code>,
<pre>, <a>, <tg-spoiler>.  Unsupported elements gracefully fall back to
plain text.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from mistune.core import BaseRenderer, BlockState
from mistune.util import escape as escape_text

import mistune


class TelegramHTMLRenderer(BaseRenderer):
    """Render Markdown AST to Telegram-compatible HTML.

    Follows the same ``render_token`` pattern as mistune's built-in
    HTMLRenderer: methods receive pre-rendered children (or raw text)
    as the first positional argument, plus any ``attrs`` as keyword args.
    """

    NAME = "tg_html"

    def render_token(self, token: dict[str, Any], state: BlockState) -> str:
        func = self._get_method(token["type"])
        attrs = token.get("attrs")
        if "raw" in token:
            text = token["raw"]
        elif "children" in token:
            text = self.render_tokens(token["children"], state)
        else:
            if attrs:
                return func(**attrs)
            return func()
        if attrs:
            return func(text, **attrs)
        return func(text)

    # -- Inline ----------------------------------------------------------

    def text(self, text: str) -> str:
        return escape_text(text)

    def emphasis(self, text: str) -> str:
        return f"<i>{text}</i>"

    def strong(self, text: str) -> str:
        return f"<b>{text}</b>"

    def link(self, text: str, url: str, title: Optional[str] = None) -> str:
        url = escape_text(url)
        return f'<a href="{url}">{text}</a>'

    def image(self, text: str, url: str, title: Optional[str] = None) -> str:
        url = escape_text(url)
        alt = escape_text(text) if text else "image"
        return f'[<a href="{url}">{alt}</a>]'

    def codespan(self, text: str) -> str:
        return f"<code>{escape_text(text)}</code>"

    def linebreak(self) -> str:
        return "\n"

    def softbreak(self) -> str:
        return "\n"

    def inline_html(self, html: str) -> str:
        return escape_text(html)

    def strikethrough(self, text: str) -> str:
        return f"<s>{text}</s>"

    # -- Block -----------------------------------------------------------

    def paragraph(self, text: str) -> str:
        return f"{text}\n\n"

    def heading(self, text: str, level: int = 1, **attrs: Any) -> str:
        return f"<b>{text}</b>\n\n"

    def blank_line(self) -> str:
        return ""

    def thematic_break(self) -> str:
        return "——————\n\n"

    def block_code(self, code: str, info: Optional[str] = None, **attrs: Any) -> str:
        code = escape_text(code)
        if info:
            info = info.strip()
            return f'<pre><code class="language-{escape_text(info)}">{code}</code></pre>\n\n'
        return f"<pre>{code}</pre>\n\n"

    def block_quote(self, text: str) -> str:
        lines = text.strip().split("\n")
        quoted = "\n".join(f"▍ {line}" for line in lines)
        return f"{quoted}\n\n"

    def block_text(self, text: str) -> str:
        return text

    def block_html(self, html: str) -> str:
        return html + "\n"

    def block_error(self, text: str) -> str:
        return ""

    def list(self, text: str, ordered: bool = False, **attrs: Any) -> str:
        return f"{text}\n"

    def list_item(self, text: str, **attrs: Any) -> str:
        text = text.strip()
        return f"• {text}\n"


# Singleton markdown parser
_md = mistune.create_markdown(
    renderer=TelegramHTMLRenderer(),
    plugins=["strikethrough"],
)


def md_to_tg_html(text: str) -> str:
    """Convert Markdown text to Telegram HTML."""
    return _md(text)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Smart splitting for Telegram's 4096-char limit
# ---------------------------------------------------------------------------

TG_MAX_LEN = 4096

_OPEN_TAG_RE = re.compile(r"<(b|i|u|s|code|pre|a|tg-spoiler)(?:\s[^>]*)?>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"</(b|i|u|s|code|pre|a|tg-spoiler)>", re.IGNORECASE)


def _find_unclosed_tags(text: str) -> list[tuple[str, str]]:
    """Return ``[(tag_name, full_open_tag), ...]`` for unclosed tags in *text*.

    Tags are processed in document order so that properly closed pairs
    (e.g. ``<b>…</b>``) are correctly eliminated.
    """
    events: list[tuple[int, str, str, str]] = []  # (pos, "open"/"close", tag_name, full_match)
    for m in _OPEN_TAG_RE.finditer(text):
        events.append((m.start(), "open", m.group(1).lower(), m.group(0)))
    for m in _CLOSE_TAG_RE.finditer(text):
        events.append((m.start(), "close", m.group(1).lower(), m.group(0)))
    events.sort(key=lambda e: e[0])

    stack: list[tuple[str, str]] = []  # (tag_name, full_open_tag)
    for _, kind, tag_name, full in events:
        if kind == "open":
            stack.append((tag_name, full))
        else:
            # Pop the nearest matching open tag (search from top)
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag_name:
                    stack.pop(i)
                    break
    return stack


def split_tg_message(html: str, max_len: int = TG_MAX_LEN) -> list[str]:
    """Split HTML into chunks that fit Telegram's message size limit.

    Tries to split on paragraph / code-block boundaries first, then falls
    back to newlines, then hard-cuts.  Unclosed tags are healed across
    chunks, preserving attributes (e.g. ``class="language-python"``).
    """
    if len(html) <= max_len:
        return [html]

    chunks: list[str] = []
    remaining = html

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        split_at = -1
        idx = remaining.rfind("\n\n", 0, max_len)
        if idx > max_len // 4:
            split_at = idx + 2
        else:
            idx = remaining.rfind("\n", 0, max_len)
            if idx > max_len // 4:
                split_at = idx + 1
            else:
                split_at = max_len

        chunk = remaining[:split_at]
        remaining = remaining[split_at:]

        unclosed = _find_unclosed_tags(chunk)
        if unclosed:
            # Close tags in reverse order at end of chunk
            for tag_name, _ in reversed(unclosed):
                chunk += f"</{tag_name}>"
            # Re-open with original attributes at start of next chunk
            # Reversed because each prepend inverts order
            for _, full_open_tag in reversed(unclosed):
                remaining = full_open_tag + remaining

        chunks.append(chunk)

    return chunks
