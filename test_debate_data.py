"""
多Agent辩论行情数据优化测试
覆盖：数据新鲜度、工具实时取数、多空喂更多数据、快照锁定
用法: source .venv/bin/activate && python test_debate_data.py
或:   pytest test_debate_data.py
"""
import unittest
from unittest.mock import MagicMock, patch
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd


make_db = lambda: None  # 占位，测试内各自构造


class TestFreshness(unittest.TestCase):
    """方向1: 数据新鲜度检查"""

    def _make_orchestrator(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator.__new__(Orchestrator)
        return o

    def test_fresh_no_sync(self):
        o = self._make_orchestrator()
        db = MagicMock()
        o._latest_quote_date = MagicMock(return_value=date(2026, 6, 10))
        with patch("app.agents.orchestrator.settings") as st:
            st.debate_max_data_lag_days = 3
            res = o._ensure_fresh_data(["510300"], date(2026, 6, 11), db)
        self.assertEqual(res["status"], "fresh")

    def test_stale_triggers_sync(self):
        o = self._make_orchestrator()
        db = MagicMock()
        # 同步前滞后，同步后仍滞后 → stale
        o._latest_quote_date = MagicMock(side_effect=[date(2026, 6, 1), date(2026, 6, 2)])
        with patch("app.agents.orchestrator.settings") as st, \
             patch("app.services.data_service.get_data_service") as gsvc:
            st.debate_max_data_lag_days = 3
            gsvc.return_value.update_today_quotes.return_value = {}
            res = o._ensure_fresh_data(["510300"], date(2026, 6, 11), db)
        self.assertEqual(res["status"], "stale")
        gsvc.return_value.update_today_quotes.assert_called_once()

    def test_sync_brings_fresh(self):
        o = self._make_orchestrator()
        db = MagicMock()
        o._latest_quote_date = MagicMock(side_effect=[date(2026, 6, 1), date(2026, 6, 10)])
        with patch("app.agents.orchestrator.settings") as st, \
             patch("app.services.data_service.get_data_service") as gsvc:
            st.debate_max_data_lag_days = 3
            res = o._ensure_fresh_data(["510300"], date(2026, 6, 11), db)
        self.assertEqual(res["status"], "synced")

    def test_no_data_stale(self):
        o = self._make_orchestrator()
        db = MagicMock()
        o._latest_quote_date = MagicMock(return_value=None)
        with patch("app.agents.orchestrator.settings") as st:
            st.debate_max_data_lag_days = 3
            res = o._ensure_fresh_data(["510300"], date(2026, 6, 11), db)
        self.assertEqual(res["status"], "stale")

    def test_lock_date(self):
        o = self._make_orchestrator()
        db = MagicMock()
        o._latest_quote_date = MagicMock(return_value=date(2026, 6, 10))
        # latest(6/10) < analysis(6/11) → 锁 6/10
        self.assertEqual(o._compute_lock_date(["510300"], date(2026, 6, 11), db), date(2026, 6, 10))
        # latest(6/12) > analysis(6/11) → 锁 6/11
        o._latest_quote_date.return_value = date(2026, 6, 12)
        self.assertEqual(o._compute_lock_date(["510300"], date(2026, 6, 11), db), date(2026, 6, 11))


class TestToolCalling(unittest.TestCase):
    """方向2: 辩论 agent 工具实时取数"""

    def _make_agent(self):
        from app.agents.bull_researcher import BullResearcher
        a = BullResearcher.__new__(BullResearcher)
        a.name = "bull_researcher"
        a.llm_client = MagicMock()
        return a

    def _tool_call_resp(self, tool_calls, final_content=None):
        if tool_calls:
            tcs = [
                SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=args))
                for i, (name, args) in enumerate(tool_calls)
            ]
            msg = SimpleNamespace(content="", tool_calls=tcs)
        else:
            msg = SimpleNamespace(content=final_content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    def test_calls_tool_then_final(self):
        a = self._make_agent()
        db = MagicMock()
        # 首轮调 get_etf_detail，次轮返回最终 JSON
        a.llm_client.chat.completions.create.side_effect = [
            self._tool_call_resp([("get_etf_detail", '{"etf_code": "510300"}')]),
            self._tool_call_resp([], '{"bullish_case": "均线多头"}'),
        ]
        a._execute_tool = MagicMock(return_value={"etf_code": "510300", "latest_close": 4.0})

        result = a.call_llm_with_tools("分析", db=db)
        self.assertEqual(result["bullish_case"], "均线多头")
        a._execute_tool.assert_called_once_with("get_etf_detail", {"etf_code": "510300"}, db)
        # 两次调用，第二次带 tool 回填
        self.assertEqual(a.llm_client.chat.completions.create.call_count, 2)

    def test_no_db_falls_back_to_plain(self):
        a = self._make_agent()
        a.call_llm = MagicMock(return_value={"bullish_case": "x"})
        result = a.call_llm_with_tools("分析", db=None)
        self.assertEqual(result["bullish_case"], "x")
        a.call_llm.assert_called_once()

    def test_read_tools_exclude_write(self):
        from app.tools.registry import ToolDef

        a = self._make_agent()
        read_tool = ToolDef(name="get_etf_history", description="d", func=lambda db: {}, parameters={}, risk_level="read")
        write_tool = ToolDef(name="execute_rebalance", description="d", func=lambda db: {}, parameters={}, risk_level="write")
        with patch("app.tools.registry._TOOL_REGISTRY", {
            "get_etf_history": read_tool, "execute_rebalance": write_tool,
        }):
            schemas = a._get_read_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        self.assertIn("get_etf_history", names)
        self.assertNotIn("execute_rebalance", names)


class TestBullMoreData(unittest.TestCase):
    """方向3: 多空辩论喂更多数据"""

    def test_bull_include_macro(self):
        from app.agents.bull_researcher import BullResearcher

        a = BullResearcher.__new__(BullResearcher)
        a.call_llm_with_tools = MagicMock(return_value={"bullish_case": "x"})
        a.analyze(
            {"overall_trend": "bullish"}, {"market_sentiment": "bullish"},
            macro_report={"cycle": "growth"}, cross_asset_report={"corr": 0.5},
            volatility_report={"regime": "low"}, data_date="2026-06-10", db=MagicMock(),
        )
        prompt = a.call_llm_with_tools.call_args[0][0]
        self.assertIn("growth", prompt)
        self.assertIn("2026-06-10", prompt)

    def test_bull_without_macro_shows_none(self):
        from app.agents.bull_researcher import BullResearcher

        a = BullResearcher.__new__(BullResearcher)
        a.call_llm_with_tools = MagicMock(return_value={"bullish_case": "x"})
        a.analyze({"overall_trend": "bullish"}, {"market_sentiment": "bullish"}, db=MagicMock())
        prompt = a.call_llm_with_tools.call_args[0][0]
        self.assertIn("暂无", prompt)  # 缺省宏观段显示"暂无"


class TestSnapshotLock(unittest.TestCase):
    """方向4: 快照锁定"""

    def _make_db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.database import Base
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_indicators_respect_end_date(self):
        from app.models.etf import ETFQuotation
        from app.services.technical_indicator_service import TechnicalIndicatorService

        db = self._make_db()
        # 造 30 个交易日，价格递增（避免布林带除零）
        for i in range(30):
            d = date(2026, 6, 1) + pd.Timedelta(days=i).to_pytimedelta()
            px = 1.0 + i * 0.01
            db.add(ETFQuotation(etf_code="510300", trade_date=d,
                                open_price=px, close_price=px, high_price=px,
                                low_price=px, volume=100, amount=100, change_pct=0))
        db.commit()

        svc = TechnicalIndicatorService()
        # 不锁 → 最新到 6/30
        r1 = svc.calculate_all_indicators("510300", db, days=30)
        self.assertEqual(r1["latest_date"], date(2026, 6, 30).isoformat())
        # 锁到 6/28 → 只取 <= 6/28（仍 >=20 条，避免"数据不足"）
        r2 = svc.calculate_all_indicators("510300", db, days=30, end_date=date(2026, 6, 28))
        self.assertEqual(r2["latest_date"], date(2026, 6, 28).isoformat())

    def test_nav_changes_respect_lock(self):
        from app.models.etf import ETFQuotation
        from app.agents.market_analyst import MarketAnalystAgent

        db = self._make_db()
        for d in [date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)]:
            db.add(ETFQuotation(etf_code="510300", trade_date=d,
                                open_price=1.0, close_price=1.0, high_price=1.0,
                                low_price=1.0, volume=100, amount=100, change_pct=0))
        db.commit()

        a = MarketAnalystAgent.__new__(MarketAnalystAgent)
        res = a._get_nav_changes(["510300"], db, lock_date=date(2026, 6, 9))
        self.assertEqual(res["510300"]["latest_nav"], 1.0)


if __name__ == "__main__":
    unittest.main()