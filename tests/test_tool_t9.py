"""T9：MCP 远程接入——配置解析（stdio/http 分发 · headers 可选），离线确定性"""

from pathlib import Path

from hello_agents.tool.mcp._config import (
    HttpServerConfig,
    StdioServerConfig,
    load_mcp_config,
)


def test_load_http_config(tmp_path: Path):
    f = tmp_path / "mcp.yaml"
    f.write_text(
        "servers:\n  local_http:\n    transport: http\n"
        "    url: http://127.0.0.1:8765/mcp\n",
        encoding="utf-8",
    )
    configs = load_mcp_config(f)
    cfg = configs["local_http"]
    assert isinstance(cfg, HttpServerConfig)
    assert cfg.url == "http://127.0.0.1:8765/mcp"


def test_load_mixed_stdio_and_http(tmp_path: Path):
    f = tmp_path / "mcp.yaml"
    f.write_text(
        "servers:\n"
        "  everything:\n    transport: stdio\n    command: uvx\n"
        "    args: [mcp-server-everything]\n"
        "  web:\n    transport: http\n    url: http://x/sse\n",
        encoding="utf-8",
    )
    configs = load_mcp_config(f)
    assert isinstance(configs["everything"], StdioServerConfig)
    assert isinstance(configs["web"], HttpServerConfig)
    assert configs["web"].url.endswith("/sse")


def test_http_headers_optional():
    assert HttpServerConfig(url="http://x/mcp").headers is None
    cfg = HttpServerConfig(url="http://x/mcp", headers={"X": "y"})
    assert cfg.headers == {"X": "y"}
