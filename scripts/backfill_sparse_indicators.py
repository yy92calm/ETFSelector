"""稀疏日指标重算：行情无成交量时跳过流动性过滤

背景：2025-05-12~2025-12-31 共161个交易日的历史行情 volume/amount=0
（东财历史接口当时未返回或被限流），_compute_indicator 的
"5日均成交额>=500万"过滤导致这些天只算出4只债券ETF指标。

本脚本对指标行数<10的日期重算：临时放开流动性过滤，
量相关因子（vol_ratio/obv/flow）自然中性化，动量/趋势/波动率准确。
"""
import sys
import time
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.database import SessionLocal
from app.models.etf import ETFQuotation, ETFDailyIndicator
from app.services import market_scanner_service
from app.services.market_scanner_service import MarketScannerService
from sqlalchemy import text


def main():
    db = SessionLocal()
    scanner = MarketScannerService()

    # 找出稀疏日（指标行数<10）
    sparse = [
        r[0] if isinstance(r[0], _date) else _date.fromisoformat(str(r[0]))
        for r in db.execute(text(
            "SELECT trade_date FROM ("
            "  SELECT trade_date, COUNT(*) n FROM etf_daily_indicator GROUP BY trade_date"
            ") WHERE n < 10 ORDER BY trade_date"
        )).fetchall()
    ]
    print(f"稀疏日: {len(sparse)} 天")
    if not sparse:
        return

    # 放开流动性过滤
    original_min_amount = market_scanner_service.MIN_AMOUNT_5D
    market_scanner_service.MIN_AMOUNT_5D = 0

    codes = [r[0] for r in db.execute(text("SELECT etf_code FROM etf_basic")).fetchall()]
    total = 0
    t0 = time.time()

    try:
        for i, d in enumerate(sparse):
            # 删除该日旧指标（4只债券ETF），重算全池
            db.execute(text("DELETE FROM etf_daily_indicator WHERE trade_date = :d"), {"d": d.isoformat()})
            db.commit()

            results = []
            for code in codes:
                ind = scanner._compute_indicator(code, d, db)
                if ind:
                    results.append(ind)

            if not results:
                continue

            # 全市场排名
            import numpy as np
            scores = [r["composite_score"] for r in results]
            sorted_indices = np.argsort(scores)[::-1]
            for rank, idx in enumerate(sorted_indices, 1):
                results[idx]["rank_in_market"] = rank

            for r in results:
                db.add(ETFDailyIndicator(
                    etf_code=r["etf_code"],
                    trade_date=d,
                    momentum_5d=r["momentum_5d"],
                    momentum_20d=r["momentum_20d"],
                    momentum_score=r["momentum_score"],
                    trend_strength=r["trend_strength"],
                    ma5=r["ma5"],
                    ma10=r["ma10"],
                    ma20=r["ma20"],
                    vol_ratio=r["vol_ratio"],
                    volatility_20d=r["volatility_20d"],
                    obv_slope=r["obv_slope"],
                    amount_avg_5d=r["amount_avg_5d"],
                    composite_score=r["composite_score"],
                    rank_in_market=r["rank_in_market"],
                ))
            db.commit()
            total += len(results)

            if (i + 1) % 20 == 0:
                print(f"[{i+1}/{len(sparse)}] {d} | {len(results)}只 | 累计{total}条 | {time.time()-t0:.0f}秒")
    finally:
        market_scanner_service.MIN_AMOUNT_5D = original_min_amount

    print(f"\n完成: {len(sparse)}天 共写入 {total} 条指标, 耗时 {time.time()-t0:.0f}秒")
    db.close()


if __name__ == "__main__":
    main()
