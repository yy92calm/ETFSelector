"""
定时任务调度器
每个工作日执行串行管道：

  净值更新 → (间隔) 组合执行/舆情采集 → (间隔) AI分析+风险检查+策略调整

关键原则：每一步只在前一步完成后才执行，通过单个 job 内的串行调用实现。
"""

import logging
from datetime import date
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import get_settings
from app.tasks.task_logger import log_task_execution

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler | None = None

# 提示词文件目录
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str, **kwargs) -> str:
    """从 prompts/ 目录加载提示词模板，支持变量替换"""
    filepath = _PROMPTS_DIR / filename
    content = filepath.read_text(encoding="utf-8")
    if kwargs:
        content = content.format(**kwargs)
    return content


# 自主决策指令：驱动LLM主动管理而非被动维持
AUTONOMOUS_INSTRUCTION = _load_prompt("autonomous_instruction.md")


def _record_failure_experience(db, stage: str, error_msg: str, run_date):
    """将定时任务阶段失败记录为经验，供后续 LLM 决策参考"""
    from app.models.experience import Experience
    from datetime import timedelta

    # 提取错误类型作为签名
    error_type = error_msg.split(':')[0] if ':' in error_msg else error_msg[:50]
    failure_sig = f"pipeline_{stage}_{error_type}"

    # 检查是否已有相同签名的经验（合并计数）
    existing = db.query(Experience).filter(
        Experience.failure_signature == failure_sig,
        Experience.is_active == True
    ).first()

    if existing:
        existing.occurrence_count += 1
        existing.last_triggered_date = run_date
        existing.description = f"{existing.description}\n\n[{run_date}] 再次失败: {error_msg[:200]}"
        db.commit()
        logger.info(f"经验合并: {failure_sig} 第{existing.occurrence_count}次")
    else:
        # 新建经验
        exp = Experience(
            strategy_id=None,  # 系统级经验
            experience_type="failure",
            scenario_tags=["定时任务", stage, "管道失败"],
            title=f"管道阶段 {stage} 执行失败",
            description=f"执行日期: {run_date}\n错误信息: {error_msg}",
            result="negative",
            key_insight=f"{stage} 阶段不稳定，需关注 {error_type}",
            effectiveness_score=0.0,
            application_count=0,
            success_count=0,
            failure_count=1,
            source_type="scheduler",
            generated_date=run_date,
            is_validated=False,
            is_active=True,
            expires_date=run_date + timedelta(days=90),
            weight=1.0,
            review_status="pending",
            failure_signature=failure_sig,
            occurrence_count=1,
            last_triggered_date=run_date,
        )
        db.add(exp)
        db.commit()
        logger.info(f"失败经验已记录: {failure_sig}")


@log_task_execution("daily_pipeline")
def _job_daily_pipeline():
    """
    每日自驱动串行管道（LLM驱动 + fallback）

    阶段1: 净值更新 + 组合再平衡（确定性操作）
    阶段2: 舆情采集 + 政策评估 + 资金流向（数据采集层）
    阶段3: 全市场量化扫描 + 轮动复盘（纯量化）
    阶段4: LLM自主决策（感知→推理→行动），失败时降级为原有管道

    支持断点续跑：每阶段完成后写入检查点，中断后从失败阶段续跑。
    """
    from app.db.database import SessionLocal
    from app.services.pipeline_checkpoint_service import get_pipeline_checkpoint_service
    from datetime import date

    _cp_svc = get_pipeline_checkpoint_service()
    _run_date = date.today()
    _stages = ["net_value", "quotes", "rebalance", "sentiment", "policy_flow",
               "market_scan", "market_regime", "sw_sector", "fundamental",
               "rotation_review", "autonomous"]

    db = SessionLocal()
    try:
        done = _cp_svc.get_done_stages("daily_pipeline", _run_date, db)
    finally:
        db.close()

    def _run_stage(stage: str, fn):
        """执行阶段，成功标记检查点；失败记录并跳过（不中断整个管道）"""
        from app.models.task_log import TaskExecutionLog
        from datetime import datetime
        
        if stage in done:
            logger.info(f"[Checkpoint] 阶段 {stage} 已完成，跳过")
            return
        
        db_local = SessionLocal()
        started_at = datetime.utcnow()
        
        # 创建执行日志
        log_entry = TaskExecutionLog(
            task_name=f"daily_pipeline.{stage}",
            status="running",
            started_at=started_at
        )
        db_local.add(log_entry)
        db_local.commit()
        
        try:
            fn()
            _cp_svc.mark_stage_done("daily_pipeline", _run_date, stage, db_local)
            
            # 更新为成功
            log_entry.status = "success"
            log_entry.finished_at = datetime.utcnow()
            log_entry.duration_seconds = (log_entry.finished_at - started_at).total_seconds()
            db_local.commit()
        except Exception as e:
            logger.error(f"[Checkpoint] 阶段 {stage} 失败: {e}")
            
            # 更新为失败
            log_entry.status = "failed"
            log_entry.finished_at = datetime.utcnow()
            log_entry.duration_seconds = (log_entry.finished_at - started_at).total_seconds()
            log_entry.error_message = str(e)[:1000]
            db_local.commit()
            
            try:
                _cp_svc.mark_failed("daily_pipeline", _run_date, stage, str(e), db_local)
                # 写入失败经验
                _record_failure_experience(db_local, stage, str(e), _run_date)
            except Exception as log_err:
                logger.error(f"记录失败经验时出错: {log_err}")
            return
        finally:
            db_local.close()

    # ============================== 阶段1 ==============================
    _run_stage("net_value", _step_update_net_values)
    _run_stage("quotes", _step_update_quotes)
    _run_stage("rebalance", _step_run_strategies)

    # ============================== 阶段2 ==============================
    _run_stage("sentiment", _step_collect_sentiments)
    _run_stage("policy_flow", lambda: (_step_policy_impact(), _step_capital_flow()))

    # ============================== 阶段3 ==============================
    _run_stage("market_scan", _step_market_scan)
    _run_stage("market_regime", _step_market_regime)
    _run_stage("sw_sector", _step_sw_sector)
    _run_stage("fundamental", _step_fundamental)
    _run_stage("rotation_review", _step_rotation_review)

    # ============================== 阶段4 ==============================
    _run_stage("autonomous", _step_autonomous_decision)

    # 管道完成：新分析日志已产生，失效规则缓存使次日规则刷新
    try:
        from app.services.rule_engine import get_rule_engine
        get_rule_engine().invalidate_cache()
    except Exception as e:
        logger.warning(f"规则缓存失效失败: {e}")

    db_local = SessionLocal()
    try:
        _cp_svc.mark_completed("daily_pipeline", _run_date, db_local)
    finally:
        db_local.close()


def _step_update_net_values():
    """STEP 1: 更新ETF净值数据"""
    from app.db.database import SessionLocal
    from app.services.net_value_service import get_net_value_service

    logger.info("===== [阶段1] 更新ETF净值数据 =====")
    db = SessionLocal()
    try:
        svc = get_net_value_service()
        result = svc.batch_update_net_values(db)
        logger.info(f"净值更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}")
        if result.get('total', 0) > 6:
            logger.info(f"还有 {result['total'] - 6} 只ETF待更新，将在下一个周期继续")
    except Exception as e:
        logger.error(f"净值更新异常: {e}")
    finally:
        db.close()


def _step_update_quotes():
    """STEP 1.5: 更新当日行情数据（日K线）"""
    from app.db.database import SessionLocal
    from app.services.data_service import get_data_service

    logger.info("===== [阶段1] 更新当日行情数据 =====")
    db = SessionLocal()
    try:
        svc = get_data_service()
        result = svc.update_today_quotes(db)
        logger.info(f"行情更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}")
    except Exception as e:
        logger.error(f"行情更新异常: {e}")
    finally:
        db.close()


def _step_run_strategies():
    """STEP 2: 所有活跃策略的再平衡检查（基于当前配置比例执行交易）"""
    from app.db.database import SessionLocal
    from app.services.portfolio_service import get_portfolio_service

    logger.info("===== [阶段1] 组合再平衡 =====")
    db = SessionLocal()
    try:
        svc = get_portfolio_service()
        svc.run_all_active_strategies(db)
        logger.info("组合再平衡完成")
    except Exception as e:
        logger.error(f"组合再平衡异常: {e}")
    finally:
        db.close()


def _step_collect_sentiments():
    """STEP 3: 舆情采集"""
    from app.db.database import SessionLocal
    from app.services.sentiment_service import SentimentService

    logger.info("===== [阶段2] 舆情采集 =====")
    db = SessionLocal()
    try:
        svc = SentimentService()
        result = svc.collect_daily_sentiment(date.today(), db)
        logger.info(f"舆情采集完成: {result.get('news_count', 0)}条")
    except Exception as e:
        logger.error(f"舆情采集异常: {e}")
    finally:
        db.close()


@log_task_execution("sentiment_collect")
def _job_collect_sentiments():
    """交易时段舆情采集独立任务（10/12/14点定时执行）

    采集后判定情绪是否极端：负面极端时条件触发一次轮动复核（门槛加严），
    使极端情绪不必等到 20:00 管道才被处理。
    """
    _step_collect_sentiments()
    _maybe_trigger_sentiment_review()


def _maybe_trigger_sentiment_review():
    """情绪极端 → 条件触发轮动复核（semisettings.sentiment_condition_gap_threshold 门槛）"""
    from datetime import datetime
    from sqlalchemy import func
    from app.config import get_settings
    from app.db.database import SessionLocal
    from app.models.etf import ETFDailyIndicator
    from app.models.strategy import Strategy
    from app.models.task_log import TaskExecutionLog
    from app.services.rotation_service import get_rotation_service
    from app.services.sentiment_service import get_sentiment_service

    settings = get_settings()
    if not settings.sentiment_review_enabled:
        return

    logger.info("===== [条件触发] 情绪极端复核 =====")
    started = datetime.utcnow()
    db = SessionLocal()
    try:
        strategies = db.query(Strategy).filter(
            Strategy.strategy_source == "auto_generated",
            Strategy.auto_strategy_status == "running",
        ).all()
        scan_date = db.query(func.max(ETFDailyIndicator.trade_date)).scalar()
        if not strategies or not scan_date:
            return

        sentiment_svc = get_sentiment_service()
        rotation_svc = get_rotation_service()
        triggered = []

        for strategy in strategies:
            guard = sentiment_svc.evaluate_extreme(db, strategy.id)
            if not guard.get("extreme"):
                logger.info(f"策略{strategy.id} 情绪未达极端（均分{guard.get('market_score')}），跳过条件复核")
                continue
            logger.info(f"策略{strategy.id} 情绪极端触发复核: {guard['reasons']}")
            plan = rotation_svc.evaluate_rotation(
                strategy.id, scan_date, db,
                gap_threshold=settings.sentiment_condition_gap_threshold,
            )
            result = None
            if plan.get("action") == "rotate":
                result = rotation_svc.execute_rotation(strategy.id, plan, db)
                logger.info(f"策略{strategy.id} 条件复核执行: {result.get('status')}")
            else:
                logger.info(f"策略{strategy.id} 条件复核维持: {plan.get('reason', '')}")
            _record_condition_review(strategy, plan, guard, result, db)
            triggered.append({
                "strategy_id": strategy.id,
                "action": plan.get("action"),
                "reasons": guard.get("reasons"),
            })

        db.add(TaskExecutionLog(
            task_name="sentiment_condition_review",
            status="success",
            started_at=started,
            finished_at=datetime.utcnow(),
            duration_seconds=(datetime.utcnow() - started).total_seconds(),
            result_summary={"checked": len(strategies), "triggered": len(triggered), "details": triggered},
        ))
        db.commit()
    except Exception as e:
        logger.error(f"情绪条件复核异常（不影响舆情采集）: {e}")
        db.rollback()
    finally:
        db.close()


def _record_condition_review(strategy, plan, guard, result, db):
    """条件触发决策留痕：写入/更新当日 analyzed 记录（20:00 管道当日会再覆盖更新）"""
    from datetime import date

    from app.models.auto_strategy_log import AutoStrategyLog
    from app.services.strategy_evidence_service import get_strategy_evidence_service

    action_map = {"rotate": "rebalance", "hold": "hold", "skip": "hold"}
    cited = ["market", "sentiment"]
    if (plan.get("sector_meta") or {}).get("mode") != "off":
        cited.append("research")
    if plan.get("rule_signal"):
        cited.append("rules")

    analysis = {
        "suggested_action": action_map.get(plan.get("action"), "hold"),
        "suggested_allocation": (result or {}).get("new_allocation") or plan.get("suggested_allocation"),
        "action_reason": "情绪条件触发：" + "；".join(guard.get("reasons") or []) +
                         "｜" + str(plan.get("reason") or plan.get("summary") or "条件复核完成"),
        "key_signals_summary": guard.get("reasons") or [],
        "source": "sentiment_condition",
        "trigger": "condition",
        "gap_threshold": plan.get("gap_threshold"),
    }
    try:
        analysis["evidence"] = {
            "sources_cited": cited,
            "snapshot": get_strategy_evidence_service().get_snapshot(strategy.id, db),
        }
    except Exception as e:
        logger.warning(f"[条件复核] 策略{strategy.id}依据快照生成失败: {e}")

    today = date.today()
    existing = db.query(AutoStrategyLog).filter_by(
        strategy_id=strategy.id, log_date=today, action_type="analyzed"
    ).first()
    if existing:
        existing.analysis_result = analysis
        existing.status = "success"
    else:
        db.add(AutoStrategyLog(
            strategy_id=strategy.id, log_date=today,
            status="success", action_type="analyzed", analysis_result=analysis,
        ))
    db.commit()


def _step_policy_impact():
    """STEP 4: 政策事件冲击评估"""
    from app.db.database import SessionLocal
    from app.services.policy_impact_service import get_policy_impact_service

    logger.info("===== [阶段2] 政策影响评估 =====")
    db = SessionLocal()
    try:
        svc = get_policy_impact_service()
        result = svc.assess_policy_impact(db)
        if "error" not in result:
            events = result.get("policy_events", [])
            logger.info(f"政策影响评估完成: {len(events)}个事件")
        else:
            logger.warning(f"政策影响评估跳过: {result.get('error')}")
    except Exception as e:
        logger.error(f"政策影响评估异常: {e}")
    finally:
        db.close()


def _step_capital_flow():
    """STEP 5: 资金流向分析"""
    from app.db.database import SessionLocal
    from app.services.capital_flow_service import get_capital_flow_service
    from app.models.strategy import Strategy

    logger.info("===== [阶段2] 资金流向分析 =====")
    db = SessionLocal()
    try:
        strategy = db.query(Strategy).filter(
            Strategy.strategy_source == "auto_generated",
            Strategy.auto_strategy_status == "running",
        ).first()
        if not strategy or not strategy.allocation_config:
            logger.info("无活跃策略，跳过资金流向分析")
            return

        etf_codes = list(strategy.allocation_config.keys())
        svc = get_capital_flow_service()
        result = svc.analyze_capital_flow(etf_codes, db)
        if "error" not in result:
            logger.info(f"资金流向分析完成: {result.get('summary', '')[:80]}")
        else:
            logger.warning(f"资金流向分析跳过: {result.get('error')}")
    except Exception as e:
        logger.error(f"资金流向分析异常: {e}")
    finally:
        db.close()


def _step_market_scan():
    """STEP 6: 全市场量化指标扫描（纯计算）+ 因子表现回填"""
    from app.db.database import SessionLocal
    from app.services.market_scanner_service import get_market_scanner_service
    from app.services.factor_performance_service import get_factor_performance_service

    logger.info("===== [阶段3] 全市场量化扫描 =====")
    db = SessionLocal()
    try:
        svc = get_market_scanner_service()
        result = svc.scan_all(date.today(), db)
        logger.info(f"量化扫描完成: {result}")
        # 回填因子未来收益，供后续IC计算
        try:
            fp_svc = get_factor_performance_service()
            # 首次上线：从已有指标重建因子记录
            from app.models.factor_performance import FactorPerformance
            has_factor = db.query(FactorPerformance).first()
            if not has_factor:
                rebuilt = fp_svc.backfill_from_indicators(db)
                logger.info(f"因子记录首次重建: {rebuilt}条")
            filled = fp_svc.backfill_forward_returns(db)
            if filled:
                logger.info(f"因子收益回填完成: {filled}条")
        except Exception as e:
            logger.error(f"因子收益回填异常: {e}")
    except Exception as e:
        logger.error(f"量化扫描异常: {e}")
    finally:
        db.close()


def _step_market_regime():
    """STEP 6.5: 市场状态刻画 — 风险偏好/风格轮动/基金收益率分化度 → 中期仓位信号"""
    from app.db.database import SessionLocal
    from app.services.market_regime_service import get_market_regime_service

    logger.info("===== [阶段3.5] 市场状态刻画 =====")
    db = SessionLocal()
    try:
        snap = get_market_regime_service().compute(date.today(), db)
        logger.info(f"市场状态刻画完成: {snap}")
    except Exception as e:
        logger.error(f"市场状态刻画异常: {e}")
        raise
    finally:
        db.close()


def _step_sw_sector():
    """STEP 6.5: 申万一级行业板块同步与评分（板块轮动模型，独立数据源独立降级）"""
    from app.db.database import SessionLocal
    from app.services.sw_industry_service import get_sw_industry_service

    logger.info("===== [阶段3.5] 申万板块轮动 =====")
    db = SessionLocal()
    try:
        summary = get_sw_industry_service().sync(db)
        logger.info(
            f"申万板块同步完成: 截止{summary['cutoff']} {summary['industries']}个行业 "
            f"超配{summary['overweight']}/低配{summary['underweight']}"
        )
    except Exception as e:
        logger.error(f"申万板块阶段异常（降级为最近已落库数据）: {e}")
        raise
    finally:
        db.close()


def _step_fundamental():
    """STEP 6.8: 基本面同步与性价比模型 — 池内估值增量 + 行业盈利-估值性价比评分"""
    from app.db.database import SessionLocal
    from app.services.fundamental_data_service import get_fundamental_data_service
    from app.services.value_model_service import get_value_model_service

    logger.info("===== [阶段3.8] 基本面与性价比模型 =====")
    db = SessionLocal()
    try:
        result = get_fundamental_data_service().sync_fundamentals(db, days_back=5)
        logger.info(f"基本面同步完成: {result}")
        n = get_value_model_service().compute_industry_scores(db, date.today())
        logger.info(f"行业性价比评分完成: {n} 个行业")
    except Exception as e:
        logger.error(f"基本面阶段异常: {e}")
        raise
    finally:
        db.close()


def _step_rotation_review():
    """STEP 7: 轮动复盘 — 评估所有自动策略是否需要换仓（持仓≤5，有进必出）"""
    from app.db.database import SessionLocal
    from app.services.rotation_service import get_rotation_service
    from app.models.strategy import Strategy
    from app.models.etf import ETFDailyIndicator
    from sqlalchemy import func

    logger.info("===== [阶段3] 轮动复盘 =====")
    db = SessionLocal()
    try:
        # 使用指标表中最新的交易日（即行情数据实际到达的日期），而非date.today()
        latest_indicator_date = db.query(func.max(ETFDailyIndicator.trade_date)).scalar()
        if not latest_indicator_date:
            logger.info("无量化指标数据，跳过轮动")
            return

        strategies = db.query(Strategy).filter(
            Strategy.strategy_source == "auto_generated",
            Strategy.auto_strategy_status == "running",
        ).all()

        if not strategies:
            logger.info("无活跃自动策略，跳过轮动")
            return

        logger.info(f"轮动基准日: {latest_indicator_date}")
        svc = get_rotation_service()
        for strategy in strategies:
            plan = svc.evaluate_rotation(strategy.id, latest_indicator_date, db)
            action = plan.get("action")

            if action == "rotate":
                logger.info(f"策略{strategy.id} 触发轮动: {len(plan['rotations'])}只替换")
                for rot in plan["rotations"]:
                    logger.info(
                        f"  换出 {rot['remove']}({rot['remove_name']}) 得分{rot['remove_score']} "
                        f"→ 换入 {rot['add']}({rot['add_name']}) 得分{rot['add_score']} "
                        f"差距{rot['score_gap']}"
                    )
                result = svc.execute_rotation(strategy.id, plan, db)
                logger.info(f"策略{strategy.id} 轮动执行: {result.get('status')}")
            elif action == "hold":
                logger.info(f"策略{strategy.id} 维持持仓: {plan.get('reason', '')}")
            else:
                logger.info(f"策略{strategy.id} 跳过: {plan.get('reason', '')}")
    except Exception as e:
        logger.error(f"轮动复盘异常: {e}")
    finally:
        db.close()


def _step_autonomous_decision():
    """
    STEP 8: LLM自主决策（感知→推理→行动）

    优先使用 AgentLoop 自主决策；
    LLM不可用时降级为原有硬编码管道。
    """
    from app.db.database import SessionLocal
    from app.config import get_settings

    _settings = get_settings()
    db = SessionLocal()
    try:
        if _settings.llm_api_key and _settings.llm_api_key.strip():
            logger.info("===== [阶段2] LLM自主决策 =====")
            from app.agent_core.loop import AgentLoop
            agent = AgentLoop()
            result = agent.run_autonomous(
                trigger="daily",
                instruction=AUTONOMOUS_INSTRUCTION,
                db=db,
            )
            logger.info(f"LLM自主决策完成: {result.content[:200] if result.content else 'no content'}")
            if result.tool_calls_made:
                logger.info(f"工具调用: {[t['tool'] for t in result.tool_calls_made]}")
            if not result.error:
                _record_autonomous_analysis(result, db)
        else:
            logger.info("===== [阶段2] LLM未配置，降级为原有管道 =====")
            _step_auto_pipeline_fallback(db)
    except Exception as e:
        logger.error(f"LLM自主决策异常，降级为原有管道: {e}")
        _step_auto_pipeline_fallback(db)
    finally:
        db.close()


def _record_autonomous_analysis(autonomous_result, db):
    """AgentLoop自主决策后回写 analyzed 日志。

    分析与规则视图的数据源是 auto_strategy_log 的 analyzed 记录（旧管道专属），
    阶段4切换到 AgentLoop 后无人写入，导致视图恒空。此函数按运行中的自动策略
    补写当日 analyzed 日志：辩论工具结果可用时取结构化字段，否则以决策摘要降级。
    """
    from app.models.auto_strategy_log import AutoStrategyLog
    from app.models.strategy import Strategy

    strategies = db.query(Strategy).filter(
        Strategy.strategy_source == "auto_generated",
        Strategy.auto_strategy_status == "running",
    ).all()
    if not strategies:
        return

    # 辩论结果按策略归集（自主决策期间 LLM 可能调用 run_multi_agent_analysis）
    debate_by_sid: dict = {}
    for tc in (autonomous_result.tool_calls_made or []):
        if tc.get("tool") == "run_multi_agent_analysis" and tc.get("result") and "error" not in tc["result"]:
            sid = (tc.get("arguments") or {}).get("strategy_id")
            if sid is not None:
                debate_by_sid[int(sid)] = tc["result"]

    summary = (autonomous_result.content or "")[:500]
    today = date.today()

    # 依据留痕：工具调用映射「本次引用了哪些依据」
    from app.services.strategy_evidence_service import (
        classify_tool_sources, get_strategy_evidence_service,
    )
    cited_tools = classify_tool_sources([
        tc.get("tool") for tc in (autonomous_result.tool_calls_made or []) if tc.get("tool")
    ])
    sources_cited = list(cited_tools.keys())

    for strategy in strategies:
        debate = debate_by_sid.get(strategy.id) or {}
        analysis = {
            "market_regime": debate.get("market_regime"),
            "regime_confidence": debate.get("confidence_level"),
            "suggested_action": debate.get("suggested_action") or "hold",
            "suggested_allocation": debate.get("suggested_allocation"),
            "action_reason": debate.get("action_reason") or summary,
            "risk_alert": debate.get("risk_alert"),
            "agreement_level": debate.get("agreement_level"),
            "key_signals_summary": debate.get("key_signals_summary") or [],
            "source": "agentloop_autonomous",
        }
        # 决策时点依据快照（失败不影响日志写入）
        try:
            analysis["evidence"] = {
                "sources_cited": sources_cited,
                "cited_tools": cited_tools,
                "snapshot": get_strategy_evidence_service().get_snapshot(strategy.id, db),
            }
        except Exception as e:
            logger.warning(f"[自主决策] 策略{strategy.id}依据快照生成失败: {e}")
        existing = db.query(AutoStrategyLog).filter_by(
            strategy_id=strategy.id, log_date=today, action_type="analyzed"
        ).first()
        if existing:
            # 当日重跑（补跑/单阶段触发）时更新而非重复插入
            existing.analysis_result = analysis
            existing.status = "success"
        else:
            db.add(AutoStrategyLog(
                strategy_id=strategy.id, log_date=today,
                status="success", action_type="analyzed",
                analysis_result=analysis,
            ))
    db.commit()
    logger.info(f"[自主决策] analyzed 日志已回写: {len(strategies)}个策略")


def _step_auto_pipeline_fallback(db):
    """原有硬编码管道（fallback）"""
    from app.services.auto_strategy_executor import AutoStrategyExecutor

    try:
        svc = AutoStrategyExecutor()
        result = svc.run_all_auto_strategies(date.today(), db)
        logger.info(f"[fallback] AI自驱动管道完成: {result}")
    except Exception as e:
        logger.error(f"[fallback] AI自驱动管道异常: {e}")


@log_task_execution("weekly_review")
def _job_weekly_review():
    """每周复盘 - 每周三、周日21:00（复盘后触发提示词进化）"""
    from app.db.database import SessionLocal
    from app.services.review_service import ReviewService
    from app.models.strategy import Strategy

    logger.info("===== 每周复盘 =====")
    db = SessionLocal()
    try:
        svc = ReviewService()
        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == 'auto_generated',
            Strategy.auto_strategy_status == 'running'
        ).all()

        for strategy in auto_strategies:
            result = svc.trigger_review(strategy.id, 'weekly', db)
            logger.info(f"策略{strategy.id}每周复盘: {result}")
    except Exception as e:
        logger.error(f"每周复盘异常: {e}")
    finally:
        db.close()


@log_task_execution("auto_fetch_quotes")
def _job_auto_fetch_quotes():
    """
    LLM自动行情补全 - 工作日18:30（盘后数据就绪）

    LLM判断哪些ETF需要补数据、补多长范围，然后自动执行。
    """
    from app.db.database import SessionLocal
    from app.config import get_settings
    from app.models.etf import ETFBasic, ETFQuotation
    from app.services.data_service import get_data_service
    from sqlalchemy import func

    _settings = get_settings()
    logger.info("===== [定时] LLM自动行情补全 =====")
    db = SessionLocal()
    try:
        all_etfs = db.query(ETFBasic).all()
        if not all_etfs:
            logger.info("ETF池为空，跳过")
            return

        stale_info = []
        for etf in all_etfs:
            latest = db.query(func.max(ETFQuotation.trade_date)).filter(
                ETFQuotation.etf_code == etf.etf_code
            ).scalar()
            stale_info.append({
                "code": etf.etf_code,
                "name": etf.etf_name or "",
                "latest_date": latest.isoformat() if latest else "无数据",
            })

        no_data = [s for s in stale_info if s["latest_date"] == "无数据"]
        today = date.today()
        stale = [s for s in stale_info
                 if s["latest_date"] != "无数据"
                 and (today - date.fromisoformat(s["latest_date"])).days > 3]

        if not no_data and not stale:
            logger.info("所有ETF数据均为最新，无需补全")
            return

        fetch_plan = _llm_plan_fetch(no_data, stale, _settings)

        svc = get_data_service()
        total_success = 0
        total_fail = 0

        for item in fetch_plan:
            code = item["code"]
            start = item["start_date"]
            end = item.get("end_date", today.strftime("%Y%m%d"))
            try:
                df = svc.fetch_etf_daily_scheduled(code, start_date=start, end_date=end)
                if not df.empty:
                    added = svc.save_daily_quotes(code, df, db)
                    total_success += 1
                    if added > 0:
                        logger.info(f"✓ {code} 补全 {added} 条")
                else:
                    total_fail += 1
            except Exception as e:
                total_fail += 1
                logger.warning(f"✗ {code} 补全失败: {e}")

            from app.services.data_service import _random_sleep
            _random_sleep()

        logger.info(f"[定时] 行情补全完成: 成功 {total_success}, 失败 {total_fail}")
    except Exception as e:
        logger.error(f"[定时] 行情补全异常: {e}")
    finally:
        db.close()


def _llm_plan_fetch(no_data: list, stale: list, settings) -> list:
    """LLM决定补全计划；LLM不可用时降级为全量补最近30天"""
    from openai import OpenAI
    import json as _json

    today_str = date.today().strftime("%Y%m%d")
    default_start = (date.today() - __import__('datetime').timedelta(days=30)).strftime("%Y%m%d")

    if not (settings.llm_api_key and settings.llm_api_key.strip()):
        logger.info("[行情补全] LLM未配置，使用默认计划（近30天）")
        return [{"code": s["code"], "start_date": default_start, "end_date": today_str}
                for s in (no_data + stale)]

    prompt = _load_prompt(
        "fetch_plan_prompt.md",
        today_str=today_str,
        default_start=default_start,
        no_data_count=len(no_data),
        no_data_json=_json.dumps(no_data[:50], ensure_ascii=False),
        stale_count=len(stale),
        stale_json=_json.dumps(stale[:50], ensure_ascii=False),
    )

    try:
        client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_api_base_url)
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=3000,
        )
        import re
        content = resp.choices[0].message.content
        match = re.search(r'\[[\s\S]*\]', content)
        if match:
            plan = _json.loads(match.group())
            logger.info(f"[行情补全] LLM生成计划: {len(plan)}只ETF")
            return plan
    except Exception as e:
        logger.warning(f"[行情补全] LLM规划失败，降级默认: {e}")

    return [{"code": s["code"], "start_date": default_start, "end_date": today_str}
            for s in (no_data + stale)]


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()

        # ========== 工作日串行管道（20:00） ==========
        # 一个 job 内部顺序执行所有步骤，不存在并行竞态问题
        _scheduler.add_job(
            _job_daily_pipeline,
            trigger=CronTrigger(day_of_week='mon-fri', hour=settings.scheduler_hour, minute=settings.scheduler_minute),
            id="daily_auto_pipeline",
            replace_existing=True,
            misfire_grace_time=7200,
        )

        # ========== 每周复盘（周三 + 周日 21:00，提示词每周进化两次） ==========
        _scheduler.add_job(
            _job_weekly_review,
            trigger=CronTrigger(day_of_week='sun', hour=21, minute=0),
            id="weekly_review",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        _scheduler.add_job(
            _job_weekly_review,
            trigger=CronTrigger(day_of_week='wed', hour=21, minute=0),
            id="midweek_review",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # ========== 工作日行情自动补全（盘后18:00-19:00随机） ==========
        # 使用 jitter 实现每天随机时间执行，避免固定时间被识别
        _scheduler.add_job(
            _job_auto_fetch_quotes,
            trigger=CronTrigger(day_of_week='mon-fri', hour=18, minute=30),
            id="auto_fetch_quotes",
            replace_existing=True,
            misfire_grace_time=3600,
            jitter=1800,  # ±30分钟，实际执行时间 18:00-19:00
        )
        logger.info("行情补全任务已调度: 工作日 18:00-19:00 随机执行")

        # ========== 交易时段舆情采集（每2小时一次：10:00, 12:00, 14:00） ==========
        for hour in [10, 12, 14]:
            _scheduler.add_job(
                _job_collect_sentiments,
                trigger=CronTrigger(day_of_week='mon-fri', hour=hour, minute=0),
                id=f"sentiment_collect_{hour}",
                replace_existing=True,
                misfire_grace_time=1800,
            )

    return _scheduler
