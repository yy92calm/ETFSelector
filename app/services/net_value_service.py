"""
ETF行情数据服务
数据源: DataSourceManager（Ashare 主 + efinance 兜底），不再直接依赖 efinance
"""

import logging
import random
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFBasic, ETFQuotation
from app.db.database import SessionLocal
from app.services.data_sources import get_data_source_manager

logger = logging.getLogger(__name__)

# 批量更新请求间隔（秒），随机化防封
BATCH_SLEEP_MIN = 1.0
BATCH_SLEEP_MAX = 2.5


class NetValueService:
    """ETF行情数据服务"""

    def __init__(self):
        pass

    def sync_etf_list_from_db(self, db: Session) -> List[ETFBasic]:
        """从数据库获取ETF列表"""
        etfs = db.query(ETFBasic).all()
        logger.info(f"从数据库获取 {len(etfs)} 只ETF")
        return etfs

    def _fetch_quotes(self, etf_code: str, days_limit: int = None) -> pd.DataFrame:
        """使用 DataSourceManager（Ashare 主）获取ETF日K线数据"""
        try:
            manager = get_data_source_manager()
            if days_limit:
                start_date = (datetime.now() - timedelta(days=days_limit)).strftime("%Y%m%d")
            else:
                start_date = "20200101"
            df = manager.fetch_etf_daily(etf_code, start_date=start_date)
            if df.empty:
                return pd.DataFrame()
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            return df
        except Exception as e:
            logger.warning(f"[数据源] {etf_code} 获取失败: {e}")
            return pd.DataFrame()

    def fetch_and_save_net_value(self, etf_code: str, db: Session, days_limit: int = None) -> Dict:
        """获取并保存单只ETF的行情数据"""
        df = self._fetch_quotes(etf_code, days_limit=days_limit)

        if df.empty:
            logger.warning(f"{etf_code} 未获取到行情数据")
            return {'success': False, 'count': 0, 'etf_code': etf_code}

        count = self._save_net_value_to_db(etf_code, df, db)

        return {
            'success': True,
            'count': count,
            'etf_code': etf_code,
            'latest_date': df['trade_date'].max().strftime('%Y-%m-%d') if 'trade_date' in df.columns else None,
            'latest_net_value': float(df['close'].iloc[-1]) if 'close' in df.columns else None,
        }

    def _save_net_value_to_db(self, etf_code: str, df: pd.DataFrame, db: Session) -> int:
        """保存行情数据到数据库。

        重要：仅在该日期无真实行情数据时才写入，
        避免重复数据覆盖真实K线。
        """
        existing_dates = set(
            r[0]
            for r in db.query(ETFQuotation.trade_date)
            .filter(ETFQuotation.etf_code == etf_code)
            .all()
        )

        count = 0
        for _, row in df.iterrows():
            trade_date = row.get("trade_date")
            if pd.isna(trade_date):
                continue

            trade_date = pd.to_datetime(trade_date).date()

            # 已有数据的日期一律跳过
            if trade_date in existing_dates:
                continue

            close_price = float(row.get("close", 0))
            open_price = float(row.get("open", close_price))
            high_price = float(row.get("high", close_price))
            low_price = float(row.get("low", close_price))
            volume = float(row.get("volume", 0))
            amount = float(row.get("amount", 0))
            change_pct = float(row.get("change_pct", 0) or 0)

            # 数据质量校验：价格必须为正数
            if close_price <= 0:
                continue

            quote = ETFQuotation(
                etf_code=etf_code,
                trade_date=trade_date,
                open_price=open_price,
                close_price=close_price,
                high_price=high_price,
                low_price=low_price,
                volume=volume,
                amount=amount,
                change_pct=change_pct,
            )

            db.add(quote)
            count += 1

        db.commit()
        return count

    def batch_update_net_values(self, db: Session, limit: int = None, days_limit: int = None) -> Dict:
        """批量更新ETF净值数据"""
        from app.models.etf import ETFBasic
        all_etfs = db.query(ETFBasic).all()

        if limit is None:
            update_etfs = all_etfs
        else:
            update_etfs = all_etfs[:min(limit, len(all_etfs))]

        logger.info(f"开始批量更新 {len(update_etfs)} 只ETF净值数据（days_limit={days_limit}）")

        result = {
            'success_count': 0,
            'fail_count': 0,
            'total_etfs': len(all_etfs),
            'updated_etfs': [],
            'failed_etfs': [],
        }

        for etf in update_etfs:
            try:
                res = self.fetch_and_save_net_value(etf.etf_code, db, days_limit=days_limit)

                if res['success']:
                    result['success_count'] += 1
                    result['updated_etfs'].append({
                        'code': etf.etf_code,
                        'name': etf.etf_name,
                        'count': res['count'],
                    })
                else:
                    result['fail_count'] += 1
                    result['failed_etfs'].append(etf.etf_code)

            except Exception as e:
                logger.error(f"{etf.etf_code} 更新失败: {e}")
                result['fail_count'] += 1
                result['failed_etfs'].append(etf.etf_code)

            # 随机间隔防封
            time.sleep(random.uniform(BATCH_SLEEP_MIN, BATCH_SLEEP_MAX))

        logger.info(f"批量更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}, 共 {len(all_etfs)} 只ETF")

        return {
            'success_count': result['success_count'],
            'fail_count': result['fail_count'],
            'total': len(all_etfs),
            'updated_etfs': result['updated_etfs'],
            'failed_etfs': result['failed_etfs'],
        }

    def get_net_value_overview(self, db: Session, limit: int = 500) -> List[dict]:
        """获取ETF净值概览"""
        latest_date = db.query(func.max(ETFQuotation.trade_date)).scalar()

        if not latest_date:
            return []

        all_etfs = db.query(ETFBasic).all()

        latest_quotes = db.query(ETFQuotation).filter(
            ETFQuotation.trade_date == latest_date
        ).all()

        quotes_dict = {q.etf_code: q for q in latest_quotes}

        result = []
        for etf in all_etfs:
            quote = quotes_dict.get(etf.etf_code)

            result.append({
                'etf_code': etf.etf_code,
                'etf_name': etf.etf_name,
                'net_value': quote.close_price if quote else None,
                'net_value_change_pct': quote.change_pct if quote else None,
                'trade_date': quote.trade_date.isoformat() if quote else None,
                'has_net_value': quote is not None,
            })

        result.sort(key=lambda x: x['net_value_change_pct'] or 0, reverse=True)
        return result[:limit]


# 单例
_net_value_service: Optional[NetValueService] = None


def get_net_value_service() -> NetValueService:
    global _net_value_service
    if _net_value_service is None:
        _net_value_service = NetValueService()
    return _net_value_service
