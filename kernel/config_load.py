from __future__ import annotations
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any
from kernel.config_types import (
    CLIConfig,
    Config,
    GeneralConfig,
    MCPServerConfig,
    ProviderConfig,
    STTConfig,
    TTSConfig,
    TelegramConfig,
    TitlesConfig,
)

if sys.version_info >= (3, 12):
    import tomllib
else:
    import tomli as tomllib

log = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{(\w+)\}")

def _expand_env(value: str) -> str:
    def _repl(m: re.Match) -> str:
        name = m.group(1)
        val = os.environ.get(name)
        if val is None:
            raise ValueError(f"Environment variable ${{{name}}} not set")
        return val

    return _ENV_RE.sub(_repl, value)

def _expand_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if headers is None:
        return None
    expanded: dict[str, str] = {}
    for k, v in headers.items():
        expanded[k] = _expand_env(v) if "${" in v else v
    return expanded

def _provider_env_key(provider_name: str) -> str:
    sanitised = re.sub("[^A-Za-z0-9]", "_", provider_name).upper()
    return f"KERNEL_PROVIDER_{sanitised}_API_KEY"

def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        path = os.environ.get("KERNEL_CONFIG", "config.toml")
    path = Path(path).resolve()
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")
    with open(path, "rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    config_dir = path.parent
    tg_raw = raw.get("telegram", {})
    tg_token = os.environ.get("KERNEL_TELEGRAM_TOKEN") or tg_raw.get("token", "")
    tg_user = tg_raw.get("allowed_user", 0)
    if not tg_token or tg_token == "YOUR_BOT_TOKEN":
        raise SystemExit("telegram.token is required. Set it in config.toml or env KERNEL_TELEGRAM_TOKEN.")
    if not tg_user:
        raise SystemExit("telegram.allowed_user is required. Use @userinfobot on Telegram to get your user ID.")
    telegram = TelegramConfig(token=tg_token, allowed_user=int(tg_user))

    g = raw.get("general", {})
    general = GeneralConfig(
        timezone=g.get("timezone", "Asia/Shanghai"),
        default_provider=g.get("default_provider", "anthropic"),
        default_workspace=g.get("default_workspace", "."),
        context_rounds=g.get("context_rounds", 50),
        memory_recall_k=g.get("memory_recall_k", 5),
        data_dir=g.get("data_dir", "data"),
    )

    providers: dict[str, ProviderConfig] = {}
    for name, praw in raw.get("providers", {}).items():
        env_key = _provider_env_key(name)
        api_key = os.environ.get(env_key) or praw.get("api_key", "")
        ptype = praw.get("type", "openai_compat")
        if ptype not in ("claude", "openai_compat"):
            raise SystemExit(
                f"providers.{name}.type must be one of: claude, openai_compat (got {ptype!r})"
            )
        max_tokens = praw.get("max_tokens")
        if ptype == "claude" and max_tokens is None:
            raise SystemExit(f"providers.{name}.max_tokens is required for Claude providers.")
        try:
            headers = _expand_headers(praw.get("headers"))
        except ValueError as exc:
            log.warning("Provider %s headers: %s - skipping headers", name, exc)
            headers = None
        providers[name] = ProviderConfig(
            name=name,
            type=ptype,
            api_key=api_key,
            default_model=praw.get("default_model", ""),
            models=praw.get("models", []),
            api_base=praw.get("api_base"),
            max_tokens=max_tokens,
            headers=headers,
        )

    if general.default_provider not in providers:
        raise SystemExit(f"Default provider '{general.default_provider}' not found in [providers].")
    default_prov = providers[general.default_provider]
    if (
        not default_prov.api_key
        or default_prov.api_key.startswith("sk-ant-...")
        or default_prov.api_key == "sk-..."
    ):
        raise SystemExit(f"API key for default provider '{general.default_provider}' is required.")

    titles: TitlesConfig | None = None
    if "titles" in raw:
        t = raw["titles"]
        t_api_key = os.environ.get("KERNEL_TITLES_API_KEY") or t.get("api_key", "")
        if t_api_key and t_api_key != "sk-...":
            t_type = t.get("type", "openai_compat")
            if t_type not in ("claude", "openai_compat"):
                raise SystemExit(
                    f"titles.type must be one of: claude, openai_compat (got {t_type!r})"
                )
            try:
                t_headers = _expand_headers(t.get("headers"))
            except ValueError as exc:
                log.warning("Titles headers: %s - skipping headers", exc)
                t_headers = None
            titles = TitlesConfig(
                type=t_type,
                api_base=t.get("api_base", ""),
                api_key=t_api_key,
                model=t.get("model", ""),
                max_tokens=t.get("max_tokens", 100),
                headers=t_headers,
            )
        else:
            log.warning("Titles API key not set - auto-title disabled.")

    stt: STTConfig | None = None
    if "stt" in raw:
        s = raw["stt"]
        s_api_key = os.environ.get("KERNEL_STT_API_KEY") or s.get("api_key", "")
        if s_api_key and s_api_key != "sk-...":
            try:
                s_headers = _expand_headers(s.get("headers"))
            except ValueError as exc:
                log.warning("STT headers: %s - skipping headers", exc)
                s_headers = None
            stt = STTConfig(
                api_base=s.get("api_base", "https://api.openai.com/v1"),
                api_key=s_api_key,
                model=s.get("model", "whisper-1"),
                headers=s_headers,
            )
        else:
            log.warning("STT API key not set - voice messages disabled.")

    tts: TTSConfig | None = None
    if "tts" in raw:
        t = raw["tts"]
        voice = t.get("voice", "zh-CN-XiaoxiaoNeural")
        tts = TTSConfig(voice=voice)

    cli: dict[str, CLIConfig] = {}
    for name, c in raw.get("cli", {}).items():
        cli[name] = CLIConfig(command=c.get("command", name), args=c.get("args", []))

    mcp_servers: list[MCPServerConfig] = []
    for s in raw.get("mcp", {}).get("servers", []):
        try:
            hdrs = _expand_headers(s.get("headers"))
        except ValueError as exc:
            log.warning("MCP server '%s' headers: %s - skipping server", s.get("name", "?"), exc)
            continue
        mcp_servers.append(
            MCPServerConfig(
                name=s.get("name", "unnamed"),
                type=s.get("type", "stdio"),
                url=s.get("url"),
                command=s.get("command"),
                args=s.get("args"),
                headers=hdrs,
            )
        )

    return Config(
        telegram=telegram,
        general=general,
        providers=providers,
        titles=titles,
        stt=stt,
        tts=tts,
        cli=cli,
        mcp_servers=mcp_servers,
        config_dir=config_dir,
    )
