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
    from app.models import etf, strategy, portfolio  # noqa: F401
    Base.metadata.create_all(bind=engine)
    
    # 添加新字段（兼容旧数据库）
    try:
        with engine.connect() as conn:
            # 检查并添加allocation_config字段
            result = conn.execute(text("PRAGMA table_info(strategy)"))
            columns = [row[1] for row in result.fetchall()]
            
            if 'allocation_config' not in columns:
                conn.execute(text("ALTER TABLE strategy ADD COLUMN allocation_config JSON"))
                conn.commit()
                print("✓ 已添加 allocation_config 字段")
            
            if 'rebalance_freq' not in columns:
                conn.execute(text("ALTER TABLE strategy ADD COLUMN rebalance_freq VARCHAR(20) DEFAULT 'quarterly'"))
                conn.commit()
                print("✓ 已添加 rebalance_freq 字段")
            
            if 'rebalance_threshold' not in columns:
                conn.execute(text("ALTER TABLE strategy ADD COLUMN rebalance_threshold FLOAT DEFAULT 0.05"))
                conn.commit()
                print("✓ 已添加 rebalance_threshold 字段")
                
    except Exception as e:
        print(f"数据库迁移警告: {e}")
