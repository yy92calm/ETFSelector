"""
定时任务调度器
每个工作日执行串行管道：

  净值更新 → (间隔) 组合执行/舆情采集 → (间隔) AI分析+风险检查+策略调整

关键原则：每一步只在前一步完成后才执行，通过单个 job 内的串行调用实现。
"""

import logging
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler | None = None

# 自主决策指令：驱动LLM主动管理而非被动维持
AUTONOMOUS_INSTRUCTION = """你是ETF量化工作台的自主决策大脑，请执行以下完整决策流程：

【第1步：感知市场】
- 查看市场概况，识别当日热点板块和异动ETF
- 查看舆情数据，判断市场情绪方向

【第2步：审视持仓】
- 检查所有活跃策略的持仓状态和收益表现
- 检查风控状态（熔断/回撤）

【第3步：主动决策】根据以上分析，自主执行以下操作（可多选）：
- 调整配置：如果某ETF趋势走弱或另一只更强，用 update_allocation 调整比例
- 发现新标的：如果市场出现新热点，用 search_etf 搜索相关ETF，用 add_etf_to_pool 拉取数据，然后纳入策略配置
- 汰换劣策略：如果某策略连续表现差（收益远低于市场），用 pause_strategy 暂停它
- 创建新策略：如果发现明确机会，用 create_strategy 建立新组合
- 触发深度分析：对重点策略调用 run_multi_agent_analysis 获取辩论式分析结论

【第4步：输出结论】
- 先给出今日操作摘要（做了什么/为什么）
- 如果确实无需任何操作，说明原因

重要原则：
- 你是主动管理者，不是被动观察者。发现机会要敢于行动
- 每次至少检查是否有优化空间，而不是默认维持现状
- 风控优先：任何操作前先确认风控状态正常
- 分散风险：单一ETF配置不超过40%
"""


def _job_daily_pipeline():
    """
    每日自驱动串行管道（LLM驱动 + fallback）

    阶段1: 净值更新 + 组合再平衡（确定性操作）
    阶段2: 舆情采集 + 政策评估 + 资金流向（数据采集层）
    阶段3: 全市场量化扫描 + 轮动复盘（纯量化）
    阶段4: LLM自主决策（感知→推理→行动），失败时降级为原有管道
    """
    # ============================== 阶段1 ==============================
    _step_update_net_values()
    _step_run_strategies()

    # ============================== 阶段2 ==============================
    _step_collect_sentiments()
    _step_policy_impact()
    _step_capital_flow()

    # ============================== 阶段3 ==============================
    _step_market_scan()
    _step_rotation_review()

    # ============================== 阶段4 ==============================
    _step_autonomous_decision()


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
    """STEP 6: 全市场量化指标扫描（纯计算）"""
    from app.db.database import SessionLocal
    from app.services.market_scanner_service import get_market_scanner_service

    logger.info("===== [阶段3] 全市场量化扫描 =====")
    db = SessionLocal()
    try:
        svc = get_market_scanner_service()
        result = svc.scan_all(date.today(), db)
        logger.info(f"量化扫描完成: {result}")
    except Exception as e:
        logger.error(f"量化扫描异常: {e}")
    finally:
        db.close()


def _step_rotation_review():
    """STEP 7: 轮动复盘 — 评估所有自动策略是否需要换仓（持仓≤5，有进必出）"""
    from app.db.database import SessionLocal
    from app.services.rotation_service import get_rotation_service
    from app.models.strategy import Strategy

    logger.info("===== [阶段3] 轮动复盘 =====")
    db = SessionLocal()
    try:
        strategies = db.query(Strategy).filter(
            Strategy.strategy_source == "auto_generated",
            Strategy.auto_strategy_status == "running",
        ).all()

        if not strategies:
            logger.info("无活跃自动策略，跳过轮动")
            return

        svc = get_rotation_service()
        for strategy in strategies:
            plan = svc.evaluate_rotation(strategy.id, date.today(), db)
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
                df = svc.fetch_etf_daily(code, start_date=start, end_date=end)
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

    prompt = f"""你是ETF数据管理助手。以下是需要补全行情数据的ETF列表。
请为每只ETF决定需要拉取的起始日期（格式YYYYMMDD），结束日期统一为{today_str}。

## 无历史数据的ETF（{len(no_data)}只）
{_json.dumps(no_data[:50], ensure_ascii=False)}

## 数据过期的ETF（{len(stale)}只，latest_date为当前最新日期）
{_json.dumps(stale[:50], ensure_ascii=False)}

## 规则
- 无数据的ETF：start_date设为30天前（{default_start}）
- 过期ETF：start_date设为其latest_date次日
- 如果列表超过50只，只输出前50只最重要的（优先策略持仓中的ETF）

输出JSON数组（不要其他文字）：
[{{"code": "ETF代码", "start_date": "YYYYMMDD", "end_date": "{today_str}"}}]"""

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

        # ========== 工作日串行管道 ==========
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

        # ========== 工作日行情自动补全（盘后18:30） ==========
        _scheduler.add_job(
            _job_auto_fetch_quotes,
            trigger=CronTrigger(day_of_week='mon-fri', hour=18, minute=30),
            id="auto_fetch_quotes",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    return _scheduler
