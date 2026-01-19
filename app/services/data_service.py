"""
数据获取服务模块
负责从Qtrade API获取行情数据并存储到数据库
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from app.utils.api_client import get_api_client
from app.models.etf_basic import ETFBasic
from app.models.etf_quotation import ETFQuotation
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)

# 常见的上证市场ETF代码
SHANGHAI_ETFS = [
    "sh510050",  # 华夏上证50ETF
    "sh510300",  # 华夏沪深300ETF
    "sh510500",  # 华夏中证500ETF
    "sh510610",  # 易方达消费行业
    "sh510180",  # 上海50
    "sh511800",  # 易方达易利
]

# 常见的深证市场ETF代码
SHENZHEN_ETFS = [
    "sz150018",  # 鹏华创业板
    "sz159915",  # 易方达创业板
    "sz159920",  # 小康证券300
    "sz159949",  # 华夏创业板
    "sz159935",  # 广发创业板
    "sz159999",  # 易方达创业板B
]

# 所有主流ETF代码（包括上深市场）
ALL_MAIN_ETFS = SHANGHAI_ETFS + SHENZHEN_ETFS


class ETFDataService:
    """ETF数据获取服务"""
    
    def __init__(self):
        self.api_client = get_api_client()
    
    async def fetch_and_save_etf_quote(self, etf_code: str, db: Session) -> bool:
        """
        获取单个ETF的行情数据并保存到数据库
        
        Args:
            etf_code: ETF代码
            db: 数据库会话
            
        Returns:
            是否成功保存
        """
        try:
            # 从API获取行情数据
            quote_data = await self.api_client.get_etf_quote(etf_code)
            if not quote_data:
                logger.warning(f"无法获取 {etf_code} 的行情数据")
                return False
            
            # 检查或创建ETF基础信息
            etf_basic = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
            if not etf_basic:
                etf_basic = ETFBasic(
                    etf_code=etf_code,
                    etf_name=quote_data.get("etf_name", ""),
                    update_time=datetime.utcnow()
                )
                db.add(etf_basic)
                db.flush()
                logger.info(f"创建新ETF基础信息: {etf_code}")
            
            # 保存行情数据
            quotation = ETFQuotation(
                etf_code=etf_code,
                trade_date=datetime.now().date(),
                open_price=quote_data.get("last_price", 0),
                close_price=quote_data.get("last_price", 0),
                high_price=quote_data.get("last_price", 0),
                low_price=quote_data.get("last_price", 0),
                volume=quote_data.get("volume", 0),
                amount=quote_data.get("amount", 0),
                change_rate=quote_data.get("change_rate", 0),
                update_time=datetime.utcnow()
            )
            db.add(quotation)
            db.commit()
            logger.info(f"保存 {etf_code} 行情数据成功")
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"保存 {etf_code} 行情数据失败: {e}")
            return False
    
    async def fetch_and_save_etf_quotes_batch(self, etf_codes: List[str]) -> dict:
        """
        批量获取多个ETF的行情数据并保存
        
        Args:
            etf_codes: ETF代码列表
            
        Returns:
            包含成功和失败数量的字典
        """
        db = SessionLocal()
        results = {
            "success_count": 0,
            "fail_count": 0,
            "failed_codes": []
        }
        
        try:
            # 从API批量获取数据
            quotes_data = await self.api_client.get_etf_quotes_batch(etf_codes)
            
            for etf_code in etf_codes:
                if etf_code not in quotes_data:
                    results["fail_count"] += 1
                    results["failed_codes"].append(etf_code)
                    continue
                
                quote_data = quotes_data[etf_code]
                
                # 检查或创建ETF基础信息
                etf_basic = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
                if not etf_basic:
                    etf_basic = ETFBasic(
                        etf_code=etf_code,
                        etf_name=quote_data.get("etf_name", ""),
                        update_time=datetime.utcnow()
                    )
                    db.add(etf_basic)
                
                # 保存行情数据
                quotation = ETFQuotation(
                    etf_code=etf_code,
                    trade_date=datetime.now().date(),
                    open_price=quote_data.get("last_price", 0),
                    close_price=quote_data.get("last_price", 0),
                    high_price=quote_data.get("last_price", 0),
                    low_price=quote_data.get("last_price", 0),
                    volume=quote_data.get("volume", 0),
                    amount=quote_data.get("amount", 0),
                    change_rate=quote_data.get("change_rate", 0),
                    update_time=datetime.utcnow()
                )
                db.add(quotation)
                results["success_count"] += 1
            
            db.commit()
            logger.info(f"批量保存行情数据成功: {results['success_count']} 个成功，{results['fail_count']} 个失败")
        except Exception as e:
            db.rollback()
            logger.error(f"批量保存行情数据失败: {e}")
            results["fail_count"] = len(etf_codes) - results["success_count"]
        finally:
            db.close()
        
        return results
    
    def get_etf_quote_from_db(self, etf_code: str, db: Session) -> Optional[ETFQuotation]:
        """
        从数据库获取ETF最新行情
        
        Args:
            etf_code: ETF代码
            db: 数据库会话
            
        Returns:
            行情数据或None
        """
        try:
            quotation = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == etf_code
            ).order_by(ETFQuotation.trade_date.desc()).first()
            return quotation
        except Exception as e:
            logger.error(f"从数据库获取 {etf_code} 行情失败: {e}")
            return None
    
    def get_etf_history(self, etf_code: str, start_date, end_date, db: Session) -> List[ETFQuotation]:
        """
        从数据库获取ETF历史行情
        
        Args:
            etf_code: ETF代码
            start_date: 开始日期
            end_date: 结束日期
            db: 数据库会话
            
        Returns:
            行情数据列表
        """
        try:
            quotations = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == etf_code,
                ETFQuotation.trade_date >= start_date,
                ETFQuotation.trade_date <= end_date
            ).order_by(ETFQuotation.trade_date.asc()).all()
            return quotations
        except Exception as e:
            logger.error(f"从数据库获取 {etf_code} 历史行情失败: {e}")
            return []
    
    async def fetch_and_save_shanghai_market_quotes(self) -> dict:
        """
        获取上证市场所有主流ETF行情数据
        
        Returns:
            包含成功和失败数量的字典
        """
        logger.info(f"开始获取上证市场 {len(SHANGHAI_ETFS)} 个ETF行情")
        return await self.fetch_and_save_etf_quotes_batch(SHANGHAI_ETFS)
    
    async def fetch_and_save_shenzhen_market_quotes(self) -> dict:
        """
        获取深证市场所有主流ETF行情数据
        
        Returns:
            包含成功和失败数量的字典
        """
        logger.info(f"开始获取深证市场 {len(SHENZHEN_ETFS)} 个ETF行情")
        return await self.fetch_and_save_etf_quotes_batch(SHENZHEN_ETFS)
    
    async def fetch_and_save_all_market_quotes(self) -> dict:
        """
        获取上深全市场所有主流ETF行情数据
        
        Returns:
            包含成功和失败数量的字典
        """
        logger.info(f"开始获取上深全市场 {len(ALL_MAIN_ETFS)} 个ETF行情")
        return await self.fetch_and_save_etf_quotes_batch(ALL_MAIN_ETFS)
    
    def get_market_etf_quotes(self, market_type: str, db: Session) -> List[Dict[str, Any]]:
        """
        从数据库获取指定市场的所有ETF最新行情
        
        Args:
            market_type: 市场类型 ('shanghai', 'shenzhen', 'all')
            db: 数据库会话
            
        Returns:
            行情数据列表
        """
        try:
            # 选择ETF代码列表
            if market_type == "shanghai":
                etf_codes = SHANGHAI_ETFS
            elif market_type == "shenzhen":
                etf_codes = SHENZHEN_ETFS
            elif market_type == "all":
                etf_codes = ALL_MAIN_ETFS
            else:
                logger.warning(f"未知的市场类型: {market_type}")
                return []
            
            # 获取每个ETF的最新行情
            results = []
            for etf_code in etf_codes:
                # 获取ETF基础信息
                etf_basic = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
                # 获取最新行情
                quotation = db.query(ETFQuotation).filter(
                    ETFQuotation.etf_code == etf_code
                ).order_by(ETFQuotation.trade_date.desc()).first()
                
                if etf_basic and quotation:
                    results.append({
                        "etf_code": etf_code,
                        "etf_name": etf_basic.etf_name,
                        "last_price": quotation.close_price,
                        "change_rate": quotation.change_rate,
                        "volume": quotation.volume,
                        "amount": quotation.amount,
                        "trade_date": quotation.trade_date.isoformat() if quotation.trade_date else None,
                    })
            
            logger.info(f"获取 {market_type} 市场 {len(results)} 个ETF行情")
            return results
        except Exception as e:
            logger.error(f"获取 {market_type} 市场行情失败: {e}")
            return []


# 全局服务实例
_data_service: Optional[ETFDataService] = None


def get_etf_data_service() -> ETFDataService:
    """获取数据服务单例"""
    global _data_service
    if _data_service is None:
        _data_service = ETFDataService()
    return _data_service
