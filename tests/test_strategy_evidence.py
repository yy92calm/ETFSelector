"""策略决策依据闭环测试：规则建议、规则注入辩论、依据聚合、工具映射与留痕"""
import unittest
from datetime import date
from unittest.mock import patch

from tests.test_value_model import make_db


DAY = date(2026, 9, 21)


def seed_strategy(db, pool=None, name="依据策略"):
    from app.models.strategy import Strategy
    s = Strategy(name=name, allocation_config=pool or {}, strategy_type="auto", status="active",
                 strategy_source="auto_generated", auto_strategy_status="running")
    db.add(s)
    db.commit()
    return s


def seed_indicator(db, code, day=DAY, score=60.0, mom5=1.0, mom20=2.0, vol=10.0):
    from app.models.etf import ETFDailyIndicator
    db.add(ETFDailyIndicator(etf_code=code, trade_date=day, composite_score=score,
                             rank_in_market=1, momentum_5d=mom5, momentum_20d=mom20,
                             trend_strength=2, volatility_20d=vol, vol_ratio=1.0,
                             ma5=1.0, ma10=1.0, ma20=1.0))
    db.commit()


def seed_etf_name(db, code, name):
    from app.models.etf import ETFBasic
    db.add(ETFBasic(etf_code=code, etf_name=name))
    db.commit()


def seed_rule_snapshot(db, sid, regime, alloc, sample=12):
    from app.models.strategy import RuleSnapshot
    rules = {
        "scope": f"strategy:{sid}",
        "regime_rules": {regime: {"avg_allocation": alloc, "sample_count": sample,
                                  "typical_action": "rebalance"}},
        "training_period": {"days": 30, "start": "2026-08-01", "end": "2026-08-30"},
    }
    db.add(RuleSnapshot(strategy_id=sid, snapshot=rules, source="manual", days_covered=30))
    db.commit()
    return rules


class TestRuleSuggestion(unittest.TestCase):
    """RuleEngine.get_rule_suggestion：规则依据"""

    def setUp(self):
        from app.services.rule_engine import RuleEngine
        self.engine = RuleEngine()
        self.db = make_db()
        self.s = seed_strategy(self.db, pool={"510300": 0.6, "511010": 0.4})
        seed_indicator(self.db, "510300", score=80)
        seed_indicator(self.db, "511010", score=40)

    def test_ai_history_converged_to_pool(self):
        """AI规则：池外标的剔除、归一化、来源与偏离正确"""
        seed_rule_snapshot(self.db, self.s.id, "neutral", {"510300": 0.5, "159915": 0.3, "511010": 0.2})
        with patch.object(type(self.engine), "_compute_regime", return_value="neutral"):
            sig = self.engine.get_rule_suggestion(
                DAY, self.db, strategy_id=self.s.id, base_allocation=self.s.allocation_config)

        self.assertEqual(sig["rule_source"], "ai_history_strategy")
        self.assertEqual(sig["regime_label"], "震荡市")
        self.assertEqual(set(sig["suggested_allocation"].keys()), {"510300", "511010"})
        self.assertAlmostEqual(sum(sig["suggested_allocation"].values()), 1.0, places=3)
        self.assertEqual(sig["sample_count"], 12)

        dev = {d["etf_code"]: d for d in sig["deviation"]}
        self.assertAlmostEqual(dev["510300"]["current"], 0.6, places=3)
        self.assertAlmostEqual(dev["510300"]["suggested"], 0.7143, places=3)  # 0.5 / (0.5+0.2)
        self.assertGreater(dev["510300"]["delta"], 0)

    def test_deterministic_when_no_rules(self):
        """无AI规则样本 → 确定性规则分支"""
        with patch.object(type(self.engine), "_ensure_trained_rules", return_value=None), \
             patch.object(type(self.engine), "_compute_regime", return_value="neutral"):
            sig = self.engine.get_rule_suggestion(
                DAY, self.db, strategy_id=self.s.id, base_allocation=self.s.allocation_config)
        self.assertEqual(sig["rule_source"], "deterministic")
        self.assertEqual(sig["rule_source_label"], "确定性规则")
        self.assertEqual(set(sig["suggested_allocation"].keys()), {"510300", "511010"})
        self.assertIn("得分", sig["note"])

    def test_no_indicator_degrades(self):
        """当日无指标 → 回退当前配置并标注来源"""
        empty_db = make_db()
        s = seed_strategy(empty_db, pool={"510300": 1.0})
        with patch.object(type(self.engine), "_ensure_trained_rules", return_value=None):
            sig = self.engine.get_rule_suggestion(
                DAY, empty_db, strategy_id=s.id, base_allocation=s.allocation_config)
        self.assertEqual(sig["rule_source"], "no_indicator")
        self.assertEqual(sig["suggested_allocation"], {"510300": 1.0})
        self.assertEqual([d["delta"] for d in sig["deviation"]], [0.0])

    def test_returns_market_context(self):
        seed_rule_snapshot(self.db, self.s.id, "neutral", {"510300": 1.0})
        with patch.object(type(self.engine), "_compute_regime", return_value="neutral"):
            sig = self.engine.get_rule_suggestion(
                DAY, self.db, strategy_id=self.s.id, base_allocation=self.s.allocation_config)
        self.assertIn("avg_score", sig["market_context"])


class TestRuleSignalInjection(unittest.TestCase):
    """轮动决策：规则依据注入辩论并在计划中返回"""

    def setUp(self):
        from app.services.rotation_service import RotationService
        self.svc = RotationService()
        self.db = make_db()
        self.s = seed_strategy(self.db, pool={"510300": 1.0})
        seed_indicator(self.db, "510300", score=50)
        seed_indicator(self.db, "159915", score=75)   # 候选领先25分，过5分门槛

    def test_rule_signal_passed_to_debate_and_returned(self):
        captured = {}

        def fake_debate(holdings, candidates, rule_signal=None, sector_context="",
                            sentiment_context=""):
            captured["rule_signal"] = rule_signal
            captured["sector_context"] = sector_context
            captured["holdings"] = holdings
            captured["candidates"] = candidates
            return {"decision": "hold", "final_swaps": [], "summary": "维持持仓"}

        fake_signal = {"regime": "neutral", "rule_source": "deterministic",
                       "suggested_allocation": {"510300": 1.0}}
        with patch.object(self.svc, "_build_rule_signal", return_value=fake_signal), \
             patch.object(self.svc, "_run_debate", side_effect=fake_debate):
            plan = self.svc.evaluate_rotation(self.s.id, DAY, self.db)

        self.assertIs(captured["rule_signal"], fake_signal)
        self.assertEqual(captured["candidates"][0]["etf_code"], "159915")
        self.assertIs(plan["rule_signal"], fake_signal)
        self.assertEqual(plan["action"], "hold")

    def test_build_rule_signal_degrades_on_error(self):
        with patch("app.services.rule_engine.get_rule_engine",
                   side_effect=RuntimeError("boom")):
            self.assertIsNone(self.svc._build_rule_signal(self.s, DAY, self.db))


class TestRuleContextFormat(unittest.TestCase):
    """辩论材料的规则参考渲染"""

    def test_format_with_signal(self):
        from app.agents.rotation_debate.orchestrator import format_rule_context
        text = format_rule_context({
            "regime": "bull_weak", "regime_label": "弱牛市",
            "rule_source_label": "本策略AI规则", "sample_count": 12,
            "suggested_allocation": {"518850": 0.6, "511010": 0.4},
            "deviation": [
                {"etf_code": "518850", "delta": 0.1},
                {"etf_code": "511010", "delta": -0.1},
                {"etf_code": "510300", "delta": 0.001},
            ],
            "note": "调仓 (样本12天)",
        })
        self.assertIn("弱牛市", text)
        self.assertIn("本策略AI规则", text)
        self.assertIn("518850 60%", text)
        self.assertIn("518850 +10%", text)
        self.assertNotIn("510300", text)      # 偏离<1%不展示
        self.assertIn("规则说明", text)

    def test_format_without_signal(self):
        from app.agents.rotation_debate.orchestrator import format_rule_context
        self.assertIn("无规则参考", format_rule_context(None))


class TestEvidenceService(unittest.TestCase):
    """StrategyEvidenceService：四类依据 + 决策留痕"""

    def setUp(self):
        from app.services.strategy_evidence_service import StrategyEvidenceService
        self.svc = StrategyEvidenceService()
        self.db = make_db()
        self.s = seed_strategy(self.db, pool={"512800": 0.6, "511010": 0.4})
        seed_etf_name(self.db, "512800", "银行ETF")
        seed_etf_name(self.db, "511010", "国债ETF")
        seed_indicator(self.db, "512800", score=72.0)
        seed_indicator(self.db, "511010", score=40.0)
        seed_indicator(self.db, "159915", score=88.0)     # 池外最强候选
        # 池内标的全部持仓（持仓关系来自 holding 表）
        from app.models.portfolio import Holding
        for code, mv in (("512800", 60000.0), ("511010", 40000.0)):
            self.db.add(Holding(strategy_id=self.s.id, etf_code=code, quantity=100,
                                avg_cost=1.0, current_price=1.0, market_value=mv))
        self.db.commit()

    def test_market_section(self):
        ev = self.svc.get_evidence(self.s.id, self.db)["market"]
        self.assertEqual(ev["as_of"], DAY.isoformat())
        self.assertEqual({i["etf_code"] for i in ev["items"]}, {"512800", "511010"})
        self.assertEqual(ev["candidates"][0]["etf_code"], "159915")
        self.assertEqual(ev["weakest"]["etf_code"], "511010")
        self.assertAlmostEqual(ev["gap"], 48.0, places=1)   # 88 - 40
        self.assertTrue(ev["trigger"])

    def test_research_section(self):
        from app.models.stock_fundamental import IndustryScore
        self.db.add(IndustryScore(trade_date=DAY, industry="货币金融业", score=88.0,
                                  rank=1, sample_count=10))
        self.db.commit()
        ev = self.svc.get_evidence(self.s.id, self.db)["research"]
        by_code = {i["etf_code"]: i for i in ev["items"]}
        self.assertEqual(by_code["512800"]["industry"], "货币金融业")
        self.assertEqual(by_code["512800"]["rank"], 1)
        self.assertIsNone(by_code["511010"]["industry"])
        self.assertEqual(ev["matched"], 1)

    def test_rules_section(self):
        seed_rule_snapshot(self.db, self.s.id, "neutral", {"512800": 1.0})
        with patch("app.services.rule_engine.RuleEngine._compute_regime", return_value="neutral"):
            ev = self.svc.get_evidence(self.s.id, self.db)["rules"]
        self.assertEqual(ev["rule_source"], "ai_history_strategy")
        self.assertEqual(set(ev["suggested_allocation"].keys()), {"512800"})

    def test_sentiment_section(self):
        from app.models.sentiment import SentimentData
        self.db.add(SentimentData(data_date=DAY, source="eastmoney", data_type="news",
                                  title="银行板块走强", related_etfs=["512800", "159915"],
                                  sentiment_score=0.6, sentiment_label="positive"))
        self.db.add(SentimentData(data_date=DAY, source="eastmoney", data_type="news",
                                  title="无关新闻", related_etfs=["510999"],
                                  sentiment_score=-0.6, sentiment_label="negative"))
        self.db.commit()
        ev = self.svc.get_evidence(self.s.id, self.db)["sentiment"]
        self.assertEqual(ev["total"], 1)                 # 只统计落在策略池/持仓内的
        self.assertAlmostEqual(ev["avg_score"], 0.6, places=2)
        self.assertEqual(ev["recent"][0]["etf_codes"], ["512800"])

    def test_decision_section_with_evidence(self):
        from app.models.auto_strategy_log import AutoStrategyLog
        self.db.add(AutoStrategyLog(
            strategy_id=self.s.id, log_date=DAY, status="success", action_type="analyzed",
            analysis_result={
                "suggested_action": "hold", "action_reason": "依据充分，维持持仓",
                "market_regime": "neutral", "key_signals_summary": ["银行板块性价比第一"],
                "evidence": {"sources_cited": ["market", "research"],
                             "snapshot": {"as_of": DAY.isoformat()}},
            },
        ))
        self.db.commit()
        ev = self.svc.get_evidence(self.s.id, self.db)["decision"]
        self.assertEqual(ev["action"], "hold")
        self.assertEqual(ev["sources_cited"], ["market", "research"])
        self.assertEqual(ev["snapshot"]["as_of"], DAY.isoformat())

    def test_missing_strategy(self):
        self.assertEqual(self.svc.get_evidence(999, self.db), {"exists": False})
        self.assertEqual(self.svc.get_snapshot(999, self.db), {})

    def test_snapshot_is_compact_and_complete(self):
        seed_rule_snapshot(self.db, self.s.id, "neutral", {"512800": 1.0})
        snap = self.svc.get_snapshot(self.s.id, self.db)
        self.assertEqual(set(snap.keys()), {"as_of", "market", "research", "sector", "rules", "sentiment"})
        self.assertIn("gap", snap["market"])
        self.assertIn("suggested_allocation", snap["rules"])
        self.assertNotIn("items", snap["market"])        # 明细不落盘

    def test_section_failure_isolated(self):
        """单段依据计算失败不影响其它段落"""
        with patch.object(type(self.svc), "_research_evidence", side_effect=RuntimeError("boom")):
            ev = self.svc.get_evidence(self.s.id, self.db)
        self.assertIsNone(ev["research"])
        self.assertIsNotNone(ev["market"])


class TestToolSourceMapping(unittest.TestCase):
    """决策留痕：工具调用 → 依据类别"""

    def test_classify(self):
        from app.services.strategy_evidence_service import classify_tool_sources
        result = classify_tool_sources([
            "get_market_position_signal", "get_rule_suggestion",
            "get_sentiment_data", "get_industry_ranking", "unknown_tool",
        ])
        self.assertEqual(set(result.keys()), {"market", "research", "rules", "sentiment"})
        self.assertEqual(result["rules"], ["get_rule_suggestion"])
        self.assertNotIn("unknown_tool", str(result))

    def test_classify_empty(self):
        from app.services.strategy_evidence_service import classify_tool_sources
        self.assertEqual(classify_tool_sources([]), {})
        self.assertEqual(classify_tool_sources(["get_pipeline_status"]), {})


class TestEvidenceToolsRegistered(unittest.TestCase):
    """新增依据工具已注册且为只读"""

    def test_tools_present(self):
        from app.tools.registry import get_tool_registry
        registry = get_tool_registry()
        for name in ("get_rule_suggestion", "get_strategy_evidence"):
            tool = registry.get_tool(name)
            self.assertIsNotNone(tool, f"{name} 未注册")
            self.assertEqual(tool.risk_level, "read", f"{name} 应为只读")


class TestDecisionTracing(unittest.TestCase):
    """决策留痕：_record_autonomous_analysis 写入依据引用与快照"""

    def setUp(self):
        from app.models.strategy import Strategy
        self.db = make_db()
        self.db.add(Strategy(
            id=1, name="自动策略1", strategy_type="auto",
            strategy_source="auto_generated", auto_strategy_status="running",
            status="active", allocation_config={"510300": 1.0},
            rebalance_freq="daily", initial_capital=1000000.0,
        ))
        self.db.commit()
        seed_indicator(self.db, "510300", score=70.0)

    def tearDown(self):
        self.db.close()

    def test_evidence_written_with_sources_and_snapshot(self):
        from app.agent_core.loop import AgentResponse
        from app.models.auto_strategy_log import AutoStrategyLog
        from app.tasks.scheduler import _record_autonomous_analysis

        result = AgentResponse(content="依据充分，维持持仓", tool_calls_made=[
            {"tool": "get_market_position_signal", "arguments": {}, "result": {}},
            {"tool": "get_rule_suggestion", "arguments": {"strategy_id": 1}, "result": {}},
            {"tool": "get_pipeline_status", "arguments": {}, "result": {}},
        ])
        _record_autonomous_analysis(result, self.db)

        log = self.db.query(AutoStrategyLog).filter_by(strategy_id=1, action_type="analyzed").first()
        evidence = log.analysis_result["evidence"]
        self.assertEqual(set(evidence["sources_cited"]), {"market", "rules"})
        self.assertEqual(evidence["cited_tools"]["rules"], ["get_rule_suggestion"])
        self.assertNotIn("get_pipeline_status", str(evidence["cited_tools"]))
        self.assertIn("rules", evidence["snapshot"])
        self.assertEqual(evidence["snapshot"]["market"]["as_of"], DAY.isoformat())


if __name__ == "__main__":
    unittest.main()