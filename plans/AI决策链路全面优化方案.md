# AI 决策链路全面优化方案

## 实施状态

| 阶段 | 项 | 状态 | 验证 |
|---|---|---|---|
| P0 | 1.1 市场扫描批量预加载 | ✅ | 270 只逐字段等价；quotes 87→40ms |
| P0 | 1.2 自适应权重查询提到循环外 | ✅ | 单测断言 `_resolve_weights` 调用次数==1；指标计算 129→9ms |
| P0 | 1.3 市场分析 Orchestrator 无依赖 Agent 并行 | ✅ | 12 条单测：9 Agent 并发耗时 < 串行；每任务独立 Session 且关闭；单任务异常→error 不阻断 |
| P0 | 1.4 轮动辩论两派并行 | ✅ | 6 条单测：两派收到同一份材料；一派失败仍可进裁决 |
| P0 | 1.5 舆情 LLM 批量分析 | ✅ | 19 条单测：20 条新闻 → 3 次调用（8/8/4）；缺槽位回退关键词；index 越界不采信 |
| P0 | 1.6 因子回填批量预加载 forward return | ✅ | 9 条单测：SQL 次数不随记录数增长；与逐条口径逐条等值；缺失因子补齐不再被整日跳过 |
| P1 | 2.1 上下文补全（持仓盈亏/近期交易/大盘指数/仓位） | ✅ | 12 条单测：快照含成本/现价/浮盈/持有期锁定、最近5笔成交、权重合计+待生效+现金利用率、指数当日/5日涨跌+建议仓位；截断可见；空库退回「系统刚初始化」；新增 section SQL 次数不随策略数增长 |
| P1 | 2.2 轮动辩论裁决官输入去重 | ✅ | 12 条单测：未提及候选不进材料；持仓全保留（替换制基准）；★提案标的带指标出现；材料段 7342→482 字符（-93%），整段 prompt 9048→2143；日期/金额等 6 位以上数字不会误入材料 |
| P1 | 2.3 工具 Schema 类型支持（Optional/Literal/list[dict]） | ✅ | 24 条单测：swaps.items 含 remove/add 与必填；Optional→nullable；Literal/Enum→enum；参数校验错误可读 |
| P1 | 2.4 run_autonomous 状态判定 bug | ✅ | 6 条单测：以 "LLM" 开头的正常结论→completed；异常/跑满轮数无结论→failed；按主键排除当前消息 |
| P1 | 2.5 经验匹配带权相似度 + 冲突检测修正 | ✅ | 11 条单测：不相关 failure/success 不再判冲突；同场景对立结论仍判冲突；regime 命中权重高于普通因素 |
| P2 | 3.1 管道阶段关键路径阻断 | ⬜ | 单测：quotes 失败 → market_scan 标记跳过 |
| P2 | 3.2 上下文压缩 Token 估算 + 分层压缩 | ⬜ | 单测：中文估算更准；分级触发 |
| P2 | 3.3 经验权重指数衰减 + 单一权威写入 | ⬜ | 单测：衰减不覆盖 boost 结果 |
| P2 | 3.4 辩论降级显式标记 debate_degraded | ⬜ | 单测：LLM 失败路径带标记 |
| P2 | 3.5 N+1 查询清理（最短持有期/建议校验/禁入缓存） | ⬜ | 单测：批量查询生效 |
| P3 | 4.1 风控工具补全（相关性/VaR/what-if 熔断） | ⬜ | 单测：工具注册 + 计算正确 |
| P3 | 4.2 因子表现与板块影响分析工具 | ⬜ | 单测：工具返回结构 |
| P3 | 4.3 复盘 avg_return 改期间收益率 | ⬜ | 单测：期间收益率计算 |
| P3 | 4.4 delete_strategy 软删除 | ⬜ | 单测：删除后数据可查回 |

## 实测修正（P0.1 完成后 · 2026-10-10）

初版分析把「DB N+1」列为最大瓶颈，实测**不成立**，据此调整 P0 优先级：

- 本地 SQLite + `ix_quotation_code_date(etf_code, trade_date)` 索引下，270 次「逐只 order_by desc limit 30」仅 87~97ms，本就极便宜
- 窗口函数 `ROW_NUMBER() OVER (PARTITION BY etf_code)` 反而更慢（**277ms**，需全表扫 21.6 万行）→ **已弃用**
- 单次日期区间扫描 + 只取必要列（不构造 ORM 对象）= **38~41ms**（约 2.3x），是唯一有效的批量改法
- 真正的扫描瓶颈是 `_compute_indicator` **在循环里每次都解析自适应权重**（import + 查库）：外提后指标计算 **129ms → 9ms（14x）**

`scan_all` 核心耗时 约 216ms → 49ms（270 只，逐字段结果完全一致）。绝对节省仅 ~170ms/晚，
但消除 270 次冗余往返——标的池增长、服务器磁盘更慢时收益放大。

**结论**：P0 真正值钱的是**减少 LLM 调用次数**（11 个 Agent 串行、舆情逐条 LLM），不是 DB 查询合并。

### P0.4 例外：因子回填是**真的** N+1（实测 · 2026-10-10）

`backfill_forward_returns` 每条 pending 记录要 2 次行情查询（找 T+5、查 T 日收盘），
本地库 2000 条待回填实测：

- 逐条：**680ms / 4000 次查询**，回填 1550 条
- 单次区间查询 + 内存 bisect 定位 T+5：**43ms / 1 次查询**（**15.8x**），回填 1550 条，**逐条结果 0 处差异**

与扫描器不同，这里每只 ETF 需要「T 之后的第 5 个交易日」，属于按记录定位的窗口，
一次 `WHERE etf_code IN (...) AND trade_date >= min(T)` 区间扫描（21.6 万行的子集）
即可在内存用 bisect 覆盖全部记录，所以批量改法有效。
顺带修 `backfill_from_indicators` 的去重键：原按 `trade_date` 整日跳过，
某日只有部分因子落库时缺失因子**永远补不上**；改为按 (代码, 日期, 因子) 记录身份去重。

## 目标

对 AI 决策链路做一次贯通式优化，覆盖四条决策路径（自主 AgentLoop / 对话 / 轮动辩论 / 降级管道）及其上下游数据供给，分四个优先级：

- **P0 性能**：消除全链路 N+1 查询与无谓串行等待，降低每日管道耗时与 LLM token 成本
- **P1 决策质量**：补齐 LLM 视野缺失的关键上下文，压缩辩论重复输入，修工具 Schema 与状态判定 bug
- **P2 健壮性**：让失败可被感知（阶段阻断、降级标记）、让经验权重可被信任（衰减/boost 不互相覆盖）
- **P3 能力边界**：补足风控与分析工具维度，修正复盘统计口径

**非目标**：不改动「LLM 只建议、轮动通道唯一执行入口」的既有架构；不引入新依赖（Celery/Redis/向量库）；不改变前端交互契约（仅新增只读字段）。

## 现状与问题定位

### P0 性能瓶颈

| 位置 | 问题 |
|---|---|
| `market_scanner_service.py:50-53` | `scan_all` 逐只调 `_compute_indicator`，每只独立查 30 条行情 → N 次查询；自适应权重在每只计算内部重复获取（`market_scanner_service.py:186-194`） |
| `orchestrator.py:58-124` | 11 个 Agent 全串行；阶段1（技术+情绪）与阶段1.5（宏观/跨资产/波动率）内部互不依赖 |
| `rotation_debate/orchestrator.py:57-67` | 动量派与稳定派串行，二者输入相同、互不依赖 |
| `sentiment_service.py:284-307` | LLM 情感分析逐条调用，15-30 条新闻 = 15-30 次 API |
| `factor_performance_service.py:78-119` | `backfill_forward_returns` 逐条查 T+5 行情 |
| `rotation_service.py:580-593` | `_check_min_hold_period` 每只持仓单独查 TradeRecord |
| `allocation_suggestion_service.py:82-87` | ETF 存在性逐条查询 |
| `failure_mode_service.py:70-88` | `get_banned_codes` 每次全表扫描 + 函数内 `import re`；`is_code_banned` 反复重建列表 |
| `scheduler.py:837-845` | 行情补全逐只查 `max(trade_date)` |

### P1 决策质量

| 位置 | 问题 |
|---|---|
| `context.py:91,169` | 策略数硬截断 5 个 / 风控只查 3 个，超出静默不可见 |
| `context.py` | 缺持仓浮动盈亏与成本、近期实际成交、仓位/现金利用率、大盘指数状态 |
| `rotation_debate/*` | 裁决官重复接收 holdings/candidates/rule/sector/sentiment/suggestion 全量原始数据，约 40-50% token 与两派重复 |
| `momentum/stability_advocate.py:68/72` | temperature=0.4 对结构化 JSON 偏高 |
| `registry.py:97-111` | 类型映射不支持 `Optional`/`Literal`/`list[dict]`，`swaps` 参数 schema 退化为 `list[string]`，LLM 传参格式错误率高 |
| `registry.py:194-216` | `execute` 无参数校验，多余/类型错参数直接抛穿 |
| `loop.py:599` | `status = "completed" if not final_content.startswith("LLM") else "failed"` — 靠文本前缀判失败，不可靠 |
| `loop.py:202` | 用户消息去重用内容字符串比较，重复内容会误删历史 |
| `smart_experience_matcher.py:61-95` | Jaccard 只看 value 丢 key 语义、无标签权重；failure 再乘 2.0 使失败经验权重过陡 |
| `smart_experience_matcher.py:142-165` | 冲突分 type 差异 0.5 + action 0.2 已达阈值 0.7，不相关场景也报「假冲突」 |

### P2 健壮性

| 位置 | 问题 |
|---|---|
| `scheduler.py:119-167` vs `_step_*` | `_run_stage` 靠异常判失败，但 `_step_*` 内部 catch 全部异常只记日志（如 `scheduler.py:202-218`）→ 检查点被误标成功 |
| `scheduler.py` | 阶段间无依赖声明：`quotes` 失败仍继续 `market_scan`，基于过期数据出指标 |
| `compaction.py:23-26` | Token 估算只算 `content`、统一 4 字符/token，中文严重低估；`tool_calls` 完全未计 |
| `compaction.py:29-34` | 阈值 0.8（128k×0.8≈102k）触发过晚；`_trim_fallback` 只留 user 消息，丢弃全部推理过程 |
| `experience_manager.py:159-165` | 权重从 `generated_date` 线性重算并硬置基线 1.0，覆盖 `boost_failure_experience_weights`（L320-339）的 2x 结果；decay 与场景调权（L285-318）互相覆写 |
| `experience_manager.py:191-196` | 低效仅置 `review_status=pending`，无后续动作，仍被使用 |
| `rotation_service.py:460-481` | 辩论降级为纯量化时无任何标记，下游无法知晓决策质量下降 |
| `orchestrator.py:59-77` | 前序 Agent 失败仍以错误结果喂给后续 Agent |

### P3 能力边界

| 位置 | 问题 |
|---|---|
| `risk_tools.py`（全文件 88 行） | 无相关性/VaR/因子暴露；`run_stress_test`、`check_circuit_breaker` 无参数，无法 what-if |
| `analysis_tools.py` | 无因子表现（IC/自适应权重）查询工具、无板块对组合影响工具 |
| `review_service.py:805-808` | `avg_return` 对**累计**收益率求平均，无金融含义 |
| `review_service.py:431-463` | 单日收益按快照相邻位取，跨周末失真；`status != "success"` 把 skipped 也计为失败 |
| `strategy_tools.py:78-102` | `delete_strategy` 物理删除关联数据，误调不可恢复 |

## 模块结构（改动范围）

```
app/services/market_scanner_service.py       # P0 批量预加载 + 权重外提
app/services/factor_performance_service.py   # P0 批量 forward return + IC 计算去重
app/services/sentiment_service.py            # P0 LLM 批量分析
app/services/rotation_service.py             # P0 最短持有期批量 + P2 debate_degraded 标记
app/services/allocation_suggestion_service.py# P0 ETF 存在性批量校验
app/services/failure_mode_service.py         # P0 正则模块级 + banned 缓存 + 过期过滤
app/agents/orchestrator.py                   # P0 无依赖 Agent 并行 + P2 级联降级
app/agents/rotation_debate/orchestrator.py   # P0 两派并行
app/agents/rotation_debate/{momentum,stability}_advocate.py  # P1 temperature 0.2 + 输入精简
app/agents/rotation_debate/rotation_judge.py # P1 只收差异摘要
app/agent_core/context.py                    # P1 新增 4 个 section + 截断可见化
app/agent_core/compaction.py                 # P2 中英文分别估 token + 分层压缩
app/agent_core/loop.py                       # P1 状态判定 + 去重键
app/tools/registry.py                        # P1 类型映射 + 参数校验
app/tools/risk_tools.py                      # P3 风控工具补全
app/tools/analysis_tools.py                  # P3 因子/板块分析工具
app/tools/strategy_tools.py                  # P3 软删除
app/services/experience_manager.py           # P2 指数衰减 + 单一权威写入
app/services/smart_experience_matcher.py     # P1 带权相似度 + 冲突修正
app/services/review_service.py               # P3 期间收益率 + 快照口径
app/tasks/scheduler.py                       # P2 关键路径阻断 + 异常口径统一
app/config.py                                # 新增配置项（见下）
```

## 实施步骤

### 阶段 P0 — 性能（先做，收益最直接）

**1.1 市场扫描批量预加载**
- 新增 `_load_price_map(codes, end_date, db, lookback=60)`：一次 `ETFQuotation` 查询（`etf_code IN codes` + `trade_date <= end_date`），按 code 分组为 `{code: [rows asc]}`，截断到每只最近 30 条
- `_compute_indicator` 改签名接收 `rows`（不再自查），保留旧签名兼容或直接内部重构
- 验证：对同一 scan_date，批量路径与逐只路径产出的 `ETFDailyIndicator` 字段完全一致

**1.2 自适应权重外提**
- `scan_all` 入口查一次 `get_adaptive_weights(db)`，作为参数传入逐只计算
- 验证：`get_adaptive_weights` 在单测 mock 中调用次数 == 1

**1.3 Orchestrator 并行**
- 用 `concurrent.futures.ThreadPoolExecutor(max_workers=4)` 并行：阶段1（技术/情绪）、阶段1.5（宏观/跨资产/波动率）
- 每个 Agent 的 db session 独立（Agent 内部各自创建，不共享 session），避免 SQLAlchemy session 跨线程
- 单 Agent 失败 → 该项置 `{"error": ...}`，不抛穿（保持现有容错语义）
- 验证：mock 每个 Agent 睡眠 0.3s，并行总耗时 < 串行一半

**1.4 轮动辩论两派并行**
- 同 1.3，`momentum_advocate.analyze` 与 `stability_advocate.analyze` 并行提交
- 验证：mock 计时

**1.5 舆情 LLM 批量分析**
- 新增 `SENTIMENT_ANALYSIS_BATCH_SIZE`（默认 8）；把逐条 `call_llm` 改为每批一次，prompt 要求返回 `[{title, sentiment, score}]` 数组并按 index 对齐
- 解析失败或数量不匹配 → 该批回退关键词评分（复用现有 `_keyword_score`）
- 验证：mock 20 条 + batch=8 → LLM 调用 3 次

**1.6 因子回填批量预加载**
- `backfill_forward_returns`：先一次查询所有 pending 记录涉及的 `(code, date)` 区间行情，建 `{(code,date): close}` 字典，再内存查 T+5
- 顺带把 `backfill_from_indicators` 的去重从「按 trade_date」改为「按 (trade_date, factor_name)」，修复部分因子缺失时整日被跳过的问题
- 验证：单测断言 SQL 查询次数为常数级

### 阶段 P1 — 决策质量

**2.1 上下文补全**（`context.py`）
新增 section，各自独立 try/except，数据缺失时输出「暂无」而非省略：
- **持仓盈亏**：查最新 `PortfolioSnapshot` / `Holding`，输出每只成本、现价、浮动盈亏%、持仓天数（对齐 `MIN_HOLD_DAYS` 让 LLM 知道是否可换）
- **近期成交**：最近 5 笔 `TradeRecord`（买/卖/标的/金额/日期）
- **仓位与现金利用率**：当前 `allocation_config` 权重和、待生效 `pending_allocation` 是否存在及内容
- **大盘环境**：主要指数（沪深300/中证500/创业板指对应 ETF）当日与 5 日涨跌幅 + `MarketRegimeSnapshot` 的风险偏好/建议仓位区间
- **截断可见化**：策略数超限时输出「共 N 个活跃策略，以下展示前 5 个」而非静默截断
- 验证：单测构造 fixture 策略，断言快照文本包含上述关键字段

实施记录（2026-10-10）：
- 「暂无」与「系统刚初始化」兜底的边界：库内**完全没有行情**时大盘 section 返回空（让整体兜底语生效，否则空库快照被一堆「暂无」占满），**有行情但指数代理/状态快照缺失**才显式标注「暂无」——静默省略才会让 LLM 以为大盘坐标可忽略。
- 持有期口径与轮动通道一致：从该标的**最近一笔买入**起算（group-by 一次取回全部 `(策略,标的)→最近买入日`，不逐标的查）。
- 遗留：旧 section（目标进度、风控）仍逐策略查询，随策略数线性增长，归入 3.5 清理。

**2.2 辩论 Token 去重**
- 新增 `_build_diff_context(momentum_opinion, stability_opinion, holdings, candidates)`：只提取两派**提及到的标的**及其得分摘要，作为裁决官输入
- 裁决官 prompt 改为：两派立场 + 差异标的摘要 + 硬约束清单；不再全量传 holdings/candidates
- 建议标的（★/✖ 提案）单独成段保留（裁决官必须看到），并补一句说明标记来源（上游通道校验结果）
- 验证：单测断言裁决官 prompt 长度显著下降且不丢失被提议标的

实施记录（2026-10-10）：
- **偏差**：持仓不做争议过滤，仍**全量**列出（压成一行/只）。裁决是替换制（有进必有出），持仓集是选 `remove` 与校验「≤5只」的基准，缺了它反而会让裁决官凭空编造换出标的；被砍掉的是全量候选池（15 只 × 10 余字段 + 板块/性价比嵌套 dict）。
- 争议集 = 两派意见 JSON ∪ 调仓建议文本里出现的 6 位代码 ∩ 候选池代码；白名单求交使日期（`20261010`）、金额（`1000000`）这类长数字串不会误入材料。
- 省略可见：材料段末尾输出「另有 N 只未被提及的候选已从材料中省略」，避免裁决官以为池里只剩 1 只。

**2.3 工具 Schema 类型支持**
- `_python_type_to_json_schema` 增加：`Optional[X]`/`X | None` → 递归取 X 并标 `nullable`；`Literal[...]`/`Enum` → `enum` 数组；`list[dict]`/嵌套 pydantic → 生成 `items.properties`
- `swaps` 参数从裸 `list` 改为带结构描述（在 docstring/Annotated 中定义 `{remove, add, weight?}`），使 nested schema 可生成
- 验证：`suggest_allocation_change` 的 schema 中 `swaps.items` 含 `remove/add` 属性

**2.4 状态判定 bug**
- `run_autonomous` 用显式标志：LLM 调用异常路径置 `llm_error=True`，`status = "failed" if llm_error or not final_content else "completed"`
- 消息去重键改为 `(role, content, created_at)` 或消息 id，不用纯内容比较
- 验证：mock LLM 抛异常 → status=failed；mock 返回以 "LLM" 开头的正常文本 → status=completed

**2.5 经验匹配算法**
- 相似度：**带维度权重的 Jaccard**（`market_regime` 1.5、`volatility` 1.2、其余 1.0；同值出现在多维取最大权重）。原设想把标签写成 `f"{key}={value}"`，落地前核对数据发现 `Experience.scenario_tags` 存的是**裸值**（「高波动」「管道失败」等，来自复盘 LLM / 异常检测 / 定时任务），改键值形式会让两侧永不相交、匹配全部归零，故改为「按值匹配 + 按维度加权」，权重只作用于命中项贡献
- 权重调整曲线放宽：`base * (1 + similarity)`（去掉 ×1.5），failure 乘数由 2.0 降为 1.5
- 冲突检测：**先过标签重叠度门槛（≥0.3 才计分，否则直接返回非冲突）**，并把「type 不同」权重从 0.5 降到 0.25、重叠贡献从 0.3 提到 0.5，消除「类型相反 + 操作不同 = 0.7」的假冲突
- `SmartExperienceMatcher` 改模块级单例（`get_smart_experience_matcher()`），6 处调用点同步替换
- 验证：单测——完全不相干的 failure/success 不再判为冲突；同 regime 场景的冲突仍能识别

### 阶段 P2 — 健壮性

**3.1 管道关键路径与异常口径**
- `_stages` 改为带依赖的有序结构：`[("net_value", deps=[]), ("quotes", deps=[]), ("market_scan", deps=["quotes"]), ...]`
- `_run_stage` 前置检查：任一 dep 本轮失败 → 本阶段标记 `skipped_dependency` 并记账，不执行（避免用过期数据出指标）
- 统一异常策略：`_step_*` 不再吞异常（或改为返回 bool 状态），让 `_run_stage` 真实感知失败
- `autonomous` 的双重降级：降级管道自身失败时写 failure 经验 + 标记阶段 `failed_both_paths`
- 验证：单测 mock `quotes` 抛异常 → `market_scan` 状态为 `skipped_dependency`，检查点不误标 success

**3.2 上下文压缩**
- `estimate_tokens`：中文字符按 1.5 字符/token、其他按 4 字符/token 分别累加；额外计入 `tool_calls` 的 name+arguments 长度
- 分层触发：`ratio ≥ 0.55` 轻度（只摘要最早一半消息），`≥ 0.8` 重度（全量摘要）
- 摘要长度上限改为动态：`min(600, max(200, original_chars // 5))`
- `_trim_fallback` 同时保留最近 4 条 assistant 消息（不丢推理过程）
- 验证：单测——同一批中文消息，新估算 > 旧估算；分层阈值触发正确

**3.3 经验权重单一权威**
- `Experience` 增 `initial_weight` 列（`init_db` 内 ALTER TABLE 兼容迁移，默认 1.0）
- 衰减改指数：`weight = max(0.3, initial_weight * exp(-0.1 * months))`
- 引入 `weight_source`（`decay`/`boost`/`scenario`），同一次 lifecycle 更新内按优先级 `boost > scenario > decay` 结算，后写不覆盖前写的高值（取 max 而非直接赋值）
- 低效处理补后续动作：`review_status=pending` 且 `effectiveness < 3.0` → 直接 `is_active=False`
- 验证：单测——boost 到 2.0 的经验再跑 decay 不应跌回线性基线

**3.4 辩论降级显式标记**
- `evaluate_rotation` 走 `_fallback_quant_decision` 时返回 `debate_degraded: true` + `degrade_reason`
- Orchestrator 中前序 Agent 失败 → 依赖它的下游 Agent 跳过并置 `upstream_missing`，最终 `debate_degraded`
- 前端「决策依据」展示该标记（只读字段，新增不改契约）
- 验证：单测——LLM 不可用路径返回体含 `debate_degraded`

**3.5 剩余 N+1 清理**
- `_check_min_hold_period`：一次查所有持仓 code 的最近买入记录
- `allocation_suggestion_service`：ETF 存在性改 `IN` 查询做差集
- `failure_mode_service`：`import re` 提模块顶 + 预编译；banned_codes 加进程内 TTL 缓存（当日有效，写入失败模式时失效）；查询增加 `expires_date >= today` 过滤
- `scheduler.py` 行情补全：改 `GROUP BY etf_code` 一次取全部最新日期

### 阶段 P3 — 能力边界

**4.1 风控工具补全**
- `get_portfolio_correlation(db, strategy_id)`：持仓 ETF 近 60 交易日日收益相关系数矩阵 + 平均相关度提示
- `get_portfolio_var(db, strategy_id, horizon=1, confidence=0.95)`：历史模拟法 VaR（持仓权重 × 历史日收益分位）
- `check_circuit_breaker(what_if_swaps=None)`：可选参数，模拟「若执行这些换仓后」的仓位/回撤是否触发拦截
- `run_stress_test(scenario=None)`：支持自定义跌幅场景，缺省沿用预设
- 注册进 `@tool`，`get_*` 为 read、`what-if` 亦为 read（不改状态）

**4.2 分析工具补全**
- `get_factor_performance(db)`：当前自适应权重 + 各因子近 20 日 IC 均值/ICIR（复用 `factor_performance_service`）
- `get_sector_impact_analysis(db, strategy_id)`：持仓所在申万板块的当前评分/低配标记 + 逆风持仓清单（复用 `sector_selection_service`）

**4.3 复盘统计口径**
- `avg_return` → `period_return = (last.total_asset - first.total_asset) / first.total_asset`，并另给 `daily_avg_return`（按相邻**交易日**快照，跳过周末间隔）
- 连续失败检测只对 `status == "error"` 计数，`skipped` 单独统计
- 回撤 peak 改为全周期扫描而非仅最近 5 个快照

**4.4 delete_strategy 软删除**
- `Strategy` 增 `status` 字段（`active`/`deleted`，ALTER TABLE 迁移），删除改置 `deleted` 并记录 `deleted_at`
- 所有查询策略的入口默认过滤 `status == 'active'`
- 验证：单测——软删后列表不含该策略，但 DB 记录仍在

## 配置新增（`app/config.py` + `.env.example`）

| 配置项 | 默认 | 用途 |
|---|---|---|
| `market_scan_lookback_days` | 60 | 批量预加载行情的回溯窗口 |
| `sentiment_analysis_batch_size` | 8 | 舆情 LLM 批量分析每批条数 |
| `agent_parallel_max_workers` | 4 | 辩论 Agent 并行线程数 |
| `context_compaction_light_ratio` | 0.55 | 轻度压缩触发比例 |
| `context_compaction_heavy_ratio` | 0.80 | 重度压缩触发比例 |
| `experience_weight_decay_rate` | 0.1 | 经验权重指数衰减率（每月） |

## 验证策略

1. **单测先行**（AGENTS.md 目标驱动要求）：每个优化项先写复现/对照断言再改
   - P0 性能类：**结果等价性**测试（批量 vs 逐只、并行 vs 串行产出一致）+ 调用次数断言，而非只测耗时（耗时依赖机器不稳定）
   - P1/P2 逻辑类：直接断言新行为
2. **回归**：全量 `pytest`，重点 `test_risk_circuit_breaker`、轮动辩论、建议通道相关用例（注意已有 test_full_debate_flow 历史失败，需区分）
3. **集成冒烟**：手动触发单阶段管道（`/api/tasks/trigger-stage`），验证 market_scan → rotation_review → autonomous 全链无异常
4. **Token 预算核对**：改前后各测一次裁决官 prompt 字符数，确认下降且信息未丢

## 风险与回退

| 风险 | 缓解 |
|---|---|
| 并行 Agent 共享 session 导致 SQLAlchemy 线程问题 | 每 Agent 独立 `SessionLocal()`，任务结束即 close；不通过参数传 session 给并行任务 |
| 舆情批量解析数量错位 | 按数组 index 对齐 + 长度校验，不匹配即整批回退关键词评分 |
| Schema 类型改动影响既有工具调用 | 只增不改：新分支处理 Optional/Literal，原 `str/int/float/bool/list` 路径不变 |
| 经验权重迁移丢历史 | `initial_weight` 迁移时用现有 `weight` 值回填 |
| 关键路径阻断导致整晚管道停摆 | 只对 `quotes→market_scan→rotation_review→autonomous` 这条真实数据依赖链阻断，其余阶段维持「失败不中断整体」的既有语义 |
| 单阶段行为变化 | 每项独立 commit，任一回归可单独 revert |

## 交付

- 每项完成即 commit（用户已确认提交节奏）
- 全部完成后 `pytest` 全绿 + 冒烟通过，再询问是否部署到服务器（部署须先问）
