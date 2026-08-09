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
               "market_scan", "rotation_review", "autonomous"]

    db = SessionLocal()
    try:
        done = _cp_svc.get_done_stages("daily_pipeline", _run_date, db)
    finally:
        db.close()

    def _run_stage(stage: str, fn):
        """执行阶段，成功标记检查点；失败记录并跳过（不中断整个管道）"""
        if stage in done:
            logger.info(f"[Checkpoint] 阶段 {stage} 已完成，跳过")
            return
        db_local = SessionLocal()
        try:
            fn()
            _cp_svc.mark_stage_done("daily_pipeline", _run_date, stage, db_local)
        except Exception as e:
            logger.error(f"[Checkpoint] 阶段 {stage} 失败: {e}")
            try:
                _cp_svc.mark_failed("daily_pipeline", _run_date, stage, str(e), db_local)
            finally:
                db_local.close()
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
    _run_stage("rotation_review", _step_rotation_review)

    # ============================== 阶段4 ==============================
    _run_stage("autonomous", _step_autonomous_decision)

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
        else:
            logger.info("===== [阶段2] LLM未配置，降级为原有管道 =====")
            _step_auto_pipeline_fallback(db)
    except Exception as e:
        logger.error(f"LLM自主决策异常，降级为原有管道: {e}")
        _step_auto_pipeline_fallback(db)
    finally:
        db.close()


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
    """每周复盘 - 每周日21:00"""
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

        # ========== 每周复盘 ==========
        _scheduler.add_job(
            _job_weekly_review,
            trigger=CronTrigger(day_of_week='sun', hour=21, minute=0),
            id="weekly_review",
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
                _step_collect_sentiments,
                trigger=CronTrigger(day_of_week='mon-fri', hour=hour, minute=0),
                id=f"sentiment_collect_{hour}",
                replace_existing=True,
                misfire_grace_time=1800,
            )

    return _scheduler
