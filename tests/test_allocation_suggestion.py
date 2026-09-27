"""调仓建议测试：LLM 只建议 → 轮动通道唯一裁决（含提级评估与结算回执）"""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from tests.test_value_model import make_db

DAY = date(2026, 9, 24)


def seed_strategy(db, sid=1, pool=None):
    from app.models.strategy import Strategy
    s = Strategy(id=sid, name=f"建议策略{sid}", allocation_config=pool or {"512480": 1.0},
                 strategy_type="auto", strategy_source="auto_generated",
                 auto_strategy_status="running", status="active", initial_capital=1000000.0)
    db.add(s)
    db.commit()
    return s


def seed_etf(db, code, name="测试ETF", score=60.0, day=DAY):
    from app.models.etf import ETFBasic, ETFDailyIndicator
    db.add(ETFBasic(etf_code=code, etf_name=name))
    db.add(ETFDailyIndicator(etf_code=code, trade_date=day, composite_score=score,
                             rank_in_market=1, momentum_5d=1.0, momentum_20d=2.0,
                             trend_strength=2, volatility_20d=10.0, vol_ratio=1.0,
                             ma5=1.0, ma10=1.0, ma20=1.0))
    db.commit()


class TestSuggestionService(unittest.TestCase):
    """建议服务：校验 / 上限 / 过期 / 结算"""

    def setUp(self):
        from app.services.allocation_suggestion_service import AllocationSuggestionService
        self.svc = AllocationSuggestionService()
        self.db = make_db()
        seed_strategy(self.db)
        seed_etf(self.db, "512480", "半导体ETF")
        seed_etf(self.db, "511800", "货币ETF")

    def test_create_valid(self):
        r = self.svc.create(self.db, 1, {"512480": 0.6, "511800": 0.4}, reason="板块超配", source="agentloop")
        self.assertNotIn("error", r)
        self.assertEqual(r["status"], "pending")
        self.assertIn("建议已记录", r["message"])
        self.assertIn("swaps", r["message"])          # 提示可用显式提案精确执行
        self.assertEqual(len(self.svc.get_pending(self.db, 1)), 1)

    def test_rejects_bad_sum_and_unknown_code(self):
        self.assertIn("总和", self.svc.create(self.db, 1, {"512480": 0.5})["error"])
        self.assertIn("ETF 列表", self.svc.create(self.db, 1, {"999999": 1.0})["error"])

    def test_overflow_settles_oldest(self):
        from app.models.allocation_suggestion import AllocationSuggestion
        for i in range(5):
            self.svc.create(self.db, 1, {"512480": 1.0}, reason=f"第{i}次")
        pending = self.db.query(AllocationSuggestion).filter_by(status="pending").all()
        rejected = self.db.query(AllocationSuggestion).filter_by(status="rejected").all()
        self.assertEqual(len(pending), 3)
        self.assertEqual(len(rejected), 2)
        self.assertIn("超上限", rejected[0].decided_note)

    def test_expire_stale(self):
        from app.models.allocation_suggestion import AllocationSuggestion
        row = AllocationSuggestion(strategy_id=1, source="agentloop",
                                   suggested_allocation={"512480": 1.0}, reason="旧建议",
                                   status="pending", created_at=datetime.utcnow() - timedelta(days=8))
        self.db.add(row)
        self.db.commit()
        self.assertEqual(self.svc.expire_stale(self.db, 1), 1)
        self.db.refresh(row)
        self.assertEqual(row.status, "rejected")
        self.assertIn("超期", row.decided_note)

    def test_settle(self):
        self.svc.create(self.db, 1, {"512480": 1.0})
        self.assertEqual(self.svc.settle(self.db, 1, "adopted", "轮动采纳：512480→511800"), 1)
        rows = self.db.query(__import__("app.models.allocation_suggestion", fromlist=["AllocationSuggestion"]).AllocationSuggestion).all()
        self.assertEqual(rows[0].status, "adopted")
        self.assertIn("轮动采纳", rows[0].decided_note)


class TestToolsOnlySuggest(unittest.TestCase):
    """工具层：只写建议，绝不改配置；旧写工具已下线"""

    def setUp(self):
        self.db = make_db()
        seed_strategy(self.db, pool={"512480": 1.0})
        seed_etf(self.db, "512480", "半导体ETF")
        seed_etf(self.db, "511800", "货币ETF")

    def test_suggest_does_not_touch_config(self):
        from app.models.strategy import Strategy
        from app.tools.registry import get_tool_registry
        reg = get_tool_registry()
        r = reg.execute("suggest_allocation_change",
                        {"strategy_id": 1, "new_allocation": {"511800": 0.5, "512480": 0.5},
                         "reason": "货币+半导体"}, self.db)
        self.assertNotIn("error", r)
        s = self.db.query(Strategy).filter(Strategy.id == 1).first()
        self.assertEqual(s.allocation_config, {"512480": 1.0})     # 配置未变
        self.assertIsNone(s.pending_allocation)                     # 待生效未写入

    def test_old_write_tools_removed(self):
        from app.tools.registry import get_tool_registry
        names = get_tool_registry().get_tool_names()
        for gone in ("update_allocation", "add_etf_to_strategy", "remove_etf_from_strategy"):
            self.assertNotIn(gone, names)
        for present in ("suggest_allocation_change", "get_allocation_suggestions"):
            self.assertIn(present, names)

    def test_suggest_tool_is_read_risk(self):
        """建议工具不改资金，声明为只读（对话不弹审批）"""
        from app.tools.registry import get_tool_registry
        tool = get_tool_registry().get_tool("suggest_allocation_change")
        self.assertEqual(tool.risk_level, "read")

    def test_get_suggestions_tool(self):
        from app.tools.registry import get_tool_registry
        reg = get_tool_registry()
        reg.execute("suggest_allocation_change",
                    {"strategy_id": 1, "new_allocation": {"512480": 1.0}, "reason": "维持"}, self.db)
        r = reg.execute("get_allocation_suggestions", {"strategy_id": 1}, self.db)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["suggestions"][0]["status"], "pending")


class TestRotationConsumesSuggestion(unittest.TestCase):
    """轮动通道：建议提级评估、纳入候选、材料注入、结算回执"""

    def setUp(self):
        from app.services.rotation_service import RotationService
        self.svc = RotationService()
        self.db = make_db()
        seed_strategy(self.db, pool={"512480": 1.0})
        seed_etf(self.db, "512480", "半导体ETF", score=70.0)   # 持仓
        seed_etf(self.db, "159915", "创业板ETF", score=72.0)   # 动量候选，仅领先2分（常规门槛不触发）
        seed_etf(self.db, "511800", "货币ETF", score=55.0)     # 建议标的不在候选Top
        # 已有持仓历史（否则配置变更会立即生效而非待生效）
        from app.models.portfolio import PortfolioSnapshot
        self.db.add(PortfolioSnapshot(strategy_id=1, trade_date=DAY, total_asset=1000000.0,
                                      cash=0.0, market_value=1000000.0, profit=0.0, profit_pct=0.0))
        self.db.commit()

    def _suggest(self, alloc=None, reason="建议降风险"):
        from app.services.allocation_suggestion_service import get_allocation_suggestion_service
        return get_allocation_suggestion_service().create(
            self.db, 1, alloc or {"512480": 0.7, "511800": 0.3}, reason=reason)

    def test_suggestion_triggers_debate_despite_small_gap(self):
        captured = {}

        def fake_debate(holdings, candidates, rule_signal=None, sector_context="",
                        sentiment_context="", suggestion_context=""):
            captured["candidates"] = [c["etf_code"] for c in candidates]
            captured["suggestion_ctx"] = suggestion_context
            return {"decision": "hold", "final_swaps": [], "summary": "维持持仓（建议不予采纳）"}

        self._suggest()
        with patch.object(self.svc, "_run_debate", side_effect=fake_debate), \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch.object(self.svc, "_attach_value_signals"), \
             patch("app.services.sector_selection_service.SectorSelectionService.get_mode", return_value="off"):
            plan = self.svc.evaluate_rotation(1, DAY, self.db)

        # 常规门槛（2分<5分）本不该辩论；有建议 → 提级进入辩论
        self.assertIn("suggestion_ctx", captured)
        self.assertIn("511800", captured["suggestion_ctx"])          # 建议内容进入材料
        self.assertIn("511800", captured["candidates"])              # 建议标的补入候选
        self.assertEqual(plan["action"], "hold")

    def test_rejected_suggestion_gets_receipt(self):
        from app.models.allocation_suggestion import AllocationSuggestion
        self._suggest()
        with patch.object(self.svc, "_run_debate",
                          return_value={"decision": "hold", "final_swaps": [], "summary": "板块与规则不支持"}):
            self.svc.evaluate_rotation(1, DAY, self.db)
        row = self.db.query(AllocationSuggestion).first()
        self.assertEqual(row.status, "rejected")
        self.assertIn("辩论驳回", row.decided_note)

    def test_adopted_suggestion_marked_and_staged(self):
        from app.models.allocation_suggestion import AllocationSuggestion
        self._suggest({"512480": 0.6, "511800": 0.4})
        with patch.object(self.svc, "_run_debate", return_value={
                "decision": "rotate",
                "final_swaps": [{"remove": "512480", "add": "511800",
                                 "reason": "防御切换", "weight_suggestion": 0.4}],
                "summary": "采纳建议，换入货币ETF"}):
            plan = self.svc.evaluate_rotation(1, DAY, self.db)

        self.assertEqual(plan["action"], "rotate")
        result = self.svc.execute_rotation(1, plan, self.db)
        self.assertEqual(result["status"], "ok")
        from app.models.strategy import Strategy
        s = self.db.query(Strategy).filter(Strategy.id == 1).first()
        self.assertIsNotNone(s.pending_allocation)                   # 待生效已写入（唯一换仓通道）
        row = self.db.query(AllocationSuggestion).first()
        self.assertEqual(row.status, "adopted")
        self.assertIn("轮动通道采纳", row.decided_note)

    def test_no_suggestion_keeps_original_gate(self):
        """无建议时保持原门槛逻辑（2分差距不辩论）"""
        with patch.object(self.svc, "_run_debate") as mock_debate, \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch("app.services.sector_selection_service.SectorSelectionService.get_mode", return_value="off"):
            plan = self.svc.evaluate_rotation(1, DAY, self.db)
        mock_debate.assert_not_called()
        self.assertIn("差距不足", plan["reason"])


class TestFallbackPipelineOnlySuggests(unittest.TestCase):
    """降级管道（未使用 LLM）：只生成建议并标注，不写配置"""

    def setUp(self):
        self.db = make_db()
        seed_strategy(self.db, pool={"512480": 1.0})
        seed_etf(self.db, "512480", "半导体ETF")
        seed_etf(self.db, "511800", "货币ETF")

    def test_fallback_writes_suggestion_not_config(self):
        from app.services.auto_strategy_executor import AutoStrategyExecutor
        from app.models.strategy import Strategy
        from app.models.allocation_suggestion import AllocationSuggestion

        executor = AutoStrategyExecutor()
        # 单标的最大变化 8% < SAFETY_LIMITS.max_allocation_change(10%)，确保走到阶段7
        analysis = {"suggested_allocation": {"512480": 0.92, "511800": 0.08},
                    "action_reason": "降低半导体暴露", "suggested_action": "rebalance",
                    "risk_alert": {"level": "low"}, "key_signals_summary": []}

        executor._run_risk_checks = lambda sid, d: {"stage": {"stage": "risk", "status": "passed"}, "status": "passed"}
        executor._run_analysis = lambda sid, day, d, skip: {"stage": {"stage": "analysis", "status": "passed"},
                                                            "status": "passed", "analysis": analysis}
        executor._validate_etf_codes = lambda alloc, d: {"passed": True,
                                                          "stage": {"stage": "validate_etf", "status": "passed"}}
        executor._log_execution = lambda *a, **k: None
        executor._record_experience_usage = lambda *a, **k: None

        pipeline = executor.execute_full_pipeline(1, DAY, self.db)

        self.assertEqual(pipeline["status"], "suggested")
        self.assertIn("未使用 LLM", pipeline["overall_message"])
        strategy = self.db.query(Strategy).filter(Strategy.id == 1).first()
        self.assertEqual(strategy.allocation_config, {"512480": 1.0})    # 配置未变
        self.assertIsNone(strategy.pending_allocation)                   # 待生效未写
        rows = self.db.query(AllocationSuggestion).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "fallback")
        self.assertIn("未使用 LLM", rows[0].reason)


class TestExecutableSuggestion(unittest.TestCase):
    """建议带显式替换提案 → 通道校验硬约束后可直接执行"""

    def setUp(self):
        from app.services.rotation_service import RotationService
        self.svc = RotationService()
        self.db = make_db()
        seed_strategy(self.db, pool={"512480": 0.35, "159915": 0.35, "562530": 0.30})
        seed_etf(self.db, "512480", "半导体ETF", score=70.0)      # 在持仓（可换出）
        seed_etf(self.db, "159915", "创业板ETF", score=72.0)      # 在持仓
        seed_etf(self.db, "562530", "中证1000价值ETF", score=68.0) # 在持仓
        seed_etf(self.db, "511800", "货币ETF", score=55.0)        # 拟换入（不在持仓）
        seed_etf(self.db, "159053", "稀有金属ETF", score=66.0)    # 备用换入
        from app.models.portfolio import PortfolioSnapshot
        self.db.add(PortfolioSnapshot(strategy_id=1, trade_date=DAY, total_asset=1000000.0,
                                      cash=0.0, market_value=1000000.0, profit=0.0, profit_pct=0.0))
        self.db.commit()
        self.sug_svc = __import__("app.services.allocation_suggestion_service",
                                  fromlist=["get_allocation_suggestion_service"]).get_allocation_suggestion_service()

    def test_fast_path_executes_and_settles(self):
        from app.models.strategy import Strategy
        from app.models.allocation_suggestion import AllocationSuggestion
        self.sug_svc.create(self.db, 1, {"159915": 0.35, "562530": 0.30, "511800": 0.35},
                            reason="防御切换",
                            swaps=[{"remove": "512480", "add": "511800", "weight": 0.35}])
        with patch.object(self.svc, "_run_debate") as mock_debate, \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch.object(self.svc, "_attach_value_signals"), \
             patch("app.services.sector_selection_service.SectorSelectionService.get_mode", return_value="off"):
            plan = self.svc.evaluate_rotation(1, DAY, self.db)

        mock_debate.assert_not_called()                      # 快路径不消耗辩论
        self.assertEqual(plan["action"], "rotate")
        self.assertIn("建议驱动执行", plan["reason"])
        self.assertEqual(plan["rotations"][0]["weight_suggestion"], 0.35)
        self.assertEqual(plan["suggestion_execution"]["executable"][0]["add"], "511800")

        result = self.svc.execute_rotation(1, plan, self.db)
        self.assertEqual(result["status"], "ok")
        s = self.db.query(Strategy).filter(Strategy.id == 1).first()
        self.assertIsNotNone(s.pending_allocation)
        # 权重与换出标的一致 → 精确落地；若建议权重小于换出权重，差额会按比例归一化分摊
        self.assertAlmostEqual(s.pending_allocation["511800"], 0.35, places=3)
        self.assertAlmostEqual(sum(s.pending_allocation.values()), 1.0, places=3)
        row = self.db.query(AllocationSuggestion).first()
        self.assertEqual(row.status, "adopted")
        self.assertIn("采纳并已落地", row.decided_note)

    def test_auto_execute_off_goes_to_debate(self):
        from app.config import get_settings
        settings = get_settings()
        old = settings.suggestion_auto_execute
        settings.suggestion_auto_execute = False
        try:
            captured = {}

            def fake_debate(holdings, candidates, rule_signal=None, sector_context="",
                            sentiment_context="", suggestion_context=""):
                captured["ctx"] = suggestion_context
                return {"decision": "hold", "final_swaps": [], "summary": "维持"}

            self.sug_svc.create(self.db, 1, {"159915": 0.35, "562530": 0.30, "511800": 0.35},
                                reason="防御切换",
                                swaps=[{"remove": "512480", "add": "511800", "weight": 0.35}])
            with patch.object(self.svc, "_run_debate", side_effect=fake_debate), \
                 patch.object(self.svc, "_build_rule_signal", return_value=None), \
                 patch.object(self.svc, "_attach_value_signals"), \
                 patch("app.services.sector_selection_service.SectorSelectionService.get_mode", return_value="off"):
                plan = self.svc.evaluate_rotation(1, DAY, self.db)
            self.assertIn("★ 可执行提案", captured["ctx"])
            self.assertIn("512480→511800", captured["ctx"])
            self.assertEqual(plan["action"], "hold")
        finally:
            settings.suggestion_auto_execute = old

    def test_hard_constraint_rejections(self):
        """逐条硬约束：不在基准/已在组合/禁入/无指标/权重越界/超持仓上限"""
        base = {"512480": 0.35, "159915": 0.35, "562530": 0.30}
        score_map = {"512480": {"composite_score": 70}, "159915": {"composite_score": 72},
                     "562530": {"composite_score": 68},
                     "159053": {"composite_score": 66}, "511800": {"composite_score": 55}}
        cases = [
            ({"remove": "999999", "add": "159053"}, "不在组合基准"),
            ({"remove": "512480", "add": "159053"}, None),            # 合法：有指标且不在基准
            ({"remove": "512480", "add": "159915"}, "已在组合内"),
            ({"remove": "512480", "add": "511800", "weight": 0.9}, "建议权重"),
            ({"remove": "512480", "add": "000000"}, "无当日量化指标"),
            ({"remove": "512480", "add": "159053", "weight": 0.4}, None),   # 上限边界允许
        ]
        for swap, expect in cases:
            exec_list, rej = self.svc._collect_proposals(
                base, [type("SG", (), {"id": 1, "strategy_id": 1,
                                        "proposed_swaps": [swap], "reason": "t"})()],
                DAY, self.db, {}, score_map)
            if expect is None:
                self.assertEqual(len(exec_list), 1, swap)
            else:
                self.assertTrue(any(expect in x["reason_note"] for x in rej), f"{swap} -> {rej}")

    def test_banned_add_rejected(self):
        exec_list, rej = self.svc._collect_proposals(
            {"512480": 0.35, "159915": 0.35, "562530": 0.30},
            [type("SG", (), {"id": 1, "strategy_id": 1,
                             "proposed_swaps": [{"remove": "512480", "add": "159053"}],
                             "reason": "t"})()],
            DAY, self.db, {"159053": 5},
            {"512480": {"composite_score": 70}, "159053": {"composite_score": 66}})
        self.assertEqual(exec_list, [])
        self.assertIn("禁入名单", rej[0]["reason_note"])


if __name__ == "__main__":
    unittest.main()