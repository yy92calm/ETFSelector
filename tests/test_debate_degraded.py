"""辩论降级显式标记：结论不是完整 LLM 辩论产物时，计划、建议回执与前端依据都必须可见"""
import unittest
from unittest.mock import MagicMock, patch

from tests.test_allocation_suggestion import DAY, seed_etf, seed_strategy
from tests.test_value_model import make_db
# make_db() 按 Base.metadata 里「已导入的模型」建表，故须在 setUp 之前显式导入建议模型
from app.models.allocation_suggestion import AllocationSuggestion  # noqa: F401

# 持仓 512480(50) vs 候选 159915(90)：差距 40 分，纯量化通道也会决定换仓
HOLD, CAND = "512480", "159915"


class TestRotationDegradationMarker(unittest.TestCase):
    def setUp(self):
        from app.services.rotation_service import RotationService
        self.svc = RotationService()
        self.db = make_db()
        seed_strategy(self.db, pool={HOLD: 1.0})
        seed_etf(self.db, HOLD, "半导体ETF", score=50.0)
        seed_etf(self.db, CAND, "创业板ETF", score=90.0)

    def _evaluate(self, settings=None, orchestrator=None):
        st = MagicMock()
        st.llm_api_key = "sk-test"
        st.suggestion_auto_execute = False
        if settings:
            settings(st)
        patches = [
            # rotation_service 在函数内 `from app.config import get_settings`，patch 目标必须是 app.config
            patch("app.config.get_settings", return_value=st),
            patch("app.services.sector_selection_service.SectorSelectionService.get_mode",
                  return_value="off"),
            patch.object(self.svc, "_build_rule_signal", return_value=None),
            patch.object(self.svc, "_attach_value_signals"),
        ]
        if orchestrator is not None:
            patches.append(patch("app.agents.rotation_debate.orchestrator.RotationDebateOrchestrator",
                                 new=orchestrator))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return self.svc.evaluate_rotation(1, DAY, self.db)

    def test_llm_not_configured_marks_plan_degraded(self):
        plan = self._evaluate(lambda st: setattr(st, "llm_api_key", ""))
        self.assertTrue(plan["debate_degraded"])
        self.assertEqual(plan["degrade_reason"], "llm_not_configured")
        self.assertTrue(plan["debate"]["debate_degraded"])
        # 量化兜底仍能决策（缺口 40 分），不是"降级即维持"
        self.assertEqual(plan["action"], "rotate")

    def test_debate_exception_marks_plan_degraded(self):
        plan = self._evaluate(orchestrator=MagicMock(side_effect=RuntimeError("LLM 503")))
        self.assertTrue(plan["debate_degraded"])
        self.assertTrue(plan["degrade_reason"].startswith("debate_exception"))
        self.assertIn("LLM 503", plan["degrade_reason"])

    def test_upstream_missing_falls_back_to_quant_with_reason(self):
        fake = MagicMock()
        fake.return_value.debate.return_value = {
            "decision": "hold", "final_swaps": [], "debate_degraded": True,
            "degrade_reason": "upstream_missing: 动量派",
        }
        plan = self._evaluate(orchestrator=fake)
        self.assertEqual(plan["degrade_reason"], "upstream_missing: 动量派")
        self.assertTrue(plan["debate_degraded"])
        # 材料残缺时由量化接管，而不是让裁决官在单边材料上出结论
        self.assertEqual(plan["action"], "rotate")

    def test_judge_failed_keeps_hold_but_marked(self):
        fake = MagicMock()
        fake.return_value.debate.return_value = {
            "decision": "hold", "final_swaps": [], "reason": "辩论异常，维持持仓",
            "debate_degraded": True, "degrade_reason": "judge_failed",
        }
        plan = self._evaluate(orchestrator=fake)
        self.assertEqual(plan["action"], "hold")
        self.assertTrue(plan["debate_degraded"])
        self.assertEqual(plan["degrade_reason"], "judge_failed")

    def test_full_debate_carries_no_marker(self):
        fake = MagicMock()
        fake.return_value.debate.return_value = {
            "decision": "rotate",
            "final_swaps": [{"remove": HOLD, "add": CAND, "reason": "动量领先", "weight_suggestion": None}],
            "summary": "换入创业板",
        }
        plan = self._evaluate(orchestrator=fake)
        self.assertFalse(plan["debate_degraded"])
        self.assertIsNone(plan["degrade_reason"])
        self.assertEqual(plan["action"], "rotate")


class TestDegradationVisibleInReceipts(unittest.TestCase):
    """降级结论进入建议回执与决策依据（用户可见的两处留痕）"""

    def setUp(self):
        from app.services.rotation_service import RotationService
        self.svc = RotationService()
        self.db = make_db()
        seed_strategy(self.db, pool={HOLD: 1.0})
        seed_etf(self.db, HOLD, "半导体ETF", score=50.0)
        seed_etf(self.db, CAND, "创业板ETF", score=90.0)
        from app.services.allocation_suggestion_service import get_allocation_suggestion_service
        get_allocation_suggestion_service().create(
            self.db, 1, {HOLD: 0.6, CAND: 0.4}, reason="建议换入强势品种")

    def test_suggestion_receipt_prefixes_degradation(self):
        from app.models.allocation_suggestion import AllocationSuggestion
        st = MagicMock()
        st.llm_api_key = ""
        st.suggestion_auto_execute = False
        with patch("app.config.get_settings", return_value=st), \
             patch("app.services.sector_selection_service.SectorSelectionService.get_mode",
                   return_value="off"), \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch.object(self.svc, "_attach_value_signals"):
            self.svc.evaluate_rotation(1, DAY, self.db)

        row = self.db.query(AllocationSuggestion).first()
        self.assertIn("【未经辩论·降级】", row.decided_note)
        self.assertIn("未经辩论", row.decided_note)

    def test_evidence_exposes_degradation(self):
        from app.models.auto_strategy_log import AutoStrategyLog
        from app.services.strategy_evidence_service import get_strategy_evidence_service
        self.db.add(AutoStrategyLog(
            strategy_id=1, log_date=DAY, status="success", action_type="analyzed",
            analysis_result={"suggested_action": "rebalance", "action_reason": "纯量化裁决",
                             "debate_degraded": True, "degrade_reason": "llm_not_configured"},
        ))
        self.db.commit()
        dec = get_strategy_evidence_service().get_evidence(1, self.db)["decision"]
        self.assertTrue(dec["debate_degraded"])
        self.assertEqual(dec["degrade_reason"], "llm_not_configured")

    def test_normal_debate_leaves_marker_false_in_evidence(self):
        from app.models.auto_strategy_log import AutoStrategyLog
        from app.services.strategy_evidence_service import get_strategy_evidence_service
        self.db.add(AutoStrategyLog(
            strategy_id=1, log_date=DAY, status="success", action_type="analyzed",
            analysis_result={"suggested_action": "hold", "action_reason": "辩论维持"},
        ))
        self.db.commit()
        dec = get_strategy_evidence_service().get_evidence(1, self.db)["decision"]
        self.assertFalse(dec["debate_degraded"])


if __name__ == "__main__":
    unittest.main()
