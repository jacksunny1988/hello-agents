"""本地 Streamable HTTP MCP server，供 T9 集成测试连接。"""

import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("local-demo")


@server.tool()
def echo(message: str) -> str:
    """原样回显消息。"""
    return f"echo:{message}"


@server.tool()
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server.run("streamable-http", host="127.0.0.1", port=port)
