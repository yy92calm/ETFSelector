"""裁决官输入去重：只收争议标的，不再全量转发持仓与候选池"""
import unittest
from unittest.mock import MagicMock

from app.agents.rotation_debate.rotation_judge import RotationJudge, _format_etf_line, _mentioned_codes

HOLDING_CODES = ["510300", "512880", "515050"]


def make_item(code: str, score: float, name: str = "测试ETF") -> dict:
    return {
        "etf_code": code, "etf_name": name, "composite_score": score, "rank": int(score),
        "momentum_5d": 1.2, "momentum_20d": 8.1, "trend_strength": 0.62,
        "volatility_20d": 1.4, "vol_ratio": 1.2,
        "sector": {"sector": "白酒", "score": 68, "signal": "超配"},
        "industry_value": {"industry": "食品饮料", "rank": 12, "total": 95},
    }


def make_candidates(count: int) -> list:
    codes = [f"{512000 + i * 7:06d}" for i in range(count)]
    return [make_item(c, 70.0 - i) for i, c in enumerate(codes)]


HOLDINGS = [make_item(c, 55.0 - i, name=f"持仓{i}") for i, c in enumerate(HOLDING_CODES)]


class JudgePromptCase(unittest.TestCase):

    def setUp(self):
        self.judge = RotationJudge()
        self.judge.call_llm = MagicMock(return_value={"decision": "hold", "final_swaps": []})

    def _prompt(self, momentum, stability, holdings, candidates, suggestion_context=""):
        self.judge.analyze(momentum, stability, holdings, candidates,
                           rule_context="规则", suggestion_context=suggestion_context)
        return self.judge.call_llm.call_args.args[0]

    @staticmethod
    def _materials(prompt: str) -> str:
        """只取「决策材料」段：两派原文里出现任意数字是正常的，约束只针对材料"""
        start = prompt.index("## 决策材料")
        return prompt[start:prompt.index("## 规则参考")]


class TestDiffContext(JudgePromptCase):

    def test_unmentioned_candidate_is_omitted(self):
        candidates = make_candidates(15)
        mentioned_code = candidates[0]["etf_code"]
        dropped_code = candidates[-1]["etf_code"]
        momentum = {"proposed_swaps": [{"remove": "510300", "add": mentioned_code}]}

        prompt = self._prompt(momentum, {"stance": "conservative_hold"}, HOLDINGS, candidates)
        materials = self._materials(prompt)

        self.assertIn(mentioned_code, materials)
        self.assertNotIn(dropped_code, materials, "两派未提及的候选不应出现在裁决材料里")

    def test_all_holdings_are_kept_even_without_mention(self):
        """持仓是「有进必有出」的基准，必须全量保留（候选才做争议过滤）"""
        prompt = self._prompt({"stance": "x"}, {"stance": "y"}, HOLDINGS, make_candidates(15))

        for code in HOLDING_CODES:
            self.assertIn(code, prompt)
        self.assertIn("当前持仓（全部）", prompt)

    def test_suggestion_channel_codes_are_kept(self):
        """提案标的必须带指标出现，否则裁决官无法原样采纳"""
        candidates = make_candidates(15)
        proposal_code = candidates[3]["etf_code"]
        candidates[3]["from_suggestion"] = True
        suggestion = f"★ 可执行提案: 510300 → {proposal_code} 权重20%"

        prompt = self._prompt({}, {}, HOLDINGS, candidates, suggestion_context=suggestion)

        self.assertIn(proposal_code, prompt)
        self.assertIn("调仓建议标的", prompt)

    def test_prompt_shrinks_when_debate_is_narrow(self):
        candidates = make_candidates(15)
        all_mentioned = {"swaps": [{"add": c["etf_code"]} for c in candidates]}
        one_mentioned = {"swaps": [{"add": candidates[0]["etf_code"]}]}

        wide = self._prompt(all_mentioned, {}, HOLDINGS, candidates)
        narrow = self._prompt(one_mentioned, {}, HOLDINGS, candidates)

        self.assertLess(len(narrow), len(wide) * 0.6, "只列争议标的应显著缩短输入")

    def test_no_disputed_candidate_explains_itself(self):
        prompt = self._prompt({}, {}, HOLDINGS, make_candidates(15))

        self.assertIn("未进入争议", prompt)

    def test_omitted_count_is_visible(self):
        """省略要可见：否则裁决官以为候选池只有 1 只"""
        candidates = make_candidates(15)
        prompt = self._prompt({"add": candidates[0]["etf_code"]}, {}, HOLDINGS, candidates)

        self.assertIn("另有 14 只未被提及的候选已从材料中省略", prompt)

    def test_line_carries_scores_factors_and_sector(self):
        item = make_item("512880", 72.5, name="中证白酒")
        line = _format_etf_line(item, "候选")

        self.assertIn("512880 中证白酒 [候选] 综合分72.5", line)
        self.assertIn("20日动量+8.10", line)
        self.assertIn("板块:白酒评分68(超配)", line)
        self.assertIn("行业性价比食品饮料12/95", line)

    def test_line_tolerates_missing_fields(self):
        line = _format_etf_line({"etf_code": "510300"}, "持仓")

        self.assertEqual(line, "- 510300 [持仓]")

    def test_dates_are_not_treated_as_codes(self):
        """理由文本里的日期/金额是 6 位以上数字，不能误认成标的"""
        codes = _mentioned_codes("20261010 调仓，成交金额 1000000 元，换入 512010")

        self.assertEqual(codes, {"512010"})

    def test_phantom_code_cannot_enter_materials(self):
        candidates = make_candidates(15)
        momentum = {"reason": "参考 20261010 与 999999 的历史表现"}

        prompt = self._prompt(momentum, {}, HOLDINGS, candidates)
        materials = self._materials(prompt)

        self.assertNotIn("999999", materials)
        self.assertIn("未进入争议", materials)

    def test_judge_result_is_returned_unchanged(self):
        self.judge.call_llm.return_value = {"decision": "rotate", "final_swaps": [{"remove": "510300", "add": "512010"}]}

        out = self.judge.analyze({}, {}, HOLDINGS, make_candidates(3))

        self.assertEqual(out["decision"], "rotate")

    def test_llm_error_still_returns_hold(self):
        self.judge.call_llm.return_value = {"error": "LLM客户端未配置"}

        out = self.judge.analyze({}, {}, HOLDINGS, make_candidates(3))

        self.assertEqual(out["decision"], "hold")
        self.assertEqual(out["final_swaps"], [])


if __name__ == "__main__":
    unittest.main()
