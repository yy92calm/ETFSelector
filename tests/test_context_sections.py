"""状态快照补全：持仓盈亏 / 近期成交 / 仓位与待生效 / 大盘环境，缺失数据可见而非静默省略"""
import unittest
from datetime import timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.agent_core.context import CONTEXT_STRATEGY_LIMIT, ContextBuilder
from app.db.database import Base
from app.models.etf import ETFQuotation
from app.models.market_regime import MarketRegimeSnapshot
from app.models.portfolio import Holding, PortfolioSnapshot, TradeRecord
from app.models.strategy import Strategy
from app.utils.trading_calendar import now_cn

# 持有期用「今天」倒推，硬编码日期会随系统日期推移产生误报
TODAY = now_cn().date()


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def add_strategy(db, sid=1, name="自动策略", status="active",
                 allocation=None, pending=None):
    db.add(Strategy(
        id=sid, name=name, strategy_type="auto", strategy_source="auto_generated",
        status=status, auto_strategy_status="running",
        allocation_config=allocation if allocation is not None else {"510300": 0.6, "512880": 0.4},
        pending_allocation=pending,
        rebalance_freq="daily", initial_capital=1_000_000.0,
    ))


class TestSnapshotSections(unittest.TestCase):

    def setUp(self):
        self.db, self.engine = make_db()
        self.builder = ContextBuilder()

    def tearDown(self):
        self.db.close()

    def _sections(self):
        return self.builder.build_state_sections(self.db)

    def test_empty_database_falls_back_to_init_note(self):
        text = self.builder.build_turn_snapshot(self.db)

        self.assertIn("系统刚初始化", text)
        self.assertNotIn("持仓盈亏", text, "无活跃策略时该 section 应整体省略")

    def test_missing_index_proxies_are_visible_when_market_data_exists(self):
        """有行情数据但指数代理/状态快照缺失时显式标注，不静默省略该 section"""
        self.db.add(ETFQuotation(etf_code="512880", trade_date=TODAY, open_price=1.2,
                                 close_price=1.2, high_price=1.2, low_price=1.2,
                                 volume=1, amount=1, change_pct=-0.5))
        self.db.commit()

        text = self.builder.build_turn_snapshot(self.db)

        self.assertIn("大盘环境与仓位基准", text)
        self.assertIn("暂无指数行情与市场状态快照", text)
        self.assertNotIn("系统刚初始化", text)

    def test_holdings_show_cost_price_pnl_and_lock(self):
        add_strategy(self.db)
        self.db.add(Holding(strategy_id=1, etf_code="510300", quantity=10000,
                            avg_cost=4.0, current_price=4.4, market_value=44000))
        self.db.add(TradeRecord(strategy_id=1, trade_date=TODAY - timedelta(days=8),
                                etf_code="510300", direction="buy", price=4.0,
                                quantity=10000, amount=40000))
        self.db.commit()

        text = self._sections()

        self.assertIn("持仓盈亏", text)
        self.assertIn("成本4.000/现价4.400 +10.0%", text)
        self.assertIn("持8日(可换)", text)

    def test_lock_period_remaining_uses_rotation_channel_baseline(self):
        """最短持有期与轮动通道同口径（最近一笔买入起算），未到期要写明还能不能换"""
        add_strategy(self.db)
        self.db.add(Holding(strategy_id=1, etf_code="512880", quantity=5000,
                            avg_cost=1.2, current_price=1.15, market_value=5750))
        self.db.add(TradeRecord(strategy_id=1, trade_date=TODAY - timedelta(days=2),
                                etf_code="512880", direction="buy", price=1.2,
                                quantity=5000, amount=6000))
        self.db.add(ETFQuotation(etf_code="512880", trade_date=TODAY, open_price=1.15,
                                 close_price=1.15, high_price=1.15, low_price=1.15,
                                 volume=1, amount=1, change_pct=-0.5))
        self.db.commit()

        text = self._sections()

        self.assertIn("持2日(还可换3日后)", text)
        self.assertIn("-4.2%", text)

    def test_holding_without_trade_marks_unknown_entry_date(self):
        add_strategy(self.db)
        self.db.add(Holding(strategy_id=1, etf_code="510500", quantity=100,
                            avg_cost=5.0, current_price=5.0, market_value=500))
        self.db.commit()

        self.assertIn("建仓日未知", self._sections())

    def test_recent_trades_lists_last_five_with_direction(self):
        add_strategy(self.db)
        for i in range(7):
            self.db.add(TradeRecord(strategy_id=1, trade_date=TODAY - timedelta(days=i),
                                    etf_code="510300",
                                    direction="buy" if i % 2 == 0 else "sell",
                                    price=4.0 + i / 100, quantity=1000, amount=4000 + i * 10,
                                    reason=f"再平衡{i}"))
        self.db.commit()

        text = self._sections()

        self.assertIn("近期成交", text)
        self.assertIn("买入", text)
        self.assertIn("卖出", text)
        self.assertIn("再平衡0", text)
        self.assertEqual(text.count("510300 买入") + text.count("510300 卖出"), 5,
                         "只保留最近5笔")

    def test_no_trades_is_explicit(self):
        add_strategy(self.db)
        self.db.commit()

        self.assertIn("暂无成交记录", self._sections())

    def test_position_summary_covers_weights_pending_and_cash(self):
        add_strategy(self.db, pending={"512010": 0.5, "510300": 0.5})
        self.db.add(PortfolioSnapshot(strategy_id=1, trade_date=TODAY, total_asset=1_000_000,
                                      cash=120_000, market_value=880_000,
                                      profit=1000, profit_pct=3.25))
        self.db.commit()

        text = self._sections()

        self.assertIn("目标权重合计:100%", text)
        self.assertIn("待生效:512010:50%, 510300:50%", text)
        self.assertIn("现金12%（已用88%）", text)
        self.assertIn("累计+3.25%", text)

    def test_position_summary_without_pending_and_snapshot(self):
        add_strategy(self.db, allocation={"510300": 0.5})
        self.db.commit()

        text = self._sections()

        self.assertIn("目标权重合计:50%", text)
        self.assertIn("无待生效配置", text)
        self.assertIn("组合快照暂无数据", text)

    def test_index_and_regime_baseline(self):
        """指数代理给当日/5日涨跌，状态快照给建议权益仓位"""
        closes = {i: 4.0 + i * 0.01 for i in range(10)}   # 每天 +0.01
        for offset, close in closes.items():
            day = TODAY - timedelta(days=9 - offset)
            self.db.add(ETFQuotation(etf_code="510300", trade_date=day, open_price=close,
                                     close_price=close, high_price=close, low_price=close,
                                     volume=1, amount=1, change_pct=0.25))
        self.db.add(ETFQuotation(etf_code="510500", trade_date=TODAY, open_price=6.0,
                                 close_price=6.0, high_price=6.0, low_price=6.0,
                                 volume=1, amount=1, change_pct=-0.3))
        self.db.add(MarketRegimeSnapshot(
            trade_date=TODAY, risk_appetite=58.0, risk_label="中性",
            state_label="机会", dispersion_label="分化",
            suggested_equity_range=[0.6, 0.8],
        ))
        self.db.commit()

        text = self._sections()

        self.assertIn("沪深300(510300) 当日+0.25% 5日+1.24%", text)
        self.assertIn("中证500(510500) 当日— 5日—", text)
        self.assertIn(f"市场状态({TODAY}): 机会 | 风险偏好:中性", text)
        self.assertIn("建议权益仓位:60%~80%", text)

    def test_truncation_is_visible(self):
        for i in range(1, CONTEXT_STRATEGY_LIMIT + 3):
            add_strategy(self.db, sid=i, name=f"策略{i}")
        self.db.commit()

        text = self._sections()

        self.assertIn(f"共 {CONTEXT_STRATEGY_LIMIT + 2} 个活跃策略，以上仅列前 "
                      f"{CONTEXT_STRATEGY_LIMIT} 个", text)
        self.assertEqual(text.count("| 状态:"), CONTEXT_STRATEGY_LIMIT)
        self.assertNotIn("策略6 | 状态:", text)

    def test_new_section_queries_do_not_scale_with_strategy_count(self):
        """新增 section 一次批量取回（分组/IN 查询），策略数增加不应增加 SQL 往返

        旧 section（目标进度、风控）仍逐策略查询，属方案 3.5 的清理范围。
        """
        new_sections = ("_get_index_regime_summary", "_get_holdings_summary",
                        "_get_recent_trades", "_get_position_summary")

        def run(count):
            db, engine = make_db()
            try:
                for i in range(1, count + 1):
                    add_strategy(db, sid=i, name=f"策略{i}")
                    for day in (0, 1):
                        db.add(PortfolioSnapshot(strategy_id=i,
                                                 trade_date=TODAY - timedelta(days=day),
                                                 total_asset=100, cash=10, market_value=90,
                                                 profit=1, profit_pct=1.0))
                    db.add(Holding(strategy_id=i, etf_code="510300", quantity=100,
                                   avg_cost=4.0, current_price=4.2, market_value=420))
                    db.add(TradeRecord(strategy_id=i, trade_date=TODAY, etf_code="510300",
                                       direction="buy", price=4.0, quantity=100, amount=400))
                db.commit()
                statements = []
                event.listen(engine, "before_cursor_execute",
                             lambda c, cur, sql, *a: statements.append(sql))
                builder = ContextBuilder()
                for name in new_sections:
                    getattr(builder, name)(db)
                return len(statements)
            finally:
                db.close()

        self.assertEqual(run(1), run(CONTEXT_STRATEGY_LIMIT))


if __name__ == "__main__":
    unittest.main()
