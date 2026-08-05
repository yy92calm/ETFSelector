"""
Agent Core Loop - LLM 决策循环

支持多轮 Tool Calling 的 ReAct 模式：
1. 组装 system prompt + 上下文 + 用户消息
2. 调用 LLM（带 tools 定义）
3. 如果 LLM 返回 tool_calls -> 执行 -> 结果回填 -> 再次调用
4. 循环直到 LLM 返回最终文本回复
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.tools.registry import get_tool_registry
from app.agent_core.context import ContextBuilder
from app.agent_core.memory import ChatMemory

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_TOOL_ROUNDS = 10  # 防止无限循环


@dataclass
class AgentResponse:
    """Agent 响应结构"""
    content: str = ""
    tool_calls_made: List[Dict] = field(default_factory=list)
    session_id: str = ""
    error: Optional[str] = None


SYSTEM_PROMPT = """你是ETF量化工作台的AI决策大脑，拥有完整的自主管理能力。你负责：
1. 主动分析市场环境，发现投资机会和风险信号
2. 自主管理ETF配置组合（搜索新标的、创建策略、调整配置、汰换劣策略）
3. 监控风险，必要时立即采取行动（暂停策略、减仓、触发熔断）
4. 回答用户关于市场、策略、持仓的问题

你可以通过工具调用来执行具体操作。请遵循以下原则：
- 主动管理：不要等待指令，发现机会或风险要主动行动
- 数据驱动：先获取数据再下结论，用 evaluate_strategy_health 评估策略健康度
- 风险优先：任何调整前先用 check_circuit_breaker 确认风控状态
- 分散配置：单一ETF不超过40%，保持组合多样性
- 生命周期：表现持续不佳的策略应暂停，发现新机会应创建新策略
- 简洁明了：回复精炼，重点突出

当前系统状态：
{context}

可用技能：
{skills}
"""


class AgentLoop:
    """LLM 决策循环 - 支持多轮 tool calling"""

    def __init__(self):
        self.client: Optional[OpenAI] = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
        self.registry = get_tool_registry()
        self.context_builder = ContextBuilder()
        self.memory = ChatMemory()

    def _build_skills_summary(self) -> str:
        """构建可用技能摘要（仅 name+description，全文按需 load_skill）"""
        from app.agent_core.skill_manager import get_skill_manager

        skills = get_skill_manager().list_skills()
        if not skills:
            return "- 无（如需外部数据接入，可提示用户配置 MCP server 和技能）"
        return "\n".join(
            f"- {s['name']}: {s['description']}" for s in skills
        )

    def run(self, user_message: str, session_id: Optional[str], db: Session) -> AgentResponse:
        """对话式执行：处理用户消息，可能触发多轮工具调用

        Args:
            user_message: 用户输入
            session_id: 会话ID（None则新建）
            db: 数据库会话

        Returns:
            AgentResponse 包含最终回复和工具调用记录
        """
        if not self.client:
            return AgentResponse(content="LLM未配置，请设置 LLM_API_KEY 环境变量。", error="no_llm")

        # 确保会话存在
        session_id = self.memory.get_or_create_session(session_id, db)

        # 保存用户消息
        self.memory.save_message(session_id, "user", user_message, db=db)

        # 构建消息列表
        context = self.context_builder.build_system_context(db)
        skills_summary = self._build_skills_summary()
        system_msg = SYSTEM_PROMPT.format(context=context, skills=skills_summary)

        messages = [{"role": "system", "content": system_msg}]

        # 加载历史
        history = self.memory.get_history(session_id, db)
        # 排除刚保存的最后一条（就是当前 user_message）
        if history and history[-1].get("content") == user_message:
            history = history[:-1]
        messages.extend(history)

        # 添加当前用户消息
        messages.append({"role": "user", "content": user_message})

        # 获取工具定义
        tools = self.registry.get_openai_tools()

        # 多轮工具调用循环
        tool_calls_made = []
        final_content = ""

        for round_idx in range(MAX_TOOL_ROUNDS):
            try:
                response = self.client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    tools=tools if tools else None,
                    temperature=0.3,
                    max_tokens=2000,
                )
            except Exception as e:
                logger.error(f"LLM调用失败: {e}")
                return AgentResponse(content=f"AI服务调用失败: {str(e)}", error=str(e), session_id=session_id)

            choice = response.choices[0]
            assistant_msg = choice.message

            # 如果没有工具调用，结束循环
            if not assistant_msg.tool_calls:
                final_content = assistant_msg.content or ""
                break

            # 有工具调用 -> 执行
            # 记录 assistant 消息（含 tool_calls）
            tc_list = []
            for tc in assistant_msg.tool_calls:
                tc_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                })

            messages.append({
                "role": "assistant",
                "content": assistant_msg.content or "",
                "tool_calls": tc_list,
            })

            # 逐个执行工具
            tool_results_for_db = []
            for tc in assistant_msg.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                logger.info(f"[AgentLoop] 调用工具: {tool_name}({arguments})")
                result = self.registry.execute(tool_name, arguments, db)

                result_str = json.dumps(result, ensure_ascii=False, default=str)
                # 截断过长结果
                if len(result_str) > 3000:
                    result_str = result_str[:3000] + "...(截断)"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                tool_calls_made.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result_preview": result_str[:200],
                })
                tool_results_for_db.append({
                    "tool_call_id": tc.id,
                    "tool_name": tool_name,
                    "content": result_str[:1000],
                })

            # 保存工具调用记录
            self.memory.save_message(
                session_id, "assistant", assistant_msg.content,
                tool_calls=tc_list, tool_results=tool_results_for_db, db=db
            )

        # 保存最终回复
        if final_content:
            self.memory.save_message(session_id, "assistant", final_content, db=db)
            # 自动更新会话标题
            self.memory.update_session_title(session_id, user_message, db)

        return AgentResponse(
            content=final_content,
            tool_calls_made=tool_calls_made,
            session_id=session_id,
        )

    def run_autonomous(self, trigger: str, instruction: str, db: Session) -> AgentResponse:
        """自主决策模式：无用户交互，LLM根据指令自主行动

        Args:
            trigger: 触发类型（daily/condition/manual）
            instruction: 给LLM的指令
            db: 数据库会话

        Returns:
            AgentResponse
        """
        if not self.client:
            return AgentResponse(content="LLM未配置", error="no_llm")

        context = self.context_builder.build_system_context(db)
        system_msg = SYSTEM_PROMPT.format(context=context)

        autonomous_prompt = f"""[自主决策模式 - 触发类型: {trigger}]

{instruction}

请分析当前状态，决定是否需要采取行动。如果需要，请调用相应工具执行。
如果一切正常无需操作，直接回复"维持现状"及简要理由。
回复格式：先给出结论，再说明理由。"""

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": autonomous_prompt},
        ]

        tools = self.registry.get_openai_tools()
        tool_calls_made = []
        final_content = ""

        for round_idx in range(MAX_TOOL_ROUNDS):
            try:
                response = self.client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    tools=tools if tools else None,
                    temperature=0.2,
                    max_tokens=2000,
                )
            except Exception as e:
                logger.error(f"[自主决策] LLM调用失败: {e}")
                self._log_action(trigger, f"LLM调用失败: {e}", [], "failed", db)
                return AgentResponse(content=f"AI服务调用失败: {str(e)}", error=str(e))

            choice = response.choices[0]
            assistant_msg = choice.message

            if not assistant_msg.tool_calls:
                final_content = assistant_msg.content or ""
                break

            # 执行工具调用
            tc_list = []
            for tc in assistant_msg.tool_calls:
                tc_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                })

            messages.append({
                "role": "assistant",
                "content": assistant_msg.content or "",
                "tool_calls": tc_list,
            })

            for tc in assistant_msg.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                logger.info(f"[自主决策] 调用工具: {tool_name}({arguments})")
                result = self.registry.execute(tool_name, arguments, db)

                result_str = json.dumps(result, ensure_ascii=False, default=str)
                if len(result_str) > 3000:
                    result_str = result_str[:3000] + "...(截断)"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                tool_calls_made.append({"tool": tool_name, "arguments": arguments})

        # 记录自主决策日志
        status = "completed" if not final_content.startswith("LLM") else "failed"
        self._log_action(trigger, final_content, tool_calls_made, status, db)

        return AgentResponse(content=final_content, tool_calls_made=tool_calls_made)

    def _log_action(self, trigger: str, reasoning: str, tools_called: list, status: str, db: Session):
        """记录AI自主决策日志"""
        from app.models.chat import AIActionLog

        log = AIActionLog(
            trigger_type=trigger,
            reasoning=reasoning[:2000] if reasoning else "",
            actions_taken=[t.get("tool") for t in tools_called] if tools_called else [],
            tools_called=tools_called[:10],
            status=status,
        )
        db.add(log)
        db.commit()
