"""盈利-估值性价比模型 - 行业比较与个股安全边际

模型口径（用户框架）：
- 季度真实增长：最新已披露报告期的净利润同比（YOYNI）
- 估值变化：PE-TTM 处于自身历史（约2年）分位
- 性价比/安全边际：估值能被盈利消化的程度（PEG）+ 估值历史低位
- 行业比较：行业中位 PE/增速/PEG + 行业ETF动量（产业趋势代理）→ 综合评分排名
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.etf import ETFBasic, ETFQuotation
from app.models.stock_fundamental import IndustryScore, StockFundamental

logger = logging.getLogger(__name__)

# 得分权重：性价比 / 增长 / 产业趋势
W_VALUE, W_GROWTH, W_TREND = 0.5, 0.3, 0.2
HISTORY_DAYS = 500          # 行业中位PE历史分位回看（自然日）
STOCK_PE_HISTORY_DAYS = 500  # 个股自身PE分位回看
PEG_CAP = 3.0               # PEG≥3 计 0 分

# 证监会行业（中文名片段）→ ETF名称关键词（产业趋势代理）
INDUSTRY_ETF_KEYWORDS: Dict[str, List[str]] = {
    "货币金融": ["银行"], "资本市场": ["证券", "保险", "金融"],
    "医药": ["医药", "医疗", "创新药", "生物"], "食品": ["食品饮料", "白酒", "消费"],
    "酒、饮料": ["白酒", "食品饮料"], "电子": ["半导体", "芯片", "电子", "消费电子"],
    "计算机": ["计算机", "信息", "软件", "大数据", "云计算", "人工智能", "AI"],
    "电信": ["通信", "5G", "通讯"], "互联网": ["互联网", "传媒", "游戏"],
    "电气": ["新能源", "光伏", "电力设备", "电池", "储能"], "汽车": ["汽车", "新能源车", "智能汽车"],
    "有色": ["有色金属", "有色", "稀有金属", "黄金"], "黑色金属": ["钢铁"],
    "煤炭": ["煤炭"], "石油": ["石油", "石化", "油气", "能源"],
    "房地产": ["房地产", "地产"], "非金属矿物": ["建材"],
    "铁路、船舶": ["军工", "国防", "高端装备"], "土木工程": ["基建", "建筑"],
    "农业": ["农业", "畜牧", "养殖", "农牧"], "电力、热力": ["电力", "公用事业", "环保"],
    "通用设备": ["机械", "装备", "机器人"], "纺织": ["纺织", "服装"],
    "运输": ["交通运输", "物流", "运输", "航空"], "保险": ["保险"],
}


def _percentile_rank(values: List[float], v: float) -> float:
    """v 在 values 中的百分位（0-100，越高越大）"""
    if not values:
        return 50.0
    arr = np.array(values, dtype=float)
    return round(float(np.searchsorted(arr, v) / len(arr) * 100), 1)


class ValueModelService:
    """行业盈利-估值性价比 + 个股安全边际筛选"""

    # ---------------- 行业评分 ----------------

    def compute_industry_scores(self, db: Session, trade_date: Optional[date] = None) -> int:
        """计算并落库最新快照日的行业性价比排名，返回参评行业数"""
        latest = db.query(func.max(StockFundamental.trade_date)).filter(
            StockFundamental.pe_ttm.isnot(None)
        ).scalar()
        if latest is None:
            logger.warning("[ValueModel] 无估值数据，跳过行业评分（需先回填）")
            return 0
        snapshot_date = min(latest, trade_date or latest)

        rows = db.query(StockFundamental).filter(
            StockFundamental.trade_date == snapshot_date,
            StockFundamental.pe_ttm.isnot(None),
            StockFundamental.pe_ttm > 0,
        ).all()
        if not rows:
            return 0

        by_industry: Dict[str, List[StockFundamental]] = {}
        for r in rows:
            if r.industry:
                by_industry.setdefault(r.industry, []).append(r)

        hist_medians = self._industry_pe_history(db, snapshot_date)
        etf_names = self._load_etf_names(db)

        results: List[dict] = []
        for industry, stocks in by_industry.items():
            if len(stocks) < 5:  # 样本过少的行业不参与排名
                continue
            pes = [s.pe_ttm for s in stocks]
            growths = [s.ni_yoy for s in stocks if s.ni_yoy is not None]
            median_pe = float(np.median(pes))
            median_growth = float(np.median(growths)) if growths else None
            peg = round(median_pe / median_growth, 2) if (median_growth and median_growth > 0) else None
            pe_history = hist_medians.get(industry, [])
            pe_pct = _percentile_rank(pe_history, median_pe) if len(pe_history) >= 30 else None
            momentum = self._industry_momentum(db, industry, etf_names)
            results.append({
                "industry": industry, "sample_count": len(stocks),
                "median_pe": round(median_pe, 2), "median_growth": (
                    round(median_growth, 2) if median_growth is not None else None),
                "peg": peg, "pe_percentile": pe_pct, "trend_momentum": momentum,
            })

        if not results:
            return 0
        self._attach_scores(results)

        existing = {(s.industry): s for s in db.query(IndustryScore).filter(
            IndustryScore.trade_date == snapshot_date).all()}
        for item in sorted(results, key=lambda x: -x["score"]):
            obj = existing.get(item["industry"])
            if obj is None:
                obj = IndustryScore(trade_date=snapshot_date, industry=item["industry"])
                db.add(obj)
            for k, v in item.items():
                setattr(obj, k, v)
        db.commit()
        logger.info(f"[ValueModel] 行业评分完成: {snapshot_date} {len(results)} 个行业")
        return len(results)

    def _attach_scores(self, results: List[dict]) -> None:
        """组内百分位合成综合得分并排名"""
        pegas = [r["peg"] for r in results if r["peg"] is not None]
        pes = [r["pe_percentile"] for r in results if r["pe_percentile"] is not None]
        growths = [r["median_growth"] for r in results if r["median_growth"] is not None]
        trends = [r["trend_momentum"] for r in results if r["trend_momentum"] is not None]
        for r in results:
            if r["peg"] is not None and pegas:
                v1 = 100 - _percentile_rank(pegas, r["peg"])       # PEG越低性价比越高
            else:
                v1 = 50.0
            v2 = 100 - _percentile_rank(pes, r["pe_percentile"]) if (
                r["pe_percentile"] is not None and pes) else 50.0   # 估值历史分位越低越好
            value_score = v1 * 0.6 + v2 * 0.4
            growth_score = _percentile_rank(growths, r["median_growth"]) if (
                r["median_growth"] is not None and growths) else 50.0
            trend_score = _percentile_rank(trends, r["trend_momentum"]) if (
                r["trend_momentum"] is not None and trends) else 50.0
            r["score"] = round(value_score * W_VALUE + growth_score * W_GROWTH
                               + trend_score * W_TREND, 1)
        for i, r in enumerate(sorted(results, key=lambda x: -x["score"]), start=1):
            r["rank"] = i

    def _industry_pe_history(self, db: Session, snapshot_date: date) -> Dict[str, List[float]]:
        """各行业历史每日中位PE序列（回看 HISTORY_DAYS），供估值分位计算"""
        cutoff = snapshot_date - timedelta(days=HISTORY_DAYS)
        rows = db.query(
            StockFundamental.trade_date, StockFundamental.industry, StockFundamental.pe_ttm
        ).filter(
            StockFundamental.trade_date <= snapshot_date,
            StockFundamental.trade_date >= cutoff,
            StockFundamental.pe_ttm.isnot(None),
            StockFundamental.pe_ttm > 0,
        ).all()
        by_key: Dict[Tuple[date, str], List[float]] = {}
        for d, ind, pe in rows:
            if ind:
                by_key.setdefault((d, ind), []).append(pe)
        history: Dict[str, List[float]] = {}
        for (d, ind), pes in by_key.items():
            history.setdefault(ind, []).append(float(np.median(pes)))
        return history

    def _load_etf_names(self, db: Session) -> List[Tuple[str, str]]:
        return [(e.etf_code, e.etf_name or "") for e in db.query(ETFBasic).all()]

    def _industry_momentum(self, db: Session, industry: str,
                           etf_names: List[Tuple[str, str]]) -> Optional[float]:
        """行业ETF关键词匹配的20日动量均值（产业趋势代理）"""
        keywords: List[str] = []
        for key, kws in INDUSTRY_ETF_KEYWORDS.items():
            if key in industry or industry in key:
                keywords.extend(kws)
        if not keywords:
            return None
        matched = [code for code, name in etf_names
                   if any(kw in name for kw in keywords)]
        if not matched:
            return None
        moms = []
        for code in matched[:5]:
            quotes = db.query(ETFQuotation.close_price).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(21).all()
            if len(quotes) < 21 or not quotes[-1].close_price:
                continue
            moms.append((quotes[0].close_price / quotes[-1].close_price - 1) * 100)
        if not moms:
            return None
        return round(float(np.mean(moms)), 2)

    # ---------------- 个股安全边际 ----------------

    def get_stock_picks(self, db: Session, top_n: int = 20,
                        max_peg: float = 1.5) -> List[dict]:
        """个股筛选：盈利正增长 + PEG 达标，按安全边际得分排序

        安全边际 = 0.6×PEG分（越低越好，PEG_CAP 封顶） + 0.4×估值历史分位反分
        """
        latest = db.query(func.max(StockFundamental.trade_date)).filter(
            StockFundamental.pe_ttm.isnot(None)
        ).scalar()
        if latest is None:
            return []
        rows = db.query(StockFundamental).filter(
            StockFundamental.trade_date == latest,
            StockFundamental.pe_ttm.isnot(None),
            StockFundamental.pe_ttm > 0,
            StockFundamental.ni_yoy.isnot(None),
            StockFundamental.ni_yoy > 0,
        ).all()

        candidates = []
        for r in rows:
            peg = r.pe_ttm / r.ni_yoy
            if peg <= max_peg:
                candidates.append((r, round(peg, 2)))
        # 候选过多时按 PEG 预筛，控制历史分位查询量
        candidates.sort(key=lambda x: x[1])
        candidates = candidates[:200]

        picks = []
        for r, peg in candidates:
            own_pe_pct = self._own_pe_percentile(db, r.stock_code, r.pe_ttm, latest)
            peg_score = max(0.0, 100 - peg / PEG_CAP * 100)
            # 历史样本不足时以中性 50 分计
            margin_score = 100 - own_pe_pct if own_pe_pct is not None else 50.0
            picks.append({
                "stock_code": r.stock_code.replace("sh.", "").replace("sz.", ""),
                "stock_name": r.stock_name,
                "industry": r.industry,
                "close": r.close,
                "pe_ttm": r.pe_ttm,
                "ni_yoy": r.ni_yoy,
                "peg": peg,
                "pe_percentile": own_pe_pct,
                "score": round(peg_score * 0.6 + margin_score * 0.4, 1),
            })
        picks.sort(key=lambda x: -x["score"])
        return picks[:top_n]

    def _own_pe_percentile(self, db: Session, stock_code: str,
                           current_pe: float, as_of: date) -> Optional[float]:
        """个股 PE 在自身历史（约2年）中的分位"""
        cutoff = as_of - timedelta(days=STOCK_PE_HISTORY_DAYS)
        pes = [p for (p,) in db.query(StockFundamental.pe_ttm).filter(
            StockFundamental.stock_code == stock_code,
            StockFundamental.trade_date <= as_of,
            StockFundamental.trade_date >= cutoff,
            StockFundamental.pe_ttm.isnot(None),
            StockFundamental.pe_ttm > 0,
        ).all()]
        if len(pes) < 30:
            return None
        return _percentile_rank(pes, current_pe)

    # ---------------- 读取 ----------------

    def get_industry_ranking(self, db: Session, days: int = 1) -> List[IndustryScore]:
        latest = db.query(func.max(IndustryScore.trade_date)).scalar()
        if latest is None:
            return []
        cutoff = latest - timedelta(days=days - 1)
        return db.query(IndustryScore).filter(
            IndustryScore.trade_date >= cutoff
        ).order_by(IndustryScore.trade_date.desc(), IndustryScore.rank.asc()).all()


_service = None


def get_value_model_service() -> ValueModelService:
    global _service
    if _service is None:
        _service = ValueModelService()
    return _service
