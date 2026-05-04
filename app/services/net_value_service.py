"""
基于证监会官方净值的ETF服务
放弃成交额，只使用每日净值数据
"""

import logging
from datetime import datetime
from typing import List, Optional, Dict
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFBasic, ETFQuotation
from app.db.database import SessionLocal
from app.services.csrc_data_source import get_csrc_data_source

logger = logging.getLogger(__name__)


class NetValueService:
    """基于净值的ETF数据服务"""
    
    def __init__(self):
        self.csrc_source = get_csrc_data_source()
    
    def sync_etf_list_from_db(self, db: Session) -> List[ETFBasic]:
        """
        从数据库获取广发基金ETF列表
        证监会接口没有列表功能，使用数据库已有的列表
        """
        gf_etfs = db.query(ETFBasic).filter(
            ETFBasic.etf_name.contains('广发')
        ).all()
        
        logger.info(f"从数据库获取 {len(gf_etfs)} 只广发基金ETF")
        return gf_etfs
    
    def fetch_and_save_net_value(self, etf_code: str, db: Session, days_limit: int = None) -> Dict:
        """
        获取并保存单只ETF的净值数据
        
        Args:
            etf_code: ETF代码
            db: 数据库会话
            days_limit: 限制获取的天数（None表示全部历史，1表示只获取最近1天）
        
        Returns:
            {'success': bool, 'count': int, 'etf_code': str}
        """
        logger.info(f"开始获取 {etf_code} 净值数据（days_limit={days_limit}）...")
        
        # 从证监会获取净值数据（严格遵守频率限制）
        df = self.csrc_source.fetch_etf_net_value(etf_code, days_limit=days_limit)
        
        if df.empty:
            logger.warning(f"{etf_code} 未获取到净值数据")
            return {'success': False, 'count': 0, 'etf_code': etf_code}
        
        # 保存到数据库（转换为净值数据）
        count = self._save_net_value_to_db(etf_code, df, db)
        
        logger.info(f"{etf_code} 成功保存 {count} 条净值数据")
        
        return {
            'success': True,
            'count': count,
            'etf_code': etf_code,
            'latest_date': df['trade_date'].max().strftime('%Y-%m-%d') if 'trade_date' in df.columns else None,
            'latest_net_value': float(df['net_value'].iloc[-1]) if 'net_value' in df.columns else None
        }
    
    def _save_net_value_to_db(self, etf_code: str, df: pd.DataFrame, db: Session) -> int:
        """
        保存净值数据到数据库
        
        将净值数据映射为ETFQuotation模型：
        - close_price = net_value (净值作为收盘价)
        - open_price = net_value (净值作为开盘价，净值每日只有一个值)
        - high_price = net_value
        - low_price = net_value
        - change_pct = net_value_change_pct (净值增长率)
        - volume = 0 (无成交量)
        - amount = 0 (无成交额)
        """
        # 查询已有数据
        existing_dates = set(
            r[0]
            for r in db.query(ETFQuotation.trade_date)
            .filter(ETFQuotation.etf_code == etf_code)
            .all()
        )
        
        count = 0
        for _, row in df.iterrows():
            trade_date = row.get('trade_date')
            if pd.isna(trade_date):
                continue
            
            trade_date = pd.to_datetime(trade_date).date()
            
            # 跳过已有日期
            if trade_date in existing_dates:
                continue
            
            net_value = float(row.get('net_value', 0))
            change_pct = float(row.get('net_value_change_pct', 0) or 0)
            
            # 创建行情记录（用净值代替交易价格）
            quote = ETFQuotation(
                etf_code=etf_code,
                trade_date=trade_date,
                open_price=net_value,  # 净值作为开盘价
                close_price=net_value,  # 净值作为收盘价
                high_price=net_value,  # 净值作为最高价
                low_price=net_value,  # 净值作为最低价
                volume=0,  # 无成交量
                amount=0,  # 无成交额
                change_pct=change_pct,  # 净值增长率
            )
            
            db.add(quote)
            count += 1
        
        db.commit()
        return count
    
    def batch_update_net_values(self, db: Session, limit: int = None, days_limit: int = None) -> Dict:
        """
        批量更新ETF净值数据
        
        Args:
            db: 数据库会话
            limit: 每次最多更新数量（遵守频率限制）
            days_limit: 限制获取的天数（None表示全部历史，1表示只获取最近1天）
        
        Returns:
            {
                'success_count': int,
                'fail_count': int,
                'total_etfs': int,
                'updated_etfs': list,
                'failed_etfs': list
            }
        """
        # 获取数据库中所有ETF（不再限定广发基金）
        from app.models.etf import ETFBasic
        all_etfs = db.query(ETFBasic).all()
        
        # 限制每次最多更新的数量（None表示更新全部）
        if limit is None:
            update_etfs = all_etfs
        else:
            update_etfs = all_etfs[:min(limit, len(all_etfs))]
        
        logger.info(f"开始批量更新 {len(update_etfs)} 只ETF净值数据（频率限制：1秒1次，days_limit={days_limit}）")
        
        result = {
            'success_count': 0,
            'fail_count': 0,
            'total_etfs': len(all_etfs),
            'updated_etfs': [],
            'failed_etfs': []
        }
        
        for etf in update_etfs:
            try:
                res = self.fetch_and_save_net_value(etf.etf_code, db, days_limit=days_limit)
                
                if res['success']:
                    result['success_count'] += 1
                    result['updated_etfs'].append({
                        'code': etf.etf_code,
                        'name': etf.etf_name,
                        'count': res['count']
                    })
                else:
                    result['fail_count'] += 1
                    result['failed_etfs'].append(etf.etf_code)
                    
            except Exception as e:
                logger.error(f"{etf.etf_code} 更新失败: {e}")
                result['fail_count'] += 1
                result['failed_etfs'].append(etf.etf_code)
        
        logger.info(f"批量更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}, 共 {len(all_etfs)} 只ETF")
        
        return {
            'success_count': result['success_count'],
            'fail_count': result['fail_count'],
            'total': len(all_etfs),
            'updated_etfs': result['updated_etfs'],
            'failed_etfs': result['failed_etfs']
        }
    
    def get_net_value_overview(self, db: Session, limit: int = 500) -> List[dict]:
        """
        获取ETF净值概览
        
        Returns:
            [{
                'etf_code': str,
                'etf_name': str,
                'net_value': float,  # 最新净值
                'net_value_change_pct': float,  # 净值增长率
                'trade_date': str
            }]
        """
        # 获取最新净值日期
        latest_date = db.query(func.max(ETFQuotation.trade_date)).scalar()
        
        if not latest_date:
            return []
        
        # 获取所有ETF基础信息（广发、易方达、华夏）
        all_etfs = db.query(ETFBasic).all()
        
        # 获取最新净值数据
        latest_quotes = db.query(ETFQuotation).filter(
            ETFQuotation.trade_date == latest_date
        ).all()
        
        # 构建结果
        quotes_dict = {q.etf_code: q for q in latest_quotes}
        
        result = []
        for etf in all_etfs:
            quote = quotes_dict.get(etf.etf_code)
            
            result.append({
                'etf_code': etf.etf_code,
                'etf_name': etf.etf_name,
                'net_value': quote.close_price if quote else None,  # 净值
                'net_value_change_pct': quote.change_pct if quote else None,  # 净值增长率
                'trade_date': quote.trade_date.isoformat() if quote else None,
                'has_net_value': quote is not None
            })
        
        # 按净值增长率排序
        result.sort(key=lambda x: x['net_value_change_pct'] or 0, reverse=True)
        
        return result[:limit]


# 单例
_net_value_service: Optional[NetValueService] = None


def get_net_value_service() -> NetValueService:
    """获取净值服务单例"""
    global _net_value_service
    if _net_value_service is None:
        _net_value_service = NetValueService()
    return _net_value_service
