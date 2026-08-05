# Loop因子发现引擎落地方案

参考来源：中金研究《大模型系列（7）：基于Loop Engineering的自动化因子发现引擎》

## 现状对照

| 中金系统概念 | 本项目现状 | 差距 |
|---|---|---|
| 定时调度 + 检查点断点续跑 | APScheduler 串行管道（_job_daily_pipeline） | 无检查点，中断即从头重跑 |
| 失败模式库自动规避 | 经验库（Experience）已存 failure 类型 | 失败经验不结构化、不自动规避 |
| FSA 频繁结构规避（>15% 冻结） | 无 | 无结构去重/冻结机制 |
| 11项联合过滤（IC/超额/夏普/Calmar/相关性） | 轮动评分 5 权重固定 | 无 IC 跟踪、无因子有效性验证 |
| 多信号源复合 | WEIGHTS 固定权重打分 | 权重不可自适应调整 |

## 关键约束

- 不引入 Alembic，字段迁移走 `init_db()` 的 ALTER TABLE 兼容逻辑
- 新增表必须在 `init_db()` 中导入
- 新 service 遵循单例模式
- 定时任务保持单 job 串行，检查点设计不能引入并行
- 所有 DB 操作通过 Session 传入

## 落地范围（三步）

### 方向1：每日管道检查点/断点续跑

**模块**：`app/services/pipeline_checkpoint_service.py`

**设计**：
- 新建 `PipelineCheckpoint` 模型（表 `pipeline_checkpoint`）：pipeline 名、执行日期、已完成阶段列表（JSON）、更新时间
- 管道每个阶段完成后写入检查点；下次执行时读取已完成的阶段并跳过
- `_job_daily_pipeline` 拆分为可标记阶段的版本：每阶段 `mark_stage_done(db, "daily_pipeline", date, stage)` / `get_done_stages(db, "daily_pipeline", date)`
- 阶段失败不标记完成 → 中断后从失败阶段续跑

**新增表**：
```
pipeline_checkpoint
  id, pipeline_name, run_date, done_stages(JSON), updated_at
```

### 方向2：失败模式库（经验库升级）

**模块**：`app/services/failure_mode_service.py`

**设计**：
- 在 `Experience` 增加 `failure_signature`（结构签名，如 `买入{code}后X日亏损`）、`occurrence_count`（出现次数）、`last_triggered_date`
- 新增 `FailureModeService`：
  - `record_failure(签名, 场景, 原因)`：失败经验入失败模式库，`experience_type=failure`，幂等（相同 signature 累加 occurrence_count）
  - `get_active_failure_modes(场景)`：返回活跃失败模式（用于LLM提示词注入，规避已失败操作）
  - `get_banned_codes(阈值=3)`：连续失败≥阈值 的 ETF 代码进入规避名单
  - FSA 式规避：同一 failure_signature 出现频率超阈值时，生成阶段禁止复用该操作

**接入点**：
- `rotation_service.evaluate_rotation`：检查候选 add 代码是否在规避名单
- `auto_strategy_executor`：AI 建议的配置若包含规避代码则标记警告/降级
- `review_service`：失败案例生成时走 `FailureModeService.record_failure` 而非裸建 Experience

### 方向3：增强轮动打分（IC跟踪 + 自适应权重）

**模块**：`app/services/factor_performance_service.py`

**设计**：
- 新建 `FactorPerformance` 模型（表 `factor_performance`）：日期、ETF代码、因子值（momentum/trend/volume/volatility/capital_flow）、未来5日收益率
- 扫描存指标时同时写入因子表现记录；下个周期回填未来收益
- 计算 IC（Spearman 秩相关）：每个因子值与未来5日收益的相关性，按日期汇总
- 动态权重：基于最近 N 日各因子 IC 绝对值归一化得到新权重，替换固定 `WEIGHTS`
- 保留固定权重作为默认值，无 IC 数据时退回固定权重

**接入点**：
- `market_scanner_service.scan_all`：存指标时写入 factor_performance
- `rotation_service.evaluate_rotation`：用 IC 自适应权重替代固定 WEIGHTS 计算 composite_score（或提供开关）

## 实施步骤

1. 模型层：`pipeline_checkpoint.py`、`failure_mode.py`（或复用 Experience）、`factor_performance.py`，更新 `app/models/__init__.py`
2. DB迁移：`init_db()` 导入新表 + Experience 新字段 ALTER
3. Service 层：三个新 service，单例模式
4. 接入点：scheduler 检查点、rotation/executor/review 失败规避、scanner 因子表现记录
5. 验证：重启服务、单元测试、curl 验证 API

## 风险与说明

- IC 计算需要足够历史（建议 ≥ 20 个交易日），初期数据不足时保持固定权重
- 失败规避不能过度：banned 阈值设 3 次，避免单次偶发亏损即永久禁入
- 检查点表按 (pipeline, run_date) 唯一，跨日期自动重置
