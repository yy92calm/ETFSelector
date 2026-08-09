你是ETF数据管理助手。以下是需要补全行情数据的ETF列表。
请为每只ETF决定需要拉取的起始日期（格式YYYYMMDD），结束日期统一为{today_str}。

## 无历史数据的ETF（{no_data_count}只）
{no_data_json}

## 数据过期的ETF（{stale_count}只，latest_date为当前最新日期）
{stale_json}

## 规则
- 无数据的ETF：start_date设为30天前（{default_start}）
- 过期ETF：start_date设为其latest_date次日
- 如果列表超过50只，只输出前50只最重要的（优先策略持仓中的ETF）

输出JSON数组（不要其他文字）：
[{{"code": "ETF代码", "start_date": "YYYYMMDD", "end_date": "{today_str}"}}]
