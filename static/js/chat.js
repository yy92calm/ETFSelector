/**
 * AI对话侧边栏逻辑
 */
const CHAT_VIEW_LABELS = {
    overview: '总览',
    market: '行情',
    strategies: '策略',
    sentiment: '舆情',
    tasks: '任务',
    analyses: '分析',
    research: '研究',
    rules: '规则',
};

const CHAT_SUGGESTIONS = {
    overview: [
        { mark: '市', title: '今天市场怎么样？', desc: '行情 · 情绪 · 仓位建议', cmd: '分析当前市场环境，并给出仓位建议' },
        { mark: '险', title: '检查策略风险', desc: '回撤 · 熔断 · 集中度', cmd: '检查所有策略的风控状态，并指出需要关注的风险' },
        { mark: '调', title: '看看调仓建议', desc: '待裁决 · 已采纳 · 驳回理由', cmd: '查看最近的调仓建议及采纳情况' },
        { mark: '复', title: '复盘最近决策', desc: '成败原因 · 经验沉淀', cmd: '复盘最近的自动策略决策，总结可复用的经验' },
    ],
    market: [
        { mark: '榜', title: '今天哪些 ETF 最强？', desc: '动量 · 资金流 · 板块', cmd: '分析今天涨幅和动量靠前的 ETF，并说明背后的板块线索' },
        { mark: '持', title: '检查持仓技术面', desc: '趋势 · RSI · 波动率', cmd: '检查当前策略持仓标的的技术面状态' },
        { mark: '位', title: '现在适合加仓吗？', desc: '市场状态 · 仓位信号', cmd: '结合当前市场状态和仓位信号，判断现在是否适合加仓' },
        { mark: '比', title: '对比持仓与候选', desc: '走势 · 估值 · 动量', cmd: '对比当前持仓和候选池中动量最强的 ETF' },
    ],
    strategies: [
        { mark: '健', title: '评估策略健康度', desc: '收益 · 回撤 · 运行状态', cmd: '评估所有自动策略的健康状态，并指出需要调整的策略' },
        { mark: '仓', title: '检查持仓集中度', desc: '单只 · 板块 · 相关性', cmd: '检查当前策略的持仓集中度和组合相关性' },
        { mark: '建', title: '为什么最近没换仓？', desc: '缺口 · 持有期 · 禁入', cmd: '解释最近为什么没有触发换仓，检查换仓缺口和约束条件' },
        { mark: '绩', title: '看看策略收益', desc: '区间收益 · 月目标', cmd: '汇总当前策略的收益表现和月目标完成情况' },
    ],
    sentiment: [
        { mark: '情', title: '总结最近舆情', desc: '正负面 · 热点 · 关联标的', cmd: '总结最近的市场舆情，并提示对持仓的潜在影响' },
        { mark: '险', title: '有没有负面冲击？', desc: '负面新闻 · 关联持仓', cmd: '检查最近有没有影响当前持仓的负面舆情' },
        { mark: '极', title: '情绪是否极端？', desc: '情绪指数 · 条件复核', cmd: '判断当前市场情绪是否处于极端状态，并说明是否触发复核' },
        { mark: '策', title: '舆情如何影响策略？', desc: '情绪 → 仓位建议', cmd: '结合最近舆情判断对当前策略仓位的影响' },
    ],
    tasks: [
        { mark: '管', title: '今天管道跑完了吗？', desc: '阶段状态 · 失败原因', cmd: '检查今天的每日管道执行情况，指出失败或跳过的阶段' },
        { mark: '数', title: '数据新鲜吗？', desc: '行情 · 净值 · 板块', cmd: '检查当前行情、净值和板块数据的新鲜度' },
        { mark: '查', title: '解释最近一次执行', desc: '工具调用 · 决策留痕', cmd: '解释最近一次自动执行的决策过程和工具调用' },
        { mark: '复', title: '触发一次复盘', desc: '经验 · 规则 · 提示词进化', cmd: '对当前策略触发一次复盘' },
    ],
    analyses: [
        { mark: '依', title: '这次决策依据是什么？', desc: '行情 · 规则 · 舆情', cmd: '查看最近一次 AI 决策的依据，并解释为什么这样调仓' },
        { mark: '变', title: '配置变化轨迹', desc: '历次调仓 · 权重变化', cmd: '总结最近的配置变化轨迹和每次调仓的理由' },
        { mark: '采', title: '建议采纳情况', desc: '采纳 · 驳回 · 回执', cmd: '统计最近的调仓建议采纳和驳回情况' },
        { mark: '验', title: '回测当前策略', desc: '收益 · 回撤 · 夏普', cmd: '对当前策略执行一次历史回测并总结结果' },
    ],
    research: [
        { mark: '行', title: '哪些行业性价比最高？', desc: '估值 · 盈利 · 动量', cmd: '查看当前行业性价比排名，并指出最值得关注的行业' },
        { mark: '风', title: '持仓板块逆风吗？', desc: '板块评分 · 超配低配', cmd: '分析当前持仓所在板块是否逆风' },
        { mark: '轮', title: '板块轮动全景', desc: '31 个行业 · 相对强度', cmd: '查看申万一级行业轮动全景' },
        { mark: '值', title: '哪个行业更值得配？', desc: '基本面 · 匹配 ETF', cmd: '结合行业性价比和当前持仓，判断哪个行业更值得配置' },
    ],
    rules: [
        { mark: '规', title: '当前规则建议是什么？', desc: '市场状态 → 配置', cmd: '获取当前市场状态下的规则建议配置' },
        { mark: '差', title: '和现有配置差多少？', desc: '偏离 · 权重', cmd: '对比规则建议配置和当前策略配置的差异' },
        { mark: '测', title: '规则回测表现如何？', desc: '收益 · 回撤 · 胜率', cmd: '用规则驱动模式回测当前策略' },
        { mark: '解', title: '解释这条规则', desc: '样本 · 触发条件', cmd: '解释当前规则建议的来源和样本依据' },
    ],
};

const Chat = {
    sessionId: null,
    isLoading: false,
    streaming: false,
    messagesEl: null,
    inputEl: null,
    _toolSeq: 0,
    _toolBubbles: null,
    _streamState: null,   // {thinkingEl, thinkingPre, textEl, indicatorEl, pendingDeltas, rafScheduled}

    init() {
        this.messagesEl = document.getElementById('chat-messages');
        this.inputEl = document.getElementById('chat-input');
        const sendBtn = document.getElementById('chat-send');

        sendBtn.addEventListener('click', () => this.send());
        this.inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.send();
            }
        });

        document.querySelectorAll('.quick-cmd').forEach(btn => {
            btn.addEventListener('click', () => this.sendCommand(btn.dataset.cmd));
        });
        this.messagesEl.addEventListener('click', (e) => {
            const suggestion = e.target.closest('.chat-suggestion');
            if (suggestion && !suggestion.disabled) this.sendCommand(suggestion.dataset.cmd);
        });

        this.loadSessions();
        this.loadModels();
        this.loadAutoApproveState();
        this.renderWelcome();
        this.updateAiState('online');
        this.syncLauncher();
    },

    getView() {
        return (typeof Workbench !== 'undefined' && Workbench.currentView) || 'overview';
    },

    renderWelcome() {
        if (!this.messagesEl) return;
        if (this.messagesEl.querySelector('.msg')) return;
        this.messagesEl.querySelectorAll('.chat-welcome').forEach(el => el.remove());
        const view = this.getView();
        const wrap = document.createElement('div');
        wrap.className = 'chat-welcome';
        wrap.id = 'chat-welcome';
        wrap.innerHTML = `
            <div class="chat-welcome-hero">
                <span class="ai-orb ai-orb-xl" aria-hidden="true"><i></i></span>
                <div class="chat-welcome-copy">
                    <div class="chat-welcome-kicker">AI 决策搭档</div>
                    <h4>先问一句，再动手</h4>
                    <p>我能读行情、查策略、看风控、跑复盘；涉及写操作会先向你确认。</p>
                </div>
            </div>
            <div class="chat-welcome-context" id="chat-welcome-context">当前视图 · ${this.escapeHtml(CHAT_VIEW_LABELS[view] || view)}</div>
            <div class="chat-suggestions" id="chat-suggestions">${this.suggestionHtml(view)}</div>`;
        this.messagesEl.appendChild(wrap);
    },

    suggestionHtml(view) {
        const items = CHAT_SUGGESTIONS[view] || CHAT_SUGGESTIONS.overview;
        return items.map(item => `
            <button class="chat-suggestion" data-cmd="${this.escapeAttr(item.cmd)}">
                <span class="chat-suggestion-mark">${this.escapeHtml(item.mark)}</span>
                <span class="chat-suggestion-body">
                    <b>${this.escapeHtml(item.title)}</b>
                    <small>${this.escapeHtml(item.desc)}</small>
                </span>
                <span class="chat-suggestion-arrow">→</span>
            </button>`).join('');
    },

    onViewChange(view) {
        const context = document.getElementById('chat-welcome-context');
        if (!context) return;
        context.textContent = `当前视图 · ${CHAT_VIEW_LABELS[view] || view}`;
        const list = document.getElementById('chat-suggestions');
        if (list) list.innerHTML = this.suggestionHtml(view);
    },

    sendCommand(cmd) {
        if (!cmd || this.isLoading || this.streaming) return;
        this.inputEl.value = cmd;
        this.send();
    },

    updateAiState(state, text) {
        const presence = document.getElementById('ai-presence');
        const presenceText = document.getElementById('ai-presence-text');
        const entry = document.getElementById('ai-entry');
        const entryStatus = document.getElementById('ai-entry-status');
        const busy = state === 'thinking' || state === 'tool';
        const labels = { online: '在线', thinking: '思考中', tool: '执行中', error: '异常' };
        if (presence) {
            presence.classList.toggle('is-busy', busy);
            presence.classList.toggle('is-error', state === 'error');
        }
        if (entry) {
            entry.classList.toggle('is-busy', busy);
            entry.classList.toggle('is-error', state === 'error');
        }
        if (entryStatus) entryStatus.textContent = labels[state] || '在线';
        if (presenceText) {
            presenceText.textContent = text || (state === 'online' ? '在线 · 随时待命' : labels[state] || '在线');
        }
        document.querySelectorAll('#ai-orb, #ai-fab .ai-orb').forEach(orb => {
            orb.classList.toggle('busy', busy);
        });
    },

    syncLauncher() {
        const sidebar = document.getElementById('wb-sidebar');
        const fab = document.getElementById('ai-fab');
        if (!sidebar || !fab) return;
        fab.classList.toggle('visible', sidebar.classList.contains('collapsed'));
    },

    markNews() {
        const sidebar = document.getElementById('wb-sidebar');
        if (!sidebar || !sidebar.classList.contains('collapsed')) return;
        const fab = document.getElementById('ai-fab');
        if (fab) fab.classList.add('has-news');
    },

    async loadModels() {
        const sel = document.getElementById('chat-model-select');
        if (!sel) return;
        try {
            const resp = await fetch('/api/chat/model/options');
            const data = await resp.json();
            const models = data.data && data.data.models || [];
            sel.innerHTML = '<option value="">默认模型</option>' + models.map(m =>
                `<option value="${this.escapeHtml(m)}">${this.escapeHtml(m)}</option>`
            ).join('');
        } catch (err) {
            sel.innerHTML = '<option value="">默认模型</option>';
        }
    },

    async switchModel(model) {
        if (!this.sessionId) {
            this.sessionId = null;
            return;
        }
        try {
            await fetch('/api/chat/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: this.sessionId, model }),
            });
        } catch (err) { /* 静默失败 */ }
    },

    async loadSessions() {
        try {
            const resp = await fetch('/api/chat/sessions');
            const data = await resp.json();
            const list = document.getElementById('session-list');
            if (!list) return;
            if (data.code !== 200) { list.innerHTML = '<div class="session-empty">加载失败</div>'; return; }
            const sessions = data.data.sessions || [];
            if (sessions.length === 0) {
                list.innerHTML = '<div class="session-empty">暂无历史会话</div>';
                return;
            }
            list.innerHTML = sessions.map(s => {
                const active = this.sessionId && s.session_id === this.sessionId ? ' active' : '';
                return `<div class="session-item${active}" onclick="Chat.selectSession('${s.session_id}')">
                    <div class="session-item-content">
                        <div class="session-item-title">${this.escapeHtml(s.title || '新对话')}</div>
                        <div class="session-item-time">${this.formatTime(s.updated_at)}</div>
                    </div>
                    <button class="session-delete-btn" onclick="event.stopPropagation();Chat.deleteSession('${s.session_id}')" title="删除">×</button>
                </div>`;
            }).join('');
        } catch (err) {
            const list = document.getElementById('session-list');
            if (list) list.innerHTML = '<div class="session-empty">加载失败</div>';
        }
    },

    toggleSessions() {
        const panel = document.getElementById('session-panel');
        if (!panel) return;
        panel.hidden = !panel.hidden;
        if (!panel.hidden) this.loadSessions();
    },

    async deleteSession(sessionId) {
        if (!confirm('确定删除此会话？')) return;
        try {
            const resp = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.code === 200) {
                if (this.sessionId === sessionId) this.sessionId = null;
                this.loadSessions();
            }
        } catch (err) { /* 忽略 */ }
    },

    async loadAutoApproveState() {
        try {
            const resp = await fetch('/api/chat/auto-approve');
            const data = await resp.json();
            const cb = document.getElementById('auto-approve-checkbox');
            if (cb) cb.checked = data.data && data.data.enabled;
        } catch (err) { /* 忽略 */ }
    },

    async toggleAutoApprove(enabled) {
        try {
            await fetch('/api/chat/auto-approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled }),
            });
        } catch (err) { /* 忽略 */ }
    },

    newSession() {
        this.sessionId = null;
        const panel = document.getElementById('session-panel');
        if (panel) panel.hidden = true;
        this.messagesEl.innerHTML = '';
        this.renderWelcome();
        this.updateAiState('online');
        this.loadSessions();
    },

    async selectSession(sessionId) {
        this.sessionId = sessionId;
        const panel = document.getElementById('session-panel');
        if (panel) panel.hidden = true;
        this.messagesEl.innerHTML = '';
        this.setLoading(true);
        this.updateAiState('thinking', '正在加载历史会话…');
        try {
            const resp = await fetch(`/api/chat/history?session_id=${encodeURIComponent(sessionId)}`);
            const data = await resp.json();
            if (data.code === 200 && data.data && data.data.messages) {
                data.data.messages.forEach(m => {
                    if (m.role === 'user') {
                        this.addMessage('user', m.content, m.created_at);
                    } else if (m.role === 'assistant') {
                        const isToolRound = m.tool_calls && m.tool_calls.length > 0;
                        if (isToolRound) {
                            const resultsById = {};
                            (m.tool_results || []).forEach(tr => { resultsById[tr.tool_call_id] = tr.content; });
                            m.tool_calls.forEach(tc => {
                                const name = tc.function && tc.function.name;
                                if (!name) return;
                                const div = document.createElement('div');
                                div.className = 'msg tool-info done';
                                div.innerHTML = this._toolBubbleHtml(name, tc.function.arguments, resultsById[tc.id] || '', false);
                                const stateEl = div.querySelector('.tool-state');
                                if (stateEl) stateEl.textContent = '完成';
                                this.messagesEl.appendChild(div);
                            });
                        }
                        if (m.content) this.addMessage('assistant', m.content, m.created_at);
                    }
                });
            } else {
                this.addMessage('assistant', `加载历史失败: ${data.message || '未知错误'}`);
            }
        } catch (err) {
            this.addMessage('assistant', `加载历史失败: ${err.message}`);
        } finally {
            this.setLoading(false);
            this.updateAiState('online');
            if (!this.messagesEl.querySelector('.msg')) this.renderWelcome();
            this.loadSessions();
        }
    },

    formatTime(isoStr) {
        const d = parseServerTime(isoStr);
        if (!d) return '';
        return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    },

    async send() {
        const text = this.inputEl.value.trim();
        if (!text || this.isLoading || this.streaming) return;

        this.inputEl.value = '';
        const welcome = this.messagesEl.querySelector('.chat-welcome');
        if (welcome) welcome.remove();
        this.addMessage('user', text);
        this.setLoading(true);
        this.updateAiState('thinking', '正在理解你的问题…');

        // 只走流式接口，实时展示工具调用过程
        const streamed = await this.sendStreaming(text);
        if (!streamed) {
            this.addMessage('assistant', '流式连接失败，请刷新页面重试');
            this.updateAiState('error', '连接失败，请重试');
            setTimeout(() => {
                if (!this.isLoading && !this.streaming) this.updateAiState('online');
            }, 3000);
        }

        this.setLoading(false);
        this.hideStopButton();
        this.loadSessions();
    },

    async sendStreaming(text) {
        this.streaming = true;
        this._toolSeq = 0;
        this._toolBubbles = new Map();
        try {
            const resp = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, session_id: this.sessionId }),
            });
            if (!resp.ok || !resp.body) return false;

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let idx;
                while ((idx = buffer.indexOf('\n\n')) !== -1) {
                    const frame = buffer.slice(0, idx);
                    buffer = buffer.slice(idx + 2);
                    try {
                        this.handleSSEFrame(frame);
                    } catch (err) {
                        console.error('SSE事件处理失败', err, frame);
                    }
                }
            }
            return true;
        } catch (err) {
            // 流式失败，回退到非流式接口
            return false;
        } finally {
            this.streaming = false;
            if (this._streamState) {
                this._finalizeStreamState();
                this.updateAiState('online');
            }
        }
    },

    handleSSEFrame(frame) {
        const lines = frame.split('\n');
        for (const line of lines) {
            if (!line.startsWith('data:')) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;
            let ev;
            try { ev = JSON.parse(payload); } catch (e) { continue; }
            this.dispatchEvent(ev);
        }
    },

    dispatchEvent(ev) {
        const t = ev.type;
        const d = ev.data || {};
        if (t === 'turn_start') {
            if (d.session_id) this.sessionId = d.session_id;
            this._initStreamState();
            this.showStopButton();
            this.updateAiState('thinking', '正在理解你的问题…');
        } else if (t === 'text_delta') {
            this._onTextDelta(d.content);
        } else if (t === 'thinking_delta') {
            this._onThinkingDelta(d.content);
        } else if (t === 'thinking_done') {
            this._onThinkingDone();
        } else if (t === 'tool_started') {
            this.setLoading(false);
            this._finalizeThinkingIfActive();
            this.addToolBubble(d.seq, d.tool, d.arguments);
            this.updateAiState('tool', `正在调用 ${d.tool}…`);
        } else if (t === 'tool_finished') {
            this.updateToolBubble(d.seq, d.status, d.preview);
            this.updateAiState('thinking', '正在整理工具结果…');
        } else if (t === 'thinking') {
            // 兼容非流式fallback
            this.setLoading(false);
            this.addThinkingBubble(d.content);
            this.updateAiState('thinking', '正在推理…');
        } else if (t === 'permission_required') {
            this.permissionRequired(d);
            this.updateAiState('tool', '等待你的授权…');
        } else if (t === 'assistant_message') {
            this.setLoading(false);
            // 正文已随 text_delta 流式渲染过则跳过，避免同一回复出现两个气泡
            const st = this._streamState;
            if (d.content && st && st.textEl) {
                const streamed = st.textEl.getAttribute('data-raw') || '';
                if (streamed.includes(d.content)) return;
            }
            if (d.content) this.addMessage('assistant', d.content);
        } else if (t === 'turn_end') {
            if (d.session_id) this.sessionId = d.session_id;
            this._finalizeStreamState();
            this.setLoading(false);
            this.hideStopButton();
            this.refreshWorkbench(d.tool_calls);
            this.updateAiState('online');
            this.markNews();
        } else if (t === 'compacted') {
            this.addToolInfo('已整理上下文（历史摘要）');
        } else if (t === 'interrupted') {
            this._finalizeStreamState();
            this.setLoading(false);
            this.hideStopButton();
            this.addMessage('assistant', '已停止本次执行。');
            this.updateAiState('online');
        } else if (t === 'error') {
            this._finalizeStreamState();
            this.setLoading(false);
            this.hideStopButton();
            this.addMessage('assistant', d.error || 'AI服务调用失败');
            this.updateAiState('error', '刚才的请求出错了');
            setTimeout(() => {
                if (!this.isLoading && !this.streaming) this.updateAiState('online');
            }, 3000);
        }
    },

    permissionRequired(d) {
        const bubble = this._toolBubbles.get(d.seq);
        if (!bubble) return;
        bubble.classList.add('approval');
        bubble.classList.remove('running');
        const spinner = bubble.querySelector('.tool-spinner');
        if (spinner) spinner.remove();
        const stateEl = bubble.querySelector('.tool-state');
        if (stateEl) stateEl.textContent = '等待授权';

        const actions = document.createElement('div');
        actions.className = 'approval-actions';
        const reqId = d.request_id;
        const mk = (label, outcome) => {
            const b = document.createElement('button');
            b.className = 'appr-btn';
            b.textContent = label;
            b.addEventListener('click', () => this.submitApproval(reqId, outcome, actions, bubble));
            return b;
        };
        actions.appendChild(mk('允许本次', 'once'));
        actions.appendChild(mk('本会话允许', 'always'));
        actions.appendChild(mk('拒绝', 'deny'));
        bubble.appendChild(actions);
    },

    async submitApproval(requestId, outcome, actionsEl, bubble) {
        try {
            await fetch('/api/chat/approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ request_id: requestId, outcome }),
            });
        } catch (err) { /* 后端超时会自动拒绝，忽略 */ }
        if (actionsEl) {
            actionsEl.querySelectorAll('.appr-btn').forEach(b => { b.disabled = true; });
        }
        const stateEl = bubble.querySelector('.tool-state');
        if (stateEl) stateEl.textContent = outcome === 'deny' ? '已拒绝' : '已批准';
    },

    showStopButton() {
        let btn = document.getElementById('chat-stop');
        if (!btn) {
            btn = document.createElement('button');
            btn.id = 'chat-stop';
            btn.className = 'chat-stop-btn';
            btn.textContent = '停止';
            btn.addEventListener('click', () => this.stopStreaming());
            const sendBtn = document.getElementById('chat-send');
            if (sendBtn && sendBtn.parentElement) {
                sendBtn.parentElement.insertBefore(btn, sendBtn);
            } else {
                const area = document.querySelector('.chat-input-area');
                if (area) area.appendChild(btn);
            }
        }
        btn.hidden = false;
        btn.disabled = false;
        btn.textContent = '停止';
    },

    hideStopButton() {
        const btn = document.getElementById('chat-stop');
        if (btn) { btn.hidden = true; btn.disabled = false; btn.textContent = '停止'; }
    },

    async stopStreaming() {
        const btn = document.getElementById('chat-stop');
        if (btn) { btn.disabled = true; btn.textContent = '已停止'; }
        if (this.sessionId) {
            try {
                await fetch('/api/chat/stop', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: this.sessionId }),
                });
            } catch (err) { /* 忽略 */ }
        }
    },

    _toolBubbleHtml(toolName, argsStr, resultStr, running) {
        const head = running
            ? '<span class="tool-spinner"></span>'
            : '';
        const argsTxt = argsStr || '{}';
        const resTxt = resultStr !== null && resultStr !== undefined
            ? resultStr
            : '（等待执行结果…）';
        return head +
            `<span class="tool-name">${this.escapeHtml(toolName)}</span>` +
            `<span class="tool-state">${running ? '执行中' : ''}</span>` +
            `<details class="tool-detail"><summary>参数与结果</summary>` +
            `<div class="tool-sec">参数</div><pre class="tool-pre">${this.escapeHtml(argsTxt)}</pre>` +
            `<div class="tool-sec">结果</div><pre class="tool-pre tool-result">${this.escapeHtml(resTxt)}</pre>` +
            `</details>`;
    },

    addThinkingBubble(content) {
        const div = document.createElement('div');
        div.className = 'msg tool-info thinking';
        div.innerHTML = `<details class="tool-detail"><summary>💭 模型思考</summary>` +
            `<pre class="tool-pre">${this.escapeHtml(content)}</pre></details>`;
        this.messagesEl.appendChild(div);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    addToolBubble(seq, toolName, arguments) {
        const div = document.createElement('div');
        div.className = 'msg tool-info running';
        div.dataset.seq = seq;
        let argsStr = '';
        try { argsStr = JSON.stringify(arguments, null, 1); } catch (e) { argsStr = String(arguments || ''); }
        div.innerHTML = this._toolBubbleHtml(toolName, argsStr, null, true);
        this._toolBubbles.set(seq, div);
        this.messagesEl.appendChild(div);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    updateToolBubble(seq, status, preview) {
        const div = this._toolBubbles.get(seq);
        if (!div) return;
        div.classList.remove('running');
        const isErr = status === 'error';
        div.classList.add(isErr ? 'error' : 'done');
        const spinner = div.querySelector('.tool-spinner');
        if (spinner) spinner.remove();
        const stateEl = div.querySelector('.tool-state');
        if (stateEl) stateEl.textContent = isErr ? '失败' : '完成';
        const resEl = div.querySelector('.tool-result');
        if (resEl && preview != null) resEl.textContent = preview;
    },

    _finalizeStreamState() {
        const st = this._streamState;
        if (!st) return;
        this._removeThinkingIndicator();
        this._finalizeThinkingIfActive();
        // 收尾前强制刷出未消费的增量，避免最后一帧随 turn_end 一起丢失
        this._flushTextDeltas();
        // 移除textEl的streaming光标
        if (st.textEl) st.textEl.classList.remove('streaming');
        this._streamState = null;
    },

    refreshWorkbench(toolCalls) {
        const mutatingTools = new Set([
            'create_strategy', 'pause_strategy', 'resume_strategy',
            'add_etf_to_pool', 'run_backtest', 'run_multi_agent_analysis',
            'execute_rebalance', 'delete_strategy',
        ]);
        const hasMutation = (toolCalls || []).some(t => mutatingTools.has(t.tool));
        if (!hasMutation) return;

        setTimeout(() => {
            if (typeof Workbench !== 'undefined') {
                if (Workbench.currentView === 'overview') Workbench.loadOverview();
                else if (Workbench.currentView === 'strategies') Workbench.loadStrategies();
                else if (Workbench.currentView === 'market') Workbench.loadMarket();
            }
        }, 500);
    },

    addToolInfo(toolNames, createdAt) {
        const div = document.createElement('div');
        div.className = 'msg tool-info';
        div.innerHTML = `<span class="tool-icon">⚙</span> ${this.escapeHtml(toolNames)}`;
        if (createdAt) {
            const time = document.createElement('div');
            time.className = 'msg-time';
            const d = parseServerTime(createdAt);
            time.textContent = d ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
            div.appendChild(time);
        }
        this.messagesEl.appendChild(div);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    // --- 流式增量渲染（参照deepseek-harness PartialAccumulator模式） ---

    _initStreamState() {
        this.setLoading(false);
        this._streamState = {
            thinkingEl: null,
            thinkingPre: null,
            textEl: null,
            indicatorEl: null,
            pendingDeltas: [],
            rafScheduled: false,
            finalized: false,
        };
        // 显示首token指示器
        this._showThinkingIndicator();
    },

    _showThinkingIndicator() {
        const st = this._streamState;
        if (!st || st.indicatorEl) return;
        const div = document.createElement('div');
        div.className = 'msg assistant';
        div.innerHTML = '<span class="chat-thinking-indicator"></span>';
        this.messagesEl.appendChild(div);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
        st.indicatorEl = div;
    },

    _removeThinkingIndicator() {
        const st = this._streamState;
        if (!st || !st.indicatorEl) return;
        st.indicatorEl.remove();
        st.indicatorEl = null;
    },

    _onThinkingDelta(content) {
        const st = this._streamState;
        if (!st) return;
        this._removeThinkingIndicator();
        // 首次：创建thinking气泡
        if (!st.thinkingEl) {
            const div = document.createElement('div');
            div.className = 'msg tool-info thinking streaming';
            div.innerHTML = '<details class="tool-detail" open><summary class="thinking-summary">模型思考</summary>'
                + '<pre class="thinking-body"></pre></details>';
            this.messagesEl.appendChild(div);
            st.thinkingEl = div;
            st.thinkingPre = div.querySelector('.thinking-body');
            this.updateAiState('thinking', '正在推理…');
        }
        st.thinkingPre.textContent += content;
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    _onThinkingDone() {
        const st = this._streamState;
        if (!st || !st.thinkingEl) return;
        st.thinkingEl.classList.remove('streaming');
        // 折叠：只保留首行摘要
        const fullText = st.thinkingPre.textContent;
        const firstLine = (fullText.split('\n')[0] || '').slice(0, 80);
        const summaryEl = st.thinkingEl.querySelector('.thinking-summary');
        if (summaryEl) summaryEl.textContent = firstLine ? '模型思考: ' + firstLine + '...' : '模型思考';
        const details = st.thinkingEl.querySelector('details');
        if (details) details.removeAttribute('open');
    },

    _finalizeThinkingIfActive() {
        const st = this._streamState;
        if (!st || st.finalized || !st.thinkingEl) return;
        st.finalized = true;
        this._onThinkingDone();
    },

    _onTextDelta(content) {
        const st = this._streamState;
        if (!st) return;
        this._removeThinkingIndicator();
        this._finalizeThinkingIfActive();
        // 首次：创建assistant气泡
        if (!st.textEl) {
            const div = document.createElement('div');
            div.className = 'msg assistant streaming';
            div.innerHTML = '';
            this.messagesEl.appendChild(div);
            st.textEl = div;
            this.updateAiState('thinking', '正在生成回答…');
        }
        // 帧节流：累积delta，requestAnimationFrame批量flush
        st.pendingDeltas.push(content);
        if (!st.rafScheduled) {
            st.rafScheduled = true;
            requestAnimationFrame(() => {
                this._flushTextDeltas();
                st.rafScheduled = false;
            });
        }
    },

    _flushTextDeltas() {
        const st = this._streamState;
        if (!st || !st.textEl || st.pendingDeltas.length === 0) return;
        const chunk = st.pendingDeltas.join('');
        st.pendingDeltas.length = 0;
        // 增量追加：用textContent避免XSS，再渲染markdown
        const current = st.textEl.getAttribute('data-raw') || '';
        const updated = current + chunk;
        st.textEl.setAttribute('data-raw', updated);
        st.textEl.innerHTML = this.renderMarkdown(updated);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    addMessage(role, content, createdAt) {
        const div = document.createElement('div');
        div.className = `msg ${role}`;

        if (role === 'assistant') {
            div.innerHTML = this.renderMarkdown(content);
        } else {
            div.innerHTML = this.escapeHtml(content).replace(/\n/g, '<br>');
        }

        const time = document.createElement('div');
        time.className = 'msg-time';
        if (createdAt) {
            const d = parseServerTime(createdAt);
            time.textContent = d ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
        } else {
            time.textContent = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        }
        div.appendChild(time);

        this.messagesEl.appendChild(div);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    renderMarkdown(text) {
        if (!text) return '';
        let html = this.escapeHtml(text);

        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
            return `<pre class="md-code-block"><code>${code.trim()}</code></pre>`;
        });

        html = html.replace(/`([^`]+)`/g, '<code class="md-code-inline">$1</code>');

        html = html.replace(/^### (.+)$/gm, '<div class="md-h3">$1</div>');
        html = html.replace(/^## (.+)$/gm, '<div class="md-h2">$1</div>');
        html = html.replace(/^# (.+)$/gm, '<div class="md-h1">$1</div>');

        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

        html = html.replace(/^\- (.+)$/gm, '<div class="md-li">• $1</div>');
        html = html.replace(/^\d+\. (.+)$/gm, '<div class="md-li md-li-num">$1</div>');

        html = html.replace(/^\|(.+)\|$/gm, (match) => {
            const cells = match.split('|').filter(c => c.trim()).map(c => c.trim());
            if (cells.every(c => /^[-:]+$/.test(c))) return '';
            return '<div class="md-tr">' + cells.map(c => `<span class="md-td">${c}</span>`).join('') + '</div>';
        });

        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="md-link">$1</a>');

        html = html.replace(/^---$/gm, '<hr class="md-hr">');

        html = html.replace(/\n{2,}/g, '<br><br>');
        html = html.replace(/\n/g, '<br>');

        return html;
    },

    setLoading(loading) {
        this.isLoading = loading;
        const sendBtn = document.getElementById('chat-send');
        sendBtn.disabled = loading;
        document.querySelectorAll('.chat-suggestion, .quick-cmd').forEach(btn => {
            btn.disabled = loading;
        });

        const existing = document.getElementById('typing-indicator');
        if (loading && !existing) {
            const div = document.createElement('div');
            div.id = 'typing-indicator';
            div.className = 'msg assistant';
            div.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
            this.messagesEl.appendChild(div);
            this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
        } else if (!loading && existing) {
            existing.remove();
        }
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    escapeAttr(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    },

    toggle() {
        const sidebar = document.getElementById('wb-sidebar');
        if (!sidebar) return;
        sidebar.classList.toggle('collapsed');
        this.syncLauncher();
        if (!sidebar.classList.contains('collapsed')) {
            const fab = document.getElementById('ai-fab');
            if (fab) fab.classList.remove('has-news');
            setTimeout(() => {
                if (this.inputEl) this.inputEl.focus();
            }, 320);
        }
    }
};

document.addEventListener('DOMContentLoaded', () => Chat.init());
