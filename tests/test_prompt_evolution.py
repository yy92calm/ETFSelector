"""提示词自进化与收益目标锁定测试"""
import unittest
from unittest.mock import patch, MagicMock

from app.models.strategy import Strategy, StrategyEvolvedPrompt


def make_db():
    """内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_strategy(db, target_min=0.05, target_max=0.10):
    s = Strategy(name="进化测试策略", allocation_config={"510300": 1.0},
                 initial_capital=100000, status="active",
                 target_monthly_min=target_min, target_monthly_max=target_max)
    db.add(s)
    db.commit()
    return s


def make_llm_response(prompt_text="【目标使命】月收益5%~10%不可变", summary="初始化"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = (
        '```json\n{"prompt_text": "' + prompt_text + '", "evolution_summary": "' + summary + '"}\n```'
    )
    return resp


class TestEvolvePrompt(unittest.TestCase):
    """ReviewService._evolve_prompt：upsert + version 递增 + 审计"""

    def setUp(self):
        self.db = make_db()
        self.s = seed_strategy(self.db)
        from app.services.review_service import ReviewService
        self.svc = ReviewService()

    def _run_evolve(self):
        return self.svc._evolve_prompt(
            self.s.id, "weekly",
            [{"type": "insight", "title": "测试经验", "key_insight": "k"}],
            self.db,
        )

    @patch("app.services.review_service.get_settings")
    @patch("app.services.rule_trainer.RuleTrainer.train")
    @patch("app.services.portfolio_service.PortfolioService.get_monthly_progress")
    def test_first_evolution_creates_v1(self, mock_progress, mock_train, _mock_settings):
        mock_train.return_value = {"regime_rules": {"bull": {"action": "hold", "allocation": {"510300": 0.8}}}}
        mock_progress.return_value = {"text": "本月收益2%，落后"}
        with patch.object(self.svc, "llm_client") as mock_llm:
            mock_llm.chat.completions.create.return_value = make_llm_response(summary="首次生成")
            result = self._run_evolve()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], 1)
        row = self.db.query(StrategyEvolvedPrompt).filter_by(strategy_id=self.s.id).first()
        self.assertEqual(row.version, 1)
        self.assertIn("目标使命", row.prompt_text)

    @patch("app.services.rule_trainer.RuleTrainer.train")
    @patch("app.services.portfolio_service.PortfolioService.get_monthly_progress")
    def test_second_evolution_increments_version(self, mock_progress, mock_train):
        mock_train.return_value = {"regime_rules": {}}
        mock_progress.return_value = {"text": "本月收益6%，达标"}
        with patch.object(self.svc, "llm_client") as mock_llm:
            mock_llm.chat.completions.create.return_value = make_llm_response()
            self._run_evolve()
            mock_llm.chat.completions.create.return_value = make_llm_response(summary="第二次")
            result = self._run_evolve()
        self.assertEqual(result["version"], 2)
        # 单策略单行（upsert不新增行）
        count = self.db.query(StrategyEvolvedPrompt).filter_by(strategy_id=self.s.id).count()
        self.assertEqual(count, 1)

    @patch("app.services.rule_trainer.RuleTrainer.train")
    @patch("app.services.portfolio_service.PortfolioService.get_monthly_progress")
    def test_invalid_llm_output_skips(self, mock_progress, mock_train):
        """LLM返回无效内容时不写库"""
        mock_train.return_value = {"regime_rules": {}}
        mock_progress.return_value = None
        with patch.object(self.svc, "llm_client") as mock_llm:
            bad = MagicMock()
            bad.choices = [MagicMock()]
            bad.choices[0].message.content = "无法解析为JSON的文字"
            mock_llm.chat.completions.create.return_value = bad
            result = self._run_evolve()
        self.assertIsNone(result)
        count = self.db.query(StrategyEvolvedPrompt).filter_by(strategy_id=self.s.id).count()
        self.assertEqual(count, 0)

    def test_no_llm_client_skips(self):
        self.svc.llm_client = None
        result = self._run_evolve()
        self.assertIsNone(result)


class TestPromptInjection(unittest.TestCase):
    """进化提示词与目标进度注入AI快照"""

    def test_snapshot_contains_target_and_evolved_prompt(self):
        from app.agent_core.context import ContextBuilder
        db = make_db()
        s = seed_strategy(db)
        db.add(StrategyEvolvedPrompt(
            strategy_id=s.id, prompt_text="【目标使命】月收益5%~10%不可变",
            version=1, source_type="weekly",
        ))
        db.commit()
        snapshot = ContextBuilder().build_turn_snapshot(db)
        self.assertIn("收益目标:月5%~10%（不可变）", snapshot)
        self.assertIn("策略进化提示词", snapshot)
        self.assertIn("【目标使命】月收益5%~10%不可变", snapshot)
        self.assertIn("（v1）", snapshot)

    def test_snapshot_without_evolved_prompt_still_has_target(self):
        from app.agent_core.context import ContextBuilder
        db = make_db()
        seed_strategy(db)
        snapshot = ContextBuilder().build_turn_snapshot(db)
        self.assertIn("收益目标:月5%~10%（不可变）", snapshot)

    def test_system_prompt_has_target_discipline(self):
        """SYSTEM_PROMPT 含目标不可变条款"""
        from app.agent_core.loop import SYSTEM_PROMPT
        self.assertIn("不可变使命", SYSTEM_PROMPT)
        self.assertIn("target_monthly_min", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
