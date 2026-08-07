"""
ETF数据源配置
主数据源: Ashare（新浪+腾讯双核自动切换）
备用数据源: efinance（东方财富）
"""

import logging
import time
from typing import Optional, List
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)


def _to_ashare_code(etf_code: str) -> str:
    code = etf_code.replace('sh', '').replace('sz', '').strip()
    if code.startswith('5') or code.startswith('6'):
        return f'sh{code}'
    return f'sz{code}'


class AshareDataSource:
    """Ashare数据源（新浪+腾讯双核）"""

    def get_source_name(self) -> str:
        return "ashare"

    def is_available(self) -> bool:
        try:
            from app.services.Ashare import get_price
            return True
        except Exception:
            return False

    def fetch_etf_daily(
        self,
        etf_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        from app.services.Ashare import get_price

        code = _to_ashare_code(etf_code)

        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        start_dt = datetime.strptime(start_date, "%Y%m%d")
        end_dt = datetime.strptime(end_date, "%Y%m%d")
        count = max((end_dt - start_dt).days + 10, 30)

        try:
            df = get_price(code, end_date=end_date, count=count, frequency='1d')
        except Exception as e:
            logger.error(f"[Ashare] {etf_code} 获取行情失败: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            logger.warning(f"[Ashare] {etf_code} 未获取到行情数据")
            return pd.DataFrame()

        df = df.reset_index()
        df = df.rename(columns={df.columns[0]: 'trade_date'})

        start_filter = pd.to_datetime(start_date)
        df = df[df['trade_date'] >= start_filter]

        if df.empty:
            return pd.DataFrame()

        df['change_pct'] = df['close'].pct_change() * 100
        df['change_pct'] = df['change_pct'].fillna(0)
        df['amount'] = df['close'] * df['volume']
        df['etf_code'] = etf_code

        for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        logger.info(f"[Ashare] {etf_code} 获取 {len(df)} 条行情 ({start_date}~{end_date})")
        return df[['trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct', 'etf_code']]


class EFinanceDataSource:
    """efinance数据源（备用）"""

    def get_source_name(self) -> str:
        return "efinance"

    def is_available(self) -> bool:
        try:
            import efinance
            return True
        except Exception:
            return False

    def fetch_etf_list(self) -> pd.DataFrame:
        import efinance as ef
        try:
            df = ef.fund.get_fund_codes()
            if df.empty:
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
            etf_df['data_source'] = 'efinance'
            return etf_df[['etf_code', 'etf_name', 'data_source']]
        except Exception as e:
            logger.error(f"[efinance] 获取ETF列表失败: {e}")
            return pd.DataFrame()

    def fetch_etf_daily(
        self,
        etf_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        import efinance as ef

        if end_date is None:
            end_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

        try:
            df = ef.stock.get_quote_history(etf_code, beg=start_date, end=end_date)
            if df.empty:
                return pd.DataFrame()

            df = df.rename(columns={
                '日期': 'trade_date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume',
                '成交额': 'amount', '涨跌幅': 'change_pct',
            })
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df['etf_code'] = etf_code
            for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df[['trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct', 'etf_code']]
        except Exception as e:
            logger.error(f"[efinance] {etf_code} 获取行情失败: {e}")
            return pd.DataFrame()


class DataSourceManager:
    """数据源管理器：Ashare(新浪+腾讯) → efinance(东方财富) 自动降级"""

    def __init__(self, ashare_only: bool = False):
        """
        Args:
            ashare_only: True=仅使用Ashare（定时任务场景），False=允许降级到efinance
        """
        self.primary = AshareDataSource()
        self.fallback = EFinanceDataSource()
        self.ashare_only = ashare_only
        if ashare_only:
            logger.info("✓ 数据源已加载: Ashare (定时任务模式，不降级)")
        else:
            logger.info("✓ 数据源已加载: Ashare(主) + efinance(备)")

    def fetch_etf_list(self) -> pd.DataFrame:
        return self.fallback.fetch_etf_list()

    def fetch_etf_daily(
        self,
        etf_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        df = self.primary.fetch_etf_daily(etf_code, start_date, end_date)
        if not df.empty:
            return df

        # ashare_only 模式下不降级
        if self.ashare_only:
            logger.warning(f"[DataSource] Ashare失败，定时任务模式不降级: {etf_code}")
            return pd.DataFrame()

        logger.warning(f"[DataSource] Ashare失败，降级到efinance: {etf_code}")
        return self.fallback.fetch_etf_daily(etf_code, start_date, end_date)

    def get_available_sources(self) -> List[str]:
        return ["ashare", "efinance"]


_manager: Optional[DataSourceManager] = None


def get_data_source_manager() -> DataSourceManager:
    global _manager
    if _manager is None:
        _manager = DataSourceManager()
    return _manager
