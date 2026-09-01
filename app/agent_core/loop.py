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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import SessionLocal
from app.tools.registry import get_tool_registry
from app.agent_core.context import ContextBuilder
from app.agent_core.memory import ChatMemory
from app.agent_core.permissions import Mode, PermissionEngine
from app.agent_core import compaction, provider
from app.agent_core.approvals import (
    APPROVAL_OUTCOME_ALWAYS,
    APPROVAL_OUTCOME_DENY,
    get_approval_store,
)

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_TOOL_ROUNDS = 10  # 防止无限循环

# 会话级中断标志（POST /api/chat/stop 置位，_iter_events 在迭代边界检查）
_INTERRUPT_FLAGS: Dict[str, bool] = {}
_INTERRUPT_LOCK = threading.Lock()


def request_interrupt(session_id: str) -> None:
    with _INTERRUPT_LOCK:
        _INTERRUPT_FLAGS[session_id] = True


def check_interrupted(session_id: str) -> bool:
    with _INTERRUPT_LOCK:
        return _INTERRUPT_FLAGS.get(session_id, False)


def clear_interrupt(session_id: str) -> None:
    with _INTERRUPT_LOCK:
        _INTERRUPT_FLAGS.pop(session_id, None)


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

每轮对话开始前会有一条「系统状态快照」消息，包含当前时间与策略/市场/风控状态，以它为事实依据。

可用技能：
{skills}
"""


class AgentLoop:
    """LLM 决策循环 - 支持多轮 tool calling"""

    def __init__(self):
        self.registry = get_tool_registry()
        self.context_builder = ContextBuilder()
        self.memory = ChatMemory()
        self.permissions = PermissionEngine()
        self.client = provider.build_openai_client(settings.llm_api_base_url, settings.llm_api_key)

    def _build_skills_summary(self) -> str:
        """构建可用技能摘要（仅 name+description，全文按需 load_skill；只含模型可调用技能）"""
        from app.agent_core.skill_manager import get_skill_manager

        skills = get_skill_manager().list_skills(audience="model")
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
        final_content = ""
        tool_calls_made: List[Dict] = []
        sid = session_id or ""

        for ev in self._iter_events(user_message, session_id, db):
            ev_type = ev["type"]
            if ev_type == "turn_start":
                sid = ev["data"]["session_id"]
            elif ev_type == "tool_finished":
                tool_calls_made.append({
                    "tool": ev["data"]["tool"],
                    "arguments": ev["data"].get("arguments", {}),
                    "result_preview": ev["data"].get("preview", ""),
                })
            elif ev_type == "assistant_message":
                final_content = ev["data"].get("content", "")
            elif ev_type == "error":
                return AgentResponse(
                    content=ev["data"].get("error", "AI服务调用失败"),
                    error=ev["data"].get("error", "unknown"),
                    session_id=sid,
                )

        return AgentResponse(
            content=final_content,
            tool_calls_made=tool_calls_made,
            session_id=sid,
        )

    def run_streaming(self, user_message: str, session_id: Optional[str], db: Session):
        """流式执行：逐条产出事件字典，配合 SSE 推送给前端实时渲染。

        事件类型:
            turn_start          {"session_id"}
            thinking_delta      {"content"}         # 思考增量（流式）
            thinking_done       {"content"}         # 思考结束（折叠）
            text_delta          {"content"}         # 正文增量（流式）
            tool_started        {"seq", "tool", "arguments"}
            permission_required {"request_id", "seq", "tool", "arguments", "reason"}
            tool_finished       {"seq", "tool", "status", "preview", "arguments"}
            assistant_message   {"content"}
            turn_end            {"session_id", "tool_calls"}
            interrupted         {"session_id"}
            error               {"error"}
        """
        yield from self._iter_events(user_message, session_id, db)

    def _iter_events(self, user_message: str, session_id: Optional[str], db: Session):
        """一次完整对话的事件流生成器（run 与 run_streaming 共用）"""
        # 确保会话存在
        session_id = self.memory.get_or_create_session(session_id, db)
        yield {"type": "turn_start", "data": {"session_id": session_id}}
        clear_interrupt(session_id)

        # 会话级模型/Provider 解析（模型别名 → base_url/API Key）
        session_model = self.memory.get_session_model(session_id, db)
        if session_model:
            base_url, api_key, model = provider.resolve_llm(session_model)
            client = provider.build_openai_client(base_url, api_key)
        else:
            model = settings.llm_model
            client = self.client
        if not client:
            yield {"type": "error", "data": {"error": "LLM未配置，请设置 LLM_API_KEY 环境变量。"}}
            return

        # 保存用户消息
        self.memory.save_message(session_id, "user", user_message, db=db)

        # 构建消息列表（system prompt 只含静态身份与规则，跨轮字节稳定）
        skills_summary = self._build_skills_summary()
        system_msg = SYSTEM_PROMPT.format(skills=skills_summary)

        # 加载历史
        history = self.memory.get_history(session_id, db)
        # 排除刚保存的最后一条（就是当前 user_message）
        if history and history[-1].get("content") == user_message:
            history = history[:-1]

        # 上下文压缩：历史过长则生成摘要，出站视图只保留最近轮
        if compaction.compaction_due(
            compaction.estimate_tokens(history),
            settings.context_window_tokens,
            settings.compaction_threshold,
            settings.compaction_min_tokens,
        ):
            yield {"type": "compacting", "data": {"session_id": session_id}}
            summary = compaction.summarize(history, lambda msgs: self._complete_text(msgs, client, model))
            if summary:
                self.memory.save_summary(session_id, summary, db)
                history = self.memory.get_history(session_id, db)
                if history and history[-1].get("content") == user_message:
                    history = history[:-1]
            yield {"type": "compacted", "data": {"session_id": session_id}}

        messages = [{"role": "system", "content": system_msg}]
        messages.extend(history)

        # 系统状态快照：动态状态（时间/摘要/策略/市场/风控）以 user-role 消息注入，
        # 不落库；与静态 system prompt 分离以保 provider prompt cache
        snapshot = self.context_builder.build_turn_snapshot(
            db, summary=self.memory.get_summary(session_id, db)
        )
        messages.append({"role": "user", "content": f"[系统状态快照]\n{snapshot}"})

        # 添加当前用户消息
        messages.append({"role": "user", "content": user_message})

        # 获取工具定义
        tools = self.registry.get_openai_tools()

        # 多轮工具调用循环
        tool_calls_made = []
        final_content = ""
        seq = 0
        round_usage = None

        for _ in range(MAX_TOOL_ROUNDS):
            # 中断检查：上一轮工具执行中用户点了停止
            if check_interrupted(session_id):
                yield {"type": "interrupted", "data": {"session_id": session_id}}
                return

            # 流式LLM调用（参照deepseek-harness translate.ts模式）；
            # 瞬态错误有界重试：仅在未产出任何内容时，避免前端收到重复增量
            text_acc = ""
            thinking_acc = ""
            tool_calls_acc = {}
            got_stream_error = False
            stream_error_msg = ""
            max_retries = getattr(settings, "llm_stream_retries", 2)
            retry_base = getattr(settings, "llm_stream_retry_base", 1.0)

            for attempt in range(max_retries + 1):
                text_acc = ""
                thinking_acc = ""
                tool_calls_acc = {}
                got_stream_error = False
                emitted_any = False
                try:
                  for ev in provider.stream_completion(client, model, messages, tools if tools else None):
                    etype = ev["type"]
                    if etype == "thinking_delta":
                        thinking_acc += ev["content"]
                        emitted_any = True
                        yield {"type": "thinking_delta", "data": {"content": ev["content"]}}
                    elif etype == "text_delta":
                        text_acc += ev["content"]
                        emitted_any = True
                        yield {"type": "text_delta", "data": {"content": ev["content"]}}
                    elif etype == "thinking_done":
                        yield {"type": "thinking_done", "data": {"content": thinking_acc}}
                    elif etype == "tool_calls":
                        tool_calls_acc = {tc["id"]: tc for tc in ev["tool_calls"]}
                    elif etype == "usage":
                        round_usage = {
                            "usage": ev.get("usage") or {},
                            "finish_reason": ev.get("finish_reason"),
                        }
                        yield {"type": "usage", "data": round_usage}
                    elif etype == "done":
                        break
                    elif etype == "error":
                        stream_error_msg = ev.get("error", "流式响应异常")
                        got_stream_error = True
                        break
                except Exception as _stream_exc:
                    logger.error(f"流式迭代异常: {_stream_exc}")
                    stream_error_msg = f"流式响应异常: {_stream_exc}"
                    got_stream_error = True

                if not got_stream_error:
                    break
                if emitted_any or attempt >= max_retries or check_interrupted(session_id):
                    break
                backoff = retry_base * (2 ** attempt)
                logger.warning(f"[AgentLoop] 流式错误（第{attempt + 1}次），{backoff}s后重试: {stream_error_msg}")
                time.sleep(backoff)

            if got_stream_error:
                yield {"type": "error", "data": {"error": stream_error_msg or "流式响应异常"}}
                return

            # 构造虚拟assistant_msg供后续工具执行逻辑复用
            class _VirtualMsg:
                pass
            assistant_msg = _VirtualMsg()
            assistant_msg.content = text_acc
            assistant_msg.tool_calls = []

            if tool_calls_acc:
                for tc_id, tc_data in tool_calls_acc.items():
                    tc_obj = _VirtualMsg()
                    tc_obj.id = tc_data["id"]
                    tc_obj.function = _VirtualMsg()
                    tc_obj.function.name = tc_data["function"]["name"]
                    tc_obj.function.arguments = tc_data["function"]["arguments"]
                    assistant_msg.tool_calls.append(tc_obj)

            # 如果没有工具调用，结束循环
            if not assistant_msg.tool_calls:
                final_content = text_acc
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

            # 逐个执行工具：先全部发出 tool_started，读操作并行执行，写操作串行审批
            prepared = []
            for tc in assistant_msg.tool_calls:
                seq += 1
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}
                prepared.append({"seq": seq, "tc": tc, "tool": tool_name, "args": arguments})
                logger.info(f"[AgentLoop] 调用工具: {tool_name}({arguments})")
                yield {"type": "tool_started", "data": {"seq": seq, "tool": tool_name, "arguments": arguments}}

            # 只读工具并行执行（每任务独立 Session，避免跨线程共享）
            parallel_ids = {id(it) for it in prepared if self._is_parallel_safe(it["tool"])}
            parallel_results = {}
            if parallel_ids:
                targets = [it for it in prepared if id(it) in parallel_ids]

                def _exec_parallel(it):
                    tidb = SessionLocal()
                    try:
                        return self.registry.execute(it["tool"], it["args"], tidb)
                    finally:
                        tidb.close()

                with ThreadPoolExecutor(max_workers=4) as ex:
                    for it, res in zip(targets, ex.map(_exec_parallel, targets)):
                        parallel_results[id(it)] = res

            tool_results_for_db = []
            for it in prepared:
                seq, tc = it["seq"], it["tc"]
                tool_name, arguments = it["tool"], it["args"]
                if check_interrupted(session_id):
                    # 中断：未执行工具回填 error，避免孤儿 tool_calls
                    result = {"error": "用户中断，工具未执行"}
                elif id(it) in parallel_results:
                    result = parallel_results[id(it)]
                else:
                    result = yield from self._exec_tool(tool_name, arguments, db, seq, session_id)

                result_str = json.dumps(result, ensure_ascii=False, default=str)
                # 截断过长结果
                if len(result_str) > 3000:
                    result_str = result_str[:3000] + "...(截断)"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                status = "error" if (isinstance(result, dict) and result.get("error")) else "success"
                yield {
                    "type": "tool_finished",
                    "data": {
                        "seq": seq,
                        "tool": tool_name,
                        "arguments": arguments,
                        "status": status,
                        "preview": result_str[:1500],
                    },
                }

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
                tool_calls=tc_list, tool_results=tool_results_for_db,
                usage=round_usage, db=db
            )

        # 保存最终回复
        if final_content:
            self.memory.save_message(session_id, "assistant", final_content, usage=round_usage, db=db)
            # 自动更新会话标题
            self.memory.update_session_title(session_id, user_message, db)

        yield {"type": "assistant_message", "data": {"content": final_content}}
        yield {"type": "turn_end", "data": {"session_id": session_id, "tool_calls": tool_calls_made}}

    def _is_parallel_safe(self, tool_name: str) -> bool:
        """只读工具可并行执行（无副作用、无需审批）"""
        tool_def = self.registry.get_tool(tool_name)
        risk_level = getattr(tool_def, "risk_level", "read") if tool_def else "read"
        return risk_level == "read"

    def _exec_tool(self, tool_name: str, arguments: dict, db: Session, seq: int, session_id: str):
        """执行单个工具：权限判定 + 写操作审批（生成器，yield 权限事件并返回执行结果）"""
        tool_def = self.registry.get_tool(tool_name)
        risk_level = getattr(tool_def, "risk_level", "read") if tool_def else "read"
        decision = self.permissions.evaluate(tool_name, risk_level)

        if decision.allowed:
            return self.registry.execute(tool_name, arguments, db)

        # 明确拒绝（如只读模式），不弹审批
        if not decision.needs_user:
            return {"error": decision.reason}

        # 全局自动审批模式：跳过弹窗直接执行
        store = get_approval_store()
        if store.auto_approve:
            return self.registry.execute(tool_name, arguments, db)

        # 需要审批的写操作：发权限事件，阻塞等待用户在前端审批
        req = store.create(
            tool_name, arguments, timeout=getattr(settings, "chat_approval_timeout", 120)
        )
        yield {
            "type": "permission_required",
            "data": {
                "request_id": req.request_id,
                "seq": seq,
                "tool": tool_name,
                "arguments": arguments,
                "reason": decision.reason,
            },
        }
        outcome = store.wait(req)
        if outcome == APPROVAL_OUTCOME_DENY:
            return {"error": "用户拒绝授权，写操作未执行"}
        if outcome == APPROVAL_OUTCOME_ALWAYS:
            self.permissions.allow_tool_for_session(tool_name)
        return self.registry.execute(tool_name, arguments, db)

    def _complete_text(self, msgs: List[dict], client=None, model: str = "") -> str:
        """短文本补全（用于上下文摘要等）"""
        if client is None:
            client = self.client
        if not model:
            model = settings.llm_model
        resp = client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=0.2,
            max_tokens=500,
        )
        return resp.choices[0].message.content or ""

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

        skills_summary = self._build_skills_summary()
        system_msg = SYSTEM_PROMPT.format(skills=skills_summary)

        # 状态快照并入首条 user 消息（system prompt 保持静态）
        snapshot = self.context_builder.build_turn_snapshot(db)
        autonomous_prompt = f"""[系统状态快照]
{snapshot}

[自主决策模式 - 触发类型: {trigger}]

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
