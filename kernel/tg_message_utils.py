from __future__ import annotations

import html
import re
from pathlib import Path

from kernel.render import md_to_tg_html

_TTS_PRE_RE = re.compile(r'<pre(?:\s[^>]*)?>.*?</pre>', re.IGNORECASE | re.DOTALL)
_TTS_TAG_RE = re.compile(r'<[^>]+>')
_TTS_COLON_EMOJI_RE = re.compile(r':[A-Za-z][A-Za-z0-9_+-]{1,}:')
_TTS_UNICODE_EMOJI_RE = re.compile(r'[\U0001F1E6-\U0001F1FF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]')


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
    '.markdown',
    '.rst',
    '.py',
    '.js',
    '.ts',
    '.jsx',
    '.tsx',
    '.mjs',
    '.cjs',
    '.c',
    '.h',
    '.cpp',
    '.hpp',
    '.cc',
    '.cxx',
    '.java',
    '.kt',
    '.kts',
    '.scala',
    '.groovy',
    '.go',
    '.rs',
    '.rb',
    '.php',
    '.pl',
    '.lua',
    '.r',
    '.R',
    '.jl',
    '.swift',
    '.m',
    '.mm',
    '.json',
    '.yaml',
    '.yml',
    '.toml',
    '.ini',
    '.cfg',
    '.conf',
    '.html',
    '.htm',
    '.css',
    '.scss',
    '.less',
    '.svg',
    '.sql',
    '.graphql',
    '.gql',
    '.xml',
    '.csv',
    '.tsv',
    '.log',
    '.env',
    '.gitignore',
    '.dockerignore',
    '.dockerfile',
    '.makefile',
    '.tf',
    '.hcl',
    '.vue',
    '.svelte',
    '.sh',
    '.bash',
    '.zsh',
    '.bat',
    '.cmd',
    '.ps1',
}
_UNSUPPORTED_EXTENSIONS: set[str] = {
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.ppt',
    '.pptx',
    '.odt',
    '.ods',
    '.odp',
    '.rtf',
    '.zip',
    '.tar',
    '.gz',
    '.bz2',
    '.7z',
    '.rar',
    '.exe',
    '.dll',
    '.so',
    '.dylib',
    '.bin',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.bmp',
    '.webp',
    '.ico',
    '.tiff',
    '.mp3',
    '.mp4',
    '.avi',
    '.mkv',
    '.wav',
    '.flac',
    '.ogg',
}


def _is_text_file(filename: str) -> bool | None:
    ext = Path(filename).suffix.lower()
    if not ext:
        basename = Path(filename).name.lower()
        if basename in ('makefile', 'dockerfile', 'vagrantfile', 'gemfile', 'rakefile', 'procfile'):
            return True
        return None
    if ext in _TEXT_EXTENSIONS:
        return True
    if ext in _UNSUPPORTED_EXTENSIONS:
        return False
    return None


async def _extract_file_text(file_path: Path) -> str:
    text = file_path.read_text(encoding='utf-8')
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + f'\n\n[… 截断，共 {len(text)} 字符]'
    return text

