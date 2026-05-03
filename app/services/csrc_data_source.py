"""
证监会官方净值数据源
访问频率限制：1分钟60次（每次间隔1秒）
"""

import logging
import time
from typing import Optional, Dict, List
from datetime import datetime
import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class CSRCRateLimiter:
    """证监会接口频率限制器：1分钟最多60次访问"""
    
    def __init__(self, max_calls: int = 60, period: int = 60):
        """
        Args:
            max_calls: 时间周期内最大调用次数（默认60次）
            period: 时间周期（默认60秒=1分钟）
        """
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.min_interval = 1.0  # 每次最小间隔1秒
    
    def wait_if_needed(self):
        """如果达到频率限制，等待"""
        now = time.time()
        
        # 移除超过时间周期的旧调用记录
        self.calls = [call_time for call_time in self.calls if now - call_time < self.period]
        
        # 如果达到限制，等待
        if len(self.calls) >= self.max_calls:
            wait_time = self.period - (now - self.calls[0])
            if wait_time > 0:
                logger.info(f"[证监会频率限制] 达到限制 ({self.max_calls}次/{self.period}秒)，等待 {wait_time:.1f} 秒")
                time.sleep(wait_time)
                # 清理旧记录
                self.calls = [call_time for call_time in self.calls if time.time() - call_time < self.period]
        
        # 检查最小间隔（确保每次调用间隔至少1秒）
        if self.calls:
            last_call_time = self.calls[-1]
            elapsed = now - last_call_time
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                logger.debug(f"[证监会频率限制] 等待间隔 {wait_time:.1f} 秒")
                time.sleep(wait_time)
        
        # 记录本次调用
        self.calls.append(time.time())
        logger.debug(f"[证监会频率限制] 本次调用记录，当前周期内已调用 {len(self.calls)} 次")


class CSRCDataSource:
    """证监会官方净值数据源"""
    
    def __init__(self):
        self.base_url = "http://eid.csrc.gov.cn/fund/disclose/list_net_daily.do"
        self.rate_limiter = CSRCRateLimiter(max_calls=60, period=60)  # 放开限制：1分钟60次
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'http://eid.csrc.gov.cn/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
    
    def get_source_name(self) -> str:
        return "csrc"  # China Securities Regulatory Commission
    
    def is_available(self) -> bool:
        """检查证监会数据源是否可用"""
        try:
            # 测试一次请求
            params = {'reportType': 'FB040', 'fundCode': '520880'}
            response = requests.get(
                self.base_url, 
                params=params, 
                headers=self.headers, 
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[证监会] 数据源检查失败: {e}")
            return False
    
    def fetch_etf_net_value(self, etf_code: str) -> pd.DataFrame:
        """
        获取单只ETF的历史净值数据（支持分页）
        
        Args:
            etf_code: ETF代码（如520880、159136）
        
        Returns:
            DataFrame包含：净值日期、份额净值、净值增长率等字段
        """
        logger.info(f"[证监会] 开始获取 {etf_code} 净值数据（支持分页）...")
        
        all_data = []
        page = 0
        
        while True:
            # 分页参数（每页20条）
            params = {
                'reportType': 'FB040',
                'fundCode': etf_code,
                'limit': 20,
                'start': page * 20
            }
            
            # 频率限制
            self.rate_limiter.wait_if_needed()
            
            try:
                response = requests.get(
                    self.base_url,
                    params=params,
                    headers=self.headers,
                    timeout=15
                )
                
                if response.status_code != 200:
                    logger.error(f"[证监会] {etf_code} 第{page+1}页请求失败: {response.status_code}")
                    break
                
                # 解析HTML
                soup = BeautifulSoup(response.content, 'html.parser')
                tables = soup.find_all('table', {'class': 'table-bordered'})
                
                if not tables:
                    logger.warning(f"[证监会] {etf_code} 第{page+1}页未找到数据表")
                    break
                
                # 解析净值表格
                table = tables[0]
                rows = table.find_all('tr')
                
                # 提取数据行
                data_rows = []
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 7:
                        row_data = [cell.get_text(strip=True) for cell in cells]
                        if row_data[0] == etf_code or row_data[0].isdigit():
                            data_rows.append(row_data)
                
                if not data_rows:
                    logger.info(f"[证监会] {etf_code} 第{page+1}页无数据，分页结束")
                    break
                
                # 转换为DataFrame
                df = pd.DataFrame(data_rows)
                df.columns = ['etf_code', 'level_code', 'etf_name', 'net_value', 'accumulated_value', 'asset_value', 'trade_date', 'remark']
                
                # 清理数据
                df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')
                df['net_value'] = pd.to_numeric(df['net_value'], errors='coerce')
                
                df = df.dropna(subset=['trade_date', 'net_value'])
                
                if df.empty:
                    break
                
                all_data.append(df)
                logger.info(f"[证监会] {etf_code} 第{page+1}页获取 {len(df)} 条净值数据")
                
                if len(df) < 20:
                    break
                
                page += 1
                
            except Exception as e:
                logger.error(f"[证监会] {etf_code} 第{page+1}页解析失败: {e}")
                break
        
        if not all_data:
            logger.warning(f"[证监会] {etf_code} 未获取到任何净值数据")
            return pd.DataFrame()
        
        final_df = pd.concat(all_data, ignore_index=True)
        
        # 计算净值增长率
        final_df = final_df.sort_values('trade_date')
        final_df['net_value_change_pct'] = final_df['net_value'].pct_change() * 100
        final_df['net_value_change_pct'] = final_df['net_value_change_pct'].fillna(0)
        
        logger.info(f"[证监会] {etf_code} 共获取 {len(final_df)} 条净值数据（{len(all_data)}页），已计算增长率")
        
        return final_df[['trade_date', 'net_value', 'net_value_change_pct', 'etf_name']]

    def fetch_etf_list(self) -> pd.DataFrame:
        """
        获取广发基金ETF列表
        
        注意：证监会接口没有ETF列表功能，需要从其他数据源获取
        这里返回一个硬编码的广发基金ETF列表作为示例
        """
        # 广发基金常见ETF列表（从数据库获取或手动维护）
        gf_etfs = [
            {'etf_code': '520880', 'etf_name': '港股通创新药ETF'},
            {'etf_code': '159136', 'etf_name': 'A50ETF广发'},
            {'etf_code': '159016', 'etf_name': '证券ETF广发'},
            {'etf_code': '159207', 'etf_name': '高股息ETF广发'},
            {'etf_code': '159262', 'etf_name': '港股通科技ETF广发'},
            {'etf_code': '159605', 'etf_name': '中概互联ETF广发'},
        ]
        
        df = pd.DataFrame(gf_etfs)
        logger.info(f"[证监会] 返回广发基金ETF列表 {len(df)} 条")
        return df


# 单例
_csrc_source: Optional[CSRCDataSource] = None


def get_csrc_data_source() -> CSRCDataSource:
    """获取证监会数据源单例"""
    global _csrc_source
    if _csrc_source is None:
        _csrc_source = CSRCDataSource()
    return _csrc_source
