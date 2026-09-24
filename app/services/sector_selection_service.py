"""板块 → 选型（半硬模式）：申万板块评分参与候选分流与换仓优先级

模式（`settings.sector_rotation_mode`，可回退）：
  - off        完全关闭，选型逻辑与历史一致
  - semi_hard  低配板块候选排序降级；低配板块持仓标为逆风并优先换出；超配板块在辩论中作为加分证据

边界：板块信号缺失（数据源失败/未匹配板块）时该标的按中性处理，绝不阻塞选型。
"""

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.sw_industry_service import get_sw_industry_service

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_SEMI_HARD = "semi_hard"
SIGNAL_LABELS = {"overweight": "超配", "neutral": "中性", "underweight": "低配"}


class SectorSelectionService:
    """给候选/持仓附加申万板块信息，并按模式做板块分流"""

    def get_mode(self) -> str:
        mode = (get_settings().sector_rotation_mode or MODE_SEMI_HARD).strip().lower()
        return mode if mode in (MODE_OFF, MODE_SEMI_HARD) else MODE_SEMI_HARD

    def annotate(self, db: Session, items: List[dict]) -> int:
        """就地附加 sector 字段（{sector, score, signal, rank, total, pe, ...}），返回命中数"""
        if not items:
            return 0
        names = {i["etf_code"]: i.get("etf_name", "") for i in items if i.get("etf_code")}
        signals = get_sw_industry_service().get_etf_sector_signals(db, names)
        for item in items:
            sig = signals.get(item.get("etf_code"))
            if sig:
                item["sector"] = sig
        return len(signals)

    @staticmethod
    def _sector_signal(item: dict) -> Optional[str]:
        return ((item.get("sector") or {}).get("signal")) or None

    @staticmethod
    def _sector_score(item: dict) -> Optional[int]:
        value = (item.get("sector") or {}).get("score")
        return value if isinstance(value, int) else None

    def split(self, holdings: List[dict], candidates: List[dict], mode: str) -> Tuple[List[dict], List[dict], Dict]:
        """按板块信号分流：返回 (holdings, candidates, meta)

        - 持仓：低配板块 → sector_headwind=True（优先换出）
        - 候选：低配板块 → sector_downgrade=True（排序降级，不进首选）
        """
        meta = {"mode": mode, "overweight": 0, "underweight": 0, "headwind_holdings": [], "downgraded": []}
        if mode == MODE_OFF:
            return holdings, candidates, meta

        for item in holdings:
            if self._sector_signal(item) == "underweight":
                item["sector_headwind"] = True
                meta["headwind_holdings"].append(item.get("etf_code"))

        ordered_candidates = []
        for item in candidates:
            if self._sector_signal(item) == "underweight":
                item["sector_downgrade"] = True
                meta["downgraded"].append(item.get("etf_code"))
        normal = [c for c in candidates if not c.get("sector_downgrade")]
        downgraded = [c for c in candidates if c.get("sector_downgrade")]
        ordered_candidates = normal + downgraded
        meta["overweight"] = sum(1 for c in candidates if self._sector_signal(c) == "overweight")

        return holdings, ordered_candidates, meta

    def ordering_key(self, holdings: List[dict]) -> List[dict]:
        """换仓优先顺序（纯量化降级用）：逆风持仓优先换出，同组内得分低者优先"""
        return sorted(holdings, key=lambda h: (
            0 if h.get("sector_headwind") else 1,
            h.get("composite_score") or 0,
        ))


def format_sector_context(holdings: List[dict], candidates: List[dict], meta: Dict) -> str:
    """辩论材料中的「板块轮动参考」文本段"""
    lines: List[str] = []
    if meta.get("mode") == MODE_OFF:
        return "板块层未启用（仅个股动量）"

    def _line(prefix: str, item: dict, extra: str = "") -> Optional[str]:
        sig = (item.get("sector") or {})
        if not sig:
            return None
        label = SIGNAL_LABELS.get(sig.get("signal"), "中性")
        rank = f"#{sig.get('rank')}/{sig.get('total')}" if sig.get("rank") else "-"
        pe = f" PE {sig['pe']:.1f}" if sig.get("pe") is not None else ""
        delta = ""
        return (f"{prefix} {item.get('etf_name') or item.get('etf_code')}"
                f"（{sig.get('sector')} {label} 板块评分{sig.get('score')} 排名{rank}{pe}）{delta}{extra}")

    holding_lines = [
        _line("持仓", h, " ← 板块逆风，优先换出" if h.get("sector_headwind") else "")
        for h in holdings
    ]
    candidate_lines = [
        _line("候选", c, " ← 板块低配，排序降级" if c.get("sector_downgrade") else "")
        for c in candidates[:5]
    ]
    lines.extend([x for x in holding_lines if x])
    lines.extend([x for x in candidate_lines if x])

    if meta.get("headwind_holdings"):
        lines.append(f"⚠ 逆风持仓（低配板块）：{'、'.join(meta['headwind_holdings'])}")
    lines.append("口径：申万一级行业评分 ≥67 超配 / ≤33 低配（0.45×相对强度分位 + 0.35×动量分位 + 0.20×趋势）；"
                 "板块仅做分层参考，个券强度仍以综合分为准。")
    return "\n".join(lines)


_service: Optional[SectorSelectionService] = None


def get_sector_selection_service() -> SectorSelectionService:
    global _service
    if _service is None:
        _service = SectorSelectionService()
    return _service