"""4.2 因子表现 / 板块影响分析工具：返回结构、统计口径与降级分支"""

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
# create_all 只建「已导入模型」的表，故先导入全部模型（与 init_db 口径一致）
from app.models import (  # noqa: F401
    allocation_suggestion, auto_strategy_log, chat, etf, experience, factor_performance,
    market_regime, pipeline_checkpoint, portfolio, sentiment, stock_fundamental, strategy,
    sw_industry, system_config, task_log,
)
from app.models.factor_performance import FactorPerformance
from app.models.etf import ETFBasic
from app.models.sw_industry import SwIndustryDaily
from app.tools.analysis_tools import (
    _ic_reliability, _ic_stats, get_factor_performance, get_sector_impact_analysis,
)

TODAY = date.today()
CODES = [f"51{i:04d}" for i in range(12)]   # 12 只，满足每因子截面 ≥10 样本


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_ic(db, days: int = 3):
    """造三种 ICIR 分支：

    - momentum 与未来收益完全同序 → 每日 IC=+1、跨日无波动（ICIR 无定义）
    - volume  在奇偶日完全反序     → IC 在 ±1 间摆动，均值 0（噪声）
    - trend   截面内大量并列        → IC 恒定但不足 1
    - capital_flow 因子值全相同     → 秩相关无意义，该因子当日不出 IC
    """
    for d in range(days):
        trade_date = TODAY - timedelta(days=d)
        for i, code in enumerate(CODES):
            ret = float(i)
            db.add(FactorPerformance(etf_code=code, trade_date=trade_date,
                                     factor_name="momentum",
                                     factor_value=float(i), forward_return_5d=ret))
            db.add(FactorPerformance(etf_code=code, trade_date=trade_date,
                                     factor_name="volume",
                                     factor_value=float(i if d % 2 == 0 else 11 - i),
                                     forward_return_5d=ret))
            db.add(FactorPerformance(etf_code=code, trade_date=trade_date,
                                     factor_name="trend",
                                     factor_value=float(i % 6), forward_return_5d=ret))
            db.add(FactorPerformance(etf_code=code, trade_date=trade_date,
                                     factor_name="capital_flow",
                                     factor_value=5.0, forward_return_5d=ret))
    db.commit()


def seed_sector_strategy(db, alloc=None, pending=None, sid=1):
    from app.models.strategy import Strategy
    for code, name in (("512480", "半导体ETF"), ("512800", "银行ETF"),
                       ("510300", "沪深300ETF")):
        if not db.query(ETFBasic).filter(ETFBasic.etf_code == code).first():
            db.add(ETFBasic(etf_code=code, etf_name=name))
    s = Strategy(id=sid, name=f"板块策略{sid}",
                 allocation_config={"512480": 0.5, "512800": 0.3, "510300": 0.2}
                 if alloc is None else alloc,
                 pending_allocation=pending,
                 strategy_type="auto", strategy_source="auto_generated",
                 auto_strategy_status="running", status="active", initial_capital=1000000.0)
    db.add(s)
    db.commit()
    return s


def seed_sw(db, rows):
    """rows: {行业名: (sw_code, score, signal)}"""
    for name, (sw_code, score, signal) in rows.items():
        db.add(SwIndustryDaily(trade_date=TODAY, sw_code=sw_code, name=name,
                               score=score, signal=signal))
    db.commit()


def sector_settings(mode: str = "semi_hard"):
    st = MagicMock()
    st.sector_rotation_mode = mode
    return st


class TestIcStats(unittest.TestCase):
    """ICIR 的统计口径用解析值锁死（不靠真实 IC 序列反推）"""

    def test_mean_std_icir(self):
        stats = _ic_stats([0.1, 0.3])
        self.assertEqual(stats["obs_days"], 2)
        self.assertAlmostEqual(stats["ic_mean"], 0.2, places=6)
        # ddof=1：sqrt(((0.1-0.2)^2+(0.3-0.2)^2)/1)=0.1414，总体标准差口径会是 0.1
        self.assertAlmostEqual(stats["ic_std"], 0.1414, places=4)
        self.assertAlmostEqual(stats["icir"], 1.4142, places=4)

    def test_negative_mean_keeps_sign(self):
        stats = _ic_stats([-0.1, -0.3])
        self.assertAlmostEqual(stats["icir"], -1.4142, places=4)

    def test_single_observation_has_no_icir(self):
        stats = _ic_stats([0.2])
        self.assertEqual(stats["obs_days"], 1)
        self.assertEqual(stats["ic_mean"], 0.2)
        self.assertIsNone(stats["ic_std"])
        self.assertIsNone(stats["icir"])
        self.assertEqual(_ic_reliability(stats), "样本不足")

    def test_zero_std_icir_undefined(self):
        stats = _ic_stats([0.15, 0.15, 0.15])
        self.assertEqual(stats["ic_std"], 0.0)
        self.assertIsNone(stats["icir"])
        self.assertEqual(_ic_reliability(stats), "IC 无波动")

    def test_empty_series(self):
        self.assertEqual(_ic_stats([]), {"obs_days": 0, "ic_mean": None,
                                         "ic_std": None, "icir": None})

    def test_reliability_bands(self):
        for icir, label in ((0.6, "稳定"), (-0.6, "稳定"), (0.35, "一般"), (0.1, "噪声")):
            stats = {"obs_days": 10, "ic_mean": 0.02, "ic_std": 0.05, "icir": icir}
            self.assertEqual(_ic_reliability(stats), label)


class TestFactorPerformanceTool(unittest.TestCase):

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def test_structure_with_ic_data(self):
        seed_ic(self.db, days=4)
        result = get_factor_performance(self.db)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["weight_source"], "adaptive")
        self.assertEqual(result["window"]["ic_observations"], 4)
        self.assertEqual({f["factor"] for f in result["factors"]},
                         {"momentum", "trend", "volume", "volatility", "capital_flow"})
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0, places=3)
        self.assertEqual(set(result["weight_delta"]), set(result["weights"]))

    def test_ic_branches(self):
        seed_ic(self.db, days=4)
        factors = {f["factor"]: f for f in get_factor_performance(self.db)["factors"]}
        # 每日 IC 恒为 +1：均值 1、标准差 0、ICIR 无定义
        self.assertEqual(factors["momentum"]["obs_days"], 4)
        self.assertAlmostEqual(factors["momentum"]["ic_mean"], 1.0, places=3)
        self.assertIsNone(factors["momentum"]["icir"])
        self.assertEqual(factors["momentum"]["reliability"], "IC 无波动")
        # IC 在 ±1 间摆动：均值 0、ICIR≈0 → 噪声（只看均值会误判为「有正 IC」）
        self.assertAlmostEqual(factors["volume"]["ic_mean"], 0.0, places=3)
        self.assertEqual(factors["volume"]["reliability"], "噪声")
        # 因子值全相同 → 秩相关无意义，该因子没有任何 IC 观测
        self.assertEqual(factors["capital_flow"]["obs_days"], 0)
        self.assertEqual(factors["capital_flow"]["reliability"], "样本不足")
        self.assertIsNone(factors["volatility"]["ic_mean"])

    def test_sorted_by_abs_ic_mean(self):
        seed_ic(self.db, days=4)
        means = [abs(f["ic_mean"] or 0) for f in get_factor_performance(self.db)["factors"]]
        self.assertEqual(means, sorted(means, reverse=True))

    def test_no_ic_data_falls_back_to_default_weights(self):
        result = get_factor_performance(self.db)
        self.assertEqual(result["status"], "insufficient_ic")
        self.assertEqual(result["weight_source"], "default")
        self.assertEqual(result["weights"], result["default_weights"])
        self.assertEqual(result["window"]["ic_observations"], 0)
        self.assertIn("IC 观测不足", result["text"])
        self.assertIn("固定默认", result["text"])
        self.assertTrue(all(f["obs_days"] == 0 for f in result["factors"]))

    def test_windows_clamped(self):
        result = get_factor_performance(self.db, ic_days=0, lookback_days=-5)
        self.assertEqual(result["window"]["ic_days"], 5)
        self.assertEqual(result["window"]["lookback_days"], 5)
        result = get_factor_performance(self.db, ic_days=9999)
        self.assertEqual(result["window"]["ic_days"], 250)


class TestSectorImpactTool(unittest.TestCase):

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _run(self, mode="semi_hard"):
        with patch("app.services.sector_selection_service.get_settings",
                   return_value=sector_settings(mode)):
            return get_sector_impact_analysis(self.db, 1)

    def test_structure_and_headwind(self):
        seed_sector_strategy(self.db)
        seed_sw(self.db, {"电子": ("801080", 80, "overweight"),
                          "银行": ("801780", 20, "underweight")})
        result = self._run()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["as_of"], TODAY.isoformat())
        holdings = {h["etf_code"]: h for h in result["holdings"]}
        self.assertEqual(holdings["512480"]["sector"], "电子")
        self.assertEqual(holdings["512480"]["signal_label"], "超配")
        self.assertFalse(holdings["512480"]["headwind"])
        self.assertEqual(holdings["512800"]["signal_label"], "低配")
        self.assertTrue(holdings["512800"]["headwind"])
        # 宽基不在板块关键词表内 → 未归类，按中性处理
        self.assertIsNone(holdings["510300"]["sector"])
        self.assertEqual(result["unmatched_codes"], ["510300"])
        self.assertEqual(result["headwind_holdings"], ["512800"])
        self.assertEqual(result["headwind_weight"], 0.3)
        self.assertIn("逆风", result["context_text"])

    def test_sector_exposure_and_concentration(self):
        seed_sector_strategy(self.db)
        seed_sw(self.db, {"电子": ("801080", 80, "overweight"),
                          "银行": ("801780", 20, "underweight")})
        result = self._run()
        exposure = {e["sector"]: e for e in result["sector_exposure"]}
        self.assertEqual(exposure["电子"]["weight"], 0.5)
        self.assertEqual(exposure["电子"]["codes"], ["512480"])
        self.assertEqual(result["sector_exposure"][0]["sector"], "电子")   # 按权重降序
        self.assertEqual(result["concentrated_sectors"], ["电子"])
        self.assertEqual(result["sector_concentration_limit"], 0.40)
        self.assertIn("超限", result["text"])

    def test_compliant_when_no_sector_over_limit(self):
        seed_sector_strategy(self.db, alloc={"512480": 0.3, "512800": 0.3, "510300": 0.4})
        seed_sw(self.db, {"电子": ("801080", 80, "overweight"),
                          "银行": ("801780", 20, "underweight")})
        result = self._run()
        self.assertEqual(result["concentrated_sectors"], [])
        self.assertIn("合规", result["text"])

    def test_pending_allocation_is_the_basis(self):
        seed_sector_strategy(self.db, alloc={"512480": 1.0},
                             pending={"512800": 0.6, "510300": 0.4})
        seed_sw(self.db, {"银行": ("801780", 20, "underweight")})
        result = self._run()
        self.assertEqual({h["etf_code"] for h in result["holdings"]}, {"512800", "510300"})
        self.assertEqual(result["headwind_holdings"], ["512800"])

    def test_mode_off_keeps_signals_but_no_headwind(self):
        seed_sector_strategy(self.db)
        seed_sw(self.db, {"电子": ("801080", 80, "overweight"),
                          "银行": ("801780", 20, "underweight")})
        result = self._run(mode="off")
        self.assertEqual(result["mode"], "off")
        self.assertEqual(result["headwind_holdings"], [])
        self.assertTrue(all(not h["headwind"] for h in result["holdings"]))
        self.assertIn("未启用", result["text"])
        # 展示用信号仍在（低配标记与是否参与选型是两件事）
        self.assertEqual({h["etf_code"]: h["signal"] for h in result["holdings"]}["512800"],
                         "underweight")

    def test_no_sw_data_returns_no_sector_data(self):
        seed_sector_strategy(self.db)
        result = self._run()
        self.assertEqual(result["status"], "no_sector_data")
        self.assertIn("申万板块", result["message"])
        self.assertEqual(len(result["holdings"]), 3)
        self.assertTrue(all(h["sector"] is None for h in result["holdings"]))

    def test_missing_strategy(self):
        self.assertEqual(get_sector_impact_analysis(self.db, 99)["error"], "策略 99 不存在")

    def test_empty_allocation(self):
        seed_sector_strategy(self.db, alloc={})
        with patch("app.services.sector_selection_service.get_settings",
                   return_value=sector_settings()):
            self.assertIn("无有效持仓配置", get_sector_impact_analysis(self.db, 1)["error"])


class TestToolRegistration(unittest.TestCase):

    def test_both_tools_registered_as_read(self):
        import app.tools.analysis_tools  # noqa: F401
        from app.tools.registry import _TOOL_REGISTRY
        for name in ("get_factor_performance", "get_sector_impact_analysis"):
            self.assertIn(name, _TOOL_REGISTRY)
            self.assertEqual(_TOOL_REGISTRY[name].risk_level, "read")
        self.assertEqual(_TOOL_REGISTRY["get_sector_impact_analysis"].parameters
                         ["required"], ["strategy_id"])
        self.assertNotIn("strategy_id", _TOOL_REGISTRY["get_factor_performance"]
                         .parameters.get("required") or [])

    def test_factor_tool_runs_via_registry(self):
        db = make_db()
        try:
            seed_ic(db, days=2)
            import app.tools.analysis_tools  # noqa: F401
            from app.tools.registry import get_tool_registry
            out = get_tool_registry().execute("get_factor_performance", {}, db)
            self.assertEqual(out["weight_source"], "adaptive")
            self.assertEqual(len(out["factors"]), 5)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
