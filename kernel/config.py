"""Configuration loader for Kernel.

Loads ``config.toml`` (or ``$KERNEL_CONFIG``), applies env-var overrides for
sensitive fields, and validates required values at startup.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 12):
    import tomllib
else:
    import tomli as tomllib

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TelegramConfig:
    token: str
    allowed_user: int


@dataclass
class GeneralConfig:
    timezone: str = "Asia/Shanghai"
    default_provider: str = "anthropic"
    default_workspace: str = "."
    context_rounds: int = 50
    memory_recall_k: int = 5
    data_dir: str = "data"


@dataclass
class ProviderConfig:
    name: str
    type: str  # "claude" | "openai_compat"
    api_key: str
    default_model: str
    models: list[str]
    api_base: str | None = None
    max_tokens: int | None = None
    headers: dict[str, str] | None = None


@dataclass
class TitlesConfig:
    type: str
    api_base: str
    api_key: str
    model: str
    max_tokens: int = 100
    headers: dict[str, str] | None = None


@dataclass
class CLIConfig:
    command: str
    args: list[str] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    name: str
    type: str  # "http" | "stdio"
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    headers: dict[str, str] | None = None


@dataclass
class Config:
    telegram: TelegramConfig
    general: GeneralConfig
    providers: dict[str, ProviderConfig]
    titles: TitlesConfig | None
    cli: dict[str, CLIConfig]
    mcp_servers: list[MCPServerConfig]
    config_dir: Path  # directory containing config.toml (path base)

    @property
    def data_path(self) -> Path:
        return self.config_dir / self.general.data_dir

    @property
    def default_workspace_path(self) -> Path:
        return self.config_dir / self.general.default_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENV_RE = re.compile(r"\$\{(\w+)\}")


def _expand_env(value: str) -> str:
    """Replace ``${VAR}`` placeholders with environment variable values."""
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
        try:
            expanded[k] = _expand_env(v) if "${" in v else v
        except ValueError:
            raise
    return expanded


def _provider_env_key(provider_name: str) -> str:
    """``anthropic`` → ``KERNEL_PROVIDER_ANTHROPIC_API_KEY``."""
    sanitised = re.sub(r"[^A-Za-z0-9]", "_", provider_name).upper()
    return f"KERNEL_PROVIDER_{sanitised}_API_KEY"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate config.toml, returning a :class:`Config` instance."""

    if path is None:
        path = os.environ.get("KERNEL_CONFIG", "config.toml")
    path = Path(path).resolve()

    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")

    with open(path, "rb") as f:
        raw: dict[str, Any] = tomllib.load(f)

    config_dir = path.parent

    # -- Telegram --------------------------------------------------------
    tg_raw = raw.get("telegram", {})
    tg_token = os.environ.get("KERNEL_TELEGRAM_TOKEN") or tg_raw.get("token", "")
    tg_user = tg_raw.get("allowed_user", 0)
    if not tg_token or tg_token == "YOUR_BOT_TOKEN":
        raise SystemExit(
            "telegram.token is required. Set it in config.toml or env KERNEL_TELEGRAM_TOKEN."
        )
    if not tg_user:
        raise SystemExit(
            "telegram.allowed_user is required. Use @userinfobot on Telegram to get your user ID."
        )
    telegram = TelegramConfig(token=tg_token, allowed_user=int(tg_user))

    # -- General ---------------------------------------------------------
    g = raw.get("general", {})
    general = GeneralConfig(
        timezone=g.get("timezone", "Asia/Shanghai"),
        default_provider=g.get("default_provider", "anthropic"),
        default_workspace=g.get("default_workspace", "."),
        context_rounds=g.get("context_rounds", 50),
        memory_recall_k=g.get("memory_recall_k", 5),
        data_dir=g.get("data_dir", "data"),
    )

    # -- Providers -------------------------------------------------------
    providers: dict[str, ProviderConfig] = {}
    for name, praw in raw.get("providers", {}).items():
        env_key = _provider_env_key(name)
        api_key = os.environ.get(env_key) or praw.get("api_key", "")
        ptype = praw.get("type", "openai_compat")
        max_tokens = praw.get("max_tokens")

        # Claude requires max_tokens
        if ptype == "claude" and max_tokens is None:
            raise SystemExit(
                f"providers.{name}.max_tokens is required for Claude providers."
            )

        try:
            headers = _expand_headers(praw.get("headers"))
        except ValueError as exc:
            log.warning("Provider %s headers: %s — skipping headers", name, exc)
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

    # Validate default provider
    if general.default_provider not in providers:
        raise SystemExit(
            f"Default provider '{general.default_provider}' not found in [providers]."
        )
    default_prov = providers[general.default_provider]
    if not default_prov.api_key or default_prov.api_key.startswith("sk-ant-...") or default_prov.api_key == "sk-...":
        raise SystemExit(
            f"API key for default provider '{general.default_provider}' is required."
        )

    # -- Titles ----------------------------------------------------------
    titles: TitlesConfig | None = None
    if "titles" in raw:
        t = raw["titles"]
        t_api_key = os.environ.get("KERNEL_TITLES_API_KEY") or t.get("api_key", "")
        if t_api_key and t_api_key != "sk-...":
            try:
                t_headers = _expand_headers(t.get("headers"))
            except ValueError as exc:
                log.warning("Titles headers: %s — skipping headers", exc)
                t_headers = None
            titles = TitlesConfig(
                type=t.get("type", "openai_compat"),
                api_base=t.get("api_base", ""),
                api_key=t_api_key,
                model=t.get("model", ""),
                max_tokens=t.get("max_tokens", 100),
                headers=t_headers,
            )
        else:
            log.warning("Titles API key not set — auto-title disabled.")

    # -- CLI -------------------------------------------------------------
    cli: dict[str, CLIConfig] = {}
    for name, c in raw.get("cli", {}).items():
        cli[name] = CLIConfig(command=c.get("command", name), args=c.get("args", []))

    # -- MCP servers -----------------------------------------------------
    mcp_servers: list[MCPServerConfig] = []
    for s in raw.get("mcp", {}).get("servers", []):
        try:
            hdrs = _expand_headers(s.get("headers"))
        except ValueError as exc:
            log.warning("MCP server '%s' headers: %s — skipping server", s.get("name", "?"), exc)
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
        cli=cli,
        mcp_servers=mcp_servers,
        config_dir=config_dir,
    )
