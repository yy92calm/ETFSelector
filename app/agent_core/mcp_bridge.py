"""MCP 桥接 - 将外部 MCP server 工具接入项目 Tool Registry

支持两种 server 类型：
- stdio: 本地子进程启动（command/args/env）
- http: 流式 HTTP endpoint（url/headers）

设计（对齐 deepseek-harness mcp-client 模式）：
- 工具名 `mcp__<server>__<raw>`，规避部分 OpenAI 兼容端 function name 字符集
  对点号的限制；旧名 `{server}.{tool}` 同步注册为兼容别名（指向同一 ToolDef）
- 长驻会话模式（默认）：ClientSession 在后台事件循环线程内保活，调用失败销毁
  并按有界退避（1s 起步、30s 封顶）重建，连续失败达上限后停用该 server 并摘除
  其工具；回退调用成功时自动恢复注册。可配置回退为「连接即开即关」
- 单次工具调用统一超时（asyncio.wait_for），超时返回 error dict
- MCP server 未配置/连接失败时优雅降级，不影响内置工具
"""

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

from app.config import get_settings
from app.tools.registry import ToolDef

logger = logging.getLogger(__name__)

# server 名约束（对齐 harness SERVER_NAME_PATTERN）：保住 mcp__<name>__<tool> 命名预算
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# 长驻会话建立超时（秒）
_SESSION_OPEN_TIMEOUT_S = 30
# 连续失败达该次数后停用 server（摘除工具），回退调用成功可恢复
_MAX_CONSECUTIVE_FAILURES = 10
_BACKOFF_BASE_S = 1.0
_BACKOFF_MAX_S = 30.0


def public_tool_name(server_name: str, raw_name: str) -> str:
    """MCP 工具的模型侧公开名：mcp__<server>__<raw>"""
    return f"mcp__{server_name}__{raw_name}"


def legacy_tool_name(server_name: str, raw_name: str) -> str:
    """旧命名（{server}.{tool}）：作为兼容别名保留一个版本"""
    return f"{server_name}.{raw_name}"


class _BackgroundLoop:
    """后台事件循环线程：长驻 MCP 会话的宿主，也承载运行中 loop 内的协程提交"""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, daemon=True, name="mcp-bg-loop"
                )
                self._thread.start()
            return self._loop

    def run(self, coro, timeout: Optional[float] = None):
        """在后台 loop 上执行协程并阻塞取回结果（timeout 秒）"""
        fut = asyncio.run_coroutine_threadsafe(coro, self.ensure())
        return fut.result(timeout=timeout)


_bg_loop = _BackgroundLoop()


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


class _ServerRuntime:
    """单个 MCP server 的运行时状态：长驻会话 + 重连退避"""

    def __init__(self, server: Dict):
        self.server = server
        self.session: Any = None          # 长驻 ClientSession（宿主在后台 loop）
        self._transport_cm: Any = None    # 配套传输上下文（负责子进程/连接生命周期）
        self.consecutive_failures = 0
        self.disabled = False
        self.next_retry_at = 0.0

    def in_backoff(self) -> bool:
        return time.monotonic() < self.next_retry_at

    def record_failure(self) -> None:
        """记录一次失败：递增退避窗口，达到上限停用"""
        self.consecutive_failures += 1
        delay = min(_BACKOFF_MAX_S, _BACKOFF_BASE_S * (2 ** (self.consecutive_failures - 1)))
        self.next_retry_at = time.monotonic() + delay
        if self.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            self.disabled = True
            logger.error(
                f"MCP server [{self.server.get('name')}] 连续失败 "
                f"{self.consecutive_failures} 次，已停用并摘除工具；"
                f"回退调用成功可自动恢复"
            )


# 旧名兼容别名（per-server）：{server_name: {legacy_name: ToolDef}}
_ALIAS_REGISTRY: Dict[str, Dict[str, ToolDef]] = {}


class McpBridge:
    """MCP 客户端桥接"""

    def __init__(self, servers_config: Optional[str] = None):
        config = servers_config if servers_config is not None else get_settings().mcp_servers
        self.servers = self._parse_servers(config)
        self.tool_defs: List[ToolDef] = []
        self.disabled = len(self.servers) == 0
        self._persistent = bool(getattr(get_settings(), "mcp_persistent_session", True))
        self._tool_call_timeout_s = getattr(get_settings(), "mcp_tool_call_timeout_ms", 60000) / 1000.0
        self._runtimes: Dict[str, _ServerRuntime] = {
            s["name"]: _ServerRuntime(s) for s in self.servers
        }
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
            if not _SERVER_NAME_RE.match(name):
                logger.warning(
                    f"跳过 MCP server [{name}]：名称需匹配 [A-Za-z0-9_-]{{1,32}}"
                )
                continue
            # read_tools: 显式声明为只读的工具名（不带 server 前缀），其余按写操作处理（需审批）
            read_tools = set(item.get("read_tools") or [])
            servers.append({"name": name, "type": stype, "read_tools": read_tools, **item})
        return servers

    # ---- 发现与注册 ----

    def _discover(self):
        """连接所有 server，列出工具，生成 ToolDef"""
        for server in self.servers:
            self._register_server_tools(server)

    def _register_server_tools(self, server: Dict) -> int:
        """发现单个 server 的工具并注册（含旧名兼容别名），返回注册数

        重复注册（恢复场景）按公开名覆盖，不累积重复项。
        """
        name = server["name"]
        try:
            tools = self._run_coroutine(
                asyncio.wait_for(self._list_tools(server), timeout=_SESSION_OPEN_TIMEOUT_S)
            )
        except Exception as e:
            logger.error(f"MCP server [{name}] 连接/发现失败: {e}", exc_info=True)
            return 0

        read_tools = server.get("read_tools") or set()
        registered = 0
        existing_names = {t.name for t in self.tool_defs}
        for tool in tools:
            params = tool.inputSchema or {"type": "object", "properties": {}}
            is_read = tool.name in read_tools
            tool_def = ToolDef(
                name=public_tool_name(name, tool.name),
                description=tool.description or "",
                func=self._make_func(name, tool.name),
                parameters=params,
                risk_level="read" if is_read else "write",
                requires_approval=not is_read,
            )
            if tool_def.name in existing_names:
                self.tool_defs = [t for t in self.tool_defs if t.name != tool_def.name]
            self.tool_defs.append(tool_def)
            existing_names.add(tool_def.name)
            # 旧命名别名指向同一 ToolDef（description 标记 deprecated）
            legacy_def = ToolDef(
                name=legacy_tool_name(name, tool.name),
                description=f"[deprecated: 改用 {tool_def.name}] " + (tool.description or ""),
                func=tool_def.func,
                parameters=params,
                risk_level=tool_def.risk_level,
                requires_approval=tool_def.requires_approval,
            )
            _ALIAS_REGISTRY.setdefault(name, {})[legacy_def.name] = legacy_def
            registered += 1
        logger.info(f"MCP server [{name}] 注册 {registered} 个工具（含旧名别名）")
        return registered

    def register_all(self, registry: Dict[str, ToolDef]):
        """将 MCP 工具注册进项目工具注册表（新名 + 旧名别名）"""
        for tool_def in self.tool_defs:
            registry[tool_def.name] = tool_def
        for aliases in _ALIAS_REGISTRY.values():
            for legacy_name, legacy_def in aliases.items():
                registry.setdefault(legacy_name, legacy_def)

    def _unregister_server_tools(self, server_name: str, registry: Dict[str, ToolDef]):
        """从项目注册表摘除某 server 的全部工具（新名与别名）"""
        prefix_new = f"mcp__{server_name}__"
        prefix_old = f"{server_name}."
        for key in [k for k in registry if k.startswith(prefix_new) or k.startswith(prefix_old)]:
            registry.pop(key, None)

    def _make_func(self, server_name: str, tool_name: str):
        """生成 ToolDef 执行函数（兼容 registry 的 db 注入）"""
        def _call(db: Any = None, **kwargs) -> Dict:
            return self.call_tool(server_name, tool_name, kwargs)
        return _call

    # ---- 调用 ----

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict) -> Dict:
        """调用指定 MCP server 的工具，返回结果 dict

        长驻模式：复用后台 loop 上的 ClientSession；失败销毁会话、按退避重连，
        并降级一次「连接即开即关」调用。连续失败达上限停用并摘除工具，
        回退调用成功时自动恢复。
        """
        server = next((s for s in self.servers if s["name"] == server_name), None)
        if not server:
            return {"error": f"未知 MCP server: {server_name}"}
        rt = self._runtimes.setdefault(server_name, _ServerRuntime(server))
        timeout = self._tool_call_timeout_s

        if self._persistent:
            try:
                if not rt.disabled and not rt.in_backoff():
                    session = self._acquire_session(rt)
                    result = self._run_coroutine(
                        asyncio.wait_for(session.call_tool(tool_name, arguments), timeout=timeout),
                        bg=True,
                    )
                    rt.consecutive_failures = 0
                    return _result_to_dict(result)
            except Exception as e:
                logger.warning(f"MCP 长驻会话调用失败 [{server_name}.{tool_name}]: {e}")
                self._drop_session(rt)
                rt.record_failure()
                if rt.disabled:
                    self._disable_server(server_name)
                # 落到下方回退路径（受退避窗口保护）

        # 回退路径：连接即开即关；server 停用后也走此路径试探恢复
        try:
            if rt.in_backoff():
                return {"error": f"MCP server [{server_name}] 处于重连退避窗口，请稍后重试"}
            result = self._run_coroutine(
                asyncio.wait_for(self._call_tool(server, tool_name, arguments), timeout=timeout)
            )
            # 回退成功：重置失败计数；停用状态恢复并重新注册工具
            if rt.disabled or rt.consecutive_failures > 0:
                was_disabled = rt.disabled
                rt.consecutive_failures = 0
                rt.disabled = False
                rt.next_retry_at = 0.0
                if was_disabled:
                    self._register_server_tools(server)
                    self._resync_registry(server_name)
                    logger.info(f"MCP server [{server_name}] 已恢复并重新注册工具")
            return _result_to_dict(result)
        except Exception as e:
            rt.record_failure()
            if rt.disabled:
                self._disable_server(server_name)
            logger.error(f"MCP 工具 {server_name}.{tool_name} 调用失败: {e}")
            return {"error": f"MCP 工具调用失败: {str(e)}"}

    # ---- 长驻会话管理（宿主在后台 loop） ----

    def _acquire_session(self, rt: _ServerRuntime):
        """获取（必要时重建）长驻会话；返回 ClientSession"""
        if rt.session is not None:
            return rt.session
        session = _bg_loop.run(self._open_persistent(rt), timeout=_SESSION_OPEN_TIMEOUT_S)
        rt.session = session
        rt.consecutive_failures = 0
        rt.next_retry_at = 0.0
        return session

    async def _open_persistent(self, rt: _ServerRuntime):
        """在后台 loop 内打开长驻会话（手工进出异步上下文以保持传输存活）"""
        server = rt.server
        cm = _make_stdio_client(server) if server["type"] == "stdio" else _make_http_client(server)
        read, write = await cm.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        rt._transport_cm = cm
        return session

    def _drop_session(self, rt: _ServerRuntime):
        """销毁长驻会话（后台 loop 内清理，失败静默）"""
        session, cm = rt.session, rt._transport_cm
        rt.session, rt._transport_cm = None, None
        if session is None and cm is None:
            return

        async def _close():
            for obj in (session, cm):
                if obj is None:
                    continue
                try:
                    await obj.__aexit__(None, None, None)
                except Exception:
                    pass

        try:
            _bg_loop.run(_close(), timeout=5)
        except Exception:
            pass

    def _disable_server(self, server_name: str):
        """停用 server：从项目注册表摘除其工具"""
        try:
            from app.tools.registry import _TOOL_REGISTRY
            self._unregister_server_tools(server_name, _TOOL_REGISTRY)
        except Exception as e:
            logger.warning(f"MCP server [{server_name}] 工具摘除失败: {e}")

    def _resync_registry(self, server_name: str):
        """恢复时把该 server 的工具（新名与别名）同步回项目注册表（若注册表已初始化）"""
        try:
            from app.tools.registry import _TOOL_REGISTRY
            if not _TOOL_REGISTRY:
                return
            prefix = f"mcp__{server_name}__"
            for tool_def in self.tool_defs:
                if tool_def.name.startswith(prefix):
                    _TOOL_REGISTRY[tool_def.name] = tool_def
            for legacy_name, legacy_def in _ALIAS_REGISTRY.get(server_name, {}).items():
                _TOOL_REGISTRY.setdefault(legacy_name, legacy_def)
        except Exception:
            pass

    # ---- 协程执行 ----

    @staticmethod
    def _run_coroutine(coro, timeout: Optional[float] = None, bg: bool = False):
        """同步执行协程。

        bg=True：固定提交到后台 loop（长驻会话宿主在那，跨 loop 调用会挂）。
        bg=False：当前线程已有运行中的事件循环时同样提交后台 loop
        （asyncio.run 不允许嵌套），否则直接 asyncio.run。
        timeout 同时作为 fut.result 的兜底上限。
        """
        if bg:
            return _bg_loop.run(coro, timeout=timeout)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        return _bg_loop.run(coro, timeout=timeout)

    async def _list_tools(self, server: Dict):
        """连接 server 并列出工具（连接即开即关）"""
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
        """连接 server 并调用工具（连接即开即关）"""
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


def get_mcp_bridge() -> "McpBridge":
    global _bridge
    if not _MCP_AVAILABLE:
        logger.warning("MCP SDK 未安装，MCP 桥接降级为不可用")
        return None
    if _bridge is None:
        _bridge = McpBridge()
    return _bridge
