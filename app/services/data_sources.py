"""
ETF数据源配置
使用efinance作为唯一数据源
"""

import logging
import time
from typing import Optional, List
from datetime import datetime, timedelta
import pandas as pd
import efinance as ef

logger = logging.getLogger(__name__)


class EFinanceDataSource:
    """efinance数据源"""

    def __init__(self):
        self._codes_df = None

    def get_source_name(self) -> str:
        return "efinance"

    def is_available(self) -> bool:
        try:
            import efinance
            return True
        except Exception:
            return False

    def fetch_etf_list(self) -> pd.DataFrame:
        """
        获取ETF列表（广发、易方达、华夏三家基金公司）
        efinance 提供全市场基金代码表，按规则过滤出 ETF
        """
        try:
            df = ef.fund.get_fund_codes()
            if df.empty:
                logger.warning("[efinance] 未获取到基金列表")
                return pd.DataFrame()

            is_etf = (
                df['基金代码'].str.match(r'^159\d{3}$')
                | (
                    df['基金代码'].str.match(r'^5[168]\d{4}$')
                    & df['基金简称'].str.contains('ETF', na=False)
                )
            )
            etf_df = df[is_etf].copy().rename(columns={
                '基金代码': 'etf_code',
                '基金简称': 'etf_name'
            })

            target_funds = ['广发', '易方达', '华夏']
            filtered_df = etf_df[etf_df['etf_name'].apply(
                lambda name: any(fund in str(name) for fund in target_funds)
            )].copy()

            logger.info(
                f"[efinance] 全市场基金 {len(df)} 只, "
                f"ETF {len(etf_df)} 只, "
                f"目标基金 {len(filtered_df)} 只"
            )

            if filtered_df.empty:
                logger.warning(f"[efinance] 未找到目标基金公司ETF（{target_funds}）")
                return pd.DataFrame()

            filtered_df['data_source'] = 'efinance'
            return filtered_df[['etf_code', 'etf_name', 'data_source']]

        except Exception as e:
            logger.error(f"[efinance] 获取ETF列表失败: {e}")
            return pd.DataFrame()

    def fetch_etf_daily(
        self,
        etf_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """获取ETF日K线数据（efinance 东方财富行情）"""
        if end_date is None:
            end_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

        try:
            df = ef.stock.get_quote_history(
                etf_code,
                beg=start_date,
                end=end_date,
            )
            if df.empty:
                logger.warning(f"[efinance] {etf_code} 未获取到行情数据")
                return pd.DataFrame()

            # 标准化列名
            df = df.rename(columns={
                '日期': 'trade_date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '涨跌幅': 'change_pct',
            })

            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df['etf_code'] = etf_code

            for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            logger.info(f"[efinance] {etf_code} 获取 {len(df)} 条行情 ({start_date}~{end_date})")
            return df[['trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct', 'etf_code']]

        except Exception as e:
            logger.error(f"[efinance] {etf_code} 获取行情失败: {e}")
            return pd.DataFrame()


class DataSourceManager:
    """数据源管理器（仅使用 efinance）"""

    def __init__(self):
        self.source = EFinanceDataSource()
        logger.info("✓ efinance 数据源已加载")

    def fetch_etf_list(self) -> pd.DataFrame:
        return self.source.fetch_etf_list()

    def fetch_etf_daily(
        self,
        etf_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        return self.source.fetch_etf_daily(etf_code, start_date, end_date)

    def get_available_sources(self) -> List[str]:
        return ["efinance"]


# 单例
_manager: Optional[DataSourceManager] = None


def get_data_source_manager() -> DataSourceManager:
    global _manager
    if _manager is None:
        _manager = DataSourceManager()
    return _manager
