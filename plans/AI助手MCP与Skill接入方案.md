# AI助手 MCP 与 Skill 接入方案

## 现状对照

| 目标能力 | 本项目现状 | 差距 |
|---|---|---|
| 外部数据接入 | 仅内置 24 个 `@tool`（市场/策略/组合/风控/分析），数据源只限本地库+efinance | 无外部服务（搜索/飞书/行情API）接入通道 |
| MCP 工具调用 | 无 | 无 `mcp` SDK 依赖，`_TOOL_REGISTRY` 不支持外部工具 |
| skill 文档注入 | 无 skill 概念，系统 prompt 静态拼接 | 无按需加载指令文档的机制 |

## 关键约束

- 新增依赖仅允许 `mcp`（Python SDK v1.x 稳定线，`pip install mcp` 默认 2.x，需 `<2` 上界）
- AI 助手架构不变：`AgentLoop` 仍走 OpenAI Function Calling，MCP 工具桥接进 `_TOOL_REGISTRY`
- 不引入重量级框架（无 Celery/Redis），不改变单 job 串行管道
- 耗时操作（MCP 连接、工具调用）必须有 try/except 和日志
- 配置文件敏感信息走 `.env`
- MCP server 未配置时系统功能不受影响（优雅降级）

## 模块结构

```
app/agent_core/
├── mcp_bridge.py       # 新增：MCP 客户端桥接（连接 server → 工具注册 → 执行转发）
├── skill_manager.py    # 新增：skill 文档管理（扫描/加载/注入）
app/tools/
├── registry.py         # 修改：get_tool_registry() 初始化时调用 mcp_bridge 注册 MCP 工具
app/agent_core/
├── loop.py             # 修改：SYSTEM_PROMPT 增加 skill 说明段；加载 skill 注册 load_skill 工具
skills/                 # 新增：skill 文档目录（Markdown，每个 skill 一个文件）
```

## MCP 桥接设计（app/agent_core/mcp_bridge.py）

### 配置（.env）

```
# MCP servers 配置（JSON），type: stdio / http
MCP_SERVERS=[{"name":"web_search","type":"http","url":"http://localhost:8001/mcp"},{"name":"lark","type":"stdio","command":"lark-mcp","args":[],"env":{}}]
```

### 核心类 `McpBridge`

```
__init__(): 从 settings 读 MCP_SERVERS，解析 JSON；无配置则 disabled=True
register_all(registry): 遍历 server → 连接 → list_tools → 转 ToolDef 注册进 _TOOL_REGISTRY
    - 工具名加 server 前缀：`{server_name}.{tool_name}`，避免跨 server 冲突
    - schema 转换：MCP inputSchema → 项目 ToolDef（OpenAI Function Calling 格式）
    - 连接失败/无 MCP server → 记录日志，跳过，不影响其余工具
execute(server_name, tool_name, arguments): 转发 call_tool，返回结果 dict
```

### 连接与执行

- `stdio`：`stdio_client(StdioServerParameters(command, args, env))` + `ClientSession`，启动本地子进程
- `http`：`streamablehttp_client(url, headers)` + `ClientSession`，走流式 HTTP
- 生命周期：每次调用即开即关（`async with`），避免长驻连接带来的僵尸进程；执行失败返回 `{"error": ...}`
- 同步封装：注册/执行均通过 `asyncio.run()` 包装（现有 AgentLoop 是同步代码）

### 桥接注册

在 `registry.py: get_tool_registry()` 内，完成内置工具注册后追加：

```
mcp_bridge = McpBridge()
mcp_bridge.register_all(_TOOL_REGISTRY)
```

MCP 工具经 `ToolDef` 包装后，与内置工具走同一条 `ToolRegistry.execute()` 路径，LLM 无需感知差异。

## Skill 管理设计（app/agent_core/skill_manager.py）

### 目录约定 `skills/{skill_name}.md`

```
---
name: web_search
description: 网络搜索外部信息（新闻、政策、数据），用于市场环境补充分析
---
<skill 正文：使用说明、调用建议、注意事项>
```

### 核心类 `SkillManager`

```
__init__(): 扫描 skills/ 目录，解析每个 md 的 frontmatter（name/description），构建 skill 索引
list_skills() -> [{name, description}]: 供 system prompt 注入 skill 说明列表
load_skill(name) -> str | None: 读取 skill 全文（正文部分），用于按需注入
```

### 注入机制（ReAct 风格）

1. **说明注入**：`SYSTEM_PROMPT` 增加一段 `可用技能`，列出 `SkillManager.list_skills()` 的 name+description，让 LLM 知道存在哪些 skill
2. **动态加载工具**：注册一个特殊工具 `load_skill(name)`（不走 MCP，由 SkillManager 实现）
   - LLM 判断需要某 skill 时调用 `load_skill("web_search")`
   - 工具执行返回 skill 正文，作为 tool result 回填给 LLM，指导其调用对应 MCP 工具
3. **执行链**：LLM 视角 = `load_skill` 获取指引 → 调用 MCP 桥接工具 `{server}.{tool}` 获取外部数据

## 接入点清单

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | dependencies 增加 `mcp>=1.28,<2` |
| `app/config.py` | Settings 增加 `mcp_servers`（.env 的 MCP_SERVERS） |
| `app/agent_core/mcp_bridge.py` | 新增 McpBridge |
| `app/agent_core/skill_manager.py` | 新增 SkillManager + load_skill 工具 |
| `app/tools/registry.py` | get_tool_registry() 中追加 MCP 注册 |
| `app/agent_core/loop.py` | SYSTEM_PROMPT 增加技能说明段；工具列表并入 load_skill |
| `app/routes/chat_routes.py` | /api/chat/tools 自动覆盖新工具（无需改动） |
| `.env.example` | 增加 MCP_SERVERS 示例 |
| `skills/` | 新增示例 skill 文档（可选，作为模板） |

## 实施步骤

1. 依赖 + 配置 → 验证: `pip install "mcp>=1.28,<2"` 成功，Settings 能读 MCP_SERVERS
2. SkillManager → 验证: 单测扫描 skill 目录、load_skill 返回正文、无 skill 目录时不报错
3. McpBridge → 验证: 用本地最小 MCP server（FastMCP echo 工具）测 stdio/http 连接、工具注册、执行转发
4. registry + loop 集成 → 验证: `get_openai_tools()` 包含 MCP 工具和 load_skill；SYSTEM_PROMPT 含技能段
5. 回归 → 验证: `pytest` 通过，无 MCP 配置时原有 24 工具不受影响

## 风险与降级

- MCP server 不可用：连接失败仅日志，工具不注册，LLM 仅提示"外部能力不可用"
- MCP 工具返回值过大：统一截断（沿用 loop.py 3000 字符策略）
- v1.x 与 2.x API 差异：锁定 `>=1.28,<2` 上界，方案基于 v1.29 实测 API（ClientSession/stdio_client/streamablehttp_client/list_tools/call_tool）
- skill 注入膨胀 system prompt：仅注入 name+description 摘要，全文按需 load_skill
