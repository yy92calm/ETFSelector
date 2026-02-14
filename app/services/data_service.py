"""
AKShare 数据获取服务
负责从AKShare获取全市场ETF列表和日K线行情
"""

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict

import akshare as ak
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFBasic, ETFQuotation
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)


class DataService:
    """ETF数据获取与存储服务"""

    # ------------------------------------------------------------------ #
    #  全市场 ETF 列表
    # ------------------------------------------------------------------ #
    def fetch_etf_list(self) -> pd.DataFrame:
        """
        从AKShare获取全市场ETF列表
        返回DataFrame包含: symbol, name 等字段
        """
        try:
            # 获取上交所ETF
            df_sh = ak.fund_etf_spot_em()
            logger.info(f"获取到 {len(df_sh)} 条ETF记录")
            return df_sh
        except Exception as e:
            logger.error(f"获取ETF列表失败: {e}")
            return pd.DataFrame()

    def sync_etf_list(self, db: Session) -> int:
        """将全市场ETF列表同步到数据库，返回新增/更新数量"""
        df = self.fetch_etf_list()
        if df.empty:
            return 0

        count = 0
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))
            if not code:
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
        logger.info(f"ETF列表同步完成，新增/更新 {count} 条")
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
        获取单只ETF的日K线数据
        etf_code: 纯数字代码，如 510050
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        try:
            df = ak.fund_etf_hist_em(
                symbol=etf_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",  # 前复权
            )
            logger.info(
                f"获取 {etf_code} 日K线 {len(df)} 条 ({start_date} ~ {end_date})"
            )
            return df
        except Exception as e:
            logger.error(f"获取 {etf_code} 日K线失败: {e}")
            return pd.DataFrame()

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
            trade_date = pd.to_datetime(row["日期"]).date()
            if trade_date in existing_dates:
                continue
            db.add(
                ETFQuotation(
                    etf_code=etf_code,
                    trade_date=trade_date,
                    open_price=float(row.get("开盘", 0)),
                    close_price=float(row.get("收盘", 0)),
                    high_price=float(row.get("最高", 0)),
                    low_price=float(row.get("最低", 0)),
                    volume=float(row.get("成交量", 0)),
                    amount=float(row.get("成交额", 0)),
                    change_pct=float(row.get("涨跌幅", 0)),
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
        获取全市场ETF最新交易日行情并存储
        返回 {success_count, fail_count, failed_codes}
        """
        # 先同步ETF列表
        self.sync_etf_list(db)

        # 获取数据库中所有ETF代码
        etf_codes = [r[0] for r in db.query(ETFBasic.etf_code).all()]

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
            "target_dates": target_dates,
        }

        for code in etf_codes:  # 更新所有ETF的行情
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
                except Exception as e:
                    logger.debug(f"更新 {code} {target_date} 行情失败: {e}")
                    continue

            if not success:
                result["fail_count"] += 1
                result["failed_codes"].append(code)

        logger.info(f"最新交易日行情更新完成: {result}")
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

    def get_market_overview(self, db: Session, limit: int = 500) -> List[dict]:
        """获取全市场最新行情概览（包含所有ETF，无论是否有行情数据）"""
        # 找到最新交易日
        latest_date = db.query(func.max(ETFQuotation.trade_date)).scalar()

        # 获取所有ETF基础信息
        all_etfs = db.query(ETFBasic).all()

        # 如果有行情数据，获取最新交易日的行情
        latest_quotes = {}
        if latest_date:
            quote_rows = (
                db.query(ETFQuotation)
                .filter(ETFQuotation.trade_date == latest_date)
                .all()
            )
            for q in quote_rows:
                latest_quotes[q.etf_code] = q

        # 构建结果列表（所有ETF都包含）
        result = []
        for etf in all_etfs:
            quote = latest_quotes.get(etf.etf_code)
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
