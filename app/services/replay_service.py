"""
历史回放服务：对历史交易日重跑AI市场判断

用当日技术指标（确定性计算）+ LLM裁决，生成 regime + 建议配置，
以 action_type="replayed" 写入 auto_strategy_log，与真实记录（analyzed）严格分离。

用途：补足熊市/多周期样本，供规则学习分表提取。

运行方式（服务器上）：
    python -m app.services.replay_service --start 2024-01-02 --end 2026-07-30
"""

import argparse
import json
import logging
import time
from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.config import get_settings
from app.db.database import SessionLocal
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.etf import ETFDailyIndicator
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)

# 市场状态枚举（与 market_analyst 保持一致）
VALID_REGIMES = {"bull_quiet", "bull_volatile", "neutral", "bear_quiet", "bear_panic", "crisis"}

REPLAY_PROMPT = """你是ETF量化策略的市场研究主管。以下是对{trade_date}收盘后市场技术面的量化摘要（基于历史数据回放，无当日舆情与新闻信息）。请仅依据技术面做出市场判断与配置建议。

## 市场广度（全ETF池 {pool_size} 只）
- 平均综合得分: {avg_score}（0-100，越高越强）
- 5日动量为正的占比: {breadth}%
- 平均20日波动率: {avg_vol}%

## 策略池各ETF当日指标
{etf_table}

## 策略核心目标
月收益5%-10%。熊市/危机状态应主动降低权益暴露、增配债券与黄金类防御资产。

## 输出要求
仅输出JSON（不要其他文字）：
{{
  "market_regime": "bull_quiet/bull_volatile/neutral/bear_quiet/bear_panic/crisis 之一",
  "regime_confidence": "high/medium/low",
  "suggested_action": "hold/rebalance 之一",
  "suggested_allocation": {{"ETF代码": 权重}},
  "action_reason": "30字以内的中文判断理由"
}}
约束：suggested_allocation 的键只能来自上方策略池，权重合计=1.0，单一ETF不超过40%。"""


class ReplayService(BaseAgent):
    """历史回放：逐日生成AI市场判断"""

    name = "replay"

    def __init__(self):
        super().__init__()
        self.settings = get_settings()

    # ---------- 数据准备 ----------

    def _trading_dates(self, db: Session, start: date, end: date) -> List[date]:
        """区间内有指标数据的交易日"""
        rows = (
            db.query(ETFDailyIndicator.trade_date)
            .filter(
                ETFDailyIndicator.trade_date >= start,
                ETFDailyIndicator.trade_date <= end,
            )
            .distinct()
            .order_by(ETFDailyIndicator.trade_date.asc())
            .all()
        )
        return [r[0] for r in rows]

    def _already_replayed(self, db: Session, strategy_id: int, d: date) -> bool:
        return db.query(AutoStrategyLog.id).filter(
            AutoStrategyLog.strategy_id == strategy_id,
            AutoStrategyLog.log_date == d,
            AutoStrategyLog.action_type == "replayed",
        ).first() is not None

    def _snapshot(self, d: date, db: Session, etf_codes: List[str]) -> Optional[Dict]:
        """当日技术面量化摘要"""
        rows = db.query(ETFDailyIndicator).filter(
            ETFDailyIndicator.trade_date == d
        ).all()
        if not rows:
            return None

        scores = [r.composite_score for r in rows if r.composite_score is not None]
        mom5 = [r.momentum_5d for r in rows if r.momentum_5d is not None]
        vols = [r.volatility_20d for r in rows if r.volatility_20d is not None]
        if not scores:
            return None

        pool_size = len(rows)
        avg_score = round(sum(scores) / len(scores), 1)
        breadth = round(100.0 * sum(1 for m in mom5 if m > 0) / len(mom5), 0) if mom5 else 0
        avg_vol = round(sum(vols) / len(vols), 1) if vols else 0

        by_code = {r.etf_code: r for r in rows}
        lines = []
        for c in etf_codes:
            r = by_code.get(c)
            if not r:
                continue
            lines.append(
                f"- {c}: 得分{r.composite_score if r.composite_score is not None else '-'} "
                f"5日动量{r.momentum_5d if r.momentum_5d is not None else '-'}% "
                f"20日动量{r.momentum_20d if r.momentum_20d is not None else '-'}% "
                f"波动率{r.volatility_20d if r.volatility_20d is not None else '-'}%"
            )
        if not lines:
            return None

        return {
            "pool_size": pool_size,
            "avg_score": avg_score,
            "breadth": breadth,
            "avg_vol": avg_vol,
            "etf_table": "\n".join(lines),
            "valid_codes": [c for c in etf_codes if c in by_code],
        }

    # ---------- 单日回放 ----------

    def replay_one(self, d: date, db: Session, strategy: Strategy) -> Optional[Dict]:
        snap = self._snapshot(d, db, list((strategy.allocation_config or {}).keys()))
        if not snap:
            logger.warning(f"[Replay] {d} 无指标数据，跳过")
            return None

        prompt = REPLAY_PROMPT.format(
            trade_date=d.isoformat(),
            pool_size=snap["pool_size"],
            avg_score=snap["avg_score"],
            breadth=snap["breadth"],
            avg_vol=snap["avg_vol"],
            etf_table=snap["etf_table"],
        )

        for attempt in range(2):
            result = self.call_llm(prompt, temperature=0.3)
            if "error" not in result:
                break
            logger.warning(f"[Replay] {d} LLM失败(第{attempt+1}次): {result['error']}")
            time.sleep(2)
        else:
            return None

        if "error" in result:
            return None

        # 校验与清洗
        regime = result.get("market_regime")
        if regime not in VALID_REGIMES:
            logger.warning(f"[Replay] {d} regime非法: {regime}，丢弃")
            return None

        alloc = result.get("suggested_allocation") or {}
        alloc = {k: float(v) for k, v in alloc.items() if k in snap["valid_codes"] and float(v) > 0}
        total = sum(alloc.values())
        if total <= 0:
            logger.warning(f"[Replay] {d} 配置为空，丢弃")
            return None
        alloc = {k: round(v / total, 4) for k, v in alloc.items()}

        analysis = {
            "analysis_date": d.isoformat(),
            "market_regime": regime,
            "regime_confidence": result.get("regime_confidence"),
            "suggested_action": result.get("suggested_action", "hold"),
            "suggested_allocation": alloc,
            "action_reason": result.get("action_reason", ""),
            "source": "replay",
            "model": self.settings.llm_model,
            "prompt_version": "v1",
            "tech_snapshot": {
                "avg_score": snap["avg_score"],
                "breadth": snap["breadth"],
                "avg_vol": snap["avg_vol"],
            },
        }

        log = AutoStrategyLog(
            strategy_id=strategy.id,
            log_date=d,
            status="success",
            action_type="replayed",
            analysis_result=analysis,
        )
        db.add(log)
        db.commit()
        return analysis

    # ---------- 批量回放 ----------

    def run(self, start: date, end: date, strategy_id: int = 1) -> Dict:
        db = SessionLocal()
        try:
            strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
            if not strategy:
                return {"error": f"策略{strategy_id}不存在"}

            dates = self._trading_dates(db, start, end)
            todo = [d for d in dates if not self._already_replayed(db, strategy_id, d)]
            logger.info(f"[Replay] 区间 {start}~{end} 共{len(dates)}个交易日，待回放{len(todo)}天")

            done = fail = 0
            for i, d in enumerate(todo):
                try:
                    r = self.replay_one(d, db, strategy)
                    if r:
                        done += 1
                    else:
                        fail += 1
                    if (i + 1) % 10 == 0:
                        logger.info(f"[Replay] 进度 {i+1}/{len(todo)} 成功{done} 失败{fail}")
                except Exception as e:
                    fail += 1
                    db.rollback()
                    logger.error(f"[Replay] {d} 异常: {e}")
                time.sleep(0.5)

            summary = {"total": len(todo), "done": done, "failed": fail}
            logger.info(f"[Replay] 完成: {summary}")
            return summary
        finally:
            db.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    parser = argparse.ArgumentParser(description="AI市场判断历史回放")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--strategy-id", type=int, default=1)
    args = parser.parse_args()

    svc = ReplayService()
    result = svc.run(
        datetime.strptime(args.start, "%Y-%m-%d").date(),
        datetime.strptime(args.end, "%Y-%m-%d").date(),
        args.strategy_id,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
