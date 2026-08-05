"""A股交易日历工具"""

from datetime import date, datetime, time, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# A股交易时段
MARKET_MORNING_OPEN = time(9, 30)
MARKET_MORNING_CLOSE = time(11, 30)
MARKET_AFTERNOON_OPEN = time(13, 0)
MARKET_AFTERNOON_CLOSE = time(15, 0)


def is_trading_day(d: date) -> bool:
    """判断是否为交易日（简化版：仅判断工作日，不含节假日日历）"""
    return d.weekday() < 5


def is_market_open_now() -> bool:
    """判断当前是否在交易时段内"""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    current_time = now.time()
    return (MARKET_MORNING_OPEN <= current_time <= MARKET_MORNING_CLOSE or
            MARKET_AFTERNOON_OPEN <= current_time <= MARKET_AFTERNOON_CLOSE)


def is_during_trading_hours() -> bool:
    """判断当前是否在交易时间内（9:30-15:00，含午休）"""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    current_time = now.time()
    return MARKET_MORNING_OPEN <= current_time <= MARKET_AFTERNOON_CLOSE


def is_after_market_close() -> bool:
    """判断当前是否已收盘（15:00之后）"""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    return now.time() > MARKET_AFTERNOON_CLOSE


def get_previous_trading_day(d: date) -> date:
    """获取前一个交易日"""
    d = d - timedelta(days=1)
    while not is_trading_day(d):
        d = d - timedelta(days=1)
    return d


def get_display_date() -> date:
    """
    获取界面应显示的日期。
    
    规则：
    - 交易时段内（9:30-15:00）：显示T-1（前一交易日）
    - 收盘后：显示指标表中最新日期（T日扫描完成后即为T日）
    - 非交易日：显示最近一个有数据的交易日
    """
    from app.db.database import SessionLocal
    from app.models.etf import ETFDailyIndicator
    from sqlalchemy import func
    
    now = datetime.now()
    today = now.date()
    
    # 交易时段内，显示T-1
    if is_during_trading_hours():
        return get_previous_trading_day(today)
    
    # 收盘后或非交易日，显示指标表最新日期
    db = SessionLocal()
    try:
        latest = db.query(func.max(ETFDailyIndicator.trade_date)).scalar()
        if latest:
            return latest
        # 无指标数据，显示前一交易日
        return get_previous_trading_day(today)
    finally:
        db.close()


def get_latest_quote_date() -> Optional[date]:
    """获取行情表中最新日期"""
    from app.db.database import SessionLocal
    from app.models.etf import ETFQuotation
    from sqlalchemy import func
    
    db = SessionLocal()
    try:
        return db.query(func.max(ETFQuotation.trade_date)).scalar()
    finally:
        db.close()


def get_latest_indicator_date() -> Optional[date]:
    """获取指标表中最新日期"""
    from app.db.database import SessionLocal
    from app.models.etf import ETFDailyIndicator
    from sqlalchemy import func
    
    db = SessionLocal()
    try:
        return db.query(func.max(ETFDailyIndicator.trade_date)).scalar()
    finally:
        db.close()
