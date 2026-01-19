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
        
        db.commit()
        print("\n数据库初始化完成！")
        
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
