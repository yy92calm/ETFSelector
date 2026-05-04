"""
广发基金ETF数据源配置
优先使用Baostock，添加频率限制（1分钟10次）
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)


class RateLimiter:
    """频率限制器：控制API调用频率"""
    
    def __init__(self, max_calls: int = 10, period: int = 60):
        """
        Args:
            max_calls: 时间周期内最大调用次数（默认10次）
            period: 时间周期（默认60秒=1分钟）
        """
        self.max_calls = max_calls
        self.period = period
        self.calls = []
    
    def wait_if_needed(self):
        """如果达到频率限制，等待"""
        now = time.time()
        
        # 移除超过时间周期的旧调用记录
        self.calls = [call_time for call_time in self.calls if now - call_time < self.period]
        
        # 如果达到限制，等待
        if len(self.calls) >= self.max_calls:
            wait_time = self.period - (now - self.calls[0])
            if wait_time > 0:
                logger.info(f"[频率限制] 达到限制 ({self.max_calls}次/{self.period}秒)，等待 {wait_time:.1f} 秒")
                time.sleep(wait_time)
                # 清理旧记录
                self.calls = [call_time for call_time in self.calls if time.time() - call_time < self.period]
        
        # 记录本次调用
        self.calls.append(time.time())


class DataSourceBase(ABC):
    """数据源抽象基类"""
    
    @abstractmethod
    def fetch_etf_list(self) -> pd.DataFrame:
        """获取广发基金ETF列表"""
        pass
    
    @abstractmethod
    def fetch_etf_daily(
        self, 
        etf_code: str, 
        start_date: str, 
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """获取单只ETF日K线"""
        pass
    
    @abstractmethod
    def get_source_name(self) -> str:
        """数据源名称"""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """检查数据源是否可用"""
        pass
    
    def is_gf_etf(self, etf_code: str) -> bool:
        """判断是否为广发基金ETF（代码特征）"""
        # 广发基金ETF代码特征：
        # - 上交所：5开头（510xxx, 511xxx, 512xxx, 513xxx等）
        # - 深交所：15开头（159xxx）
        code = etf_code.replace('sh', '').replace('sz', '').strip()
        return code.startswith('5') or code.startswith('15')


class BaostockDataSource(DataSourceBase):
    """Baostock数据源（优先使用）"""
    
    def __init__(self):
        self._bs = None
        self._logged_in = False
        self.rate_limiter = RateLimiter(max_calls=10, period=60)  # 1分钟10次
    
    def _login(self):
        """登录baostock"""
        if not self._logged_in:
            try:
                import baostock as bs
                lg = bs.login()
                if lg.error_code == '0':
                    self._bs = bs
                    self._logged_in = True
                    logger.info("[Baostock] 登录成功")
                else:
                    logger.error(f"[Baostock] 登录失败: {lg.error_msg}")
            except Exception as e:
                logger.error(f"[Baostock] 导入失败: {e}")
    
    def _logout(self):
        """登出baostock"""
        if self._logged_in and self._bs:
            self._bs.logout()
            self._logged_in = False
    
    def _format_date(self, date_str: str) -> str:
        """格式化日期为Baostock格式（YYYY-MM-DD）"""
        if '-' in date_str:
            return date_str
        # YYYYMMDD -> YYYY-MM-DD
        if len(date_str) == 8:
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_str
    
    def get_source_name(self) -> str:
        return "baostock"
    
    def is_available(self) -> bool:
        try:
            import baostock as bs
            return True
        except Exception:
            return False
    
    def fetch_etf_list(self) -> pd.DataFrame:
        """
        获取广发基金ETF列表
        Baostock没有ETF列表接口，通过AKShare获取名称包含'广发'的ETF
        后续使用Baostock通过指数映射获取行情
        """
        try:
            import akshare as ak
            
            # 频率限制
            self.rate_limiter.wait_if_needed()
            
            # 获取全市场ETF列表
            df = ak.fund_etf_spot_em()
            logger.info(f"[Baostock/AKShare] 获取全市场ETF {len(df)} 条")
            
            # 标准化列名
            df = df.rename(columns={
                '代码': 'etf_code',
                '名称': 'etf_name'
            })
            
            # 过滤广发基金ETF（名称包含'广发'）
            gf_df = df[df['etf_name'].str.contains('广发', na=False)].copy()
            logger.info(f"[Baostock] 过滤广发基金ETF {len(gf_df)} 条")
            
            if gf_df.empty:
                logger.warning("[Baostock] 未找到名称包含'广发'的ETF")
                return pd.DataFrame()
            
            # 添加数据源标记
            gf_df['data_source'] = 'baostock'
            gf_df['index_mapping'] = 'available'  # 标记可通过指数映射获取
            
            return gf_df[['etf_code', 'etf_name', 'data_source', 'index_mapping']]
            
        except Exception as e:
            logger.error(f"[Baostock/AKShare] 获取ETF列表失败: {e}")
            return pd.DataFrame()
    
    def fetch_etf_daily(
        self, 
        etf_code: str, 
        start_date: str, 
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        通过指数代码获取ETF对应指数数据
        ETF通常跟踪指数，用指数数据近似
        """
        # 检查是否为广发基金ETF
        if not self.is_gf_etf(etf_code):
            logger.warning(f"[Baostock] {etf_code} 不是广发基金ETF代码，跳过")
            return pd.DataFrame()
        
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        
        # 频率限制
        self.rate_limiter.wait_if_needed()
        
        self._login()
        
        if not self._logged_in:
            return pd.DataFrame()
        
        try:
            # ETF代码映射到指数代码
            index_map = {
                # === 宽基指数ETF ===
                '510050': 'sh.000016',    # 上证50
                '510300': 'sh.000300',    # 沪深300
                '510500': 'sh.000905',    # 中证500
                '510800': 'sh.000010',    # 上证180
                '512100': 'sh.000903',    # 中证100
                
                # === 广发基金宽基ETF ===
                '159136': 'sh.000688',    # A50ETF广发 -> 科创50（近似）
                '159919': 'sh.000300',    # 沪深300ETF广发
                '159949': 'sz.399673',    # 创业板50ETF广发
                '159576': 'sz.399330',    # 深证100ETF广发
                
                # === 行业/主题ETF ===
                '512170': 'sh.000842',    # 医药ETF -> 中证医药
                '512330': 'sh.000933',    # 军工ETF -> 中证国防
                '512660': 'sh.000986',    # 军工龙头ETF
                '512690': 'sh.000842',    # 酒ETF -> 医药指数（近似）
                '512880': 'sh.000932',    # 证券ETF -> 中证全指证券公司
                
                # === 广发基金行业ETF ===
                '159016': 'sz.399976',    # 证券ETF广发 -> 证券公司指数
                '159507': 'sh.000XXX',    # 通信ETF广发（待补充）
                '159512': 'sh.000XXX',    # 汽车ETF广发（待补充）
                '159527': 'sh.000XXX',    # 云计算ETF广发（待补充）
                '159539': 'sh.000XXX',    # 信创ETF广发（待补充）
                '159587': 'sh.000XXX',    # 粮食ETF广发（待补充）
                '159589': 'sh.000933',    # 红利ETF广发 -> 高股息指数（近似）
                '159608': 'sh.000XXX',    # 稀有金属ETF广发（待补充）
                '159611': 'sh.000XXX',    # 电力ETF广发（待补充）
                
                # === 特殊ETF（无映射） ===
                # '159262': 港股通科技ETF广发（Baostock不支持港股）
                # '159605': 中概互联ETF广发（无对应指数）
                # '159305': 储能电池ETF广发（无对应指数）
                # '159207': 高股息ETF广发（需要查找对应指数）
                # '159229': 自由现金流ETF广发（新概念，无指数）
            }
            
            index_code = index_map.get(etf_code)
            
            if not index_code:
                # 对于债券、黄金、美股等ETF，Baostock无对应数据
                logger.warning(f"[Baostock] {etf_code} 无对应指数代码")
                return pd.DataFrame()
            
            logger.info(f"[Baostock] {etf_code} -> {index_code} 获取数据...")
            
            # 查询指数K线
            rs = self._bs.query_history_k_data_plus(
                index_code,
                "date,code,open,high,low,close,volume,amount,turn",
                start_date=self._format_date(start_date),
                end_date=self._format_date(end_date),
                frequency="d",
                adjustflag="3"  # 不复权
            )
            
            if rs.error_code != '0':
                logger.error(f"[Baostock] {etf_code} 查询失败: {rs.error_msg}")
                return pd.DataFrame()
            
            data_list = []
            while (rs.error_code == '0') & rs.next():
                data_list.append(rs.get_row_data())
            
            if not data_list:
                logger.warning(f"[Baostock] {etf_code} 未获取到数据")
                return pd.DataFrame()
            
            df = pd.DataFrame(data_list, columns=[
                'trade_date', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn'
            ])
            
            # 数据类型转换
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df['open'] = pd.to_numeric(df['open'], errors='coerce')
            df['high'] = pd.to_numeric(df['high'], errors='coerce')
            df['low'] = pd.to_numeric(df['low'], errors='coerce')
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
            df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
            
            # 计算涨跌幅
            df['change_pct'] = df['close'].pct_change() * 100
            df['etf_code'] = etf_code
            
            logger.info(f"[Baostock] {etf_code} 获取 {len(df)} 条记录 ({start_date}~{end_date})")
            return df
            
        except Exception as e:
            logger.error(f"[Baostock] {etf_code} 获取失败: {e}")
            return pd.DataFrame()


class AKShareDataSource(DataSourceBase):
    """AKShare数据源（优先使用，获取ETF真实数据）"""
    
    def __init__(self):
        self.rate_limiter = RateLimiter(max_calls=8, period=60)  # 降低频率，1分钟8次（更安全）
    
    def get_source_name(self) -> str:
        return "akshare"
    
    def is_available(self) -> bool:
        try:
            import akshare as ak
            return True
        except Exception:
            return False
    
    def fetch_etf_list(self) -> pd.DataFrame:
        """获取ETF列表（广发、易方达、华夏三家基金公司）"""
        try:
            import akshare as ak
            
            # 频率限制
            self.rate_limiter.wait_if_needed()
            
            df = ak.fund_etf_spot_em()
            logger.info(f"[AKShare] 获取到 {len(df)} 条ETF记录")
            
            # 标准化列名
            df = df.rename(columns={
                '代码': 'etf_code',
                '名称': 'etf_name'
            })
            
            # 过滤三家基金公司的ETF（名称包含'广发'或'易方达'或'华夏')
            target_funds = ['广发', '易方达', '华夏']
            filtered_df = df[df['etf_name'].apply(lambda name: any(fund in str(name) for fund in target_funds))].copy()
            logger.info(f"[AKShare] 过滤后ETF {len(filtered_df)} 条（广发/易方达/华夏）")
            
            if filtered_df.empty:
                logger.warning(f"[AKShare] 未找到目标基金公司的ETF（{target_funds}）")
                return pd.DataFrame()
            
            # 添加数据源标记
            filtered_df['data_source'] = 'akshare'
            
            return filtered_df[['etf_code', 'etf_name', 'data_source']]
            
        except Exception as e:
            logger.error(f"[AKShare] 获取ETF列表失败: {e}")
            return pd.DataFrame()
    
    def fetch_etf_daily(
        self, 
        etf_code: str, 
        start_date: str, 
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """获取日K线数据（ETF真实数据，不需要指数映射）"""
        # 检查是否为广发基金ETF
        if not self.is_gf_etf(etf_code):
            logger.warning(f"[AKShare] {etf_code} 不是广发基金ETF代码，跳过")
            return pd.DataFrame()
        
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        
        # 频率限制（更严格）
        self.rate_limiter.wait_if_needed()
        
        try:
            import akshare as ak
            
            # AKShare使用纯数字代码
            code = etf_code.replace('sh', '').replace('sz', '')
            
            logger.info(f"[AKShare] 正在获取 {etf_code} 真实ETF数据...")
            
            # 尝试多次获取（添加重试机制）
            max_retries = 3
            for retry in range(max_retries):
                try:
                    df = ak.fund_etf_hist_em(
                        symbol=code,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust="qfq"  # 前复权
                    )
                    
                    if df.empty:
                        if retry < max_retries - 1:
                            logger.warning(f"[AKShare] {etf_code} 第{retry+1}次获取为空，等待后重试...")
                            time.sleep(2)  # 等待2秒后重试
                            continue
                        else:
                            logger.warning(f"[AKShare] {etf_code} 未获取到数据（尝试{max_retries}次）")
                            return pd.DataFrame()
                    
                    # 成功获取，跳出重试循环
                    break
                    
                except Exception as e:
                    if retry < max_retries - 1:
                        logger.warning(f"[AKShare] {etf_code} 第{retry+1}次失败: {e}，等待后重试...")
                        time.sleep(3)  # 等待3秒后重试
                        continue
                    else:
                        logger.error(f"[AKShare] {etf_code} 获取失败（尝试{max_retries}次）: {e}")
                        return pd.DataFrame()
            
            # 标准化列名
            df = df.rename(columns={
                '日期': 'trade_date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '涨跌幅': 'change_pct'
            })
            
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df['etf_code'] = etf_code
            
            # 确保数值类型正确
            df['open'] = pd.to_numeric(df['open'], errors='coerce')
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['high'] = pd.to_numeric(df['high'], errors='coerce')
            df['low'] = pd.to_numeric(df['low'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
            df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
            df['change_pct'] = pd.to_numeric(df['change_pct'], errors='coerce')
            
            logger.info(f"[AKShare] {etf_code} 成功获取 {len(df)} 条真实ETF数据 ({start_date}~{end_date})")
            logger.info(f"[AKShare] {etf_code} 最新数据: volume={df.iloc[-1]['volume']}, amount={df.iloc[-1]['amount']}")
            
            return df
            
        except Exception as e:
            logger.error(f"[AKShare] {etf_code} 获取失败: {e}")
            return pd.DataFrame()


class DataSourceManager:
    """数据源管理器：优先使用AKShare获取ETF真实数据"""
    
    def __init__(self):
        self.sources = []
        
        # 初始化数据源（AKShare优先，获取ETF真实数据）
        if AKShareDataSource().is_available():
            self.sources.append(AKShareDataSource())
            logger.info("✓ AKShare数据源已加载（优先使用，获取ETF真实数据）")
        
        if BaostockDataSource().is_available():
            self.sources.append(BaostockDataSource())
            logger.info("✓ Baostock数据源已加载（备用，仅用于指数映射获取）")
        
        if not self.sources:
            logger.warning("⚠ 无可用数据源")
    
    def fetch_etf_list(self) -> pd.DataFrame:
        """获取广发基金ETF列表"""
        for source in self.sources:
            df = source.fetch_etf_list()
            if not df.empty:
                logger.info(f"✓ 使用数据源: {source.get_source_name()}")
                return df
        
        logger.error("所有数据源均无法获取ETF列表")
        return pd.DataFrame()
    
    def fetch_etf_daily(
        self, 
        etf_code: str, 
        start_date: str, 
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """获取日K线（优先使用AKShare获取ETF真实数据）"""
        for source in self.sources:
            df = source.fetch_etf_daily(etf_code, start_date, end_date)
            if not df.empty:
                logger.info(f"✓ {etf_code} 使用数据源: {source.get_source_name()}")
                return df
            else:
                logger.warning(f"⚠ {etf_code} {source.get_source_name()} 获取失败，尝试下一个数据源")
        
        logger.error(f"✗ {etf_code} 所有数据源均失败")
        return pd.DataFrame()
    
    def get_available_sources(self) -> List[str]:
        """获取可用数据源列表"""
        return [s.get_source_name() for s in self.sources]


# 单例
_manager: Optional[DataSourceManager] = None


def get_data_source_manager() -> DataSourceManager:
    global _manager
    if _manager is None:
        _manager = DataSourceManager()
    return _manager