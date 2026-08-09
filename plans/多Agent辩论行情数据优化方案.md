# 多Agent辩论行情数据优化方案

## 实施状态（2026-08-09）

| 项 | 状态 | 验证 |
|---|---|---|
| 方向1 数据新鲜度 `_ensure_fresh_data` | ✅ | mock：新鲜→fresh、滞后触发同步→synced/stale、无数据→stale |
| 方向2 工具实时取数 `call_llm_with_tools` | ✅ | mock：LLM 首轮调 get_etf_detail→次轮最终JSON；无db退化为普通调用；read工具过滤write |
| 方向3 多空喂更多数据 | ✅ | mock：Bull 传 macro_report→PROMPT含宏观段；缺省→"暂无" |
| 方向4 快照锁定 | ✅ | mock：指标/净值带 end_date 只取该日前数据 |
| 回归 | ✅ | pytest 82 passed（仅既有 test_full_debate_flow 历史失败）；服务启动无导入错误 |

## 目标（老杨确认的四个方向）

1. **数据新鲜度**：辩论前检查 `ETFQuotation` 最新交易日，过期自动同步或提示滞后
2. **辩论 agent 工具实时取数**：`BaseAgent` 支持 tools + 多轮 tool calling，行情工具开放给辩论 agent 按需取数
3. **多空辩论喂更多数据**：Bull/Bear 增加宏观/跨资产/波动率 report
4. **一致性保障**：辩论中锁定 `analysis_date` 快照，防止运行期间数据错位

## 现状

- `BaseAgent.call_llm`：单次无 tools 调用，返回 JSON
- 技术分析师自己查库算指标（`technical_indicator_service`），多空研究员只读两份预计算 report
- `Orchestrator.analyze`：顺序调用各 agent，无新鲜度检查、无快照锁定
- 行情工具（`get_etf_history`/`get_etf_detail`/`sync_market_data`）已注册，但只给对话侧 agent_core 用

## 模块结构

```
app/agents/base.py                    # + call_llm_with_tools（多轮 tool calling）
app/agents/orchestrator.py            # + _ensure_fresh_data + 快照锁定 + 传参
app/agents/bull_researcher.py         # + tools 取数 + 宏观/跨资产/波动率 report
app/agents/bear_researcher.py         # 同上
app/agents/technical_analyst.py       # 传 data_lock_date
app/agents/market_analyst.py          # _get_nav_changes 加 lock_date
app/services/technical_indicator_service.py  # calculate_all_indicators 加 end_date
app/config.py                         # + debate_max_data_lag_days
```

## 实施步骤

### 方向1：数据新鲜度（orchestrator.py）

- 新增 `_ensure_fresh_data(etf_codes, analysis_date, db)`：
  - 查策略 ETF 的 `max(trade_date)`
  - `lag_days = (analysis_date - max_date).days`
  - `lag_days > debate_max_data_lag_days`（默认3）→ 调 `sync_market_data` 逻辑（`DataService.update_today_quotes`）先拉再分析
  - 返回 `{status: fresh|synced|stale, latest_date, lag_days}`
- `analyze()` 开头调用；结果塞进 `combined["data_freshness"]`
- 状态语义：fresh=数据够新未同步；synced=同步后数据够新；stale=同步仍滞后（提示"数据滞后到 X 日"）

### 方向2：工具实时取数（base.py + bull/bear）

- `BaseAgent._get_read_tool_schemas()`：从 `ToolRegistry.get_openai_tools()` 过滤 `risk_level=="read"`（不含写工具）
- `BaseAgent.call_llm_with_tools(prompt, db, temperature=0.3, max_rounds=3)`：
  - 多轮循环：LLM 带 tools 调用 → 返回 `tool_calls` → `ToolRegistry.execute(name, args, db)` 执行（只读工具直接执行）→ 结果回填 tool 消息 → 再调 LLM
  - 无 `tool_calls` 或达 `max_rounds` 结束；异常捕获返回 `{"error": ...}`
  - `db` 为 `None` 时退化为普通 `call_llm`（无工具）
- Bull/Bear：`analyze` 加 `db`（可选，向后兼容）与工具取数；prompt 注明"数据截至 {data_date}，可调用工具获取补充数据"

### 方向3：多空辩论喂更多数据（bull/bear）

- `analyze(technical_report, sentiment_report, macro_report=None, cross_asset_report=None, volatility_report=None, db=None)`
- PROMPT 增加「宏观周期」「跨资产相关性」「波动率体制」三段（缺失时显示"暂无"）
- Orchestrator 传入已有 report

### 方向4：快照锁定（technical_indicator_service / market_analyst）

- `technical_indicator_service.calculate_all_indicators(etf_code, db, days=60, end_date=None)`：查询加 `trade_date <= end_date`，兼容缺省（不锁）
- `TechnicalAnalyst.analyze` 加 `lock_date` 参数 → 传入 `batch_calculate_indicators`
- `MarketAnalyst._get_nav_changes` 加 `lock_date`：查询 `trade_date <= lock_date`
- `Orchestrator.analyze` 开头计算 `data_lock_date` = min(策略 ETF 最新 max_date, analysis_date)，传给技术分析师与主管
- Bull/Bear prompt 注明数据截至日期，LLM 取数时自行带 end_date

## 配置新增（app/config.py）

| 键 | 默认 | 说明 |
|---|---|---|
| `debate_max_data_lag_days` | `3` | 辩论前允许的数据最大滞后自然日，超过则自动同步 |

## 验证

1. 新鲜度：mock 数据滞后 → `_ensure_fresh_data` 触发同步；同步失败 → status=stale
2. tools：mock LLM 首轮返回 tool_call、次轮返回最终 JSON → `call_llm_with_tools` 执行工具并回填
3. 多空多数据：Bull 传 macro_report → PROMPT 含宏观段；不传 → 显示"暂无"
4. 快照锁定：`calculate_all_indicators` 带 end_date 只取该日前数据
5. 回归：pytest 通过，既有 mock 测试不受影响（可选参数向后兼容）
