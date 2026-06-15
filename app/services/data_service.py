"""
ETF数据获取服务
所有行情数据通过 efinance 获取
"""

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFBasic, ETFQuotation
from app.db.database import SessionLocal
from app.services.data_sources import DataSourceManager

logger = logging.getLogger(__name__)


class DataService:
    """ETF数据获取与存储服务"""
    
    def __init__(self):
        self.data_source = DataSourceManager()

    # ------------------------------------------------------------------ #
    #  ETF 列表（广发、易方达、华夏）
    # ------------------------------------------------------------------ #
    def fetch_etf_list(self) -> pd.DataFrame:
        """
        从efinance获取ETF列表
        返回DataFrame包含: etf_code, etf_name 等字段
        """
        df = self.data_source.fetch_etf_list()
        if df.empty:
            logger.warning("未获取到ETF列表数据")
        return df

    def sync_etf_list(self, db: Session) -> int:
        """
        将ETF列表同步到数据库，返回新增/更新数量
        
        只同步以下基金公司的ETF：
        - 广发基金
        - 易方达基金  
        - 华夏基金
        """
        df = self.fetch_etf_list()
        if df.empty:
            logger.warning("未获取到ETF列表数据")
            return 0
        
        # 只保留三家基金公司的ETF
        target_funds = ['广发', '易方达', '华夏']
        filtered_df = df[df['etf_name'].apply(lambda name: any(fund in str(name) for fund in target_funds))]
        
        if filtered_df.empty:
            logger.warning(f"过滤后无目标基金公司ETF（{target_funds}），请检查数据源")
            return 0
        
        logger.info(f"准备同步 {len(filtered_df)} 只ETF（广发/易方达/华夏）")
        
        count = 0
        for _, row in filtered_df.iterrows():
            # 支持不同数据源的列名
            code = str(row.get("etf_code", "") or row.get("代码", ""))
            name = str(row.get("etf_name", "") or row.get("名称", ""))
            
            if not code or not name:
                continue
            
            # 验证是否为目标基金公司（防御性编程）
            is_target = any(fund in name for fund in target_funds)
            if not is_target:
                logger.debug(f"跳过非目标基金ETF: {code} {name}")
                continue
            
            existing = db.query(ETFBasic).filter(ETFBasic.etf_code == code).first()
            if existing:
                if existing.etf_name != name:
                    existing.etf_name = name
                    existing.update_time = datetime.utcnow()
                    count += 1
            else:
                db.add(ETFBasic(etf_code=code, etf_name=name))
                count += 1
        
        db.commit()
        logger.info(f"ETF列表同步完成，新增/更新 {count} 条（共 {len(filtered_df)} 只）")
        return count

    # ------------------------------------------------------------------ #
    #  获取日 K 线行情
    # ------------------------------------------------------------------ #
    def fetch_etf_daily(
        self,
        etf_code: str,
        start_date: str = "20200101",
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        从efinance获取ETF真实日K线数据
        
        Args:
            etf_code: ETF代码，如 510300, 159915
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD（默认今天）
        
        Returns:
            DataFrame: 日K线数据
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        
        df = self.data_source.fetch_etf_daily(etf_code, start_date, end_date)
        
        if df.empty:
            logger.warning(f"{etf_code} efinance未获取到数据")
        
        return df
    
    def _is_gf_etf(self, etf_code: str) -> bool:
        """判断是否为广发基金ETF"""
        code = etf_code.replace('sh', '').replace('sz', '').strip()
        return code.startswith('5') or code.startswith('15')

    def save_daily_quotes(self, etf_code: str, df: pd.DataFrame, db: Session) -> int:
        """将日K线DataFrame存入数据库，自动去重，返回新增数量"""
        if df.empty:
            return 0

        # 查已有日期
        existing_dates = set(
            r[0]
            for r in db.query(ETFQuotation.trade_date)
            .filter(ETFQuotation.etf_code == etf_code)
            .all()
        )

        count = 0
        for _, row in df.iterrows():
            # 支持不同数据源的列名
            trade_date_col = row.get("trade_date") or row.get("日期")
            if not trade_date_col:
                continue
                
            trade_date = pd.to_datetime(trade_date_col).date()
            if trade_date in existing_dates:
                continue
            
            db.add(
                ETFQuotation(
                    etf_code=etf_code,
                    trade_date=trade_date,
                    open_price=float(row.get("open") or row.get("开盘", 0)),
                    close_price=float(row.get("close") or row.get("收盘", 0)),
                    high_price=float(row.get("high") or row.get("最高", 0)),
                    low_price=float(row.get("low") or row.get("最低", 0)),
                    volume=float(row.get("volume") or row.get("成交量", 0)),
                    amount=float(row.get("amount") or row.get("成交额", 0)),
                    change_pct=float(row.get("change_pct") or row.get("涨跌幅", 0)),
                )
            )
            count += 1

        db.commit()
        logger.info(f"{etf_code} 新增 {count} 条日K线")
        return count

    # ------------------------------------------------------------------ #
    #  批量更新当日行情（定时任务用）
    # ------------------------------------------------------------------ #
    def update_today_quotes(self, db: Session) -> Dict:
        """
        获取ETF最新交易日行情并存储
        使用efinance接口
        
        返回 {success_count, fail_count, failed_codes, etf_count}
        """
        # 先同步广发基金ETF列表
        self.sync_etf_list(db)
        
        # 获取数据库中所有广发基金ETF代码（名称包含'广发'）
        gf_etfs = db.query(ETFBasic).filter(
            ETFBasic.etf_name.contains('广发')
        ).all()
        
        etf_codes = [etf.etf_code for etf in gf_etfs]
        
        logger.info(f"开始更新 {len(etf_codes)} 只ETF的最新行情")
        
        if not etf_codes:
            logger.warning("数据库中没有ETF")
            return {
                "success_count": 0,
                "fail_count": 0,
                "failed_codes": [],
                "gf_etf_count": 0,
                "message": "数据库中没有ETF"
            }
        
        # 计算最近的交易日（向后查找最近7天）
        today = datetime.now()
        target_dates = []
        for i in range(7):
            check_date = today - timedelta(days=i)
            # 排除周末
            if check_date.weekday() < 5:  # 0-4 表示周一到周五
                target_dates.append(check_date.strftime("%Y%m%d"))
        
        result = {
            "success_count": 0,
            "fail_count": 0,
            "failed_codes": [],
            "gf_etf_count": len(etf_codes),
            "target_dates": target_dates,
        }
        
        for code in etf_codes:
            success = False
            for target_date in target_dates:
                try:
                    df = self.fetch_etf_daily(
                        code, start_date=target_date, end_date=target_date
                    )
                    if not df.empty:
                        added = self.save_daily_quotes(code, df, db)
                        if added > 0:
                            result["success_count"] += 1
                            success = True
                            logger.info(f"✓ {code} 更新成功，新增 {added} 条")
                            break
                    else:
                        logger.debug(f"{code} {target_date} 未获取到数据")
                except Exception as e:
                    logger.debug(f"更新 {code} {target_date} 行情失败: {e}")
                    continue
            
            if not success:
                result["fail_count"] += 1
                result["failed_codes"].append(code)
                logger.warning(f"✗ {code} 更新失败")
        
        logger.info(f"ETF行情更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}")
        return result

    # ------------------------------------------------------------------ #
    #  补全历史数据
    # ------------------------------------------------------------------ #
    def backfill_history(self, etf_code: str, start_date: str, db: Session) -> int:
        """补全某ETF从start_date到今天的历史行情"""
        end_date = datetime.now().strftime("%Y%m%d")
        df = self.fetch_etf_daily(etf_code, start_date=start_date, end_date=end_date)
        return self.save_daily_quotes(etf_code, df, db)

    def initialize_sample_data(self, db: Session) -> Dict:
        """
        初始化热门ETF的样本数据（最近5个交易日）
        用于演示和测试
        """
        # 选择热门ETF代码
        popular_etfs = [
            "510300",
            "159915",
            "510050",
            "510500",
            "159919",
            "512170",
            "512690",
            "515000",
        ]

        # 计算最近的5个交易日
        end_date = datetime.now()
        valid_dates = []
        for i in range(10):  # 往前查找10天，确保有5个交易日
            check_date = end_date - timedelta(days=i)
            if check_date.weekday() < 5:  # 工作日
                valid_dates.append(check_date)
            if len(valid_dates) >= 5:
                break

        if not valid_dates:
            return {"success": 0, "failed": len(popular_etfs)}

        start_date = min(valid_dates).strftime("%Y%m%d")
        end_date = max(valid_dates).strftime("%Y%m%d")

        result = {"success": 0, "failed": 0, "dates": f"{start_date}~{end_date}"}

        for code in popular_etfs:
            try:
                df = self.fetch_etf_daily(
                    code, start_date=start_date, end_date=end_date
                )
                if not df.empty:
                    added = self.save_daily_quotes(code, df, db)
                    if added > 0:
                        result["success"] += 1
                        logger.info(f"初始化 {code} 数据：{added} 条记录")
                else:
                    result["failed"] += 1
                    logger.warning(f"未获取到 {code} 的数据")
            except Exception as e:
                result["failed"] += 1
                logger.error(f"初始化 {code} 数据失败: {e}")

        logger.info(f"样本数据初始化完成: {result}")
        return result

    # ------------------------------------------------------------------ #
    #  批量更新指定时间范围行情
    # ------------------------------------------------------------------ #
    def update_quotes_by_date_range(
        self, start_date: str, end_date: str, db: Session
    ) -> Dict:
        """
        批量更新指定日期范围内ETF的行情数据
        使用efinance接口
        
        start_date/end_date: 格式 YYYYMMDD
        返回: {success_count, fail_count, etf_count, failed_codes}
        """
        # 获取所有广发基金ETF代码（名称包含'广发'）
        gf_etfs = db.query(ETFBasic).filter(
            ETFBasic.etf_name.contains('广发')
        ).all()
        
        etf_codes = [etf.etf_code for etf in gf_etfs]
        
        result = {
            "success_count": 0,
            "fail_count": 0,
            "gf_etf_count": len(etf_codes),
            "date_range": f"{start_date}~{end_date}",
            "failed_codes": [],
        }
        
        if not etf_codes:
            logger.warning("数据库中没有广发基金ETF")
            return result
        
        logger.info(f"开始更新 {len(etf_codes)} 只ETF {start_date}~{end_date} 的行情数据")
        
        for code in etf_codes:
            try:
                df = self.fetch_etf_daily(code, start_date=start_date, end_date=end_date)
                if not df.empty:
                    added = self.save_daily_quotes(code, df, db)
                    if added > 0:
                        result["success_count"] += 1
                        logger.info(f"✓ {code} 新增 {added} 条行情数据")
                    else:
                        result["success_count"] += 1  # 数据已存在也算成功
                        logger.debug(f"{code} 数据已存在")
                else:
                    result["fail_count"] += 1
                    result["failed_codes"].append(code)
                    logger.warning(f"✗ {code} 未获取到数据")
            except Exception as e:
                result["fail_count"] += 1
                result["failed_codes"].append(code)
                logger.error(f"✗ {code} 更新失败: {e}")
        
        logger.info(f"ETF批量更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}")
        return result

    # ------------------------------------------------------------------ #
    #  查询接口
    # ------------------------------------------------------------------ #
    def get_etf_list(self, db: Session) -> List[ETFBasic]:
        return db.query(ETFBasic).order_by(ETFBasic.etf_code).all()

    def get_latest_quote(self, etf_code: str, db: Session) -> Optional[ETFQuotation]:
        return (
            db.query(ETFQuotation)
            .filter(ETFQuotation.etf_code == etf_code)
            .order_by(ETFQuotation.trade_date.desc())
            .first()
        )

    def get_history(
        self, etf_code: str, start_date: date, end_date: date, db: Session
    ) -> List[ETFQuotation]:
        return (
            db.query(ETFQuotation)
            .filter(
                ETFQuotation.etf_code == etf_code,
                ETFQuotation.trade_date >= start_date,
                ETFQuotation.trade_date <= end_date,
            )
            .order_by(ETFQuotation.trade_date.asc())
            .all()
        )

    def get_market_overview(self, db: Session, limit: int = 500, date: Optional[str] = None) -> List[dict]:
        """
        获取全市场行情概览（包含所有ETF，无论是否有行情数据）
        
        Args:
            db: 数据库会话
            limit: 返回数量限制
            date: 指定日期 YYYY-MM-DD，不指定则返回最新交易日
        
        Returns:
            List[dict]: ETF行情列表
        """
        # 确定查询日期
        if date:
            # 解析指定日期
            try:
                target_date = datetime.strptime(date, '%Y-%m-%d').date()
            except ValueError:
                logger.warning(f"日期格式错误: {date}, 使用最新交易日")
                target_date = db.query(func.max(ETFQuotation.trade_date)).scalar()
        else:
            # 使用最新交易日
            target_date = db.query(func.max(ETFQuotation.trade_date)).scalar()
        
        # 获取所有ETF基础信息
        all_etfs = db.query(ETFBasic).all()
        
        # 如果有行情数据，获取指定日期的行情
        target_quotes = {}
        if target_date:
            quote_rows = (
                db.query(ETFQuotation)
                .filter(ETFQuotation.trade_date == target_date)
                .all()
            )
            for q in quote_rows:
                target_quotes[q.etf_code] = q
        
        # 构建结果列表（所有ETF都包含）
        result = []
        for etf in all_etfs:
            quote = target_quotes.get(etf.etf_code)
            result.append({
                "etf_code": etf.etf_code,
                "etf_name": etf.etf_name,
                "close_price": quote.close_price if quote else None,
                "change_pct": quote.change_pct if quote else None,
                "volume": quote.volume if quote else None,
                "amount": quote.amount if quote else 0,  # 无行情时金额为0，用于排序
                "trade_date": quote.trade_date.isoformat() if quote else None,
                "has_quote": quote is not None,  # 标记是否有行情数据
            })
        
        # 按成交额降序排列（有行情的在前，无行情的在后）
        result.sort(key=lambda x: x["amount"] if x["amount"] else 0, reverse=True)
        
        return result[:limit]


# 单例
_service: Optional[DataService] = None


def get_data_service() -> DataService:
    global _service
    if _service is None:
        _service = DataService()
    return _service
