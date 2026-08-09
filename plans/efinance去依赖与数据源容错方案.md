# efinance 去依赖与数据源容错方案

## 实施状态（2026-08-09）

| 项 | 状态 | 验证 |
|---|---|---|
| 熔断 `_SourceCircuit` | ✅ | mock 连续失败≥阈值打开、熔断期后恢复、成功重置 |
| 指数退避重试 `_call_with_retry` | ✅ | mock 前2次失败第3次成功、空DataFrame视为失败、耗尽返空、熔断期跳过底层 |
| UA/headers 伪装 `_inject_efinance_headers` | ✅ | 模块加载注入 `efinance.shared.session` |
| ETF 列表换源（天天基金JS） | ✅ | 实拉 1557 只，货币ETF排除，主流510300/159915/511010齐全 |
| 列表本地缓存 | ✅ | mock 读写通过；实跑 `/api/etf/sync-list` 落盘 `app/data/etf_list_cache.json`（已gitignore） |
| 列表降级顺序 | ✅ | mock：主源→缓存→efinance 三级顺序正确 |
| 净值改走数据源 | ✅ | mock 落库 volume/amount 非零真实K线；空数据返失败 |
| 回归 | ✅ | pytest 70 passed（仅既有 test_full_debate_flow 失败）；服务启动 + `/api/etf/sync-list` 端到端通过 |

## 问题

efinance（东方财富动态接口）经常被封。现状打 efinance 的环节有三个：

| 环节 | 位置 | 请求 | 风险 |
|---|---|---|---|
| ETF 列表同步 | `data_sources.py:100` `ef.fund.get_fund_codes()` | 全市场基金列表，无主源 | 高（每次手动/定时同步都拉全量） |
| 净值更新 | `net_value_service.py:35` `ef.fund.get_quote_history` | 每只 ETF 一次请求 | 中（批量更新连续打） |
| 日K降级 | `data_sources.py:133` `ef.stock.get_quote_history` | Ashare 失败时兜底 | 低（只在主源失败时触发） |

定时任务已走 `ashare_only=True`（Ashare 新浪+腾讯双核），不依赖 efinance。

## 目标（老杨确认）

治本：ETF 列表换非 efinance 源；净值整体改走 Ashare。
治标（通用数据源层，不只 efinance）：熔断、指数退避重试+随机间隔、UA/headers 伪装、列表本地缓存兜底。

## 关键发现

- Ashare 只有日K（腾讯 `web.ifzq.gtimg.cn` + 新浪 `money.finance.sina.com.cn`），没有全市场基金列表接口 → 列表必须换新源
- 天天基金静态 JS `http://fund.eastmoney.com/js/fundcode_search.js`：全市场基金代码/简称/类型，静态文件走 CDN，比 efinance 动态接口稳定得多 → 选为列表主源
- efinance 有共享 `requests.Session`（`efinance.shared.session`，`fund_session` 复用同一对象）→ 可全局注入 headers/UA

## 模块结构

```
app/services/data_sources.py      # 改造：熔断+重试+UA注入；新增列表主源；列表缓存
app/services/net_value_service.py # 净值改走 DataSourceManager（Ashare 主）
app/services/data_service.py      # 列表同步经新源；批量更新加退避间隔
app/config.py                     # 新增数据源容错配置
app/data/etf_list_cache.json      # 列表本地缓存（运行时生成，gitignore）
```

## 实施步骤

### 1. 容错基础设施（data_sources.py 内）

- `_SourceCircuit`：每数据源持连续失败计数 + 熔断截止时间
  - `is_open()`：连续失败 ≥ `circuit_break_failures` → 熔断 `circuit_break_seconds`，期间直接失败（不再打被封 IP）；熔断期后自动尝试恢复
  - `record_success()` 重置计数 / `record_failure()` 累加
- `_call_with_retry(fetch_fn, retries)`：`base * 2^attempt + random(0,1)` 指数退避；DataFrame 空视为失败
- `_inject_efinance_headers()`：模块加载时执行一次，注入浏览器 UA + Referer（`efinance.shared.session.headers.update`）

### 2. ETF 列表换源 + 缓存

- 新增 `EastmoneyListDataSource`：GET `fundcode_search.js`，正则提取 `var r = (\[.*\]);`，筛选逻辑沿用 efinance 原判定：
  - 代码匹配 `^159\d{3}$`
  - 或代码匹配 `^5[168]\d{4}$` 且简称含 "ETF"
- `DataSourceManager.fetch_etf_list()` 顺序：天天基金静态JS → 本地缓存文件 → efinance（最后兜底）
- 缓存：成功拉取后写 `etf_list_cache.json`（含 `fetched_at`）；主源与 efinance 均失败时读缓存兜底，不硬拉

### 3. 净值服务改走 Ashare

- `net_value_service._fetch_from_efinance` → `_fetch_quotes`：改调 `DataSourceManager.fetch_etf_daily(etf_code, start_date, end_date)`（Ashare 主、efinance 兜底），返回真实 K 线（含 open/close/high/low/volume/amount/change_pct）
- `_save_net_value_to_db`：保留「已有真实行情日期跳过」逻辑；数据行改用真实 K 线字段，不再写 `volume=0` 的净值近似
- `batch_update_net_values`：每只 ETF 之间加随机退避间隔（复用 `_random_sleep`）

### 4. 批量更新退避加强

- `data_service._random_sleep`：间隔加大并随机化（列表同步等大请求场景间隔更长）
- `update_quotes_by_date_range` 现有「连续失败 10 次暂停 60s」保留，失败计数改用 `_SourceCircuit` 统一管理（连续失败触发熔断，避免反复打）

## 配置新增（app/config.py）

| 键 | 默认 | 说明 |
|---|---|---|
| `data_source_retries` | `3` | 单次拉取失败重试次数 |
| `data_source_retry_base` | `1.5` | 退避基数（秒） |
| `circuit_break_failures` | `5` | 连续失败触发熔断次数 |
| `circuit_break_seconds` | `600` | 熔断时长（秒） |
| `etf_list_cache_path` | `app/data/etf_list_cache.json` | 列表缓存路径 |

## 验证

1. 列表新源：直接调用 `EastmoneyListDataSource.fetch_etf_list()` 能拉到全市场 ETF（数量合理、含 510300/159915）
2. 缓存：主源 mock 失败 → 返回缓存文件数据，不抛错
3. 熔断：mock `fetch_etf_daily` 连续失败 ≥5 次 → 后续直接拒绝，不再调用底层
4. 重试：mock 前两次失败第三次成功 → 返回数据，间隔符合退避
5. 净值：`fetch_and_save_net_value("510300", db)` 走 Ashare 落库，`ETFQuotation` 含真实 volume/amount
6. 回归：pytest 通过
