import json
import re
import logging
from typing import Dict, Optional
from openai import OpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BaseAgent:
    name: str = "base"

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )

    def call_llm(self, prompt: str, temperature: float = 0.3) -> Dict:
        if not self.llm_client:
            return {"error": "LLM客户端未配置"}
        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=2000,
            )
            content = response.choices[0].message.content
            return self._parse_json(content)
        except Exception as e:
            logger.error(f"[{self.name}] LLM调用失败: {e}")
            return {"error": str(e)}

    def _parse_json(self, content: str) -> Dict:
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"[{self.name}] JSON解析失败: {e}")
        return {"error": "无法解析响应"}
