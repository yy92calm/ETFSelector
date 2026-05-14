"""
ETF配置组合生成器 - 对话式架构（完全自由版）
通过多轮对话让LLM完全自由处理配置生成，无硬编码限制
"""

import logging
import json
import re
from typing import Optional, Dict, List
from openai import OpenAI
from app.config import get_settings
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)
settings = get_settings()


class ETFAllocationAgent:
    """ETF配置组合生成Agent - 对话式架构（完全自由）"""
    
    SYSTEM_PROMPT = """你是一个专业的ETF配置助手，通过自然对话帮助用户创建资产配置方案。

你的任务：
1. 通过多轮对话理解用户投资需求（风险偏好、资产类型、投资目标等）
2. **严格从可用ETF列表中选择ETF代码**，禁止选择列表之外的ETF
3. 根据用户需求自由分配配置比例
4. 用自然语言友好回复用户

输出格式：
返回JSON格式（不要包含其他文字）：
{
  "reply": "回复文本（简要说明配置特点）",
  "allocation": {"ETF代码": 比例, "ETF代码": 比例},
  "confidence": "high/medium/low"
}

⚠️ 严格约束：
- reply字段：自然语言回复
- allocation字段：比例总和必须等于1.0
- confidence字段：信心程度（high/medium/low）
- **allocation中的ETF代码必须是可用ETF列表中的代码，使用其他代码将被系统拒绝**
- 完全自由发挥，但必须在可用ETF范围内选择"""

    def __init__(self):
        self.client = None
        
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def chat_and_generate(self, user_message: str, chat_history: str, 
                           current_allocation: dict, model: str, db) -> dict:
        """
        对话式生成配置方案（完全自由）
        
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
                "etf_info": {"510300": {"name": "沪深300ETF"}}
            }
        """
        logger.info(f"用户消息: {user_message}")
        
        if not self.client:
            logger.error("LLM客户端未初始化")
            return {
                "ai_response": "抱歉，系统配置错误，无法生成配置方案。",
                "allocation": {},
                "etf_info": {}
            }
        
        try:
            # 获取所有可用ETF
            etf_list = self._get_all_etfs(db)
            
            # 构建完整prompt
            prompt = self._build_prompt(user_message, chat_history, current_allocation, etf_list)
            
            # 调用LLM
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
            
            if result and "allocation" in result and "reply" in result:
                # 获取ETF详细信息
                etf_info = self._get_etf_details(result["allocation"], etf_list)
                
                return {
                    "ai_response": result.get("reply", "配置方案已生成"),
                    "allocation": result["allocation"],
                    "etf_info": etf_info
                }
            else:
                logger.warning(f"LLM返回格式错误: {result}")
                return {
                    "ai_response": "抱歉，配置生成失败，请重新描述您的需求。",
                    "allocation": {},
                    "etf_info": {}
                }
        
        except Exception as e:
            logger.error(f"LLM调用失败: {e}", exc_info=True)
            return {
                "ai_response": f"抱歉，系统错误: {str(e)}",
                "allocation": {},
                "etf_info": {}
            }
    
    def _get_all_etfs(self, db) -> list:
        """从数据库获取所有可用ETF（不限制数量）"""
        from app.models.etf import ETFBasic
        
        etfs = db.query(ETFBasic).all()
        
        etf_list = []
        for etf in etfs:
            etf_list.append({
                "code": etf.etf_code,
                "name": etf.etf_name
            })
        
        logger.info(f"获取了 {len(etf_list)} 只可用ETF")
        return etf_list
    
    def _build_prompt(self, user_message: str, chat_history: str, 
                       current_allocation: dict, etf_list: list) -> str:
        """构建完整prompt"""
        
        # 对话历史
        history_part = ""
        if chat_history and chat_history.strip():
            history_part = f"\n对话历史：\n{chat_history}\n"
        
        # 当前配置
        current_part = ""
        if current_allocation:
            current_part = f"\n当前配置：\n{json.dumps(current_allocation, ensure_ascii=False)}\n"
        
        # 用户消息
        user_part = f'\n用户消息：\n"{user_message}"'
        
        # ETF列表（格式化为简洁列表，强调范围限制）
        etf_part = f"\n⚠️ 可用ETF列表（共{len(etf_list)}只，仅限从中选择）：\n"
        etf_part += "\n".join([f"{etf['code']}: {etf['name']}" for etf in etf_list])
        etf_part += "\n\n重要：allocation中的ETF代码必须严格使用上述列表中的代码，使用其他代码将无效。"
        
        return f"{history_part}{current_part}{user_part}{etf_part}\n\n请返回JSON格式的配置方案。"
    
    def _parse_llm_response(self, content: str) -> dict:
        """解析LLM返回的JSON（只验证格式，不验证内容）"""
        try:
            # 提取JSON内容
            content = content.strip()
            
            # 尝试多种提取方式
            # 1. 直接解析
            if content.startswith("{") and content.endswith("}"):
                return json.loads(content)
            
            # 2. 提取第一个JSON对象
            match = re.search(r'\{[^{}]*\}', content)
            if match:
                return json.loads(match.group())
            
            # 3. 提取完整的JSON（包括嵌套）
            match = re.search(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', content)
            if match:
                return json.loads(match.group())
            
            logger.error(f"未找到有效JSON: {content}")
            return None
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}, 内容: {content}")
            return None
    
    def _get_etf_details(self, allocation: dict, etf_list: list) -> dict:
        """获取ETF详细信息"""
        etf_info = {}
        
        for code in allocation.keys():
            etf = next((e for e in etf_list if e['code'] == code), None)
            
            if etf:
                etf_info[code] = {
                    "name": etf['name']
                }
            else:
                etf_info[code] = {
                    "name": code
                }
        
        return etf_info


def generate_allocation_config(description: str, model: str = None) -> Optional[Dict[str, float]]:
    """
    入口函数：生成配置（兼容旧接口）
    
    Args:
        description: 配置偏好描述
        model: 使用的模型名称
    
    Returns:
        配置比例字典 {"etf_code": ratio}
    """
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
        
        return result.get("allocation") if result.get("allocation") else None
    
    finally:
        db.close()