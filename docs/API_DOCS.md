# ETF Selector API 文档

## 概述

ETF Selector 是一个智能ETF选择系统，提供ETF行情分析、虚拟交易和策略回测功能。

## API 端点

### ETF基础信息

#### `GET /api/etf/list`
获取所有ETF列表

**响应示例:**
```json
{
  "code": 200,
  "message": "获取ETF列表成功",
  "data": {
    "etfs": [
      {
        "etf_code": "510050",
        "etf_name": "上证50ETF"
      }
    ]
  }
}
```

#### `GET /api/etf/latest/{etf_code}`
获取ETF最新行情

#### `GET /api/etf/history/{etf_code}`
获取ETF历史行情

#### `GET /api/etf/detail/{etf_code}`
获取ETF详细信息（包含基础信息和最新行情）

### ETF行情数据

#### `POST /api/etf/fetch-quotes`
从Qtrade API获取并保存ETF行情数据

#### `POST /api/etf/market/shanghai`
获取上证市场所有主流ETF行情数据

#### `POST /api/etf/market/shenzhen`
获取深证市场所有主流ETF行情数据

#### `POST /api/etf/market/all`
获取上深全市场所有主流ETF行情数据

#### `POST /api/etf/market/{market_type}`
获取指定市场所有ETF行情数据

支持的市场类型: `shanghai`, `shenzhen`, `all`

#### `GET /api/etf/market/{market_type}/etfs`
获取指定市场的ETF代码列表

支持的市场类型: `shanghai`, `shenzhen`, `all`, `all_etfs`

#### `POST /api/etf/market/{market_type}/etfs`
向指定市场添加ETF代码

#### `DELETE /api/etf/market/{market_type}/etfs`
从指定市场移除ETF代码

#### `GET /api/etf/market/shanghai/quotes`
获取上证市场所有主流ETF的最新行情

#### `GET /api/etf/market/shenzhen/quotes`
获取深证市场所有主流ETF的最新行情

#### `GET /api/etf/market/all/quotes`
获取上深全市场所有主流ETF的最新行情

### **新增功能**: 全市场ETF列表

#### `GET /api/etf/market/all_etfs`
获取全市场ETF列表（从API获取最新数据）

**响应示例:**
```json
{
  "code": 200,
  "message": "获取全市场ETF列表成功",
  "data": {
    "etf_codes": ["510050", "510300", "510500", ...],
    "count": 150
  }
}
```

### 调度器管理

#### `POST /api/etf/scheduler/{action}`
管理定时任务调度器

支持的操作: `start`, `stop`, `status`

## 定时任务

系统包含以下定时任务:

- **交易时间行情更新**: 在交易日的交易时间内，每30分钟更新一次ETF行情数据
- **每日基础数据更新**: 每天早上8:30更新一次基础数据
- **每日ETF列表更新**: 每天早上9:00更新一次全市场ETF列表

## 错误处理

所有API端点都返回统一的错误格式:

```json
{
  "code": 500,
  "message": "错误信息",
  "data": null
}
```

## 状态码

- `200`: 成功
- `400`: 请求参数错误
- `404`: 资源不存在
- `500`: 服务器内部错误
