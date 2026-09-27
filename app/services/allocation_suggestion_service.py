"""调仓建议服务：LLM 只出建议，实际换仓统一由轮动通道（rotation_service）裁决

职责：
  - 接收建议（校验比例与代码、去重、标 pending）
  - 供轮动通道读取未决建议（进入辩论材料 + 纳入候选触发评估）
  - 轮动通道裁决后结算（adopted / rejected + 说明）；逾期未决按 rejected 结算
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.allocation_suggestion import AllocationSuggestion
from app.models.etf import ETFBasic
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)

MAX_PENDING_PER_STRATEGY = 3     # 同一策略同时最多保留的未决建议数（超出则旧的按 rejected 结算）
MIN_WEIGHT = 0.01                # 忽略 <1% 的权重
MAX_SWAPS = 2                    # 单条建议最多替换对数（与轮动硬约束一致）


class AllocationSuggestionService:

    @staticmethod
    def _normalize_swaps(swaps) -> List[Dict]:
        """规范化显式替换提案：[{remove, add, weight?, reason?}]"""
        if not swaps:
            return []
        if not isinstance(swaps, list):
            return []
        out = []
        for item in swaps[:MAX_SWAPS]:
            if not isinstance(item, dict):
                continue
            remove_code = str(item.get("remove") or "").strip()
            add_code = str(item.get("add") or "").strip()
            if not remove_code or not add_code or remove_code == add_code:
                continue
            row = {"remove": remove_code, "add": add_code}
            weight = item.get("weight")
            if weight is not None:
                try:
                    value = float(weight)
                    if 0 < value <= 1:
                        row["weight"] = round(value, 4)
                except (TypeError, ValueError):
                    pass
            if item.get("reason"):
                row["reason"] = str(item["reason"])[:200]
            out.append(row)
        return out

    def create(self, db: Session, strategy_id: int, suggested_allocation: Dict[str, float],
               reason: str = "", source: str = "agentloop", swaps=None) -> Dict:
        """记录一条调仓建议（不修改任何配置）

        swaps: 显式替换提案 [{remove, add, weight?}]，轮动通道校验硬约束后可**直接执行**
        """
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": f"策略 {strategy_id} 不存在"}

        allocation = {}
        for code, weight in (suggested_allocation or {}).items():
            try:
                value = float(weight)
            except (TypeError, ValueError):
                continue
            if code and value >= MIN_WEIGHT:
                allocation[str(code)] = round(value, 4)
        if not allocation:
            return {"error": "建议配置为空或权重不合法"}

        total = sum(allocation.values())
        if abs(total - 1.0) > 0.01:
            return {"error": f"建议配置权重总和应为 1.0，当前为 {total:.4f}"}

        unknown = [
            c for c in allocation
            if not db.query(ETFBasic.etf_code).filter(ETFBasic.etf_code == c).first()
        ]
        if unknown:
            return {"error": f"以下代码不在 ETF 列表中: {unknown}（建议前先搜索/入池）"}

        self._settle_overflow(db, strategy_id)

        row = AllocationSuggestion(
            strategy_id=strategy_id,
            source=source if source in ("agentloop", "chat", "manual", "fallback") else "agentloop",
            suggested_allocation=allocation,
            reason=(reason or "")[:1000],
            proposed_swaps=self._normalize_swaps(swaps) or None,
            status="pending",
        )
        db.add(row)
        db.commit()
        logger.info(f"[建议] 策略{strategy_id} 记录调仓建议#{row.id}（{source}）: "
                    f"{ {k: round(v, 3) for k, v in allocation.items()} }")
        return {
            "suggestion_id": row.id,
            "status": row.status,
            "suggested_allocation": allocation,
            "proposed_swaps": row.proposed_swaps or [],
            "message": ("建议已记录。" + (
                "含显式替换提案，轮动通道将校验硬约束（持仓池/最短持有期/禁入/数量与权重上限），通过即直接执行，"
                "否则转辩论或驳回并回执。" if row.proposed_swaps else
                "未提供显式替换提案，仅作为辩论参考（目标权重不会精确执行）；如需精确执行请用 swaps 提案。")),
        }

    def _settle_overflow(self, db: Session, strategy_id: int):
        """未决建议超过上限时，把最旧的按 rejected 结算（避免建议堆积触发重复评估）"""
        pending = (
            db.query(AllocationSuggestion)
            .filter(AllocationSuggestion.strategy_id == strategy_id,
                    AllocationSuggestion.status == "pending")
            .order_by(AllocationSuggestion.created_at.asc())
            .all()
        )
        for row in pending[:max(0, len(pending) - MAX_PENDING_PER_STRATEGY + 1)]:
            row.status = "rejected"
            row.decided_at = datetime.utcnow()
            row.decided_note = "未决建议数超上限，自动结算为驳回（等待轮动通道关注最新一条）"

    def get_pending(self, db: Session, strategy_id: int) -> List[AllocationSuggestion]:
        """未决建议（有效期内，按时间倒序）"""
        cutoff = datetime.utcnow() - timedelta(days=AllocationSuggestion.default_expires_days())
        return (
            db.query(AllocationSuggestion)
            .filter(AllocationSuggestion.strategy_id == strategy_id,
                    AllocationSuggestion.status == "pending",
                    AllocationSuggestion.created_at >= cutoff)
            .order_by(AllocationSuggestion.created_at.desc())
            .all()
        )

    def expire_stale(self, db: Session, strategy_id: int) -> int:
        """逾期未决建议结算为 rejected"""
        cutoff = datetime.utcnow() - timedelta(days=AllocationSuggestion.default_expires_days())
        rows = (
            db.query(AllocationSuggestion)
            .filter(AllocationSuggestion.strategy_id == strategy_id,
                    AllocationSuggestion.status == "pending",
                    AllocationSuggestion.created_at < cutoff)
            .all()
        )
        for row in rows:
            row.status = "rejected"
            row.decided_at = datetime.utcnow()
            row.decided_note = "建议超期未处理，自动驳回"
        if rows:
            db.commit()
        return len(rows)

    def settle(self, db: Session, strategy_id: int, status: str, note: str) -> int:
        """轮动通道裁决后结算未决建议"""
        rows = self.get_pending(db, strategy_id)
        now = datetime.utcnow()
        for row in rows:
            row.status = status
            row.decided_at = now
            row.decided_note = (note or "")[:1000]
        if rows:
            db.commit()
            logger.info(f"[建议] 策略{strategy_id} {len(rows)} 条建议结算为 {status}")
        return len(rows)


_service: Optional[AllocationSuggestionService] = None


def get_allocation_suggestion_service() -> AllocationSuggestionService:
    global _service
    if _service is None:
        _service = AllocationSuggestionService()
    return _service