"""复盘分析服务"""

import logging
import json
import re
from datetime import date, timedelta
from typing import Dict, List
from openai import OpenAI
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import get_settings
from app.models.strategy import Strategy
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.portfolio import PortfolioSnapshot, TradeRecord
from app.models.sentiment import SentimentData
from app.models.experience import Experience, ExperienceUsageRecord

logger = logging.getLogger(__name__)
settings = get_settings()


class ReviewService:
    """复盘分析服务 - 定期回顾策略执行并生成经验、异常触发、深度分析"""

    EXPERIENCE_GENERATION_PROMPT = """你是专业的投资复盘分析师。

请基于以下历史数据，总结可复用的投资经验：

## 历史执行数据
- 分析周期: {period_days}天
- 总执行次数: {total_count}
- 成功次数: {success_count}
- 失败次数: {failure_count}
- 平均收益率: {avg_return}
- 最大损失: {max_loss}

## 典型失败案例（最近3次）
{failure_cases}

## 典型成功案例（最近3次）
{success_cases}

## 舆情环境特征
{sentiment_patterns}

请生成3-5条结构化经验，每条包含：
1. experience_type: success/failure/insight
2. scenario_tags: 适用场景标签列表
3. title: 简明标题（不超过50字）
4. description: 详细描述（100-200字）
5. market_condition: 市场环境关键指标
6. action_taken: 采取/应采取的行动
7. result: positive/negative/neutral
8. key_insight: 核心洞察（一句话）

输出JSON数组格式（不要包含其他文字）：
[
  {
    "experience_type": "failure",
    "scenario_tags": ["高通胀", "政策收紧"],
    "title": "高通胀环境下债券配置风险",
    "description": "在高通胀环境下...",
    "key_insight": "通胀预期上升时应减少固收配置"
  }
]

注意：
- 经验应具有通用性和可复用性
- 避免过于具体的细节
- 总结规律而非单次事件"""

    DEEP_ANALYSIS_PROMPT = """你是专业的投资策略分析师。

请对以下异常情况进行深度分析：

## 异常概况
- 异常类型: {anomaly_type}
- 异常程度: {severity}
- 影响范围: {impact_summary}

## 相关数据
{related_data}

## 市场环境
{market_environment}

请进行深度分析，输出JSON格式：
{{
  "root_cause_analysis": {{
    "primary_cause": "根本原因描述",
    "contributing_factors": ["因素1", "因素2"],
    "trigger_event": "触发事件"
  }},
  "impact_assessment": {{
    "financial_impact": "财务影响描述",
    "strategy_health": "策略健康度评分(0-100)",
    "long_term_effect": "长期影响判断"
  }},
  "corrective_actions": {{
    "immediate": ["立即行动1", "立即行动2"],
    "short_term": ["短期调整1", "短期调整2"],
    "systemic": ["系统性改进1"]
  }},
  "lessons_learned": {{
    "key_insight": "核心洞察",
    "preventive_measures": ["预防措施1"],
    "similar_risks": ["类似风险场景"]
  }},
  "parameter_adjustments": {{
    "max_daily_adjustments": {{current: 1, suggested: 2, reason: "理由"}},
    "risk_limits": {{suggested_changes: "具体建议"}}
  }}
}}"""

    REVIEW_CONFIG = {
        "anomaly_thresholds": {
            "large_loss": -0.05,
            "consecutive_failure": 3,
            "drawdown_spike": -0.03,
        },
        "review_types": {
            "daily": 1,
            "weekly": 7,
            "monthly": 30,
        },
    }

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def trigger_review(self, strategy_id: int, review_type: str, db: Session) -> Dict:
        """触发复盘分析"""
        # 1. 评估历史经验使用成效，更新生命周期，清理低效经验
        from app.services.experience_manager import ExperienceManager
        exp_manager = ExperienceManager()
        try:
            exp_manager.evaluate_experience_usages(strategy_id, db)
            exp_manager.update_experience_lifecycle(strategy_id, db)
            pruned_count = exp_manager.prune_low_effectiveness(strategy_id, db)
            if pruned_count > 0:
                logger.info(f"策略{strategy_id}在复盘中自动清理了 {pruned_count} 条低效经验")
        except Exception as e:
            logger.error(f"处理经验生命周期失败: {e}", exc_info=True)

        period_days = 7 if review_type == "weekly" else 30
        end_date = date.today()
        start_date = end_date - timedelta(days=period_days)
        
        period_data = self._collect_period_data(strategy_id, start_date, end_date, db)
        experiences = self._generate_experiences_with_llm(period_data)
        
        saved_count = 0
        saved_experiences = []
        for exp_data in experiences:
            if self._validate_experience(exp_data):
                exp = Experience(
                    strategy_id=strategy_id,
                    experience_type=exp_data.get("experience_type", "insight"),
                    scenario_tags=exp_data.get("scenario_tags", []),
                    title=exp_data.get("title", ""),
                    description=exp_data.get("description", ""),
                    market_condition=exp_data.get("market_condition"),
                    action_taken=exp_data.get("action_taken"),
                    result=exp_data.get("result", "neutral"),
                    key_insight=exp_data.get("key_insight"),
                    generated_date=end_date,
                    expires_date=end_date + timedelta(days=90),
                )
                db.add(exp)
                saved_count += 1
                saved_experiences.append({
                    "type": exp.experience_type,
                    "title": exp.title,
                    "key_insight": exp.key_insight,
                })
        
        db.commit()
        self._cleanup_expired_experiences(strategy_id, db)
        
        logger.info(f"策略{strategy_id}复盘完成: 生成{saved_count}条经验")
        
        # 返回详细的复盘报告
        return {
            "experiences_generated": saved_count,
            "review_type": review_type,
            "review_report": self._build_review_report(period_data, saved_experiences, start_date, end_date),
        }
    
    def get_review_report(self, strategy_id: int, review_type: str, db: Session) -> Dict:
        """获取复盘报告（不触发LLM生成）"""
        period_days = 7 if review_type == "weekly" else 30
        end_date = date.today()
        start_date = end_date - timedelta(days=period_days)
        
        period_data = self._collect_period_data(strategy_id, start_date, end_date, db)
        
        # 获取最新经验
        recent_experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.generated_date >= start_date,
        ).order_by(Experience.generated_date.desc()).limit(10).all()
        
        experiences_list = [{
            "type": exp.experience_type,
            "title": exp.title,
            "key_insight": exp.key_insight,
            "effectiveness_score": exp.effectiveness_score,
        } for exp in recent_experiences]
        
        return self._build_review_report(period_data, experiences_list, start_date, end_date)
    
    def detect_anomalies(self, strategy_id: int, db: Session) -> List[Dict]:
        """检测异常情况"""
        anomalies = []
        
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id
        ).order_by(PortfolioSnapshot.trade_date.desc()).limit(30).all()
        
        if len(snapshots) < 2:
            return anomalies
        
        large_loss_threshold = self.REVIEW_CONFIG["anomaly_thresholds"]["large_loss"]
        for i, snapshot in enumerate(snapshots[:5]):
            if snapshot.profit_pct and snapshot.profit_pct < large_loss_threshold:
                anomalies.append({
                    "type": "large_loss",
                    "severity": "high",
                    "date": snapshot.trade_date.isoformat(),
                    "loss_pct": round(snapshot.profit_pct * 100, 2),
                    "message": f"单日大幅亏损{abs(snapshot.profit_pct):.2%}",
                })
        
        consecutive_failures = 0
        logs = db.query(AutoStrategyLog).filter(
            AutoStrategyLog.strategy_id == strategy_id
        ).order_by(AutoStrategyLog.log_date.desc()).limit(10).all()
        
        for log in logs:
            if log.status != "success":
                consecutive_failures += 1
            else:
                break
        
        if consecutive_failures >= self.REVIEW_CONFIG["anomaly_thresholds"]["consecutive_failure"]:
            anomalies.append({
                "type": "consecutive_failure",
                "severity": "medium",
                "count": consecutive_failures,
                "message": f"连续{consecutive_failures}次策略调整失败",
            })
        
        if len(snapshots) >= 5:
            recent_drawdown = self._calculate_recent_drawdown(snapshots[:5])
            if recent_drawdown < self.REVIEW_CONFIG["anomaly_thresholds"]["drawdown_spike"]:
                anomalies.append({
                    "type": "drawdown_spike",
                    "severity": "high",
                    "drawdown_pct": round(recent_drawdown * 100, 2),
                    "message": f"近期回撤突增{abs(recent_drawdown):.2%}",
                })
        
        return anomalies
    
    def trigger_anomaly_review(self, strategy_id: int, anomaly: Dict, db: Session) -> Dict:
        """异常触发复盘"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": "策略不存在"}
        
        related_data = self._collect_anomaly_related_data(strategy_id, anomaly, db)
        market_environment = self._get_market_environment_summary(anomaly.get("date"), db)
        
        deep_analysis = self._perform_deep_analysis(anomaly, related_data, market_environment)
        
        if deep_analysis and "error" not in deep_analysis:
            corrective_experience = self._create_corrective_experience(
                strategy_id, anomaly, deep_analysis, db
            )
            
            return {
                "anomaly_type": anomaly["type"],
                "deep_analysis": deep_analysis,
                "corrective_experience": corrective_experience,
                "review_triggered": True,
            }
        
        return {
            "anomaly_type": anomaly["type"],
            "deep_analysis": deep_analysis,
            "review_triggered": True,
        }
    
    def _calculate_recent_drawdown(self, snapshots: List[PortfolioSnapshot]) -> float:
        """计算近期回撤"""
        if not snapshots:
            return 0
        
        peak = max(s.total_asset for s in snapshots if s.total_asset)
        current = snapshots[0].total_asset
        
        return (peak - current) / peak if peak > 0 else 0
    
    def _collect_anomaly_related_data(self, strategy_id: int, anomaly: Dict, db: Session) -> Dict:
        """收集异常相关数据"""
        anomaly_date = anomaly.get("date")
        
        if anomaly_date:
            try:
                anomaly_date = date.fromisoformat(anomaly_date)
            except:
                anomaly_date = date.today()
        else:
            anomaly_date = date.today()
        
        logs = db.query(AutoStrategyLog).filter(
            AutoStrategyLog.strategy_id == strategy_id,
            AutoStrategyLog.log_date >= anomaly_date - timedelta(days=3),
            AutoStrategyLog.log_date <= anomaly_date + timedelta(days=1),
        ).all()
        
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date >= anomaly_date - timedelta(days=3),
            SentimentData.data_date <= anomaly_date + timedelta(days=1),
        ).all()
        
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id,
            PortfolioSnapshot.trade_date >= anomaly_date - timedelta(days=3),
            PortfolioSnapshot.trade_date <= anomaly_date + timedelta(days=1),
        ).all()
        
        return {
            "logs": [{"date": l.log_date.isoformat(), "status": l.status, "action": l.action_type} for l in logs],
            "sentiments": [{"date": s.data_date.isoformat(), "score": s.sentiment_score} for s in sentiments],
            "snapshots": [{"date": s.trade_date.isoformat(), "profit_pct": s.profit_pct} for s in snapshots],
        }
    
    def _get_market_environment_summary(self, anomaly_date: str, db: Session) -> Dict:
        """获取市场环境摘要"""
        target_date = date.today()
        if anomaly_date:
            try:
                target_date = date.fromisoformat(anomaly_date)
            except:
                pass
        
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date == target_date
        ).limit(20).all()
        
        avg_sentiment = sum(s.sentiment_score or 0 for s in sentiments) / len(sentiments) if sentiments else 0
        
        return {
            "avg_sentiment_score": round(avg_sentiment, 3),
            "news_count": len(sentiments),
            "market_sentiment": "positive" if avg_sentiment > 0.1 else "negative" if avg_sentiment < -0.1 else "neutral",
        }
    
    def _perform_deep_analysis(self, anomaly: Dict, related_data: Dict, market_env: Dict) -> Dict:
        """执行深度分析"""
        if not self.llm_client:
            return {"error": "LLM客户端未配置"}
        
        prompt = self.DEEP_ANALYSIS_PROMPT.format(
            anomaly_type=anomaly["type"],
            severity=anomaly["severity"],
            impact_summary=anomaly["message"],
            related_data=json.dumps(related_data, ensure_ascii=False),
            market_environment=json.dumps(market_env, ensure_ascii=False),
        )
        
        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            
            content = response.choices[0].message.content
            return self._parse_json_response(content)
        
        except Exception as e:
            logger.error(f"深度分析失败: {e}")
            return {"error": str(e)}
    
    def _create_corrective_experience(
        self,
        strategy_id: int,
        anomaly: Dict,
        deep_analysis: Dict,
        db: Session
    ) -> Dict:
        """创建纠正性经验"""
        lessons = deep_analysis.get("lessons_learned", {})
        corrective = deep_analysis.get("corrective_actions", {})
        
        exp = Experience(
            strategy_id=strategy_id,
            experience_type="failure",
            scenario_tags=[anomaly["type"]],
            title=f"{anomaly['message']}应对经验",
            description=lessons.get("key_insight", ""),
            market_condition=anomaly.get("date"),
            action_taken=corrective.get("immediate", []),
            result="negative",
            key_insight=lessons.get("key_insight", ""),
            generated_date=date.today(),
            expires_date=date.today() + timedelta(days=90),
        )

        db.add(exp)
        db.commit()

        # 同步写入失败模式库（幂等累计）
        try:
            from app.services.failure_mode_service import get_failure_mode_service
            get_failure_mode_service().record_failure(
                strategy_id=strategy_id,
                signature=f"异常{anomaly['type']}:{anomaly.get('message', '')[:40]}",
                title=exp.title,
                description=exp.description,
                scenario_tags=[anomaly["type"]],
                key_insight=exp.key_insight,
                db=db,
            )
        except Exception as e:
            logger.error(f"记录失败模式异常: {e}")

        return {
            "experience_id": exp.id,
            "title": exp.title,
            "key_insight": exp.key_insight,
        }
    
    def suggest_parameter_adjustments(self, strategy_id: int, db: Session) -> Dict:
        """建议参数调整"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": "策略不存在"}
        
        anomalies = self.detect_anomalies(strategy_id, db)
        
        suggestions = []
        
        if any(a["type"] == "consecutive_failure" for a in anomalies):
            suggestions.append({
                "parameter": "max_daily_adjustments",
                "current": strategy.max_daily_adjustments or 1,
                "suggested": 2,
                "reason": "连续失败次数过多，建议增加调整频率以快速响应市场",
            })
        
        if any(a["type"] == "large_loss" for a in anomalies):
            suggestions.append({
                "parameter": "max_allocation_change",
                "current": 0.10,
                "suggested": 0.05,
                "reason": "单日大幅亏损，建议降低单次调整幅度限制",
            })
        
        if any(a["type"] == "drawdown_spike" for a in anomalies):
            suggestions.append({
                "parameter": "auto_strategy_status",
                "current": strategy.auto_strategy_status,
                "suggested": "paused",
                "reason": "回撤突增，建议暂停策略等待市场稳定",
            })
        
        return {
            "strategy_id": strategy_id,
            "suggestions": suggestions,
            "confidence": len(anomalies) > 0,
        }
    
    def compare_periods(
        self,
        strategy_id: int,
        period1_start: date,
        period1_end: date,
        period2_start: date,
        period2_end: date,
        db: Session
    ) -> Dict:
        """跨周期对比分析"""
        period1_data = self._collect_period_data(strategy_id, period1_start, period1_end, db)
        period2_data = self._collect_period_data(strategy_id, period2_start, period2_end, db)
        
        comparison = {
            "period1": {
                "start": period1_start.isoformat(),
                "end": period1_end.isoformat(),
                "success_rate": period1_data["success_count"] / period1_data["total_count"] if period1_data["total_count"] > 0 else 0,
                "avg_return": period1_data["avg_return"],
            },
            "period2": {
                "start": period2_start.isoformat(),
                "end": period2_end.isoformat(),
                "success_rate": period2_data["success_count"] / period2_data["total_count"] if period2_data["total_count"] > 0 else 0,
                "avg_return": period2_data["avg_return"],
            },
            "performance_change": {
                "success_rate_diff": round(
                    (period2_data["success_count"] / period2_data["total_count"] - 
                     period1_data["success_count"] / period1_data["total_count"]) * 100, 2
                ) if period1_data["total_count"] > 0 and period2_data["total_count"] > 0 else 0,
                "avg_return_diff": round(
                    period2_data["avg_return"] - period1_data["avg_return"], 2
                ),
            },
        }
        
        if comparison["performance_change"]["success_rate_diff"] > 10:
            comparison["conclusion"] = "第二周期表现明显改善"
        elif comparison["performance_change"]["success_rate_diff"] < -10:
            comparison["conclusion"] = "第二周期表现明显恶化"
        else:
            comparison["conclusion"] = "表现基本持平"
        
        return comparison
    
    def _build_review_report(self, period_data: Dict, experiences: List[Dict], 
                             start_date: date, end_date: date) -> Dict:
        """构建复盘报告"""
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": period_data["period_days"],
            },
            "statistics": {
                "total_executions": period_data["total_count"],
                "success_count": period_data["success_count"],
                "failure_count": period_data["failure_count"],
                "success_rate": round(period_data["success_count"] / period_data["total_count"] * 100, 1) if period_data["total_count"] > 0 else 0,
                "avg_return": period_data["avg_return"],
                "max_loss": period_data["max_loss"],
            },
            "sentiment_analysis": period_data["sentiment_patterns"],
            "cases": {
                "failures": period_data["failure_cases"],
                "successes": period_data["success_cases"],
            },
            "generated_experiences": experiences,
            "summary": self._generate_summary(period_data),
        }
    
    def _generate_summary(self, period_data: Dict) -> str:
        """生成复盘总结"""
        total = period_data["total_count"]
        success = period_data["success_count"]
        avg_return = period_data["avg_return"]
        
        if total == 0:
            return "本周无自动策略执行记录"
        
        success_rate = success / total * 100
        
        if success_rate >= 80:
            return f"本周策略执行表现优秀，成功率{success_rate:.0f}%，平均收益{avg_return:.2f}%"
        elif success_rate >= 50:
            return f"本周策略执行表现中等，成功率{success_rate:.0f}%，需关注失败案例分析"
        else:
            return f"本周策略执行表现欠佳，成功率{success_rate:.0f}%，建议暂停并调整策略参数"
    
    def _collect_period_data(self, strategy_id: int, start_date: date, end_date: date, db: Session) -> Dict:
        """收集周期数据"""
        logs = db.query(AutoStrategyLog).filter(
            AutoStrategyLog.strategy_id == strategy_id,
            AutoStrategyLog.log_date >= start_date,
            AutoStrategyLog.log_date <= end_date,
        ).all()
        
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id,
            PortfolioSnapshot.trade_date >= start_date,
            PortfolioSnapshot.trade_date <= end_date,
        ).all()
        
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date >= start_date,
            SentimentData.data_date <= end_date,
        ).all()
        
        total_count = len(logs)
        success_count = sum(1 for l in logs if l.status == "success")
        failure_count = total_count - success_count
        
        avg_return = 0
        max_loss = 0
        if snapshots:
            returns = [s.profit_pct for s in snapshots if s.profit_pct]
            if returns:
                avg_return = sum(returns) / len(returns)
                max_loss = min(returns)
        
        failure_cases = [
            {"date": l.log_date, "reason": l.safety_reason or l.analysis_result}
            for l in logs if l.status != "success" or l.action_type == "skipped"
        ][:3]
        
        success_cases = [
            {"date": l.log_date, "analysis": l.analysis_result}
            for l in logs if l.action_type == "adjusted"
        ][:3]
        
        sentiment_patterns = {
            "avg_score": sum(s.sentiment_score or 0 for s in sentiments) / len(sentiments) if sentiments else 0,
            "positive_ratio": sum(1 for s in sentiments if s.sentiment_label == "positive") / len(sentiments) if sentiments else 0,
        }
        
        return {
            "period_days": (end_date - start_date).days,
            "total_count": total_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "avg_return": round(avg_return, 2),
            "max_loss": round(max_loss, 2),
            "failure_cases": failure_cases,
            "success_cases": success_cases,
            "sentiment_patterns": sentiment_patterns,
        }
    
    def _generate_experiences_with_llm(self, period_data: Dict) -> List[Dict]:
        """使用LLM生成经验"""
        if not self.llm_client:
            return []
        
        prompt = self.EXPERIENCE_GENERATION_PROMPT.format(
            period_days=period_data["period_days"],
            total_count=period_data["total_count"],
            success_count=period_data["success_count"],
            failure_count=period_data["failure_count"],
            avg_return=period_data["avg_return"],
            max_loss=period_data["max_loss"],
            failure_cases=json.dumps(period_data["failure_cases"], ensure_ascii=False),
            success_cases=json.dumps(period_data["success_cases"], ensure_ascii=False),
            sentiment_patterns=json.dumps(period_data["sentiment_patterns"], ensure_ascii=False),
        )
        
        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=1500,
            )
            
            content = response.choices[0].message.content
            return self._parse_json_array(content)
            
        except Exception as e:
            logger.error(f"LLM经验生成失败: {e}")
            return []
    
    def _validate_experience(self, exp_data: Dict) -> bool:
        """验证经验数据"""
        if not exp_data.get("title"):
            return False
        if not exp_data.get("description"):
            return False
        if exp_data.get("experience_type") not in ["success", "failure", "insight"]:
            return False
        return True
    
    def _cleanup_expired_experiences(self, strategy_id: int, db: Session):
        """清理过期经验"""
        expired = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.expires_date < date.today(),
            Experience.is_active == True,
        ).all()
        
        for exp in expired:
            exp.is_active = False
        
        db.commit()
        logger.info(f"清理{len(expired)}条过期经验")
    
    def _parse_json_array(self, content: str) -> List[Dict]:
        """解析JSON数组"""
        try:
            match = re.search(r'\[[\s\S]*\]', content)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"JSON解析失败: {e}")
        return []