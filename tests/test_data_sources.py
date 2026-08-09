"""
数据源容错测试（efinance 防封）
覆盖：熔断、指数退避重试、列表缓存降级、UA注入、净值服务改走数据源
用法: source .venv/bin/activate && python test_data_sources.py
或:   pytest test_data_sources.py
"""
import unittest
from unittest.mock import MagicMock, patch
import json
import tempfile
import os
import time

import pandas as pd


def make_quotes_df(n=3):
    """构造日K DataFrame"""
    dates = pd.date_range("2026-08-01", periods=n)
    return pd.DataFrame({
        "trade_date": dates,
        "open": [1.0, 1.1, 1.2],
        "close": [1.05, 1.15, 1.25],
        "high": [1.1, 1.2, 1.3],
        "low": [0.95, 1.05, 1.15],
        "volume": [1000.0, 1100.0, 1200.0],
        "amount": [1050.0, 1265.0, 1500.0],
        "change_pct": [1.0, 1.0, 1.0],
        "etf_code": ["510300"] * n,
    })


class TestCircuitBreaker(unittest.TestCase):
    """熔断器"""

    def test_opens_after_max_failures(self):
        from app.services.data_sources import _SourceCircuit

        c = _SourceCircuit(max_failures=3, break_seconds=600)
        for _ in range(3):
            self.assertFalse(c.is_open())
            c.record_failure()
        # 达到阈值后熔断打开
        self.assertTrue(c.is_open())

    def test_closes_after_break_seconds(self):
        from app.services.data_sources import _SourceCircuit

        c = _SourceCircuit(max_failures=2, break_seconds=0.1)
        c.record_failure()
        c.record_failure()
        self.assertTrue(c.is_open())
        time.sleep(0.15)
        # 熔断期过后尝试恢复
        self.assertFalse(c.is_open())

    def test_success_resets(self):
        from app.services.data_sources import _SourceCircuit

        c = _SourceCircuit(max_failures=3, break_seconds=600)
        c.record_failure()
        c.record_failure()
        c.record_success()
        self.assertFalse(c.is_open())


class TestRetry(unittest.TestCase):
    """指数退避重试"""

    def test_retry_success(self):
        from app.services.data_sources import _call_with_retry

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("临时失败")
            return make_quotes_df()

        df = _call_with_retry(flaky, retries=3)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(df), 3)

    def test_retry_empty_dataframe_is_failure(self):
        from app.services.data_sources import _call_with_retry

        calls = {"n": 0}

        def empty_then_ok():
            calls["n"] += 1
            return pd.DataFrame() if calls["n"] == 1 else make_quotes_df()

        df = _call_with_retry(empty_then_ok, retries=2)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(df), 3)

    def test_retry_exhausted_returns_empty(self):
        from app.services.data_sources import _call_with_retry

        def always_fail():
            raise ValueError("持续失败")

        df = _call_with_retry(always_fail, retries=2)
        self.assertTrue(df.empty)

    def test_open_circuit_skips_call(self):
        from app.services.data_sources import _call_with_retry, _SourceCircuit

        circuit = _SourceCircuit(max_failures=1, break_seconds=600)
        circuit.record_failure()  # 已熔断
        called = {"n": 0}

        def fn():
            called["n"] += 1
            return make_quotes_df()

        df = _call_with_retry(fn, circuit=circuit)
        self.assertTrue(df.empty)
        self.assertEqual(called["n"], 0)  # 熔断期内不调用底层


class TestListCache(unittest.TestCase):
    """ETF列表本地缓存"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache_path = os.path.join(self.tmp, "cache.json")

    def test_save_load(self):
        with patch("app.services.data_sources.settings") as st:
            st.etf_list_cache_path = self.cache_path
            from app.services.data_sources import _save_list_cache, _load_list_cache

            df = pd.DataFrame([{"etf_code": "510300", "etf_name": "沪深300ETF"}])
            _save_list_cache(df)
            self.assertTrue(os.path.exists(self.cache_path))
            loaded = _load_list_cache()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded.iloc[0]["etf_code"], "510300")
            self.assertEqual(loaded.iloc[0]["data_source"], "cache")

    def test_load_missing_returns_empty(self):
        with patch("app.services.data_sources.settings") as st:
            st.etf_list_cache_path = self.cache_path
            from app.services.data_sources import _load_list_cache

            self.assertTrue(_load_list_cache().empty)


class TestListFallback(unittest.TestCase):
    """列表降级顺序：主源 → 缓存 → efinance"""

    def test_primary_then_cache_then_efinance(self):
        import app.services.data_sources as ds

        manager = ds.DataSourceManager.__new__(ds.DataSourceManager)
        manager.list_source = MagicMock()
        manager.fallback = MagicMock()
        manager.ashare_only = False

        cached = pd.DataFrame([{"etf_code": "510300", "etf_name": "沪深300ETF"}])

        # 主源失败 + 有缓存 → 用缓存，不再打 efinance
        manager.list_source.fetch_etf_list.return_value = pd.DataFrame()
        with patch("app.services.data_sources._load_list_cache", return_value=cached):
            df = ds.DataSourceManager.fetch_etf_list(manager)
        self.assertEqual(len(df), 1)
        manager.fallback.fetch_etf_list.assert_not_called()

        # 主源失败 + 无缓存 → 降级 efinance
        manager.list_source.fetch_etf_list.return_value = pd.DataFrame()
        manager.fallback.fetch_etf_list.return_value = cached
        with patch("app.services.data_sources._load_list_cache", return_value=pd.DataFrame()):
            df = ds.DataSourceManager.fetch_etf_list(manager)
        self.assertEqual(len(df), 1)
        manager.fallback.fetch_etf_list.assert_called()

        # 主源成功 → 不降级
        manager.list_source.fetch_etf_list.return_value = cached
        with patch("app.services.data_sources._load_list_cache", return_value=pd.DataFrame()):
            df = ds.DataSourceManager.fetch_etf_list(manager)
        self.assertEqual(len(df), 1)


class TestEfinanceCircuitUsed(unittest.TestCase):
    """efinance 数据源接入熔断与重试"""

    def test_efinance_source_has_circuit(self):
        from app.services.data_sources import EFinanceDataSource

        src = EFinanceDataSource()
        self.assertIsNotNone(src.circuit)


class TestNetValueUsesDataSource(unittest.TestCase):
    """净值服务改走 DataSourceManager（Ashare 主）"""

    def _make_db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.database import Base
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_fetch_and_save_quotes(self):
        from app.services.net_value_service import NetValueService

        db = self._make_db()
        svc = NetValueService()

        with patch("app.services.net_value_service.get_data_source_manager") as gm:
            mgr = MagicMock()
            mgr.fetch_etf_daily.return_value = make_quotes_df()
            gm.return_value = mgr

            res = svc.fetch_and_save_net_value("510300", db)

        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 3)
        mgr.fetch_etf_daily.assert_called_once()

        from app.models.etf import ETFQuotation
        rows = db.query(ETFQuotation).all()
        self.assertEqual(len(rows), 3)
        # 真实 K 线字段：volume/amount 非零
        self.assertTrue(all(r.volume > 0 for r in rows))
        self.assertTrue(all(r.amount > 0 for r in rows))
        self.assertEqual(rows[0].close_price, 1.05)

    def test_empty_result_returns_failure(self):
        from app.services.net_value_service import NetValueService

        db = self._make_db()
        svc = NetValueService()
        with patch("app.services.net_value_service.get_data_source_manager") as gm:
            gm.return_value.fetch_etf_daily.return_value = pd.DataFrame()
            res = svc.fetch_and_save_net_value("510300", db)
        self.assertFalse(res["success"])
        self.assertEqual(res["count"], 0)


if __name__ == "__main__":
    unittest.main()
