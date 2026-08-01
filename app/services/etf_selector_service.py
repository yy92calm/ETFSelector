import json
import logging
from typing import Dict, List, Optional
from openai import OpenAI
from sqlalchemy.orm import Session
import numpy as np

from app.config import get_settings
from app.models.etf import ETFBasic, ETFQuotation

logger = logging.getLogger(__name__)
settings = get_settings()


class ETFSelectorService:
    """同类ETF优选评分服务 - 跟踪同一指数的多只ETF中选出最优"""

    SELECT_PROMPT = """你是ETF选品分析师。基于以下同类ETF对比数据，为每个指数/赛道选出最优ETF。

## 同类ETF对比数据
{comparison_data}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "selections": [
    {{
      "category": "指数/赛道名称",
      "selected_etf": "选中ETF代码",
      "selected_name": "选中ETF名称",
      "score": 0.0-1.0,
      "reasons": ["选择理由"],
      "runners_up": [
        {{"etf_code": "代码", "reason_not_selected": "未选原因"}}
      ]
    }}
  ],
  "overall_notes": "选品整体说明",
  "summary": "一句话总结"
}}

评分权重：
- 流动性（成交额）: 30%
- 跟踪精度（波动率越低越好，作为代理）: 25%
- 规模代理（日均成交额稳定性）: 25%
- 近期动量（同等条件下优选）: 20%"""

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )

    def select_best_etfs(self, db: Session, categories: Optional[Dict[str, List[str]]] = None) -> Dict:
        if not self.llm_client:
            return {"error": "LLM客户端未配置"}

        if categories is None:
            categories = self._auto_group_etfs(db)

        if not categories:
            return {"error": "无可对比的同类ETF分组"}

        comparison = self._build_comparison(categories, db)
        if not comparison:
            return {"error": "行情数据不足，无法对比"}

        prompt = self.SELECT_PROMPT.format(
            comparison_data=json.dumps(comparison, ensure_ascii=False, indent=2)
        )

        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2000,
            )
            content = response.choices[0].message.content
            return self._parse_json(content)
        except Exception as e:
            logger.error(f"ETF优选LLM调用失败: {e}")
            return {"error": str(e)}

    GROUP_PROMPT = """你是ETF分类专家。将以下ETF按跟踪指数/赛道分组，同类ETF归为一组（至少2只才算一组）。

## ETF列表
{etf_list}

输出JSON（不要包含其他文字）：
{{
  "groups": {{
    "赛道/指数名称": ["ETF代码1", "ETF代码2", ...]
  }}
}}

规则：
- 只输出有2只及以上同类ETF的分组
- 分组名用简洁的赛道/指数名（如"沪深300""半导体""黄金"）
- 无法归类的ETF忽略"""

    def _auto_group_etfs(self, db: Session) -> Dict[str, List[str]]:
        etfs = db.query(ETFBasic).limit(200).all()
        if not etfs:
            return {}

        etf_list = [{"code": e.etf_code, "name": e.etf_name or ""} for e in etfs]

        if not self.llm_client:
            return self._fallback_group(etfs)

        prompt = self.GROUP_PROMPT.format(
            etf_list=json.dumps(etf_list, ensure_ascii=False)
        )

        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            content = response.choices[0].message.content
            result = self._parse_json(content)
            groups = result.get("groups", {})
            return {k: v for k, v in groups.items() if len(v) >= 2}
        except Exception as e:
            logger.warning(f"LLM分组失败，降级为关键词匹配: {e}")
            return self._fallback_group(etfs)

    def _fallback_group(self, etfs: List) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        keyword_map = {
            "沪深300": ["300"], "中证500": ["500"], "创业板": ["创业板", "创50"],
            "科创50": ["科创"], "纳斯达克": ["纳斯达克", "纳指"],
            "黄金": ["黄金"], "债券": ["国债", "信用债", "转债"],
            "半导体": ["半导体", "芯片"], "新能源": ["新能源", "光伏", "锂电"],
            "医药": ["医药", "医疗"], "消费": ["消费", "食品", "白酒"],
            "军工": ["军工", "国防"],
        }
        for etf in etfs:
            name = etf.etf_name or ""
            for category, keywords in keyword_map.items():
                if any(kw in name for kw in keywords):
                    groups.setdefault(category, []).append(etf.etf_code)
                    break
        return {k: v for k, v in groups.items() if len(v) >= 2}

    def _build_comparison(self, categories: Dict[str, List[str]], db: Session) -> Dict:
        comparison = {}
        for category, codes in categories.items():
            etf_metrics = []
            for code in codes[:6]:
                metrics = self._compute_etf_metrics(code, db)
                if metrics:
                    etf_metrics.append(metrics)

            if len(etf_metrics) >= 2:
                comparison[category] = etf_metrics

        return comparison

    def _compute_etf_metrics(self, etf_code: str, db: Session) -> Optional[Dict]:
        quotes = db.query(ETFQuotation).filter(
            ETFQuotation.etf_code == etf_code
        ).order_by(ETFQuotation.trade_date.desc()).limit(60).all()

        if len(quotes) < 20:
            return None

        quotes.reverse()
        prices = [q.close_price for q in quotes]
        amounts = [q.amount for q in quotes]
        volumes = [q.volume for q in quotes]

        daily_returns = [(prices[i] / prices[i-1] - 1) for i in range(1, len(prices))]
        volatility = np.std(daily_returns) * np.sqrt(252) * 100

        avg_amount_20d = np.mean(amounts[-20:])
        amount_stability = 1 - (np.std(amounts[-20:]) / np.mean(amounts[-20:])) if np.mean(amounts[-20:]) else 0

        momentum_20d = (prices[-1] / prices[-20] - 1) * 100 if prices[-20] else 0

        etf_info = db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first()

        return {
            "etf_code": etf_code,
            "etf_name": etf_info.etf_name if etf_info else "",
            "avg_daily_amount_20d": round(avg_amount_20d, 0),
            "amount_stability": round(max(0, amount_stability), 3),
            "volatility_annualized_pct": round(volatility, 2),
            "momentum_20d_pct": round(momentum_20d, 2),
            "latest_price": prices[-1],
        }

    def _parse_json(self, content: str) -> Dict:
        import re
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"ETF优选JSON解析失败: {e}")
        return {"error": "无法解析响应"}


_service: ETFSelectorService | None = None


def get_etf_selector_service() -> ETFSelectorService:
    global _service
    if _service is None:
        _service = ETFSelectorService()
    return _service
