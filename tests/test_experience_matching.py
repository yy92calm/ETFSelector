"""经验匹配：加权相似度、放宽的权重曲线、冲突需标签真正重叠"""
import unittest

from app.models.experience import Experience
from app.services.smart_experience_matcher import (
    SmartExperienceMatcher,
    get_smart_experience_matcher,
)


def make_exp(exp_type="success", tags=None, action="持有", title="经验", exp_id=None):
    return Experience(
        id=exp_id,
        strategy_id=1,
        experience_type=exp_type,
        title=title,
        scenario_tags=tags or [],
        action_taken=action,
        weight=1.0,
    )


class TestScenarioSimilarity(unittest.TestCase):

    def setUp(self):
        self.matcher = SmartExperienceMatcher()

    def test_identical_single_tag_is_one(self):
        scenario = {"scenario_tags": ["高波动"]}

        self.assertEqual(
            self.matcher._calculate_scenario_similarity(scenario, ["高波动"]), 1.0)

    def test_no_tags_returns_zero(self):
        self.assertEqual(
            self.matcher._calculate_scenario_similarity({"market_regime": "牛市"}, []), 0.0)
        self.assertEqual(
            self.matcher._calculate_scenario_similarity({}, ["高波动"]), 0.0)

    def test_regime_hit_outranks_key_factor_hit(self):
        """同为命中一个标签，状态维度的匹配度应高于普通因素"""
        scenario = {"market_regime": "牛市", "volatility": "高波动",
                    "scenario_tags": ["政策收紧"]}

        regime = self.matcher._calculate_scenario_similarity(scenario, ["牛市"])
        factor = self.matcher._calculate_scenario_similarity(scenario, ["政策收紧"])

        self.assertGreater(regime, factor)
        self.assertAlmostEqual(regime, 1.5 / 3.7, places=6)
        self.assertAlmostEqual(factor, 1.0 / 3.7, places=6)

    def test_case_and_space_normalized(self):
        self.assertEqual(
            self.matcher._calculate_scenario_similarity({"regime": " Bull "}, ["bull"]), 1.0)

    def test_similarity_never_exceeds_one(self):
        scenario = {"market_regime": "牛市", "volatility": "高波动"}

        similarity = self.matcher._calculate_scenario_similarity(
            scenario, ["牛市", "高波动", "额外标签"])

        self.assertLessEqual(similarity, 1.0)

    def test_weight_curve(self):
        """完全匹配时权重翻倍，failure 再乘 1.5（原 ×1.5 放大 + ×2.0 会让失败经验压过一切）"""
        success = make_exp("success", ["高波动"])
        failure = make_exp("failure", ["高波动"], action="减仓")

        self.assertEqual(self.matcher._adjust_weight_by_scenario(success, 1.0), 2.0)
        self.assertEqual(self.matcher._adjust_weight_by_scenario(failure, 1.0), 3.0)


class TestConflictDetection(unittest.TestCase):

    def setUp(self):
        self.matcher = SmartExperienceMatcher()

    def test_unrelated_experiences_are_not_conflict(self):
        """类型相反但场景无交集：旧口径 0.5+0.2 已达标，属假冲突"""
        failure = make_exp("failure", ["流动性枯竭"], action="清仓", title="A")
        success = make_exp("success", ["新能源政策利好"], action="加仓", title="B")

        score = self.matcher._calculate_conflict_score(failure, success)

        self.assertEqual(score, 0.0)
        self.assertEqual(self.matcher.detect_experience_conflicts([failure, success]), [])

    def test_same_scenario_opposite_conclusion_is_conflict(self):
        failure = make_exp("failure", ["高波动", "下跌趋势"], action="追高买入")
        success = make_exp("success", ["高波动", "下跌趋势"], action="等待企稳")

        conflicts = self.matcher.detect_experience_conflicts([failure, success])

        self.assertEqual(len(conflicts), 1)
        self.assertGreaterEqual(conflicts[0]["conflict_score"],
                                self.matcher.MATCHING_CONFIG["conflict_detection_threshold"])

    def test_overlap_below_gate_is_not_conflict(self):
        """重叠度 1/4 = 0.25 未过门槛 → 即使类型相反、操作不同也不算冲突"""
        a = make_exp("failure", ["高波动", "缩量"], action="买入")
        b = make_exp("success", ["高波动", "放量", "上涨"], action="卖出")

        self.assertAlmostEqual(self.matcher._tag_overlap_ratio({"高波动", "缩量"},
                                                              {"高波动", "放量", "上涨"}), 0.25)
        self.assertEqual(self.matcher._calculate_conflict_score(a, b), 0.0)
        self.assertEqual(self.matcher.detect_experience_conflicts([a, b]), [])

    def test_same_type_pair_is_not_action_conflict(self):
        """同类型经验即便场景相同也不该按结论相反计分"""
        a = make_exp("success", ["高波动", "下跌趋势"], action="买入")
        b = make_exp("success", ["高波动", "下跌趋势"], action="卖出")

        score = self.matcher._calculate_conflict_score(a, b)

        self.assertAlmostEqual(score, 0.7, places=6)


class TestMatcherSingleton(unittest.TestCase):

    def test_returns_same_instance(self):
        self.assertIs(get_smart_experience_matcher(), get_smart_experience_matcher())


if __name__ == "__main__":
    unittest.main()
