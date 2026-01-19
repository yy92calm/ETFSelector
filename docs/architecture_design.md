# 智能ETF选择系统架构设计文档

## 1. 文档概述

### 1.1 文档目的
本文档详细描述智能ETF选择系统的架构设计，包括系统分层结构、核心模块设计、数据库设计、技术选型等内容，为系统开发提供技术指导和依据。

### 1.2 术语定义
- **ETF**：交易所交易基金（Exchange Traded Fund）
- **K线图**：表示股票价格变动的图表，包含开盘价、收盘价、最高价、最低价
- **技术指标**：基于历史价格和成交量数据计算的用于分析市场趋势的指标
- **虚拟交易**：在不涉及真实资金的情况下模拟购买ETF的操作
- **回测**：使用历史数据验证投资策略有效性的过程
- **SQLite**：轻量级的嵌入式关系型数据库

## 2. 系统架构概述

### 2.1 架构设计原则
智能ETF选择系统采用模块化、分层架构设计，遵循以下原则：
- **高内聚低耦合**：各模块职责明确，接口清晰，减少模块间依赖
- **可扩展性**：系统设计支持功能扩展和技术升级
- **易用性**：前端界面友好，操作流程简洁
- **性能优化**：合理使用缓存和异步处理，提高系统响应速度

### 2.2 系统整体架构
系统采用经典的三层架构：
- **表示层**：Web前端界面，负责数据展示和用户交互
- **业务逻辑层**：核心业务处理，包括数据获取、指标计算、虚拟交易、策略回测等
- **数据访问层**：数据库访问和数据管理

## 3. 系统分层结构

### 3.1 表示层

#### 3.1.1 技术选型
- **前端框架**：React.js
- **UI组件库**：Ant Design
- **图表库**：ECharts
- **状态管理**：Redux Toolkit
- **路由管理**：React Router

#### 3.1.2 主要页面组件
1. **首页组件**：展示系统概览和热门ETF推荐
2. **行情数据页面组件**：ETF列表和最新行情展示
3. **基金详情页面组件**：单个ETF的详细信息、行情图、技术指标
4. **虚拟交易页面组件**：虚拟账户信息、购买/卖出操作、持仓管理
5. **策略回测页面组件**：策略编辑、回测执行、结果分析

### 3.2 业务逻辑层

#### 3.2.1 技术选型
- **后端语言**：Python 3.9+
- **Web框架**：Flask
- **任务调度**：APScheduler
- **技术指标计算**：TA-Lib

#### 3.2.2 核心服务模块

1. **数据获取服务**
   - 负责每日从金融数据API获取ETF行情数据
   - 支持手动触发数据更新
   - 数据验证和清洗

2. **技术指标计算服务**
   - 基于历史行情数据计算各类技术指标
   - 支持指标参数自定义
   - 指标结果缓存

3. **虚拟交易服务**
   - 虚拟账户管理
   - ETF购买/卖出操作处理
   - 持仓管理
   - 交易历史记录

4. **策略回测服务**
   - 自定义策略解析和执行
   - 历史数据回测
   - 回测结果分析和统计

### 3.3 数据访问层

#### 3.3.1 技术选型
- **数据库**：SQLite
- **ORM框架**：SQLAlchemy

#### 3.3.2 数据访问组件
1. **ETF数据访问组件**：处理ETF基础信息和行情数据的CRUD操作
2. **技术指标数据访问组件**：处理技术指标数据的存储和查询
3. **虚拟交易数据访问组件**：处理虚拟账户、交易记录、持仓数据的CRUD操作
4. **策略数据访问组件**：处理自定义策略的存储和查询

## 4. 核心模块设计

### 4.1 数据获取模块

#### 4.1.1 模块架构
```
数据获取模块
├── 数据API客户端
│   ├── 基础信息API
│   ├── 行情数据API
│   └── 历史数据API
├── 数据解析器
│   ├── 基础信息解析器
│   └── 行情数据解析器
├── 数据验证器
├── 数据持久化
└── 任务调度器
```

#### 4.1.2 核心流程
1. 任务调度器定时触发数据获取任务
2. 数据API客户端调用第三方金融数据API
3. 数据解析器解析API返回的原始数据
4. 数据验证器验证数据完整性和正确性
5. 数据持久化组件将验证后的数据存入SQLite数据库

### 4.2 行情可视化模块

#### 4.2.1 模块架构
```
行情可视化模块
├── 数据查询组件
├── 图表配置组件
│   ├── K线图配置
│   ├── 均线配置
│   └── 技术指标叠加配置
├── 图表渲染引擎
└── 交互控制组件
```

#### 4.2.2 核心流程
1. 用户选择ETF和时间周期
2. 数据查询组件从数据库获取历史行情数据
3. 图表配置组件根据用户选择生成图表配置
4. 图表渲染引擎使用ECharts渲染K线图和技术指标
5. 用户通过交互控制组件进行缩放、平移等操作

### 4.3 技术指标计算模块

#### 4.3.1 模块架构
```
技术指标计算模块
├── 指标计算引擎
│   ├── 趋势指标计算
│   │   ├── MA（移动平均线）
│   │   ├── EMA（指数移动平均线）
│   │   └── MACD（移动平均收敛发散）
│   ├── 震荡指标计算
│   │   ├── RSI（相对强弱指标）
│   │   ├── KDJ（随机指标）
│   │   └── WR（威廉指标）
│   ├── 成交量指标计算
│   │   ├── VOL（成交量）
│   │   ├── MAVOL（成交量均线）
│   │   └── OBV（能量潮）
│   └── 波动率指标计算
│       ├── BOLL（布林带）
│       └── ATR（平均真实波动范围）
├── 参数配置组件
└── 结果缓存组件
```

#### 4.3.2 核心流程
1. 用户选择ETF和技术指标
2. 参数配置组件获取用户自定义参数
3. 指标计算引擎从数据库获取历史行情数据
4. 使用TA-Lib库计算技术指标
5. 结果缓存组件缓存计算结果
6. 返回计算结果给前端展示

### 4.4 虚拟交易模块

#### 4.4.1 模块架构
```
虚拟交易模块
├── 账户管理组件
├── 交易执行组件
│   ├── 买入执行
│   └── 卖出执行
├── 持仓管理组件
└── 交易记录组件
```

#### 4.4.2 核心流程
1. 用户选择ETF和交易类型（买入/卖出）
2. 账户管理组件检查可用资金（买入）或持仓数量（卖出）
3. 交易执行组件处理交易逻辑，计算成交价格和数量
4. 持仓管理组件更新持仓信息
5. 交易记录组件记录交易详情
6. 账户管理组件更新账户总资产和可用资金

### 4.5 策略回测模块

#### 4.5.1 模块架构
```
策略回测模块
├── 策略编辑器
│   ├── 条件设置器
│   └── 参数配置器
├── 回测引擎
│   ├── 历史数据加载器
│   ├── 策略执行器
│   └── 交易模拟器
└── 结果分析组件
    ├── 性能指标计算器
    └── 结果可视化器
```

#### 4.5.2 核心流程
1. 用户在策略编辑器中定义回测策略
2. 回测引擎加载指定时间范围的历史数据
3. 策略执行器根据策略条件生成交易信号
4. 交易模拟器模拟执行交易
5. 结果分析组件计算回测指标（收益率、最大回撤等）
6. 结果可视化器生成资金曲线和交易记录

## 5. 数据库设计

### 5.1 数据库概述
系统使用SQLite作为嵌入式数据库，存储所有系统数据，包括ETF基础信息、行情数据、技术指标数据、虚拟交易数据和自定义策略数据。

### 5.2 数据库表结构

#### 5.2.1 ETF基础信息表（etf_basic）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| etf_code | VARCHAR(10) | PRIMARY KEY | ETF代码 |
| etf_name | VARCHAR(50) | NOT NULL | ETF名称 |
| issuer | VARCHAR(50) | | 发行机构 |
| establish_date | DATE | | 成立日期 |
| update_time | DATETIME | NOT NULL | 更新时间 |

#### 5.2.2 ETF行情数据表（etf_quotation）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键ID |
| etf_code | VARCHAR(10) | NOT NULL, FOREIGN KEY | ETF代码 |
| trade_date | DATE | NOT NULL | 交易日期 |
| open_price | FLOAT | NOT NULL | 开盘价 |
| close_price | FLOAT | NOT NULL | 收盘价 |
| high_price | FLOAT | NOT NULL | 最高价 |
| low_price | FLOAT | NOT NULL | 最低价 |
| volume | INTEGER | NOT NULL | 成交量 |
| amount | FLOAT | NOT NULL | 成交额 |
| change_rate | FLOAT | NOT NULL | 涨跌幅 |
| update_time | DATETIME | NOT NULL | 更新时间 |
| UNIQUE(etf_code, trade_date) | | | ETF代码和交易日期唯一约束 |

#### 5.2.3 技术指标数据表（technical_indicator）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键ID |
| etf_code | VARCHAR(10) | NOT NULL, FOREIGN KEY | ETF代码 |
| trade_date | DATE | NOT NULL | 交易日期 |
| indicator_name | VARCHAR(20) | NOT NULL | 指标名称 |
| params | JSON | NOT NULL | 指标参数 |
| value | JSON | NOT NULL | 指标值 |
| update_time | DATETIME | NOT NULL | 更新时间 |
| UNIQUE(etf_code, trade_date, indicator_name, params) | | | 唯一约束 |

#### 5.2.4 虚拟账户表（virtual_account）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键ID |
| total_asset | FLOAT | NOT NULL | 总资产 |
| available_fund | FLOAT | NOT NULL | 可用资金 |
| position_value | FLOAT | NOT NULL | 持仓市值 |
| update_time | DATETIME | NOT NULL | 更新时间 |

#### 5.2.5 持仓表（position）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键ID |
| etf_code | VARCHAR(10) | NOT NULL, FOREIGN KEY | ETF代码 |
| shares | INTEGER | NOT NULL | 持仓数量 |
| avg_cost | FLOAT | NOT NULL | 平均成本 |
| current_price | FLOAT | NOT NULL | 当前价格 |
| market_value | FLOAT | NOT NULL | 市值 |
| profit_loss | FLOAT | NOT NULL | 盈亏额 |
| profit_loss_rate | FLOAT | NOT NULL | 盈亏率 |
| update_time | DATETIME | NOT NULL | 更新时间 |
| UNIQUE(etf_code) | | | 每个ETF只能有一条持仓记录 |

#### 5.2.6 交易记录表（transaction_record）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键ID |
| etf_code | VARCHAR(10) | NOT NULL, FOREIGN KEY | ETF代码 |
| trade_date | DATETIME | NOT NULL | 交易日期 |
| trade_type | VARCHAR(10) | NOT NULL | 交易类型（买入/卖出） |
| price | FLOAT | NOT NULL | 成交价格 |
| shares | INTEGER | NOT NULL | 成交数量 |
| amount | FLOAT | NOT NULL | 成交金额 |
| update_time | DATETIME | NOT NULL | 更新时间 |

#### 5.2.7 自定义策略表（custom_strategy）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键ID |
| strategy_name | VARCHAR(50) | NOT NULL | 策略名称 |
| strategy_desc | TEXT | | 策略描述 |
| buy_condition | JSON | NOT NULL | 买入条件 |
| sell_condition | JSON | NOT NULL | 卖出条件 |
| params | JSON | NOT NULL | 策略参数 |
| create_time | DATETIME | NOT NULL | 创建时间 |
| update_time | DATETIME | NOT NULL | 更新时间 |

#### 5.2.8 回测结果表（backtest_result）
| 字段名 | 数据类型 | 约束 | 描述 |
|--------|----------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键ID |
| strategy_id | INTEGER | NOT NULL, FOREIGN KEY | 策略ID |
| start_date | DATE | NOT NULL | 回测开始日期 |
| end_date | DATE | NOT NULL | 回测结束日期 |
| initial_fund | FLOAT | NOT NULL | 初始资金 |
| final_asset | FLOAT | NOT NULL | 最终资产 |
| total_return | FLOAT | NOT NULL | 总收益率 |
| annual_return | FLOAT | NOT NULL | 年化收益率 |
| max_drawdown | FLOAT | NOT NULL | 最大回撤 |
| sharpe_ratio | FLOAT | NOT NULL | 夏普比率 |
| trade_count | INTEGER | NOT NULL | 交易次数 |
| win_rate | FLOAT | NOT NULL | 胜率 |
| create_time | DATETIME | NOT NULL | 创建时间 |

## 6. 接口设计

### 6.1 前端API接口

#### 6.1.1 ETF数据接口
1. **获取ETF列表**
   - URL: /api/etf/list
   - Method: GET
   - Response: ETF基础信息列表

2. **获取ETF最新行情**
   - URL: /api/etf/latest/{etf_code}
   - Method: GET
   - Response: ETF最新行情数据

3. **获取ETF历史行情**
   - URL: /api/etf/history/{etf_code}
   - Method: GET
   - Params: start_date, end_date, period
   - Response: ETF历史行情数据

#### 6.1.2 技术指标接口
1. **获取技术指标**
   - URL: /api/indicator/{etf_code}
   - Method: GET
   - Params: indicator_name, params, start_date, end_date
   - Response: 技术指标计算结果

#### 6.1.3 虚拟交易接口
1. **获取虚拟账户信息**
   - URL: /api/account/info
   - Method: GET
   - Response: 虚拟账户信息

2. **ETF购买**
   - URL: /api/trade/buy
   - Method: POST
   - Params: etf_code, amount/shares
   - Response: 交易结果

3. **ETF卖出**
   - URL: /api/trade/sell
   - Method: POST
   - Params: etf_code, shares
   - Response: 交易结果

4. **获取持仓列表**
   - URL: /api/position/list
   - Method: GET
   - Response: 持仓列表

5. **获取交易历史**
   - URL: /api/trade/history
   - Method: GET
   - Params: start_date, end_date
   - Response: 交易历史记录

#### 6.1.4 策略回测接口
1. **保存自定义策略**
   - URL: /api/strategy/save
   - Method: POST
   - Params: strategy_name, strategy_desc, buy_condition, sell_condition, params
   - Response: 保存结果

2. **获取策略列表**
   - URL: /api/strategy/list
   - Method: GET
   - Response: 自定义策略列表

3. **执行回测**
   - URL: /api/backtest/run
   - Method: POST
   - Params: strategy_id, start_date, end_date, initial_fund
   - Response: 回测结果

4. **获取回测结果**
   - URL: /api/backtest/result/{backtest_id}
   - Method: GET
   - Response: 回测详细结果

### 6.2 外部数据接口

#### 6.2.1 金融数据API
系统通过第三方金融数据API获取ETF行情数据，推荐使用以下API：
- **Tushare Pro API**：提供全面的中国股票市场数据
- **AKShare**：开源的金融数据接口库
- **JoinQuant API**：提供量化投资数据服务

## 7. 部署方案

### 7.1 部署环境
- **操作系统**：Windows 10+/macOS 10.15+/Linux
- **Python版本**：3.9+
- **浏览器支持**：Chrome 80+, Firefox 75+, Safari 13+, Edge 80+

### 7.2 部署步骤
1. **环境准备**
   - 安装Python 3.9+
   - 安装依赖包：`pip install -r requirements.txt`

2. **数据库初始化**
   - 执行数据库初始化脚本：`python init_db.py`

3. **启动服务**
   - 启动后端服务：`python app.py`
   - 启动前端开发服务器：`npm start`（开发环境）

4. **生产环境部署**
   - 构建前端项目：`npm run build`
   - 使用WSGI服务器（如Gunicorn）部署后端：`gunicorn -w 4 app:app`
   - 配置Nginx作为反向代理

### 7.3 数据更新配置
- 配置每日定时任务：使用APScheduler设置每日收盘后自动获取行情数据
- 配置文件：`config.py`中的`DATA_UPDATE_TIME`参数

## 8. 安全考虑

### 8.1 数据安全
- 金融数据API密钥加密存储
- 敏感数据传输使用HTTPS
- 定期备份SQLite数据库文件

### 8.2 访问控制
- API访问频率限制
- 输入参数验证和过滤
- 防止SQL注入攻击

### 8.3 错误处理
- 完善的异常捕获和日志记录
- 友好的错误提示
- 系统监控和告警

## 9. 性能优化

### 9.1 缓存策略
- 使用Redis缓存热点数据（可选）
- 技术指标计算结果缓存
- 页面组件缓存

### 9.2 数据库优化
- 合理创建索引
- 使用批量操作减少数据库连接次数
- 定期清理过期数据

### 9.3 计算优化
- 使用TA-Lib进行高效的技术指标计算
- 异步处理耗时任务
- 数据分页查询

## 10. 扩展性设计

### 10.1 功能扩展
- 模块化设计支持新增技术指标
- 插件式架构支持新增数据源
- 策略模板支持快速创建新策略

### 10.2 技术升级
- 前后端分离架构支持独立升级
- ORM框架支持数据库迁移
- RESTful API设计支持接口版本管理

## 11. 监控和维护

### 11.1 日志管理
- 使用Python logging模块记录系统日志
- 日志分级：DEBUG、INFO、WARNING、ERROR、CRITICAL
- 定期归档日志文件

### 11.2 系统监控
- 监控数据获取任务执行情况
- 监控系统资源使用情况
- 监控API访问频率和响应时间

### 11.3 维护计划
- 定期更新依赖包
- 定期备份数据库
- 定期检查系统运行状态

## 12. 总结

智能ETF选择系统采用分层架构设计，前端使用React.js构建用户界面，后端使用Python Flask框架提供API服务，数据存储使用SQLite嵌入式数据库。系统功能模块化，包括数据获取、行情可视化、技术指标计算、虚拟交易和策略回测等核心模块。架构设计遵循高内聚低耦合原则，支持功能扩展和技术升级，确保系统的稳定性、性能和可维护性。