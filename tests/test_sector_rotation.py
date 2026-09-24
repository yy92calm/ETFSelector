"""申万板块轮动（板块→选型）测试：解析/截止日/评分/映射/分流/降级"""
import unittest
from datetime import date, timedelta

from tests.test_value_model import make_db

DAY = date(2026, 9, 23)


def seed_sector(db, code, name, score=70, signal="overweight", day=DAY, pe=20.0, amount_share=5.0):
    from app.models.sw_industry import SwIndustryDaily
    db.add(SwIndustryDaily(
        trade_date=day, sw_code=code, name=name, close=1000.0, markup=1.2,
        turnover_rate=3.0, pe=pe, pb=2.0, dividend_yield=1.5,
        amount_share=amount_share, float_mcap=10000.0,
        ret60=0.1, rs60=0.05, trend=1.0, vol60=0.2,
        score=score, signal=signal, score_delta=3, live_change_pct=0.8,
    ))
    db.commit()


def seed_etf_name(db, code, name):
    from app.models.etf import ETFBasic
    db.add(ETFBasic(etf_code=code, etf_name=name))
    db.commit()


def seed_indicator(db, code, day=DAY, score=60.0):
    from app.models.etf import ETFDailyIndicator
    db.add(ETFDailyIndicator(etf_code=code, trade_date=day, composite_score=score,
                             rank_in_market=1, momentum_5d=1.0, momentum_20d=2.0,
                             trend_strength=2, volatility_20d=10.0, vol_ratio=1.0,
                             ma5=1.0, ma10=1.0, ma20=1.0))
    db.commit()


def series_from_return(target_ret, days=61):
    """构造 61 日收盘序列，使 60 日收益 = target_ret"""
    start = 100.0
    step = (1 + target_ret) ** (1 / (days - 1))
    return [(DAY - timedelta(days=days - 1 - i), round(start * step ** i, 4)) for i in range(days)]


class TestParsers(unittest.TestCase):
    """申万接口解析与截止日（纯函数）"""

    def test_parse_current(self):
        from app.services.sw_industry_service import parse_current
        payload = {"data": {"results": [
            {"swindexcode": "801010", "swindexname": "农林牧渔", "l3": "2581.53", "l8": "2538.38"},
            {"swindexcode": "801030", "swindexname": "基础化工", "l3": "-", "l8": ""},
        ]}}
        rows = parse_current(payload)
        self.assertEqual(rows[0]["live_change_pct"], -1.67)
        self.assertIsNone(rows[1]["live_change_pct"])

    def test_parse_analysis_tolerates_strings_and_blanks(self):
        from app.services.sw_industry_service import parse_analysis
        payload = {"data": {"results": [
            {"swindexcode": "801080", "swindexname": "电子",
             "bargaindate": "2026-09-23T08:00:00+08:00", "closeindex": "9159.93",
             "markup": "-4.17", "turnoverrate": "9.36", "pe": "79.09", "pb": "6.71",
             "dp": "0.33", "bargainsumrate": "28.08", "negotiablessharesum1": "94775.39"},
            {"swindexcode": "801780", "swindexname": "银行",
             "bargaindate": "2026-09-23T08:00:00+08:00", "closeindex": "", "markup": "-",
             "turnoverrate": "-", "pe": "-", "pb": "-", "dp": "-",
             "bargainsumrate": "-", "negotiablessharesum1": "-"},
            {"swindexcode": "bad", "bargaindate": "", "closeindex": "1"},
        ]}}
        rows = parse_analysis(payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["close"], 9159.93)
        self.assertEqual(rows[0]["amount_share"], 28.08)
        self.assertEqual(rows[0]["dividend_yield"], 0.33)
        self.assertIsNone(rows[1]["pe"])
        self.assertEqual(rows[1]["trade_date"], DAY)

    def test_common_cutoff_falls_back_when_latest_is_partial(self):
        from app.services.sw_industry_service import common_cutoff
        partial = [{"trade_date": DAY} for _ in range(14)]
        complete = [{"trade_date": DAY - timedelta(days=1)} for _ in range(31)]
        self.assertEqual(common_cutoff(partial + complete), DAY - timedelta(days=1))

    def test_common_cutoff_none_when_insufficient(self):
        from app.services.sw_industry_service import common_cutoff
        self.assertIsNone(common_cutoff([{"trade_date": DAY} for _ in range(5)]))


class TestScoring(unittest.TestCase):
    """轮动评分模型：分位口径 / 信号分档 / 数据不足降级"""

    def setUp(self):
        from app.services.sw_industry_service import compute_rotation
        self.compute = compute_rotation
        self.bench = [(d, 100.0) for d, _ in series_from_return(0.0)]

    def test_score_and_signal_buckets(self):
        series = {
            "A": series_from_return(0.30),
            "B": series_from_return(0.10),
            "C": series_from_return(-0.10),
        }
        result = self.compute(series, self.bench, DAY)
        self.assertEqual(result["A"]["score"], 100)
        self.assertEqual(result["A"]["signal"], "overweight")
        self.assertEqual(result["B"]["score"], 73)
        self.assertEqual(result["B"]["signal"], "overweight")
        self.assertEqual(result["C"]["score"], 27)   # 下跌序列 trend=0，≤33 → 低配
        self.assertEqual(result["C"]["signal"], "underweight")
        self.assertAlmostEqual(result["A"]["rs60"], 0.30, places=3)

    def test_short_series_has_no_score(self):
        series = {"A": series_from_return(0.2), "B": series_from_return(0.1)[:10]}
        result = self.compute(series, self.bench, DAY)
        self.assertIsNone(result["B"]["score"])
        self.assertIsNone(result["B"]["signal"])

    def test_trend_reflects_ma20(self):
        rising = series_from_return(0.1)
        falling = [(d, c) for d, c in reversed(rising)]  # 反向序列（最后一日最低）
        falling = sorted([(DAY - timedelta(days=60 - i), 100 - i * 0.1) for i in range(61)])
        result = self.compute({"A": rising, "B": falling}, self.bench, DAY)
        self.assertEqual(result["A"]["trend"], 1.0)
        self.assertEqual(result["B"]["trend"], 0.0)


class TestSectorMapping(unittest.TestCase):
    """ETF 名称 → 申万行业（长关键词优先）"""

    def test_longest_keyword_wins(self):
        from app.services.sw_industry_service import match_sector
        self.assertEqual(match_sector("华夏新能源车ETF"), "汽车")
        self.assertEqual(match_sector("半导体ETF"), "电子")
        self.assertEqual(match_sector("有色金属ETF"), "有色金属")
        self.assertEqual(match_sector("银行ETF"), "银行")
        self.assertIsNone(match_sector("沪深300ETF"))
        self.assertIsNone(match_sector(""))
        # 真实案例：「港股通+信息技术」跨界子串不应误判为通信行业（长词「信息技术」优先 → 计算机）
        self.assertEqual(match_sector("易方达中证港股通信息技术综合ETF"), "计算机")
        self.assertEqual(match_sector("信息技术ETF"), "计算机")
        self.assertEqual(match_sector("通信ETF"), "通信")
        self.assertEqual(match_sector("港股通医药ETF"), "医药生物")


class TestSectorSignals(unittest.TestCase):
    """ETF 板块信号反查（只读 DB）"""

    def setUp(self):
        self.db = make_db()
        seed_sector(self.db, "801080", "电子", score=82, signal="overweight", amount_share=28.1)
        seed_sector(self.db, "801780", "银行", score=25, signal="underweight", pe=7.0)
        seed_etf_name(self.db, "512480", "半导体ETF")
        seed_etf_name(self.db, "512800", "银行ETF")
        seed_etf_name(self.db, "510300", "沪深300ETF")

    def test_signals_and_unmatched(self):
        from app.services.sw_industry_service import get_etf_sector_signals
        signals = get_etf_sector_signals(self.db, {
            "512480": "半导体ETF", "512800": "银行ETF", "510300": "沪深300ETF",
        })
        self.assertEqual(signals["512480"]["sector"], "电子")
        self.assertEqual(signals["512480"]["signal"], "overweight")
        self.assertEqual(signals["512800"]["signal"], "underweight")
        self.assertNotIn("510300", signals)

    def test_empty_table_degrades(self):
        from app.services.sw_industry_service import get_etf_sector_signals
        empty = make_db()
        self.assertEqual(get_etf_sector_signals(empty, {"512480": "半导体ETF"}), {})


class TestSectorSelection(unittest.TestCase):
    """半硬分流：低配候选降级、低配持仓逆风、关闭开关"""

    def setUp(self):
        from app.services.sector_selection_service import SectorSelectionService
        self.svc = SectorSelectionService()

    def test_split_downgrades_and_flags(self):
        holdings = [{"etf_code": "512800", "sector": {"signal": "underweight"}},
                    {"etf_code": "512480", "sector": {"signal": "overweight"}}]
        candidates = [{"etf_code": "512000", "sector": {"signal": "underweight"}},
                      {"etf_code": "159915", "sector": {"signal": "overweight"}}]
        hs, cs, meta = self.svc.split(holdings, candidates, "semi_hard")
        self.assertTrue(holdings[0]["sector_headwind"])
        self.assertEqual([c["etf_code"] for c in cs], ["159915", "512000"])
        self.assertEqual(meta["downgraded"], ["512000"])
        self.assertEqual(meta["headwind_holdings"], ["512800"])
        self.assertEqual(meta["overweight"], 1)

    def test_mode_off_passthrough(self):
        holdings = [{"etf_code": "512800", "sector": {"signal": "underweight"}}]
        candidates = [{"etf_code": "512000", "sector": {"signal": "underweight"}}]
        hs, cs, meta = self.svc.split(holdings, candidates, "off")
        self.assertNotIn("sector_headwind", holdings[0])
        self.assertEqual([c["etf_code"] for c in cs], ["512000"])
        self.assertEqual(meta["mode"], "off")

    def test_ordering_key_puts_headwind_first(self):
        holdings = [
            {"etf_code": "A", "composite_score": 80},
            {"etf_code": "B", "composite_score": 40, "sector_headwind": True},
            {"etf_code": "C", "composite_score": 60},
        ]
        ordered = self.svc.ordering_key(holdings)
        self.assertEqual([h["etf_code"] for h in ordered], ["B", "C", "A"])

    def test_format_context_mentions_headwind(self):
        from app.services.sector_selection_service import format_sector_context
        holdings = [{"etf_code": "512800", "etf_name": "银行ETF", "sector_headwind": True,
                     "sector": {"sector": "银行", "signal": "underweight", "score": 25,
                                "rank": 31, "total": 31, "pe": 7.0}}]
        candidates = [{"etf_code": "512480", "etf_name": "半导体ETF",
                       "sector": {"sector": "电子", "signal": "overweight", "score": 82,
                                  "rank": 2, "total": 31, "pe": 79.1}}]
        text = format_sector_context(holdings, candidates, {"mode": "semi_hard",
                                                           "headwind_holdings": ["512800"]})
        self.assertIn("板块逆风", text)
        self.assertIn("超配", text)
        self.assertIn("申万一级行业", text)


class TestRotationWithSectorLayer(unittest.TestCase):
    """轮动评估接入板块层：低配候选降级、计划携带板块上下文"""

    def setUp(self):
        from app.services.rotation_service import RotationService
        self.svc = RotationService()
        self.db = make_db()
        from app.models.strategy import Strategy
        self.s = Strategy(name="板块策略", allocation_config={"512800": 1.0},
                          strategy_type="auto", status="active")
        self.db.add(self.s)
        self.db.commit()
        seed_indicator(self.db, "512800", score=50)     # 持仓：银行（低配板块）
        seed_indicator(self.db, "512000", score=90)     # 候选：证券（低配板块）领先40分，原首选
        seed_indicator(self.db, "159915", score=70)     # 候选：电力设备（超配板块）领先20分
        seed_etf_name(self.db, "512800", "银行ETF")
        seed_etf_name(self.db, "512000", "证券ETF")
        seed_etf_name(self.db, "159915", "新能源ETF")
        seed_sector(self.db, "801780", "银行", score=25, signal="underweight")
        seed_sector(self.db, "801790", "非银金融", score=30, signal="underweight")
        seed_sector(self.db, "801730", "电力设备", score=80, signal="overweight")

    def test_low_sector_candidate_downgraded_and_context_returned(self):
        captured = {}

        def fake_debate(holdings, candidates, rule_signal=None, sector_context=""):
            captured["candidates"] = [c["etf_code"] for c in candidates]
            captured["context"] = sector_context
            return {"decision": "hold", "final_swaps": [], "summary": "维持持仓"}

        from unittest.mock import patch
        with patch.object(self.svc, "_run_debate", side_effect=fake_debate), \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch.object(self.svc, "_attach_value_signals"):
            plan = self.svc.evaluate_rotation(self.s.id, DAY, self.db)

        # 低配板块候选（512000）被降级到超配候选（159915）之后
        self.assertEqual(captured["candidates"][0], "159915")
        self.assertIn("板块逆风", captured["context"])
        self.assertEqual(plan["sector_meta"]["downgraded"], ["512000"])
        self.assertEqual(plan["sector_meta"]["headwind_holdings"], ["512800"])

    def test_mode_off_keeps_original_order(self):
        from unittest.mock import patch
        captured = {}

        def fake_debate(holdings, candidates, rule_signal=None, sector_context=""):
            captured["candidates"] = [c["etf_code"] for c in candidates]
            captured["context"] = sector_context
            return {"decision": "hold", "final_swaps": [], "summary": "维持"}

        with patch("app.services.sector_selection_service.SectorSelectionService.get_mode",
                   return_value="off"), \
             patch.object(self.svc, "_run_debate", side_effect=fake_debate), \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch.object(self.svc, "_attach_value_signals"):
            plan = self.svc.evaluate_rotation(self.s.id, DAY, self.db)

        self.assertEqual(captured["candidates"][0], "512000")
        self.assertEqual(captured["context"], "")
        self.assertEqual(plan["sector_meta"]["mode"], "off")

    def test_sector_failure_degrades_to_momentum(self):
        from unittest.mock import patch
        with patch("app.services.sw_industry_service.get_etf_sector_signals",
                   side_effect=RuntimeError("boom")), \
             patch.object(self.svc, "_run_debate",
                          return_value={"decision": "hold", "final_swaps": [], "summary": "维持"}), \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch.object(self.svc, "_attach_value_signals"):
            plan = self.svc.evaluate_rotation(self.s.id, DAY, self.db)
        self.assertEqual(plan["action"], "hold")
        self.assertEqual(plan["sector_meta"]["mode"], "off")


class TestSectorEvidenceAndTools(unittest.TestCase):
    """依据层板块段与工具注册"""

    def test_evidence_sector_section(self):
        from app.services.strategy_evidence_service import StrategyEvidenceService
        from app.models.strategy import Strategy
        db = make_db()
        s = Strategy(name="板块依据", allocation_config={"512480": 1.0},
                     strategy_type="auto", status="active")
        db.add(s)
        db.commit()
        seed_etf_name(db, "512480", "半导体ETF")
        seed_indicator(db, "512480", score=70)
        seed_sector(db, "801080", "电子", score=82, signal="overweight", amount_share=28.1)

        ev = StrategyEvidenceService().get_evidence(s.id, db)["sector"]
        self.assertEqual(ev["mode"], "semi_hard")
        self.assertEqual(ev["items"][0]["sector"], "电子")
        self.assertEqual(ev["items"][0]["signal"], "overweight")
        self.assertEqual(ev["top_sectors"][0]["name"], "电子")

        snap = StrategyEvidenceService().get_snapshot(s.id, db)
        self.assertIn("sector", snap)
        self.assertEqual(snap["sector"]["items"][0]["sector"], "电子")

    def test_tool_registered_readonly(self):
        from app.tools.registry import get_tool_registry
        tool = get_tool_registry().get_tool("get_sector_rotation")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.risk_level, "read")


if __name__ == "__main__":
    unittest.main()