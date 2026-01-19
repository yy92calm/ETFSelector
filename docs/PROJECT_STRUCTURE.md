# 项目文件结构

根据架构设计文档创建的项目文件结构说明。
采用FastAPI单体应用，前端使用静态文件由FastAPI直接serve。

## 核心应用结构 (app/)

### app/services/ - 核心业务服务
- data_service.py - 数据获取服务（每日行情数据、历史数据）
- indicator_service.py - 技术指标计算服务
- trade_service.py - 虚拟交易服务
- backtest_service.py - 策略回测服务

### app/models/ - SQLAlchemy数据库模型
- etf_basic.py - ETF基础信息模型
- etf_quotation.py - ETF行情数据模型
- technical_indicator.py - 技术指标模型
- virtual_account.py - 虚拟账户模型
- position.py - 持仓模型
- transaction_record.py - 交易记录模型
- custom_strategy.py - 自定义策略模型
- backtest_result.py - 回测结果模型

### app/routes/ - FastAPI路由
- etf_routes.py - ETF相关接口
- indicator_routes.py - 技术指标相关接口
- trade_routes.py - 虚拟交易相关接口
- strategy_routes.py - 策略相关接口
- account_routes.py - 账户相关接口

### app/schemas/ - Pydantic请求/响应模型
- etf_schemas.py
- trade_schemas.py
- strategy_schemas.py
- account_schemas.py

### app/utils/ - 工具模块
- data_parser.py - 数据解析器
- data_validator.py - 数据验证器
- api_client.py - 第三方金融数据API客户端
- indicator_calculator.py - 技术指标计算工具
- task_scheduler.py - 任务调度器

### app/db/ - 数据访问层
- database.py - SQLite数据库连接和配置
- base_dao.py - 基础数据访问对象

### app/__init__.py - FastAPI应用初始化
### app/config.py - 配置管理

---

## 前端静态文件 (static/)

由FastAPI serve的静态HTML/CSS/JS文件

- **js/** - JavaScript文件
  - main.js - 主应用入口
  - api.js - API调用模块
  - charts.js - 图表相关JS
  - trade.js - 交易相关JS
  
- **css/** - 样式文件
  - style.css - 全局样式
  - responsive.css - 响应式设计

- **images/** - 图片资源

- **index.html** - 主页面

---

## 其他目录

### tests/ - 单元测试
- test_services.py
- test_routes.py
- test_models.py

### migrations/ - 数据库迁移文件（Alembic）

---

## 配置和启动文件

- **main.py** - FastAPI应用启动文件
- **requirements.txt** - Python依赖
- **.env** - 环境变量配置
- **.env.example** - 环境变量示例

---

## 文档

- docs/architecture_design.md - 架构设计文档
- docs/requirements_doc.md - 需求文档
- docs/PROJECT_STRUCTURE.md - 项目文件结构说明
- README.md - 项目简介
