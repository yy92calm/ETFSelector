"""数据库引擎与会话管理"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有表并添加新字段"""
    # 确保所有模型已导入
    from app.models import (  # noqa: F401
        etf, strategy, portfolio,
        auto_strategy_log, sentiment, experience,
        system_config,
    )
    from app.models import chat  # noqa: F401
    from app.models.etf import ETFDailyIndicator  # noqa: F401
    from app.models.task_log import TaskExecutionLog  # noqa: F401
    from app.models.pipeline_checkpoint import PipelineCheckpoint  # noqa: F401
    from app.models.factor_performance import FactorPerformance  # noqa: F401
    from app.models.market_regime import MarketRegimeSnapshot  # noqa: F401
    from app.models.stock_fundamental import StockFundamental, IndustryScore  # noqa: F401
    from app.models.sw_industry import SwIndustryDaily  # noqa: F401
    from app.models.allocation_suggestion import AllocationSuggestion  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # 调仓建议的显式替换提案（建议可执行化）
    try:
        with engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(allocation_suggestion)"))}
            if cols and "proposed_swaps" not in cols:
                conn.execute(text("ALTER TABLE allocation_suggestion ADD COLUMN proposed_swaps JSON"))
                conn.commit()
    except Exception as e:
        print(f"迁移 proposed_swaps 失败: {e}")

    # 策略暂停冷却字段（熔断自动恢复用）
    try:
        with engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(strategy)"))}
            if "paused_cooldown_days" not in cols:
                conn.execute(text("ALTER TABLE strategy ADD COLUMN paused_cooldown_days INTEGER"))
                conn.commit()
    except Exception as e:
        print(f"迁移 paused_cooldown_days 失败: {e}")

    # 添加新字段（兼容旧数据库）
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(strategy)"))
            columns = [row[1] for row in result.fetchall()]

            migrations = [
                ("allocation_config", "JSON"),
                ("rebalance_freq", "VARCHAR(20) DEFAULT 'monthly'"),
                ("rebalance_threshold", "FLOAT DEFAULT 0.05"),
                ("strategy_source", "VARCHAR(20) DEFAULT 'manual'"),
                ("auto_strategy_status", "VARCHAR(20)"),
                ("last_auto_analysis_date", "DATE"),
                ("auto_adjustment_count", "INTEGER DEFAULT 0"),
                ("max_daily_adjustments", "INTEGER DEFAULT 1"),
                ("last_analysis_result", "JSON"),
                ("enable_memory", "BOOLEAN DEFAULT 1"),
                ("experience_limit", "INTEGER DEFAULT 50"),
                ("paused_reason", "VARCHAR(200)"),
                ("paused_date", "DATE"),
                ("holding_start_date", "DATE"),
                ("pending_allocation", "JSON"),
                ("pending_set_date", "DATE"),
                ("failure_signature", "VARCHAR(200)"),
                ("occurrence_count", "INTEGER DEFAULT 1"),
                ("last_triggered_date", "DATE"),
                ("target_monthly_min", "FLOAT DEFAULT 0.05"),
                ("target_monthly_max", "FLOAT DEFAULT 0.10"),
                ("deleted_at", "DATETIME"),
            ]

            for col_name, col_type in migrations:
                if col_name not in columns:
                    conn.execute(text(f"ALTER TABLE strategy ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                    print(f"✓ 已添加 {col_name} 字段")

    except Exception as e:
        print(f"数据库迁移警告: {e}")

    # 兼容旧数据库：experience 表新增字段
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(experience)"))
            columns = [row[1] for row in result.fetchall()]

            exp_migrations = [
                ("failure_signature", "VARCHAR(200)"),
                ("occurrence_count", "INTEGER DEFAULT 1"),
                ("last_triggered_date", "DATE"),
                ("initial_weight", "FLOAT DEFAULT 1.0"),
                ("weight_source", "VARCHAR(20)"),
            ]

            for col_name, col_type in exp_migrations:
                if col_name not in columns:
                    conn.execute(text(f"ALTER TABLE experience ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                    print(f"✓ 已添加 experience.{col_name} 字段")

    except Exception as e:
        print(f"experience 表迁移警告: {e}")

    # 兼容旧数据库：experience.strategy_id 旧表为 NOT NULL，模型自引入系统级经验起为 nullable
    # （空 strategy_id 的管道失败经验，此前每次写入都撞 IntegrityError）。
    # SQLite 无法 ALTER 放宽约束，须一次性表重建；幂等：按 PRAGMA notnull 检测跳过。
    # 顺序必须「建新表(临时名) → 拷贝 → 删原表 → 改名回来」：若先把原表改名再 DROP，
    # SQLite 3.25+ 会同步改写引用方（experience_usage_record）的外键指向被删表，留下悬空引用。
    try:
        with engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(experience)")).fetchall()
            strategy_col = next((c for c in cols if c[1] == "strategy_id"), None)
            if strategy_col and strategy_col[3] == 1:  # notnull=1
                from sqlalchemy.schema import CreateTable, CreateIndex
                from app.models.experience import Experience

                old_cols = {c[1] for c in cols}
                # 仅迁移旧表已有的列；新表缺列由上面的 ADD COLUMN 迁移保证已补齐
                common = [c.name for c in Experience.__table__.columns if c.name in old_cols]
                col_list = ", ".join(common)

                ddl = str(CreateTable(Experience.__table__).compile(dialect=engine.dialect)).strip()
                _, body = ddl.split("(", 1)  # 模型 DDL 的表名换成临时名
                conn.execute(text(f"CREATE TABLE experience_new ({body}"))
                conn.execute(text(
                    f"INSERT INTO experience_new ({col_list}) SELECT {col_list} FROM experience"
                ))
                conn.execute(text("DROP TABLE experience"))
                conn.execute(text("ALTER TABLE experience_new RENAME TO experience"))
                for idx in Experience.__table__.indexes:
                    conn.execute(text(str(CreateIndex(idx)).strip()))
                conn.commit()
                print("✓ 已放宽 experience.strategy_id 为可空（表重建，全部行保留）")

    except Exception as e:
        print(f"experience 表约束迁移警告: {e}")

    # 兼容旧数据库：trade_record 表新增字段
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(trade_record)"))
            columns = [row[1] for row in result.fetchall()]
            if "fee" not in columns:
                conn.execute(text("ALTER TABLE trade_record ADD COLUMN fee FLOAT DEFAULT 0.0"))
                conn.commit()
                print("✓ 已添加 trade_record.fee 字段")
    except Exception as e:
        print(f"trade_record 表迁移警告: {e}")

    # 兼容旧数据库：chat_session 表新增字段
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(chat_session)"))
            columns = [row[1] for row in result.fetchall()]

            chat_migrations = [
                ("context_summary", "TEXT"),
                ("model", "VARCHAR(100)"),
            ]

            for col_name, col_type in chat_migrations:
                if col_name not in columns:
                    conn.execute(text(f"ALTER TABLE chat_session ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                    print(f"✓ 已添加 chat_session.{col_name} 字段")

    except Exception as e:
        print(f"chat_session 表迁移警告: {e}")

    # 兼容旧数据库：chat_message 表新增字段
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(chat_message)"))
            columns = [row[1] for row in result.fetchall()]
            if "usage" not in columns:
                conn.execute(text("ALTER TABLE chat_message ADD COLUMN usage JSON"))
                conn.commit()
                print("✓ 已添加 chat_message.usage 字段")
    except Exception as e:
        print(f"chat_message 表迁移警告: {e}")
