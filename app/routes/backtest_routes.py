"""回测API"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse, BacktestRequest
from app.services.backtest_service import get_backtest_engine
from app.services.strategy_service import get_strategy_service

router = APIRouter(prefix="/api/backtest", tags=["回测"])


@router.post("/run", response_model=APIResponse)
def run_backtest(req: BacktestRequest, db: Session = Depends(get_db)):
    """执行策略回测"""
    svc = get_strategy_service()
    strategy = svc.get_strategy(req.strategy_id, db)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    engine = get_backtest_engine()
    initial = req.initial_capital or strategy.initial_capital

    try:
        result = engine.run(
            strategy=strategy,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=float(initial),
            db=db,
        )
        return APIResponse(message="回测完成", data=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测失败: {str(e)}")
