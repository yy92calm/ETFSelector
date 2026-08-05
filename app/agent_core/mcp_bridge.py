"""MCP 桥接 - 将外部 MCP server 工具接入项目 Tool Registry

支持两种 server 类型：
- stdio: 本地子进程启动（command/args/env）
- http: 流式 HTTP endpoint（url/headers）

设计：
- 工具名加 server 前缀 `{server}.{tool}`，避免跨 server 冲突
- 连接即开即关（async with），每次调用独立建连，避免僵尸进程
- MCP server 未配置/连接失败时优雅降级，不影响内置工具
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from app.config import get_settings
from app.tools.registry import ToolDef

logger = logging.getLogger(__name__)


def _result_to_dict(result) -> Dict[str, Any]:
    """将 CallToolResult 序列化为可 JSON 化的 dict"""
    is_error = bool(getattr(result, "isError", False))
    structured = getattr(result, "structuredContent", None)
    if structured:
        return {"structured_content": structured, "is_error": is_error}
    texts = []
    for block in (result.content or []):
        if getattr(block, "type", None) == "text":
            texts.append(getattr(block, "text", ""))
    return {"content": "\n".join(texts), "is_error": is_error}


def _make_stdio_client(server: Dict):
    """构建 stdio 传输上下文"""
    params = StdioServerParameters(
        command=server.get("command", ""),
        args=server.get("args", []),
        env=server.get("env"),
        cwd=server.get("cwd"),
    )
    return stdio_client(params)


def _make_http_client(server: Dict):
    """构建 http 流式传输上下文"""
    return streamablehttp_client(
        server.get("url", ""),
        headers=server.get("headers"),
        timeout=server.get("timeout", 30),
    )


class McpBridge:
    """MCP 客户端桥接"""

    def __init__(self, servers_config: Optional[str] = None):
        config = servers_config if servers_config is not None else get_settings().mcp_servers
        self.servers = self._parse_servers(config)
        self.tool_defs: List[ToolDef] = []
        self.disabled = len(self.servers) == 0
        if not self.disabled:
            self._discover()

    def _parse_servers(self, config: str) -> List[Dict]:
        """解析 MCP_SERVERS 配置字符串"""
        if not config or not config.strip():
            return []
        try:
            data = json.loads(config)
        except json.JSONDecodeError as e:
            logger.error(f"MCP_SERVERS 配置解析失败: {e}")
            return []
        servers = []
        items = data if isinstance(data, list) else [data]
        for item in items:
            name = item.get("name", "")
            stype = item.get("type", "")
            if not name or stype not in ("stdio", "http"):
                logger.warning(f"跳过无效 MCP server 配置: {item}")
                continue
            servers.append({"name": name, "type": stype, **item})
        return servers

    def _discover(self):
        """连接所有 server，列出工具，生成 ToolDef"""
        for server in self.servers:
            try:
                tools = self._run_coroutine(self._list_tools(server))
                for tool in tools:
                    prefixed = f"{server['name']}.{tool.name}"
                    params = tool.inputSchema or {"type": "object", "properties": {}}
                    self.tool_defs.append(ToolDef(
                        name=prefixed,
                        description=tool.description or "",
                        func=self._make_func(server["name"], tool.name),
                        parameters=params,
                    ))
                logger.info(f"MCP server [{server['name']}] 注册 {len(tools)} 个工具")
            except Exception as e:
                logger.error(f"MCP server [{server['name']}] 连接/发现失败: {e}", exc_info=True)

    def _make_func(self, server_name: str, tool_name: str):
        """生成 ToolDef 执行函数（兼容 registry 的 db 注入）"""
        def _call(db: Any = None, **kwargs) -> Dict:
            return self.call_tool(server_name, tool_name, kwargs)
        return _call

    def register_all(self, registry: Dict[str, ToolDef]):
        """将 MCP 工具注册进项目工具注册表"""
        for tool_def in self.tool_defs:
            registry[tool_def.name] = tool_def

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict) -> Dict:
        """调用指定 MCP server 的工具，返回结果 dict"""
        server = next((s for s in self.servers if s["name"] == server_name), None)
        if not server:
            return {"error": f"未知 MCP server: {server_name}"}
        try:
            result = self._run_coroutine(self._call_tool(server, tool_name, arguments))
            return _result_to_dict(result)
        except Exception as e:
            logger.error(f"MCP 工具 {server_name}.{tool_name} 调用失败: {e}")
            return {"error": f"MCP 工具调用失败: {str(e)}"}

    @staticmethod
    def _run_coroutine(coro):
        """同步包装异步协程（AgentLoop 为同步代码）"""
        return asyncio.run(coro)

    async def _list_tools(self, server: Dict):
        """连接 server 并列出工具"""
        if server["type"] == "stdio":
            async with _make_stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    return resp.tools
        else:
            async with _make_http_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    return resp.tools

    async def _call_tool(self, server: Dict, tool_name: str, arguments: Dict):
        """连接 server 并调用工具"""
        if server["type"] == "stdio":
            async with _make_stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(tool_name, arguments)
        else:
            async with _make_http_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(tool_name, arguments)


# 模块级单例
_bridge: Optional[McpBridge] = None


def get_mcp_bridge() -> McpBridge:
    global _bridge
    if _bridge is None:
        _bridge = McpBridge()
    return _bridge
