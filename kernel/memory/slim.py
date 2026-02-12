from __future__ import annotations
import json
from typing import Any

_SLIM_THRESHOLD = 200

def slim_content(_role: str, content: Any) -> Any:
    if not isinstance(content, list):
        return content
    slimmed: list[Any] = []
    for block in content:
        if not isinstance(block, dict):
            slimmed.append(block)
            continue
        btype = block.get('type')
        if btype == 'image':
            slimmed.append({'type': 'text', 'text': '[图片已处理]'})
            continue
        if btype == 'text':
            text = block.get('text', '')
            if text.startswith('[文件: ') and '\n```' in text:
                fname = text.split(']', 1)[0].removeprefix('[文件: ')
                slimmed.append({'type': 'text', 'text': f'[文件 {fname} 已处理]'})
                continue
            if text.startswith('[语音: ') and text.endswith(']'):
                slimmed.append({'type': 'text', 'text': '[语音已处理]'})
                continue
        if btype == 'tool_result':
            raw = block.get('content', '')
            if isinstance(raw, str):
                should_slim = _should_slim_tool_result(raw)
                if should_slim:
                    summary = _summarise_tool_result(raw)
                    slimmed.append({**block, 'content': summary})
                    continue
        slimmed.append(block)
    return slimmed

def _should_slim_tool_result(raw: str) -> bool:
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get('output_path'):
            return True
    except (json.JSONDecodeError, TypeError):
        pass
    return len(raw) > _SLIM_THRESHOLD

def _summarise_tool_result(raw: str) -> str:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            ok = data.get('ok')
            cli = data.get('cli', '')
            exit_code = data.get('exit_code')
            output_path = data.get('output_path', '')
            if ok is not None and output_path:
                status = '成功' if ok else f'失败(exit={exit_code})'
                return f'[{cli} 任务{status}，详见 {output_path}]'
            keys = ', '.join(list(data.keys())[:5])
            return f'[工具结果: {{{keys}...}}，{len(raw)} 字符已省略]'
    except (json.JSONDecodeError, TypeError):
        pass
    preview = raw[:80].replace('\n', ' ')
    return f'[工具结果: {preview}… ({len(raw)} 字符已省略)]'
