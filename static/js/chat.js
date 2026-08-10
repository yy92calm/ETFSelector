/**
 * AI对话侧边栏逻辑
 */
const Chat = {
    sessionId: null,
    isLoading: false,
    streaming: false,
    messagesEl: null,
    inputEl: null,
    _toolSeq: 0,
    _toolBubbles: null,

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
            btn.addEventListener('click', () => {
                this.inputEl.value = btn.dataset.cmd;
                this.inputEl.focus();
            });
        });

        this.loadSessions();
        this.loadModels();
        this.loadAutoApproveState();
        this.addMessage('assistant', '你好！我是ETF工作台的AI助手。我可以帮你分析市场、管理策略、检查风控、执行回测。试试下方的快捷指令，或直接输入问题。');
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
        this.addMessage('assistant', '你好！我是ETF工作台的AI助手。我可以帮你分析市场、管理策略、检查风控、执行回测。试试下方的快捷指令，或直接输入问题。');
        this.loadSessions();
    },

    async selectSession(sessionId) {
        this.sessionId = sessionId;
        const panel = document.getElementById('session-panel');
        if (panel) panel.hidden = true;
        this.messagesEl.innerHTML = '';
        this.setLoading(true);
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
                            const names = m.tool_calls.map(tc => tc.function && tc.function.name).filter(Boolean).join(', ');
                            if (names) this.addToolInfo(names, m.created_at);
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
        this.addMessage('user', text);
        this.setLoading(true);

        // 只走流式接口，实时展示工具调用过程
        const streamed = await this.sendStreaming(text);
        if (!streamed) {
            this.addMessage('assistant', '流式连接失败，请刷新页面重试');
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
            this.showStopButton();
        } else if (t === 'tool_started') {
            this.setLoading(false);
            this.addToolBubble(d.seq, d.tool);
        } else if (t === 'tool_finished') {
            this.updateToolBubble(d.seq, d.status);
        } else if (t === 'permission_required') {
            this.permissionRequired(d);
        } else if (t === 'assistant_message') {
            this.setLoading(false);
            if (d.content) this.addMessage('assistant', d.content);
        } else if (t === 'turn_end') {
            if (d.session_id) this.sessionId = d.session_id;
            this.setLoading(false);
            this.hideStopButton();
            this.refreshWorkbench(d.tool_calls);
        } else if (t === 'compacted') {
            this.addToolInfo('已整理上下文（历史摘要）');
        } else if (t === 'interrupted') {
            this.setLoading(false);
            this.hideStopButton();
            this.addMessage('assistant', '已停止本次执行。');
        } else if (t === 'error') {
            this.setLoading(false);
            this.hideStopButton();
            this.addMessage('assistant', d.error || 'AI服务调用失败');
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

    addToolBubble(seq, toolName) {
        const div = document.createElement('div');
        div.className = 'msg tool-info running';
        div.dataset.seq = seq;
        div.innerHTML = `<span class="tool-spinner"></span><span class="tool-name">${this.escapeHtml(toolName)}</span><span class="tool-state">执行中</span>`;
        this._toolBubbles.set(seq, div);
        this.messagesEl.appendChild(div);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    updateToolBubble(seq, status) {
        const div = this._toolBubbles.get(seq);
        if (!div) return;
        div.classList.remove('running');
        const isErr = status === 'error';
        div.classList.add(isErr ? 'error' : 'done');
        const spinner = div.querySelector('.tool-spinner');
        if (spinner) spinner.remove();
        const stateEl = div.querySelector('.tool-state');
        if (stateEl) stateEl.textContent = isErr ? '失败' : '完成';
    },

    refreshWorkbench(toolCalls) {
        const mutatingTools = new Set([
            'create_strategy', 'update_allocation', 'pause_strategy', 'resume_strategy',
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

    toggle() {
        const sidebar = document.getElementById('wb-sidebar');
        sidebar.classList.toggle('collapsed');
    }
};

document.addEventListener('DOMContentLoaded', () => Chat.init());
