/* ====== ETF量化选择系统 - 前端逻辑 ====== */

const API = '';

// ---- 工具函数 ----
async function api(path, options = {}) {
    const resp = await fetch(API + path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || '请求失败');
    return data;
}

function toast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

function fmtNum(n, d = 2) {
    if (n == null) return '-';
    return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtPct(n) {
    if (n == null) return '-';
    const cls = n >= 0 ? 'text-success' : 'text-danger';
    return `<span class="${cls}">${n >= 0 ? '+' : ''}${fmtNum(n)}%</span>`;
}

function fmtAmount(n) {
    if (n == null) return '-';
    if (n >= 1e8) return fmtNum(n / 1e8) + '亿';
    if (n >= 1e4) return fmtNum(n / 1e4) + '万';
    return fmtNum(n);
}

// ---- Tab 切换 ----
document.querySelectorAll('.nav-links a').forEach(a => {
    a.addEventListener('click', (e) => {
        e.preventDefault();
        document.querySelectorAll('.nav-links a').forEach(x => x.classList.remove('active'));
        a.classList.add('active');
        const tab = a.dataset.tab;
        document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
        document.getElementById('tab-' + tab).classList.add('active');

        if (tab === 'market') loadMarket();
        if (tab === 'strategy') loadStrategies();
        if (tab === 'backtest') loadBacktestPage();
        if (tab === 'portfolio') loadPortfolioPage();
    });
});

// ---- Modal ----
function openModal(id) { document.getElementById(id).classList.add('active'); }
function closeModal(id) { document.getElementById(id).classList.remove('active'); }

// ==================================================================
//  行情看板
// ==================================================================
let currentMarket = 'all';
let currentPage = 1;
let allQuotes = [];
let filteredQuotes = [];

// 初始化市场标签点击事件
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.market-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.market-tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            currentMarket = this.dataset.market;
            currentPage = 1;
            filterAndDisplayQuotes();
        });
    });
});

async function loadMarket() {
    try {
        // 获取更多数据用于分类展示
        const res = await api('/api/etf/overview?limit=500');
        allQuotes = res.data?.quotes || [];
        
        if (allQuotes.length) {
            document.getElementById('market-stats').style.display = 'flex';
            updateMarketStats(allQuotes);
        }
        
        if (!allQuotes.length) {
            const tbody = document.getElementById('market-table');
            const empty = document.getElementById('market-empty');
            tbody.innerHTML = '';
            empty.style.display = 'block';
            document.getElementById('market-count').textContent = '';
            document.getElementById('market-stats').style.display = 'none';
            return;
        }

        filterAndDisplayQuotes();
    } catch (e) {
        console.error(e);
    }
}

function filterQuotesByMarket(quotes, market) {
    if (market === 'all') return quotes;
    if (market === 'hot') {
        // 热门ETF：成交额前30（有行情的ETF）
        return quotes.filter(q => q.has_quote || q.close_price !== null).slice(0, 30);
    }
    
    return quotes.filter(q => {
        const code = q.etf_code;
        // ETF代码规则：
        // 51xxxx - 上交所ETF（如510300沪深300ETF）
        // 58xxxx - 科创板ETF（如588000科创50ETF）
        // 159xxx - 深交所ETF/创业板ETF（如159915创业板ETF）
        // 15xxxx, 16xxxx, 18xxxx - 深交所其他ETF
        switch(market) {
            case 'sh':
                // 上交所：51开头 或 58开头（58也是上交所的科创板）
                return code.startsWith('51');
            case 'sz':
                // 深交所：15、16、18开头（不含159，159单独算创业板）
                return code.startsWith('15') && !code.startsWith('159') || 
                       code.startsWith('16') || 
                       code.startsWith('18');
            case 'cy':
                // 创业板ETF：159开头
                return code.startsWith('159');
            case 'kc':
                // 科创板ETF：58、588、589开头
                return code.startsWith('58');
            default:
                return true;
        }
    });
}

function filterAndDisplayQuotes() {
    filteredQuotes = filterQuotesByMarket(allQuotes, currentMarket);
    const totalCount = filteredQuotes.length;
    const perPage = 50;
    const totalPages = Math.ceil(totalCount / perPage);
    
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;
    
    const startIndex = (currentPage - 1) * perPage;
    const endIndex = startIndex + perPage;
    const showQuotes = filteredQuotes.slice(startIndex, endIndex);
    
    const tbody = document.getElementById('market-table');
    const empty = document.getElementById('market-empty');
    const title = document.getElementById('market-title');
    
    if (!showQuotes.length) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        document.getElementById('market-count').textContent = '';
        document.getElementById('market-pagination').style.display = 'none';
        title.textContent = getMarketTitle(currentMarket);
        return;
    }

    empty.style.display = 'none';
    document.getElementById('market-count').textContent = `共 ${totalCount} 条，当前显示 ${startIndex + 1}-${Math.min(endIndex, totalCount)} 条`;
    title.textContent = getMarketTitle(currentMarket);
    
    tbody.innerHTML = showQuotes.map(q => {
        const hasQuote = q.has_quote || (q.close_price !== null && q.close_price !== undefined);
        return `
        <tr>
            <td><strong>${q.etf_code}</strong></td>
            <td>${q.etf_name}</td>
            <td class="text-right">${hasQuote ? fmtNum(q.close_price, 3) : '-'}</td>
            <td class="text-right">${hasQuote ? fmtPct(q.change_pct) : '-'}</td>
            <td class="text-right">${hasQuote ? fmtAmount(q.volume) : '-'}</td>
            <td class="text-right">${hasQuote ? fmtAmount(q.amount) : '-'}</td>
            <td>${hasQuote ? q.trade_date : '-'}</td>
            <td><button class="btn btn-outline btn-sm" onclick="fetchETFData('${q.etf_code}')">拉取历史</button></td>
        </tr>
    `}).join('');
    
    // 更新分页控件
    const pagination = document.getElementById('market-pagination');
    if (totalCount > perPage) {
        pagination.style.display = 'flex';
        document.getElementById('current-page').textContent = currentPage;
        document.getElementById('total-pages').textContent = totalPages;
    } else {
        pagination.style.display = 'none';
    }
}

function getMarketTitle(market) {
    const titles = {
        'all': '全市场ETF行情',
        'sh': '上交所ETF行情',
        'sz': '深交所ETF行情', 
        'cy': '创业板ETF行情',
        'kc': '科创板ETF行情',
        'hot': '热门ETF（按成交额排序）'
    };
    return titles[market] || 'ETF行情';
}

function updateMarketStats(quotes) {
    let upCount = 0, downCount = 0, flatCount = 0;
    let totalValue = 0;
    
    quotes.forEach(q => {
        totalValue += q.amount || 0;
        if (q.change_pct > 0) upCount++;
        else if (q.change_pct < 0) downCount++;
        else flatCount++;
    });
    
    document.getElementById('total-market-value').textContent = fmtAmount(totalValue);
    document.getElementById('up-count').textContent = upCount;
    document.getElementById('down-count').textContent = downCount;
    document.getElementById('flat-count').textContent = flatCount;
}

function changePage(direction) {
    const totalPages = Math.ceil(filteredQuotes.length / 50);
    currentPage += direction;
    if (currentPage < 1) currentPage = 1;
    if (currentPage > totalPages) currentPage = totalPages;
    filterAndDisplayQuotes();
}

async function syncETFList() {
    toast('正在同步ETF列表...', 'info');
    try {
        const res = await api('/api/etf/sync-list', { method: 'POST' });
        toast(res.message, 'success');
        loadMarket();
    } catch (e) { toast(e.message, 'error'); }
}

async function updateTodayQuotes() {
    toast('正在更新行情（可能需要几分钟）...', 'info');
    try {
        const res = await api('/api/etf/update-today', { method: 'POST' });
        toast(`更新完成: 成功 ${res.data.success_count}, 失败 ${res.data.fail_count}`, 'success');
        loadMarket();
    } catch (e) { toast(e.message, 'error'); }
}

async function fetchETFData(code) {
    toast(`正在拉取 ${code} 历史数据...`, 'info');
    try {
        const res = await api(`/api/etf/fetch/${code}?start_date=20200101`, { method: 'POST' });
        toast(`${code}: 新增 ${res.data.new_records} 条`, 'success');
    } catch (e) { toast(e.message, 'error'); }
}

// ==================================================================
//  策略管理
// ==================================================================
let templates = [];

async function loadTemplates() {
    try {
        const res = await api('/api/strategy/templates');
        templates = res.data?.templates || [];
    } catch (e) { console.error(e); }
}

async function loadStrategies() {
    try {
        const res = await api('/api/strategy/list');
        const strategies = res.data?.strategies || [];
        const tbody = document.getElementById('strategy-table');
        const empty = document.getElementById('strategy-empty');

        if (!strategies.length) {
            tbody.innerHTML = '';
            empty.style.display = 'block';
            return;
        }
        empty.style.display = 'none';

        tbody.innerHTML = strategies.map(s => `
            <tr>
                <td>${s.id}</td>
                <td><strong>${s.name}</strong></td>
                <td><span class="badge ${s.strategy_type === 'template' ? 'badge-template' : 'badge-ai'}">${s.strategy_type === 'template' ? '模板' : 'AI'}</span></td>
                <td>${(s.etf_codes || []).join(', ')}</td>
                <td class="text-right">${fmtNum(s.initial_capital, 0)}</td>
                <td><span class="badge badge-${s.status}">${s.status}</span></td>
                <td>${s.created_at ? s.created_at.slice(0, 10) : '-'}</td>
                <td>
                    <button class="btn btn-outline btn-sm" onclick="deleteStrategy(${s.id})">删除</button>
                </td>
            </tr>
        `).join('');
    } catch (e) { console.error(e); }
}

function showCreateStrategyModal() {
    const sel = document.getElementById('cs-template');
    sel.innerHTML = templates.map(t => `<option value="${t.template_name}">${t.template_name} - ${t.description}</option>`).join('');
    renderTemplateParams();
    sel.onchange = renderTemplateParams;
    openModal('modal-create-strategy');
}

function renderTemplateParams() {
    const name = document.getElementById('cs-template').value;
    const tpl = templates.find(t => t.template_name === name);
    const area = document.getElementById('cs-params-area');
    if (!tpl) { area.innerHTML = ''; return; }

    area.innerHTML = '<div class="form-row">' + Object.entries(tpl.default_params).map(([k, v]) => `
        <div class="form-group">
            <label>${k}</label>
            <input type="number" step="any" class="tpl-param" data-key="${k}" value="${v}">
        </div>
    `).join('') + '</div>';
}

async function submitCreateStrategy() {
    const params = {};
    document.querySelectorAll('.tpl-param').forEach(el => {
        params[el.dataset.key] = Number(el.value);
    });

    const body = {
        name: document.getElementById('cs-name').value,
        strategy_type: 'template',
        template_name: document.getElementById('cs-template').value,
        params,
        etf_codes: document.getElementById('cs-etfs').value.split(',').map(s => s.trim()).filter(Boolean),
        initial_capital: Number(document.getElementById('cs-capital').value),
    };

    try {
        const res = await api('/api/strategy/create', { method: 'POST', body: JSON.stringify(body) });
        toast(res.message, 'success');
        closeModal('modal-create-strategy');
        loadStrategies();
    } catch (e) { toast(e.message, 'error'); }
}

function showAIStrategyModal() {
    openModal('modal-ai-strategy');
}

async function submitAIStrategy() {
    const btn = document.getElementById('ai-submit-btn');
    btn.disabled = true;
    btn.textContent = '生成中...';

    const body = {
        description: document.getElementById('ai-desc').value,
        etf_codes: document.getElementById('ai-etfs').value.split(',').map(s => s.trim()).filter(Boolean),
        initial_capital: Number(document.getElementById('ai-capital').value),
    };

    try {
        const res = await api('/api/strategy/create-ai', { method: 'POST', body: JSON.stringify(body) });
        toast(res.message, 'success');
        closeModal('modal-ai-strategy');
        loadStrategies();
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '生成策略';
    }
}

async function deleteStrategy(id) {
    if (!confirm('确定删除该策略？')) return;
    try {
        await api(`/api/strategy/${id}`, { method: 'DELETE' });
        toast('已删除', 'success');
        loadStrategies();
    } catch (e) { toast(e.message, 'error'); }
}

// ==================================================================
//  回测
// ==================================================================
async function loadBacktestPage() {
    // 设置默认结束日期
    document.getElementById('bt-end').value = new Date().toISOString().slice(0, 10);

    try {
        const res = await api('/api/strategy/list');
        const strategies = res.data?.strategies || [];
        const sel = document.getElementById('bt-strategy');
        sel.innerHTML = strategies.map(s => `<option value="${s.id}">${s.name} (${s.strategy_type})</option>`).join('');
    } catch (e) { console.error(e); }
}

async function runBacktest() {
    const btn = document.getElementById('bt-run-btn');
    btn.disabled = true;
    btn.textContent = '回测中...';

    const body = {
        strategy_id: Number(document.getElementById('bt-strategy').value),
        start_date: document.getElementById('bt-start').value,
        end_date: document.getElementById('bt-end').value,
        initial_capital: Number(document.getElementById('bt-capital').value),
    };

    try {
        const res = await api('/api/backtest/run', { method: 'POST', body: JSON.stringify(body) });
        const d = res.data;
        document.getElementById('bt-result').style.display = 'block';

        // 统计卡片
        document.getElementById('bt-stats').innerHTML = [
            { label: '最终资产', value: '¥' + fmtNum(d.final_asset), color: '' },
            { label: '总收益率', value: fmtNum(d.total_return_pct) + '%', color: d.total_return_pct >= 0 ? 'var(--success)' : 'var(--danger)' },
            { label: '最大回撤', value: fmtNum(d.max_drawdown_pct) + '%', color: 'var(--danger)' },
            { label: 'Sharpe', value: d.sharpe_ratio != null ? fmtNum(d.sharpe_ratio) : '-', color: '' },
            { label: '交易次数', value: d.trade_count, color: '' },
            { label: '胜率', value: d.win_rate != null ? fmtNum(d.win_rate) + '%' : '-', color: '' },
        ].map(s => `<div class="stat-card"><div class="stat-value" style="color:${s.color || 'inherit'}">${s.value}</div><div class="stat-label">${s.label}</div></div>`).join('');

        // 收益曲线
        renderBacktestChart(d.daily_data, d.initial_capital);

        // 交易记录
        document.getElementById('bt-trades').innerHTML = d.trades.map(t => `
            <tr>
                <td>${t.date}</td>
                <td>${t.etf_code}</td>
                <td><span class="${t.direction === 'buy' ? 'text-danger' : 'text-success'}">${t.direction === 'buy' ? '买入' : '卖出'}</span></td>
                <td class="text-right">${fmtNum(t.price, 3)}</td>
                <td class="text-right">${t.quantity}</td>
                <td class="text-right">${fmtNum(t.amount)}</td>
                <td>${t.reason}</td>
            </tr>
        `).join('');

        toast('回测完成', 'success');
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '开始回测';
    }
}

function renderBacktestChart(dailyData, initialCapital) {
    const chart = echarts.init(document.getElementById('bt-chart'));
    const dates = dailyData.map(d => d.date);
    const assets = dailyData.map(d => d.total_asset);
    const returns = dailyData.map(d => d.profit_pct);

    chart.setOption({
        tooltip: {
            trigger: 'axis',
            formatter: params => {
                const d = params[0];
                return `${d.axisValue}<br/>总资产: ¥${fmtNum(d.value)}<br/>收益率: ${fmtNum(returns[d.dataIndex])}%`;
            }
        },
        grid: { left: '8%', right: '4%', top: '10%', bottom: '12%' },
        xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
        yAxis: [
            { type: 'value', name: '资产(¥)', axisLabel: { formatter: v => v >= 1e4 ? (v / 1e4).toFixed(0) + '万' : v } },
        ],
        series: [
            {
                name: '总资产',
                type: 'line',
                data: assets,
                smooth: true,
                lineStyle: { width: 2, color: '#3b82f6' },
                areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.25)' }, { offset: 1, color: 'rgba(59,130,246,0.02)' }] } },
                markLine: {
                    silent: true,
                    data: [{ yAxis: initialCapital, label: { formatter: '初始资金' }, lineStyle: { color: '#94a3b8', type: 'dashed' } }]
                },
            }
        ],
    });

    window.addEventListener('resize', () => chart.resize());
}

// ==================================================================
//  实盘模拟
// ==================================================================
async function loadPortfolioPage() {
    try {
        const res = await api('/api/strategy/list');
        const strategies = res.data?.strategies || [];
        const sel = document.getElementById('pf-strategy');
        sel.innerHTML = '<option value="">-- 请选择策略 --</option>' +
            strategies.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    } catch (e) { console.error(e); }
}

async function loadPortfolio() {
    const sid = document.getElementById('pf-strategy').value;
    if (!sid) { document.getElementById('pf-content').style.display = 'none'; return; }

    document.getElementById('pf-content').style.display = 'block';

    try {
        const [histRes, holdRes, tradeRes] = await Promise.all([
            api(`/api/portfolio/${sid}/history`),
            api(`/api/portfolio/${sid}/holdings`),
            api(`/api/portfolio/${sid}/trades`),
        ]);

        const snapshots = histRes.data?.snapshots || [];
        const holdings = holdRes.data?.holdings || [];
        const trades = tradeRes.data?.trades || [];

        // 统计
        if (snapshots.length) {
            const latest = snapshots[snapshots.length - 1];
            document.getElementById('pf-stats').innerHTML = [
                { label: '总资产', value: '¥' + fmtNum(latest.total_asset) },
                { label: '累计收益率', value: fmtNum(latest.profit_pct) + '%', color: latest.profit_pct >= 0 ? 'var(--success)' : 'var(--danger)' },
                { label: '可用现金', value: '¥' + fmtNum(latest.cash) },
                { label: '持仓市值', value: '¥' + fmtNum(latest.market_value) },
            ].map(s => `<div class="stat-card"><div class="stat-value" style="color:${s.color || 'inherit'}">${s.value}</div><div class="stat-label">${s.label}</div></div>`).join('');

            document.getElementById('pf-info').textContent = `数据截至 ${latest.trade_date}，共 ${snapshots.length} 个交易日`;
        } else {
            document.getElementById('pf-stats').innerHTML = '<div class="empty-state">暂无资产数据，请先"补跑策略"</div>';
            document.getElementById('pf-info').textContent = '';
        }

        // 资产曲线
        if (snapshots.length) {
            const chart = echarts.init(document.getElementById('pf-chart'));
            chart.setOption({
                tooltip: { trigger: 'axis' },
                grid: { left: '8%', right: '4%', top: '10%', bottom: '12%' },
                xAxis: { type: 'category', data: snapshots.map(s => s.trade_date) },
                yAxis: { type: 'value', name: '资产(¥)' },
                series: [{
                    type: 'line', data: snapshots.map(s => s.total_asset), smooth: true,
                    lineStyle: { width: 2, color: '#10b981' },
                    areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(16,185,129,0.25)' }, { offset: 1, color: 'rgba(16,185,129,0.02)' }] } },
                }],
            });
            window.addEventListener('resize', () => chart.resize());
        }

        // 持仓
        document.getElementById('pf-holdings').innerHTML = holdings.length
            ? holdings.map(h => `<tr><td>${h.etf_code}</td><td class="text-right">${h.quantity}</td><td class="text-right">${fmtNum(h.avg_cost, 3)}</td><td class="text-right">${fmtNum(h.current_price, 3)}</td><td class="text-right">${fmtNum(h.market_value)}</td></tr>`).join('')
            : '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary)">空仓</td></tr>';

        // 交易
        document.getElementById('pf-trades').innerHTML = trades.length
            ? trades.map(t => `<tr><td>${t.trade_date}</td><td>${t.etf_code}</td><td class="${t.direction === 'buy' ? 'text-danger' : 'text-success'}">${t.direction === 'buy' ? '买入' : '卖出'}</td><td class="text-right">${fmtNum(t.price, 3)}</td><td class="text-right">${t.quantity}</td><td class="text-right">${fmtNum(t.amount)}</td><td>${t.reason || ''}</td></tr>`).join('')
            : '<tr><td colspan="7" style="text-align:center;color:var(--text-secondary)">暂无交易</td></tr>';

    } catch (e) {
        toast(e.message, 'error');
    }
}

async function catchUpStrategy() {
    const sid = document.getElementById('pf-strategy').value;
    if (!sid) return;
    toast('正在补跑策略（可能需要一些时间）...', 'info');
    try {
        const res = await api(`/api/portfolio/${sid}/catch-up`, { method: 'POST' });
        toast(res.message, 'success');
        loadPortfolio();
    } catch (e) { toast(e.message, 'error'); }
}

// ---- 初始化 ----
(async function init() {
    await loadTemplates();
    loadMarket();
})();
