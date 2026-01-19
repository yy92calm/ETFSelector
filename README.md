# 智能ETF选择系统

## 项目简介

智能ETF选择系统是一个功能完整的ETF投资分析和模拟交易平台，帮助投资者快速准确地选择适合的ETF产品，并支持虚拟交易和策略回测。

## 核心功能

- **行情数据获取**：每日自动更新ETF行情数据，支持历史数据查询（至少5年）
- **全市场行情**：支持获取上证、深证市场的所有主流ETF行情数据
- **行情可视化**：K线图、均线、成交量等多种技术图表展示，支持多基金对比分析
- **技术指标计算**：支持趋势、震荡、成交量、波动率等多种技术指标，指标参数可自定义
- **虚拟交易**：虚拟账户管理、模拟买卖操作、实时持仓管理和交易历史记录
- **策略回测**：支持自定义投资策略，使用历史数据验证策略有效性

## 系统架构

采用FastAPI单体应用架构：

- **后端框架**：FastAPI + Uvicorn + SQLAlchemy
- **数据库**：SQLite
- **前端**：静态HTML/CSS/JS (由FastAPI serve)
- **API集成**：Qtrade行情数据接口

## 快速开始

```bash
# 1. 激活虚拟环境
source .venv/bin/activate

# 2. 启动应用
python main.py

# 3. 访问应用
# - 应用: http://localhost:8000
# - API文档: http://localhost:8000/docs
```

## 新功能 (v0.1.1)

- ✅ 获取上证市场所有主流ETF行情
- ✅ 获取深证市场所有主流ETF行情
- ✅ 获取上深全市场ETF行情

详见 [全市场行情功能文档](docs/MARKET_QUOTES_FEATURE.md)

## 项目文档

- [快速开始指南](docs/QUICKSTART.md)
- [全市场行情功能](docs/MARKET_QUOTES_FEATURE.md)
- [实现总结](docs/IMPLEMENTATION_SUMMARY.md)
- [项目结构](docs/PROJECT_STRUCTURE.md)
- [架构设计](docs/architecture_design.md)
- [需求文档](docs/requirements_doc.md)

