#!/usr/bin/env python
"""
初始化脚本 - 创建数据库并添加示例数据
"""

import sys
from app.db.database import init_db, SessionLocal
from app.models.etf_basic import ETFBasic
from app.models.etf_quotation import ETFQuotation
from datetime import datetime, date

def init_sample_data():
    """初始化示例数据"""
    db = SessionLocal()
    
    try:
        # 创建数据库表
        print("初始化数据库表...")
        init_db()
        
        # 添加示例ETF
        print("添加示例ETF数据...")
        sample_etfs = [
            {
                "etf_code": "sh510050",
                "etf_name": "华夏上证50ETF",
                "issuer": "华夏基金"
            },
            {
                "etf_code": "sh510300",
                "etf_name": "华夏沪深300ETF",
                "issuer": "华夏基金"
            },
            {
                "etf_code": "sh510500",
                "etf_name": "华夏中证500ETF",
                "issuer": "华夏基金"
            },
            {
                "etf_code": "sh510610",
                "etf_name": "易方达消费行业",
                "issuer": "易方达基金"
            },
            {
                "etf_code": "sh510180",
                "etf_name": "上证180ETF",
                "issuer": "华安基金"
            },
            {
                "etf_code": "sh511800",
                "etf_name": "易方达中证800ETF",
                "issuer": "易方达基金"
            },
            {
                "etf_code": "sz150018",
                "etf_name": "鹏华创业板A",
                "issuer": "鹏华基金"
            },
            {
                "etf_code": "sz159915",
                "etf_name": "易方达创业板ETF",
                "issuer": "易方达基金"
            },
            {
                "etf_code": "sz159920",
                "etf_name": "华夏恒生ETF",
                "issuer": "华夏基金"
            },
            {
                "etf_code": "sz159949",
                "etf_name": "华夏创业板ETF",
                "issuer": "华夏基金"
            },
            {
                "etf_code": "sz159935",
                "etf_name": "广发医药卫生ETF",
                "issuer": "广发基金"
            },
            {
                "etf_code": "sz159999",
                "etf_name": "华夏半导体ETF",
                "issuer": "华夏基金"
            },
            # 添加更多ETF代码以支持全市场获取
            {
                "etf_code": "sh512690",
                "etf_name": "国泰中证传媒ETF",
                "issuer": "国泰基金"
            },
            {
                "etf_code": "sh512880",
                "etf_name": "国泰中证全指证券公司ETF",
                "issuer": "国泰基金"
            },
            {
                "etf_code": "sh513050",
                "etf_name": "华夏中证5G通信主题ETF",
                "issuer": "华夏基金"
            },
            {
                "etf_code": "sh513500",
                "etf_name": "博时标普500ETF",
                "issuer": "博时基金"
            },
            {
                "etf_code": "sh513100",
                "etf_name": "易方达标普信息科技ETF",
                "issuer": "易方达基金"
            },
            {
                "etf_code": "sz159928",
                "etf_name": "汇添富中证主要消费ETF",
                "issuer": "汇添富基金"
            },
            {
                "etf_code": "sz159934",
                "etf_name": "易方达标普全球高端消费品ETF",
                "issuer": "易方达基金"
            },
            {
                "etf_code": "sz159941",
                "etf_name": "华夏中证新能源汽车ETF",
                "issuer": "华夏基金"
            },
            {
                "etf_code": "sz159805",
                "etf_name": "易方达中证人工智能主题ETF",
                "issuer": "易方达基金"
            },
            {
                "etf_code": "sz159825",
                "etf_name": "国泰中证煤炭ETF",
                "issuer": "国泰基金"
            },
            {
                "etf_code": "sz159919",
                "etf_name": "嘉实沪深300ETF",
                "issuer": "嘉实基金"
            },
            {
                "etf_code": "sz159922",
                "etf_name": "嘉实中证中期国债ETF",
                "issuer": "嘉实基金"
            }
        ]
        
        for etf_data in sample_etfs:
            # 检查是否已存在
            existing = db.query(ETFBasic).filter(
                ETFBasic.etf_code == etf_data["etf_code"]
            ).first()
            
            if not existing:
                etf = ETFBasic(**etf_data)
                db.add(etf)
                print(f"  ✓ 添加 {etf_data['etf_name']}")
            else:
                print(f"  - {etf_data['etf_name']} 已存在")
        
        db.commit()
        print("\n数据库初始化完成！")
        
        # 更新ETF市场管理器的全市场ETF列表
        print("更新全市场ETF列表...")
        from app.services.data_service import market_manager
        market_manager.update_all_etfs_from_db(db)
        print(f"已从数据库加载 {market_manager.get_all_etfs_count()} 个ETF到全市场列表")
        
    except Exception as e:
        print(f"初始化失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()
    
    return True

if __name__ == "__main__":
    success = init_sample_data()
    sys.exit(0 if success else 1)
