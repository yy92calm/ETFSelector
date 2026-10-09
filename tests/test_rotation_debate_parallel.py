"""轮动辩论：动量派与稳定派并行执行，结果与串行一致"""
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agents.rotation_debate.orchestrator import RotationDebateOrchestrator


HOLDINGS = [{"etf_code": "510300", "composite_score": 60.0}]
CANDIDATES = [{"etf_code": "512880", "composite_score": 72.0}]


class TestRotationDebateParallel(unittest.TestCase):
    def setUp(self):
        self.o = RotationDebateOrchestrator()
        self.o.momentum = MagicMock()
        self.o.stability = MagicMock()
        self.o.judge = MagicMock()

    def test_both_advocates_called_once_with_same_inputs(self):
        self.o.momentum.analyze.return_value = {"stance": "rotate"}
        self.o.stability.analyze.return_value = {"stance": "hold"}
        self.o.judge.analyze.return_value = {"decision": "rotate", "final_swaps": []}

        self.o.debate(HOLDINGS, CANDIDATES, macro_context="牛市")

        self.assertEqual(self.o.momentum.analyze.call_count, 1)
        self.assertEqual(self.o.stability.analyze.call_count, 1)
        self.assertEqual(
            self.o.momentum.analyze.call_args, self.o.stability.analyze.call_args,
            "两派应收到完全相同的辩论材料")

    def test_runs_concurrently(self):
        """两派各 0.3s，并行应明显小于串行 0.6s"""
        slow = MagicMock(side_effect=lambda *a, **k: (time.sleep(0.3), {"stance": "x"})[1])
        self.o.momentum.analyze = slow
        self.o.stability.analyze = slow
        self.o.judge.analyze.return_value = {"decision": "hold", "final_swaps": []}

        start = time.perf_counter()
        self.o.debate(HOLDINGS, CANDIDATES)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.55, f"疑似串行执行，耗时 {elapsed:.2f}s")

    def test_judge_receives_both_opinions(self):
        self.o.momentum.analyze.return_value = {"tag": "momentum"}
        self.o.stability.analyze.return_value = {"tag": "stability"}
        self.o.judge.analyze.return_value = {"decision": "hold", "final_swaps": []}

        self.o.debate(HOLDINGS, CANDIDATES)
        args, _ = self.o.judge.analyze.call_args
        self.assertEqual(args[0]["tag"], "momentum")
        self.assertEqual(args[1]["tag"], "stability")

    def test_one_side_error_still_reaches_judge(self):
        """一方失败不应中断流程，仍要把可用意见交给裁决官"""
        self.o.momentum.analyze.return_value = {"error": "LLM超时"}
        self.o.stability.analyze.return_value = {"proposed_swaps": [], "stance": "hold"}
        self.o.judge.analyze.return_value = {"decision": "hold", "final_swaps": []}

        out = self.o.debate(HOLDINGS, CANDIDATES)
        self.assertEqual(out["decision"], "hold")
        self.o.judge.analyze.assert_called_once()

    def test_judge_error_falls_back_to_hold(self):
        self.o.momentum.analyze.return_value = {"stance": "rotate"}
        self.o.stability.analyze.return_value = {"stance": "hold"}
        self.o.judge.analyze.return_value = {"error": "解析失败"}

        out = self.o.debate(HOLDINGS, CANDIDATES)
        self.assertEqual(out, {"decision": "hold", "final_swaps": [],
                               "reason": "辩论异常，维持持仓"})

    def test_rule_context_still_injected(self):
        self.o.momentum.analyze.return_value = {}
        self.o.stability.analyze.return_value = {}
        self.o.judge.analyze.return_value = {"decision": "hold", "final_swaps": []}

        self.o.debate(HOLDINGS, CANDIDATES, rule_signal={
            "regime_label": "震荡市", "regime": "choppy",
            "suggested_allocation": {"510300": 0.6},
            "rule_source_label": "历史规则",
        })
        materials = self.o.momentum.analyze.call_args[0]
        self.assertIn("震荡市", materials[3])


if __name__ == "__main__":
    unittest.main()
