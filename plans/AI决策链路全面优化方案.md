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
| P2 | 3.1 管道阶段关键路径阻断 | ✅ | 15 条单测：quotes 失败 → rebalance/market_scan/regime/rotation/autonomous 全部 skipped_dependency 不执行；检查点不标 completed 且未成功阶段不进 done_stages；失败经验只对真失败阶段写；LLM 返回 error 转降级；双路径失败抛 [failed_both_paths] |
| P2 | 3.2 上下文压缩 Token 估算 + 分层压缩 | ✅ | `TestCompaction` 15 条（新增 10）：同一批中文消息新估算 2035 > 旧口径 800（x2.54；旧口径需约 41 万中文字符才触发 0.8×128k 重度压缩，新口径约 15.4 万）；档位边界 70399/70400/102400 → None/light/heavy；tool_calls 计入；摘要预算随规模伸缩；trim 保留最近 4 条 assistant；loop 实测选中 light 档 |
| P2 | 3.3 经验权重指数衰减 + 单一权威写入 | ✅ | 7 条单测：60 天经验衰减为 e^(-0.2)=0.819（旧线性口径 0.8）；`initial_weight=1.5` 衰减后 1.357 仍高于 1.0；boost 到 2.0 再跑 decay 值与来源都保持（核心回归）；boost/场景调权重复调用不放大（2 次仍 1.3）；评分 2.0 + pending → 停用，无使用数据的新经验不被误停 |
| P2 | 3.4 辩论降级显式标记 debate_degraded | ✅ | 8 条新单测 + 3 条改写：LLM 未配置时计划 `degrade_reason=llm_not_configured` 且量化仍决策（缺口 40 分 → rotate）；一派观点缺失 → 跳过裁决官、量化接管（`upstream_missing: 动量派`）；裁决官失败 → 维持 hold 但带 `judge_failed`；完整辩论无任何标记；建议回执前缀「【未经辩论·降级】」；依据接口透出两字段 |
| P2 | 3.5 N+1 查询清理（最短持有期/建议校验/禁入缓存/行情补全/上下文遗留） | ✅ | 14 条新单测：SQL 计数断言次数不随实体数增长（1 持仓 vs 4 持仓均 1 次 trade_record 查询、1 vs 4 代码均 1 次 etf_basic、3 vs 20 只 ETF 均 1 次 etf_quotation、1 vs 4 策略均 1 次 portfolio_snapshot）；禁入名单二次调用 0 查询、record_failure 后重查；过期失败模式不再规避；批量与逐标的结果逐字段相等 |
| P3 | 4.1 风控工具补全（相关性/VaR/what-if 熔断） | ✅ | 21 条新单测：VaR 用解析期望值断言（5 个亏损日+55 个+0.2%，60 观测下 95% 分位=2%、ES=3.5%）+ √t 缩放 + 置信度夹取 + 待生效优先；相关矩阵 ±1/均值/样本不足/缺行情分支；what-if 四结构与越线组合，并断言 what-if 后 allocation_config/pending_allocation 逐字段不变；工具注册为 read 且经 registry 可执行 |
| P3 | 4.2 因子表现与板块影响分析工具 | ✅ | 21 条新单测：ICIR 用解析值锁 ddof=1（`[0.1,0.3]` → std 0.1414、ICIR 1.4142，总体口径会得 0.1/1.0）；IC 三分支用**真实秩相关**构造（每日恒 +1 → ICIR 无定义、±1 摆动 → 均值 0 判噪声、因子值全同 → 0 观测判样本不足）；板块侧锁逆风名单与合计权重、集中度超限/合规、待生效优先、`mode=off` 无逆风、无板块数据、策略不存在、空配置；两工具 `risk_level=read` 且经 registry 可执行 |
| P3 | 4.3 复盘 avg_return 改期间收益率 | ✅ | 18 条新单测 + 1 条改写：解析值断言 period_return 1.0%、日均 0.5098%、最差单日 -0.98%；横盘 5 快照（累计水位递增）三口径全 0（旧口径会读出 ~22%）；`statistics` 断言已无 `avg_return`；3 次跳过不再误报连续失败、失败夹跳过时 count=3 且 skipped_count=2；高点在 6 个快照之前仍报 -10% 回撤，并修正回撤符号约定使 `drawdown_spike` 与其「建议暂停」首次可达；全量 511 passed |
| P3 | 4.4 delete_strategy 软删除 | ✅ | 18 条新单测：删除后五类关联数据**逐字段计数相等**（快照/交易/持仓/日志/经验各 1 条不减）、策略行仍在（`status=deleted` + `deleted_at` + `auto_strategy_status=stopped`）；列表工具/service/工作台总览/自动策略列表/异动复核/每周复盘共 6 个枚举入口不再拾取，而按 ID 仍可查回（`get_strategy_detail` 返回 deleted、快照 JOIN 策略名可用）；二次删除幂等（`already_deleted=True` 且 `deleted_at` 不变）；`paused`/`archived` 仍留在列表（口径是排除 deleted 而非只留 active）；旧库迁移用真实 `etf_selector.db` 副本实测——1 行策略保留、`integrity_check` ok、老行 `deleted_at` 为 NULL 按未删除处理；全量 529 passed |

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
- 已完成（归入 3.5）：旧 section（目标进度、风控）已改批量查询，查询次数不随策略数增长。

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

实施记录（2026-10-10）：
- 阶段表改为模块级 `DAILY_PIPELINE_STAGES: List[(阶段, 依赖, 执行函数)]`（定义在阶段函数之后），管道遍历同一张表，依赖图与执行顺序单点定义。
- 顺带修掉两个隐性 bug：① `_step_*` 吞异常导致阶段永远「成功」并写检查点；② `run_autonomous` 返回 `result.error` 时旧代码既不降级也不失败——「当日没有任何决策」被记成正常。
- `failed_both_paths` 用**异常文本前缀**承载（进 `TaskExecutionLog.error_message`、检查点 `error_message` 与失败经验签名），不加新列；失败经验仍由 `_run_stage` 单点写入，避免同一次失败记两条。
- 检查点收尾：只要有阶段失败或被依赖阻断，改调 `mark_failed("pipeline", detail)` 而非 `mark_completed`。旧实现无条件 `mark_completed` 并把 `error_message` 清成 None，等于把整条管道的失败记录擦掉；`detail` 形如 `失败: quotes | 依赖跳过: market_scan←quotes; autonomous←market_scan`。
- `sw_sector` / `fundamental` 不设阻断依赖：二者自带「最近落库」降级，且板块/基本面信号缺失时按中性处理，不应连带停掉换仓。
- `policy_flow` 阶段新增 `_step_policy_flow()`：政策评估与资金流向互不依赖（一个读舆情、一个读行情），各自执行、任一失败则阶段失败；手动触发映射同步指向它。
- 附带发现（未改）：`task_routes.PIPELINE_STAGES` 前端阶段清单缺 `sw_sector`，与管道 11 阶段不一致。

**3.2 上下文压缩**
- `estimate_tokens`：中文字符按 1.5 字符/token、其他按 4 字符/token 分别累加；额外计入 `tool_calls` 的 name+arguments 长度
- 分层触发：`ratio ≥ 0.55` 轻度（只摘要最早一半消息），`≥ 0.8` 重度（全量摘要）
- 摘要长度上限改为动态：`min(600, max(200, original_chars // 5))`
- `_trim_fallback` 同时保留最近 4 条 assistant 消息（不丢推理过程）
- 验证：单测——同一批中文消息，新估算 > 旧估算；分层阈值触发正确

实施记录（2026-10-10）：
- 估算改 `_text_tokens`：CJK 区间（含日文假名/扩展A/兼容表意）按 1.5 字符/token，其余按 4 字符/token，逐条消息累加后取整；`tool_calls` 计入 `function.name` + `function.arguments`（ReAct 一轮的 JSON 参数常常比正文长，漏算会让压缩永不触发）。
- `compaction_due()` 删除，改为 `compaction_tier() -> "heavy" | "light" | None`：先判 `min_tokens` 与窗口有效性（窗口 ≤0 直接不压），再按 ratio 先重后轻。**复用既有 `compaction_threshold` 作重度线**（默认同为 0.8），只新增 `context_compaction_light_ratio`（0.55），避免出现两个同义旋钮。
- 轻度档只摘要 `messages[:len//2]`，重度全量。代价是「未被摘要覆盖、又落在出站窗口外」的中间段会随截断一并丢弃（canonical 行不动，只是不再进模型视野）——这是成本换空间的取舍，按方案设计执行。
- 新增 `prior_summary` 参数并由 loop 传入既有摘要：摘要是压缩后唯一的历史载体，旧实现每次 `save_summary` 整份覆盖，多轮压缩会把早期结论丢光；轻度/增量压缩让这个缺陷更频繁，故把旧摘要作为 `[已有摘要]` 并入新的摘要输入。降级 trim 同样拼接旧摘要。
- 摘要预算 `summary_budget()` = `min(600, max(200, 原文字符 // 5))`，通过 `{budget}` 占位符进提示词（小对话不必写 600 字，大对话不能只写 200 字）。
- `_trim_fallback` 除最近 10 条 user 外，再保留最近 4 条 assistant（查过什么/得出什么结论），否则降级后 LLM 会重复劳动。
- `memory.get_history()` 压缩后的 `min(limit, 8)` 改引用 `RECENT_ROUNDS_AFTER_COMPACTION`，与压缩模块出站口径同源（该常量此前是死值）。
- 本地库无 `chat_messages` 表，倍率用合成中文会话实测（20 轮含工具调用：旧 800 → 新 2035）。

**3.3 经验权重单一权威**
- `Experience` 增 `initial_weight` 列（`init_db` 内 ALTER TABLE 兼容迁移，默认 1.0）
- 衰减改指数：`weight = max(0.3, initial_weight * exp(-0.1 * months))`
- 引入 `weight_source`（`decay`/`boost`/`scenario`），同一次 lifecycle 更新内按优先级 `boost > scenario > decay` 结算，后写不覆盖前写的高值（取 max 而非直接赋值）
- 低效处理补后续动作：`review_status=pending` 且 `effectiveness < 3.0` → 直接 `is_active=False`
- 验证：单测——boost 到 2.0 的经验再跑 decay 不应跌回线性基线

实施记录（2026-10-10）：
- `Experience` 新增 `initial_weight`（默认 1.0，衰减基线）与 `weight_source`（decay/scenario/boost），走 `init_db()` 的 PRAGMA + `ALTER TABLE` 兼容迁移；实测旧库迁移后既有行 `initial_weight` 取默认值 1.0、`weight_source` 为 NULL（按最低优先级 decay 处理）。
- 衰减改 `max(0.3, initial_weight × e^(-rate × 月数))`，rate 从 `settings.experience_weight_decay_rate`（0.1/月）读取，`LIFECYCLE_CONFIG` 里删掉同名的硬编码值，避免两处真值。
- `_set_weight()` 为唯一写权重的入口：先统一封顶 `max_weight=2.0` 再结算，若现值来自更高优先级规则且不低于本次结果，则**整笔作废（值与 weight_source 都保持）**——只取 max 而仍把 source 写成 decay 的话，下一次衰减就又能把它抹掉，保护等于只生效一次。
- boost 与场景调权改为**以衰减基线为参照换算**（`_decay_weight() × 系数`）而不是在上一次的存量权重上再乘：二者原本可被重复调用逐次放大。原 `min(2.0, weight×2)` 的封顶语义由 `_set_weight` 统一承担。
- `_check_effectiveness` 补上停用动作，但**以 `success_rate is not None` 为闸门**：`effectiveness_score` 默认 0.0，若照字面按「pending 且 <3.0 就停用」实现，所有刚生成、尚无足量使用数据的经验会在第一轮生命周期里被全部停用（直接把经验闭环关掉）。`prune_low_effectiveness` 的 3.0 硬编码同步改用 `prune_effectiveness_threshold` 配置项，与停用线同源。
- 附带发现（未改）：① `SmartExperienceMatcher.update_experience_weights()` 是直接写 `exp.weight` 的第二个写入口，但全仓无调用方，暂不动；② `tests/test_sector_rotation.py` 在 helper 内才 `from app.models.sw_industry import SwIndustryDaily`，而 `setUp` 先 `make_db()`，导致该文件单独跑或用 `-k` 筛跑时因建表早于模型导入而报 `no such table: sw_industry_daily`（全量跑靠别的模块先导入而掩盖）。

**3.4 辩论降级显式标记**
- `evaluate_rotation` 走 `_fallback_quant_decision` 时返回 `debate_degraded: true` + `degrade_reason`
- Orchestrator 中前序 Agent 失败 → 依赖它的下游 Agent 跳过并置 `upstream_missing`，最终 `debate_degraded`
- 前端「决策依据」展示该标记（只读字段，新增不改契约）
- 验证：单测——LLM 不可用路径返回体含 `debate_degraded`

实施记录（2026-10-10）：
- `degrade_reason` 用**原因码前缀**承载机器可判读的口径，中文只作展示：`llm_not_configured` / `upstream_missing: 动量派` / `judge_failed` / `debate_exception: <异常前120字>`。前端 `degradeLabels` 做码→中文映射，原始 reason 放 tooltip，避免把中文文案当契约。
- **偏离方案字面（有意的行为变更）**：原实现是「一派失败仍把单边材料交给裁决官」（`test_one_side_error_still_reaches_judge` 明确断言过）。现改为一派缺失即**跳过裁决官**并返回 `upstream_missing`，由 `rotation_service._run_debate` 接管退回量化通道——对抗式辩论缺一派就只剩一面之词，硬裁决只会产出偏向存活一方的结论，且白花一次 LLM 调用。该用例已改写为 `test_one_side_error_skips_judge_and_marks_degraded`（断言 `judge.analyze` 未被调用）。
- 三类降级的**处置不同**：`llm_not_configured` / `upstream_missing` / `debate_exception` → 量化兜底**仍可换仓**（缺口 40 分实测 action=rotate）；`judge_failed`（裁决官自己出错）→ 沿用原口径**维持持仓**，只补标记。理由是前者是"没有 LLM 但材料完整"，后者是"材料完整而裁决环节出错"。
- 降级标记的落点：`evaluate_rotation` 三个含辩论的返回体（hold-after-debate / hold-无有效替换 / rotate）、`_record_condition_review` 的 `analysis_result`、`strategy_evidence_service._last_decision`。**未在每日 `rotation_review` 阶段的返回体加**——该阶段本就不写 `action_type="analyzed"` 日志，前端「决策依据」读不到它，加了也没有消费方；对用户可见的两处是条件复核留痕与**建议回执**（`settle` 的 note 前缀「【未经辩论·降级】」，3 处 settle 全带）。
- 附：`static/js/workbench.js` 徽章 `.ev-dec-degraded` 用 `var(--warning)` 橙（非红非绿，避开 A股涨跌色语义），静态资源 bump 至 v44/v52。**该徽章未在浏览器实测**：本地库没有带 `debate_degraded` 的 analyzed 日志，为造数据而写运行时库不合适；仅做了 `node --check` 语法校验与 diff 复核。
- 踩坑记录：`rotation_service` 是在函数内 `from app.config import get_settings`，故 patch 目标必须是 `app.config.get_settings`，写成 `app.services.rotation_service.get_settings` 会报「module does not have the attribute」；另因 `make_db()` 只按「当前已导入的模型」建表，测试文件须在模块顶导入 `AllocationSuggestion`，否则首个用例先跑 `make_db()` 会报 `no such table: allocation_suggestion`（即 3.3 附带发现②的同一坑）。
- 附带发现（未改）：`rotation_service.py:167` 与 `:173` 是同一个返回体里的重复键 `"suggestion_execution"`，后者覆盖前者且值相同，属无害冗余；与本次需求无关，按精准修改原则不动。

**3.5 剩余 N+1 清理**
- `_check_min_hold_period`：一次查所有持仓 code 的最近买入记录
- `allocation_suggestion_service`：ETF 存在性改 `IN` 查询做差集
- `failure_mode_service`：`import re` 提模块顶 + 预编译；banned_codes 加进程内 TTL 缓存（当日有效，写入失败模式时失效）；查询增加 `expires_date >= today` 过滤
- `scheduler.py` 行情补全：改 `GROUP BY etf_code` 一次取全部最新日期

实施记录（2026-10-10）：
- 最短持有期改 `_latest_buy_dates(strategy_id, codes, db)`（`in_` + `group_by` + `func.max` 一次取回）+ 纯判定 `_held_long_enough(last_buy, scan_date)`；`evaluate_rotation` 在循环前取一次、把结果同时传给持有期过滤与 `_collect_proposals`。**偏离方案字面**：`_check_min_hold_period` 没有改成批量版而是**整个删除**——它只被 `evaluate_rotation` 的单标的路径调用，批量口径下已无调用方，留一个只有测试用的包装函数属死代码。`_collect_proposals` 的签名同步去掉不再需要的 `db` 参数（改为收 `last_buys`），两处调用点与 `tests/test_allocation_suggestion.py` 已同步。
- 建议标的校验：一次 `ETFBasic.etf_code.in_(...)` 取回已知集合做差集，报错文本仍列**全部**未知代码（不是首个即返回），单测断言 4 个未知代码时错误里同时含 `159995` 与 `512760`。
- 禁入缓存的键是 `(engine 身份, 当日, 结果)`，**不是只按日期**：`FailureModeService` 是进程单例，同进程可能挂着多个库（测试用内存库、多环境），只按日期取键会跨库读到别处的规避名单。这个坑在本次实施中被单测真实打到——`tests/test_debate_degraded.py` 单独跑正常、接在其他建库用例之后因读到前一个库的名单而提前命中「无候选标的」返回体（不含 `debate_degraded` 键）报 `KeyError`，改按 `is bind` 比较后全量转绿。阈值非默认值（`threshold` 显式传入）时不读写缓存，避免把带参结果当通用名单缓存。
- 缓存失效点有两处：`record_failure()` 两条提交路径后调 `invalidate_banned_cache()`；`scheduler._record_failure_experience()` 是**绕过服务直写 `Experience`** 的第二个写入口，累计次数可能刚跨过阈值，故也在提交后失效（否则当日新跨阈值的失败模式被旧缓存漏过）。
- 过期口径 `or_(expires_date.is_(None), expires_date >= today)`：用 `is_(None)` 放行历史 NULL 行，若只写 `>= today`，早期未填过期时间的失败模式会全部失去规避（静默放宽风控）。
- `_llm_plan_fetch` 之前的行情新鲜度判定改一次 `GROUP BY etf_code` + `func.max(trade_date)`（ETF 池上千只时，逐标的查就是上千次查询），`stale_info` 由 map 组装，无行情仍标「无数据」。
- 上下文快照的两处遗留查询改新增批量 API：`RiskController.assess_risk_map(strategies, db)`、`PortfolioService.get_monthly_progress_map(strategies, db)`。为此把判定逻辑抽成纯静态方法 `_eval_circuit_breaker` / `_eval_drawdown_protection` / `_monthly_progress`，原 `check_circuit_breaker(strategy_id, db)`、`apply_drawdown_protection(strategy_id, db)`、`get_monthly_progress(strategy_id, db)` **签名与返回口径不变**（公开 API 的调用方很多，改签名会波及风控/前端），批量入口只是「一次查询 + 同样的纯判定」。`assess_risk_map` 明确只读不写库（与 `apply_drawdown_protection` 原本就不落库一致）。
- 验证方式：`tests/test_n_plus_one_cleanup.py` 14 条用例**用 SQL 计数断言「次数不随实体数增长」**（`event.listen(engine, "before_cursor_execute")`），而不是只看结果对不对——只看结果无法区分一次批量与 N 次单查。关键断言：最短持有期 1 vs 4 个持仓均为 1 次 `trade_record` 查询；ETF 存在性 1 vs 4 个代码均为 1 次 `etf_basic` 查询；禁入名单同日二次调用 0 次查询、`record_failure` 后重新查 1 次；行情补全 3 vs 20 只 ETF 均为 1 次 `etf_quotation` 查询；进度/风控 section 1 vs 3~4 个策略均为 1 次 `portfolio_snapshot` 查询。批量与逐标的**等价**另有 3 条用例锁死（`_latest_buy_dates` 含重复买入取最近、`assess_risk_map` 与单策略返回逐字段相等、`get_monthly_progress_map` 同上）。全量 451 passed（顺序跑与随机序各验证一次）。
- 踩坑记录：`PortfolioSnapshot` 没有 `holdings`/`positions` 列（只有 `total_asset/cash/market_value/profit/profit_pct`），按直觉造 fixture 会 `TypeError: 'holdings' is an invalid keyword argument`；另本文件 `make_db()` 在模块顶导入**全部** `app.models` 子模块后再建表，避免 3.3/3.4 记录过的「建表早于模型导入 → no such table」顺序坑。
- 附带发现（未改）：`get_banned_codes` 的缓存只在 `threshold` 为默认值时生效，而管道每轮只调一次，实际收益有限；跨进程（多 worker）仍各自查库，未引入 Redis 等外部依赖（符合项目约束）。
- 取舍：`_get_strategies_summary` 的批量进度查询用**一个** try/except 包住（原实现每策略各包一个），批量口径下失败本就影响全部策略，分开包没有意义——代价是进度整体退化为空文本，而原来可能只少一个策略。

### 阶段 P3 — 能力边界

**4.1 风控工具补全**
- `get_portfolio_correlation(db, strategy_id)`：持仓 ETF 近 60 交易日日收益相关系数矩阵 + 平均相关度提示
- `get_portfolio_var(db, strategy_id, horizon=1, confidence=0.95)`：历史模拟法 VaR（持仓权重 × 历史日收益分位）
- `check_circuit_breaker(what_if_swaps=None)`：可选参数，模拟「若执行这些换仓后」的仓位/回撤是否触发拦截
- `run_stress_test(scenario=None)`：支持自定义跌幅场景，缺省沿用预设
- 注册进 `@tool`，`get_*` 为 read、`what-if` 亦为 read（不改状态）

实施记录（2026-10-10）：
- 服务落点在 `RiskController`（新增 `get_diversification` / `get_var`，工具名仍按方案叫 `get_portfolio_correlation`、`get_portfolio_var`）：相关度与 VaR 都是「组合层面风险度量」，与熔断/回撤/风险预算同源，另起 service 会把风险口径拆成两处真值。
- 取价一次 `in_(codes)` + Python 组网格（`_load_price_series`→`_aligned_returns`）：多取 `window×1.8+20` 日历日以覆盖停牌/节假日，再按标的截到最近 `window+1` 个观测，最后在**所有标的都有价的公共交易日**上算日收益——否则不同 ETF 的缺失日会让相关系数与组合收益串期。
- VaR 用**历史模拟**（权重合成组合日收益后取经验分位），不做正态假设：ETF 日收益普遍尖峰厚尾，参数法会系统性低估尾部损失。同时给出 `expected_shortfall_pct`（分位以下均值），因为 VaR 只说「至少亏这么多」，尾部均值才是 LLM 判断「最坏能坏到哪」需要的数。
- 多日 VaR 按 `√t` 缩放（`horizon=4` 即 2 倍单日），文档与 `text` 里写明该假设；置信度夹到 `[0.5, 0.999]`、`horizon` 下限 1，避免 LLM 传 0.9999/0 这类样本量撑不起或无意义的参数。
- **基准口径**：`get_diversification`/`get_var`/what-if 用 `get_effective_target_allocation`（待生效优先），与轮动/执行一致——否则「周一会执行的那套配置」的风险数永远看不到。`run_stress_test` 与 `check_risk_budget` 仍按 `allocation_config`（本次未改，属既有口径）。
- what-if 明确**不重复轮动通道的硬约束裁决**：`_apply_swaps` 只做换出/换入/权重换算与结构合法性（remove 在组合、add 不在组合、替换后合计≈1.0），仓位是否越线复用 `_eval_risk_budget`，尾部风险用**同一交易日网格**上的前后两次 VaR 相减（各自取公共日会得到不可比的差值）。有进必出、每次≤2只、最短持有期、禁入名单仍归 `_collect_proposals`。
- `blocked` 的三个来源合并成一个字段：现网熔断已触发 / 替换后仓位越线 / 结构不成立。熔断已触发时文案单独说明「先按冷却期处理再谈换仓」——这与「换仓本身好不好」是两件事，混在一起会让 LLM 误判替换质量。
- `_eval_risk_budget` 违规项新增 `over_codes`（原来只有 `current`=max，多项越线时看不出是谁）。
- 单测 21 条：VaR 有**解析期望值**（构造 5 个亏损日 + 55 个 +0.2% 的收盘价序列，60 个观测下 95% 分位固定落在第 4 差的 -2%，ES=3.5%），不是只断言「返回了个数」；另锁 √t 缩放、置信度夹取、待生效优先、相关矩阵 ±1 与均值、样本不足/缺行情/策略不存在的分支、what-if 四种结构与越线组合、**以及 `allocation_config`/`pending_allocation` 在 what-if 后逐字段不变**（这是「只算不改库」的硬约束）；工具层 2 条验证注册、`risk_level=read` 与经 registry 执行。全量 472 passed。
- 踩坑记录：`run_stress_test` 原实现用 `for scenario in scenarios` 且体内取 `scenario["name"]`，新增同名参数后二者冲突——体内引用已改名 `item`；`if scenario:` 会把显式传的空 dict 当「没传」静默走预设，改 `is not None` 让其按非法场景报错；单标的VaR 在「60 个观测只有 1 个亏损日」时 95% 分位其实是正收益（VaR 为负），不是 bug 而是样本分布，故测试fixture 必须铺够尾部观测。
- 附带发现（未改）：`RISK_CONFIG["risk_budget"]["max_single_position"]=0.30` 比轮动硬约束的单只 40% 更严，二者语义不同（风险预算建议线 vs 换仓准入线），未统一；what-if 用的是 0.30 这条。

**4.2 分析工具补全**
- `get_factor_performance(db)`：当前自适应权重 + 各因子近 20 日 IC 均值/ICIR（复用 `factor_performance_service`）
- `get_sector_impact_analysis(db, strategy_id)`：持仓所在申万板块的当前评分/低配标记 + 逆风持仓清单（复用 `sector_selection_service`）

实施记录（2026-10-11）：
- 两个工具都落 `app/tools/analysis_tools.py`（与 `run_multi_agent_analysis`/`get_review_report` 同层的只读分析工具），service 不改；IC 统计放工具层私有函数 `_ic_stats`/`_ic_reliability`，与 `get_sentiment_data` 在工具内做正负计数的既有先例一致，不为一个消费方新增 service API。
- **与 `get_sector_rotation` 的分工**（避免重复工具）：那个是**板块视角**——全市场 31 个申万一级按评分排名，回答「现在哪些板块强」；新工具是**持仓视角**——我持有的标的各自站在什么板块信号里、谁逆风、板块集中度是否超限。数据源同为 `sw_industry_service.get_sector_view`，但输入集合与结论不同，不合并。
- ICIR = `mean(IC) / std(IC, ddof=1)`。只看 IC 均值会把「今天 +1、明天 -1」的因子当有效（均值 0 但完全不可用），除以标准差惩罚的正是这种不稳定；一个稳定在 0.05 的因子比在 ±0.6 之间跳荡的均值 0.2 更可用。样本标准差用 ddof=1（截面观测是总体的抽样）。`n<2` 与 `std=0` 都返回 **None 而不是 0**：0 会被读成「稳定地没用」，None 才是「没法判断」，`reliability` 相应分「样本不足 / IC 无波动」两档。
- 因子列按 **|IC 均值| 降序**排（与自适应权重的 |IC| 归一口径一致，权重列与因子列不会互相打脸），ICIR 只作 `reliability` 注脚（稳定 ≥0.5 / 一般 ≥0.3 / 噪声）。
- 方案里的「近 20 日」实现为 `ic_days=20` **自然日**（沿用 `get_ic_history` 已有的 `today - timedelta(days)` 窗口口径，不新造一套交易日窗口语义），实测约对应 14 个交易日截面；`ic_days`/`lookback_days` 夹到 `[5, 250]`，避免 LLM 传 0 或 100000。
- 截面因子值全相同（如停更的 `capital_flow`）时 Spearman 分母为 0，service 已丢弃该日 → 工具里表现为 `obs_days=0` + 「样本不足」，**不会伪装成 IC=0**。
- 板块判定不自己写阈值：直接调 `sector_selection_service.annotate()` + `split()` 取 `meta["headwind_holdings"]`，因此逆风口径与选型/辩论材料**同源**（改阈值只有一处真值）。`sector_rotation_mode=off` 时信号照常展示、但 `headwind` 全 False，`text` 明写「板块层未启用…仅为展示，不参与选型」，防止 LLM 把展示当指令。
- 板块集中度阈值取 `RiskController.RISK_CONFIG["risk_budget"]["max_sector_concentration"]`（0.40）——该配置此前**没有任何消费方**（`_eval_risk_budget` 只校验单只仓位），本工具是第一个；不在工具里另写 40%，避免与风控预算两处真值。
- 基准口径与 4.1 一致用 `get_effective_target_allocation`（待生效优先），ETF 名称一次 `in_()` 取回（板块归类靠名称关键词反查，逐只查就是 N+1）。
- 两种「没有板块」分开表达：`status=no_sector_data`（sw_sector 阶段未同步，全部持仓 `sector=None`，信息是「数据缺失别下结论」）vs `unmatched_codes`（宽基/跨行业 ETF 本就不在关键词表，属正常、按中性）。
- 踩坑记录：测试 helper 写 `alloc or {默认三持仓}` 会把显式传入的 `{}` 当「没传」（`{} or x → x`），于是「空配置 → error」分支拿到的是默认持仓而 `KeyError: 'error'`，改 `if alloc is None else`；`_ic_stats([0.1,0.3])` 的样本标准差是 0.1414 不是 0.2（首版按总体口径写断言）；构造 IC 摆动分支必须用**偶数个**观测日——3 天时 ±1 的均值是 +0.333 而非 0，噪声分支不成立。
- 附带发现（未改）：本工具要扫两遍 `factor_performance`（`get_ic_history` 与 `get_adaptive_weights` 各自重算逐日截面 IC），因两个窗口独立（`ic_days` vs `lookback_days`）不能简单合并成一次查询；每轮决策只调一次，暂不优化。另 `docs/工作流程.md` 的工具清单表已与实际注册名不符（`get_sentiment_summary`、`match_experience` 均非注册工具名，也缺 4.1/4.2 新增工具），属历史文档，未整理。
- 附带发现（未改）：截面因子值恒定时 numpy 在 `np.corrcoef` 内抛 `invalid value encountered in divide` RuntimeWarning（service 既有行为，已用 `isnan` 兜住并跳过该因子），测试造常量因子会看到该告警。

**4.3 复盘统计口径**
- `avg_return` → `period_return = (last.total_asset - first.total_asset) / first.total_asset`，并另给 `daily_avg_return`（按相邻**交易日**快照，跳过周末间隔）
- 连续失败检测只对 `status == "error"` 计数，`skipped` 单独统计
- 回撤 peak 改为全周期扫描而非仅最近 5 个快照

实施记录（2026-10-11）：
- 口径改在 `_collect_period_data`，新增纯静态方法 `_period_returns(snapshots)`（先按 trade_date 升序、过滤 None/0 资产，再由**相邻净值**推导），`profit_pct` 彻底退出收益统计：它是相对初始资金的**累计**水位（`portfolio_service.py:180`），对它求平均会把「成立以来涨 20%、本周横盘」读成「本周平均收益 20%」；对它取 min 同理是「历史最低累计水位」而不是损失。
- **偏离方案字面 1**：方案写的 `status == "error"` 在实际枚举里不存在——`auto_strategy_executor.py:340` 落的是 `success`/`failed`/`skipped`（模型注释同源），照字面实现会让连续失败检测**永不触发**。现按 `status == "failed"` 计数，`skipped` 单独 tally 进 `skipped_count`，异常体带 `skipped_count` 且 message 写「其间跳过 N 次」。
- **偏离方案字面 2**：`max_loss` 一并改口径为「期间最差单日」。它与 avg_return 同病（都对累计值取 min），只改一个会让同一张 prompt 里一个说区间、一个说成立以来。
- skipped 既不进 `failure_count` 也不进 `failure_cases`（prompt 的「典型失败案例」），但 `success_rate` 分母仍是 `total_count`：跳过是「当天跑过管道但没动作」（非交易日、熔断冷却、无候选），把它从分母摘掉会让成功率虚高，故保留分母、另列 `skipped_count` 供读者判断。
- **顺带修一处静默失效**：`_calculate_recent_drawdown` 原返回 `(peak-current)/peak`（恒 ≥0），而调用处比的是 `< -0.03`、文案用 `abs()`——符号约定不一致使 `drawdown_spike` 异常**从未触发过**，连带 `suggest_parameter_adjustments` 的「回撤突增 → 建议暂停」也是死分支。现返回**负数**跌幅并在 docstring 写明两处修正。这是真实行为变更：以前不会提的暂停建议现在会提（该函数只在 GET 路由 `auto_strategy_routes.py:656` 出参、不写库，需人工确认）。
- 峰值扫描由 `snapshots[:5]` 改**全窗口**（查询本身 `limit(30)`），去掉 `len(snapshots) >= 5` 闸门（已有 `<2 return` 保护）；`large_loss` 仍只看最近 5 个间隔——它的语义是「近期单日」，不是全周期。
- 消费方同步：prompt 字段 `{avg_return}` → `{period_return}`/`{daily_avg_return}`/`{skipped_count}`/`{return_days}`（`{max_loss}` 文案改「最差单日」）、`_build_review_report.statistics`、`_generate_summary`（三档都带区间收益）、`compare_periods` 的 `avg_return`→`period_return`、`avg_return_diff`→`period_return_diff`、前端 `app.js` 复盘卡片「平均收益」→「区间收益」（`index.html` 引 app.js 无 `?v`，workbench 不加载 app.js，故无资源 bump）。`trigger_review` 工具只透传 `statistics`、不点名字段，无需改。
- `return_days`（间隔数）随 period/日均一起进 prompt 与 statistics：日均 0.3% 在「3 个间隔」和「20 个间隔」下的可信度完全不同，不给样本数的日均是没法用的数。
- 验证方式：18 条新单测（`tests/test_review_period_stats.py`）+ 1 条改写（`test_prompt_templates.py`）。解析值：`[100000,102000,101000]` → period 1.0%、日均 0.5098%、最差单日 -0.98%；**横盘回归**（5 个相同资产 + 递增 profit_pct）→ 三口径全 0，锁死旧口径不复活；顺序不敏感（升序与倒序同结果）；脏快照（None/0）被跳过而不炸；`statistics` 断言 `assertNotIn("avg_return")`；skipped 分账 4 条（3 次跳过不误报、跳过夹失败中间时 count=3/skipped_count=2、success 断链）；回撤 3 条（符号解析值 -1/11、**高点在 6 个快照之前**仍报 -10%、-2.5% 不报）+ 1 条端到端「建议暂停现在可达」；LLM prompt 用真实 period_data 渲染（防漏键 KeyError）。全量 511 passed。
- 踩坑记录：`_calculate_recent_drawdown` 沿用 DB 的**倒序**快照（`values[0]` 是最新），而 `_period_returns` 自己排序——两个方法对同一名参数的顺序期望不同，测试 helper 最初按升序喂 drawdown 结果把「上涨序列」判成 -9% 回撤；现 `snap()` 明确「assets[0] 最早，倒序传 `[::-1]`」。另 `detect_anomalies` 在快照 <2 时直接返回空，所以纯状态分账用例也必须铺净值序列。
- 附带发现（未改）：`docs/AI全自动驱动策略模块设计文档.md:1165-1166` 复制了一份旧 prompt 文本（仍是 `{avg_return}`/`{max_loss}`），属历史设计文档，不作真值来源。

**4.4 delete_strategy 软删除**
- `Strategy` 增 `status` 字段（`active`/`deleted`，ALTER TABLE 迁移），删除改置 `deleted` 并记录 `deleted_at`
- 所有查询策略的入口默认过滤 `status == 'active'`
- 验证：单测——软删后列表不含该策略，但 DB 记录仍在

实施记录（2026-10-11）：
- 三个落点全在模型层：`STATUS_DELETED` 常量、`deleted_at` 列、`Strategy.not_deleted()` 过滤条件；软删除动作本身只有 `strategy_service.mark_deleted` 一处，LLM 工具 `delete_strategy` 与人工 API `DELETE /api/strategy/{id}` 都走它——两条删除路径语义不分叉（同一策略此前一个物理删五张表、一个只删策略行，本身就不一致）。
- **偏离方案字面 1**：方案写「入口默认过滤 `status == 'active'`」，实现是 `not_deleted()`（`status <> 'deleted'`）。`status` 实际有四个合法值，暂停/归档策略必须继续出现在列表、复盘与依据视图里——`_run_anomaly_reviews`/`_job_weekly_review` 的既有注释就明确「暂停期恰恰最需要总结经验」，照字面做白名单会把暂停策略从复盘里抹掉，那比物理删除更彻底地隐身。`test_paused_and_archived_stay_visible` 锁死这条口径。
- **偏离方案字面 2**：方案写「`Strategy` 增 `status` 字段」，该字段本就存在（`models/strategy.py:29`），实为补 `deleted_at` 列 + 新增 `deleted` 取值；迁移项加进 `init_db()` 既有的 strategy `migrations` 列表（与 `paused_cooldown_days` 同风格，不引入 Alembic）。
- **真实行为变更**：原工具一次调用会 `.delete()` 净值快照/交易/持仓/执行日志/经验五张表再删策略本身，而这是 AI 可自主触发、审批通过后不可逆的操作。现只改状态，返回体带 `retained_records` 五类计数，让调用方（人和 LLM）看得见「删掉了可见性、留下了什么数据」。
- **按 ID 单点查询故意不过滤**（`get_strategy(id)` 及 40+ 处按 strategy_id 的工具/service/agent 查询保持原样）：已删策略的净值曲线、决策依据、进化提示词仍需可查回，恢复路径也依赖它（`update_strategy_status(id, "active")` 即复活，并清空 `deleted_at`，不留「active 却带删除时间」的矛盾态）。若按 ID 也过滤，删除就变成「查不到但外键仍指向」的半死状态。
- 停自动运行：`mark_deleted` 同时置 `auto_strategy_status="stopped"`，管道里按 `running` 过滤的作业（轮动复核、资金流向、情绪极端复核、自主决策及其经验回写）与 `resume_if_cooldown_expired` 的 `paused` 过滤天然不再拾取，无需逐个加条件；只有**覆盖非 running 策略**的两个复盘入口需要显式 `not_deleted()`，否则删掉的策略每周照样跑 LLM 复盘并沉淀经验。`portfolio_service` / `context.py` 的三处 `status == "active"` 过滤无需改。
- `pending_allocation` 不清空：删除后该策略已脱离全部枚举，待生效配置再无消费方；清空反而违背「数据保留可查回」。
- 前端两处确认弹窗的「删除后无法恢复，相关…也会被清除」在软删除下是假话，改为说明保留范围（`index.html` 引 app.js 无 `?v`，workbench 不加载 app.js，故无静态资源 bump；两处文案改动未在浏览器实测，仅 `node --check`）。
- 验证：18 条单测（`tests/test_soft_delete.py`，含 legacy 表 DDL 跑 `init_db` 两次不报错的幂等迁移用例）+ 真实本地库副本迁移实测（复制 `etf_selector.db` → patch `app.db.database.engine` → `init_db()`：列补上、1 行策略保留、`PRAGMA integrity_check` ok、老行按未删除处理）。
- 踩坑记录：`Experience.expires_date` 是 `nullable=False` 且无列默认（由业务代码传），seed helper 漏填直接撞 NOT NULL；`_job_weekly_review` 带 `@log_task_execution` 装饰器，装饰器与任务体各开一个 session，沿用仓内既有写法 `patch("app.db.database.SessionLocal", return_value=db)`（内存库走 SingletonThreadPool，`close()` 后 session 仍复用同一份数据）。
- 附带发现（未改）：`agent_core/context.py:335` 的进化提示词 JOIN 用 `Strategy.status == "active"`，于是**暂停策略的进化提示词从不注入 LLM 上下文**，与复盘侧「暂停期最需总结经验」的口径相左；属既有行为、与本项无关，未动。

## 配置新增（`app/config.py` + `.env.example`）

| 配置项 | 默认 | 用途 |
|---|---|---|
| `market_scan_lookback_days` | 60 | 批量预加载行情的回溯窗口 |
| `sentiment_analysis_batch_size` | 8 | 舆情 LLM 批量分析每批条数 |
| `agent_parallel_max_workers` | 4 | 辩论 Agent 并行线程数 |
| `context_compaction_light_ratio` | 0.55 | 轻度压缩触发比例 |
| ~~`context_compaction_heavy_ratio`~~ | 0.80 | **未新增**：重度线复用既有 `compaction_threshold`（默认同为 0.8），避免两个同义旋钮 |
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
