import json
import re
import logging
from typing import Dict, List, Optional
from openai import OpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# 辩论 agent 单轮最多执行的工具数，防止循环
MAX_TOOL_ROUNDS = 3


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

    def call_llm_with_tools(self, prompt: str, db=None, temperature: float = 0.3,
                            max_rounds: int = MAX_TOOL_ROUNDS) -> Dict:
        """带工具的多轮调用：LLM 按需调用只读工具取数后，再输出最终分析。

        db 为 None 时退化为普通 call_llm（无工具能力）。
        """
        if not self.llm_client:
            return {"error": "LLM客户端未配置"}
        if db is None:
            return self.call_llm(prompt, temperature=temperature)

        tools = self._get_read_tool_schemas()
        if not tools:
            return self.call_llm(prompt, temperature=temperature)

        messages = [{"role": "user", "content": prompt}]
        tool_calls_used = []

        for _ in range(max_rounds):
            try:
                response = self.llm_client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=2000,
                )
            except Exception as e:
                logger.error(f"[{self.name}] LLM调用失败: {e}")
                return {"error": str(e)}

            choice = response.choices[0]
            assistant_msg = choice.message
            if not assistant_msg.tool_calls:
                return self._parse_json(assistant_msg.content or "")

            # 执行本轮全部工具调用，结果回填
            messages.append({
                "role": "assistant",
                "content": assistant_msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in assistant_msg.tool_calls
                ],
            })

            for tc in assistant_msg.tool_calls:
                name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = self._execute_tool(name, arguments, db)
                tool_calls_used.append({"tool": name, "arguments": arguments})
                result_str = json.dumps(result, ensure_ascii=False, default=str)[:2000]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # 达到轮数上限：用最后一次回复兜底
        return self._parse_json(assistant_msg.content or "")

    def _get_read_tool_schemas(self) -> List[Dict]:
        """获取只读工具 schema（write 类不开放给辩论 agent）"""
        from app.tools.registry import get_tool_registry, _TOOL_REGISTRY

        get_tool_registry()  # 确保工具已注册
        schemas = []
        for t in _TOOL_REGISTRY.values():
            if t.risk_level != "write":
                schemas.append(t.to_openai_schema())
        return schemas

    def _execute_tool(self, tool_name: str, arguments: Dict, db) -> Dict:
        """执行只读工具（辩论场景，工具均为 read 类，直接执行）"""
        from app.tools.registry import get_tool_registry

        return get_tool_registry().execute(tool_name, arguments, db)

    def _parse_json(self, content: str) -> Dict:
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"[{self.name}] JSON解析失败: {e}")
        return {"error": "无法解析响应"}
