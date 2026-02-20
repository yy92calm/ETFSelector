"""ETF数据相关API"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.services.data_service import get_data_service

router = APIRouter(prefix="/api/etf", tags=["ETF数据"])


@router.get("/list", response_model=APIResponse)
def get_etf_list(db: Session = Depends(get_db)):
    """获取数据库中所有ETF列表"""
    svc = get_data_service()
    etfs = svc.get_etf_list(db)
    return APIResponse(
        data={
            "etfs": [{"etf_code": e.etf_code, "etf_name": e.etf_name} for e in etfs],
            "count": len(etfs),
        }
    )


@router.post("/sync-list", response_model=APIResponse)
def sync_etf_list(db: Session = Depends(get_db)):
    """从AKShare同步全市场ETF列表"""
    svc = get_data_service()
    count = svc.sync_etf_list(db)
    return APIResponse(message=f"同步完成，新增/更新 {count} 条", data={"count": count})


@router.get("/overview", response_model=APIResponse)
def get_market_overview(
    limit: int = Query(2000, ge=1, le=5000), db: Session = Depends(get_db)
):
    """获取全市场最新行情概览（按成交额排序）"""
    svc = get_data_service()
    data = svc.get_market_overview(db, limit=limit)
    return APIResponse(data={"quotes": data, "count": len(data)})


@router.get("/history/{etf_code}", response_model=APIResponse)
def get_etf_history(
    etf_code: str, start_date: date, end_date: date, db: Session = Depends(get_db)
):
    """获取指定ETF的历史行情"""
    svc = get_data_service()
    rows = svc.get_history(etf_code, start_date, end_date, db)
    return APIResponse(
        data={
            "etf_code": etf_code,
            "quotations": [
                {
                    "trade_date": r.trade_date.isoformat(),
                    "open": r.open_price,
                    "close": r.close_price,
                    "high": r.high_price,
                    "low": r.low_price,
                    "volume": r.volume,
                    "amount": r.amount,
                    "change_pct": r.change_pct,
                }
                for r in rows
            ],
            "count": len(rows),
        }
    )


@router.post("/fetch/{etf_code}", response_model=APIResponse)
def fetch_etf_data(
    etf_code: str, start_date: str = Query("20200101"), db: Session = Depends(get_db)
):
    """从AKShare拉取指定ETF历史行情并存储"""
    svc = get_data_service()
    df = svc.fetch_etf_daily(etf_code, start_date=start_date)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"未获取到 {etf_code} 的行情数据")

    # 确保ETF在基础表中
    from app.models.etf import ETFBasic

    existing = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
    if not existing:
        db.add(ETFBasic(etf_code=etf_code, etf_name=etf_code))
        db.commit()

    added = svc.save_daily_quotes(etf_code, df, db)
    return APIResponse(
        message=f"拉取完成",
        data={"etf_code": etf_code, "new_records": added, "total_rows": len(df)},
    )


@router.post("/update-today", response_model=APIResponse)
def update_today_quotes(db: Session = Depends(get_db)):
    """手动触发更新全市场最新交易日行情"""
    svc = get_data_service()
    result = svc.update_today_quotes(db)
    return APIResponse(message="行情更新完成", data=result)


@router.post("/init-sample", response_model=APIResponse)
def initialize_sample_data(db: Session = Depends(get_db)):
    """初始化热门ETF样本数据"""
    svc = get_data_service()
    result = svc.initialize_sample_data(db)
    return APIResponse(message="样本数据初始化完成", data=result)


@router.post("/update-range", response_model=APIResponse)
def update_quotes_by_range(
    start_date: str = Query(..., description="开始日期 YYYYMMDD"),
    end_date: str = Query(..., description="结束日期 YYYYMMDD"),
    db: Session = Depends(get_db)
):
    """批量更新指定日期范围内所有ETF的行情数据"""
    svc = get_data_service()
    result = svc.update_quotes_by_date_range(start_date, end_date, db)
    return APIResponse(
        message=f"行情更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}",
        data=result
    )
