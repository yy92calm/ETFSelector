# AgentCore 上下文/流式/MCP/技能优化方案（对齐 deepseek-harness 模式）

## 现状对照

参照 deepseek-harness 的四个模块（prompt 组装、流式协议、MCP 桥接、技能系统），逐项对照本项目 agent_core：

| 模块 | deepseek-harness 模式 | 本项目现状 | 差距 |
|---|---|---|---|
| 上下文 | 静态 section 与动态快照分离；动态状态以带 source 归因的 user-role 快照注入，每轮独立；system prompt 保持稳定 | `SYSTEM_PROMPT.format(context=...)` 把策略/行情/风控/最近决策全部嵌入 system prompt，每轮重查 DB 重拼 | system prompt 每轮都在变，provider 端 prompt cache 全部失效；动态内容与静态身份无法分别演进 |
| 流式 | StreamChunk 协议（block/text-delta/reasoning-delta/usage/finish），usage 与 finish_reason 必达；request-error 有界重试 | provider.py 已简化移植 4 种事件（thinking/text/tool_calls/done），但无 usage、无 finish_reason、流中错误直接终止整轮、无 `</think>` 时 thinking_done 永不发出（前端思考块无法折叠） | 无法统计 token 成本；一次瞬态流错误毁掉整轮；思考块折叠有 bug |
| MCP | 长驻连接 + 断线重连（有界指数退避）+ per-call 超时 + tools/list_changed 刷新；命名 `mcp__<server>__<raw>` | 每次调用 `asyncio.run` 现建连接（stdio 每次调用都 spawn 子进程）；命名 `{server}.{tool}`（点号，部分 OpenAI 兼容端 function name 仅允许 `[a-zA-Z0-9_-]`，有拒答风险）；无超时、无重连、无变更刷新；`_run_coroutine` 在运行中的事件循环内会抛 RuntimeError | 每次调用延迟高、有命名兼容隐患、断线后工具静默失效直到重启 |
| 技能 | provider 注册表 + 多根目录优先级 + 热更新 + model/user 调用策略 + catalog 变更重发布 | 单目录 `skills/*.md`，单例初始化时扫描一次（新增文件需重启进程）；无优先级、无调用策略字段 | 无法运行时增删技能；无法区分模型可调用/用户可调用 |

## 关键约束

- AgentLoop 架构不变：同步代码 + OpenAI Function Calling + 生成器事件流 + SSE 推送
- 前端事件协议向后兼容：只新增事件类型（usage/thinking 修正），不改已有事件字段语义，前端忽略未知类型即可
- 不引入新框架依赖（无 Celery/Redis/ watchdog）；MCP 沿用 `mcp` SDK 现有依赖
- 中文注释、类型注解、模块级单例 + `get_xxx_service()` 工厂，遵循 AGENTS.md 编程约束
- 数据库迁移走 `init_db()` 内 `ALTER TABLE` 兼容逻辑，不引入 Alembic
- MCP server 未配置/连接失败时优雅降级，不影响内置 24 个 `@tool`
- 单次变更可追溯：每个优化项独立提交，测试先行

## 优化项清单（按优先级）

### P0-1 上下文静态/动态分离（KV cache 友好）

`app/agent_core/context.py` + `app/agent_core/loop.py`

- SYSTEM_PROMPT 只保留静态身份与规则（角色、原则、技能说明占位），移除 `{context}` 动态段
- ContextBuilder 拆为 4 个命名 section 函数（strategies/market/risk/recent_actions），每个独立 try/except，单 section 失败不拖垮整体
- 动态状态以一条 user-role 快照消息注入当前请求（不落库），消息带 `[系统状态快照 turn=N]` 标记，插在用户消息之前
- 压缩摘要从 history 中间的 system 消息改为该快照消息的前置段落（部分 provider 不接受消息列表中段的 system role）
- 效果：system prompt 跨轮字节级稳定，provider prompt cache 命中；历史消息不再因快照变化产生语义漂移

### P0-2 MCP 工具命名对齐

`app/agent_core/mcp_bridge.py`

- 命名从 `{server}.{tool}` 改为 `mcp__{server}__{tool}`，与 harness 一致，规避部分兼容端 function name 字符集限制
- 兼容旧名：注册时新旧两个名字指向同一 ToolDef（旧名标记 deprecated description），一个版本后移除

### P0-3 thinking_done 兜底

`app/agent_core/provider.py`

- 流正常结束（finish_reason 到达）时若仍处 in_thinking 状态，补发 `thinking_done`，避免前端思考块永远展开

### P1-1 usage 与 finish_reason 采集

`app/agent_core/provider.py` + `app/agent_core/loop.py` + `app/models/chat.py`

- stream_completion 在流结束时捕获最后一个 chunk 的 `usage` 与 `finish_reason`，产出 `{"type": "usage", "usage": {...}, "finish_reason": "..."}` 事件（对齐 harness StreamChunk 的 usage→finish 顺序）
- loop 收到后：(a) 新增 SSE 事件 `usage` 推给前端；(b) 存入 ChatMessage 新列 `usage`（JSON，init_db 加 ALTER TABLE 兼容列）
- 效果：每轮 token 成本可统计，为后续配额/成本控制打基础

### P1-2 流中瞬态错误有界重试

`app/agent_core/loop.py`

- 对齐 harness request-error 重试语义：流式调用抛异常或返回 error 事件时，若本轮尚未产出任何 tool_calls 且重试次数 < 2，则指数退避（1s/2s）后重发同一请求
- 已产出部分文本后失败不重试（避免重复推送），维持现状直接报错
- 中断标志检查优先于重试

### P1-3 MCP per-call 超时 + 事件循环安全

`app/agent_core/mcp_bridge.py`

- `_run_coroutine` 改为：`asyncio.get_running_loop()` 存在时用 `asyncio.run_coroutine_threadsafe` 提交到独立 loop 线程（进程启动时建一个常驻后台 loop），否则 `asyncio.run`
- 工具调用统一加 `asyncio.wait_for` 超时（默认 60s，配置项 `MCP_TOOL_CALL_TIMEOUT_MS`），超时返回 `{"error": "MCP工具调用超时"}`

### P1-4 技能热更新

`app/agent_core/skill_manager.py`

- 记录 skills 目录 mtime，`list_skills()` 时检测变化，变化才重扫（无 watchdog 依赖的轻量方案）
- frontmatter 支持 `model_invocable` / `user_invocable`（默认 true），`list_skills()` 增加按用途过滤

### P2-1 MCP 长驻会话与失败重连

`app/agent_core/mcp_bridge.py`

- stdio/http server 各维护一个懒创建的长驻 `ClientSession`（后台 loop 线程内），调用失败时销毁并按有界退避（1s 起步、30s 封顶、连续 10 次后停用该 server 并日志告警）重建
- 保留"连接即开即关"作为 `MCP_PERSISTENT_SESSION=false` 的回退模式，默认开启长驻
- 停用的 server 工具从注册表摘除，恢复后重新注册

### P2-2 技能多根目录与优先级

`app/agent_core/skill_manager.py`

- 目录优先级：项目 `skills/`（rank 100）> 用户 `~/.etfselector/skills/`（rank 400），同名低 rank 胜出
- .env 配置 `SKILL_EXTRA_DIRS` 追加自定义目录（rank 300）

### P2-3 每轮时间上下文

`app/agent_core/context.py`

- P0-1 的快照消息头部加入当前时间与是否交易日判断（复用 scheduler 的交易日历逻辑），模型获得"现在几点"的事实来源

## 模块结构与接入点

```
app/agent_core/
├── context.py        # 改造：section 化 + 快照消息构建（P0-1/P2-3）
├── loop.py           # 改造：SYSTEM_PROMPT 静态化、快照注入、usage 事件、重试（P0-1/P1-1/P1-2）
├── provider.py       # 改造：usage/finish_reason 事件、thinking_done 兜底（P0-3/P1-1）
├── mcp_bridge.py     # 改造：命名、超时、loop 安全、长驻会话（P0-2/P1-3/P2-1）
├── skill_manager.py  # 改造：热更新、多根目录、调用策略（P1-4/P2-2）
app/models/chat.py    # 改造：ChatMessage.usage 列（P1-1）
app/db/database.py    # 改造：init_db() ALTER TABLE 兼容（P1-1）
app/config.py         # 改造：MCP_TOOL_CALL_TIMEOUT_MS / MCP_PERSISTENT_SESSION / SKILL_EXTRA_DIRS（P1-3/P2-1/P2-2）
tests/                # 新增：test_context_snapshot.py / test_provider_stream.py / test_mcp_bridge.py / test_skill_manager.py
```

## 实施步骤

1. **P0-3 thinking_done 兜底** → 验证：构造无 `</think>` 的 mock 流，断言 thinking_done 发出
2. **P1-1 usage 采集** → 验证：mock 流带 usage，断言 SSE usage 事件与 ChatMessage.usage 落库
3. **P0-1 上下文分离** → 验证：单测断言 system prompt 不含行情数据、快照消息含各 section；两轮请求 system prompt 字节一致
4. **P0-2 MCP 命名** → 验证：mock server 注册后 `_TOOL_REGISTRY` 同时含新旧名且指向同一定义
5. **P1-3 MCP 超时与 loop 安全** → 验证：mock server sleep 超阈值返回超时 error；在 async 上下文调用不抛 RuntimeError
6. **P1-2 流中重试** → 验证：mock 首次抛错第二次成功，断言重试后正常完成且只重试 2 次封顶
7. **P1-4 技能热更新** → 验证：运行时新增 .md 文件后 list_skills 出现新条目
8. **P2-1/2/3 长驻会话、多根目录、时间上下文** → 验证：各自单测 + 手动联调
9. 每步独立提交；`pytest` 全量通过后再进入下一步

## 不做（明确排除）

- 不引入事件溯源式日志重放（ChatMessage 表已够用，改造收益不抵成本）
- 不做 MCP resources/prompts 桥接（与 harness 一致，只桥接 tools）
- 不改前端 SSE 渲染逻辑（新增事件前端自行兼容，缺失时静默忽略）
- 不做多 agent per-session 技能隔离（单用户系统，无必要）
