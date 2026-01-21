"""
ETF相关的API路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import date

from app.db.database import get_db
from app.schemas.etf_schemas import (
    ETFBasicSchema, 
    ETFQuotationSchema, 
    ETFDetailSchema,
    FetchQuoteRequest,
    APIResponse
)
from app.services.data_service import get_etf_data_service, market_manager
from app.models.etf_basic import ETFBasic
from app.models.etf_quotation import ETFQuotation

router = APIRouter(prefix="/api/etf", tags=["ETF"])


@router.get("/list", response_model=APIResponse)
async def get_etf_list(db: Session = Depends(get_db)):
    """
    获取所有ETF列表
    """
    try:
        etfs = db.query(ETFBasic).all()
        return APIResponse(
            code=200,
            message="获取ETF列表成功",
            data={"etfs": [ETFBasicSchema.model_validate(etf) for etf in etfs]}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取ETF列表失败: {str(e)}")


@router.get("/latest/{etf_code}", response_model=APIResponse)
async def get_etf_latest_quote(etf_code: str, db: Session = Depends(get_db)):
    """
    获取ETF最新行情
    """
    try:
        # 获取ETF基础信息
        etf_basic = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
        if not etf_basic:
            raise HTTPException(status_code=404, detail=f"ETF代码 {etf_code} 不存在")
        
        # 获取最新行情
        quotation = db.query(ETFQuotation).filter(
            ETFQuotation.etf_code == etf_code
        ).order_by(ETFQuotation.trade_date.desc()).first()
        
        if not quotation:
            raise HTTPException(status_code=404, detail=f"未找到 {etf_code} 的行情数据")
        
        return APIResponse(
            code=200,
            message="获取最新行情成功",
            data={
                "quote": ETFQuotationSchema.model_validate(quotation)
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取最新行情失败: {str(e)}")


@router.get("/history/{etf_code}", response_model=APIResponse)
async def get_etf_history(
    etf_code: str,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db)
):
    """
    获取ETF历史行情
    """
    try:
        # 验证ETF是否存在
        etf_basic = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
        if not etf_basic:
            raise HTTPException(status_code=404, detail=f"ETF代码 {etf_code} 不存在")
        
        # 获取历史行情
        quotations = db.query(ETFQuotation).filter(
            ETFQuotation.etf_code == etf_code,
            ETFQuotation.trade_date >= start_date,
            ETFQuotation.trade_date <= end_date
        ).order_by(ETFQuotation.trade_date.asc()).all()
        
        return APIResponse(
            code=200,
            message="获取历史行情成功",
            data={
                "quotations": [ETFQuotationSchema.model_validate(q) for q in quotations]
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史行情失败: {str(e)}")


@router.post("/fetch-quotes", response_model=APIResponse)
async def fetch_etf_quotes(request: FetchQuoteRequest):
    """
    从Qtrade API获取并保存ETF行情数据
    """
    try:
        data_service = get_etf_data_service()
        results = await data_service.fetch_and_save_etf_quotes_batch(request.etf_codes)
        
        return APIResponse(
            code=200,
            message="行情数据获取成功",
            data=results
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取行情数据失败: {str(e)}")


@router.get("/detail/{etf_code}", response_model=APIResponse)
async def get_etf_detail(etf_code: str, db: Session = Depends(get_db)):
    """
    获取ETF详细信息（包含基础信息和最新行情）
    """
    try:
        # 获取ETF基础信息
        etf_basic = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()
        if not etf_basic:
            raise HTTPException(status_code=404, detail=f"ETF代码 {etf_code} 不存在")
        
        # 获取最新行情
        quotation = db.query(ETFQuotation).filter(
            ETFQuotation.etf_code == etf_code
        ).order_by(ETFQuotation.trade_date.desc()).first()
        
        if not quotation:
            raise HTTPException(status_code=404, detail=f"未找到 {etf_code} 的行情数据")
        
        detail = ETFDetailSchema(
            etf_code=etf_basic.etf_code,
            etf_name=etf_basic.etf_name,
            last_price=quotation.close_price,
            change_rate=quotation.change_rate,
            volume=quotation.volume,
            amount=quotation.amount
        )
        
        return APIResponse(
            code=200,
            message="获取ETF详细信息成功",
            data={"detail": detail}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取ETF详细信息失败: {str(e)}")


@router.post("/market/shanghai", response_model=APIResponse)
async def fetch_shanghai_market_quotes():
    """
    获取上证市场所有主流ETF行情数据
    """
    try:
        data_service = get_etf_data_service()
        results = await data_service.fetch_and_save_shanghai_market_quotes()
        
        return APIResponse(
            code=200,
            message="上证市场行情获取成功",
            data=results
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取上证市场行情失败: {str(e)}")


@router.post("/market/shenzhen", response_model=APIResponse)
async def fetch_shenzhen_market_quotes():
    """
    获取深证市场所有主流ETF行情数据
    """
    try:
        data_service = get_etf_data_service()
        results = await data_service.fetch_and_save_shenzhen_market_quotes()
        
        return APIResponse(
            code=200,
            message="深证市场行情获取成功",
            data=results
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取深证市场行情失败: {str(e)}")


@router.post("/market/all", response_model=APIResponse)
async def fetch_all_market_quotes():
    """
    获取上深全市场所有主流ETF行情数据
    """
    try:
        data_service = get_etf_data_service()
        results = await data_service.fetch_and_save_all_market_quotes()
        
        return APIResponse(
            code=200,
            message="上深全市场行情获取成功",
            data=results
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取上深全市场行情失败: {str(e)}")


@router.post("/market/{market_type}", response_model=APIResponse)
async def fetch_market_quotes(market_type: str):
    """
    获取指定市场所有ETF行情数据
    支持的市场类型: shanghai, shenzhen, all
    """
    try:
        data_service = get_etf_data_service()
        results = await data_service.fetch_and_save_market_quotes(market_type)
        
        return APIResponse(
            code=200,
            message=f"{market_type}市场行情获取成功",
            data=results
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取{market_type}市场行情失败: {str(e)}")


@router.get("/market/{market_type}/etfs", response_model=APIResponse)
async def get_market_etfs(market_type: str, db: Session = Depends(get_db)):
    """
    获取指定市场的ETF代码列表
    支持的市场类型: shanghai, shenzhen, all, all_etfs
    """
    try:
        from app.services.data_service import market_manager
        etf_codes = market_manager.get_market_etfs(market_type)
        
        # 如果是all_etfs类型，从数据库更新全市场ETF列表
        if market_type == "all_etfs":
            market_manager.update_all_etfs_from_db(db)
            etf_codes = market_manager.get_market_etfs(market_type)
        
        if not etf_codes:
            raise HTTPException(status_code=404, detail=f"未知的市场类型: {market_type}")
        
        return APIResponse(
            code=200,
            message=f"获取{market_type}市场ETF列表成功",
            data={"etf_codes": etf_codes, "count": len(etf_codes)}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取{market_type}市场ETF列表失败: {str(e)}")


@router.post("/market/{market_type}/etfs", response_model=APIResponse)
async def add_etf_to_market(market_type: str, request: FetchQuoteRequest):
    """
    向指定市场添加ETF代码
    支持的市场类型: shanghai, shenzhen, all
    """
    try:
        from app.services.data_service import market_manager
        added_count = 0
        
        for etf_code in request.etf_codes:
            if market_manager.add_etf_to_market(etf_code, market_type):
                added_count += 1
        
        return APIResponse(
            code=200,
            message=f"成功向{market_type}市场添加{added_count}个ETF代码",
            data={"added_count": added_count, "total_count": len(market_manager.get_market_etfs(market_type))}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"向{market_type}市场添加ETF代码失败: {str(e)}")


@router.delete("/market/{market_type}/etfs", response_model=APIResponse)
async def remove_etf_from_market(market_type: str, request: FetchQuoteRequest):
    """
    从指定市场移除ETF代码
    支持的市场类型: shanghai, shenzhen, all
    """
    try:
        from app.services.data_service import market_manager
        removed_count = 0
        
        for etf_code in request.etf_codes:
            if market_manager.remove_etf_from_market(etf_code, market_type):
                removed_count += 1
        
        return APIResponse(
            code=200,
            message=f"成功从{market_type}市场移除{removed_count}个ETF代码",
            data={"removed_count": removed_count, "total_count": len(market_manager.get_market_etfs(market_type))}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"从{market_type}市场移除ETF代码失败: {str(e)}")


@router.get("/market/shanghai/quotes", response_model=APIResponse)
async def get_shanghai_market_quotes(db: Session = Depends(get_db)):
    """
    获取上证市场所有主流ETF的最新行情
    """
    try:
        data_service = get_etf_data_service()
        quotes = data_service.get_market_etf_quotes("shanghai", db)
        
        return APIResponse(
            code=200,
            message="获取上证市场行情成功",
            data={"quotes": quotes, "count": len(quotes)}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取上证市场行情失败: {str(e)}")


@router.get("/market/shenzhen/quotes", response_model=APIResponse)
async def get_shenzhen_market_quotes(db: Session = Depends(get_db)):
    """
    获取深证市场所有主流ETF的最新行情
    """
    try:
        data_service = get_etf_data_service()
        quotes = data_service.get_market_etf_quotes("shenzhen", db)
        
        return APIResponse(
            code=200,
            message="获取深证市场行情成功",
            data={"quotes": quotes, "count": len(quotes)}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取深证市场行情失败: {str(e)}")


@router.get("/market/all/quotes", response_model=APIResponse)
async def get_all_market_quotes(db: Session = Depends(get_db)):
    """
    获取上深全市场所有主流ETF的最新行情
    """
    try:
        data_service = get_etf_data_service()
        quotes = data_service.get_market_etf_quotes("all", db)
        
        return APIResponse(
            code=200,
            message="获取上深全市场行情成功",
            data={"quotes": quotes, "count": len(quotes)}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取上深全市场行情失败: {str(e)}")


@router.get("/market/all_etfs", response_model=APIResponse)
async def get_all_etfs_list(db: Session = Depends(get_db)):
    """
    获取全市场ETF列表（从API获取最新数据）
    """
    try:
        # 从API获取最新的全市场ETF列表
        count = await market_manager.update_all_etfs_from_api()
        
        # 获取更新后的ETF列表
        etf_codes = market_manager.get_market_etfs("all_etfs")
        
        return APIResponse(
            code=200,
            message="获取全市场ETF列表成功",
            data={"etf_codes": etf_codes, "count": len(etf_codes)}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取全市场ETF列表失败: {str(e)}")


@router.post("/scheduler/{action}", response_model=APIResponse)
async def manage_scheduler(action: str):
    """
    管理定时任务调度器
    支持的操作: start, stop, status
    """
    try:
        from app.tasks.scheduler import get_etf_scheduler
        scheduler = get_etf_scheduler()
        
        if action == "start":
            scheduler.start()
            return APIResponse(
                code=200,
                message="调度器已启动",
                data={"status": "started"}
            )
        elif action == "stop":
            scheduler.shutdown()
            return APIResponse(
                code=200,
                message="调度器已停止",
                data={"status": "stopped"}
            )
        elif action == "status":
            # 检查调度器是否正在运行
            is_running = bool(scheduler.scheduler.running)
            jobs = []
            if is_running:
                jobs = [job.id for job in scheduler.scheduler.get_jobs()]
            
            return APIResponse(
                code=200,
                message="调度器状态",
                data={"status": "running" if is_running else "stopped", "jobs_count": len(jobs), "jobs": jobs}
            )
        else:
            raise HTTPException(status_code=400, detail=f"不支持的操作: {action}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"调度器管理失败: {str(e)}")
