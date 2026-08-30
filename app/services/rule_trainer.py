"""
AI历史规则训练模块
从 auto_strategy_log 的 analysis_result 中提取 regime→allocation 映射规则
"""

import logging
import json
from datetime import date, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.auto_strategy_log import AutoStrategyLog

logger = logging.getLogger(__name__)


class RuleTrainer:
    """
    从AI分析历史中训练规则表
    
    输出结构：
    {
        "regime_rules": {
            "bull_quiet": {
                "avg_allocation": {"510300": 0.3, "518850": 0.15, ...},
                "sample_count": 3,
                "typical_action": "rebalance",
            },
            ...
        },
        "regime_transitions": [
            {"from": "bull_quiet", "to": "bull_volatile", "date": "2026-08-01"},
            ...
        ],
        "etf_frequency": {"510300": 18, "518850": 21, ...},
        "training_period": {"start": "2026-07-31", "end": "2026-08-28", "days": 21},
    }
    """

    def train(self, db: Session, days: int = 60) -> dict:
        """
        从历史分析记录中训练规则
        
        Args:
            db: 数据库会话
            days: 回看天数（默认60天）
        
        Returns:
            规则表字典
        """
        cutoff = date.today() - timedelta(days=days)

        def _parse_records(logs):
            """将日志解析为统一记录结构"""
            recs = []
            for log in logs:
                ar = log.analysis_result
                if isinstance(ar, str):
                    try:
                        ar = json.loads(ar)
                    except Exception:
                        continue
                if not isinstance(ar, dict):
                    continue

                ta = ar.get("technical_report") or {}
                sr = ar.get("sentiment_report") or {}
                recs.append({
                    "date": log.log_date.isoformat(),
                    "regime": ar.get("market_regime", "unknown"),
                    "action": ar.get("suggested_action", "hold"),
                    "allocation": ar.get("suggested_allocation") or {},
                    "tech_trend": ta.get("overall_trend", "unknown"),
                    "sentiment": sr.get("market_sentiment", "unknown"),
                    "sentiment_score": sr.get("sentiment_score", 0),
                })
            return recs

        def _load(action_type, since):
            return (
                db.query(AutoStrategyLog)
                .filter(
                    AutoStrategyLog.action_type == action_type,
                    AutoStrategyLog.log_date >= since,
                    AutoStrategyLog.analysis_result.isnot(None),
                )
                .order_by(AutoStrategyLog.log_date.asc())
                .all()
            )

        records = _parse_records(_load("analyzed", cutoff))

        # 回放记录（全量，不受days限制）
        replay_logs = (
            db.query(AutoStrategyLog)
            .filter(
                AutoStrategyLog.action_type == "replayed",
                AutoStrategyLog.analysis_result.isnot(None),
            )
            .order_by(AutoStrategyLog.log_date.asc())
            .all()
        )
        replay_records = _parse_records(replay_logs)

        if not records and not replay_records:
            logger.warning("[RuleTrainer] 无可用分析记录")
            return {"regime_rules": {}, "regime_transitions": [], "etf_frequency": {},
                    "training_period": None, "replay_rules": {}, "replay_period": None}

        def _build_regime_rules(recs):
            """regime → allocation 映射"""
            regime_alloc = defaultdict(lambda: {"allocations": [], "actions": [], "count": 0})
            for r in recs:
                regime = r["regime"]
                regime_alloc[regime]["allocations"].append(r["allocation"])
                regime_alloc[regime]["actions"].append(r["action"])
                regime_alloc[regime]["count"] += 1

            rules = {}
            for regime, data in regime_alloc.items():
                all_etfs = set()
                for alloc in data["allocations"]:
                    all_etfs.update(alloc.keys())

                avg_alloc = {}
                for etf in all_etfs:
                    values = [a.get(etf, 0) for a in data["allocations"]]
                    avg_alloc[etf] = round(sum(values) / len(values), 4) if values else 0

                total = sum(avg_alloc.values())
                if total > 0:
                    avg_alloc = {k: round(v / total, 4) for k, v in avg_alloc.items()}

                from collections import Counter
                action_counter = Counter(data["actions"])
                typical_action = action_counter.most_common(1)[0][0] if action_counter else "hold"

                rules[regime] = {
                    "avg_allocation": avg_alloc,
                    "sample_count": data["count"],
                    "typical_action": typical_action,
                    "etf_list": sorted(all_etfs),
                }
            return rules

        # 1. regime → allocation 映射
        regime_rules = _build_regime_rules(records) if records else {}
        replay_rules = _build_regime_rules(replay_records) if replay_records else {}

        # 2. regime 转换序列（真实记录）
        transitions = []
        for i in range(1, len(records)):
            if records[i]["regime"] != records[i-1]["regime"]:
                transitions.append({
                    "from": records[i-1]["regime"],
                    "to": records[i]["regime"],
                    "date": records[i]["date"],
                })

        # 3. ETF 出现频率（真实记录）
        etf_freq = defaultdict(int)
        for r in records:
            for etf in r["allocation"]:
                etf_freq[etf] += 1

        # 4. 训练期信息
        training_period = {
            "start": records[0]["date"],
            "end": records[-1]["date"],
            "days": len(records),
        } if records else None

        replay_period = {
            "start": replay_records[0]["date"],
            "end": replay_records[-1]["date"],
            "days": len(replay_records),
        } if replay_records else None

        result = {
            "regime_rules": regime_rules,
            "regime_transitions": transitions,
            "etf_frequency": dict(sorted(etf_freq.items(), key=lambda x: -x[1])),
            "training_period": training_period,
            "replay_rules": replay_rules,
            "replay_period": replay_period,
        }

        logger.info("[RuleTrainer] 训练完成: 真实%d天/%d种regime, 回放%d天/%d种regime" % (
            len(records), len(regime_rules), len(replay_records), len(replay_rules)
        ))

        return result

    def get_allocation_for_regime(self, regime: str, rules: dict) -> dict:
        """
        根据regime从规则表获取目标配置
        
        Args:
            regime: 市场状态
            rules: 训练后的规则表
        
        Returns:
            {etf_code: weight} 配置比例
        """
        regime_rules = rules.get("regime_rules", {})
        rule = regime_rules.get(regime)
        if rule:
            return rule.get("avg_allocation", {})
        # 真实规则缺失该状态时，回退到回放规则（补足熊市等未覆盖状态）
        # 规则引擎与AI裁决的状态枚举不同，需别名映射
        aliases = {
            "bull_strong": ["bull_quiet", "bull_volatile"],
            "bull_weak": ["bull_volatile", "bull_quiet"],
            "bear_weak": ["bear_quiet", "bear_panic"],
            "bear_strong": ["bear_panic", "crisis", "bear_quiet"],
        }
        replay_rules = rules.get("replay_rules") or {}
        for candidate in [regime] + aliases.get(regime, []):
            rule = replay_rules.get(candidate)
            if rule:
                return rule.get("avg_allocation", {})
        return {}

    def explain_regime(self, regime: str, rules: dict) -> str:
        """
        生成regime的可读解释
        """
        regime_rules = rules.get("regime_rules", {})
        rule = regime_rules.get(regime)
        if not rule:
            return "无规则数据"
        
        alloc = rule["avg_allocation"]
        top_etfs = sorted(alloc.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(["%s %.0f%%" % (k, v * 100) for k, v in top_etfs])
        
        return "%s (样本%d天) | 偏好: %s" % (
            rule["typical_action"], rule["sample_count"], top_str
        )


# 单例
_rule_trainer = None

def get_rule_trainer() -> RuleTrainer:
    global _rule_trainer
    if _rule_trainer is None:
        _rule_trainer = RuleTrainer()
    return _rule_trainer
