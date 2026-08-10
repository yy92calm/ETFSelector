"""
ETF数据源配置
主数据源: Ashare（新浪+腾讯双核自动切换）+ 天天基金静态列表
备用数据源: efinance（东方财富）

防封容错（数据源层通用）：
- 熔断：连续失败超过阈值后停用该数据源一段时间，避免反复打被封 IP
- 指数退避重试：单次拉取失败按 base*2^attempt 退避重试
- UA/headers 伪装：启动时注入 efinance 共享 Session 的浏览器请求头
- 列表本地缓存：ETF 列表失败时用缓存兜底，不硬拉
"""

import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Callable

import pandas as pd

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


def _to_ashare_code(etf_code: str) -> str:
    code = etf_code.replace('sh', '').replace('sz', '').strip()
    if code.startswith('5') or code.startswith('6'):
        return f'sh{code}'
    return f'sz{code}'


def _inject_efinance_headers() -> None:
    """给 efinance 共享 Session 注入浏览器 UA/Referer，降低被风控概率。

    efinance 的 fund/stock 共用 efinance.shared.session 对象，模块加载后注入一次即可。
    """
    try:
        from efinance.shared import session
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://fund.eastmoney.com/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })
        logger.info("✓ 已注入 efinance 请求头伪装")
    except Exception as e:
        logger.warning(f"注入 efinance 请求头失败: {e}")


class _SourceCircuit:
    """单数据源熔断器：连续失败计数 + 熔断时段"""

    def __init__(self, max_failures: int = 5, break_seconds: int = 600):
        self.max_failures = max_failures
        self.break_seconds = break_seconds
        self._consecutive_failures = 0
        self._open_until = 0.0

    def is_open(self) -> bool:
        """熔断打开则直接拒绝调用（避免反复打被封 IP）"""
        now = time.time()
        if now < self._open_until:
            return True
        if self._consecutive_failures >= self.max_failures:
            self._open_until = now + self.break_seconds
            self._consecutive_failures = 0
            logger.warning(
                f"数据源连续失败 {self.max_failures} 次，熔断 {self.break_seconds}s")
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1


def _call_with_retry(fetch_fn: Callable[[], pd.DataFrame], circuit: Optional[_SourceCircuit] = None,
                     retries: Optional[int] = None) -> pd.DataFrame:
    """指数退避重试：空 DataFrame 视为失败；重试间隔 base*2^attempt + 随机抖动"""
    if retries is None:
        retries = settings.data_source_retries
    base = settings.data_source_retry_base

    for attempt in range(retries + 1):
        if circuit is not None and circuit.is_open():
            return pd.DataFrame()
        try:
            df = fetch_fn()
            if df is None or df.empty:
                raise ValueError("空数据")
            if circuit is not None:
                circuit.record_success()
            return df
        except Exception as e:
            if circuit is not None:
                circuit.record_failure()
            if attempt >= retries:
                logger.warning(f"数据源重试耗尽（{attempt+1} 次）: {e}")
                return pd.DataFrame()
            delay = min(base * (2 ** attempt), 10) + random.uniform(0, 1)
            logger.debug(f"数据源失败，{delay:.1f}s 后重试 ({attempt+1}/{retries})")
            time.sleep(delay)
    return pd.DataFrame()


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

        def _fetch():
            df = get_price(code, end_date=end_date, count=count, frequency='1d')
            if df is None or df.empty:
                raise ValueError("空数据")

            df = df.reset_index()
            df = df.rename(columns={df.columns[0]: 'trade_date'})

            start_filter = pd.to_datetime(start_date)
            df = df[df['trade_date'] >= start_filter]

            if df.empty:
                raise ValueError("范围内无数据")

            df['change_pct'] = df['close'].pct_change() * 100
            df['change_pct'] = df['change_pct'].fillna(0)
            df['amount'] = df['close'] * df['volume']
            df['etf_code'] = etf_code

            for col in ['open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            return df[['trade_date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'change_pct', 'etf_code']]

        try:
            df = _fetch()
            logger.info(f"[Ashare] {etf_code} 获取 {len(df)} 条行情 ({start_date}~{end_date})")
            return df
        except Exception as e:
            logger.error(f"[Ashare] {etf_code} 获取行情失败: {e}")
            return pd.DataFrame()


class EastmoneyListDataSource:
    """天天基金静态列表数据源（CDN 静态 JS，比 efinance 动态接口稳定）"""

    _LIST_URL = "http://fund.eastmoney.com/js/fundcode_search.js"

    def get_source_name(self) -> str:
        return "eastmoney_list"

    def is_available(self) -> bool:
        return True

    def fetch_etf_list(self) -> pd.DataFrame:
        import requests as _requests

        df = _call_with_retry(self._fetch_raw)
        return self._filter_etf(df)

    def _fetch_raw(self) -> pd.DataFrame:
        import requests as _requests

        resp = _requests.get(
            self._LIST_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "http://fund.eastmoney.com/",
            },
            timeout=15,
        )
        resp.raise_for_status()
        m = re.search(r"var r = (\[.*\]);", resp.text)
        if not m:
            raise ValueError("列表JS解析失败")

        rows = json.loads(m.group(1))
        if not rows:
            raise ValueError("列表为空")

        # 字段: [基金代码, 拼音首字母, 基金简称, 基金类型, 拼音]
        df = pd.DataFrame(
            rows, columns=["fundcode", "pinyin_short", "fundname", "fundtype", "pinyin"]
        )
        df = df.rename(columns={
            "fundcode": "etf_code", "fundname": "etf_name", "fundtype": "fund_type",
        })
        return df

    @staticmethod
    def _filter_etf(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        code = df["etf_code"].astype(str)
        name = df["etf_name"].astype(str)
        fund_type = df["fund_type"].astype(str)

        # 代码符合 ETF 规则（159/5[168]）且类型为指数型或简称含 ETF，排除货币/LOF 混入
        is_etf_code = code.str.match(r"^159\d{3}$") | code.str.match(r"^5[168]\d{4}$")
        is_etf_type = (
            (~fund_type.str.contains("货币", na=False))
            & (fund_type.str.contains("指数型", na=False)
               | fund_type.str.contains("ETF", na=False)
               | name.str.contains("ETF", na=False))
        )
        result = df[is_etf_code & is_etf_type].copy()
        result["data_source"] = "eastmoney_list"
        return result[["etf_code", "etf_name", "data_source"]]


class EFinanceDataSource:
    """efinance数据源（兜底）"""

    def __init__(self):
        self.circuit = _SourceCircuit(
            max_failures=settings.circuit_break_failures,
            break_seconds=settings.circuit_break_seconds,
        )

    def get_source_name(self) -> str:
        return "efinance"

    def is_available(self) -> bool:
        try:
            import efinance
            return True
        except Exception:
            return False

    def fetch_etf_list(self) -> pd.DataFrame:
        def _fetch():
            import efinance as ef

            df = ef.fund.get_fund_codes()
            if df.empty:
                raise ValueError("空数据")

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
            if etf_df.empty:
                raise ValueError("无ETF")
            etf_df['data_source'] = 'efinance'
            return etf_df[['etf_code', 'etf_name', 'data_source']]

        return _call_with_retry(_fetch, circuit=self.circuit)

    def fetch_etf_daily(
        self,
        etf_code: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        if end_date is None:
            end_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

        def _fetch():
            import efinance as ef

            df = ef.stock.get_quote_history(etf_code, beg=start_date, end=end_date)
            if df.empty:
                raise ValueError("空数据")

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

        return _call_with_retry(_fetch, circuit=self.circuit)


class DataSourceManager:
    """数据源管理器：Ashare(新浪+腾讯) → efinance(东方财富) 自动降级"""

    def __init__(self, ashare_only: bool = False):
        """
        Args:
            ashare_only: True=定时任务场景；但若 settings.scheduled_task_allow_fallback=True
                         （部署环境）仍会降级到 efinance，避免 Ashare 不可用时全盘失败
        """
        self.primary = AshareDataSource()
        self.fallback = EFinanceDataSource()
        self.list_source = EastmoneyListDataSource()
        # 定时任务专用源在配置允许降级时，自动转为可降级模式
        self.ashare_only = ashare_only and not settings.scheduled_task_allow_fallback
        if ashare_only:
            if self.ashare_only:
                logger.info("✓ 数据源已加载: Ashare (定时任务模式，不降级)")
            else:
                logger.info("✓ 数据源已加载: Ashare (定时任务模式，部署环境允许降级到 efinance)")
        else:
            logger.info("✓ 数据源已加载: Ashare(主) + 天天列表 + efinance(备)")

    # ---------------- ETF 列表：天天JS → 本地缓存 → efinance ----------------
    def fetch_etf_list(self) -> pd.DataFrame:
        df = self.list_source.fetch_etf_list()
        if not df.empty:
            _save_list_cache(df)
            return df

        logger.warning("天天基金列表源失败，尝试本地缓存")
        cached = _load_list_cache()
        if not cached.empty:
            return cached

        logger.warning("本地缓存缺失，降级到efinance列表源")
        df = self.fallback.fetch_etf_list()
        if not df.empty:
            _save_list_cache(df)
        return df

    # ---------------- 日K线 ----------------
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
        return ["ashare", "eastmoney_list", "efinance"]


# ---------------- 列表本地缓存 ----------------
def _list_cache_path() -> Path:
    """缓存路径：相对路径基于项目根目录解析，避免部署时 cwd 不对导致缓存丢失"""
    p = Path(settings.etf_list_cache_path)
    if not p.is_absolute():
        # app/services/data_sources.py → 上两级为项目根目录
        project_root = Path(__file__).resolve().parent.parent.parent
        p = project_root / p
    return p


def _save_list_cache(df: pd.DataFrame) -> None:
    """保存列表快照到本地缓存（含拉取时间），供失败时兜底"""
    try:
        path = _list_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.now().isoformat(),
            "items": df.to_dict(orient="records"),
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        logger.info(f"ETF列表缓存已保存: {path} ({len(df)} 只)")
    except Exception as e:
        logger.warning(f"ETF列表缓存保存失败: {e}")


def _load_list_cache() -> pd.DataFrame:
    """读取本地缓存列表；缺失/损坏返回空 DataFrame"""
    try:
        path = _list_cache_path()
        if not path.exists():
            return pd.DataFrame()
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        df = pd.DataFrame(payload.get("items", []))
        if df.empty:
            return pd.DataFrame()
        df["data_source"] = "cache"
        logger.info(f"ETF列表缓存加载: {path}（fetch于 {payload.get('fetched_at')}，{len(df)} 只）")
        return df[["etf_code", "etf_name", "data_source"]]
    except Exception as e:
        logger.warning(f"ETF列表缓存读取失败: {e}")
        return pd.DataFrame()


# 模块加载时注入 efinance 请求头伪装
_inject_efinance_headers()

_manager: Optional[DataSourceManager] = None


def get_data_source_manager() -> DataSourceManager:
    global _manager
    if _manager is None:
        _manager = DataSourceManager()
    return _manager
