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
    // A股习惯：上涨红色，下跌绿色
    const cls = n >= 0 ? 'text-danger' : 'text-success';
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
let currentSort = { field: 'amount', order: 'desc' }; // 默认按成交额降序
let itemsPerPage = 50; // 默认每页显示数量

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
        // 获取所有ETF数据用于分类展示
        const res = await api('/api/etf/overview?limit=2000');
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
        // ETF代码规则（2024年最新）：
        // 上交所ETF：51xxxx、56xxxx（传统+新型ETF）
        // 科创板ETF：58xxxx（如588000科创50ETF）
        // 创业板ETF：159xxx（如159915创业板ETF，属于深交所）
        // 深交所主板ETF：15xxxx（除159外，目前实际较少）
        // 港股通/跨境ETF：52xxxx
        switch(market) {
            case 'sh':
                // 上交所：51、52、53、56开头（不含58科创板）
                return code.startsWith('51') || 
                       code.startsWith('52') || 
                       code.startsWith('53') || 
                       code.startsWith('56');
            case 'sz':
                // 深交所主板：15开头（不含159创业板）
                return code.startsWith('15') && !code.startsWith('159');
            case 'cy':
                // 创业板ETF：159开头
                return code.startsWith('159');
            case 'kc':
                // 科创板ETF：58开头
                return code.startsWith('58');
            default:
                return true;
        }
    });
}

function sortQuotes(quotes, field, order) {
    return quotes.sort((a, b) => {
        let valA = a[field];
        let valB = b[field];
        
        // 处理null/undefined值
        if (valA === null || valA === undefined) valA = order === 'asc' ? Infinity : -Infinity;
        if (valB === null || valB === undefined) valB = order === 'asc' ? Infinity : -Infinity;
        
        // 字符串排序（代码）
        if (field === 'code' || field === 'etf_code') {
            return order === 'asc' 
                ? String(valA).localeCompare(String(valB))
                : String(valB).localeCompare(String(valA));
        }
        
        // 数字排序
        if (order === 'asc') {
            return valA - valB;
        } else {
            return valB - valA;
        }
    });
}

function sortTable(field) {
    // 切换排序方向
    if (currentSort.field === field) {
        currentSort.order = currentSort.order === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.field = field;
        currentSort.order = 'desc'; // 新字段默认降序
    }
    
    // 更新表头样式
    document.querySelectorAll('th.sortable').forEach(th => {
        th.classList.remove('asc', 'desc');
        if (th.dataset.sort === field) {
            th.classList.add(currentSort.order);
        }
    });
    
    // 重新排序并显示
    currentPage = 1;
    filterAndDisplayQuotes();
}

function filterAndDisplayQuotes() {
    // 先按市场筛选
    let quotes = filterQuotesByMarket(allQuotes, currentMarket);
    
    // 再排序
    quotes = sortQuotes(quotes, currentSort.field, currentSort.order);
    
    filteredQuotes = quotes;
    const totalCount = filteredQuotes.length;
    const totalPages = Math.ceil(totalCount / itemsPerPage);
    
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;
    
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
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
    if (totalCount > itemsPerPage) {
        pagination.style.display = 'flex';
        document.getElementById('current-page').textContent = currentPage;
        document.getElementById('total-pages').textContent = totalPages;
    } else {
        pagination.style.display = 'none';
    }
}

function changePageSize() {
    const select = document.getElementById('page-size');
    itemsPerPage = parseInt(select.value);
    currentPage = 1; // 重置到第一页
    filterAndDisplayQuotes();
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
    const totalPages = Math.ceil(filteredQuotes.length / itemsPerPage);
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
                    <button class="btn btn-outline btn-sm" onclick="editStrategy(${s.id})">编辑</button>
                    <button class="btn btn-outline btn-sm" onclick="deleteStrategy(${s.id})">删除</button>
                </td>
            </tr>
        `).join('');
    } catch (e) { console.error(e); }
}

// 模板策略的ETF选择
let csSelectedETFs = new Set();

function showCreateStrategyModal() {
    const sel = document.getElementById('cs-template');
    sel.innerHTML = templates.map(t => `<option value="${t.template_name}">${t.template_name} - ${t.description}</option>`).join('');
    renderTemplateParams();
    sel.onchange = renderTemplateParams;
    
    // 初始化ETF搜索
    initCSETFSearch();
    
    openModal('modal-create-strategy');
}

function initCSETFSearch() {
    csSelectedETFs.clear();
    updateCSETFDisplay();
    document.getElementById('cs-etfs').value = '';
    
    const searchInput = document.getElementById('cs-etf-search');
    const resultsDiv = document.getElementById('cs-etf-results');
    
    searchInput.value = '';
    
    // 移除旧的事件监听器（避免重复绑定）
    const newInput = searchInput.cloneNode(true);
    searchInput.parentNode.replaceChild(newInput, searchInput);
    
    // 搜索输入事件
    newInput.addEventListener('input', function() {
        const keyword = this.value.trim().toLowerCase();
        if (!keyword) {
            resultsDiv.classList.remove('active');
            return;
        }
        
        // 搜索匹配的ETF
        const matches = allETFList.filter(etf => {
            if (csSelectedETFs.has(etf.etf_code)) return false;
            return etf.etf_code.toLowerCase().includes(keyword) || 
                   etf.etf_name.toLowerCase().includes(keyword);
        }).slice(0, 10);
        
        if (matches.length > 0) {
            resultsDiv.innerHTML = matches.map(etf => `
                <div class="etf-search-item" onclick="selectCSETF('${etf.etf_code}', '${etf.etf_name}')">
                    <span class="code">${etf.etf_code}</span>
                    <span class="name">${etf.etf_name}</span>
                </div>
            `).join('');
            resultsDiv.classList.add('active');
        } else {
            resultsDiv.innerHTML = '<div class="etf-search-item"><span class="name">无匹配结果</span></div>';
            resultsDiv.classList.add('active');
        }
    });
    
    // 点击外部关闭搜索结果
    const clickHandler = function(e) {
        if (!e.target.closest('#modal-create-strategy .etf-search-container')) {
            resultsDiv.classList.remove('active');
        }
    };
    document.removeEventListener('click', clickHandler);
    document.addEventListener('click', clickHandler);
}

function selectCSETF(code, name) {
    csSelectedETFs.add(code);
    document.getElementById('cs-etf-search').value = '';
    document.getElementById('cs-etf-results').classList.remove('active');
    updateCSETFDisplay();
    updateCSETFInput();
}

function removeCSETF(code) {
    csSelectedETFs.delete(code);
    updateCSETFDisplay();
    updateCSETFInput();
}

function updateCSETFDisplay() {
    const container = document.getElementById('cs-etf-selected');
    if (csSelectedETFs.size === 0) {
        container.innerHTML = '';
        return;
    }
    
    container.innerHTML = Array.from(csSelectedETFs).map(code => {
        const etf = allETFList.find(e => e.etf_code === code);
        const name = etf ? etf.etf_name : code;
        return `
            <span class="etf-selected-tag">
                ${code} ${name !== code ? '(' + name.slice(0, 10) + ')' : ''}
                <span class="remove" onclick="removeCSETF('${code}')">×</span>
            </span>
        `;
    }).join('');
}

function updateCSETFInput() {
    document.getElementById('cs-etfs').value = Array.from(csSelectedETFs).join(',');
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

// ETF搜索相关变量
let allETFList = []; // 存储全市场ETF列表
let selectedETFs = new Set(); // 已选择的ETF

// 加载全市场ETF列表
async function loadAllETFList() {
    try {
        const res = await api('/api/etf/list');
        allETFList = res.data?.etfs || [];
    } catch (e) {
        console.error('加载ETF列表失败:', e);
    }
}

function showAIStrategyModal() {
    openModal('modal-ai-strategy');
    // 加载ETF列表（如果还没加载）
    if (allETFList.length === 0) {
        loadAllETFList();
    }
    // 初始化搜索
    initETFSearch();
}

function initETFSearch() {
    selectedETFs.clear();
    updateSelectedETFDisplay();
    document.getElementById('ai-etfs').value = '';
    
    const searchInput = document.getElementById('ai-etf-search');
    const resultsDiv = document.getElementById('ai-etf-results');
    
    searchInput.value = '';
    
    // 搜索输入事件
    searchInput.addEventListener('input', function() {
        const keyword = this.value.trim().toLowerCase();
        if (!keyword) {
            resultsDiv.classList.remove('active');
            return;
        }
        
        // 搜索匹配的ETF
        const matches = allETFList.filter(etf => {
            if (selectedETFs.has(etf.etf_code)) return false;
            return etf.etf_code.toLowerCase().includes(keyword) || 
                   etf.etf_name.toLowerCase().includes(keyword);
        }).slice(0, 10); // 最多显示10条
        
        if (matches.length > 0) {
            resultsDiv.innerHTML = matches.map(etf => `
                <div class="etf-search-item" onclick="selectETF('${etf.etf_code}', '${etf.etf_name}')">
                    <span class="code">${etf.etf_code}</span>
                    <span class="name">${etf.etf_name}</span>
                </div>
            `).join('');
            resultsDiv.classList.add('active');
        } else {
            resultsDiv.innerHTML = '<div class="etf-search-item"><span class="name">无匹配结果</span></div>';
            resultsDiv.classList.add('active');
        }
    });
    
    // 点击外部关闭搜索结果
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.etf-search-container')) {
            resultsDiv.classList.remove('active');
        }
    });
}

function selectETF(code, name) {
    selectedETFs.add(code);
    document.getElementById('ai-etf-search').value = '';
    document.getElementById('ai-etf-results').classList.remove('active');
    updateSelectedETFDisplay();
    updateETFInput();
}

function removeETF(code) {
    selectedETFs.delete(code);
    updateSelectedETFDisplay();
    updateETFInput();
}

function updateSelectedETFDisplay() {
    const container = document.getElementById('ai-etf-selected');
    if (selectedETFs.size === 0) {
        container.innerHTML = '';
        return;
    }
    
    container.innerHTML = Array.from(selectedETFs).map(code => {
        const etf = allETFList.find(e => e.etf_code === code);
        const name = etf ? etf.etf_name : code;
        return `
            <span class="etf-selected-tag">
                ${code} ${name !== code ? '(' + name.slice(0, 10) + ')' : ''}
                <span class="remove" onclick="removeETF('${code}')">×</span>
            </span>
        `;
    }).join('');
}

function updateETFInput() {
    document.getElementById('ai-etfs').value = Array.from(selectedETFs).join(',');
}

async function submitAIStrategy() {
    const btn = document.getElementById('ai-submit-btn');
    btn.disabled = true;
    btn.textContent = '生成中...';

    const body = {
        description: document.getElementById('ai-desc').value,
        etf_codes: document.getElementById('ai-etfs').value.split(',').map(s => s.trim()).filter(Boolean),
        initial_capital: Number(document.getElementById('ai-capital').value),
        model: document.getElementById('ai-model').value,
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
//  编辑策略
// ==================================================================
let editSelectedETFs = new Set();
let currentEditStrategy = null;

async function editStrategy(id) {
    try {
        const res = await api(`/api/strategy/${id}`);
        const s = res.data;
        currentEditStrategy = s;
        
        // 填充表单
        document.getElementById('edit-strategy-id').value = s.id;
        document.getElementById('edit-name').value = s.name;
        document.getElementById('edit-desc').value = s.description || '';
        document.getElementById('edit-capital').value = s.initial_capital;
        
        // 设置ETF选择
        editSelectedETFs = new Set(s.etf_codes || []);
        updateEditETFDisplay();
        document.getElementById('edit-etfs').value = Array.from(editSelectedETFs).join(',');
        
        // AI策略显示代码编辑区
        const codeGroup = document.getElementById('edit-code-group');
        if (s.strategy_type === 'ai_generated' && s.code) {
            codeGroup.style.display = 'block';
            document.getElementById('edit-code').value = s.code;
        } else {
            codeGroup.style.display = 'none';
        }
        
        // 初始化搜索
        initEditETFSearch();
        
        openModal('modal-edit-strategy');
    } catch (e) {
        toast('加载策略失败: ' + e.message, 'error');
    }
}

function initEditETFSearch() {
    const searchInput = document.getElementById('edit-etf-search');
    const resultsDiv = document.getElementById('edit-etf-results');
    
    searchInput.value = '';
    
    // 移除旧的事件监听器
    const newInput = searchInput.cloneNode(true);
    searchInput.parentNode.replaceChild(newInput, searchInput);
    
    // 搜索输入事件
    newInput.addEventListener('input', function() {
        const keyword = this.value.trim().toLowerCase();
        if (!keyword) {
            resultsDiv.classList.remove('active');
            return;
        }
        
        // 搜索匹配的ETF
        const matches = allETFList.filter(etf => {
            if (editSelectedETFs.has(etf.etf_code)) return false;
            return etf.etf_code.toLowerCase().includes(keyword) || 
                   etf.etf_name.toLowerCase().includes(keyword);
        }).slice(0, 10);
        
        if (matches.length > 0) {
            resultsDiv.innerHTML = matches.map(etf => `
                <div class="etf-search-item" onclick="selectEditETF('${etf.etf_code}', '${etf.etf_name}')">
                    <span class="code">${etf.etf_code}</span>
                    <span class="name">${etf.etf_name}</span>
                </div>
            `).join('');
            resultsDiv.classList.add('active');
        } else {
            resultsDiv.innerHTML = '<div class="etf-search-item"><span class="name">无匹配结果</span></div>';
            resultsDiv.classList.add('active');
        }
    });
    
    // 点击外部关闭搜索结果
    const clickHandler = function(e) {
        if (!e.target.closest('#modal-edit-strategy .etf-search-container')) {
            resultsDiv.classList.remove('active');
        }
    };
    document.removeEventListener('click', clickHandler);
    document.addEventListener('click', clickHandler);
}

function selectEditETF(code, name) {
    editSelectedETFs.add(code);
    document.getElementById('edit-etf-search').value = '';
    document.getElementById('edit-etf-results').classList.remove('active');
    updateEditETFDisplay();
    updateEditETFInput();
}

function removeEditETF(code) {
    editSelectedETFs.delete(code);
    updateEditETFDisplay();
    updateEditETFInput();
}

function updateEditETFDisplay() {
    const container = document.getElementById('edit-etf-selected');
    if (editSelectedETFs.size === 0) {
        container.innerHTML = '';
        return;
    }
    
    container.innerHTML = Array.from(editSelectedETFs).map(code => {
        const etf = allETFList.find(e => e.etf_code === code);
        const name = etf ? etf.etf_name : code;
        return `
            <span class="etf-selected-tag">
                ${code} ${name !== code ? '(' + name.slice(0, 10) + ')' : ''}
                <span class="remove" onclick="removeEditETF('${code}')">×</span>
            </span>
        `;
    }).join('');
}

function updateEditETFInput() {
    document.getElementById('edit-etfs').value = Array.from(editSelectedETFs).join(',');
}

async function submitEditStrategy() {
    const id = document.getElementById('edit-strategy-id').value;
    const btn = document.getElementById('edit-submit-btn');
    btn.disabled = true;
    btn.textContent = '保存中...';
    
    const body = {
        name: document.getElementById('edit-name').value,
        description: document.getElementById('edit-desc').value,
        etf_codes: document.getElementById('edit-etfs').value.split(',').map(s => s.trim()).filter(Boolean),
        initial_capital: Number(document.getElementById('edit-capital').value),
    };
    
    // AI策略可以编辑代码
    const codeGroup = document.getElementById('edit-code-group');
    if (codeGroup.style.display !== 'none') {
        const code = document.getElementById('edit-code').value.trim();
        if (code) {
            body.code = code;
        }
    }
    
    try {
        const res = await api(`/api/strategy/${id}`, { 
            method: 'PUT', 
            body: JSON.stringify(body) 
        });
        toast('策略更新成功', 'success');
        closeModal('modal-edit-strategy');
        loadStrategies();
    } catch (e) {
        toast(e.message || '更新失败', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '保存';
    }
}

// ==================================================================
//  更新指定区间行情
// ==================================================================
function showUpdateRangeModal() {
    // 设置默认日期范围（最近30天）
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 30);
    
    document.getElementById('range-end-date').value = end.toISOString().slice(0, 10);
    document.getElementById('range-start-date').value = start.toISOString().slice(0, 10);
    
    openModal('modal-update-range');
}

async function submitUpdateRange() {
    const startDate = document.getElementById('range-start-date').value;
    const endDate = document.getElementById('range-end-date').value;
    
    if (!startDate || !endDate) {
        toast('请选择开始和结束日期', 'error');
        return;
    }
    
    if (startDate > endDate) {
        toast('开始日期不能晚于结束日期', 'error');
        return;
    }
    
    // 转换为YYYYMMDD格式
    const startStr = startDate.replace(/-/g, '');
    const endStr = endDate.replace(/-/g, '');
    
    const btn = document.getElementById('range-submit-btn');
    btn.disabled = true;
    btn.textContent = '更新中...';
    
    try {
        const res = await api(`/api/etf/update-range?start_date=${startStr}&end_date=${endStr}`, { 
            method: 'POST'
        });
        toast(res.message, 'success');
        closeModal('modal-update-range');
        // 刷新行情数据
        loadMarket();
    } catch (e) {
        toast(e.message || '更新失败', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '开始更新';
    }
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

        // 每日策略执行详情
        renderDailyDetails(d.daily_details);

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

// 渲染每日策略执行详情
function renderDailyDetails(dailyDetails) {
    if (!dailyDetails || dailyDetails.length === 0) {
        document.getElementById('bt-daily-tbody').innerHTML = '<tr><td colspan="7" class="text-center">无数据</td></tr>';
        return;
    }

    let html = '';
    dailyDetails.forEach(day => {
        const date = day.date;
        const hasSignals = day.signals && day.signals.length > 0;
        const hasDecisions = day.decisions && day.decisions.length > 0;
        
        // 构建持仓字符串
        const holdingsStr = Object.entries(day.holdings || {})
            .filter(([code, qty]) => qty > 0)
            .map(([code, qty]) => `${code}:${qty}`)
            .join(', ') || '无';
        
        if (!hasSignals && !hasDecisions) {
            // 无信号无决策的一天
            html += `
                <tr>
                    <td>${date}</td>
                    <td>-</td>
                    <td><span style="color:var(--text-secondary)">无信号</span></td>
                    <td>-</td>
                    <td>${holdingsStr}</td>
                    <td class="text-right">${fmtNum(day.cash)}</td>
                    <td class="text-right">${fmtNum(day.total_asset)}</td>
                </tr>
            `;
        } else {
            // 有信号或决策，每个信号/决策显示一行
            const rowCount = Math.max(day.signals?.length || 0, day.decisions?.length || 0);
            for (let i = 0; i < rowCount; i++) {
                const signal = day.signals?.[i];
                const decision = day.decisions?.[i];
                
                const signalStr = signal 
                    ? `<span style="color:${signal.direction === 'buy' ? 'var(--danger)' : 'var(--success)'}">${signal.direction === 'buy' ? '买入' : '卖出'}${signal.strength ? '(' + (signal.strength * 100).toFixed(0) + '%)' : ''}</span><br><small>${signal.reason || ''}</small>`
                    : '-';
                
                const decisionStr = decision
                    ? `<span style="color:${decision.action === '买入' ? 'var(--danger)' : 'var(--success)'}">${decision.action}</span><br><small>${decision.etf_code} @ ${fmtNum(decision.price, 3)} × ${decision.quantity}</small>`
                    : '-';
                
                html += `
                    <tr>
                        <td>${i === 0 ? date : ''}</td>
                        <td>${signal?.etf_code || decision?.etf_code || '-'}</td>
                        <td>${signalStr}</td>
                        <td>${decisionStr}</td>
                        <td>${i === 0 ? holdingsStr : ''}</td>
                        <td class="text-right">${i === 0 ? fmtNum(day.cash) : ''}</td>
                        <td class="text-right">${i === 0 ? fmtNum(day.total_asset) : ''}</td>
                    </tr>
                `;
            }
        }
    });
    
    document.getElementById('bt-daily-tbody').innerHTML = html;
}

// 切换每日详情显示
function toggleDailyDetails() {
    const el = document.getElementById('bt-daily-details');
    el.style.display = el.style.display === 'none' ? 'block' : 'none';
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
