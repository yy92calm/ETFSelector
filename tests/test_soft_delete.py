"""4.4 策略软删除：删除只摘走可见性与自动运行，关联历史数据一条不删、可按 ID 查回"""

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
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.experience import Experience
from app.models.portfolio import Holding, PortfolioSnapshot, TradeRecord
from app.models.strategy import STATUS_DELETED, Strategy
from app.services.strategy_service import get_strategy_service
from app.tools.strategy_tools import delete_strategy, get_strategy_detail, list_strategies

TODAY = date.today()


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_strategy(db, sid=1, status="active", source="auto_generated", auto_status="running"):
    strategy = Strategy(
        id=sid, name=f"策略{sid}", allocation_config={"510300": 1.0},
        strategy_type="auto", strategy_source=source, auto_strategy_status=auto_status,
        status=status, initial_capital=1000000,
    )
    db.add(strategy)
    db.commit()
    return strategy


def seed_history(db, sid=1):
    """五类关联数据各写 1 条，软删除后必须一条不少"""
    db.add(PortfolioSnapshot(strategy_id=sid, trade_date=TODAY, total_asset=1000000.0,
                             cash=0.0, market_value=1000000.0, profit=0.0, profit_pct=0.0))
    db.add(TradeRecord(strategy_id=sid, trade_date=TODAY, etf_code="510300",
                       direction="buy", price=4.0, quantity=100, amount=400.0))
    db.add(Holding(strategy_id=sid, etf_code="510300", quantity=100,
                   avg_cost=4.0, current_price=4.0, market_value=400.0))
    db.add(AutoStrategyLog(strategy_id=sid, log_date=TODAY, status="success", action_type="analyze"))
    db.add(Experience(strategy_id=sid, experience_type="failure", scenario_tags=["高波动"],
                      title="追高失败", description="追高买入后回撤", result="negative",
                      generated_date=TODAY, expires_date=TODAY + timedelta(days=90)))
    db.commit()


def related_counts(db, sid=1):
    return {
        "snapshots": db.query(PortfolioSnapshot).filter(PortfolioSnapshot.strategy_id == sid).count(),
        "trades": db.query(TradeRecord).filter(TradeRecord.strategy_id == sid).count(),
        "holdings": db.query(Holding).filter(Holding.strategy_id == sid).count(),
        "auto_logs": db.query(AutoStrategyLog).filter(AutoStrategyLog.strategy_id == sid).count(),
        "experiences": db.query(Experience).filter(Experience.strategy_id == sid).count(),
    }


class TestSoftDeleteTool(unittest.TestCase):
    """LLM 工具 delete_strategy：只置状态，不物理删除"""

    def test_related_data_survives(self):
        db = make_db()
        seed_strategy(db)
        seed_history(db)
        before = related_counts(db)

        result = delete_strategy(db, 1)

        self.assertTrue(result["success"])
        self.assertEqual(result["retained_records"], before)
        self.assertEqual(before, {"snapshots": 1, "trades": 1, "holdings": 1,
                                  "auto_logs": 1, "experiences": 1})
        self.assertEqual(related_counts(db), before)

    def test_marks_deleted_and_stops_auto(self):
        db = make_db()
        seed_strategy(db)
        delete_strategy(db, 1)

        s = db.query(Strategy).filter(Strategy.id == 1).first()
        self.assertEqual(s.status, STATUS_DELETED)
        self.assertEqual(s.auto_strategy_status, "stopped")
        self.assertIsNotNone(s.deleted_at)

    def test_excluded_from_list_but_readable_by_id(self):
        db = make_db()
        seed_strategy(db, sid=1)
        seed_strategy(db, sid=2, status="paused")
        delete_strategy(db, 1)

        listed = list_strategies(db)["strategies"]
        self.assertEqual([s["id"] for s in listed], [2])
        # 按 ID 单点查询仍可解析：历史净值/决策依据需要能查回
        detail = get_strategy_detail(db, 1)
        self.assertEqual(detail["status"], STATUS_DELETED)

    def test_paused_and_archived_stay_visible(self):
        """过滤口径是「排除 deleted」而不是「只留 active」——暂停/归档策略仍在列表里"""
        db = make_db()
        seed_strategy(db, sid=1, status="active")
        seed_strategy(db, sid=2, status="paused")
        seed_strategy(db, sid=3, status="archived")
        seed_strategy(db, sid=4, status=STATUS_DELETED)

        ids = [s["id"] for s in list_strategies(db)["strategies"]]
        self.assertEqual(sorted(ids), [1, 2, 3])

    def test_idempotent(self):
        db = make_db()
        seed_strategy(db)
        first = delete_strategy(db, 1)
        deleted_at = db.get(Strategy, 1).deleted_at

        second = delete_strategy(db, 1)

        self.assertFalse(first["already_deleted"])
        self.assertTrue(second["already_deleted"])
        self.assertEqual(db.get(Strategy, 1).deleted_at, deleted_at)

    def test_missing_strategy(self):
        db = make_db()
        self.assertIn("error", delete_strategy(db, 99))

    def test_still_a_write_tool(self):
        from app.tools.registry import _TOOL_REGISTRY, _WRITE_TOOLS

        self.assertIn("delete_strategy", _WRITE_TOOLS)
        self.assertEqual(_TOOL_REGISTRY["delete_strategy"].risk_level, "write")


class TestServiceDeleteAndRestore(unittest.TestCase):
    """人工 API 走的 service 路径与删除工具共用同一套软删除语义"""

    def test_service_delete_is_soft(self):
        db = make_db()
        seed_strategy(db)
        seed_history(db)
        svc = get_strategy_service()

        self.assertTrue(svc.delete_strategy(1, db))
        self.assertEqual(svc.list_strategies(db), [])
        self.assertIsNotNone(svc.get_strategy(1, db))
        self.assertEqual(related_counts(db)["snapshots"], 1)

    def test_service_delete_missing(self):
        db = make_db()
        self.assertFalse(get_strategy_service().delete_strategy(7, db))

    def test_restore_via_status(self):
        """恢复路径 = 把状态改回 active：列表重新可见，且不留「active 但带删除时间」的矛盾态"""
        db = make_db()
        seed_strategy(db)
        svc = get_strategy_service()
        svc.delete_strategy(1, db)

        restored = svc.update_strategy_status(1, "active", db)

        self.assertEqual(restored.status, "active")
        self.assertIsNone(restored.deleted_at)
        self.assertEqual([s.id for s in svc.list_strategies(db)], [1])


class TestEnumerationEntries(unittest.TestCase):
    """列表/聚合/定时任务枚举都不再拾取已删除策略"""

    def test_workbench_overview(self):
        from app.routes.workbench_routes import get_workbench_overview

        db = make_db()
        seed_strategy(db, sid=1)
        seed_strategy(db, sid=2)
        delete_strategy(db, 2)

        data = get_workbench_overview(db).data["strategies"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["active"], 1)

    def test_auto_strategy_list_route(self):
        from app.routes.auto_strategy_routes import list_auto_strategies

        db = make_db()
        seed_strategy(db, sid=1)
        seed_strategy(db, sid=2)
        delete_strategy(db, 2)

        payload = list_auto_strategies(db).data
        self.assertEqual([s["id"] for s in payload["strategies"]], [1])
        self.assertEqual(payload["total"], 1)

    def test_anomaly_reviews_skip_deleted(self):
        from app.tasks import scheduler as sch

        db = make_db()
        seed_strategy(db, sid=1)
        seed_strategy(db, sid=2, status="paused")
        delete_strategy(db, 2)
        visited = []

        with patch("app.services.review_service.ReviewService.detect_anomalies",
                   side_effect=lambda sid, d: visited.append(sid) or []):
            sch._run_anomaly_reviews(db)

        self.assertEqual(visited, [1])

    def test_weekly_review_skips_deleted(self):
        from app.tasks import scheduler as sch

        db = make_db()
        seed_strategy(db, sid=1)
        seed_strategy(db, sid=2)
        delete_strategy(db, 2)
        called = []

        with patch("app.db.database.SessionLocal", return_value=db), \
             patch("app.services.review_service.ReviewService.trigger_review",
                   side_effect=lambda sid, rtype, d: called.append((sid, rtype)) or {}):
            sch._job_weekly_review()

        self.assertEqual(called, [(1, "weekly")])


# 4.4 之前的线上表结构（无 deleted_at，也缺 paused_cooldown_days）
LEGACY_STRATEGY_DDL = """
CREATE TABLE strategy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    strategy_type VARCHAR(30) NOT NULL DEFAULT 'template',
    allocation_config JSON NOT NULL,
    rebalance_freq VARCHAR(20) NOT NULL DEFAULT 'monthly',
    target_monthly_min FLOAT DEFAULT 0.05,
    target_monthly_max FLOAT DEFAULT 0.10,
    rebalance_threshold FLOAT NOT NULL DEFAULT 0.05,
    code TEXT,
    initial_capital INTEGER NOT NULL DEFAULT 100000,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME,
    updated_at DATETIME,
    template_name VARCHAR(50),
    params JSON,
    etf_codes JSON,
    strategy_source VARCHAR(20) DEFAULT 'manual',
    auto_strategy_status VARCHAR(20),
    last_auto_analysis_date DATE,
    auto_adjustment_count INTEGER DEFAULT 0,
    max_daily_adjustments INTEGER DEFAULT 1,
    last_analysis_result JSON,
    enable_memory BOOLEAN DEFAULT 1,
    experience_limit INTEGER DEFAULT 50,
    holding_start_date DATE,
    pending_allocation JSON,
    pending_set_date DATE,
    paused_reason VARCHAR(200),
    paused_date DATE
)
"""


class TestLegacyDbMigration(unittest.TestCase):
    """旧库兼容：init_db 补 deleted_at 列，历史行原样保留且视为未删除"""

    def _legacy_db(self, tmp):
        url = f"sqlite:///{tmp}/legacy.db"
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text(LEGACY_STRATEGY_DDL))
            conn.execute(text(
                "INSERT INTO strategy (name, allocation_config, status) "
                "VALUES ('旧策略', '{\"510300\": 1.0}', 'active')"
            ))
            conn.commit()
        return engine

    def test_migration_adds_column_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            with patch("app.db.database.engine", engine):
                init_db()

            cols = {r[1] for r in engine.connect().execute(text("PRAGMA table_info(strategy)"))}
            self.assertIn("deleted_at", cols)

            db = sessionmaker(bind=engine)()
            rows = db.query(Strategy).all()
            self.assertEqual([r.name for r in rows], ["旧策略"])
            self.assertIsNone(rows[0].deleted_at)
            # 老行 status=active → 过滤条件必须放行（deleted_at 为 NULL 不算已删除）
            self.assertEqual(db.query(Strategy).filter(Strategy.not_deleted()).count(), 1)

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            with patch("app.db.database.engine", engine):
                init_db()
                init_db()   # 第二次不应因列已存在而报错

            self.assertEqual(engine.connect().execute(
                text("SELECT COUNT(*) FROM strategy")).scalar(), 1)

    def test_soft_delete_on_migrated_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._legacy_db(tmp)
            with patch("app.db.database.engine", engine):
                init_db()
            db = sessionmaker(bind=engine)()

            delete_strategy(db, 1)

            self.assertEqual(db.query(Strategy).filter(Strategy.not_deleted()).count(), 0)
            self.assertEqual(db.query(Strategy).count(), 1)


class TestNoDanglingReferences(unittest.TestCase):
    """软删除后策略行仍在，外键指向的关联数据不会因级联或孤儿检查出问题"""

    def test_snapshots_still_joinable(self):
        db = make_db()
        seed_strategy(db)
        seed_history(db)
        delete_strategy(db, 1)

        row = (db.query(PortfolioSnapshot, Strategy.name)
               .join(Strategy, Strategy.id == PortfolioSnapshot.strategy_id).first())
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "策略1")
