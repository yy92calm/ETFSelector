"""
基于净值的ETF API路由
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.services.net_value_service import get_net_value_service

router = APIRouter(prefix="/api/net-value", tags=["净值数据"])


@router.get("/overview", response_model=APIResponse)
def get_net_value_overview(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """获取ETF净值概览（基于证监会官方净值数据）"""
    svc = get_net_value_service()
    data = svc.get_net_value_overview(db, limit=limit)
    
    return APIResponse(
        message="净值数据概览",
        data={
            "etfs": data,
            "count": len(data),
            "note": "数据基于证监会官方净值披露（无成交量成交额）"
        }
    )


@router.post("/update-single/{etf_code}", response_model=APIResponse)
def update_single_etf_net_value(
    etf_code: str,
    db: Session = Depends(get_db)
):
    """更新单只ETF净值数据（从证监会获取）"""
    svc = get_net_value_service()
    result = svc.fetch_and_save_net_value(etf_code, db)
    
    if result['success']:
        return APIResponse(
            message=f"净值数据更新成功",
            data=result
        )
    else:
        return APIResponse(
            message=f"净值数据获取失败",
            data=result
        )


@router.post("/batch-update", response_model=APIResponse)
def batch_update_net_values(
    limit: int = Query(60, ge=1, le=100, description="每次最多更新数量（频率限制已放开）"),
    db: Session = Depends(get_db)
):
    """
    批量拉取ETF净值数据（从证监会）
    
    注意：
    - 证监会频率限制：1秒1次（已放开）
    - 自动获取所有页面的历史数据（每页20条）
    - 支持分页，每只ETF可获取完整历史净值
    - 建议limit设置为20（对应20只ETF）
    """
    svc = get_net_value_service()
    result = svc.batch_update_net_values(db, limit=limit)
    
    return APIResponse(
        message=f"批量拉取完成: 成功 {result['success_count']}, 失败 {result['fail_count']}",
        data=result
    )


@router.get("/history/{etf_code}", response_model=APIResponse)
def get_etf_net_value_history(
    etf_code: str,
    db: Session = Depends(get_db)
):
    """获取ETF历史净值数据"""
    from app.models.etf import ETFQuotation
    from datetime import date, timedelta
    
    # 获取最近1年的净值数据
    start_date = date.today() - timedelta(days=365)
    
    quotes = db.query(ETFQuotation).filter(
        ETFQuotation.etf_code == etf_code,
        ETFQuotation.trade_date >= start_date
    ).order_by(ETFQuotation.trade_date.asc()).all()
    
    return APIResponse(
        data={
            "etf_code": etf_code,
            "net_values": [
                {
                    "trade_date": q.trade_date.isoformat(),
                    "net_value": q.close_price,  # 净值
                    "net_value_change_pct": q.change_pct,  # 净值增长率
                }
                for q in quotes
            ],
            "count": len(quotes)
        }
    )
