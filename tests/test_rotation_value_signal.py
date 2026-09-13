"""轮动辩论行业性价比信号注入测试：ETF→行业反查、材料附加、无数据/异常降级"""
import unittest
from datetime import date
from unittest.mock import patch

from tests.test_value_model import make_db


def seed_industry_score(db, day, industry, score, rank):
    from app.models.stock_fundamental import IndustryScore
    db.add(IndustryScore(trade_date=day, industry=industry, score=score,
                         rank=rank, sample_count=10))
    db.commit()


class TestEtfIndustrySignals(unittest.TestCase):
    """ValueModelService.get_etf_industry_signals"""

    def setUp(self):
        self.db = make_db()
        self.day = date(2026, 9, 11)

    def test_empty_table_returns_empty(self):
        from app.services.value_model_service import ValueModelService
        names = {"512010": "医药ETF"}
        self.assertEqual(ValueModelService().get_etf_industry_signals(self.db, names), {})

    def test_no_etf_names_returns_empty(self):
        from app.services.value_model_service import ValueModelService
        seed_industry_score(self.db, self.day, "医药制造业", 88.0, 1)
        self.assertEqual(ValueModelService().get_etf_industry_signals(self.db, {}), {})

    def test_keyword_match_and_fields(self):
        from app.services.value_model_service import ValueModelService
        seed_industry_score(self.db, self.day, "医药制造业", 88.0, 1)
        seed_industry_score(self.db, self.day, "黑色金属冶炼", 15.0, 2)
        names = {
            "512010": "医药ETF",
            "515220": "钢铁ETF",
            "510300": "沪深300ETF",   # 无关键词匹配 → 不出现在结果中
        }
        signals = ValueModelService().get_etf_industry_signals(self.db, names)
        self.assertEqual(set(signals.keys()), {"512010", "515220"})
        sig = signals["512010"]
        self.assertEqual(sig["industry"], "医药制造业")
        self.assertEqual(sig["rank"], 1)
        self.assertEqual(sig["total"], 2)
        self.assertEqual(sig["as_of"], "2026-09-11")


class TestRotationAttach(unittest.TestCase):
    """RotationService._attach_value_signals"""

    def setUp(self):
        from app.services.rotation_service import RotationService
        self.svc = RotationService()
        self.db = make_db()
        self.day = date(2026, 9, 11)
        seed_industry_score(self.db, self.day, "医药制造业", 88.0, 1)

    def test_attach_writes_field(self):
        items = [
            {"etf_code": "512010", "etf_name": "医药ETF"},
            {"etf_code": "510300", "etf_name": "沪深300ETF"},
        ]
        self.svc._attach_value_signals(items, self.db)
        self.assertIn("industry_value", items[0])
        self.assertEqual(items[0]["industry_value"]["rank"], 1)
        self.assertNotIn("industry_value", items[1])

    def test_no_scores_degrades_quietly(self):
        empty_db = make_db()  # 无 IndustryScore 数据
        items = [{"etf_code": "512010", "etf_name": "医药ETF"}]
        self.svc._attach_value_signals(items, empty_db)
        self.assertNotIn("industry_value", items[0])

    def test_exception_does_not_propagate(self):
        items = [{"etf_code": "512010", "etf_name": "医药ETF"}]
        with patch(
            "app.services.value_model_service.get_value_model_service",
            side_effect=RuntimeError("boom"),
        ):
            self.svc._attach_value_signals(items, self.db)
        self.assertNotIn("industry_value", items[0])

    def test_items_without_name_skipped(self):
        items = [{"etf_code": "512010"}]
        self.svc._attach_value_signals(items, self.db)
        self.assertNotIn("industry_value", items[0])


if __name__ == "__main__":
    unittest.main()
