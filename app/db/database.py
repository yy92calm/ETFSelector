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
    Base.metadata.create_all(bind=engine)

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
            ]

            for col_name, col_type in exp_migrations:
                if col_name not in columns:
                    conn.execute(text(f"ALTER TABLE experience ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                    print(f"✓ 已添加 experience.{col_name} 字段")

    except Exception as e:
        print(f"experience 表迁移警告: {e}")

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
