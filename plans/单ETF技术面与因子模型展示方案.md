# 单ETF技术面与因子模型展示方案

## 现状

后端能力已齐备，缺口在工作台展示：
- **体系A**（`TechnicalIndicatorService`）：单 ETF 实时 MA/MACD/RSI/布林/量能/动量/趋势信号，接口 `/api/auto-strategy/enhanced/technical-indicators?etf_code=` 已存在，工作台行情页未接入（展开详情仅 MA5/10/20 等 6 项静态字段）
- **体系B**：5 因子模型（动量/趋势/量能/波动/资金流）+ IC 自适应权重闭环已运行，`/api/factors/ic-history`、`/api/factors/adaptive-weights` 已存在但前端零调用；缺"单 ETF 因子得分"只读接口（数据在 `FactorPerformance` 表）

## 设计

**行情页展开行（点击 ETF）**：展开时异步拉两个接口渲染扩展区（原有 6 项保留）——
- 实时技术面：RSI(14)+超买/超卖标签、MACD柱+多空强弱、布林位置%+触上/下轨、量比+量能状态、1/3/5日动量、趋势信号（多空票数+置信度）、指标日期
- 因子画像：综合分+市场排名 + 5 因子得分横条（0-100）
- `_mktDetailCache` 按 code 缓存，`loadMarket()` 刷新清空；失败/数据不足显示空态
- 新增后端接口 `GET /api/factors/etf?etf_code=`：该 ETF 最新 trade_date 的各因子得分 + 同日 `ETFDailyIndicator` 综合分/排名，无数据返回 `data=null`

**总览量化区**：`.ov-grid-quant` 3 列改 2×2，新增"🧭 因子模型"面板——
- 5 因子自适应权重横条（对比默认权重），注明"权重由近30日因子IC自适应生成"
- 近 30 日各因子 IC 走势 ECharts 迷你折线（dispose/init 模式同趋势分布图）
- `loadOverview` 并发增加 `adaptive-weights` + `ic-history` 两个请求；空数据兜底"暂无因子数据（待每日管道积累）"

后端不动两套指标体系的计算与存储链路，仅新增 1 个只读聚合接口。

## 验证

- `pytest tests/test_factor_profile.py`（最新日聚合/空态/缺指标行）+ 全量 pytest 回归
- `node --check static/js/workbench.js`
- 浏览器实测：总览因子面板（权重条+IC 曲线）、行情展开行（技术面+因子画像、A股红多绿空配色、二次展开走缓存）
