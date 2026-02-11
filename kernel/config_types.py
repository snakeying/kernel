from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
    type: str
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
class STTConfig:
    api_base: str
    api_key: str
    model: str = "whisper-1"
    headers: dict[str, str] | None = None


@dataclass
class TTSConfig:
    voice: str


@dataclass
class CLIConfig:
    command: str
    args: list[str] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    name: str
    type: str
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
    stt: STTConfig | None
    tts: TTSConfig | None
    cli: dict[str, CLIConfig]
    mcp_servers: list[MCPServerConfig]
    config_dir: Path

    @property
    def data_path(self) -> Path:
        return self.config_dir / self.general.data_dir

    @property
    def default_workspace_path(self) -> Path:
        return self.config_dir / self.general.default_workspace

