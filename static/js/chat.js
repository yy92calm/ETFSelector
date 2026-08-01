/**
 * AI对话侧边栏逻辑
 */
const Chat = {
    sessionId: null,
    isLoading: false,
    messagesEl: null,
    inputEl: null,

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
                this.send();
            });
        });

        this.addMessage('assistant', '你好！我是ETF工作台的AI助手。我可以帮你分析市场、管理策略、检查风控、执行回测。试试下方的快捷指令，或直接输入问题。');
    },

    async send() {
        const text = this.inputEl.value.trim();
        if (!text || this.isLoading) return;

        this.inputEl.value = '';
        this.addMessage('user', text);
        this.setLoading(true);

        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, session_id: this.sessionId }),
            });
            const data = await resp.json();

            if (data.code === 200 && data.data) {
                this.sessionId = data.data.session_id;

                if (data.data.tool_calls && data.data.tool_calls.length > 0) {
                    const toolNames = data.data.tool_calls.map(t => t.tool).join(', ');
                    this.addToolInfo(toolNames);
                    this.refreshWorkbench(data.data.tool_calls);
                }

                this.addMessage('assistant', data.data.reply || '(无回复)');
            } else {
                this.addMessage('assistant', `错误: ${data.message || '请求失败'}`);
            }
        } catch (err) {
            this.addMessage('assistant', `网络错误: ${err.message}`);
        } finally {
            this.setLoading(false);
        }
    },

    refreshWorkbench(toolCalls) {
        const mutatingTools = new Set([
            'create_strategy', 'update_allocation', 'pause_strategy', 'resume_strategy',
            'add_etf_to_pool', 'run_backtest', 'run_multi_agent_analysis',
            'execute_rebalance', 'delete_strategy',
        ]);
        const hasMutation = toolCalls.some(t => mutatingTools.has(t.tool));
        if (!hasMutation) return;

        setTimeout(() => {
            if (typeof Workbench !== 'undefined') {
                if (Workbench.currentView === 'overview') Workbench.loadOverview();
                else if (Workbench.currentView === 'strategies') Workbench.loadStrategies();
                else if (Workbench.currentView === 'market') Workbench.loadMarket();
            }
        }, 500);
    },

    addToolInfo(toolNames) {
        const div = document.createElement('div');
        div.className = 'msg tool-info';
        div.innerHTML = `<span class="tool-icon">⚙</span> ${this.escapeHtml(toolNames)}`;
        this.messagesEl.appendChild(div);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    addMessage(role, content) {
        const div = document.createElement('div');
        div.className = `msg ${role}`;

        if (role === 'assistant') {
            div.innerHTML = this.renderMarkdown(content);
        } else {
            div.innerHTML = this.escapeHtml(content).replace(/\n/g, '<br>');
        }

        const time = document.createElement('div');
        time.className = 'msg-time';
        time.textContent = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
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
