"""市场数据工具 - ETF行情查询与同步"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.tools.registry import tool

logger = logging.getLogger(__name__)


@tool(name="get_market_overview", description="获取全市场ETF行情概览，包含代码、名称、最新价、涨跌幅等")
def get_market_overview(db: Session, limit: int = 50) -> dict:
    from app.services.data_service import get_data_service

    svc = get_data_service()
    data = svc.get_market_overview(db, limit=limit)
    return {
        "total": len(data),
        "etfs": data[:limit],
    }


@tool(name="get_etf_detail", description="获取单只ETF的详细信息和近期走势（最近30个交易日）")
def get_etf_detail(db: Session, etf_code: str) -> dict:
    from app.models.etf import ETFBasic, ETFQuotation

    etf = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
    if not etf:
        return {"error": f"ETF {etf_code} 不存在"}

    cutoff = date.today() - timedelta(days=45)
    quotes = (
        db.query(ETFQuotation)
        .filter(ETFQuotation.etf_code == etf_code, ETFQuotation.trade_date >= cutoff)
        .order_by(ETFQuotation.trade_date.desc())
        .limit(30)
        .all()
    )

    recent = [
        {
            "date": q.trade_date.isoformat(),
            "close": q.close_price,
            "change_pct": q.change_pct,
            "volume": q.volume,
        }
        for q in quotes
    ]

    latest = quotes[0] if quotes else None
    return {
        "etf_code": etf_code,
        "etf_name": etf.etf_name,
        "latest_close": latest.close_price if latest else None,
        "latest_change_pct": latest.change_pct if latest else None,
        "latest_date": latest.trade_date.isoformat() if latest else None,
        "recent_30d": recent,
    }


@tool(name="get_etf_history", description="获取指定ETF在日期范围内的历史行情数据")
def get_etf_history(db: Session, etf_code: str, start_date: str, end_date: str) -> dict:
    from datetime import datetime
    from app.models.etf import ETFQuotation

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return {"error": "日期格式错误，请使用 YYYY-MM-DD"}

    quotes = (
        db.query(ETFQuotation)
        .filter(
            ETFQuotation.etf_code == etf_code,
            ETFQuotation.trade_date >= sd,
            ETFQuotation.trade_date <= ed,
        )
        .order_by(ETFQuotation.trade_date.asc())
        .all()
    )

    data = [
        {
            "date": q.trade_date.isoformat(),
            "open": q.open_price,
            "close": q.close_price,
            "high": q.high_price,
            "low": q.low_price,
            "volume": q.volume,
            "change_pct": q.change_pct,
        }
        for q in quotes
    ]

    return {"etf_code": etf_code, "period": f"{start_date}~{end_date}", "count": len(data), "data": data}


@tool(name="sync_market_data", description="触发ETF行情数据同步（更新最新交易日数据），耗时操作")
def sync_market_data(db: Session) -> dict:
    from app.services.data_service import get_data_service

    svc = get_data_service()
    result = svc.update_today_quotes(db)
    return {
        "success_count": result.get("success_count", 0),
        "fail_count": result.get("fail_count", 0),
        "message": f"同步完成: 成功{result.get('success_count', 0)}只, 失败{result.get('fail_count', 0)}只",
    }


@tool(name="search_etf", description="按关键词搜索全市场ETF（支持名称/代码模糊匹配），用于发现新的投资标的")
def search_etf(db: Session, keyword: str, limit: int = 10) -> dict:
    from sqlalchemy import text
    from app.models.etf import ETFBasic

    # 先从本地数据库搜索
    sql = text("""
        SELECT etf_code, etf_name FROM etf_basic
        WHERE etf_code LIKE :q OR etf_name LIKE :q
        ORDER BY etf_code LIMIT :limit
    """)
    rows = db.execute(sql, {"q": f"%{keyword}%", "limit": limit}).fetchall()
    local_results = [{"etf_code": r[0], "etf_name": r[1], "source": "local"} for r in rows]

    # 如果本地结果不足，尝试从数据源获取全市场列表再过滤
    if len(local_results) < 3:
        try:
            from app.services.data_service import get_data_service
            svc = get_data_service()
            df = svc.fetch_etf_list()
            if not df.empty:
                mask = df.apply(
                    lambda row: keyword in str(row.get("etf_name", "")) or keyword in str(row.get("etf_code", "")),
                    axis=1,
                )
                matched = df[mask].head(limit)
                for _, row in matched.iterrows():
                    code = str(row.get("etf_code", "") or row.get("代码", ""))
                    name = str(row.get("etf_name", "") or row.get("名称", ""))
                    if code and not any(r["etf_code"] == code for r in local_results):
                        local_results.append({"etf_code": code, "etf_name": name, "source": "market"})
        except Exception as e:
            logger.warning(f"全市场搜索失败: {e}")

    return {"keyword": keyword, "total": len(local_results), "results": local_results[:limit]}


@tool(name="add_etf_to_pool", description="将新ETF纳入观察池：拉取历史行情数据并存入数据库。用于发现新标的后获取其数据")
def add_etf_to_pool(db: Session, etf_code: str, etf_name: str = "", start_date: str = "20230101") -> dict:
    from app.models.etf import ETFBasic
    from app.services.data_service import get_data_service

    # 检查是否已存在
    existing = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
    if existing:
        return {"success": True, "message": f"ETF {etf_code}({existing.etf_name}) 已在观察池中", "etf_code": etf_code, "already_exists": True}

    svc = get_data_service()

    # 拉取历史行情
    df = svc.fetch_etf_daily(etf_code, start_date=start_date)
    if df.empty:
        return {"success": False, "error": f"无法获取 {etf_code} 的行情数据，请确认代码正确"}

    # 添加到基础表
    name = etf_name or etf_code
    db.add(ETFBasic(etf_code=etf_code, etf_name=name))
    db.commit()

    # 保存行情数据
    added = svc.save_daily_quotes(etf_code, df, db)

    return {
        "success": True,
        "etf_code": etf_code,
        "etf_name": name,
        "history_records": added,
        "message": f"已将 {name}({etf_code}) 纳入观察池，导入 {added} 条历史行情",
    }
