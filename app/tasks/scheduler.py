"""
定时任务调度器
用于定期更新ETF行情数据
"""

import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.data_service import get_etf_data_service, market_manager
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)


class ETFScheduler:
    """ETF数据更新调度器"""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.data_service = get_etf_data_service()
    
    async def update_shanghai_market_quotes(self):
        """更新上证市场ETF行情"""
        logger.info("开始定时更新上证市场ETF行情")
        try:
            result = await self.data_service.fetch_and_save_shanghai_market_quotes()
            logger.info(f"上证市场ETF行情更新完成: {result}")
        except Exception as e:
            logger.error(f"上证市场ETF行情更新失败: {e}")
    
    async def update_shenzhen_market_quotes(self):
        """更新深证市场ETF行情"""
        logger.info("开始定时更新深证市场ETF行情")
        try:
            result = await self.data_service.fetch_and_save_shenzhen_market_quotes()
            logger.info(f"深证市场ETF行情更新完成: {result}")
        except Exception as e:
            logger.error(f"深证市场ETF行情更新失败: {e}")
    
    async def update_all_market_quotes(self):
        """更新全市场ETF行情"""
        logger.info("开始定时更新全市场ETF行情")
        try:
            result = await self.data_service.fetch_and_save_all_market_quotes()
            logger.info(f"全市场ETF行情更新完成: {result}")
        except Exception as e:
            logger.error(f"全市场ETF行情更新失败: {e}")
    
    async def update_market_quotes(self, market_type: str):
        """更新指定市场ETF行情"""
        logger.info(f"开始定时更新{market_type}市场ETF行情")
        try:
            result = await self.data_service.fetch_and_save_market_quotes(market_type)
            logger.info(f"{market_type}市场ETF行情更新完成: {result}")
        except Exception as e:
            logger.error(f"{market_type}市场ETF行情更新失败: {e}")
    
    async def update_all_etfs_list(self):
        """更新全市场ETF列表"""
        logger.info("开始定时更新全市场ETF列表")
        try:
            count = await market_manager.update_all_etfs_from_api()
            logger.info(f"全市场ETF列表更新完成，共 {count} 个ETF")
        except Exception as e:
            logger.error(f"全市场ETF列表更新失败: {e}")
    
    def start(self):
        """启动调度器"""
        # 工作日的交易时间定时更新
        # 上午9:30开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=9, minute=30, day_of_week='mon-fri'),
            id='update_all_quotes_930'
        )
        
        # 上午10:00开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=10, minute=0, day_of_week='mon-fri'),
            id='update_all_quotes_1000'
        )
        
        # 上午10:30开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=10, minute=30, day_of_week='mon-fri'),
            id='update_all_quotes_1030'
        )
        
        # 上午11:00开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=11, minute=0, day_of_week='mon-fri'),
            id='update_all_quotes_1100'
        )
        
        # 下午1:00开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=13, minute=0, day_of_week='mon-fri'),
            id='update_all_quotes_1300'
        )
        
        # 下午1:30开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=13, minute=30, day_of_week='mon-fri'),
            id='update_all_quotes_1330'
        )
        
        # 下午2:00开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=14, minute=0, day_of_week='mon-fri'),
            id='update_all_quotes_1400'
        )
        
        # 下午2:30开始，每30分钟更新一次
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=14, minute=30, day_of_week='mon-fri'),
            id='update_all_quotes_1430'
        )
        
        # 下午3:00更新收盘数据
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=15, minute=0, day_of_week='mon-fri'),
            id='update_all_quotes_1500'
        )
        
        # 每天早上8:30更新一次基础数据
        self.scheduler.add_job(
            self.update_all_market_quotes,
            CronTrigger(hour=8, minute=30, day_of_week='mon-fri'),
            id='update_all_quotes_830'
        )
        
        # 每天早上9:00更新一次全市场ETF列表
        self.scheduler.add_job(
            self.update_all_etfs_list,
            CronTrigger(hour=9, minute=0, day_of_week='mon-fri'),
            id='update_all_etfs_list'
        )
        
        self.scheduler.start()
        logger.info("ETF行情更新调度器已启动")
    
    def shutdown(self):
        """关闭调度器"""
        self.scheduler.shutdown()
        logger.info("ETF行情更新调度器已关闭")


# 全局调度器实例
_scheduler: ETFScheduler = None


def get_etf_scheduler() -> ETFScheduler:
    """获取调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = ETFScheduler()
    return _scheduler
