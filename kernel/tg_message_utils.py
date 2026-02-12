from __future__ import annotations
import html
import re
from pathlib import Path
from kernel.render import md_to_tg_html

_TTS_PRE_RE = re.compile(r'<pre(?:\s[^>]*)?>.*?</pre>', re.IGNORECASE | re.DOTALL)
_TTS_TAG_RE = re.compile(r'<[^>]+>')
_TTS_COLON_EMOJI_RE = re.compile(r':[A-Za-z][A-Za-z0-9_+-]{1,}:')
_TTS_UNICODE_EMOJI_RE = re.compile(r'[\U0001F1E6-\U0001F1FF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]')

def _max_backtick_run(text: str) -> int:
    """Return the max consecutive '`' run length in text."""
    best = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
            if run > best:
                best = run
        else:
            run = 0
    return best

def _guess_code_lang(filename: str) -> str:
    p = Path(filename)
    ext = p.suffix.lower().lstrip(".")
    if ext:
        return ext
    base = p.name.lower()
    if base in ("makefile", "dockerfile"):
        return base
    return "text"

def _wrap_file_text(filename: str, text: str) -> str:
    """Wrap a text file for LLM input without breaking when file contains ``` fences."""
    fence_len = max(3, _max_backtick_run(text) + 1)
    fence = "`" * fence_len
    lang = _guess_code_lang(filename)
    return f"[文件: {filename}]\n{fence}{lang}\n{text}\n{fence}"

def _to_tts_text(markdown: str) -> str:
    html_text = md_to_tg_html(markdown)
    html_text = _TTS_PRE_RE.sub('\n代码略\n', html_text)
    text = _TTS_TAG_RE.sub('', html_text)
    text = html.unescape(text)
    text = _TTS_COLON_EMOJI_RE.sub('', text)
    text = _TTS_UNICODE_EMOJI_RE.sub('', text)
    text = text.replace('\ufe0f', '').replace('\u200d', '').replace('\u20e3', '')
    text = text.replace('• ', '').replace('▍ ', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

_MAX_FILE_SIZE = 20 * 1024 * 1024
_MAX_TEXT_CHARS = 50000
_TEXT_EXTENSIONS: set[str] = {
    '.txt',
    '.md',
    '.py',
    '.json',
    '.yaml',
    '.yml',
    '.toml',
    '.ini',
    '.sql',
    '.csv',
    '.log',
}

def _is_text_file(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    if not ext:
        basename = Path(filename).name.lower()
        return basename in ('makefile', 'dockerfile')
    return ext in _TEXT_EXTENSIONS

async def _extract_file_text(file_path: Path) -> str:
    text = file_path.read_text(encoding='utf-8')
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + f'\n\n[… 截断，共 {len(text)} 字符]'
    return text
