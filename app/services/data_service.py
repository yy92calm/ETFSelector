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


class ETFMarketManager:
    """ETF市场管理器 - 管理不同市场的ETF代码列表"""
    
    def __init__(self):
        # 常见的上证市场ETF代码
        self.shanghai_etfs = [
            "sh510050",  # 华夏上证50ETF
            "sh510300",  # 华夏沪深300ETF
            "sh510500",  # 华夏中证500ETF
            "sh510610",  # 易方达消费行业
            "sh510180",  # 上海50
            "sh511800",  # 易方达易利
        ]

        # 常见的深证市场ETF代码
        self.shenzhen_etfs = [
            "sz150018",  # 鹏华创业板
            "sz159915",  # 易方达创业板
            "sz159920",  # 小康证券300
            "sz159949",  # 华夏创业板
            "sz159935",  # 广发创业板
            "sz159999",  # 易方达创业板B
        ]

        # 所有主流ETF代码（包括上深市场）
        self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
        
        # 全市场ETF代码列表（包含所有ETF，不仅限于预定义列表）
        self.all_etfs = set(self.all_main_etfs)
    
    async def update_all_etfs_from_api(self) -> int:
        """从API更新全市场ETF列表"""
        try:
            api_client = get_api_client()
            # 获取全市场ETF列表
            all_etf_codes = await api_client.get_all_etfs_list()
            
            if all_etf_codes:
                # 更新全市场ETF列表
                for code in all_etf_codes:
                    self.all_etfs.add(code)
                
                # 同时更新市场分类
                for code in all_etf_codes:
                    if code.startswith("sh") and code not in self.shanghai_etfs:
                        self.shanghai_etfs.append(code)
                    elif code.startswith("sz") and code not in self.shenzhen_etfs:
                        self.shenzhen_etfs.append(code)
                
                # 更新all_main_etfs
                self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
                
                logger.info(f"从API更新全市场ETF列表成功，共 {len(self.all_etfs)} 个ETF")
                return len(self.all_etfs)
            else:
                logger.warning("从API获取ETF列表失败，使用数据库中的ETF列表")
                # 如果API获取失败，使用数据库中的ETF列表
                from app.db.database import SessionLocal
                db = SessionLocal()
                try:
                    etf_codes = db.query(ETFBasic.etf_code).all()
                    etf_codes = [item.etf_code for item in etf_codes]
                    
                    # 更新全市场ETF列表
                    for code in etf_codes:
                        self.all_etfs.add(code)
                    
                    # 同时更新市场分类
                    for code in etf_codes:
                        if code.startswith("sh") and code not in self.shanghai_etfs:
                            self.shanghai_etfs.append(code)
                        elif code.startswith("sz") and code not in self.shenzhen_etfs:
                            self.shenzhen_etfs.append(code)
                    
                    # 更新all_main_etfs
                    self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
                    
                    return len(self.all_etfs)
                finally:
                    db.close()
        except Exception as e:
            logger.error(f"从API更新全市场ETF列表失败: {e}")
            return len(self.all_etfs)
    
    def get_market_etfs(self, market_type: str) -> List[str]:
        """根据市场类型获取ETF代码列表"""
        if market_type == "shanghai":
            return self.shanghai_etfs
        elif market_type == "shenzhen":
            return self.shenzhen_etfs
        elif market_type == "all":
            return self.all_main_etfs
        elif market_type == "all_etfs":
            # 返回全市场ETF列表
            return list(self.all_etfs)
        else:
            return []
    
    def add_etf_to_market(self, etf_code: str, market_type: str) -> bool:
        """向指定市场添加ETF代码"""
        if market_type == "shanghai":
            if etf_code not in self.shanghai_etfs:
                self.shanghai_etfs.append(etf_code)
                self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
                self.all_etfs.add(etf_code)
                return True
        elif market_type == "shenzhen":
            if etf_code not in self.shenzhen_etfs:
                self.shenzhen_etfs.append(etf_code)
                self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
                self.all_etfs.add(etf_code)
                return True
        elif market_type == "all":
            # 对于all类型，我们将其添加到对应市场
            if etf_code.startswith("sh"):
                return self.add_etf_to_market(etf_code, "shanghai")
            elif etf_code.startswith("sz"):
                return self.add_etf_to_market(etf_code, "shenzhen")
        elif market_type == "all_etfs":
            # 添加到全市场列表
            if etf_code not in self.all_etfs:
                self.all_etfs.add(etf_code)
                # 同时添加到对应市场
                if etf_code.startswith("sh"):
                    return self.add_etf_to_market(etf_code, "shanghai")
                elif etf_code.startswith("sz"):
                    return self.add_etf_to_market(etf_code, "shenzhen")
                else:
                    # 如果不是sh或sz开头，也添加到全市场列表
                    self.all_etfs.add(etf_code)
                    return True
        return False
    
    def remove_etf_from_market(self, etf_code: str, market_type: str) -> bool:
        """从指定市场移除ETF代码"""
        if market_type == "shanghai":
            if etf_code in self.shanghai_etfs:
                self.shanghai_etfs.remove(etf_code)
                self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
                return True
        elif market_type == "shenzhen":
            if etf_code in self.shenzhen_etfs:
                self.shenzhen_etfs.remove(etf_code)
                self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
                return True
        elif market_type == "all":
            # 对于all类型，尝试从所有市场移除
            removed = False
            if etf_code in self.shanghai_etfs:
                self.shanghai_etfs.remove(etf_code)
                removed = True
            if etf_code in self.shenzhen_etfs:
                self.shenzhen_etfs.remove(etf_code)
                removed = True
            if removed:
                self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
            return removed
        elif market_type == "all_etfs":
            # 从全市场列表移除
            if etf_code in self.all_etfs:
                self.all_etfs.remove(etf_code)
                # 同时从具体市场移除
                removed = False
                if etf_code in self.shanghai_etfs:
                    self.shanghai_etfs.remove(etf_code)
                    removed = True
                if etf_code in self.shenzhen_etfs:
                    self.shenzhen_etfs.remove(etf_code)
                    removed = True
                if removed:
                    self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
                return True
        return False
    
    def add_etf_to_all_etfs(self, etf_code: str) -> bool:
        """向全市场ETF列表添加ETF代码"""
        if etf_code not in self.all_etfs:
            self.all_etfs.add(etf_code)
            return True
        return False
    
    def get_all_etfs_count(self) -> int:
        """获取全市场ETF总数"""
        return len(self.all_etfs)
    
    def update_all_etfs_from_db(self, db: Session) -> int:
        """从数据库更新全市场ETF列表"""
        try:
            # 从数据库获取所有ETF代码
            etf_codes = db.query(ETFBasic.etf_code).all()
            etf_codes = [item.etf_code for item in etf_codes]
            
            # 更新全市场ETF列表
            for code in etf_codes:
                self.all_etfs.add(code)
            
            # 同时更新市场分类
            for code in etf_codes:
                if code.startswith("sh") and code not in self.shanghai_etfs:
                    self.shanghai_etfs.append(code)
                elif code.startswith("sz") and code not in self.shenzhen_etfs:
                    self.shenzhen_etfs.append(code)
            
            # 更新all_main_etfs
            self.all_main_etfs = self.shanghai_etfs + self.shenzhen_etfs
            
            return len(self.all_etfs)
        except Exception as e:
            logger.error(f"从数据库更新全市场ETF列表失败: {e}")
            return len(self.all_etfs)


# 创建全局市场管理器实例
market_manager = ETFMarketManager()


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
            
            # 批量处理数据，减少数据库操作次数
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
            
            # 批量提交
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
        etf_codes = market_manager.get_market_etfs("shanghai")
        logger.info(f"开始获取上证市场 {len(etf_codes)} 个ETF行情")
        return await self.fetch_and_save_etf_quotes_batch(etf_codes)
    
    async def fetch_and_save_shenzhen_market_quotes(self) -> dict:
        """
        获取深证市场所有主流ETF行情数据
        
        Returns:
            包含成功和失败数量的字典
        """
        etf_codes = market_manager.get_market_etfs("shenzhen")
        logger.info(f"开始获取深证市场 {len(etf_codes)} 个ETF行情")
        return await self.fetch_and_save_etf_quotes_batch(etf_codes)
    
    async def fetch_and_save_all_market_quotes(self) -> dict:
        """
        获取上深全市场所有主流ETF行情数据
        
        Returns:
            包含成功和失败数量的字典
        """
        etf_codes = market_manager.get_market_etfs("all")
        logger.info(f"开始获取上深全市场 {len(etf_codes)} 个ETF行情")
        return await self.fetch_and_save_etf_quotes_batch(etf_codes)
    
    async def fetch_and_save_market_quotes(self, market_type: str) -> dict:
        """
        获取指定市场所有ETF行情数据并保存到数据库
        
        Args:
            market_type: 市场类型 ('shanghai', 'shenzhen', 'all')
            
        Returns:
            包含成功和失败数量的字典
        """
        etf_codes = market_manager.get_market_etfs(market_type)
        if not etf_codes:
            logger.warning(f"未知的市场类型: {market_type}")
            return {"success_count": 0, "fail_count": 0, "failed_codes": []}
        
        logger.info(f"开始获取 {market_type} 市场 {len(etf_codes)} 个ETF行情")
        return await self.fetch_and_save_etf_quotes_batch(etf_codes)

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
            etf_codes = market_manager.get_market_etfs(market_type)
            if not etf_codes:
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
