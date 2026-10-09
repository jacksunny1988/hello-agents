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


def load_mcp_config(config_path: str | Path) -> dict[str, StdioServerConfig]:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    servers = raw.get("servers", {})
    return {name: StdioServerConfig(**cfg) for name, cfg in servers.items()}
