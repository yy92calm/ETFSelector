"""
ETF数据获取服务
所有行情数据通过 efinance 获取
"""

import logging
import random
import time
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFBasic, ETFQuotation
from app.db.database import SessionLocal
from app.services.data_sources import DataSourceManager

logger = logging.getLogger(__name__)

# 请求限流间隔（秒），随机化防止被数据源封禁
REQUEST_INTERVAL_MIN = 2.0
REQUEST_INTERVAL_MAX = 5.0


def _random_sleep():
    time.sleep(random.uniform(REQUEST_INTERVAL_MIN, REQUEST_INTERVAL_MAX))


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
        将ETF列表同步到数据库，返回新增/更新数量。
        同步全市场ETF（不再限制基金公司），确保LLM自主发现的标的也能被覆盖。
        """
        df = self.fetch_etf_list()
        if df.empty:
            logger.warning("未获取到ETF列表数据")
            return 0

        logger.info(f"准备同步 {len(df)} 只ETF")

        count = 0
        for _, row in df.iterrows():
            code = str(row.get("etf_code", "") or row.get("代码", ""))
            name = str(row.get("etf_name", "") or row.get("名称", ""))

            if not code or not name:
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
        logger.info(f"ETF列表同步完成，新增/更新 {count} 条（共 {len(df)} 只）")
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
        """将日K线DataFrame存入数据库，自动去重+数据质量校验，返回新增数量"""
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
        skipped = 0
        for _, row in df.iterrows():
            # 支持不同数据源的列名
            trade_date_col = row.get("trade_date") or row.get("日期")
            if not trade_date_col:
                continue
                
            trade_date = pd.to_datetime(trade_date_col).date()
            if trade_date in existing_dates:
                continue

            close_price = float(row.get("close") or row.get("收盘", 0))
            open_price = float(row.get("open") or row.get("开盘", 0))
            high_price = float(row.get("high") or row.get("最高", 0))
            low_price = float(row.get("low") or row.get("最低", 0))
            volume = float(row.get("volume") or row.get("成交量", 0))
            amount = float(row.get("amount") or row.get("成交额", 0))
            change_pct = float(row.get("change_pct") or row.get("涨跌幅", 0))

            # 数据质量校验：跳过价格异常数据
            if close_price <= 0 or open_price <= 0:
                skipped += 1
                continue
            if high_price > 0 and low_price > 0 and high_price < low_price:
                skipped += 1
                continue

            db.add(
                ETFQuotation(
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
            )
            count += 1

        db.commit()
        if skipped > 0:
            logger.warning(f"{etf_code} 跳过 {skipped} 条异常数据")
        logger.info(f"{etf_code} 新增 {count} 条日K线")
        return count

    # ------------------------------------------------------------------ #
    #  批量更新当日行情（定时任务用）
    # ------------------------------------------------------------------ #
    def update_today_quotes(self, db: Session) -> Dict:
        """
        获取数据库中所有ETF的最新交易日行情并存储。
        覆盖 etf_basic 表中全部ETF（含LLM自主纳入的标的）。
        
        返回 {success_count, fail_count, failed_codes, etf_count}
        """
        # 获取数据库中所有ETF
        all_etfs = db.query(ETFBasic).all()
        etf_codes = [etf.etf_code for etf in all_etfs]
        
        logger.info(f"开始更新 {len(etf_codes)} 只ETF的最新行情")
        
        if not etf_codes:
            logger.warning("数据库中没有ETF")
            return {
                "success_count": 0,
                "fail_count": 0,
                "failed_codes": [],
                "etf_count": 0,
                "message": "数据库中没有ETF"
            }
        
        # 计算最近的交易日（向后查找最近7天）
        today = datetime.now()
        target_dates = []
        for i in range(7):
            check_date = today - timedelta(days=i)
            if check_date.weekday() < 5:
                target_dates.append(check_date.strftime("%Y%m%d"))
        
        result = {
            "success_count": 0,
            "fail_count": 0,
            "failed_codes": [],
            "etf_count": len(etf_codes),
            "target_dates": target_dates,
        }
        
        for idx, code in enumerate(etf_codes):
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
                            break
                    else:
                        logger.debug(f"{code} {target_date} 未获取到数据")
                except Exception as e:
                    logger.debug(f"更新 {code} {target_date} 行情失败: {e}")
                    continue
            
            if not success:
                result["fail_count"] += 1
                result["failed_codes"].append(code)

            _random_sleep()
        
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
        self, start_date: str, end_date: str, db: Session,
        limit: int = 0, offset: int = 0,
    ) -> Dict:
        """
        批量更新指定日期范围内所有ETF的行情数据。
        覆盖 etf_basic 表中全部ETF。
        
        start_date/end_date: 格式 YYYYMMDD
        limit: 本次处理数量上限（0=全部）
        offset: 跳过前N只
        返回: {success_count, fail_count, etf_count, failed_codes}
        """
        all_etfs = db.query(ETFBasic).order_by(ETFBasic.etf_code).all()
        etf_codes = [etf.etf_code for etf in all_etfs]
        total = len(etf_codes)

        if offset > 0:
            etf_codes = etf_codes[offset:]
        if limit > 0:
            etf_codes = etf_codes[:limit]
        
        result = {
            "success_count": 0,
            "fail_count": 0,
            "etf_count": len(etf_codes),
            "total_in_db": total,
            "date_range": f"{start_date}~{end_date}",
            "failed_codes": [],
        }
        
        if not etf_codes:
            logger.warning("数据库中没有ETF")
            return result
        
        logger.info(f"开始更新 {len(etf_codes)} 只ETF {start_date}~{end_date} 的行情数据 (offset={offset})")
        
        consecutive_fails = 0
        for idx, code in enumerate(etf_codes):
            try:
                df = self.fetch_etf_daily(code, start_date=start_date, end_date=end_date)
                if not df.empty:
                    added = self.save_daily_quotes(code, df, db)
                    result["success_count"] += 1
                    consecutive_fails = 0
                    if added > 0:
                        logger.info(f"✓ {code} 新增 {added} 条行情数据")
                else:
                    result["fail_count"] += 1
                    result["failed_codes"].append(code)
                    consecutive_fails += 1
            except Exception as e:
                result["fail_count"] += 1
                result["failed_codes"].append(code)
                consecutive_fails += 1
                logger.error(f"✗ {code} 更新失败: {e}")

            if consecutive_fails >= 10:
                logger.warning("连续失败10次，暂停60秒后继续")
                time.sleep(60)
                consecutive_fails = 0

            _random_sleep()
        
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
        
        交易时段内（9:30-15:00）自动显示T-1数据，闭市后显示T日。
        """
        from app.utils.trading_calendar import is_during_trading_hours, get_previous_trading_day

        # 确定查询日期
        if date:
            # 解析指定日期
            try:
                target_date = datetime.strptime(date, '%Y-%m-%d').date()
            except ValueError:
                logger.warning(f"日期格式错误: {date}, 使用最新交易日")
                target_date = db.query(func.max(ETFQuotation.trade_date)).scalar()
        elif is_during_trading_hours():
            # 交易时段内显示T-1
            target_date = get_previous_trading_day(datetime.now().date())
        else:
            # 闭市后使用最新交易日
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
