"""
ETF配置组合AI生成器 - Agent Loop架构
通过多步推理从数据库ETF列表中逐步筛选并生成配置比例
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
    """ETF配置组合生成Agent"""
    
    def __init__(self):
        self.client = None
        self.etf_pool = []  # ETF候选池
        self.user_preference = {}  # 用户偏好分析结果
        
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def chat_and_generate(self, user_message: str, chat_history: str, 
                           current_allocation: dict, model: str, db) -> dict:
        """
        对话式生成配置方案（支持迭代优化）
        
        Args:
            user_message: 用户最新消息
            chat_history: 对话历史
            current_allocation: 当前配置方案（可能为None）
            model: 模型名称
            db: 数据库会话
        
        Returns:
            {
                "ai_response": "AI回复文本",
                "allocation": {"510300": 0.5, ...},
                "etf_info": {"510300": {"name": "沪深300ETF", "category": "宽基指数"}, ...}
            }
        """
        logger.info(f"对话式生成 - 用户消息: {user_message}")
        logger.info(f"对话历史长度: {len(chat_history)}")
        logger.info(f"当前配置: {current_allocation}")
        
        # 判断是否是修改建议
        is_modification = current_allocation and self._is_modification_request(user_message)
        
        if is_modification:
            logger.info("检测到修改建议，基于当前配置调整")
            allocation = self._adjust_allocation(current_allocation, user_message, model)
        else:
            logger.info("生成新的配置方案")
            allocation = self.generate(user_message, model)
        
        # 获取ETF详细信息
        etf_info = self._get_etf_details(allocation, db)
        
        # 生成AI回复文本
        ai_response = self._generate_ai_response(
            allocation, 
            etf_info, 
            user_message, 
            chat_history,
            is_modification,
            model
        )
        
        return {
            "ai_response": ai_response,
            "allocation": allocation,
            "etf_info": etf_info
        }
    
    def _is_modification_request(self, message: str) -> bool:
        """判断是否是修改建议"""
        modification_keywords = [
            "增加", "减少", "调整", "修改", "改", "换成", "替换",
            "太大", "太小", "太多", "太少", "偏高", "偏低",
            "降低", "提高", "加大", "缩小", "扩大", "压缩"
        ]
        
        return any(keyword in message for keyword in modification_keywords)
    
    def _adjust_allocation(self, current_allocation: dict, 
                           user_message: str, model: str) -> dict:
        """
        基于用户修改建议调整配置
        
        Args:
            current_allocation: 当前配置
            user_message: 修改建议
            model: 模型名称
        
        Returns:
            调整后的配置
        """
        if not self.client:
            return self._rule_based_adjust(current_allocation, user_message)
        
        prompt = f"""根据用户的修改建议调整ETF配置比例。

当前配置：
{json.dumps(current_allocation, ensure_ascii=False)}

用户修改建议：
"{user_message}"

请返回调整后的配置（JSON格式，总和=1.0）：
{"ETF代码": 新比例, ...}

调整原则：
1. 比例总和必须等于1.0
2. 根据用户建议调整对应ETF的比例
3. 如果用户要求"增加X"，适当提高X的比例
4. 如果用户要求"减少Y"，适当降低Y的比例
5. 调整幅度建议在10%-20%之间（除非用户明确指定）
"""

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300,
            )
            
            content = response.choices[0].message.content
            new_allocation = json.loads(content.strip())
            
            # 验证并返回
            if self._validate_final_allocation(new_allocation):
                logger.info(f"调整后的配置: {new_allocation}")
                return new_allocation
            else:
                return current_allocation
        
        except Exception as e:
            logger.error(f"调整配置失败: {e}")
            return self._rule_based_adjust(current_allocation, user_message)
    
    def _rule_based_adjust(self, current_allocation: dict, user_message: str) -> dict:
        """基于规则的配置调整（fallback）"""
        new_allocation = current_allocation.copy()
        
        # 检测关键词并调整
        if "增加" in user_message or "加大" in user_message or "提高" in user_message:
            # 找到用户提到的ETF并增加比例
            for code in current_allocation.keys():
                if code in user_message or any(kw in user_message for kw in ["债券", "科创", "创业板", "宽基"]):
                    new_allocation[code] = min(current_allocation[code] + 0.1, 0.8)
                    # 需要从其他ETF减比例
                    other_codes = [c for c in current_allocation.keys() if c != code]
                    if other_codes:
                        decrease_per_etf = 0.1 / len(other_codes)
                        for other_code in other_codes:
                            new_allocation[other_code] = max(current_allocation[other_code] - decrease_per_etf, 0.05)
                    break
        
        elif "减少" in user_message or "降低" in user_message or "缩小" in user_message:
            # 找到用户提到的ETF并减少比例
            for code in current_allocation.keys():
                if code in user_message or any(kw in user_message for kw in ["债券", "科创", "创业板", "宽基"]):
                    new_allocation[code] = max(current_allocation[code] - 0.1, 0.05)
                    # 需要给其他ETF加比例
                    other_codes = [c for c in current_allocation.keys() if c != code]
                    if other_codes:
                        increase_per_etf = 0.1 / len(other_codes)
                        for other_code in other_codes:
                            new_allocation[other_code] = min(current_allocation[other_code] + increase_per_etf, 0.8)
                    break
        
        logger.info(f"规则调整后的配置: {new_allocation}")
        return new_allocation
    
    def _get_etf_details(self, allocation: dict, db) -> dict:
        """获取ETF详细信息"""
        from app.models.etf import ETFBasic
        
        etf_info = {}
        
        for code in allocation.keys():
            etf = db.query(ETFBasic).filter(ETFBasic.etf_code == code).first()
            
            if etf:
                etf_info[code] = {
                    "name": etf.etf_name,
                    "category": self._classify_etf(etf.etf_code, etf.etf_name)
                }
            else:
                etf_info[code] = {
                    "name": code,
                    "category": "未知"
                }
        
        return etf_info
    
    def _generate_ai_response(self, allocation: dict, etf_info: dict,
                              user_message: str, chat_history: str,
                              is_modification: bool, model: str) -> str:
        """生成AI回复文本"""
        if not self.client:
            return self._generate_fallback_response(allocation, etf_info, is_modification)
        
        # 构建配置展示文本
        config_text = "\n".join([
            f"- {etf_info[code]['name']}（{etf_info[code]['category']}）: {(ratio * 100):.1f}%"
            for code, ratio in allocation.items()
        ])
        
        prompt = f"""你是一个友好的AI策略助手，请用自然语言回复用户。

对话历史：
{chat_history if chat_history else "（首次对话）"}

用户最新消息：
"{user_message}"

生成的配置方案：
{config_text}

请回复用户（不要包含配置数据，用自然语言描述）：
1. 如果是首次生成：简要说明配置方案的特点（风险等级、资产分布）
2. 如果是修改调整：说明已根据建议调整，并简要说明调整内容
3. 语气友好、专业，控制在100字以内
"""

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=150,
            )
            
            return response.choices[0].message.content
        
        except Exception as e:
            logger.error(f"生成AI回复失败: {e}")
            return self._generate_fallback_response(allocation, etf_info, is_modification)
    
    def _generate_fallback_response(self, allocation: dict, etf_info: dict, 
                                     is_modification: bool) -> str:
        """生成fallback回复文本"""
        if is_modification:
            return "已根据你的建议调整配置方案。你可以继续提出修改意见，或者点击确认保存。"
        else:
            # 判断风险等级
            has_bond = any("债券" in etf_info[code]['category'] for code in allocation.keys())
            has_growth = any("科创" in etf_info[code]['category'] or "创业板" in etf_info[code]['category'] for code in allocation.keys())
            
            if has_bond and not has_growth:
                risk = "保守型"
            elif has_growth and not has_bond:
                risk = "激进型"
            else:
                risk = "均衡型"
            
            return f"为你生成了一个{risk}配置方案，包含{len(allocation)}只ETF。你可以继续对话调整，或点击确认保存。"
    
    def generate(self, description: str, model: str = None) -> Optional[Dict[str, float]]:
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
    
    def _analyze_user_preference(self, description: str, model: str) -> Optional[Dict]:
        """
        Step 1: 分析用户投资偏好
        
        Returns:
            {
                "risk_level": "conservative/balanced/aggressive",
                "asset_types": ["债券", "股票", "黄金", "科创"],
                "investment_goal": "稳健增长/追求收益/分散风险",
                "time_horizon": "长期/中期/短期"
            }
        """
        if not self.client:
            return self._extract_preference_from_keywords(description)
        
        prompt = f"""分析用户的投资偏好描述，提取以下信息：

用户描述："{description}"

请返回JSON格式（不要包含其他文字）：
{
    "risk_level": "conservative/balanced/aggressive",
    "asset_types": ["债券", "股票", "黄金", "科创板"],
    "investment_goal": "简要描述投资目标",
    "time_horizon": "长期/中期/短期"
}

风险等级判断标准：
- conservative（保守）：提到"保守"、"稳健"、"债券为主"、"安全"、"低风险"
- balanced（均衡）：提到"均衡"、"平衡"、"股债平衡"、"适中风险"
- aggressive（激进）：提到"激进"、"高收益"、"成长"、"高风险"、"追求收益"
"""

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )
            
            content = response.choices[0].message.content
            preference = json.loads(content.strip())
            
            logger.info(f"LLM分析偏好: {preference}")
            return preference
        
        except Exception as e:
            logger.error(f"偏好分析失败: {e}")
            return self._extract_preference_from_keywords(description)
    
    def _extract_preference_from_keywords(self, description: str) -> Dict:
        """从关键词提取用户偏好（fallback）"""
        risk_level = "balanced"
        asset_types = []
        
        # 风险等级判断
        if any(kw in description for kw in ["保守", "稳健", "债券", "安全", "低风险"]):
            risk_level = "conservative"
            asset_types = ["债券", "大盘蓝筹"]
        elif any(kw in description for kw in ["激进", "高收益", "成长", "高风险", "创业板", "科创"]):
            risk_level = "aggressive"
            asset_types = ["科创板", "创业板", "成长股"]
        else:
            risk_level = "balanced"
            asset_types = ["宽基指数", "债券"]
        
        # 特定资产类型
        if "黄金" in description or "避险" in description:
            asset_types.append("黄金")
        if "医药" in description or "医疗" in description:
            asset_types.append("医药")
        if "科技" in description or "芯片" in description:
            asset_types.append("科技")
        if "新能源" in description:
            asset_types.append("新能源")
        
        return {
            "risk_level": risk_level,
            "asset_types": asset_types,
            "investment_goal": description,
            "time_horizon": "长期"
        }
    
    def _filter_etf_candidates(self, preference: Dict) -> List[Dict]:
        """
        Step 2: 从数据库筛选ETF候选池
        
        Returns:
            [{"code": "510300", "name": "沪深300ETF", "category": "宽基指数", "score": 0.8}]
        """
        db = SessionLocal()
        try:
            from app.models.etf import ETFBasic
            
            # 获取所有ETF
            all_etfs = db.query(ETFBasic).all()
            
            candidates = []
            
            for etf in all_etfs:
                category = self._classify_etf(etf.etf_code, etf.etf_name)
                score = self._calculate_match_score(category, preference)
                
                # 只保留匹配度较高的ETF
                if score > 0.5:
                    candidates.append({
                        "code": etf.etf_code,
                        "name": etf.etf_name,
                        "category": category,
                        "score": score
                    })
            
            # 按匹配度排序，保留前20只
            candidates.sort(key=lambda x: x['score'], reverse=True)
            candidates = candidates[:20]
            
            logger.info(f"筛选结果: 前3只 {[(c['code'], c['category'], c['score']) for c in candidates[:3]]}")
            
            return candidates
        
        finally:
            db.close()
    
    def _classify_etf(self, code: str, name: str) -> str:
        """ETF分类"""
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
    
    def _calculate_match_score(self, category: str, preference: Dict) -> float:
        """计算ETF与用户偏好的匹配度"""
        risk_level = preference.get("risk_level", "balanced")
        asset_types = preference.get("asset_types", [])
        
        score = 0.0
        
        # 资产类型匹配
        if category in asset_types:
            score += 0.8
        
        # 风险等级匹配
        if risk_level == "conservative":
            if category in ["债券", "黄金", "大盘蓝筹"]:
                score += 0.7
            elif category in ["宽基指数"]:
                score += 0.5
            elif category in ["科创板", "创业板"]:
                score -= 0.3
        
        elif risk_level == "balanced":
            if category in ["宽基指数", "债券", "大盘蓝筹"]:
                score += 0.6
            elif category in ["中盘指数", "黄金"]:
                score += 0.5
            elif category in ["科创板", "创业板"]:
                score += 0.3
        
        elif risk_level == "aggressive":
            if category in ["科创板", "创业板", "科技", "新能源"]:
                score += 0.8
            elif category in ["中盘指数", "海外指数"]:
                score += 0.6
            elif category in ["债券"]:
                score += 0.2
        
        return max(score, 0.0)
    
    def _generate_allocation_ratio(self, etf_pool: List[Dict], preference: Dict, model: str) -> Optional[Dict[str, float]]:
        """
        Step 3: 根据筛选出的ETF生成配置比例
        
        Args:
            etf_pool: ETF候选池
            preference: 用户偏好
            model: 模型名称
        
        Returns:
            {"510300": 0.5, "511010": 0.4}
        """
        if not self.client:
            return self._rule_based_allocation(etf_pool, preference)
        
        # 构建ETF候选列表文本
        etf_text = "\n".join([
            f"{etf['code']}: {etf['name']} ({etf['category']}, 匹配度{etf['score']:.1f})"
            for etf in etf_pool
        ])
        
        risk_level = preference.get("risk_level", "balanced")
        investment_goal = preference.get("investment_goal", "")
        
        prompt = f"""根据以下ETF候选池和用户风险偏好，生成合理的资产配置比例。

## ETF候选池（已筛选，匹配用户需求）
{etf_text}

## 用户风险偏好
- 风险等级: {risk_level}
- 投资目标: {investment_goal}

## 配置原则
1. 比例总和必须等于1.0
2. 根据风险等级调整：
   - conservative: 债券类ETF 60-80%，宽基指数20-30%，黄金5-10%
   - balanced: 股债平衡，宽基指数50-60%，债券30-40%
   - aggressive: 成长类ETF 60-80%，债券20-30%
3. 建议配置3-5只ETF，避免过度集中
4. 只使用候选池中的ETF代码

请返回JSON格式（不要包含其他文字）：
{"ETF代码": 比例, "ETF代码": 比例}
"""

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300,
            )
            
            content = response.choices[0].message.content
            allocation = json.loads(content.strip())
            
            logger.info(f"LLM生成配置: {allocation}")
            return allocation
        
        except Exception as e:
            logger.error(f"LLM生成失败: {e}")
            return self._rule_based_allocation(etf_pool, preference)
    
    def _rule_based_allocation(self, etf_pool: List[Dict], preference: Dict) -> Dict[str, float]:
        """基于规则的配置生成（fallback）"""
        risk_level = preference.get("risk_level", "balanced")
        
        # 按分类分组
        bond_etfs = [e for e in etf_pool if e['category'] == "债券"]
        growth_etfs = [e for e in etf_pool if e['category'] in ["科创板", "创业板", "科技"]]
        broad_etfs = [e for e in etf_pool if e['category'] in ["宽基指数", "大盘蓝筹"]]
        gold_etfs = [e for e in etf_pool if e['category'] == "黄金"]
        
        allocation = {}
        
        if risk_level == "conservative":
            # 债券70%，宽基20%，黄金10%
            if bond_etfs:
                allocation[bond_etfs[0]['code']] = 0.7
            if broad_etfs:
                allocation[broad_etfs[0]['code']] = 0.2
            if gold_etfs:
                allocation[gold_etfs[0]['code']] = 0.1
        
        elif risk_level == "aggressive":
            # 成长股60%，宽基30%，债券10%
            if growth_etfs:
                allocation[growth_etfs[0]['code']] = 0.4
                if growth_etfs[1:]:
                    allocation[growth_etfs[1]['code']] = 0.2
            if broad_etfs:
                allocation[broad_etfs[0]['code']] = 0.3
            if bond_etfs:
                allocation[bond_etfs[0]['code']] = 0.1
        
        else:  # balanced
            # 宽基50%，债券40%，黄金10%
            if broad_etfs:
                allocation[broad_etfs[0]['code']] = 0.5
            if bond_etfs:
                allocation[bond_etfs[0]['code']] = 0.4
            if gold_etfs:
                allocation[gold_etfs[0]['code']] = 0.1
        
        # 如果allocation为空，使用前几只ETF
        if not allocation and etf_pool:
            allocation = {etf_pool[0]['code']: 0.6, etf_pool[1]['code']: 0.4}
        
        logger.info(f"规则配置: {allocation}")
        return allocation
    
    def _validate_final_allocation(self, allocation: Dict[str, float]) -> bool:
        """Step 4: 验证最终配置"""
        # 检查总和
        total = sum(allocation.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(f"配置总和 {total:.2f} 不等于1.0")
            return False
        
        # 检查比例范围
        for code, ratio in allocation.items():
            if ratio < 0.05 or ratio > 0.8:
                logger.warning(f"比例不合理 {code}: {ratio:.2f}（建议5%-80%）")
                # 不返回False，只是警告
        
        # 检查ETF数量
        if len(allocation) < 2:
            logger.warning("配置ETF数量过少（建议至少2只）")
            return False
        
        return True
    
    def _fallback_config(self, description: str) -> Dict[str, float]:
        """最终fallback：从数据库获取ETF并生成配置"""
        db = SessionLocal()
        try:
            from app.models.etf import ETFBasic
            
            etfs = db.query(ETFBasic).limit(5).all()
            
            if not etfs:
                return {"510300": 0.5, "511010": 0.4, "588060": 0.1}
            
            # 简单规则配置
            allocation = {
                etfs[0].etf_code: 0.5,
                etfs[1].etf_code: 0.3 if len(etfs) > 1 else 0.5,
            }
            if len(etfs) > 2:
                allocation[etfs[2].etf_code] = 0.2
            
            logger.info(f"Fallback配置: {allocation}")
            return allocation
        
        finally:
            db.close()


def generate_allocation_config(description: str, model: str = None) -> Optional[Dict[str, float]]:
    """
    入口函数：使用Agent Loop生成配置
    
    Args:
        description: 配置偏好描述
        model: 使用的模型名称
    
    Returns:
        配置比例字典 {"etf_code": ratio}
    """
    agent = ETFAllocationAgent()
    return agent.generate(description, model)
