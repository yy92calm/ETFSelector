"""一次性数据修复脚本（2026-08-09）

1. 初始化新表/新字段（holding_snapshot、strategy.pending_allocation）
2. 修复策略1：删除错误的 2026-08-09 快照与当日交易，恢复持仓至 8/7 状态，
   本次调仓转为待生效（t+1，下一交易日执行）
3. 回填历史每日持仓快照（按交易记录重放）
"""

from datetime import date

from app.db.database import SessionLocal, init_db
from app.models.portfolio import PortfolioSnapshot, TradeRecord, Holding, HoldingSnapshot
from app.models.etf import ETFQuotation
from app.models.strategy import Strategy

OLD_ALLOC = {"159798": 0.2, "562530": 0.2, "159512": 0.2, "510630": 0.2, "560160": 0.2}
REPAIR_DATE = date(2026, 8, 9)

# 恢复后的持仓（8/7 状态）：代码 -> (数量, 成本价, 8/7估值价)
RESTORED_HOLDINGS = {
    "159798": (222700, 0.898, 0.853),
    "562530": (153400, 1.275, 1.263),
    "510630": (237900, 0.852, 0.823),
    "159512": (176000, 1.139, 1.092),
    "560160": (212300, 0.932, 0.92),
}


def repair_strategy_1(db):
    strategy = db.query(Strategy).filter(Strategy.id == 1).first()
    if not strategy:
        print("策略1不存在，跳过修复")
        return

    pending_new = dict(strategy.allocation_config)

    n_snap = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.strategy_id == 1, PortfolioSnapshot.trade_date == REPAIR_DATE)
        .delete()
    )
    n_trade = (
        db.query(TradeRecord)
        .filter(TradeRecord.strategy_id == 1, TradeRecord.trade_date == REPAIR_DATE)
        .delete()
    )
    db.query(HoldingSnapshot).filter(
        HoldingSnapshot.strategy_id == 1, HoldingSnapshot.trade_date == REPAIR_DATE
    ).delete()

    db.query(Holding).filter(Holding.strategy_id == 1).delete()
    for code, (qty, cost, price) in RESTORED_HOLDINGS.items():
        db.add(Holding(
            strategy_id=1, etf_code=code, quantity=qty,
            avg_cost=cost, current_price=price, market_value=round(qty * price, 2),
        ))

    strategy.allocation_config = OLD_ALLOC
    strategy.pending_allocation = pending_new
    strategy.pending_set_date = REPAIR_DATE

    db.commit()
    print(f"策略1修复完成: 删除快照{n_snap}条/交易{n_trade}条, 持仓恢复到8/7, 调仓转为待生效 {pending_new}")


def backfill_holding_snapshots(db):
    strategies = db.query(Strategy).all()
    total = 0
    for strategy in strategies:
        snapshots = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.strategy_id == strategy.id)
            .order_by(PortfolioSnapshot.trade_date.asc())
            .all()
        )
        if not snapshots:
            continue

        trades = (
            db.query(TradeRecord)
            .filter(TradeRecord.strategy_id == strategy.id)
            .order_by(TradeRecord.trade_date.asc(), TradeRecord.id.asc())
            .all()
        )

        holdings = {}
        last_trade_price = {}
        trade_idx = 0
        dates = [s.trade_date for s in snapshots]

        db.query(HoldingSnapshot).filter(
            HoldingSnapshot.strategy_id == strategy.id,
            HoldingSnapshot.trade_date.in_(dates),
        ).delete(synchronize_session=False)

        for snap in snapshots:
            while trade_idx < len(trades) and trades[trade_idx].trade_date <= snap.trade_date:
                t = trades[trade_idx]
                qty = holdings.get(t.etf_code, 0)
                holdings[t.etf_code] = qty + t.quantity if t.direction == "buy" else max(qty - t.quantity, 0)
                last_trade_price[t.etf_code] = t.price
                trade_idx += 1

            for code, qty in holdings.items():
                if qty <= 0:
                    continue
                quote = (
                    db.query(ETFQuotation)
                    .filter(ETFQuotation.etf_code == code, ETFQuotation.trade_date <= snap.trade_date)
                    .order_by(ETFQuotation.trade_date.desc())
                    .first()
                )
                price = quote.close_price if quote and quote.close_price > 0 else last_trade_price.get(code, 0)
                if price <= 0:
                    continue
                db.add(HoldingSnapshot(
                    strategy_id=strategy.id,
                    trade_date=snap.trade_date,
                    etf_code=code,
                    quantity=qty,
                    price=price,
                    market_value=round(qty * price, 2),
                ))
                total += 1

        db.commit()
        print(f"策略{strategy.id} 回填完成: {len(snapshots)}个日期")
    print(f"回填持仓快照共 {total} 条")


if __name__ == "__main__":
    init_db()
    db = SessionLocal()
    try:
        repair_strategy_1(db)
        backfill_holding_snapshots(db)
    finally:
        db.close()
