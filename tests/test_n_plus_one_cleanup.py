"""N+1 清理：查询次数不随标的/代码/策略数量增长（用 SQL 计数验证，而非只看结果对不对）"""
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
# create_all 只建「已导入模型」的表，故先导入全部模型（与 init_db 口径一致）
from app.models import (  # noqa: F401
    allocation_suggestion, auto_strategy_log, chat, etf, experience, factor_performance,
    market_regime, pipeline_checkpoint, portfolio, sentiment, stock_fundamental, strategy,
    sw_industry, system_config, task_log,
)
from app.models.etf import ETFBasic, ETFQuotation  # noqa: F401
from app.models.experience import Experience  # noqa: F401
from app.models.portfolio import PortfolioSnapshot, TradeRecord  # noqa: F401
from app.models.strategy import Strategy  # noqa: F401
from tests.test_allocation_suggestion import DAY, seed_etf, seed_strategy


def make_db():
    """内存 SQLite + 全表 schema"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def counting(db, fragment: str):
    """统计执行了包含 fragment 的 SQL 的次数，返回 (holder, stop)"""
    engine = db.get_bind()
    holder = {"n": 0}

    def listen(conn, cursor, statement, params, context, executemany):
        if fragment in statement.lower():
            holder["n"] += 1

    event.listen(engine, "before_cursor_execute", listen)

    def stop():
        event.remove(engine, "before_cursor_execute", listen)
        return holder["n"]

    return holder, stop


def _settings(**attrs):
    st = MagicMock()
    st.llm_api_key = ""
    st.suggestion_auto_execute = False
    for k, v in attrs.items():
        setattr(st, k, v)
    return st


class TestMinHoldBatch(unittest.TestCase):
    """最短持有期：一次查回全部持仓的最近买入日"""

    def _count(self, codes):
        from app.services.rotation_service import RotationService
        db = make_db()
        alloc = {c: round(1.0 / len(codes), 4) for c in codes}
        alloc[codes[0]] = round(1.0 - sum(v for c, v in alloc.items() if c != codes[0]), 4)
        seed_strategy(db, sid=1, pool=alloc)
        for i, c in enumerate(codes):
            seed_etf(db, c, f"持仓{i}", score=50.0 + i)
        seed_etf(db, "159915", "候选", score=99.0)
        for c in codes:
            db.add(TradeRecord(strategy_id=1, etf_code=c, trade_date=DAY - timedelta(days=30),
                               direction="buy", quantity=1000, price=1.0, amount=1000.0))
        db.commit()

        svc = RotationService()
        _holder, stop = counting(db, "from trade_record")
        with patch("app.config.get_settings", return_value=_settings()), \
             patch("app.services.sector_selection_service.SectorSelectionService.get_mode",
                   return_value="off"), \
             patch.object(svc, "_build_rule_signal", return_value=None), \
             patch.object(svc, "_attach_value_signals"):
            svc.evaluate_rotation(1, DAY, db)
        db.close()
        return stop()

    def test_query_count_is_constant(self):
        one = self._count(["512480"])
        four = self._count(["512480", "511800", "159995", "510300"])
        self.assertEqual(one, four, f"最短持有期查询随持仓数增长: {one} -> {four}")
        self.assertEqual(four, 1, "应只有一次批量查询")

    def test_batch_and_per_code_agree(self):
        """批量口径与逐标的口径一致：不同买入日的持仓都能取对"""
        from app.services.rotation_service import RotationService
        svc = RotationService()
        db = make_db()
        recent = {"512480": DAY - timedelta(days=30),
                  "511800": DAY - timedelta(days=1),
                  "159995": DAY - timedelta(days=8)}
        for code, buy_day in recent.items():
            db.add(TradeRecord(strategy_id=1, etf_code=code, trade_date=buy_day,
                               direction="buy", quantity=100, price=1.0, amount=100.0))
        # 重复买入：应取最近一次
        db.add(TradeRecord(strategy_id=1, etf_code="512480", trade_date=recent["512480"] + timedelta(days=2),
                           direction="buy", quantity=100, price=1.0, amount=100.0))
        db.commit()
        batch = svc._latest_buy_dates(1, list(recent) + ["588000"], db)
        self.assertEqual(batch["512480"], recent["512480"] + timedelta(days=2))
        for code in recent:
            self.assertEqual(batch[code], svc._latest_buy_dates(1, [code], db)[code],
                             f"{code} 批量与单查口径不一致")
        self.assertNotIn("588000", batch, "无买入记录的标的不应凭空出现")
        # 持有期判定：1天前的不可换，8天前的可换，无记录的视为可换
        self.assertFalse(svc._held_long_enough(batch["511800"], DAY))
        self.assertTrue(svc._held_long_enough(batch["159995"], DAY))
        self.assertTrue(svc._held_long_enough(None, DAY))
        db.close()


class TestSuggestionCodeBatch(unittest.TestCase):
    """建议标的存在性校验：一次 IN 查询"""

    def _count(self, extra_codes):
        from app.services.allocation_suggestion_service import AllocationSuggestionService
        db = make_db()
        seed_strategy(db, sid=1, pool={"512480": 1.0})
        seed_etf(db, "512480", "半导体ETF")
        codes = ["512480"] + list(extra_codes)
        weight = round(1.0 / len(codes), 4)
        alloc = {c: weight for c in codes}
        alloc["512480"] = round(1.0 - weight * (len(codes) - 1), 4)
        _holder, stop = counting(db, "from etf_basic")
        result = AllocationSuggestionService().create(db, 1, alloc, reason="批量校验")
        db.close()
        return stop(), result

    def test_query_count_is_constant(self):
        one, r1 = self._count(["511800"])
        four, r2 = self._count(["511800", "159995", "510300", "512760"])
        self.assertEqual(one, four, f"ETF 存在性校验仍逐代码查询: {one} -> {four}")
        self.assertEqual(one, 1, "应只有一次 IN 查询")
        # 未知代码的报错内容应包含全部未知标的（一次差集，不是首个即返回）
        self.assertIn("159995", r2["error"])
        self.assertIn("512760", r2["error"])

    def test_unknown_code_still_rejected(self):
        from app.services.allocation_suggestion_service import AllocationSuggestionService
        from app.models.allocation_suggestion import AllocationSuggestion
        db = make_db()
        seed_strategy(db, sid=1, pool={"512480": 1.0})
        seed_etf(db, "512480", "半导体ETF")
        r = AllocationSuggestionService().create(db, 1, {"512480": 0.6, "999999": 0.4}, reason="校验")
        self.assertIn("ETF 列表", r["error"])
        self.assertEqual(db.query(AllocationSuggestion).count(), 0)
        db.close()


class TestBannedCodesCacheAndExpiry(unittest.TestCase):
    """规避名单：过期不再规避 + 当日缓存 + 写入即失效"""

    def setUp(self):
        from app.services.failure_mode_service import FailureModeService
        self.svc = FailureModeService()
        self.db = make_db()
        for cnt, expires, code in [(5, DAY + timedelta(days=30), "512480"),   # 活跃且未过期
                                   (5, DAY - timedelta(days=1), "511800")]:   # 已过期
            self.db.add(Experience(
                strategy_id=1, experience_type="failure", title=f"失败{code}",
                description="d", scenario_tags=["轮动"], result="negative",
                is_active=True, generated_date=DAY, expires_date=expires,
                failure_signature=f"买入{code} 权重40%", occurrence_count=cnt,
                last_triggered_date=DAY, weight=1.0,
            ))
        self.db.commit()
        # 各用例共用同一份日期口径（缓存按 (engine, day) 取键）
        patcher = patch("app.services.failure_mode_service.date")
        self.today = patcher.start()
        self.today.today.return_value = DAY
        self.addCleanup(patcher.stop)

    def test_expired_pattern_not_banned(self):
        banned = self.svc.get_banned_codes(self.db, use_cache=False)
        self.assertIn("512480", banned)
        self.assertNotIn("511800", banned, "已过期的失败模式仍在规避")

    def test_second_call_uses_cache(self):
        _h1, stop1 = counting(self.db, "from experience")
        self.svc.get_banned_codes(self.db)
        first = stop1()
        _h2, stop2 = counting(self.db, "from experience")
        self.svc.get_banned_codes(self.db)
        second = stop2()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "同日二次调用未命中缓存")

    def test_cache_is_not_shared_across_databases(self):
        """缓存按 engine 身份取键：另一个库的名单不能读到当前库（否则跨库串规避）"""
        self.svc.get_banned_codes(self.db)              # 写入 self.db 的缓存
        other = make_db()
        other.add(Experience(
            strategy_id=1, experience_type="failure", title="另一库的失败",
            description="d", scenario_tags=["轮动"], result="negative",
            is_active=True, generated_date=DAY, expires_date=DAY + timedelta(days=30),
            failure_signature="买入588000 权重40%", occurrence_count=5,
            last_triggered_date=DAY, weight=1.0,
        ))
        other.commit()
        banned = self.svc.get_banned_codes(other)
        self.assertIn("588000", banned, "读到别库的规避名单")
        self.assertNotIn("512480", banned, "当前库的名单不应出现在新库结果里")
        other.close()

    def test_record_failure_invalidates_cache(self):
        self.svc.get_banned_codes(self.db)              # 先填缓存
        self.svc.record_failure(
            strategy_id=1, signature="买入588000 重复失败", title="t",
            description="d", scenario_tags=["轮动"], db=self.db)
        _h, stop = counting(self.db, "from experience")
        banned = self.svc.get_banned_codes(self.db)
        self.assertEqual(stop(), 1, "写入失败模式后缓存未失效")
        self.assertNotIn("588000", banned, "未达阈值的新模式不应规避")


class TestMarketBackfillBatch(unittest.TestCase):
    """行情补全：一次 GROUP BY 取回全部标的的最新行情日"""

    def _count(self, n_etfs):
        from app.tasks import scheduler as sch
        db = make_db()
        for i in range(n_etfs):
            code = f"{500000 + i:06d}"
            db.add(ETFBasic(etf_code=code, etf_name=code))
            db.add(ETFQuotation(etf_code=code, trade_date=date.today(), open_price=1.0,
                                close_price=1.0, high_price=1.0, low_price=1.0,
                                volume=1, amount=1, change_pct=0.0))
        db.commit()
        _holder, stop = counting(db, "from etf_quotation")
        with patch("app.db.database.SessionLocal", lambda: db), \
             patch("app.config.get_settings", return_value=_settings()), \
             patch.object(sch, "_llm_plan_fetch", return_value=[]):
            sch._job_auto_fetch_quotes.__wrapped__()
        n = stop()
        db.close()
        return n

    def test_query_count_is_constant(self):
        few = self._count(3)
        many = self._count(20)
        self.assertEqual(few, many, f"行情新鲜度查询随 ETF 数增长: {few} -> {many}")
        self.assertLessEqual(many, 1, f"应只有一次 GROUP BY，实测 {many} 次")

    def test_stale_and_missing_still_planned(self):
        """批量取回的行情日仍用于判定陈旧/无数据（行为不变）"""
        from app.tasks import scheduler as sch
        db = make_db()
        db.add(ETFBasic(etf_code="512480", etf_name="半导体ETF"))
        db.add(ETFQuotation(etf_code="512480", trade_date=date.today() - timedelta(days=10),
                            open_price=1.0, close_price=1.0, high_price=1.0, low_price=1.0,
                            volume=1, amount=1, change_pct=0.0))
        db.add(ETFBasic(etf_code="511800", etf_name="货币ETF"))     # 无行情
        db.add(ETFBasic(etf_code="159995", etf_name="芯片ETF"))
        db.add(ETFQuotation(etf_code="159995", trade_date=date.today(),
                            open_price=1.0, close_price=1.0, high_price=1.0, low_price=1.0,
                            volume=1, amount=1, change_pct=0.0))
        db.commit()
        captured = {}

        def fake_plan(no_data, stale, settings):
            captured["no_data"] = [s["code"] for s in no_data]
            captured["stale"] = [s["code"] for s in stale]
            return []

        with patch("app.db.database.SessionLocal", lambda: db), \
             patch("app.config.get_settings", return_value=_settings()), \
             patch.object(sch, "_llm_plan_fetch", side_effect=fake_plan):
            sch._job_auto_fetch_quotes.__wrapped__()
        self.assertEqual(captured["no_data"], ["511800"])
        self.assertEqual(captured["stale"], ["512480"])
        db.close()


class TestContextSectionsBatch(unittest.TestCase):
    """上下文快照：目标进度与风控 section 的查询次数不随策略数增长"""

    def _seed(self, n_strategies):
        db = make_db()
        today = date.today()
        for i in range(n_strategies):
            sid = i + 1
            db.add(Strategy(id=sid, name=f"策略{sid}", allocation_config={"512480": 1.0},
                            strategy_source="auto_generated", auto_strategy_status="running",
                            status="active", initial_capital=100000.0,
                            target_monthly_min=0.05, target_monthly_max=0.10))
            for d in range(6):
                db.add(PortfolioSnapshot(
                    strategy_id=sid, trade_date=today - timedelta(days=d + 1),
                    total_asset=100000.0 + d * 100, cash=0.0,
                    market_value=100000.0, profit=0.0, profit_pct=0.01 * d,
                ))
        db.commit()
        return db

    def _count(self, n_strategies, method):
        from app.agent_core.context import ContextBuilder
        db = self._seed(n_strategies)
        _holder, stop = counting(db, "from portfolio_snapshot")
        getattr(ContextBuilder(), method)(db)
        n = stop()
        db.close()
        return n

    def test_progress_section_queries_are_constant(self):
        few = self._count(1, "_get_strategies_summary")
        many = self._count(4, "_get_strategies_summary")
        self.assertEqual(few, many, f"目标进度快照查询随策略数增长: {few} -> {many}")
        self.assertEqual(many, 1, "应只有一次批量快照查询")

    def test_risk_section_queries_are_constant(self):
        few = self._count(1, "_get_risk_summary")
        many = self._count(3, "_get_risk_summary")
        self.assertEqual(few, many, f"风控快照查询随策略数增长: {few} -> {many}")
        self.assertEqual(many, 1, "应只有一次批量快照查询")

    def test_batched_risk_matches_per_strategy(self):
        """批量风控结果与逐策略调用完全一致（只改查询方式，不改判定口径）"""
        from app.services.risk_controller import RiskController
        db = make_db()
        today = date.today()
        for i, drop in enumerate([0.0, 0.20]):    # 第二个策略最新快照回撤 20% → 熔断
            sid = i + 1
            db.add(Strategy(id=sid, name=f"策略{sid}", allocation_config={"512480": 1.0},
                            strategy_source="auto_generated", auto_strategy_status="running",
                            status="active", initial_capital=100000.0))
            for d in range(10):
                asset = 100000.0 * (1 - drop) if d == 0 else 100000.0
                db.add(PortfolioSnapshot(
                    strategy_id=sid, trade_date=today - timedelta(days=d),
                    total_asset=asset, cash=0.0, market_value=asset, profit=0.0,
                    profit_pct=0.0,
                ))
        db.commit()
        strategies = db.query(Strategy).filter(Strategy.id.in_([1, 2])).all()
        ctrl = RiskController()
        batched = ctrl.assess_risk_map(strategies, db)
        for s in strategies:
            self.assertEqual(batched[s.id]["circuit_breaker"],
                             ctrl.check_circuit_breaker(s.id, db))
            self.assertEqual(batched[s.id]["drawdown_protection"],
                             ctrl.apply_drawdown_protection(s.id, db))
        self.assertEqual(batched[2]["circuit_breaker"]["status"], "triggered")
        self.assertEqual(batched[1]["circuit_breaker"]["status"], "normal")
        db.close()

    def test_batched_progress_matches_per_strategy(self):
        """批量月进度与逐策略口径一致"""
        from app.services.portfolio_service import PortfolioService
        db = self._seed(3)
        svc = PortfolioService()
        strategies = db.query(Strategy).all()
        batched = svc.get_monthly_progress_map(strategies, db)
        self.assertEqual(set(batched), {s.id for s in strategies})
        for s in strategies:
            self.assertEqual(batched[s.id], svc.get_monthly_progress(s.id, db))
        db.close()


if __name__ == "__main__":
    unittest.main()
