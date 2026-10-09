from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class StdioServerConfig(BaseModel):
    """stdio_server 配置"""

    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class HttpServerConfig(BaseModel):
    """http_server 配置"""

    transport: Literal["http"] = "http"
    url: str
    headers: dict[str, str] | None = None


_SERVER_MODELS = {
    "stdio": StdioServerConfig,
    "http": HttpServerConfig,
}


def load_mcp_config(
    config_path: str | Path,
) -> dict[str, StdioServerConfig | HttpServerConfig]:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    servers = raw.get("servers", {})
    result: dict[str, StdioServerConfig | HttpServerConfig] = {}
    for name, cfg in servers.items():
        transport = cfg.get("transport", "stdio")
        model = _SERVER_MODELS[transport]
        result[name] = model(**cfg)
    return result
