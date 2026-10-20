"""experience.strategy_id 约束漂移修复：旧表 NOT NULL → 模型 nullable（系统级经验）
模型自 ca38514 起 nullable=True，但 init_db 只会加列不会放宽约束，线上/本地表一直是 NOT NULL，
导致「管道阶段失败 → 系统级 failure 经验」自上线起每次写入都撞 IntegrityError（0 条落库）"""

import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, init_db
# create_all 只建「已导入模型」的表，故先导入全部模型（与 init_db 口径一致）
from app.models import (  # noqa: F401
    allocation_suggestion, auto_strategy_log, chat, etf, experience, factor_performance,
    market_regime, pipeline_checkpoint, portfolio, sentiment, stock_fundamental, strategy,
    sw_industry, system_config, task_log,
)
from app.models.experience import Experience
from app.tasks.scheduler import _record_failure_experience

TODAY = date.today()

# 旧库的 experience 表：strategy_id NOT NULL，且缺 5 个后来 ADD COLUMN 的字段
LEGACY_EXPERIENCE_DDL = """
CREATE TABLE experience (
    id INTEGER NOT NULL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES strategy (id),
    experience_type VARCHAR(20) NOT NULL,
    scenario_tags JSON,
    title VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    market_condition JSON,
    action_taken JSON,
    result VARCHAR(20) NOT NULL,
    key_insight VARCHAR(200),
    effectiveness_score FLOAT,
    application_count INTEGER,
    success_count INTEGER,
    failure_count INTEGER,
    success_rate FLOAT,
    source_log_id INTEGER,
    source_type VARCHAR(20),
    generated_date DATE NOT NULL,
    is_validated BOOLEAN,
    is_active BOOLEAN,
    expires_date DATE NOT NULL,
    weight FLOAT,
    review_status VARCHAR(20),
    reviewed_by VARCHAR(50),
    reviewed_at DATETIME,
    created_at DATETIME,
    updated_at DATETIME
)
"""

LEGACY_INSERT = (
    "INSERT INTO experience (strategy_id, experience_type, scenario_tags, title, description, "
    "result, source_type, generated_date, expires_date, weight, review_status, created_at, updated_at) "
    "VALUES (1, 'success', '[\"高动量\"]', '旧经验一', 'd1', 'positive', 'review', "
    "'2026-09-01', '2026-12-01', 1.0, 'approved', '2026-09-01 10:00:00', '2026-09-01 10:00:00'), "
    "(2, 'failure', '[\"低波动\"]', '旧经验二', 'd2', 'negative', 'review', "
    "'2026-09-02', '2026-12-02', 0.8, 'pending', '2026-09-02 10:00:00', '2026-09-02 10:00:00')"
)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def notnull_of(engine, table: str, column: str) -> int:
    rows = engine.connect().execute(text(f"PRAGMA table_info({table})")).fetchall()
    return next(r[3] for r in rows if r[1] == column)


class TestConstraintMigration(unittest.TestCase):
    """旧库兼容：init_db 放宽 strategy_id 非空约束，历史行原样保留"""

    def _legacy_db(self, tmp):
        engine = create_engine(f"sqlite:///{tmp}/legacy.db")
        with engine.connect() as conn:
            conn.execute(text(LEGACY_EXPERIENCE_DDL))
            conn.execute(text(LEGACY_INSERT))
            conn.commit()
        return engine

    def test_migration_relaxes_notnull_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            self.assertEqual(notnull_of(engine, "experience", "strategy_id"), 1)
            with patch("app.db.database.engine", engine):
                init_db()
            self.assertEqual(notnull_of(engine, "experience", "strategy_id"), 0)

            db = sessionmaker(bind=engine)()
            rows = db.query(Experience).order_by(Experience.id).all()
            self.assertEqual([r.title for r in rows], ["旧经验一", "旧经验二"])
            self.assertEqual([r.strategy_id for r in rows], [1, 2])
            self.assertEqual(rows[0].weight, 1.0)
            # 迁移补齐的 5 列对旧行为 NULL/默认
            self.assertIsNone(rows[0].failure_signature)
            self.assertIsNone(rows[0].last_triggered_date)

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            with patch("app.db.database.engine", engine):
                init_db()
                init_db()
            self.assertEqual(engine.connect().execute(
                text("SELECT COUNT(*) FROM experience")).scalar(), 2)

    def test_system_level_experience_writable_after_migration(self):
        """迁移后 strategy_id=None 可写入（旧行为：IntegrityError NOT NULL）"""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            with patch("app.db.database.engine", engine):
                init_db()
            db = sessionmaker(bind=engine)()
            db.add(Experience(
                strategy_id=None, experience_type="failure", scenario_tags=["系统级"],
                title="管道失败", description="d", result="negative",
                generated_date=TODAY, expires_date=TODAY + timedelta(days=90),
            ))
            db.commit()
            self.assertEqual(
                db.query(Experience).filter(Experience.strategy_id.is_(None)).count(), 1)

    def test_rebuild_preserves_referencing_fk(self):
        """experience_usage_record 的外键在表重建后仍指向 experience"""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            with patch("app.db.database.engine", engine):
                init_db()
            fks = engine.connect().execute(
                text("PRAGMA foreign_key_list(experience_usage_record)")).fetchall()
            self.assertTrue(any(fk[2] == "experience" for fk in fks))

    def test_rebuild_recreates_model_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            with patch("app.db.database.engine", engine):
                init_db()
            idx = engine.connect().execute(
                text("PRAGMA index_list(experience)")).fetchall()
            self.assertIn("ix_experience_strategy_id", {r[1] for r in idx})


class TestFailureExperienceWrite(unittest.TestCase):
    """生产同款路径：管道阶段失败经验写入与同签名合并（此前在旧约束库上必失败）"""

    def test_write_and_merge_on_fresh_db(self):
        db = make_db()
        _record_failure_experience(db, "policy_flow", "政策/资金流阶段失败: 资金流向: Object of type bool", TODAY)

        rows = db.query(Experience).filter(Experience.strategy_id.is_(None)).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].failure_signature, "pipeline_policy_flow_政策/资金流阶段失败")
        self.assertEqual(rows[0].occurrence_count, 1)
        self.assertEqual(rows[0].experience_type, "failure")
        self.assertEqual(rows[0].source_type, "scheduler")

        _record_failure_experience(db, "policy_flow", "政策/资金流阶段失败: 另一次", TODAY)
        rows = db.query(Experience).filter(Experience.strategy_id.is_(None)).all()
        self.assertEqual(len(rows), 1)  # 同签名合并，不新增行
        self.assertEqual(rows[0].occurrence_count, 2)
        self.assertIn("另一次", rows[0].description)
        self.assertEqual(rows[0].last_triggered_date, TODAY)

    def test_write_on_migrated_legacy_db(self):
        """端到端复现线上场景：旧约束库 → init_db 迁移 → 阶段失败经验成功落库"""
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{tmp}/legacy.db")
            with engine.connect() as conn:
                conn.execute(text(LEGACY_EXPERIENCE_DDL))
                conn.execute(text(LEGACY_INSERT))
                conn.commit()
            with patch("app.db.database.engine", engine):
                init_db()
            db = sessionmaker(bind=engine)()

            _record_failure_experience(
                db, "policy_flow", "政策/资金流阶段失败: 资金流向: Object of type bool", TODAY)

            row = db.query(Experience).filter(Experience.strategy_id.is_(None)).one()
            self.assertEqual(row.occurrence_count, 1)
            self.assertEqual(engine.connect().execute(
                text("SELECT COUNT(*) FROM experience")).scalar(), 3)  # 旧 2 行 + 新 1 行


if __name__ == "__main__":
    unittest.main()
