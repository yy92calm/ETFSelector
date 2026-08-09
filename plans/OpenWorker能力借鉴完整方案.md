# OpenWorker 能力借鉴完整方案

借鉴 OpenWorker-Core-Reference（coworker 包）剩余可复用能力，在已落地的「侧边栏实时工具事件流」之上，
把事件流从「展示」升级为「人机协作控制 + 长会话质量」层。共六项能力，分六阶段，一次实施。

> 范围确认：P5 Provider 抽象由「可裁剪」改为「纳入」；新增 P6 中断控制（侧边栏停止按钮）。

## 实施状态（2026-08-09 更新）

| 阶段 | 状态 | 验证 |
|---|---|---|
| P1 权限审批 | ✅ 已实施 | 四场景（once/deny/discuss/interrupt）mock 通过；`/api/chat/approve` 注册 |
| P2 docstring schema | ✅ 已实施 | `_parse_docstring_params` + `_unwrap_annotated`，schema 含 description/default |
| P3 上下文压缩 | ✅ 已实施 | 触发→摘要保存→出站视图收敛（8轮）；canonical 历史未删（82条） |
| P4 低风险并行 | ✅ 已实施 | 并行+审批联动 mock 通过；事件顺序稳定 |
| P5 Provider 抽象 | ✅ 已实施 | 别名最长前缀解析 + 会话切模型 mock 通过（qwen-max→对应 base_url） |
| P6 中断控制 | ✅ 已实施 | `/api/chat/stop` + 迭代边界软中断，未执行 tool_call 回填 error |

回归：pytest 28 通过（`test_full_debate_flow` 为既有失败，与本次改动无关）；`node --check` 通过；全部新路由注册确认。

## 现状对照

| 能力 | 现状 | 借鉴来源 | 阶段 |
|---|---|---|---|
| 权限审批 | 对话中所有工具自动执行，`execute_rebalance`/`create_strategy`/`delete_strategy` 等写操作零确认 | `risk.py` + `permissions.py` + `engine.py::_authorize` | P1 |
| MCP 读写门控 | `mcp_bridge.py` 全量注册、全自动执行，无读写分类 | 学习笔记「approval_for_tool 按 read/write 分类裁决」 | P1 |
| 工具 schema | `registry.py` 仅类型注解生成，参数无 description，docstring 不参与 | `tools/registry.py`（docstring → schema） | P2 |
| 上下文压缩 | `ChatMemory.MAX_HISTORY_ROUNDS=20` 硬截断，早期意图/决策丢失 | `compaction.py`（摘要 + 只改出站视图） | P3 |
| 低风险并行 | 多轮工具全串行 | `engine.py::_parallel_safe` | P4 |
| Provider 抽象 | 写死 OpenAI chat.completions 单模型 | `providers/`（路由 + 切模型） | P5 |
| 中断控制 | turn 运行中无法停止，只能等全部跑完 | `engine.py` 中断模型（未执行 tool_call 回填 tool-error） | P6 |

## 关键约束

- 不引入新重量级依赖（无 Redis/Celery/websocket），审批通道用「SSE 事件 + 独立审批端点 + 进程内 pending 表」实现
- 现有 `run_autonomous()`（定时管道）保持全自动，不接入审批，逻辑不动
- 旧 `POST /api/chat` 保留为回退路径，但改用只读模式（DISCUSS），写操作明确拒绝
- 事件流契约向后兼容：新增 `permission_required` 事件，不破坏既有 `tool_started/finished` 结构
- 工具风险分级尽量不改 29 个既有工具源码，用「按名称默认表」覆盖（与 chat.js `mutatingTools` 集合对齐）

---

## 阶段一 P1：权限与审批引擎（核心）

### 模块结构

```
app/agent_core/
  permissions.py      # RiskClass / Mode / Decision / PermissionEngine（精简移植）
  approvals.py        # PendingApproval + 进程内 store（threading.Event 等待审批）
app/routes/chat_routes.py   # + POST /api/chat/approve
static/js/chat.js           # 审批卡片渲染 + 回调
static/css/workbench.css    # 审批卡片样式
```

### 1. 工具风险元数据（app/tools/registry.py）

- `ToolDef` 增加 `risk_level: str = "read"`、`requires_approval: bool = False`
- `tool()` 装饰器支持 `risk="read"|"write"` 显式声明
- 未显式声明时按默认写操作表归类（单点真源，与 chat.js `mutatingTools` 对齐）：
  ```
  _WRITE_TOOLS = {
    create_strategy, update_allocation, pause_strategy, resume_strategy,
    add_etf_to_pool, run_backtest, run_multi_agent_analysis,
    execute_rebalance, delete_strategy,
  }
  ```
- MCP 工具：默认 write（require approval），除非 server 配置声明 `read_tools`

### 2. PermissionEngine（app/agent_core/permissions.py）

裁剪 OpenWorker 五模式为三模式：
- `RiskClass: READ / WRITE`
- `Mode: DISCUSS`（只读问答，写拒绝）/ `INTERACTIVE`（默认：读放行、写问用户）/ `AUTO`（全放行，保留给未来自动化）
- `Decision: allowed / reason / needs_user`
- `evaluate(tool_name, metadata) -> Decision`：DISCUSS 拒写、AUTO 全放、INTERACTIVE 读放写问、session 放行清单优先
- `allow_tool_for_session(name)`：本会话放行

### 3. 审批通道（app/agent_core/approvals.py）

- `PendingApproval`: `request_id / tool / arguments / created_at / event(threading.Event) / outcome`
- `store.create() / store.resolve(request_id, outcome) / store.get()`
- 超时（`chat_approval_timeout`，默认 120s）自动 deny
- 进程内内存表，不持久化；并发安全用 `threading.Lock`

### 4. AgentLoop 集成（app/agent_core/loop.py `_iter_events`）

- 执行工具前 `permissions.evaluate(tool_name, metadata)`
- `allowed` → 照常执行
- `needs_user` →
  - `store.create()` 建 pending
  - yield `{"type": "permission_required", "data": {"request_id", "tool", "arguments", "reason"}}`
  - `event.wait(timeout)` 阻塞等待（SSE 生成器在 threadpool 线程，不卡事件循环）
  - outcome：`once` → 执行本次；`always` → `allow_tool_for_session` 后执行；`deny`/超时 → 结果回填 `{"error": "用户拒绝授权"}` 写入 history（模型可见失败原因，关键不变量与 OpenWorker 一致）
- 被拒工具同样发 `tool_finished(status="error")`，前端气泡标红

### 5. 路由

- 新 `POST /api/chat/approve`：`{request_id, outcome}` → `store.resolve()` → APIResponse
- 旧 `POST /api/chat`：`mode=DISCUSS`，写操作明确拒绝（不阻塞、不等待），安全兜底
- 新 `POST /api/chat/stream`：`mode=INTERACTIVE`，走审批卡片

### 6. 侧边栏审批 UI（chat.js / css）

- 新事件 `permission_required` → 在工具气泡区插审批卡片：工具名 + 参数摘要 + 三按钮「允许本次 / 本会话允许 / 拒绝」
- 点击 → `POST /api/chat/approve` → 卡片转「已批准 / 已拒绝」
- 等待中气泡显示「等待授权…」，超时/拒绝标红

### 7. MCP 读写门控（app/agent_core/mcp_bridge.py）

- `_parse_servers` 支持 server 级可选 `read_tools: [...]`
- `_discover` 建 ToolDef 时：`risk = "read" if name in read_tools else "write"`，write 必审批
- 与 PermissionEngine 联动，无需额外逻辑

---

## 阶段二 P2：工具 schema docstring 增强（小改动大收益）

### app/tools/registry.py

- `_parse_docstring_params(func)`：正则解析 docstring `Args:`/`参数:` 段（`name: 描述`），填进 JSON schema `properties[n].description`
- `get_type_hints(..., include_extras=True)` 支持 `Annotated[str, "描述"]` 参数级描述
- 无 docstring 维持现状（description 缺省），全兼容

### 验证

对比 `get_openai_tools()` 输出，参数带 description 后 LLM 选工具/填参准确率提升。

---

## 阶段三 P3：上下文自动压缩

### 模块

```
app/agent_core/compaction.py   # estimate_tokens / summarize / trim（精简移植，不引入 tiktoken）
app/models/chat.py             # ChatSession + context_summary TEXT（init_db ALTER TABLE 迁移）
app/agent_core/memory.py       # get_summary / save_summary
app/agent_core/loop.py         # 每轮循环前检查 _compaction_due
app/config.py                  # + context_window_tokens / compaction_threshold / compaction_min_tokens
```

### 设计

- 触发：`estimate_tokens(history) >= min(0.8 × context_window_tokens, cap)`；低于 `compaction_min_tokens` 不触发
- 压缩：LLM 生成结构化摘要（意图 / 关键决策含 WHY / 已动作 / 当前持仓状态 / 待办）；失败降级为 trim 最近 N 条
- 只改出站视图：canonical `ChatMessage` 历史不动，`context_summary` 存在会话级字段；`get_history` 返回「摘要 + 最近消息」
- 用户消息逐字保留最近 20 条（意图地面真相，不依赖摘要记忆）
- `_iter_events` 每轮循环开始前检查触发，压缩时 yield `{"type": "compacting"}` 事件（侧边栏显示「整理上下文…」瞬时信号）

---

## 阶段四 P4：低风险工具并行

### app/agent_core/loop.py `_iter_events`

- 同一轮内 `risk=="read"` 且免审批的 tool_calls 用 `concurrent.futures.ThreadPoolExecutor(max_workers=4)` 并行执行
- 先逐个 yield `tool_started`，全部完成后按调用顺序 yield `tool_finished`（结果按序收集，事件顺序稳定）
- 写工具保持串行（严格按调用顺序）
- SQLite 已配 `check_same_thread=False`，并发读安全

---

## 阶段五 P5：Provider 抽象 + 会话切模型

### 模块

```
app/agent_core/provider.py   # ProviderClient 最小接口 + 按前缀路由 base_url
app/routes/chat_routes.py    # + POST /api/chat/model
app/models/chat.py           # ChatSession + model VARCHAR（迁移）
static/js/chat.js            # 会话面板模型选择
```

### 设计

- `ProviderClient.complete(messages, tools, temperature, max_tokens)`，默认 OpenAI 兼容实现（复用现有 SDK）
- 模型前缀路由：`dashscope:` / `deepseek:` / `ollama:` → 对应 base_url；裸模型名 → settings 默认
- 会话级 `model` 字段，切模型只改字段（借鉴「存储格式统一，换模型只改一个字段」）
- v1 不做 vision/pdf 能力降级（项目纯文本场景），仅声明 tools 能力

---

## 阶段六 P6：中断控制（侧边栏停止按钮）

### 模块

```
app/agent_core/loop.py       # 迭代边界检查中断标志，未执行 tool_call 回填 tool-error
app/routes/chat_routes.py    # + POST /api/chat/stop
static/js/chat.js            # 流式期间显示停止按钮
static/css/workbench.css     # 停止按钮样式
```

### 设计

- 模块级 `INTERRUPT_FLAGS: dict[session_id, bool]` + `request_interrupt(session_id)` / `check_interrupted(session_id)`
- `_iter_events` 每轮迭代开始前、每个工具执行前检查标志：
  - 已中断 → 未执行的本轮 tool_calls 逐条回填 `{"error": "用户中断"}` 写入 history（关键不变量，防孤儿 tool_calls）
  - yield `{"type": "interrupted"}` 后提前终止，不发 assistant_message
- 软中断：LLM 调用/工具执行本身不打断，在边界生效（与 OpenWorker 的 asyncio 硬中断相比更简单，够用）
- 路由 `POST /api/chat/stop`：`{session_id}` → 置标志
- 前端：`tool_started` 首次出现时显示停止按钮，点击 → POST stop → 置「已停止」

---

## 配置与数据改动汇总

### Settings 新增（app/config.py）

| 键 | 默认 | 说明 |
|---|---|---|
| `chat_approval_timeout` | `120` | 审批等待超时秒数 |
| `context_window_tokens` | `128000` | 模型上下文窗口，用于压缩触发 |
| `compaction_threshold` | `0.8` | 压缩触发比例 |
| `compaction_min_tokens` | `20000` | 低于此值不压缩 |
| `llm_model_aliases` | `""` | 模型前缀 → base_url 路由表（JSON） |

### 模型新增字段（init_db ALTER TABLE 迁移）

| 表 | 字段 | 类型 |
|---|---|---|
| `chat_session` | `context_summary` | TEXT |
| `chat_session` | `model` | VARCHAR(50) |

---

## 实施步骤与验证

### P1 权限审批
1. registry 风险元数据 + 默认写表 → 验证: `get_openai_tools()` 含 risk/requires_approval
2. permissions.py + approvals.py → 验证: 单测 evaluate 三模式 + 审批 once/always/deny/超时
3. AgentLoop 接入 → 验证: mock 场景审批流完整、deny 回填 tool-error
4. `/api/chat/approve` + SSE `permission_required` → 验证: curl 端到端审批流转
5. 前端审批卡片 → 验证: Playwright 点三按钮状态正确
6. MCP read/write 门控 → 验证: 配置 read_tools 后 read 免审、write 必审
7. 回归 → 验证: pytest 通过

### P2 docstring schema
8. 解析器 + 注入 → 验证: schema 含参数 description，旧工具兼容

### P3 压缩
9. compaction + 迁移 + 触发 → 验证: 构造长会话触发压缩，`get_history` 返回摘要+最近

### P4 并行
10. 并行执行 → 验证: 同轮多读耗时下降、事件顺序稳定、写仍串行

### P5 Provider
11. 路由 + 切模型 → 验证: 切模型后新轮用新 base_url

### P6 中断
12. 中断标志 + stop 路由 + 停止按钮 → 验证: 运行中点击停止，边界生效、未执行 tool_call 回填 error
