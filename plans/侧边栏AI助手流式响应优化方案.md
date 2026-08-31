# 侧边栏AI助手流式响应优化方案

## 参照系统：deepseek-harness

deepseek-harness 的流式架构（7个关键模块）：

| 模块 | 路径 | 作用 |
|------|------|------|
| StreamChunk协议 | `packages/llm/llm/src/types.ts:291-303` | 7种chunk类型：block-start/text-delta/reasoning-delta/tool-call-delta/block-end/usage/finish |
| SSE帧解析 | `packages/llm/llm-deepseek/src/sse.ts` | eventsource-parser解析SSE，遇到[DONE]结束 |
| Wire→StreamChunk转换 | `packages/llm/llm-deepseek/src/translate.ts:86-184` | 状态机：reasoning_content→reasoning-delta，所有block-end延迟到[DONE]才发 |
| 块组装器 | `packages/llm/llm/src/assembler.ts:36-163` | 服务端：index稀疏数组，块级不可变，block-end后delta静默忽略 |
| 客户端增量折叠 | `packages/client/runtime/src/client/sessions/partial.ts:23-103` | 客户端：StreamChunk→AssistantBlock[]，每个delta只替换对应index引用 |
| Thinking折叠展示 | `packages/client/ui-conversation/src/client/chat/ReasoningRow.tsx:27-64` | 折叠态显示首行/末行摘要，流式时自动滚到尾部，完成后折叠 |
| 帧节流 | `packages/client/ui-conversation/src/client/chat/use-throttled-visual-update.ts` | animation-frame节流，避免每个token触发React重渲染 |

**harness核心设计模式**：
- Block-index协议：每个content block有唯一index，delta通过index关联
- 延迟关闭：block-end/usage/finish全部延迟到`[DONE]`才发
- 双层组装：服务端BlockAssembler + 客户端PartialAccumulator
- Reasoning原生字段：DeepSeek的`reasoning_content`是独立字段，不需要`<think>`标签解析
- Waterfall拦截：`llm/stream`事件允许中间件链拦截/替换chunk流

**ETFSelector与harness的关键差异**：
- Qwen没有`reasoning_content`原生字段，需要从`<think>`标签解析
- ETFSelector用SSE（已有），harness用WebSocket
- ETFSelector规模小，不需要BlockAssembler级别的复杂度，可简化

---

## 现状与瓶颈

```
当前流程：
用户输入 → POST /api/chat/stream → AgentLoop.run_streaming()
    → _iter_events()
        → client.chat.completions.create()  ← 无stream=True，阻塞5-30秒
        → yield {type: "assistant_message", content: "完整文本"}
        → 前端一次性渲染
```

| 瓶颈 | 位置 | 体感 |
|------|------|------|
| LLM调用非流式 | `loop.py:235` | 长时间空白→突然整段文字出现 |
| thinking无法渐进展示 | `<think>`块被整体截取 | 思考过程不可见 |

## 目标

1. 体感响应时间从5-30秒降至即时（首token <1秒）
2. LLM回复逐token流入，`<think>`过程可视
3. 工具调用前显示"正在思考"动态指示器

## 方案设计

### Phase 1：后端流式化

#### 1.1 provider.py — 简化版StreamChunk协议

参照harness的7种chunk类型，ETFSelector简化为4种：

```python
# 参照 harness/types.ts:291-303 的 StreamChunk 协议，简化为4种
def stream_completion(client, model, messages, tools=None, temperature=0.3, max_tokens=2000):
    """流式调用LLM，逐chunk产出。

    协议设计参照deepseek-harness StreamChunk（types.ts:291），简化为：
    - text_delta:      正文增量（对应harness text-delta）
    - thinking_delta:  思考增量（对应harness reasoning-delta）
    - tool_calls:      完整工具调用列表（对应harness tool-call-delta累积后的结果）
    - done:            流结束（对应harness finish）
    """
    response = client.chat.completions.create(
        model=model, messages=messages, tools=tools,
        temperature=temperature, max_tokens=max_tokens,
        stream=True,
    )
    in_thinking = False
    tool_calls_buf = {}  # {index: {id, function: {name, arguments}}}

    for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta:
            continue

        content = getattr(delta, "content", None) or ""
        if content:
            # 参照harness translate.ts的reasoning识别逻辑：
            # harness用reasoning_content原生字段；Qwen用<think>标签解析
            if "<think>" in content:
                in_thinking = True
                # 剥离<think>标签本身，只输出内容
                tag_stripped = content.replace("<think>", "", 1)
                if tag_stripped:
                    yield {"type": "thinking_delta", "content": tag_stripped}
            elif in_thinking and "</think>" in content:
                before, after = content.split("</think>", 1)
                if before:
                    yield {"type": "thinking_delta", "content": before}
                in_thinking = False
                yield {"type": "thinking_done", "content": ""}
                if after:
                    yield {"type": "text_delta", "content": after}
            elif in_thinking:
                yield {"type": "thinking_delta", "content": content}
            else:
                yield {"type": "text_delta", "content": content}

        # tool_calls增量累积（参照harness assembler.ts的index稀疏数组模式）
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_calls_buf:
                    tool_calls_buf[idx] = {"id": "", "type": "function",
                                           "function": {"name": "", "arguments": ""}}
                if tc.id:
                    tool_calls_buf[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_calls_buf[idx]["function"]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls_buf[idx]["function"]["arguments"] += tc.function.arguments

        if chunk.choices and chunk.choices[0].finish_reason:
            break

    # 参照harness translate.ts: 延迟发送tool_calls和finish（在[DONE]时统一发）
    if tool_calls_buf:
        yield {"type": "tool_calls", "tool_calls": list(tool_calls_buf.values())}
    yield {"type": "done"}
```

#### 1.2 loop.py — `_iter_events` 改为流式

参照harness的PartialAccumulator（partial.ts:23-103），在loop层做增量累积+事件转发：

```python
# 改造前（loop.py:235，阻塞）
response = client.chat.completions.create(model=model, messages=messages, ...)
assistant_msg = response.choices[0].message

# 改造后（流式，参照harness PartialAccumulator.push()模式）
text_acc = ""
thinking_acc = ""
tool_calls_acc = {}

for ev in provider.stream_completion(client, model, messages, tools):
    etype = ev["type"]
    if etype == "thinking_delta":
        thinking_acc += ev["content"]
        yield {"type": "thinking_delta", "data": {"content": ev["content"]}}
    elif etype == "text_delta":
        text_acc += ev["content"]
        yield {"type": "text_delta", "data": {"content": ev["content"]}}
    elif etype == "thinking_done":
        yield {"type": "thinking_done", "data": {"content": thinking_acc}}
    elif etype == "tool_calls":
        tool_calls_acc = ev["tool_calls"]
    elif etype == "done":
        break
    elif etype == "error":
        yield {"type": "error", "data": {"error": ev.get("error", "流式响应异常")}}
        return

# thinking/text内容转为assistant_msg兼容格式，供后续工具执行逻辑复用
# ...（构造虚拟assistant_msg，复用现有工具执行、审批、并行逻辑）
```

**兼容要点**：只替换LLM调用阶段，`tool_started/tool_finished/permission_required` 等事件保持不变。现有工具执行、审批、并行逻辑完全复用。

### Phase 2：前端增量渲染

#### 2.1 chat.js — delta事件处理

参照harness ReasoningRow.tsx（27-64）和PartialAccumulator（partial.ts:23）：

```javascript
// 新增事件类型处理（在SSE onmessage回调中）
case "text_delta":
    // 参照harness PartialAccumulator: text-delta追加到当前block
    appendToCurrentMessage(ev.content);
    break;

case "thinking_delta":
    // 参照harness ReasoningRow: 流式时展开，显示latestLine
    showThinkingBubble(ev.content);
    break;

case "thinking_done":
    // 参照harness ReasoningRow: 完成后折叠，只保留firstLine摘要
    finalizeThinkingBubble();
    break;
```

#### 2.2 首token指示器

LLM开始调用时（`turn_start`后、首个delta前）显示动态省略号：

```
"正在思考." → "正在思考.." → "正在思考..."  （CSS animation，0.8s切换）
```

首token到达后自动消失，替换为实际文字流。

#### 2.3 Thinking展示（参照harness ReasoningRow.tsx）

`<think>`内容以折叠气泡展示（复用现有`.chat-thinking`样式）：

- **流式过程中**：默认展开，`data-follow-end`属性使摘要自动滚到尾部（参照ReasoningRow.tsx:42的`scrollLeft = scrollWidth - clientWidth`）
- **流式结束后**：自动折叠，只保留首行摘要
- **用户操作**：可手动展开/收起

#### 2.4 帧节流（参照harness use-throttled-visual-update.ts）

delta事件频率高时（Qwen可达50+token/s），用`requestAnimationFrame`节流：

```javascript
let pendingDeltas = [];
let rafScheduled = false;

function scheduleDeltaFlush() {
    if (rafScheduled) return;
    rafScheduled = true;
    requestAnimationFrame(() => {
        flushPendingDeltas();  // 批量追加到DOM
        rafScheduled = false;
    });
}
```

### Phase 3：不改动

- **自主模式**（`run_autonomous()`）：后台定时任务，不需要流式，保持阻塞调用
- **工具结果上下文**：现有截断逻辑（LLM回填3000字符、DB存1000字符）合理，不改
- **APScheduler管道**：不受影响

## 改动范围

| 文件 | 改动 | 参照harness |
|------|------|-------------|
| `app/agent_core/provider.py` | 新增 `stream_completion()` 流式生成器 | translate.ts + types.ts |
| `app/agent_core/loop.py` | `_iter_events` 中LLM调用改为流式，新增delta yield | partial.ts + assembler.ts |
| `static/js/chat.js` | SSE事件处理新增 text_delta/thinking_delta/thinking_done；帧节流 | ReasoningRow.tsx + partial.ts |
| `static/css/workbench.css` | 思考气泡流式样式；首token指示器动画 | ReasoningRow.tsx |
| `static/workbench.html` | 版本号更新 | - |

## 风险与降级

| 风险 | 降级方案 |
|------|----------|
| Qwen流式响应格式异常（<think>标签跨chunk拆分） | stream_completion内状态机处理跨chunk标签；异常时fallback到非流式（现有逻辑） |
| 前端delta拼接内容错乱 | 每条delta带递增seq，前端可校验（参照harness invariant.ts:36-84） |
| 首token延迟>3秒 | 超时显示"思考较久，请稍候"，不阻塞UI |
| SSE连接中断 | 现有重连机制不变；stream_completion收到[STREAM_CLOSED]时yield error |

## 验证方式

1. **体感测试**：发送"列出所有策略"，观察文字逐token流入（非整段出现）
2. **thinking测试**：发送"分析当前市场风险"，确认`<think>`过程可视且流式结束后折叠
3. **工具调用测试**：发送"查看沪深300ETF行情"，确认工具气泡正常显示
4. **帧率验证**：Chrome DevTools Performance面板，确认delta渲染不掉帧
5. **回归测试**：自主模式（20:00管道）不受影响
6. **错误恢复**：LLM超时时前端不卡死，显示错误提示
