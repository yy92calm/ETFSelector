# 侧边栏 AI 助手 Harness 能力优化方案

> 目标：把侧边栏对话的 Agent 运行时（loop / 历史 / 权限 / 审批 / 流式 / 上下文 / 观测）补到「可长时间对话、可中断、可审计、出错可恢复」的 harness 水平。
> 范围：`app/routes/chat_routes.py`、`app/agent_core/*`、`static/js/chat.js`、`static/workbench.html`；不改轮动/交易主链路。
> 节奏：方案一行 = 一个 commit；每行完成后置 ✅ 并写实测验证与实施记录。

## 一、现状体检（2026-10-21）

| 症状 | 证据 | 影响 |
|------|------|------|
| 历史重建把 tool 结果排在 assistant(tool_calls) 之前 | `memory.py:72-85` 先 append tool，再 append assistant | 严格 OpenAI 兼容端会报 `tool message without preceding tool_calls`，带工具的多轮对话第二轮起可能 400 |
| 历史按 `created_at` 排序、截断窗口可能从 tool 消息开始 | `memory.py:64-69` | 同一秒消息顺序不稳；窗口头部留下孤儿 tool 消息 |
| 无结果的 tool_call 仍进历史 | `memory.py:75-85` | 中断/异常轮次留下无法配对的 tool_calls |
| 「本会话允许」只活一轮 | `permissions.py:31,45-56`；`chat_routes.py:102` 每请求新建 AgentLoop | 用户点「本会话允许」后下一条消息又弹审批，UI 语义与行为不符 |
| stop 只在本轮工具边界生效 | `loop.py:249,384`；流式循环 `270-307` 不检查中断 | 长回答/长工具期间点停止无反应 |
| stop 不能解除审批等待 | `loop.py:480` 阻塞 `store.wait` | 停服按钮在「等待授权」时形同虚设，最长等 120s 超时 |
| 工具轮数耗尽后静默空回复 | `loop.py:247-332,439` | 跑满 10 轮无最终文本，前端只看到工具气泡后结束 |
| `<think>` 跨 chunk 解析失效 | `provider.py:150-169` 只匹配完整标签 | 模型把 `<thi`/`nk>` 分片时标签漏进正文；`reasoning_content` 原生字段被忽略 |
| 首轮模型选择丢失 | `chat.js:211-223` 无 session 直接 return；`ChatRequest` 无 model | 新会话先选模型等于没选；切会话后下拉也不同步 |
| 工具耗时/token 不可见 | `loop.py:404-413` 无 duration；`usage` 每轮覆盖 | 长工具像卡死；无法判断上下文/成本 |
| 上下文不知道「用户在看哪个视图」与数据新鲜度 | `context.py:72-82` 无 view / freshness section | 回答泛化；行情断更时模型仍按旧数据下结论 |
| 工具结果尾部被砍 | `loop.py:392-395` 直接截前 3000 字 | 错误堆栈/结论常在尾部，模型看到的是开头寒暄 |
| SSE 无防缓冲头 | `chat_routes.py:112` | 反向代理可能攒帧，流式变批量 |
| 会话列表时间不刷新 | `memory.py:34-48` 只写消息，不 touch ChatSession | 有活动的会话不一定排前面 |
| Markdown 链接未限协议 | `chat.js:841` | `javascript:`/`data:` 链接可被注入 |
| 审批理由、压缩开始、用量、耗时未展示 | `chat.js:461-525,481` | 用户不知道在等什么、为什么慢 |

## 二、优化项

| ID | 项 | 优先级 | 验证 |
|----|----|--------|------|
| H0.1 | 历史消息序列完整性 | P0 | ✅ 6 条新单测：assistant 先于 tool；同时间戳仍按 id 排序；全/部分无结果 tool_calls 剔除；孤立 tool 头丢弃；全孤立返回空。全量 547 passed |
| H0.2 | stop 闭环（流中 + 审批等待） | P0 | ✅ 5 条新单测：流中中断只保留已产出增量；审批等待直测返回「用户中断」且不执行；完整 loop 链路工具标 error 后 yield interrupted 且不再调 LLM；pending 清空；跨会话隔离。全量 552 passed |
| H0.3 | 会话级授权持久化 | P0 | ✅ 5 条新单测：跨引擎实例放行；其他会话/无会话不受影响；always 经 `_exec_tool` 落共享清单；auto_approve 不写授权；删除会话清授权+中断。全量 557 passed |
| H1.1 | 工具轮数耗尽后的收尾合成 | P1 | ✅ 3 条新单测：跑满后第 3 次调用为无 tools 收尾并落库；收尾失败 error 含上限与原因；未跑满不额外调用。全量 560 passed |
| H1.2 | 思考流健壮性（跨 chunk 标签 / reasoning_content / 空 tool id） | P1 | ✅ 6 条新单测：开/闭标签跨 chunk、同 chunk 开闭、残留前缀 flush 为正文、reasoning_content、空 id 补齐。全量 566 passed |
| H1.3 | 模型选择首轮生效与会话同步 | P1 | ✅ 4 条后端单测 + 浏览器实测：新会话请求体携带 `qwen3.8-max-preview`；切到保存 `deepseek-chat` 的会话时下拉同步并补选项。全量 570 passed |
| H1.4 | 快照补当前视图与数据新鲜度 | P1 | ✅ 6 条新单测：视图中文标签注入、非法视图忽略、三类最新日期、管道未运行/运行中/完成/失败四态、空库走初始化兜底、失败原因截断。全量 576 passed |
| H1.5 | 工具耗时与累计 token 用量 | P1 | ✅ 5 条新单测：单工具耗时、并行工具各自计时、跨轮累计（110→280）、最终消息落累计、无 usage 不炸。浏览器实测工具气泡「完成 · 1.2s」、在线态「本次 120 tokens」。全量 581 passed |
| H2.1 | 工具结果头尾截断 + SSE 防缓冲头 | P2 | ✅ 4 条新单测：短结果不变、长结果保头保尾且带原长、各上限下长度不越界、SSE 三响应头齐备。全量 585 passed |
| H2.2 | 侧边栏会话体验（审批理由/停止态/压缩提示/链接协议/活跃时间） | P2 | ⬜ |

## 三、各项规格

### H0.1 历史消息序列完整性

- `get_history` 按 `ChatMessage.id` 升序重建（替代 `created_at`，避免同秒并列）。
- 每条 assistant 行：先输出 assistant（含 `tool_calls`），再输出对应 tool 结果。
- `tool_calls` 只保留有匹配 `tool_results.tool_call_id` 的项；无匹配则整轮降级为普通 assistant 文本，防止孤儿 tool_calls。
- 截断窗口头部若为 tool / 带 tool_calls 的 assistant，丢弃到第一条 user 为止（一轮对话必以 user 开头）；避免「半截工具链」。
- 不引入新表、不改 canonical 历史。

验证：新增单测覆盖顺序、主键排序、截断边界、缺失结果、孤儿 tool、无 user 窗口；现有 history 测试保持通过。

实施记录：
- `_rebuild_history` 抽成模块级函数（纯逻辑可单测），`get_history` 只负责取数与截断。
- 部分配对（2 个 call 只有 1 个结果）保留有结果的 call、剔除无结果的 call；整轮无结果时降级为普通 assistant 文本，不产生孤儿 `tool_calls`。
- 头部孤立 tool 清理保留为防御逻辑：工具结果当前与 assistant 同行，正常路径不会出现，但兼容历史数据/未来拆表。
- 排序从 `created_at` 改为 `id`；`get_all_messages` 同步改，避免同秒写入时前后端顺序不一致。
- 6 条新单测（`tests/test_chat_harness.py`），全量 547 passed。

### H0.2 stop 闭环

- 流式循环每收到一个 provider 事件检查一次中断；命中即停止消费并 yield `interrupted`（不落半截 assistant 消息）。
- `PendingApproval` 增加 `session_id`；`ApprovalStore.resolve_session(session_id, deny)` 唤醒该会话所有待审批。
- `request_interrupt(session_id)` 同时调用 `resolve_session`，审批等待立即返回 deny，`_exec_tool` 返回「用户中断」，循环在下一轮边界 yield `interrupted`。
- 保持现有中断标志语义：turn_start 清理；不同会话隔离。

验证：新增 4 条单测（流中中断、审批中 stop、pending 清空、跨会话隔离）；现有 `TestInterrupt` 通过。

实施记录：
- `PendingApproval` / `ApprovalStore.create` 增加 `session_id`；`resolve_session` 在锁内摘除待办、锁外 `event.set()`，避免唤醒线程立刻再抢同一把锁。
- `request_interrupt` 置位中断标志后同步解除该会话待审批；审批等待中的 turn 不再空等满 `chat_approval_timeout`（默认 120s）。
- `_exec_tool` 在 `store.wait` 返回 deny 后先查中断标志，区分「用户中断」与「用户拒绝授权」，中断路径不执行工具。
- 流式重试逻辑配合调整：新增 `interrupted_mid_stream`，每个 provider 事件边界检查一次中断，命中直接跳出 attempt 循环并 yield `interrupted`，不会被误判为流式错误触发重试；已产出的增量保留在前端，不落半截 assistant 消息。
- 现有 `TestInterrupt` 保持通过（工具执行中置位中断仍走「未执行工具回填 error」路径）。
- 偏离：方案写 4 条单测，实际 5 条，补了一条 `_iter_events` 级别的完整审批中断链路测试（permission_required → 停止 → tool_finished=error → 下一轮边界 interrupted → 第二次 LLM 调用未发生）。
- 全量 552 passed。

### H0.3 会话级授权持久化

- `permissions.py` 增加进程级、线程安全的 `{session_id: {tool_name}}` 存储，供多个 AgentLoop 实例共享。
- `PermissionEngine.evaluate(..., session_id=None)` 先查实例清单，再查会话存储；`allow_tool_for_session(tool_name, session_id=None)` 有 session 时写共享存储。
- `AgentLoop._exec_tool` 传入当前 `session_id`；`always` 结果落共享存储。
- `DELETE /api/chat/sessions/{id}` 同步清理授权与中断标志；新建会话天然隔离。
- 单进程部署假设（与现有进程内 ApprovalStore 一致），不做多 worker 共享。

验证：新增 4 条单测（跨实例生效、会话隔离、删除清理、auto_approve 不受影响）。

实施记录：
- `permissions.py` 增加进程级 `_SESSION_ALLOW`（`{session_id: {tool_name}}`）+ 锁，以及 `remember_session_allow` / `is_session_tool_allowed` / `clear_session_permissions` 三个模块函数；`PermissionEngine` 的实例清单保留（无 session_id 的旧调用语义不变）。
- `evaluate(..., session_id=None)` 先查实例清单再查共享清单；只读模式的拒绝判定仍在最前，切换 discuss 后旧授权不会绕过。
- `_exec_tool` 的 `evaluate` 与 `always` 落库都传当前 `session_id`，所以审批「本会话允许」跨 HTTP 请求、跨 AgentLoop 实例生效。
- `DELETE /api/chat/sessions/{id}` 在 DB 删除成功后清理该会话授权与中断标志；session_id 复用不会继承旧授权。
- 单进程假设与 `ApprovalStore` 一致；多 worker 不共享，未引入外部存储。
- 偏离：方案写 4 条单测，实际 5 条，补了一条 `_exec_tool` 审批 always 后新引擎实例直接放行的链路测试。
- 全量 557 passed。

### H1.1 工具轮数耗尽后的收尾合成

- `MAX_TOOL_ROUNDS` 改为 `settings.chat_max_tool_rounds`（默认 10，保留常量兜底）。
- 循环结束且 `final_content` 为空（跑满轮数）时，再发一次不带 tools 的流式调用，要求「基于以上工具结果给出结论」；正文照常流式输出并落库。
- 若收尾调用也失败，yield `error` 并明确「已达到工具调用上限」，不再静默空回复。
- `turn_end` 带 `round_limit_reached: true`，前端可提示。

验证：3 条单测（跑满后合成、收尾调用 tools=None、收尾失败有明确错误）；现有轮次测试通过。

实施记录：
- `Settings` 新增 `chat_max_tool_rounds: int = 10`；`loop._max_tool_rounds()` 读取配置，非正整数（含测试替身）回退 `MAX_TOOL_ROUNDS` 常量。
- 用 `for ... else` 精确识别「跑满轮数未 break」：追加一条 user 指令「不要再调用工具，基于以上工具结果给出结论」，再发一次 `stream_completion(..., tools=None)`；思考/正文/usage 事件与普通轮次同协议，前端无需特判。
- 收尾正文照常 `assistant_message` 并落库、更新会话标题；`turn_end` 仅在跑满时带 `round_limit_reached: true`，未跑满不改变事件形状。
- 收尾失败或模型返回空文本时 yield `error`（含「已达到工具调用上限（N 轮）」与具体原因），不再静默结束；不写空 assistant 消息。
- 收尾流同样逐事件检查中断，停止按钮在合成阶段仍生效。
- 3 条新单测，全量 560 passed。

### H1.2 思考流健壮性

- `provider.stream_completion` 用缓冲状态机解析 `<think>`/`</think>`：跨 chunk 的标签前缀暂存，流结束 flush。
- 支持 `delta.reasoning_content` 原生思考字段（DeepSeek 风格）：直接产 `thinking_delta`，首个正文前补一次 `thinking_done`。
- tool_call id 为空时用 `call_{index}_{uuid8}` 补齐，避免 tool 消息配对失败。
- 保持 `<think>` 与正文混排时的现有事件顺序。

验证：6 条单测（开/闭标签跨 chunk、原生 reasoning、空 id、现有 thinking 测试）。

实施记录：
- 新增 `_partial_tag_suffix` + `_feed_content` 缓冲状态机：文本模式只暂存「可能是 `<think>` 前缀」的最长后缀，思考模式同理暂存 `</think>` 前缀；流结束把未决前缀按当前模式 flush（正文或思考），不丢字符。
- 顺带修掉同 chunk 内 `<think>想</think>答` 的静默错误：旧实现只处理开标签，会把 `</think>` 与正文一起当思考内容输出。
- 原生 `delta.reasoning_content` 直接产 `thinking_delta`；首个正文事件之前补一次 `thinking_done`，流结束仍是思考态也兜底闭合。
- 工具调用 id 为空时补 `call_{index}_{uuid8}`，避免 assistant(tool_calls) 与 tool 消息无法配对。
- 6 条新单测；既有 thinking/usage 测试全部通过，全量 566 passed。

### H1.3 模型选择首轮生效与会话同步

- `ChatRequest` 增加可选 `model`（≤100）；`chat_stream` → `run_streaming` → `_iter_events` 透传。
- 传入时先写会话模型再解析 Provider；空串表示恢复默认；不传保持服务端已有值（旧客户端行为不变）。
- `/api/chat/history` 返回 `model`；前端 `selectSession` 同步下拉，`newSession` 保留当前选择，`send` 每次携带当前下拉值。
- 模型不在 options（配置变更）时动态补一个 option，避免下拉静默归零。

验证：4 条后端单测 + 前端浏览器实测（新会话首轮即用所选模型、切会话同步）。

实施记录：
- `ChatRequest` 增加可选 `model`（`max_length=100`，空串表示恢复默认）；`chat_stream` → `run_streaming` → `_iter_events` 全链路透传。
- `_iter_events` 在进入会话后、解析 Provider 前处理 `model is not None`：`strip()` 后写会话模型。因此新会话首轮选模型即生效；空串清空回默认；不传（旧客户端）保持服务端已有值。
- `/api/chat/history` 增加 `model` 字段；前端 `selectSession` 拿到后同步下拉。
- 前端 `send` 每次随消息携带当前下拉值；`switchModel` 在无 session 时直接返回（选择由首轮请求带上）；`loadModels` 改为可重入的 `renderModelOptions`，会保留当前选择。
- `syncModelSelect` 对「会话保存但已不在配置里」的模型动态补 `xxx（不在当前配置）` option，避免下拉静默归零。
- 4 条后端单测；本地浏览器实测：新会话选中 `qwen3.8-max-preview` 后请求体为 `{message, session_id: null, model: "qwen3.8-max-preview"}`；切换到保存 `deepseek-chat` 的会话后下拉值同步为 `deepseek-chat`（自动补 option）。
- 全量 570 passed。

### H1.4 快照补当前视图与数据新鲜度

- `ChatRequest` 增加可选 `view`；后端白名单校验（overview/market/strategies/sentiment/tasks/analyses/research/rules），映射中文标签后拼入时间头。
- `ContextBuilder` 新增「数据新鲜度」section：最新行情日、最新指标扫描日、最新组合快照日、今日管道状态（未运行/运行中/完成/失败 + 最近失败原因）。
- 单 section 独立容错；空库返回空由「系统刚初始化」兜底。

验证：6 条单测（视图注入/非法值忽略/三类日期/管道四态/空库/截断）。

实施记录：
- 视图走白名单 `VIEW_LABELS`，未知值（含前端注入串）整段不拼，避免把任意字符串塞进 system 可见文本；前端 `sendStreaming` 随消息带 `view: this.getView()`。
- 「数据新鲜度」放在 builders 首位：日期取 `ETFQuotation`/`ETFDailyIndicator`/`PortfolioSnapshot` 各自 `max(trade_date)`；今日管道只认 `task_name == daily_pipeline` 的总行判总体状态，`daily_pipeline.%` 的失败行取最近一条（按 `started_at desc, id desc`）展示阶段与原因。
- 空库（三类日期与今日日志全无）返回空串，保留「系统刚初始化」兜底；有历史行情但今日未跑管道时显示「今日管道: 未运行」——这要求表里已有数据，测试据此先落一条行情。
- 失败原因截断 120 字符（`FRESHNESS_ERROR_LIMIT`），避免单条异常刷爆快照 token。
- 时区假设：`TaskExecutionLog.started_at` 是 naive UTC，而「今日」按北京日期取，凌晨 0–8 点运行的管道可能被算到前一天；当前管道固定 20:00 跑，暂不受影响，未为此改全链路时间口径。
- 既有 `_get_evolved_prompts` 的 `status == 'active'` 口径未动（与复盘覆盖暂停策略的口径差异属既有行为）。
- 6 条新单测；全量 576 passed。

### H1.5 工具耗时与累计 token 用量

- 每个工具记录 `perf_counter` 耗时，`tool_finished` 与 `tool_calls_made` 带 `duration_ms`（并行工具各自计时）。
- 一轮内累计 `prompt_tokens/completion_tokens/total_tokens`；`turn_end.usage` 输出累计值，最终 assistant 消息落库累计 usage。
- 前端工具气泡显示耗时；在线状态短暂显示本次 tokens；不新增页面。

验证：5 条单测（单工具/并行工具耗时、跨轮累计、最终落库、无 usage 不炸）+ 浏览器实测。

实施记录：
- 并行读工具在执行线程内用 `perf_counter` 计时（不含 `SessionLocal` 创建），每个工具各自 `duration_ms`；写工具在审批链路外层计时，因此耗时包含等待用户授权的时间——这正是用户感知的延迟。
- 被中断未执行的工具 `duration_ms=None`（前端只显示「失败」，不编造 0ms），仍照常出现在 `tool_finished` 与 `tool_calls_made`，保留事件字段一致性。
- usage 事件统一为 `{usage（本轮）, total（累计）, finish_reason}`；收尾合成分支此前直接发扁平 token dict，与主循环口径不一致，本次一并对齐。
- 累计口径：缺 `total_tokens` 时用 prompt+completion 补齐；全零视为无用量（`_usage_snapshot` 返回 None），既不落库零值也不影响前端。
- 落库口径：带 tool_calls 的 assistant 行保留「本轮」用量；最终回复行落「整轮累计」；`turn_end.usage` 输出累计值，前端在线态显示 5 秒后恢复。
- 计时毫秒用 `round` 而非 `int`（`10.123-10.0` 浮点差会被截成 122），单测锁死 123。
- 浏览器实测受限于内置浏览器点击不可用，改为注入 token 后直接 `dispatchEvent` 回放 SSE 帧：工具气泡「完成 · 1.2s」、`_formatDuration` 823→「823ms」、在线态「在线 · 本次 120 tokens」。
- 5 条新单测；全量 581 passed。

### H2.1 工具结果头尾截断 + SSE 防缓冲头

- 新增 `_truncate_tool_result`：短结果原样；超限保留头 2/3、尾 1/3，中间标记原长，确保错误尾部可见。
- 入模 tool 消息与前端 preview 同口径（preview 用更小上限）。
- `StreamingResponse` 增加 `Cache-Control: no-cache`、`X-Accel-Buffering: no`、`Connection: keep-alive`。

验证：4 条单测（短结果不变/长结果保尾/长度受控/响应头）。

实施记录：
- `_truncate_tool_result` 的截断标记计入预算，输出总长严格 ≤ limit；limit 小到放不下标记时退化为纯头部截断，保证任何上限都不越界。
- 三处上限：入模 3000、SSE preview 1500、落库 1000。落库也改成头尾截断——历史轮次是靠 `tool_results` 重建后回灌模型的，只留头部会让「尾部错误」只在第一轮可见。
- `run_autonomous` 的同类硬编码 `[:3000] + "(截断)"` 一并替换为新助手（同样是「错误在尾部看不见」的问题，非侧边栏路径但同一根因）。
- `tool_calls_made.result_preview` 仍只取头部 200 字：该字段仅供 `refreshWorkbench` 判断是否要刷新面板，不展示给用户/模型，未改。
- SSE 响应补 `Cache-Control: no-cache`、`X-Accel-Buffering: no`、`Connection: keep-alive`；单测直接检查 `StreamingResponse.headers`，未在 nginx 反代下做线上链路实测。
- 4 条新单测；全量 585 passed。

### H2.2 侧边栏会话体验

- 审批卡片显示后端 `reason`（写操作原因），按钮仍是 once/always/deny。
- stop 后按钮显示「停止中…」，直到 `interrupted`/`turn_end` 才复位。
- 处理 `compacting` 事件：显示「正在整理上下文…」；`compacted` 保留。
- `usage` 事件更新侧边栏 token 提示；工具气泡显示耗时。
- Markdown 链接仅允许 http(s)/mailto/相对路径/锚点，其余降级为 `#`。
- `ChatMemory.save_message` touch `ChatSession.updated_at`，会话列表按最近活动排序。

验证：node 校验链接白名单 + 浏览器实测（审批理由/停止态/压缩提示/耗时/tokens/会话排序）+ 1 条后端单测（updated_at 刷新）。

## 四、非目标

- 不引入 WebSocket / 持久化审批队列 / 多 worker 共享状态（当前单进程部署）。
- 不改轮动通道、交易执行、风控阈值。
- 不改 `suggest_allocation_change` 的 read 风险归类：该工具只写建议表，真实换仓仍由轮动通道裁决；如要审批建议本身，属于权限策略调整，另案讨论。
- 不重写前端框架，不引入构建工具。

## 五、验收

- 全量 pytest 通过（基线 541）。
- 每行独立 commit，方案表对应行置 ✅ 并附实测数字。
- 侧边栏改动在本地浏览器实测；无法实测的部分显式说明。
- 部署到生产需用户确认（本方案只提交代码，不自动上线）。
