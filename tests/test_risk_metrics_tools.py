"""风控工具补全：相关度/VaR/换仓 what-if/自定义压力场景（含「算风险不改库」）"""
import unittest
from datetime import date, timedelta

from app.db.database import Base
# create_all 只建「已导入模型」的表
from app.models import (  # noqa: F401
    allocation_suggestion, auto_strategy_log, chat, etf, experience, factor_performance,
    market_regime, pipeline_checkpoint, portfolio, sentiment, stock_fundamental, strategy,
    sw_industry, system_config, task_log,
)
from app.models.etf import ETFBasic, ETFQuotation
from app.models.strategy import Strategy

A, B, C, D, E = "512480", "511800", "159995", "510300", "588000"
N_DAYS = 61
# 5 个亏损日 + 55 个 +0.2%：95% 分位（60 个观测取 index 3）落在第 4 差的 -2%，VaR 有解析解
_LOSS_BY_INDEX = {5: -0.05, 15: -0.04, 25: -0.03, 35: -0.02, 45: -0.01}
RETURNS = [_LOSS_BY_INDEX.get(i, 0.002) for i in range(N_DAYS - 1)]


def make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_quotes(db, code, rets, start_price=1.0):
    """按给定日收益序列造 N_DAYS 个连续日历日的收盘价"""
    db.add(ETFBasic(etf_code=code, etf_name=f"{code}测试"))
    price = start_price
    day0 = date.today() - timedelta(days=N_DAYS - 1)
    for i in range(N_DAYS):
        if i > 0:
            price = round(price * (1 + rets[i - 1]), 8)
        db.add(ETFQuotation(etf_code=code, trade_date=day0 + timedelta(days=i),
                            open_price=price, close_price=price, high_price=price,
                            low_price=price, volume=1000, amount=1000, change_pct=0.0))


def seed_strategy(db, alloc, sid=1):
    db.add(Strategy(id=sid, name=f"风控测试{sid}", strategy_type="auto",
                    allocation_config=alloc, strategy_source="auto_generated",
                    auto_strategy_status="running", status="active",
                    initial_capital=100000.0))
    db.commit()


class TestDiversification(unittest.TestCase):
    """相关度：同涨同跌 vs 真分散"""

    def setUp(self):
        from app.services.risk_controller import RiskController
        self.ctrl = RiskController()
        self.db = make_db()
        seed_quotes(self.db, A, RETURNS)
        seed_quotes(self.db, B, RETURNS)          # 与 A 完全同涨
        seed_quotes(self.db, C, [-r for r in RETURNS])   # 与 A 完全反向
        seed_strategy(self.db, {A: 0.4, B: 0.3, C: 0.3})
        self.addCleanup(self.db.close)

    def test_matrix_and_avg(self):
        r = self.ctrl.get_diversification(1, self.db)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["obs_days"], N_DAYS - 1)
        self.assertAlmostEqual(r["matrix"][A][B], 1.0, places=3, msg="同序列相关度应为1")
        self.assertAlmostEqual(r["matrix"][A][C], -1.0, places=3, msg="反向序列相关度应为-1")
        self.assertAlmostEqual(r["avg_correlation"], -0.333, places=3)
        self.assertEqual(r["highest_pair"]["a"], A)
        self.assertIn("分散良好", r["text"])

    def test_insufficient_window(self):
        r = self.ctrl.get_diversification(1, self.db, window=5)
        self.assertEqual(r["status"], "insufficient_data")
        self.assertNotIn("matrix", r)

    def test_single_holding_not_computed(self):
        seed_strategy(self.db, {A: 1.0}, sid=2)
        r = self.ctrl.get_diversification(2, self.db)
        self.assertEqual(r["status"], "insufficient")
        self.assertIn("不足2只", r["message"])

    def test_missing_quotes_reported(self):
        seed_strategy(self.db, {A: 0.5, D: 0.5}, sid=2)
        r = self.ctrl.get_diversification(2, self.db)
        self.assertIn(D, r["missing_codes"], "无行情的标的应显式列出，不能静默少算")
        self.assertEqual(r["codes"], [A])

    def test_strategy_not_found(self):
        self.assertEqual(self.ctrl.get_diversification(99, self.db)["status"], "error")


class TestValueAtRisk(unittest.TestCase):
    """历史模拟 VaR：分位损失、时间缩放、置信度封顶"""

    def setUp(self):
        from app.services.risk_controller import RiskController
        self.ctrl = RiskController()
        self.db = make_db()
        seed_quotes(self.db, A, RETURNS)
        seed_quotes(self.db, B, RETURNS)
        seed_strategy(self.db, {A: 0.5, B: 0.5})
        self.addCleanup(self.db.close)

    def test_historical_percentile(self):
        r = self.ctrl.get_var(1, self.db, confidence=0.95)
        self.assertEqual(r["status"], "ok")
        # n=60，95% 分位取 index=floor(0.05*60)=3 → 第4差的观测 = -2%
        self.assertEqual(r["obs_days"], 60)
        self.assertAlmostEqual(r["var_pct"], 0.02, places=4)
        self.assertAlmostEqual(r["var_amount"], 0.02 * 100000, places=2)
        self.assertAlmostEqual(r["expected_shortfall_pct"], 0.035, places=4)
        self.assertAlmostEqual(r["var_day"]["return_pct"], -0.02, places=4)

    def test_horizon_scales_by_sqrt(self):
        one = self.ctrl.get_var(1, self.db, horizon=1)
        four = self.ctrl.get_var(1, self.db, horizon=4)
        self.assertAlmostEqual(four["var_pct"], one["var_pct"] * 2, places=4)

    def test_confidence_clamped(self):
        self.assertEqual(self.ctrl.get_var(1, self.db, confidence=0.0)["confidence"], 0.5)
        self.assertEqual(self.ctrl.get_var(1, self.db, confidence=1.5)["confidence"], 0.999)

    def test_uses_pending_allocation(self):
        """待生效配置优先：VaR 应基于「周一会执行的那套」而非现行配置"""
        s = self.db.query(Strategy).filter(Strategy.id == 1).first()
        s.pending_allocation = {A: 1.0}
        self.db.commit()
        self.assertEqual(self.ctrl.get_var(1, self.db)["codes_used"], [A])


class TestWhatIfSwaps(unittest.TestCase):
    """熔断检查的 what-if：算风险，绝不改配置"""

    def setUp(self):
        from app.services.risk_controller import RiskController
        self.ctrl = RiskController()
        self.db = make_db()
        for code in [A, B, C, D, E]:
            seed_quotes(self.db, code, [0.001] * (N_DAYS - 1))
        seed_strategy(self.db, {A: 0.25, B: 0.25, C: 0.25, D: 0.25})
        self.alloc_before = dict(self.db.get(Strategy, 1).allocation_config)
        self.addCleanup(self.db.close)

    def _what_if(self, swaps):
        return self.ctrl.check_circuit_breaker(1, self.db, what_if_swaps=swaps)

    def test_no_what_if_keeps_original_shape(self):
        r = self.ctrl.check_circuit_breaker(1, self.db)
        self.assertNotIn("what_if", r)
        self.assertIn("status", r)

    def test_compliant_swap_not_blocked(self):
        r = self._what_if([{"remove": D, "add": E}])
        w = r["what_if"]
        self.assertFalse(w["blocked"])
        self.assertEqual(w["resulting_allocation"], {A: 0.25, B: 0.25, C: 0.25, E: 0.25})
        self.assertEqual(w["risk_budget"]["status"], "compliant")
        self.assertEqual(w["structure_problems"], [])

    def test_over_weight_swap_blocked(self):
        r = self._what_if([{"remove": D, "add": E, "weight": 0.5}])
        w = r["what_if"]
        self.assertTrue(w["blocked"])
        self.assertEqual(w["risk_budget"]["status"], "violation")
        self.assertIn(E, w["risk_budget"]["violations"][0]["over_codes"])

    def test_weight_sum_problem_reported(self):
        r = self._what_if([{"remove": D, "add": E, "weight": 0.1}])
        self.assertTrue(r["what_if"]["blocked"])
        self.assertIn("权重合计", ";".join(r["what_if"]["structure_problems"]))

    def test_invalid_swap_structure(self):
        r = self._what_if([{"remove": "999999", "add": E}, {"remove": A, "add": B}, "x"])
        problems = r["what_if"]["structure_problems"]
        self.assertTrue(r["what_if"]["blocked"])
        self.assertIn("不在当前组合", ";".join(problems))
        self.assertIn("已在组合", ";".join(problems))

    def test_var_compare_on_same_grid(self):
        r = self._what_if([{"remove": D, "add": E}])
        w = r["what_if"]
        self.assertIsNotNone(w["var_before"])
        self.assertIsNotNone(w["var_after"])
        self.assertAlmostEqual(w["var_delta"], 0.0, places=4)   # 五条序列同形，风险不变
        self.assertIn("尾部风险", w["message"])

    def test_never_writes_allocation(self):
        """核心约束：what-if 只是计算，配置与待生效都不能被风控工具改动"""
        self._what_if([{"remove": D, "add": E, "weight": 0.9}])
        s = self.db.get(Strategy, 1)
        self.assertEqual(s.allocation_config, self.alloc_before)
        self.assertIsNone(s.pending_allocation)


class TestCustomStressScenario(unittest.TestCase):
    """压力测试：自定义跌幅场景，缺省沿用预设"""

    def setUp(self):
        from app.services.risk_controller import RiskController
        self.ctrl = RiskController()
        self.db = make_db()
        seed_quotes(self.db, A, RETURNS)
        seed_strategy(self.db, {A: 1.0})
        self.addCleanup(self.db.close)

    def test_default_presets(self):
        r = self.ctrl.run_stress_test(1, self.db)
        self.assertEqual(len(r["scenarios"]), 4)
        self.assertEqual({s["scenario"] for s in r["scenarios"]},
                         {"市场暴跌", "金融危机", "流动性危机", "政策冲击"})

    def test_custom_scenario_replaces_presets(self):
        r = self.ctrl.run_stress_test(1, self.db, scenario={"shock": 0.15, "name": "小幅回调"})
        self.assertEqual(len(r["scenarios"]), 1)
        one = r["scenarios"][0]
        self.assertEqual(one["scenario"], "小幅回调")
        self.assertLess(one["shock_pct"], 0, "自定义场景应按跌幅（负数）计")

    def test_invalid_shock_rejected(self):
        for bad in [{"shock": 0}, {"shock": 1.5}, {"shock": "abc"}, {}]:
            r = self.ctrl.run_stress_test(1, self.db, scenario=bad)
            self.assertEqual(r["status"], "error", f"{bad} 应被拒绝")
            self.assertIn("message", r)


class TestToolRegistration(unittest.TestCase):
    """新工具进注册表且为只读（what-if 不改状态，不需要审批）"""

    def test_registered_as_read(self):
        from app.tools.registry import get_tool_registry
        reg = get_tool_registry()
        for name in ["get_portfolio_correlation", "get_portfolio_var",
                     "check_circuit_breaker", "run_stress_test"]:
            td = reg.get_tool(name)
            self.assertIsNotNone(td, f"{name} 未注册")
            self.assertEqual(td.risk_level, "read", f"{name} 应为只读")

    def test_execute_through_registry(self):
        from app.tools.registry import get_tool_registry
        db = make_db()
        seed_quotes(db, A, RETURNS)
        seed_strategy(db, {A: 1.0})
        res = get_tool_registry().execute("get_portfolio_var", {"strategy_id": 1}, db)
        self.assertEqual(res["status"], "ok")
        self.assertAlmostEqual(res["var_pct"], 0.02, places=4)
        res2 = get_tool_registry().execute(
            "check_circuit_breaker",
            {"strategy_id": 1, "what_if_swaps": [{"remove": A, "add": B}]}, db)
        self.assertIn("what_if", res2)
        db.close()


if __name__ == "__main__":
    unittest.main()
