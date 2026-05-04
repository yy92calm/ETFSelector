"""
ETF配置组合生成器 - 对话式架构（简化版）
通过多轮对话让LLM自由处理配置生成
"""

import logging
import json
from typing import Optional, Dict, List
from openai import OpenAI
from app.config import get_settings
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)
settings = get_settings()


class ETFAllocationAgent:
    """ETF配置组合生成Agent - 对话式架构"""
    
    SYSTEM_PROMPT = """你是一个专业的ETF配置助手，通过自然对话帮助用户创建资产配置方案。

## 你的任务
1. 理解用户投资偏好（风险等级、资产类型、投资目标）
2. 从可用ETF列表中选择合适的ETF
3. 根据用户需求分配配置比例（总和必须等于1.0）
4. 用自然语言友好回复用户

## 配置原则（仅供参考）
- 保守型：债券为主(60-80%)，少量股票(20-30%)
- 均衡型：股债平衡(50-60%股票，30-40%债券)
- 激进型：成长股为主(60-80%)，少量债券(20-30%)
- 建议3-5只ETF，避免过度集中

## 输出格式
必须返回JSON格式（不要包含其他文字）：
{
  "reply": "友好的回复文本（100字以内）",
  "allocation": {"ETF代码": 比例, "ETF代码": 比例},
  "confidence": "high/medium/low"
}

注意：
- reply字段：用自然语言简要说明配置特点
- allocation字段：比例总和必须等于1.0
- confidence字段：表示配置方案的信心程度"""

    def __init__(self):
        self.client = None
        
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def _classify_etf(self, code: str, name: str) -> str:
        """ETF分类（根据代码和名称判断）"""
        if "债" in name or "货币" in name or code.startswith("511"):
            return "债券"
        elif "黄金" in name or "金" in name or code.startswith("518"):
            return "黄金"
        elif "科创" in name or code.startswith("588") or code.startswith("589"):
            return "科创板"
        elif code.startswith("159"):
            return "创业板"
        elif "医药" in name or "医疗" in name:
            return "医药"
        elif "科技" in name or "芯片" in name or "半导体" in name:
            return "科技"
        elif "新能源" in name or "光伏" in name or "锂电" in name:
            return "新能源"
        elif "300" in name or "沪深" in name:
            return "宽基指数"
        elif "500" in name or "中证" in name:
            return "中盘指数"
        elif "50" in name or "上证" in name:
            return "大盘蓝筹"
        elif "纳斯达克" in name or "标普" in name or code.startswith("513"):
            return "海外指数"
        else:
            return "其他"
    
    def chat_and_generate(self, user_message: str, chat_history: str, 
                           current_allocation: dict, model: str, db) -> dict:
        """
        对话式生成配置方案（LLM自由处理）
        
        Args:
            user_message: 用户最新消息
            chat_history: 对话历史（可能为空）
            current_allocation: 当前配置方案（可能为None）
            model: 模型名称
            db: 数据库会话
        
        Returns:
            {
                "ai_response": "AI回复文本",
                "allocation": {"510300": 0.5, ...},
                "etf_info": {"510300": {"name": "沪深300ETF", "category": "宽基指数"}}
            }
        """
        logger.info(f"对话生成 - 用户消息: {user_message}")
        
        # 获取可用ETF列表
        etf_list = self._get_available_etfs(db)
        
        # 构建ETF候选列表文本
        etf_options = self._build_etf_options(etf_list)
        
        # 构建完整prompt
        prompt = self._build_prompt(user_message, chat_history, current_allocation, etf_options)
        
        # 调用LLM生成
        if not self.client:
            return self._fallback_generate(user_message, etf_list, current_allocation)
        
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=500,
            )
            
            content = response.choices[0].message.content
            logger.info(f"LLM返回: {content}")
            
            # 解析JSON
            result = self._parse_llm_response(content)
            
            # 验证配置
            if result and self._validate_allocation(result.get("allocation", {})):
                # 获取ETF详细信息
                etf_info = self._get_etf_details(result["allocation"], etf_list)
                
                return {
                    "ai_response": result.get("reply", "配置方案已生成"),
                    "allocation": result["allocation"],
                    "etf_info": etf_info
                }
            else:
                logger.warning("LLM返回格式错误，使用fallback")
                return self._fallback_generate(user_message, etf_list, current_allocation)
        
        except Exception as e:
            logger.error(f"LLM调用失败: {e}", exc_info=True)
            return self._fallback_generate(user_message, etf_list, current_allocation)
    
    def _get_available_etfs(self, db, limit: int = 50) -> list:
        """从数据库获取可用ETF列表"""
        from app.models.etf import ETFBasic
        
        etfs = db.query(ETFBasic).limit(limit).all()
        
        etf_list = []
        for etf in etfs:
            category = self._classify_etf(etf.etf_code, etf.etf_name)
            etf_list.append({
                "code": etf.etf_code,
                "name": etf.etf_name,
                "category": category
            })
        
        logger.info(f"获取了 {len(etf_list)} 只可用ETF")
        return etf_list
    
    def _build_etf_options(self, etf_list: list) -> str:
        """构建ETF候选列表文本"""
        return "\n".join([
            f"{etf['code']}: {etf['name']} ({etf['category']})"
            for etf in etf_list
        ])
    
    def _build_prompt(self, user_message: str, chat_history: str, 
                       current_allocation: dict, etf_options: str) -> str:
        """构建完整prompt"""
        
        # 对话历史部分
        history_part = ""
        if chat_history:
            history_part = f"\n对话历史：\n{chat_history}\n"
        
        # 当前配置部分
        current_part = ""
        if current_allocation:
            current_part = f"\n当前配置方案：\n{json.dumps(current_allocation, ensure_ascii=False)}\n（用户可以要求调整此配置）\n"
        
        # 用户消息
        user_part = f'\n用户最新消息：\n"{user_message}"'
        
        # ETF选项
        etf_part = f"\n\n可用ETF列表（只使用这些ETF代码）：\n{etf_options}"
        
        return f"{history_part}{current_part}{user_part}{etf_part}\n\n请根据以上信息返回JSON格式的配置方案。"
    
    def _parse_llm_response(self, content: str) -> dict:
        """解析LLM返回的JSON"""
        try:
            # 直接解析JSON
            if content.strip().startswith("{"):
                return json.loads(content.strip())
            
            # 提取JSON部分
            import re
            pattern = r'\{[^}]+\}'
            match = re.search(pattern, content)
            if match:
                return json.loads(match.group())
            
            return None
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            return None
    
    def _fallback_generate(self, user_message: str, etf_list: list, 
                           current_allocation: dict) -> dict:
        """Fallback生成（简单规则）"""
        
        # 如果有当前配置，基于关键词调整
        if current_allocation:
            allocation = self._simple_adjust(current_allocation, user_message)
            etf_info = self._get_etf_details(allocation, etf_list)
            return {
                "ai_response": "已根据你的建议调整配置方案。",
                "allocation": allocation,
                "etf_info": etf_info
            }
        
        # 否则生成新配置
        allocation = self._simple_generate(user_message, etf_list)
        etf_info = self._get_etf_details(allocation, etf_list)
        
        # 判断风险等级
        reply = self._generate_simple_reply(allocation, etf_info)
        
        return {
            "ai_response": reply,
            "allocation": allocation,
            "etf_info": etf_info
        }
    
    def _simple_generate(self, user_message: str, etf_list: list) -> dict:
        """简单规则生成配置"""
        
        # 分类ETF
        bond_etfs = [e for e in etf_list if "债券" in e['category']]
        growth_etfs = [e for e in etf_list if "科创" in e['category'] or "创业板" in e['category']]
        broad_etfs = [e for e in etf_list if "宽基" in e['category'] or "大盘" in e['category']]
        
        # 根据关键词判断
        if any(kw in user_message for kw in ["保守", "稳健", "债券", "安全"]):
            if bond_etfs and broad_etfs:
                return {bond_etfs[0]['code']: 0.7, broad_etfs[0]['code']: 0.3}
        
        elif any(kw in user_message for kw in ["激进", "高收益", "成长", "科创", "创业板"]):
            if growth_etfs:
                allocation = {growth_etfs[0]['code']: 0.6}
                if bond_etfs:
                    allocation[bond_etfs[0]['code']] = 0.4
                return allocation
        
        # 默认均衡
        if broad_etfs and bond_etfs:
            return {broad_etfs[0]['code']: 0.5, bond_etfs[0]['code']: 0.5}
        
        # 使用前几只ETF
        if etf_list:
            return {etf_list[0]['code']: 0.6, etf_list[1]['code']: 0.4}
        
        return {"510300": 0.5, "511010": 0.5}
    
    def _simple_adjust(self, current_allocation: dict, user_message: str) -> dict:
        """简单调整配置"""
        new_allocation = current_allocation.copy()
        
        # 检测关键词
        if "增加" in user_message or "加大" in user_message:
            # 简单增加第一个ETF的比例
            first_code = list(current_allocation.keys())[0]
            new_allocation[first_code] = min(current_allocation[first_code] + 0.1, 0.8)
            # 其他ETF减比例
            other_codes = [c for c in current_allocation.keys() if c != first_code]
            for code in other_codes:
                new_allocation[code] = max(current_allocation[code] - 0.1/len(other_codes), 0.05)
        
        elif "减少" in user_message or "降低" in user_message:
            # 简单减少第一个ETF的比例
            first_code = list(current_allocation.keys())[0]
            new_allocation[first_code] = max(current_allocation[first_code] - 0.1, 0.05)
            # 其他ETF加比例
            other_codes = [c for c in current_allocation.keys() if c != first_code]
            for code in other_codes:
                new_allocation[code] = current_allocation[code] + 0.1/len(other_codes)
        
        return new_allocation
    
    def _get_etf_details(self, allocation: dict, etf_list: list) -> dict:
        """获取ETF详细信息"""
        etf_info = {}
        
        for code in allocation.keys():
            etf = next((e for e in etf_list if e['code'] == code), None)
            
            if etf:
                etf_info[code] = {
                    "name": etf['name'],
                    "category": etf['category']
                }
            else:
                etf_info[code] = {
                    "name": code,
                    "category": "未知"
                }
        
        return etf_info
    
    def _generate_simple_reply(self, allocation: dict, etf_info: dict) -> str:
        """生成简单回复文本"""
        etf_count = len(allocation)
        
        has_bond = any("债券" in etf_info[code]['category'] for code in allocation.keys())
        has_growth = any("科创" in etf_info[code]['category'] or "创业板" in etf_info[code]['category'] for code in allocation.keys())
        
        if has_bond and not has_growth:
            risk = "保守型"
        elif has_growth and not has_bond:
            risk = "激进型"
        else:
            risk = "均衡型"
        
        return f"为你生成了一个{risk}配置方案，包含{etf_count}只ETF。"
        """
        Agent Loop主流程：逐步筛选ETF并生成配置
        
        Args:
            description: 用户描述
            model: 使用的模型
        
        Returns:
            配置比例字典 {"etf_code": ratio}
        """
        model_name = model or settings.llm_model
        
        logger.info(f"启动Agent Loop，模型: {model_name}")
        logger.info(f"用户描述: {description}")
        
        try:
            # Step 1: 分析用户投资偏好
            logger.info("Step 1: 分析用户投资偏好...")
            self.user_preference = self._analyze_user_preference(description, model_name)
            
            if not self.user_preference:
                logger.warning("用户偏好分析失败，使用fallback")
                return self._fallback_config(description)
            
            logger.info(f"用户偏好分析结果: {self.user_preference}")
            
            # Step 2: 从数据库筛选ETF候选池
            logger.info("Step 2: 筛选ETF候选池...")
            self.etf_pool = self._filter_etf_candidates(self.user_preference)
            
            if not self.etf_pool:
                logger.warning("未筛选到合适的ETF，使用fallback")
                return self._fallback_config(description)
            
            logger.info(f"筛选到 {len(self.etf_pool)} 只ETF候选")
            
            # Step 3: 根据资产配置理论生成比例
            logger.info("Step 3: 生成配置比例...")
            allocation = self._generate_allocation_ratio(
                self.etf_pool, 
                self.user_preference, 
                model_name
            )
            
            # Step 4: 验证配置合理性
            if allocation and self._validate_final_allocation(allocation):
                logger.info(f"✅ 最终配置: {allocation}")
                return allocation
            else:
                logger.warning("配置验证失败，使用fallback")
                return self._fallback_config(description)
        
        except Exception as e:
            logger.error(f"Agent Loop执行失败: {e}", exc_info=True)
            return self._fallback_config(description)
    


def generate_allocation_config(description: str, model: str = None) -> Optional[Dict[str, float]]:
    """
    入口函数：生成配置（兼容旧接口）
    
    Args:
        description: 配置偏好描述
        model: 使用的模型名称
    
    Returns:
        配置比例字典 {"etf_code": ratio}
    """
    from app.db.database import SessionLocal
    
    db = SessionLocal()
    try:
        agent = ETFAllocationAgent()
        result = agent.chat_and_generate(
            user_message=description,
            chat_history="",
            current_allocation=None,
            model=model or settings.llm_model,
            db=db
        )
        
        return result.get("allocation")
    
    finally:
        db.close()
