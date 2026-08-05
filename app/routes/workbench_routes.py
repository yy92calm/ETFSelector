"""工作台状态 API"""

import logging
from datetime import date, timedelta
from typing import List

import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.models.strategy import Strategy
from app.models.chat import AIActionLog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workbench", tags=["工作台"])


@router.get("/overview", response_model=APIResponse)
def get_workbench_overview(db: Session = Depends(get_db)):
    """工作台总览：系统状态、策略概况、AI最近决策"""
    from app.models.etf import ETFBasic, ETFQuotation
    from app.models.portfolio import PortfolioSnapshot
    from sqlalchemy import func

    # 策略统计
    total_strategies = db.query(Strategy).count()
    active_strategies = db.query(Strategy).filter(Strategy.status == "active").count()
    auto_running = db.query(Strategy).filter(
        Strategy.strategy_source == "auto_generated",
        Strategy.auto_strategy_status == "running",
    ).count()

    # ETF数据量
    etf_count = db.query(ETFBasic).count()
    latest_quote_date = db.query(func.max(ETFQuotation.trade_date)).scalar()

    # 最近AI决策
    recent_actions = (
        db.query(AIActionLog)
        .order_by(AIActionLog.created_at.desc())
        .limit(5)
        .all()
    )

    # 待审批项
    pending_approvals = (
        db.query(AIActionLog)
        .filter(AIActionLog.approval_status == "pending")
        .count()
    )

    return APIResponse(data={
        "strategies": {
            "total": total_strategies,
            "active": active_strategies,
            "auto_running": auto_running,
        },
        "data": {
            "etf_count": etf_count,
            "latest_quote_date": latest_quote_date.isoformat() if latest_quote_date else None,
        },
        "ai": {
            "pending_approvals": pending_approvals,
            "recent_actions": [a.to_dict() for a in recent_actions],
        },
    })


@router.get("/activity", response_model=APIResponse)
def get_ai_activity(days: int = 7, db: Session = Depends(get_db)):
    """AI活动日志：最近自主决策记录"""
    cutoff = date.today() - timedelta(days=days)

    logs = (
        db.query(AIActionLog)
        .filter(AIActionLog.created_at >= cutoff.isoformat())
        .order_by(AIActionLog.created_at.desc())
        .limit(50)
        .all()
    )

    return APIResponse(data={
        "total": len(logs),
        "period_days": days,
        "activities": [log.to_dict() for log in logs],
    })


@router.get("/quant-summary", response_model=APIResponse)
def get_quant_summary(db: Session = Depends(get_db)):
    """市场量化分析概况：趋势分布、动量排名、波动率"""
    from app.models.etf import ETFQuotation, ETFBasic
    from app.utils.trading_calendar import is_during_trading_hours, get_previous_trading_day
    from sqlalchemy import func

    try:
        name_map = {e.etf_code: e.etf_name for e in db.query(ETFBasic).all()}

        # 交易时段显示T-1，闭市后显示最新
        if is_during_trading_hours():
            latest_date = get_previous_trading_day(date.today())
        else:
            latest_date = db.query(func.max(ETFQuotation.trade_date)).scalar()
        if not latest_date:
            return APIResponse(data={"error": "无行情数据"})

        # 取最近60个交易日行情用于计算指标
        cutoff_date = latest_date - timedelta(days=90)
        rows = (
            db.query(ETFQuotation)
            .filter(ETFQuotation.trade_date >= cutoff_date)
            .order_by(ETFQuotation.trade_date.asc())
            .all()
        )

        # 按ETF分组
        from collections import defaultdict
        etf_data = defaultdict(list)
        for r in rows:
            etf_data[r.etf_code].append(r)

        # 计算每只ETF的技术指标
        trend_dist = {"strong_bullish": 0, "bullish": 0, "neutral": 0, "bearish": 0, "strong_bearish": 0}
        momentum_list = []  # (code, name, 5d_change, 20d_change)
        volatility_list = []  # (code, name, ann_vol)
        rsi_list = []

        for code, quotes in etf_data.items():
            if len(quotes) < 20:
                continue
            prices = [q.close_price for q in quotes]
            name = name_map.get(code, code)

            # 趋势判断: MA5 vs MA10 vs MA20
            ma5 = np.mean(prices[-5:])
            ma10 = np.mean(prices[-10:])
            ma20 = np.mean(prices[-20:])
            current = prices[-1]

            bull = sum([current > ma5, ma5 > ma10, ma10 > ma20, current > ma20])
            if bull >= 4:
                trend_dist["strong_bullish"] += 1
            elif bull == 3:
                trend_dist["bullish"] += 1
            elif bull <= 0:
                trend_dist["strong_bearish"] += 1
            elif bull == 1:
                trend_dist["bearish"] += 1
            else:
                trend_dist["neutral"] += 1

            # 动量: 5日/20日涨幅
            if len(prices) >= 21:
                chg_5d = (prices[-1] - prices[-6]) / prices[-6] * 100
                chg_20d = (prices[-1] - prices[-21]) / prices[-21] * 100
                momentum_list.append({"code": code, "name": name, "chg_5d": round(chg_5d, 2), "chg_20d": round(chg_20d, 2)})

            # 年化波动率 (20日)
            if len(prices) >= 21:
                rets = np.diff(prices[-21:]) / np.array(prices[-21:-1])
                ann_vol = float(np.std(rets) * np.sqrt(252) * 100)
                volatility_list.append({"code": code, "name": name, "ann_vol": round(ann_vol, 2)})

            # RSI(14)
            if len(prices) >= 15:
                changes = np.diff(prices[-15:])
                gains = np.where(changes > 0, changes, 0)
                losses = np.where(changes < 0, -changes, 0)
                avg_gain = np.mean(gains)
                avg_loss = np.mean(losses)
                rsi = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
                rsi_list.append({"code": code, "name": name, "rsi": round(rsi, 1)})

        # 排序
        momentum_list.sort(key=lambda x: x["chg_5d"], reverse=True)
        volatility_list.sort(key=lambda x: x["ann_vol"], reverse=True)

        # 市场整体RSI
        avg_rsi = round(np.mean([r["rsi"] for r in rsi_list]), 1) if rsi_list else None
        # 市场整体波动率
        avg_vol = round(np.mean([v["ann_vol"] for v in volatility_list]), 2) if volatility_list else None

        total_etfs = sum(trend_dist.values())

        return APIResponse(data={
            "latest_date": latest_date.isoformat(),
            "total_analyzed": total_etfs,
            "trend_distribution": trend_dist,
            "market_rsi": avg_rsi,
            "market_volatility": avg_vol,
            "momentum_top": momentum_list[:8],
            "momentum_bottom": momentum_list[-8:][::-1] if len(momentum_list) >= 8 else [],
            "volatility_top": volatility_list[:8],
        })
    except Exception as e:
        logger.error(f"量化分析失败: {e}")
        return APIResponse(code=500, message=str(e))


class ApproveRequest(BaseModel):
    action_id: int
    approved: bool


@router.post("/approve", response_model=APIResponse)
def approve_action(request: ApproveRequest, db: Session = Depends(get_db)):
    """人工审批AI建议的操作"""
    log = db.query(AIActionLog).filter(AIActionLog.id == request.action_id).first()
    if not log:
        return APIResponse(code=404, message="记录不存在")

    log.approval_status = "approved" if request.approved else "rejected"
    db.commit()

    return APIResponse(data={
        "action_id": request.action_id,
        "approval_status": log.approval_status,
        "message": "已批准" if request.approved else "已拒绝",
    })


@router.get("/market-indicators", response_model=APIResponse)
def get_market_indicators(
    sort_by: str = "composite_score",
    desc: bool = True,
    limit: int = 50,
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    """行情+量化指标联合查询（行情页用），支持分页与跨表排序"""
    from app.models.etf import ETFBasic, ETFQuotation, ETFDailyIndicator
    from app.utils.trading_calendar import get_display_date

    # 使用交易日历获取显示日期（交易时段显示T-1，闭市后显示T日）
    display_date = get_display_date()
    if not display_date:
        return APIResponse(data={"rows": [], "date": None, "total": 0, "page": page, "page_size": page_size})

    _SORT_COLS = {
        "composite_score": ETFDailyIndicator.composite_score,
        "momentum_5d": ETFDailyIndicator.momentum_5d,
        "momentum_20d": ETFDailyIndicator.momentum_20d,
        "volatility_20d": ETFDailyIndicator.volatility_20d,
        "vol_ratio": ETFDailyIndicator.vol_ratio,
        "trend_strength": ETFDailyIndicator.trend_strength,
        "close_price": ETFQuotation.close_price,
        "change_pct": ETFQuotation.change_pct,
        "etf_name": ETFBasic.etf_name,
    }

    query = (
        db.query(ETFDailyIndicator, ETFQuotation, ETFBasic)
        .join(ETFQuotation, (ETFQuotation.etf_code == ETFDailyIndicator.etf_code)
              & (ETFQuotation.trade_date == ETFDailyIndicator.trade_date), isouter=True)
        .join(ETFBasic, ETFBasic.etf_code == ETFDailyIndicator.etf_code, isouter=True)
        .filter(ETFDailyIndicator.trade_date == display_date)
    )

    if q:
        query = query.filter(
            (ETFDailyIndicator.etf_code.contains(q)) | (ETFBasic.etf_name.contains(q))
        )

    total = query.count()

    sort_col = _SORT_COLS.get(sort_by, ETFDailyIndicator.composite_score)
    query = query.order_by(sort_col.desc() if desc else sort_col.asc())

    rows = []
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    for ind, quote, basic in query.offset((page - 1) * page_size).limit(page_size).all():
        rows.append({
            "etf_code": ind.etf_code,
            "etf_name": basic.etf_name if basic else "",
            "close_price": quote.close_price if quote else None,
            "change_pct": quote.change_pct if quote else None,
            "amount": quote.amount if quote else None,
            "composite_score": ind.composite_score,
            "rank": ind.rank_in_market,
            "momentum_5d": ind.momentum_5d,
            "momentum_20d": ind.momentum_20d,
            "trend_strength": ind.trend_strength,
            "volatility_20d": ind.volatility_20d,
            "vol_ratio": ind.vol_ratio,
            "ma5": ind.ma5,
            "ma10": ind.ma10,
            "ma20": ind.ma20,
        })

    return APIResponse(data={
        "rows": rows,
        "date": display_date.isoformat(),
        "total": total,
        "page": page,
        "page_size": page_size,
    })
