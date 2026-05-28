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

function toast(msg, type = 'info', duration = 5000) {
    const el = document.createElement('div');
    el.className = `toast toast-${type} toast-dismiss`;
    el.innerHTML = `<span>${msg}</span><span class="toast-close">×</span>`;
    
    el.querySelector('.toast-close').onclick = () => {
        el.style.animation = 'slideOut 0.2s ease';
        setTimeout(() => el.remove(), 200);
    };
    
    document.body.appendChild(el);
    setTimeout(() => {
        if (el.parentNode) {
            el.style.animation = 'slideOut 0.2s ease';
            setTimeout(() => el.remove(), 200);
        }
    }, duration);
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
        if (tab === 'auto-strategy') loadAutoStrategyPage();
    });
});

// ---- AI策略子导航切换 ----
document.querySelectorAll('.sub-nav-tab').forEach(a => {
    a.addEventListener('click', (e) => {
        e.preventDefault();
        document.querySelectorAll('.sub-nav-tab').forEach(x => x.classList.remove('active'));
        a.classList.add('active');
        const subtab = a.dataset.subtab;
        
        document.querySelectorAll('.subtab-content').forEach(x => x.classList.remove('active'));
        document.getElementById('subtab-' + subtab).classList.add('active');

        // 子页面加载
        if (subtab === 'overview') loadAutoStrategyOverview();
        if (subtab === 'sentiment') loadSentimentPage();
        if (subtab === 'technical') loadTechnicalPage();
        if (subtab === 'risk') loadRiskPage();
        if (subtab === 'review') loadReviewPage();
    });
});

// ---- Modal ----
function openModal(id) { 
    document.getElementById(id).classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeModal(id) { 
    document.getElementById(id).classList.remove('active');
    document.body.style.overflow = '';
}

// ESC键关闭弹窗
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const activeModal = document.querySelector('.modal-overlay.active');
        if (activeModal) {
            closeModal(activeModal.id);
        }
    }
});

// 点击背景关闭弹窗
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) {
        closeModal(e.target.id);
    }
});

// ==================================================================
//  行情看板
// ==================================================================
let currentCompany = 'all';
let currentMarket = 'all';
let currentPage = 1;
let allQuotes = [];
let filteredQuotes = [];
let currentSort = { field: 'amount', order: 'desc' }; // 默认按成交额降序
let itemsPerPage = 50; // 默认每页显示数量

// 初始化基金公司和市场标签点击事件
document.addEventListener('DOMContentLoaded', function() {
    // 基金公司筛选
    document.querySelectorAll('.company-tabs .filter-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.company-tabs .filter-tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            currentCompany = this.dataset.company;
            currentPage = 1;
            filterAndDisplayQuotes();
            updateMarketTitle();
        });
    });
    
    // 市场筛选
    document.querySelectorAll('.market-tabs .filter-tab').forEach(tab => {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.market-tabs .filter-tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            currentMarket = this.dataset.market;
            currentPage = 1;
            filterAndDisplayQuotes();
            updateMarketTitle();
        });
    });
});

async function loadMarket() {
    try {
        // 获取净值概览数据（来自证监会官方披露）
        const res = await api('/api/net-value/overview?limit=500');
        allQuotes = res.data?.etfs || [];
        
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

function filterQuotesByCompanyAndMarket(quotes, company, market) {
    let result = quotes;
    
    // 第一层：基金公司筛选
    if (company !== 'all') {
        const companyKeywords = {
            'gf': '广发',
            'yfd': '易方达',
            'hx': '华夏'
        };
        const keyword = companyKeywords[company];
        if (keyword) {
            result = result.filter(q => q.etf_name && q.etf_name.includes(keyword));
        }
    }
    
    // 第二层：市场筛选
    if (market !== 'all') {
        result = result.filter(q => {
            const code = q.etf_code;
            
            // 上交所：代码以5开头（51、52、56、58等）
            if (market === 'sh') {
                return code.startsWith('5') && !code.startsWith('15');
            }
            
            // 深交所：代码以15开头
            if (market === 'sz') {
                return code.startsWith('15');
            }
            
            return true;
        });
    }
    
    return result;
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
    filteredQuotes = filterQuotesByCompanyAndMarket(allQuotes, currentCompany, currentMarket);
    filteredQuotes = sortQuotes(filteredQuotes, currentSort.field, currentSort.order);
    displayQuotes(filteredQuotes);
}

function displayQuotes(quotes) {
    const tbody = document.getElementById('market-table');
    const empty = document.getElementById('market-empty');
    const pagination = document.getElementById('market-pagination');
    
    if (!quotes.length) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        pagination.style.display = 'none';
        document.getElementById('market-count').textContent = '';
        return;
    }
    
    empty.style.display = 'none';
    pagination.style.display = 'flex';
    
    // 分页逻辑
    const totalPages = Math.ceil(quotes.length / itemsPerPage);
    const startIdx = (currentPage - 1) * itemsPerPage;
    const endIdx = Math.min(startIdx + itemsPerPage, quotes.length);
    const pageQuotes = quotes.slice(startIdx, endIdx);
    
    // 渲染表格
    tbody.innerHTML = pageQuotes.map(q => `
        <tr>
            <td><span class="etf-code">${q.etf_code}</span></td>
            <td><span class="etf-name" style="cursor:pointer;color:var(--primary);" onclick="showETFHistory('${q.etf_code}', '${q.etf_name || '未知ETF'}')" title="点击查看历史走势">${q.etf_name || '-'}</span></td>
            <td class="text-right">${q.net_value != null ? fmtNum(q.net_value, 3) : '-'}</td>
            <td class="text-right">${fmtPct(q.net_value_change_pct)}</td>
            <td>${q.trade_date || '-'}</td>
            <td>
                <button class="btn btn-outline btn-sm" onclick="fetchETFData('${q.etf_code}')">拉取历史</button>
            </td>
        </tr>
    `).join('');
    
    // 更新分页信息
    document.getElementById('current-page').textContent = currentPage;
    document.getElementById('total-pages').textContent = totalPages;
    document.getElementById('market-count').textContent = `共 ${quotes.length} 只ETF`;
}

function updateMarketStats(quotes) {
    let upCount = 0, downCount = 0, flatCount = 0;
    
    quotes.forEach(q => {
        const changePct = q.net_value_change_pct || 0;
        if (changePct > 0) upCount++;
        else if (changePct < 0) downCount++;
        else flatCount++;
    });
    
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

function changePageSize() {
    const pageSizeSelect = document.getElementById('page-size');
    itemsPerPage = parseInt(pageSizeSelect.value);
    currentPage = 1;
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
    toast('正在从证监会更新数据库中所有ETF的最近净值...', 'info');
    try {
        // 更新数据库中所有ETF的最近一个工作日净值
        const res = await api('/api/net-value/batch-update?days_limit=1', { method: 'POST' });
        toast(`更新完成: 成功 ${res.data.success_count}, 失败 ${res.data.fail_count}, 共 ${res.data.total} 只ETF`, 'success');
        loadMarket();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function fetchETFData(code) {
    toast(`正在拉取 ${code} 净值数据...`, 'info');
    
    try {
        const res = await api(`/api/net-value/update-single/${code}`, { method: 'POST' });
        
        if (res.data.success) {
            toast(`${code}: 成功更新 ${res.data.count} 条净值数据`, 'success');
        } else {
            toast(`${code}: 净值数据获取失败`, 'error');
        }
    } catch (e) {
        toast('拉取失败: ' + e.message, 'error');
    }
}

// ==================================================================
//  策略管理 - 创建资产配置策略
// ==================================================================



let etfAllocations = []; // [{code: '510300', name: '沪深300ETF', ratio: 30}]

async function showCreateStrategyModal() {
    etfAllocations = [];
    updateAllocationDisplay();
    
    document.getElementById('cs-name').value = '';
    document.getElementById('cs-capital').value = '100000';
    document.getElementById('cs-rebalance-freq').value = 'monthly';
    document.getElementById('cs-rebalance-threshold').value = '5';
    document.getElementById('cs-etf-search').value = '';
    
    initETFSearch();
    
    openModal('modal-create-strategy');
}

function initETFSearch() {
    const searchInput = document.getElementById('cs-etf-search');
    const resultsDiv = document.getElementById('cs-etf-results');
    
    searchInput.value = '';
    
    const newInput = searchInput.cloneNode(true);
    searchInput.parentNode.replaceChild(newInput, searchInput);
    
    newInput.addEventListener('input', function() {
        const keyword = this.value.trim().toLowerCase();
        if (!keyword) {
            resultsDiv.classList.remove('active');
            return;
        }
        
        const matches = allQuotes.filter(etf => {
            if (etfAllocations.find(a => a.code === etf.etf_code)) return false;
            return etf.etf_code.toLowerCase().includes(keyword) || 
                   etf.etf_name.toLowerCase().includes(keyword);
        }).slice(0, 10);
        
        if (matches.length > 0) {
            resultsDiv.innerHTML = matches.map(etf => `
                <div class="etf-search-item" onclick="addETFAllocation('${etf.etf_code}', '${etf.etf_name}')">
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
    
    document.addEventListener('click', function(e) {
        if (!e.target.closest('#modal-create-strategy .etf-search-container')) {
            resultsDiv.classList.remove('active');
        }
    });
}

function addETFAllocation(code, name) {
    etfAllocations.push({
        code: code,
        name: name.slice(0, 15),
        ratio: 0
    });
    
    document.getElementById('cs-etf-search').value = '';
    document.getElementById('cs-etf-results').classList.remove('active');
    
    updateAllocationDisplay();
}

function removeETFAllocation(code) {
    etfAllocations = etfAllocations.filter(a => a.code !== code);
    updateAllocationDisplay();
}

function updateAllocationRatio(code, ratio) {
    const allocation = etfAllocations.find(a => a.code === code);
    if (allocation) {
        allocation.ratio = parseFloat(ratio) || 0;
    }
    updateAllocationDisplay();
}

function updateAllocationDisplay() {
    const container = document.getElementById('etf-allocation-list');
    
    if (etfAllocations.length === 0) {
        container.innerHTML = '<div style="color:var(--text-secondary);font-size:14px;padding:8px;">请搜索并添加ETF，然后设置占比</div>';
        document.getElementById('allocation-total').textContent = '当前占比总和: 0%';
        document.getElementById('allocation-warning').textContent = '';
        return;
    }
    
    container.innerHTML = etfAllocations.map(a => `
        <div style="display:flex;align-items:center;margin-bottom:8px;padding:8px;background:var(--bg-secondary);border-radius:4px;">
            <span style="flex:1;font-weight:600;">${a.code}</span>
            <span style="flex:2;color:var(--text-secondary);font-size:13px;">${a.name}</span>
            <input type="number" value="${a.ratio}" min="0" max="100" step="1"
                   onchange="updateAllocationRatio('${a.code}', this.value)"
                   style="width:70px;margin-right:8px;padding:4px;border:1px solid var(--border);border-radius:4px;text-align:right;">
            <span style="width:20px;color:var(--text-secondary);">%</span>
            <button class="btn btn-outline btn-sm" onclick="removeETFAllocation('${a.code}')" style="margin-left:8px;padding:2px 8px;">删除</button>
        </div>
    `).join('');
    
    const totalRatio = etfAllocations.reduce((sum, a) => sum + a.ratio, 0);
    document.getElementById('allocation-total').textContent = `当前占比总和: ${totalRatio.toFixed(1)}%`;
    
    const warning = document.getElementById('allocation-warning');
    if (totalRatio > 100) {
        warning.textContent = '⚠️ 占比总和超过100%！';
        warning.style.color = 'var(--danger)';
    } else if (totalRatio < 100) {
        warning.textContent = `还可配置 ${(100 - totalRatio).toFixed(1)}%`;
        warning.style.color = 'var(--text-secondary)';
    } else {
        warning.textContent = '✅ 配置完整';
        warning.style.color = 'var(--success)';
    }
}

async function submitCreateStrategy() {
    try {
        console.log('=== submitCreateStrategy 开始执行 ===');
        
        const nameEl = document.getElementById('cs-name');
        if (!nameEl) {
            throw new Error('找不到策略名称输入框(cs-name)');
        }
        const name = nameEl.value.trim();
        
        if (!name) {
            throw new Error('请输入策略名称');
        }
        
        const capitalEl = document.getElementById('cs-capital');
        if (!capitalEl) {
            throw new Error('找不到初始资金输入框(cs-capital)');
        }
        const initialCapital = Number(capitalEl.value);
        
        if (etfAllocations.length === 0) {
            throw new Error('请添加至少一个ETF配置');
        }
        
        const totalRatio = etfAllocations.reduce((sum, a) => sum + a.ratio, 0);
        if (Math.abs(totalRatio - 100) > 0.1) {
            throw new Error(`ETF配置占比总和需等于100%，当前总和${totalRatio.toFixed(1)}%`);
        }
        
        const allocationConfig = {};
        etfAllocations.forEach(a => {
            allocationConfig[a.code] = a.ratio / 100;
        });
        
        const freqEl = document.getElementById('cs-rebalance-freq');
        if (!freqEl) {
            throw new Error('找不到再平衡频率选择框(cs-rebalance-freq)');
        }
        
        const thresholdEl = document.getElementById('cs-rebalance-threshold');
        if (!thresholdEl) {
            throw new Error('找不到偏离阈值输入框(cs-rebalance-threshold)');
        }
        
        const body = {
            name: name,
            initial_capital: initialCapital,
            allocation_config: allocationConfig,
            rebalance_freq: freqEl.value,
            rebalance_threshold: Number(thresholdEl.value) / 100,
            strategy_type: 'custom'
        };
        
        console.log('提交的数据:', body);
        
        const btn = document.getElementById('cs-submit-btn');
        if (!btn) {
            throw new Error('找不到提交按钮(cs-submit-btn)');
        }
        btn.disabled = true;
        btn.textContent = '创建中...';
        
        const res = await api('/api/strategy/create-custom', { method: 'POST', body: JSON.stringify(body) });
        toast('资产配置策略创建成功', 'success');
        closeModal('modal-create-strategy');
        loadStrategies();
    } catch (e) {
        console.error('submitCreateStrategy错误:', e);
        toast('创建失败: ' + e.message, 'error');
    } finally {
        const btn = document.getElementById('cs-submit-btn');
        if (btn) {
            btn.disabled = false;
            btn.textContent = '创建策略';
        }
    }
}

async function loadStrategies() {
    try {
        const res = await api('/api/strategy/list');
        const strategies = res.data?.strategies || [];
        const cardsDiv = document.getElementById('strategy-cards');
        const empty = document.getElementById('strategy-empty');
        
        if (!strategies.length) {
            cardsDiv.innerHTML = '';
            empty.style.display = 'block';
            return;
        }
        
        empty.style.display = 'none';
        
        cardsDiv.innerHTML = strategies.map(s => {
            // 解析配置，显示ETF名称
            const allocationDetails = Object.entries(s.allocation_config || {})
                .map(([code, ratio]) => {
                    const etf = allQuotes.find(q => q.etf_code === code);
                    const etfName = etf ? etf.etf_name : '未知ETF';
                    return `
                        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);">
                            <span style="font-size:13px;">
                                <span style="font-weight:600;color:var(--primary);">${code}</span>
                                <span style="color:var(--text-secondary);margin-left:8px;">${etfName}</span>
                            </span>
                            <span style="font-weight:600;color:var(--success);">${(ratio * 100).toFixed(0)}%</span>
                        </div>
                    `;
                }).join('');
            
            const rebalanceText = s.rebalance_freq === 'none' 
                ? '❌ 禁用' 
                : `✅ ${getRebalanceFreqText(s.rebalance_freq)}检查 / ${(s.rebalance_threshold * 100).toFixed(0)}%阈值`;
            
            return `
                <div class="strategy-card" style="background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:16px;">
                    <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:12px;">
                        <h3 style="font-size:18px;font-weight:600;margin:0;color:var(--text);">${s.name}</h3>
                        <div style="display:flex;gap:8px;">
                            <button class="btn btn-success btn-sm" onclick="quickBacktest(${s.id}, '${s.name}', ${s.initial_capital})" style="padding:4px 12px;">回测</button>
                            <button class="btn btn-outline btn-sm" onclick="editStrategy(${s.id})" style="padding:4px 12px;">编辑</button>
                            <button class="btn btn-danger btn-sm" onclick="showDeleteConfirm(${s.id})" style="padding:4px 12px;">删除</button>
                        </div>
                    </div>
                    
                    <!-- ETF配置详情 -->
                    <div style="margin-bottom:12px;padding:12px;background:var(--bg-secondary);border-radius:6px;">
                        <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;">
                            📊 ETF配置方案
                        </div>
                        ${allocationDetails || '<div style="color:var(--text-secondary);font-size:13px;">暂无配置</div>'}
                    </div>
                    
                    <!-- 策略参数 -->
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px;">
                        <div style="padding:8px;background:var(--bg-secondary);border-radius:4px;">
                            <span style="color:var(--text-secondary);">初始资金：</span>
                            <span style="font-weight:600;color:var(--text);">${fmtAmount(s.initial_capital)}</span>
                        </div>
                        <div style="padding:8px;background:var(--bg-secondary);border-radius:4px;">
                            <span style="color:var(--text-secondary);">再平衡：</span>
                            <span style="font-weight:600;">${rebalanceText}</span>
                        </div>
                    </div>
                    
                    <!-- 策略类型标签 -->
                    <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
                        <span style="font-size:12px;padding:4px 8px;background:var(--primary);color:white;border-radius:4px;">
                            ${getStrategyTypeText(s.strategy_type)}
                        </span>
                        <span style="font-size:12px;color:var(--text-secondary);">
                            创建于 ${s.created_at ? new Date(s.created_at).toLocaleDateString('zh-CN') : '-'}
                        </span>
                    </div>
                </div>
            `;
        }).join('');
        
        updateStrategySelects(strategies);
    } catch (e) {
        console.error('加载策略失败:', e);
        toast('加载策略失败: ' + e.message, 'error');
    }
}

function updateStrategySelects(strategies) {
    const btSelect = document.getElementById('bt-strategy');
    const pfSelect = document.getElementById('pf-strategy');
    
    if (btSelect) {
        btSelect.innerHTML = strategies.map(s => 
            `<option value="${s.id}">${s.name}</option>`
        ).join('');
    }
    
    if (pfSelect) {
        pfSelect.innerHTML = strategies.map(s => 
            `<option value="${s.id}">${s.name}</option>`
        ).join('');
    }
}

function showDeleteConfirm(id) {
    const modalHtml = `
        <div class="modal-overlay active" id="modal-delete-confirm">
            <div class="modal modal-confirm" style="max-width:400px;text-align:center;">
                <div style="font-size:48px;margin-bottom:16px;">⚠️</div>
                <div class="modal-title">确认删除策略？</div>
                <div style="color:var(--text-secondary);font-size:14px;margin:12px 0 24px;">
                    删除后无法恢复，相关回测记录也会被清除
                </div>
                <div class="modal-actions" style="justify-content:center;">
                    <button class="btn btn-outline" onclick="closeDeleteConfirm()">取消</button>
                    <button class="btn btn-danger" onclick="confirmDelete(${id})" style="margin-left:12px;">确认删除</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    document.body.style.overflow = 'hidden';
}

function closeDeleteConfirm() {
    const modal = document.getElementById('modal-delete-confirm');
    if (modal) {
        modal.remove();
        document.body.style.overflow = '';
    }
}

async function deleteStrategy(id) {
    try {
        await api(`/api/strategy/${id}`, { method: 'DELETE' });
        toast('策略已删除', 'success');
        loadStrategies();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

async function confirmDelete(id) {
    closeDeleteConfirm();
    await deleteStrategy(id);
}

// ==================================================================
//  快速回测
// ==================================================================
let currentQuickBacktestStrategy = null;

function quickBacktest(strategyId, strategyName, initialCapital) {
    currentQuickBacktestStrategy = strategyId;
    
    document.getElementById('qb-strategy-name').textContent = strategyName;
    document.getElementById('qb-capital').value = initialCapital;
    
    const today = new Date();
    document.getElementById('qb-end').value = today.toISOString().split('T')[0];
    
    const oneYearAgo = new Date(today);
    oneYearAgo.setFullYear(oneYearAgo.getFullYear() - 1);
    document.getElementById('qb-start').value = oneYearAgo.toISOString().split('T')[0];
    
    document.getElementById('qb-result').style.display = 'none';
    
    openModal('modal-quick-backtest');
}

async function runQuickBacktest() {
    const btn = document.getElementById('qb-run-btn');
    btn.disabled = true;
    btn.textContent = '回测中...';
    
    const body = {
        strategy_id: currentQuickBacktestStrategy,
        start_date: document.getElementById('qb-start').value,
        end_date: document.getElementById('qb-end').value,
        initial_capital: Number(document.getElementById('qb-capital').value),
    };
    
    try {
        const res = await api('/api/backtest/run', { method: 'POST', body: JSON.stringify(body) });
        const d = res.data;
        
        if (!d) {
            throw new Error('回测返回数据为空');
        }
        
        document.getElementById('qb-result').style.display = 'block';
        
        document.getElementById('qb-stats').innerHTML = [
            { label: '最终资产', value: '¥' + fmtNum(d.final_asset || 0), color: '' },
            { label: '总收益率', value: fmtNum(d.total_return_pct || 0) + '%', color: (d.total_return_pct || 0) >= 0 ? 'var(--danger)' : 'var(--success)' },
            { label: '最大回撤', value: fmtNum(d.max_drawdown_pct || 0) + '%', color: 'var(--danger)' },
            { label: 'Sharpe', value: d.sharpe_ratio != null ? fmtNum(d.sharpe_ratio) : '-', color: '' },
            { label: '交易次数', value: d.trade_count || 0, color: '' },
        ].map(s => `<div class="stat-card"><div class="stat-value" style="color:${s.color || 'inherit'}">${s.value}</div><div class="stat-label">${s.label}</div></div>`).join('');
        
        const periodReturnsDiv = document.getElementById('qb-period-returns');
        if (d.time_period_returns && d.time_period_returns.length > 0) {
            periodReturnsDiv.innerHTML = `
                <div class="card" style="margin-top:16px;">
                    <div class="card-title">时间段收益</div>
                    <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(180px, 1fr));gap:12px;">
                        ${d.time_period_returns.map(p => `
                            <div style="background:var(--bg-secondary);border-radius:8px;padding:12px;text-align:center;">
                                <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:8px;">${p.period}</div>
                                <div style="font-size:20px;font-weight:700;color:${p.return_pct >= 0 ? 'var(--danger)' : 'var(--success)'};">
                                    ${fmtNum(p.return_pct)}%
                                </div>
                                <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
                                    ${p.days}天
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        } else {
            periodReturnsDiv.innerHTML = '';
        }
        
        setTimeout(() => {
            if (d.daily_data && d.daily_data.length > 0) {
                const dates = d.daily_data.map(x => x.date);
                const assets = d.daily_data.map(x => x.total_asset);
                const profits = d.daily_data.map(x => x.profit_pct);
                const initialCapital = body.initial_capital;
                const initialCapitalLine = dates.map(() => initialCapital);
                
                const assetChart = echarts.init(document.getElementById('qb-chart-asset'));
                assetChart.setOption({
                    tooltip: {
                        trigger: 'axis',
                        formatter: function(params) {
                            const date = params[0].axisValue;
                            let result = `${date}<br/>`;
                            params.forEach(p => {
                                if (p.seriesName === '总资产') {
                                    result += `总资产：¥${fmtNum(p.value)}<br/>`;
                                } else if (p.seriesName === '初始资产') {
                                    result += `初始资产：¥${fmtNum(p.value)}<br/>`;
                                }
                            });
                            return result;
                        }
                    },
                    legend: { data: ['总资产', '初始资产'], bottom: 0 },
                    grid: { left: '3%', right: '4%', bottom: '15%', top: '3%', containLabel: true },
                    xAxis: { type: 'category', data: dates, boundaryGap: false },
                    yAxis: { type: 'value', name: '总资产（元）' },
                    series: [
                        {
                            name: '总资产',
                            type: 'line',
                            data: assets,
                            smooth: true,
                            areaStyle: { color: 'rgba(59, 130, 246, 0.1)' },
                            lineStyle: { color: '#3b82f6', width: 2 },
                            itemStyle: { color: '#3b82f6' }
                        },
                        {
                            name: '初始资产',
                            type: 'line',
                            data: initialCapitalLine,
                            lineStyle: { color: '#9ca3af', width: 1, type: 'dashed' },
                            itemStyle: { color: '#9ca3af' },
                            symbol: 'none'
                        }
                    ]
                });
                
                const profitChart = echarts.init(document.getElementById('qb-chart-profit'));
                profitChart.setOption({
                    tooltip: {
                        trigger: 'axis',
                        formatter: function(params) {
                            const date = params[0].axisValue;
                            const profit = params[0].value;
                            return `${date}<br/>收益率：${fmtNum(profit)}%`;
                        }
                    },
                    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
                    xAxis: { type: 'category', data: dates, boundaryGap: false },
                    yAxis: { type: 'value', name: '收益率（%）' },
                    series: [
                        {
                            name: '收益率',
                            type: 'line',
                            data: profits,
                            smooth: true,
                            lineStyle: { width: 2 },
                            itemStyle: {
                                color: function(params) {
                                    return params.value >= 0 ? '#ef4444' : '#10b981';
                                }
                            }
                        }
                    ]
                });
            }
        }, 100);
        
        const tradesContainer = document.getElementById('qb-trades-container');
        if (d.rebalance_records && d.rebalance_records.length > 0) {
            const allTrades = [];
            d.rebalance_records.forEach(record => {
                record.adjustments.forEach(adj => {
                    allTrades.push({
                        date: record.date,
                        etf_code: adj.etf_code,
                        action: adj.action,
                        price: adj.price,
                        quantity: adj.quantity,
                        amount: adj.amount,
                        reason: record.reason
                    });
                });
            });
            
            allTrades.sort((a, b) => new Date(b.date) - new Date(a.date));
            
            tradesContainer.innerHTML = `
                <div class="card" style="margin-top:16px;">
                    <div class="card-title">交易记录（共${allTrades.length}条）</div>
                    <div class="table-wrap" style="max-height:300px;overflow-y:auto;">
                        <table>
                            <thead>
                                <tr>
                                    <th>日期</th>
                                    <th>ETF代码</th>
                                    <th>操作</th>
                                    <th class="text-right">价格</th>
                                    <th class="text-right">数量</th>
                                    <th class="text-right">金额</th>
                                    <th>原因</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${allTrades.slice(0, 10).map(t => `
                                    <tr>
                                        <td>${t.date}</td>
                                        <td style="font-weight:600;color:var(--primary);">${t.etf_code}</td>
                                        <td><span class="badge badge-${t.action === '买入' ? 'success' : 'danger'}">${t.action}</span></td>
                                        <td class="text-right">${fmtNum(t.price)}</td>
                                        <td class="text-right">${t.quantity}</td>
                                        <td class="text-right">¥${fmtNum(t.amount)}</td>
                                        <td style="font-size:12px;color:var(--text-secondary);">${t.reason}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        } else {
            tradesContainer.innerHTML = '';
        }
        
        btn.disabled = false;
        btn.textContent = '开始回测';
        toast('回测完成', 'success');
    } catch (e) {
        btn.disabled = false;
        btn.textContent = '开始回测';
        toast('回测失败: ' + e.message, 'error');
    }
}

// ==================================================================
//  AI策略生成
// ==================================================================
let aiChatHistory = [];
let currentAIConfig = null;

async function showAIStrategyModal() {
    aiChatHistory = [];
    currentAIConfig = null;
    
    document.getElementById('ai-chat-messages').innerHTML = `
        <div class="chat-message ai-message">
            <div class="message-avatar">🤖</div>
            <div class="message-content">
                <p>你好！我是AI策略助手，可以帮你生成ETF配置方案。</p>
                <p style="font-size:13px;color:var(--text-secondary);margin-top:8px;">
                    请告诉我你的投资偏好，例如：<br>
                    • "我要一个保守组合，债券为主"<br>
                    • "追求高收益，偏向科技股"<br>
                    • "均衡配置，股债平衡"
                </p>
            </div>
        </div>
    `;
    
    document.getElementById('ai-current-config').style.display = 'none';
    document.getElementById('ai-chat-input').value = '';
    document.getElementById('ai-strategy-name').value = '';
    document.getElementById('ai-capital').value = '100000';
    document.getElementById('ai-rebalance-freq').value = 'monthly';
    document.getElementById('ai-rebalance-threshold').value = '5';
    document.getElementById('ai-confirm-btn').style.display = 'none';
    
    openModal('modal-ai-strategy');
}

function clearAIChatHistory() {
    aiChatHistory = [];
    currentAIConfig = null;
    document.getElementById('ai-chat-messages').innerHTML = `
        <div class="chat-message ai-message">
            <div class="message-avatar">🤖</div>
            <div class="message-content">
                <p>对话已清空。请重新描述你的投资偏好。</p>
            </div>
        </div>
    `;
    document.getElementById('ai-current-config').style.display = 'none';
    document.getElementById('ai-confirm-btn').style.display = 'none';
}

async function sendAIMessage() {
    const input = document.getElementById('ai-chat-input');
    const message = input.value.trim();
    
    if (!message) {
        toast('请输入内容', 'warning');
        return;
    }
    
    const sendBtn = document.getElementById('ai-send-btn');
    sendBtn.disabled = true;
    sendBtn.textContent = '生成中...';
    
    try {
        aiChatHistory.push({ role: 'user', content: message });
        
        const messagesContainer = document.getElementById('ai-chat-messages');
        messagesContainer.innerHTML += `
            <div class="chat-message user-message">
                <div class="message-avatar">👤</div>
                <div class="message-content">
                    <p>${message}</p>
                </div>
            </div>
        `;
        
        input.value = '';
        
        const res = await api('/api/strategy/ai-chat', {
            method: 'POST',
            body: JSON.stringify({
                message: message,
                chat_history: JSON.stringify(aiChatHistory.slice(0, -1)),
                current_allocation: currentAIConfig,
                model: 'qwen3.6-plus'
            })
        });
        
        // 解析后端返回数据（注意字段名称）
        currentAIConfig = res.data.allocation || null;
        const aiResponse = res.data.ai_response || '已生成配置方案';
        
        // 显示AI回复
        messagesContainer.innerHTML += `
            <div class="chat-message ai-message">
                <div class="message-avatar">🤖</div>
                <div class="message-content">
                    <p>${aiResponse}</p>
                </div>
            </div>
        `;
        
        // 如果生成了配置方案，显示预览和确认按钮
        if (currentAIConfig && Object.keys(currentAIConfig).length > 0) {
            const configPreview = document.getElementById('ai-config-preview');
            configPreview.innerHTML = Object.entries(currentAIConfig)
                .map(([code, ratio]) => {
                    const etf = allQuotes.find(q => q.etf_code === code);
                    return `
                        <div class="config-item">
                            <span>
                                <span class="config-etf-code">${code}</span>
                                <span class="config-etf-name">${etf ? etf.etf_name : ''}</span>
                            </span>
                            <span class="config-ratio">${(ratio * 100).toFixed(0)}%</span>
                        </div>
                    `;
                }).join('');
            
            document.getElementById('ai-current-config').style.display = 'block';
            document.getElementById('ai-confirm-btn').style.display = 'inline-block';
        }
        
        aiChatHistory.push({ role: 'assistant', content: aiResponse });
        
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
        
    } catch (e) {
        toast('AI生成失败: ' + e.message, 'error');
    } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = '发送';
    }
}

async function confirmAIStrategy() {
    if (!currentAIConfig) {
        toast('没有可保存的配置方案', 'warning');
        return;
    }
    
    const nameInput = document.getElementById('ai-strategy-name');
    const name = nameInput.value.trim();
    
    if (!name) {
        toast('请输入策略名称', 'warning');
        nameInput.focus();
        return;
    }
    
    const capital = Number(document.getElementById('ai-capital').value);
    const rebalanceFreq = document.getElementById('ai-rebalance-freq').value;
    const rebalanceThreshold = Number(document.getElementById('ai-rebalance-threshold').value) / 100;
    
    const confirmBtn = document.getElementById('ai-confirm-btn');
    confirmBtn.disabled = true;
    confirmBtn.textContent = '保存中...';
    
    try {
        const res = await api('/api/strategy/create-custom', {
            method: 'POST',
            body: JSON.stringify({
                name: name,
                initial_capital: capital,
                allocation_config: currentAIConfig,
                rebalance_freq: rebalanceFreq,
                rebalance_threshold: rebalanceThreshold,
                strategy_type: 'ai_generated'  // ✅ 修复：匹配后端pattern规则
            })
        });
        
        toast('AI策略创建成功', 'success');
        closeModal('modal-ai-strategy');
        loadStrategies();
        
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    } finally {
        confirmBtn.disabled = false;
        confirmBtn.textContent = '确认并保存策略';
    }
}

// 辅助函数：获取再平衡频率文本
function getRebalanceFreqText(freq) {
    const freqMap = {
        'daily': '每日',
        'weekly': '每周',
        'monthly': '每月',
        'quarterly': '每季度',
        'yearly': '每年'
    };
    return freqMap[freq] || freq;
}

// 辅助函数：获取策略类型文本
function getStrategyTypeText(type) {
    const typeMap = {
        'template': '模板策略',
        'custom': '自定义',
        'ai_generated': 'AI生成'
    };
    return typeMap[type] || type;
}

// ==================================================================
//  编辑策略
// ==================================================================
let currentEditStrategy = null;
let editAllocations = []; // [{code: '510300', name: '沪深300ETF', ratio: 30}]

async function editStrategy(id) {
    try {
        const res = await api(`/api/strategy/${id}`);
        const s = res.data;
        currentEditStrategy = s;
        
        // 初始化编辑配置数组
        editAllocations = [];
        if (s.allocation_config) {
            Object.entries(s.allocation_config).forEach(([code, ratio]) => {
                const etf = allQuotes.find(q => q.etf_code === code);
                editAllocations.push({
                    code: code,
                    name: etf ? etf.etf_name : code,
                    ratio: Math.round(ratio * 100)
                });
            });
        }
        
        // 填充表单
        document.getElementById('edit-strategy-id').value = s.id;
        document.getElementById('edit-name').value = s.name;
        document.getElementById('edit-desc').value = s.description || '';
        document.getElementById('edit-capital').value = s.initial_capital;
        
        // 再平衡设置
        const enableRebalance = s.rebalance_freq && s.rebalance_freq !== 'none';
        document.getElementById('edit-enable-rebalance').checked = enableRebalance;
        toggleEditRebalanceOptions();
        
        if (enableRebalance) {
            document.getElementById('edit-rebalance-freq').value = s.rebalance_freq || 'quarterly';
            document.getElementById('edit-rebalance-threshold').value = Math.round((s.rebalance_threshold || 0.05) * 100);
        }
        
        // 初始化搜索
        initEditETFSearch();
        
        // 渲染配置列表
        updateEditAllocationDisplay();
        
        openModal('modal-edit-strategy');
    } catch (e) {
        toast('加载策略失败: ' + e.message, 'error');
    }
}

function initEditETFSearch() {
    const searchInput = document.getElementById('edit-etf-search');
    const resultsDiv = document.getElementById('edit-etf-results');
    
    searchInput.value = '';
    
    const newInput = searchInput.cloneNode(true);
    searchInput.parentNode.replaceChild(newInput, searchInput);
    
    newInput.addEventListener('input', function() {
        const keyword = this.value.trim().toLowerCase();
        if (!keyword) {
            resultsDiv.classList.remove('active');
            return;
        }
        
        const matches = allQuotes.filter(etf => {
            if (editAllocations.find(a => a.code === etf.etf_code)) return false;
            return etf.etf_code.toLowerCase().includes(keyword) || 
                   etf.etf_name.toLowerCase().includes(keyword);
        }).slice(0, 10);
        
        if (matches.length > 0) {
            resultsDiv.innerHTML = matches.map(etf => `
                <div class="etf-search-item" onclick="addEditAllocation('${etf.etf_code}', '${etf.etf_name}')">
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
    
    document.addEventListener('click', function(e) {
        if (!e.target.closest('#modal-edit-strategy .etf-search-container')) {
            resultsDiv.classList.remove('active');
        }
    });
}

function addEditAllocation(code, name) {
    editAllocations.push({
        code: code,
        name: name.slice(0, 15),
        ratio: 0
    });
    
    document.getElementById('edit-etf-search').value = '';
    document.getElementById('edit-etf-results').classList.remove('active');
    
    updateEditAllocationDisplay();
}

function removeEditAllocation(code) {
    editAllocations = editAllocations.filter(a => a.code !== code);
    updateEditAllocationDisplay();
}

function updateEditAllocationRatio(code, ratio) {
    const allocation = editAllocations.find(a => a.code === code);
    if (allocation) {
        allocation.ratio = parseFloat(ratio) || 0;
    }
    updateEditAllocationDisplay();
}

function updateEditAllocationDisplay() {
    const container = document.getElementById('edit-allocation-list');
    
    if (editAllocations.length === 0) {
        container.innerHTML = '<div style="color:var(--text-secondary);font-size:14px;padding:8px;">请搜索并添加ETF，然后设置占比</div>';
        document.getElementById('edit-allocation-total').textContent = '当前占比总和: 0%';
        document.getElementById('edit-allocation-warning').textContent = '';
        return;
    }
    
    container.innerHTML = editAllocations.map(a => `
        <div style="display:flex;align-items:center;margin-bottom:8px;padding:8px;background:var(--bg-secondary);border-radius:4px;">
            <span style="flex:1;font-weight:600;">${a.code}</span>
            <span style="flex:2;color:var(--text-secondary);font-size:13px;">${a.name}</span>
            <input type="number" value="${a.ratio}" min="0" max="100" step="1"
                   onchange="updateEditAllocationRatio('${a.code}', this.value)"
                   style="width:70px;margin-right:8px;padding:4px;border:1px solid var(--border);border-radius:4px;text-align:right;">
            <span style="width:20px;color:var(--text-secondary);">%</span>
            <button class="btn btn-outline btn-sm" onclick="removeEditAllocation('${a.code}')" style="margin-left:8px;padding:2px 8px;">删除</button>
        </div>
    `).join('');
    
    const totalRatio = editAllocations.reduce((sum, a) => sum + a.ratio, 0);
    document.getElementById('edit-allocation-total').textContent = `当前占比总和: ${totalRatio.toFixed(1)}%`;
    
    const warning = document.getElementById('edit-allocation-warning');
    if (totalRatio > 100) {
        warning.textContent = '⚠️ 占比总和超过100%！';
        warning.style.color = 'var(--danger)';
    } else if (totalRatio < 100) {
        warning.textContent = `还可配置 ${(100 - totalRatio).toFixed(1)}%`;
        warning.style.color = 'var(--text-secondary)';
    } else {
        warning.textContent = '✅ 配置完整';
        warning.style.color = 'var(--success)';
    }
}

function toggleEditRebalanceOptions() {
    const enable = document.getElementById('edit-enable-rebalance').checked;
    const optionsDiv = document.getElementById('edit-rebalance-options');
    const hintText = document.getElementById('edit-rebalance-disabled-hint');
    const statusText = document.getElementById('edit-rebalance-status-text');
    
    if (enable) {
        // 启用状态
        optionsDiv.style.display = 'flex';
        optionsDiv.classList.remove('options-disabled');
        optionsDiv.classList.add('options-enabled');
        hintText.style.display = 'none';
        statusText.innerHTML = '策略将定期调整持仓以保持目标配置比例';
        statusText.style.color = 'var(--text-secondary)';
    } else {
        // 禁用状态
        optionsDiv.style.display = 'none';
        optionsDiv.classList.remove('options-enabled');
        optionsDiv.classList.add('options-disabled');
        hintText.style.display = 'block';
        statusText.innerHTML = '策略将保持初始配置，不再自动调整';
        statusText.style.color = 'var(--warning)';
    }
}

async function submitEditStrategy() {
    const id = document.getElementById('edit-strategy-id').value;
    const name = document.getElementById('edit-name').value.trim();
    const description = document.getElementById('edit-desc').value.trim();
    const initialCapital = Number(document.getElementById('edit-capital').value);
    const enableRebalance = document.getElementById('edit-enable-rebalance').checked;
    const totalRatio = editAllocations.reduce((sum, a) => sum + a.ratio, 0);
    
    if (!name) {
        toast('请输入策略名称', 'error');
        return;
    }
    
    if (editAllocations.length === 0) {
        toast('请至少添加一只ETF', 'error');
        return;
    }
    
    if (totalRatio > 100) {
        toast('占比总和不能超过100%', 'error');
        return;
    }
    
    if (totalRatio < 100) {
        toast(`占比总和为${totalRatio.toFixed(1)}%，未达到100%`, 'warning');
        return;
    }
    
    const allocationConfig = {};
    editAllocations.forEach(a => {
        allocationConfig[a.code] = a.ratio / 100;
    });
    
    const body = {
        name: name,
        description: description,
        initial_capital: initialCapital,
        allocation_config: allocationConfig,
        rebalance_freq: enableRebalance ? document.getElementById('edit-rebalance-freq').value : 'none',
        rebalance_threshold: enableRebalance ? Number(document.getElementById('edit-rebalance-threshold').value) / 100 : 0.05,
    };

    const btn = document.getElementById('edit-submit-btn');
    btn.disabled = true;
    btn.textContent = '保存中...';

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
        btn.textContent = '保存修改';
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
    
    // 计算日期范围的天数
    const start = new Date(startDate);
    const end = new Date(endDate);
    const daysLimit = Math.ceil((end - start) / (1000 * 60 * 60 * 24));
    
    if (daysLimit < 1) {
        toast('日期范围至少需要1天', 'error');
        return;
    }
    
    // 提示大量数据拉取耗时
    if (daysLimit > 365) {
        const confirmed = confirm(`您选择的时间范围较大（${daysLimit}天），批量拉取可能需要较长时间。\n\n确定继续吗？`);
        if (!confirmed) {
            return;
        }
    }
    
    const btn = document.getElementById('range-submit-btn');
    btn.disabled = true;
    btn.textContent = '从证监会拉取中...';
    
    try {
        // 使用证监会净值数据源，传递天数限制
        const res = await api(`/api/net-value/batch-update?days_limit=${daysLimit}`, { 
            method: 'POST'
        });
        toast(`证监会净值拉取完成: 成功 ${res.data.success_count}, 失败 ${res.data.fail_count}, 共 ${res.data.total} 只ETF`, 'success');
        closeModal('modal-update-range');
        // 刷新行情数据
        loadMarket();
    } catch (e) {
        toast(e.message || '拉取失败', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '开始拉取';
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
        
        // 防御性检查：确保必要字段存在
        if (!d) {
            throw new Error('回测返回数据为空');
        }
        
        document.getElementById('bt-result').style.display = 'block';

        // 统计卡片（A股习惯：上涨红色，下跌绿色）
        document.getElementById('bt-stats').innerHTML = [
            { label: '最终资产', value: '¥' + fmtNum(d.final_asset || 0), color: '' },
            { label: '总收益率', value: fmtNum(d.total_return_pct || 0) + '%', color: (d.total_return_pct || 0) >= 0 ? 'var(--danger)' : 'var(--success)' },  // ✅ 正数红色，负数绿色
            { label: '最大回撤', value: fmtNum(d.max_drawdown_pct || 0) + '%', color: 'var(--danger)' },
            { label: 'Sharpe', value: d.sharpe_ratio != null ? fmtNum(d.sharpe_ratio) : '-', color: '' },
            { label: '交易次数', value: d.trade_count || 0, color: '' },
        ].map(s => `<div class="stat-card"><div class="stat-value" style="color:${s.color || 'inherit'}">${s.value}</div><div class="stat-label">${s.label}</div></div>`).join('');

        // 清理旧的时间段收益卡片（避免重复）
        const oldPeriodCard = document.getElementById('time-period-returns-card');
        if (oldPeriodCard) {
            oldPeriodCard.remove();
        }

        // 区间收益（修改为时间段显示）
        if (d.time_period_returns && d.time_period_returns.length > 0) {
            const periodReturnsHtml = `
                <div id="time-period-returns-card" class="card" style="margin-top:16px;">
                    <div class="card-title">时间段收益</div>
                    <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(180px, 1fr));gap:12px;">
                        ${d.time_period_returns.map(p => `
                            <div style="background:var(--bg-secondary);border-radius:8px;padding:12px;text-align:center;">
                                <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:8px;">${p.period}</div>
                                <div style="font-size:20px;font-weight:700;color:${p.return_pct >= 0 ? 'var(--danger)' : 'var(--success)'};">
                                    ${fmtNum(p.return_pct)}%
                                </div>
                                <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
                                    ${p.days}天
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
            
            // 在bt-stats后面插入时间段收益卡片
            const statsDiv = document.getElementById('bt-stats');
            statsDiv.insertAdjacentHTML('afterend', periodReturnsHtml);
        }

        // 交易记录列表（可展开/折叠，默认展示最近10条）
        if (d.rebalance_records && d.rebalance_records.length > 0) {
            // 将rebalance_records转换为交易记录格式
            const allTrades = [];
            d.rebalance_records.forEach(record => {
                record.adjustments.forEach(adj => {
                    allTrades.push({
                        date: record.date,
                        etf_code: adj.etf_code,
                        direction: adj.action === '买入' ? 'buy' : 'sell',
                        price: adj.price,
                        quantity: adj.quantity,
                        amount: adj.amount,
                        reason: record.reason,
                        trigger_type: record.trigger_type
                    });
                });
            });
            
            // 按日期排序（从新到旧）
            allTrades.sort((a, b) => new Date(b.date) - new Date(a.date));
            
            // 默认展示最近10条
            const defaultShowCount = 10;
            const defaultTrades = allTrades.slice(0, defaultShowCount);
            const hasMoreTrades = allTrades.length > defaultShowCount;
            
            // 保存完整数据到全局变量
            window.allBacktestTrades = allTrades;
            window.backtestTradesExpanded = false;
            
            const tradesHtml = `
                <div class="card">
                    <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
                        <span>交易记录（共${allTrades.length}条）</span>
                        ${hasMoreTrades ? `<button class="btn btn-outline btn-sm" onclick="toggleBacktestTrades()">
                            <span id="trades-toggle-text">展开全部</span>
                        </button>` : ''}
                    </div>
                    <div class="table-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>日期</th>
                                    <th>ETF</th>
                                    <th>类型</th>
                                    <th>方向</th>
                                    <th class="text-right">价格</th>
                                    <th class="text-right">数量</th>
                                    <th class="text-right">金额</th>
                                    <th>原因</th>
                                </tr>
                            </thead>
                            <tbody id="bt-trades-tbody">
                                ${defaultTrades.map(t => `
                                    <tr>
                                        <td>${t.date}</td>
                                        <td>${t.etf_code}</td>
                                        <td><span class="badge ${t.trigger_type === 'initial' ? 'badge-template' : t.trigger_type === 'time_based' ? 'badge-success' : 'badge-ai'}">${t.trigger_type === 'initial' ? '初始' : t.trigger_type === 'time_based' ? '定期' : '偏离'}</span></td>
                                        <td><span class="${t.direction === 'buy' ? 'text-danger' : 'text-success'}">${t.direction === 'buy' ? '买入' : '卖出'}</span></td>
                                        <td class="text-right">${fmtNum(t.price, 3)}</td>
                                        <td class="text-right">${t.quantity}</td>
                                        <td class="text-right">${fmtNum(t.amount)}</td>
                                        <td style="font-size:12px;">${t.reason}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                    ${hasMoreTrades ? `<div id="trades-more-hint" style="text-align:center;padding:8px;font-size:13px;color:var(--text-secondary);">还有 ${allTrades.length - defaultShowCount} 条记录，点击上方按钮展开全部</div>` : ''}
                </div>
            `;
            
            // 替换原有的交易记录卡片
            const tradesContainer = document.getElementById('bt-trades-container');
            if (tradesContainer) {
                tradesContainer.innerHTML = tradesHtml;
            }
        } else {
            document.getElementById('bt-trades-container').innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">无交易记录</div>';
        }

        // 收益曲线（检查数据是否存在）
        if (d.daily_data && d.daily_data.length > 0) {
            renderBacktestChart(d.daily_data, d.initial_capital);
        } else {
            document.getElementById('bt-chart').innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">无每日数据</div>';
        }

        // 每日策略执行详情（已删除）
        // renderDailyDetails(d.daily_details);

        toast('回测完成', 'success');
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '开始回测';
}
}

function renderBacktestChart(dailyData, initialCapital) {
    const chartDom = document.getElementById('bt-chart');
    if (!chartDom) {
        console.error('找不到bt-chart元素');
        return;
    }
    
    // 获取或创建ECharts实例（关键：不要每次都创建新实例）
    let chart = echarts.getInstanceByDom(chartDom);
    if (!chart) {
        chart = echarts.init(chartDom);
    }
    
    const dates = dailyData.map(d => d.date);
    const assets = dailyData.map(d => d.total_asset);
    const returns = dailyData.map(d => d.profit_pct);

    // 使用 notMerge: true 强制刷新（清除旧配置）
    chart.setOption({
        tooltip: {
            trigger: 'axis',
            formatter: params => {
                if (!params || params.length === 0) return '';
                const d = params[0];
                const idx = d.dataIndex;
                return `${d.axisValue}<br/>总资产: ¥${fmtNum(assets[idx])}<br/>收益率: ${fmtNum(returns[idx])}%`;
            }
        },
        grid: { left: '8%', right: '4%', top: '10%', bottom: '12%' },
        xAxis: { 
            type: 'category', 
            data: dates, 
            axisLabel: { fontSize: 11, rotate: 0 },
            boundaryGap: false
        },
        yAxis: [
            { 
                type: 'value', 
                name: '资产(¥)', 
                axisLabel: { 
                    formatter: v => v >= 1e4 ? (v / 1e4).toFixed(0) + '万' : v 
                },
                splitLine: { lineStyle: { color: 'rgba(148,163,184,0.1)' } }
            },
        ],
        series: [
            {
                name: '总资产',
                type: 'line',
                data: assets,
                smooth: true,
                symbol: 'none',
                lineStyle: { width: 2, color: '#3b82f6' },
                areaStyle: { 
                    color: { 
                        type: 'linear', 
                        x: 0, y: 0, x2: 0, y2: 1, 
                        colorStops: [
                            { offset: 0, color: 'rgba(59,130,246,0.25)' }, 
                            { offset: 1, color: 'rgba(59,130,246,0.02)' }
                        ] 
                    } 
                },
                markLine: {
                    silent: true,
                    symbol: 'none',
                    data: [
                        { 
                            yAxis: initialCapital, 
                            label: { formatter: '初始资金', position: 'end' }, 
                            lineStyle: { color: '#94a3b8', type: 'dashed', width: 1 }
                        }
                    ],
                },
            },
        ],
    }, true);

    // 响应式调整（使用防抖）
    if (!window.backtestChartResizeHandler) {
        window.backtestChartResizeHandler = () => {
            if (chart) {
                chart.resize();
            }
        };
        window.addEventListener('resize', window.backtestChartResizeHandler);
    }
    
    // 立即resize确保图表正确渲染
    setTimeout(() => chart.resize(), 100);
}

// 展开/折叠交易记录
function toggleBacktestTrades() {
    const tbody = document.getElementById('bt-trades-tbody');
    const toggleText = document.getElementById('trades-toggle-text');
    const moreHint = document.getElementById('trades-more-hint');
    
    if (!window.allBacktestTrades) {
        return;
    }
    
    if (window.backtestTradesExpanded) {
        // 折叠：只显示最近10条
        const defaultTrades = window.allBacktestTrades.slice(0, 10);
        tbody.innerHTML = defaultTrades.map(t => `
            <tr>
                <td>${t.date}</td>
                <td>${t.etf_code}</td>
                <td><span class="badge ${t.trigger_type === 'initial' ? 'badge-template' : t.trigger_type === 'time_based' ? 'badge-success' : 'badge-ai'}">${t.trigger_type === 'initial' ? '初始' : t.trigger_type === 'time_based' ? '定期' : '偏离'}</span></td>
                <td><span class="${t.direction === 'buy' ? 'text-danger' : 'text-success'}">${t.direction === 'buy' ? '买入' : '卖出'}</span></td>
                <td class="text-right">${fmtNum(t.price, 3)}</td>
                <td class="text-right">${t.quantity}</td>
                <td class="text-right">${fmtNum(t.amount)}</td>
                <td style="font-size:12px;">${t.reason}</td>
            </tr>
        `).join('');
        
        toggleText.textContent = '展开全部';
        if (moreHint) {
            moreHint.style.display = 'block';
        }
        
        window.backtestTradesExpanded = false;
    } else {
        // 展开：显示全部
        tbody.innerHTML = window.allBacktestTrades.map(t => `
            <tr>
                <td>${t.date}</td>
                <td>${t.etf_code}</td>
                <td><span class="badge ${t.trigger_type === 'initial' ? 'badge-template' : t.trigger_type === 'time_based' ? 'badge-success' : 'badge-ai'}">${t.trigger_type === 'initial' ? '初始' : t.trigger_type === 'time_based' ? '定期' : '偏离'}</span></td>
                <td><span class="${t.direction === 'buy' ? 'text-danger' : 'text-success'}">${t.direction === 'buy' ? '买入' : '卖出'}</span></td>
                <td class="text-right">${fmtNum(t.price, 3)}</td>
                <td class="text-right">${t.quantity}</td>
                <td class="text-right">${fmtNum(t.amount)}</td>
                <td style="font-size:12px;">${t.reason}</td>
            </tr>
        `).join('');
        
        toggleText.textContent = '折叠';
        if (moreHint) {
            moreHint.style.display = 'none';
        }
        
        window.backtestTradesExpanded = true;
    }
}

// 交易记录日历表（已删除，改用列表）
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


// ==================================================================
//  更新最新净值数据（从证监会）
// ==================================================================

async function updateLatestNetValues() {
    if (!confirm('确定要从证监会拉取所有ETF的最新净值数据吗？\n预计耗时约20秒（20只ETF × 1秒）')) {
        return;
    }
    
    toast('开始拉取最新净值数据...', 'info');
    
    try {
        const res = await api('/api/net-value/batch-update?limit=20', { method: 'POST' });
        
        if (res.code === 200) {
            const data = res.data;
            toast(`净值数据更新完成！成功: ${data.success_count}, 失败: ${data.fail_count}`, 'success');
            
            // 刷新行情看板
            await loadMarket();
        } else {
            toast('更新失败: ' + res.message, 'error');
        }
    } catch (e) {
        console.error('更新净值失败:', e);
        toast('更新失败: ' + e.message, 'error');
    }
}

(async function init() {
    loadMarket();
})();


// ==================================================================
//  ETF历史净值查看
// ==================================================================

let historyChart = null;

async function showETFHistory(etfCode, etfName) {
    document.getElementById('history-etf-title').textContent = `${etfCode} ${etfName} - 净值走势`;
    openModal('modal-etf-history');
    
    document.getElementById('history-table').innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--text-secondary);">正在加载净值数据...</td></tr>';
    document.getElementById('history-empty').style.display = 'none';
    document.getElementById('history-count').textContent = '';
    
    try {
        const res = await api(`/api/net-value/history/${etfCode}`);
        const netValues = res.data?.net_values || [];
        
        if (!netValues.length) {
            document.getElementById('history-table').innerHTML = '';
            document.getElementById('history-empty').style.display = 'block';
            document.getElementById('history-count').textContent = '暂无净值数据';
            return;
        }
        
        document.getElementById('history-count').textContent = `共 ${netValues.length} 条净值记录`;
        
        // 按日期倒序排列（最近的在最前面）
        const sortedNetValues = netValues.sort((a, b) => new Date(b.trade_date) - new Date(a.trade_date));
        
        const tbody = document.getElementById('history-table');
        tbody.innerHTML = sortedNetValues.map(q => `
            <tr>
                <td>${q.trade_date}</td>
                <td class="text-right">${fmtNum(q.net_value, 4)}</td>
                <td class="text-right">${fmtPct(q.net_value_change_pct)}</td>
            </tr>
        `).join('');
        
        renderHistoryChart(netValues, etfCode, etfName);
        
    } catch (e) {
        console.error('加载净值数据失败:', e);
        document.getElementById('history-table').innerHTML = `<tr><td colspan="3" style="text-align:center;color:var(--danger);">加载失败: ${e.message}</td></tr>`;
        toast('加载净值数据失败: ' + e.message, 'error');
    }
}

function renderHistoryChart(netValues, etfCode, etfName) {
    const chartDom = document.getElementById('history-chart');
    
    if (historyChart) {
        historyChart.dispose();
    }
    
    historyChart = echarts.init(chartDom);
    
    const sortedData = netValues.sort((a, b) => new Date(a.trade_date) - new Date(b.trade_date));
    
    const dates = sortedData.map(q => q.trade_date);
    const netValueData = sortedData.map(q => q.net_value);
    
    const option = {
        title: {
            text: `${etfCode} 净值走势`,
            left: 'center',
            textStyle: { fontSize: 16, color: '#333' }
        },
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross' },
            formatter: function(params) {
                const date = params[0].axisValue;
                const netValue = params[0].data;
                const changePct = sortedData.find(q => q.trade_date === date)?.net_value_change_pct || 0;
                return `${date}<br/>净值: ${fmtNum(netValue, 4)}<br/>增长率: ${fmtNum(changePct)}%`;
            }
        },
        grid: { left: '3%', right: '4%', bottom: '3%', top: 60, containLabel: true },
        xAxis: {
            type: 'category',
            data: dates,
            boundaryGap: false,
            axisLabel: { rotate: 45, fontSize: 11 }
        },
        yAxis: {
            type: 'value',
            name: '净值',
            position: 'left',
            axisLabel: {
                formatter: function(value) {
                    return fmtNum(value, 2);
                }
            }
        },
        series: [
            {
                name: '净值',
                type: 'line',
                yAxisIndex: 0,
                data: netValueData,
                smooth: true,
                lineStyle: { width: 2, color: '#5470c6' },
                areaStyle: {
                    color: {
                        type: 'linear',
                        x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: 'rgba(84, 112, 198, 0.3)' },
                            { offset: 1, color: 'rgba(84, 112, 198, 0.05)' }
                        ]
                    }
                },
                itemStyle: { color: '#5470c6' }
            }
        ]
    };
    
    historyChart.setOption(option);
    
    window.addEventListener('resize', function() {
        if (historyChart) {
            historyChart.resize();
        }
    });
}

/* ====== AI驱动策略模块 ====== */

let currentAutoStrategyId = null;
let autoStrategyAllocations = [];

async function loadAutoStrategyPage() {
    await loadAutoStrategyOverview();
}

async function loadAutoStrategies() {
    try {
        const data = await api('/api/auto-strategy/list');
        if (data.data.strategies && data.data.strategies.length > 0) {
            currentAutoStrategyId = data.data.strategies[0].id;
            renderAutoStrategyInfo(data.data.strategies[0]);
        } else {
            currentAutoStrategyId = null;
            document.getElementById('auto-strategy-info').innerHTML =
                '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">🤖</div><div>请先创建自动策略</div></div>';
        }
        document.getElementById('auto-strategy-status').textContent = `共 ${data.data.total} 个策略`;
    } catch (e) {
        toast('获取策略列表失败', 'error');
    }
}

function renderAutoStrategyInfo(strategy) {
    const statusClass = strategy.status === 'running' ? 'running' : 'paused';
    const statusText = strategy.status === 'running' ? '运行中' : '已暂停';
    const toggleBtnText = strategy.status === 'running' ? '暂停策略' : '恢复运行';
    const toggleBtnClass = strategy.status === 'running' ? 'btn-warning' : 'btn-success';

    const allocationHtml = Object.entries(strategy.allocation || {}).map(([code, ratio]) => {
        const etf = allQuotes.find(q => q.etf_code === code);
        return `
            <div class="config-item">
                <span>
                    <span class="config-etf-code">${code}</span>
                    <span class="config-etf-name">${etf ? etf.etf_name : ''}</span>
                </span>
                <span class="config-ratio">${fmtNum(ratio * 100)}%</span>
            </div>
        `;
    }).join('');

    document.getElementById('auto-strategy-info').innerHTML = `
        <div style="padding:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <div style="font-weight:600;font-size:16px;">${strategy.name}</div>
                <span class="auto-strategy-badge ${statusClass}">${statusText}</span>
            </div>
            <div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">
                调整次数: ${strategy.adjustment_count || 0} | 最近分析: ${strategy.last_analysis || '暂无'}
            </div>
            <div style="font-size:14px;font-weight:600;margin-bottom:8px;">当前配置:</div>
            ${allocationHtml || '<div style="color:var(--text-secondary);font-size:13px;">暂无配置</div>'}
            <div style="display:flex;gap:8px;margin-top:16px;border-top:1px solid var(--border);padding-top:12px;">
                <button class="btn ${toggleBtnClass} btn-sm" onclick="toggleAutoStrategyStatus(${strategy.id}, '${strategy.status}')">${toggleBtnText}</button>
                <button class="btn btn-danger btn-sm" onclick="deleteAutoStrategy(${strategy.id})">删除策略</button>
            </div>
        </div>
    `;
}

async function toggleAutoStrategyStatus(strategyId, currentStatus) {
    try {
        const endpoint = currentStatus === 'running' ? 'pause' : 'resume';
        await api(`/api/auto-strategy/${endpoint}?strategy_id=${strategyId}`, { method: 'POST' });
        toast(currentStatus === 'running' ? '策略已暂停' : '策略已恢复运行', 'success');
        loadAutoStrategies();
    } catch (e) {
        toast('操作失败: ' + e.message, 'error');
    }
}

async function deleteAutoStrategy(strategyId) {
    const modalHtml = `
        <div class="modal-overlay active" id="modal-delete-auto-strategy">
            <div class="modal modal-confirm" style="max-width:400px;text-align:center;">
                <div style="font-size:48px;margin-bottom:16px;">⚠️</div>
                <div class="modal-title">确认删除自动策略？</div>
                <div style="color:var(--text-secondary);font-size:14px;margin:12px 0 24px;">
                    删除后无法恢复，相关执行日志和经验也会被清除
                </div>
                <div class="modal-actions" style="justify-content:center;">
                    <button class="btn btn-outline" onclick="document.getElementById('modal-delete-auto-strategy').remove();document.body.style.overflow='';">取消</button>
                    <button class="btn btn-danger" onclick="confirmDeleteAutoStrategy(${strategyId})" style="margin-left:12px;">确认删除</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    document.body.style.overflow = 'hidden';
}

async function confirmDeleteAutoStrategy(strategyId) {
    const modal = document.getElementById('modal-delete-auto-strategy');
    if (modal) {
        modal.remove();
        document.body.style.overflow = '';
    }

    try {
        await api(`/api/strategy/${strategyId}`, { method: 'DELETE' });
        toast('自动策略已删除', 'success');
        currentAutoStrategyId = null;
        loadAutoStrategies();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

async function loadSentimentSummary() {
    try {
        const data = await api('/api/auto-strategy/sentiments/summary');
        const summary = data.data;

        document.getElementById('stat-sentiment-count').textContent = summary.total || 0;
        document.getElementById('stat-sentiment-positive').textContent = summary.positive || 0;
        document.getElementById('stat-sentiment-negative').textContent = summary.negative || 0;
        document.getElementById('stat-sentiment-avg').textContent = summary.avg_score || '-';
    } catch (e) {
        console.error('获取舆情汇总失败', e);
    }
}

async function loadSentiments() {
    try {
        const data = await api('/api/auto-strategy/sentiments?days=1');
        const sentiments = data.data.sentiments || [];

        if (sentiments.length === 0) {
            document.getElementById('sentiment-list').innerHTML =
                '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">📡</div><div>点击"采集舆情"获取数据</div></div>';
            return;
        }

        const sourceLabels = {
            'eastmoney': '东财',
            'cls': '财联社',
            'ths': '同花顺',
            'sina': '新浪',
            'jin10': '金十',
            'eastmoney_hot': '东财热点',
            'eastmoney_keyword': '东财关键词'
        };

        const html = sentiments.map(s => {
            const scoreClass = s.sentiment_score > 0 ? 'text-success' : s.sentiment_score < 0 ? 'text-danger' : '';
            const label = s.sentiment_label === 'positive' ? '✅' : s.sentiment_label === 'negative' ? '⚠️' : '➖';
            const sourceLabel = sourceLabels[s.source] || s.source;
            return `
                <div class="config-item" style="margin-bottom:8px;">
                    <span style="flex:1;">
                        <span class="badge" style="font-size:11px;margin-right:6px;background:var(--primary);color:white;">${sourceLabel}</span>
                        ${label} ${s.title || s.content?.substring(0, 50) || 'N/A'}
                    </span>
                    <span class="${scoreClass}" style="font-weight:600;">${s.sentiment_score?.toFixed(2) || '-'}</span>
                </div>
            `;
        }).join('');

        document.getElementById('sentiment-list').innerHTML = html;
    } catch (e) {
        toast('获取舆情数据失败', 'error');
    }
}

async function loadExecutionLogs(strategyId) {
    try {
        const data = await api(`/api/auto-strategy/logs?strategy_id=${strategyId}&days=7`);
        const logs = data.data.logs || [];

        if (logs.length === 0) {
            document.getElementById('auto-strategy-logs').innerHTML =
                '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">📋</div><div>暂无执行日志</div></div>';
            return;
        }

        const html = logs.map(log => {
            const statusIcon = log.status === 'success' ? '✅' : log.status === 'skipped' ? '⏭️' : '❌';
            return `
                <div class="config-item" style="margin-bottom:8px;">
                    <span>${statusIcon} ${log.log_date} - ${log.action_type}</span>
                    <span style="color:var(--text-secondary);font-size:12px;">${log.safety_reason || ''}</span>
                </div>
            `;
        }).join('');

        document.getElementById('auto-strategy-logs').innerHTML = html;
    } catch (e) {
        console.error('获取执行日志失败', e);
    }
}

async function loadExperiences(strategyId) {
    try {
        const data = await api(`/api/auto-strategy/experiences?strategy_id=${strategyId}`);
        const experiences = data.data.experiences || [];

        if (experiences.length === 0) {
            document.getElementById('experience-list').innerHTML =
                '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">💡</div><div>暂无历史经验</div></div>';
            return;
        }

        const html = experiences.map(exp => {
            const typeIcon = exp.experience_type === 'success' ? '✅' : exp.experience_type === 'failure' ? '⚠️' : '💡';
            const score = exp.effectiveness_score?.toFixed(1) || 'N/A';
            return `
                <div class="config-item" style="margin-bottom:8px;">
                    <span style="flex:1;">${typeIcon} ${exp.title}</span>
                    <span style="font-size:12px;color:var(--text-secondary);">评分: ${score}</span>
                </div>
            `;
        }).join('');

        document.getElementById('experience-list').innerHTML = html;
    } catch (e) {
        console.error('获取经验失败', e);
    }
}

// ---- 创建自动策略弹窗 ----

function showCreateAutoStrategyModal() {
    autoStrategyAllocations = [];
    document.getElementById('as-name').value = 'AI自动策略';
    document.getElementById('as-capital').value = '100000';
    document.getElementById('as-max-adjustments').value = '1';
    document.getElementById('as-enable-memory').checked = true;
    document.getElementById('as-etf-search').value = '';
    updateAutoStrategyAllocationDisplay();
    initAutoStrategyETFSearch();
    openModal('modal-create-auto-strategy');
}

function initAutoStrategyETFSearch() {
    const searchInput = document.getElementById('as-etf-search');
    const resultsDiv = document.getElementById('as-etf-results');

    searchInput.value = '';
    const newInput = searchInput.cloneNode(true);
    searchInput.parentNode.replaceChild(newInput, searchInput);

    newInput.addEventListener('input', function() {
        const keyword = this.value.trim().toLowerCase();
        if (!keyword) {
            resultsDiv.classList.remove('active');
            return;
        }

        const matches = allQuotes.filter(etf => {
            if (autoStrategyAllocations.find(a => a.code === etf.etf_code)) return false;
            return etf.etf_code.toLowerCase().includes(keyword) ||
                   etf.etf_name.toLowerCase().includes(keyword);
        }).slice(0, 10);

        if (matches.length > 0) {
            resultsDiv.innerHTML = matches.map(etf => `
                <div class="etf-search-item" onclick="addAutoStrategyAllocation('${etf.etf_code}', '${etf.etf_name}')">
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

    document.addEventListener('click', function(e) {
        if (!e.target.closest('#modal-create-auto-strategy .etf-search-container')) {
            resultsDiv.classList.remove('active');
        }
    });
}

function addAutoStrategyAllocation(code, name) {
    autoStrategyAllocations.push({
        code: code,
        name: name.slice(0, 15),
        ratio: 0
    });

    document.getElementById('as-etf-search').value = '';
    document.getElementById('as-etf-results').classList.remove('active');
    updateAutoStrategyAllocationDisplay();
}

function removeAutoStrategyAllocation(code) {
    autoStrategyAllocations = autoStrategyAllocations.filter(a => a.code !== code);
    updateAutoStrategyAllocationDisplay();
}

function updateAutoStrategyAllocationRatio(code, ratio) {
    const allocation = autoStrategyAllocations.find(a => a.code === code);
    if (allocation) {
        allocation.ratio = parseFloat(ratio) || 0;
    }
    updateAutoStrategyAllocationDisplay();
}

function updateAutoStrategyAllocationDisplay() {
    const container = document.getElementById('as-allocation-list');

    if (autoStrategyAllocations.length === 0) {
        container.innerHTML = '<div style="color:var(--text-secondary);font-size:14px;padding:8px;">留空将使用默认均衡配置</div>';
        document.getElementById('as-allocation-total').textContent = '当前占比总和: 0%';
        document.getElementById('as-allocation-warning').textContent = '';
        return;
    }

    container.innerHTML = autoStrategyAllocations.map(a => `
        <div style="display:flex;align-items:center;margin-bottom:8px;padding:8px;background:var(--bg-secondary);border-radius:4px;">
            <span style="flex:1;font-weight:600;">${a.code}</span>
            <span style="flex:2;color:var(--text-secondary);font-size:13px;">${a.name}</span>
            <input type="number" value="${a.ratio}" min="0" max="100" step="1"
                   onchange="updateAutoStrategyAllocationRatio('${a.code}', this.value)"
                   style="width:70px;margin-right:8px;padding:4px;border:1px solid var(--border);border-radius:4px;text-align:right;">
            <span style="width:20px;color:var(--text-secondary);">%</span>
            <button class="btn btn-outline btn-sm" onclick="removeAutoStrategyAllocation('${a.code}')" style="margin-left:8px;padding:2px 8px;">删除</button>
        </div>
    `).join('');

    const totalRatio = autoStrategyAllocations.reduce((sum, a) => sum + a.ratio, 0);
    document.getElementById('as-allocation-total').textContent = `当前占比总和: ${totalRatio.toFixed(1)}%`;

    const warning = document.getElementById('as-allocation-warning');
    if (totalRatio > 100) {
        warning.textContent = '⚠️ 占比总和超过100%！';
        warning.style.color = 'var(--danger)';
    } else if (totalRatio < 100) {
        warning.textContent = `还可配置 ${(100 - totalRatio).toFixed(1)}%`;
        warning.style.color = 'var(--text-secondary)';
    } else {
        warning.textContent = '✅ 配置完整';
        warning.style.color = 'var(--success)';
    }
}

async function submitCreateAutoStrategy() {
    const name = document.getElementById('as-name').value.trim();
    if (!name) {
        toast('请输入策略名称', 'warning');
        return;
    }

    let initialAllocation = null;
    if (autoStrategyAllocations.length > 0) {
        const totalRatio = autoStrategyAllocations.reduce((sum, a) => sum + a.ratio, 0);
        if (Math.abs(totalRatio - 100) > 0.1) {
            toast(`ETF配置占比总和需等于100%，当前总和${totalRatio.toFixed(1)}%`, 'error');
            return;
        }
        initialAllocation = {};
        autoStrategyAllocations.forEach(a => {
            initialAllocation[a.code] = a.ratio / 100;
        });
    }

    const btn = document.getElementById('as-submit-btn');
    btn.disabled = true;
    btn.textContent = '创建中...';

    try {
        const data = await api('/api/auto-strategy/create', {
            method: 'POST',
            body: JSON.stringify({
                name: name,
                initial_allocation: initialAllocation,
                initial_capital: Number(document.getElementById('as-capital').value),
                max_daily_adjustments: Number(document.getElementById('as-max-adjustments').value),
                enable_memory: document.getElementById('as-enable-memory').checked,
            }),
        });
        toast('自动策略创建成功', 'success');
        currentAutoStrategyId = data.data.strategy_id;
        closeModal('modal-create-auto-strategy');
        loadAutoStrategies();
    } catch (e) {
        toast('创建失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '创建策略';
    }
}

async function loadSentimentSummary() {
    try {
        const data = await api('/api/auto-strategy/sentiments/summary');
        const summary = data.data;

        document.getElementById('stat-sentiment-count').textContent = summary.total || 0;
        document.getElementById('stat-sentiment-positive').textContent = summary.positive || 0;
        document.getElementById('stat-sentiment-negative').textContent = summary.negative || 0;
        document.getElementById('stat-sentiment-avg').textContent = summary.avg_score || '-';
    } catch (e) {
        console.error('获取舆情汇总失败', e);
    }
}

async function triggerSentimentCollect() {
    try {
        toast('开始采集舆情...', 'info');
        const data = await api('/api/auto-strategy/trigger-collect', { method: 'POST' });
        toast(`舆情采集完成: ${data.data.news_count}条`, 'success');
        
        loadSentimentSummary();
        loadSentimentSummaryForTable();
        loadSentimentTable();
    } catch (e) {
        toast('采集失败: ' + e.message, 'error');
    }
}

async function triggerMarketAnalyze() {
    if (!currentAutoStrategyId) {
        toast('请先创建自动策略', 'warning');
        return;
    }

    try {
        toast('开始AI分析...', 'info');
        const data = await api(`/api/auto-strategy/trigger-analyze?strategy_id=${currentAutoStrategyId}`, { method: 'POST' });
        toast(`AI分析完成: ${data.data.market_sentiment}`, 'success');
        loadAutoStrategies();
    } catch (e) {
        toast('分析失败: ' + e.message, 'error');
    }
}

async function triggerReview() {
    if (!currentAutoStrategyId) {
        toast('请先创建自动策略', 'warning');
        return;
    }

    try {
        toast('开始复盘...', 'info');
        const data = await api(`/api/auto-strategy/trigger-review?strategy_id=${currentAutoStrategyId}&review_type=weekly`, { method: 'POST' });
        toast(`复盘完成: 生成${data.data.experiences_generated}条经验`, 'success');
        loadExperiences(currentAutoStrategyId);
        
        if (data.data.review_report) {
            renderReviewReport(data.data.review_report);
        }
    } catch (e) {
        toast('复盘失败: ' + e.message, 'error');
    }
}

async function loadReviewReport() {
    if (!currentAutoStrategyId) {
        document.getElementById('review-report-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">📊</div><div>请先创建自动策略</div></div>';
        return;
    }

    try {
        const data = await api(`/api/auto-strategy/review-report?strategy_id=${currentAutoStrategyId}&review_type=weekly`);
        renderReviewReport(data.data);
    } catch (e) {
        document.getElementById('review-report-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">📊</div><div>获取报告失败: ' + e.message + '</div></div>';
    }
}

function renderReviewReport(report) {
    if (!report) {
        document.getElementById('review-report-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">📊</div><div>暂无复盘报告</div></div>';
        return;
    }

    const period = report.period || {};
    const stats = report.statistics || {};
    const sentiment = report.sentiment_analysis || {};
    const cases = report.cases || {};
    const experiences = report.generated_experiences || [];
    const summary = report.summary || '';

    let html = `
        <div style="padding:12px;background:linear-gradient(135deg, rgba(59,130,246,0.08) 0%, rgba(16,185,129,0.08) 100%);border-radius:8px;margin-bottom:16px;">
            <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">
                复盘周期: ${period.start_date || '-'} ~ ${period.end_date || '-'} (${period.days || 0}天)
            </div>
            <div style="font-size:15px;font-weight:600;color:var(--text);">${summary}</div>
        </div>

        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px;">
            <div class="stat-mini">
                <div class="stat-mini-value">${stats.total_executions || 0}</div>
                <div class="stat-mini-label">执行次数</div>
            </div>
            <div class="stat-mini">
                <div class="stat-mini-value" style="color:var(--success);">${stats.success_count || 0}</div>
                <div class="stat-mini-label">成功次数</div>
            </div>
            <div class="stat-mini">
                <div class="stat-mini-value" style="color:${stats.success_rate >= 50 ? 'var(--success)' : 'var(--danger)'};">${stats.success_rate || 0}%</div>
                <div class="stat-mini-label">成功率</div>
            </div>
            <div class="stat-mini">
                <div class="stat-mini-value">${stats.avg_return >= 0 ? '+' : ''}${stats.avg_return || 0}%</div>
                <div class="stat-mini-label">平均收益</div>
            </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
            <div>
                <div style="font-size:14px;font-weight:600;margin-bottom:8px;color:var(--danger);">⚠️ 失败案例</div>
                <div style="max-height:150px;overflow-y:auto;">
                    ${(cases.failures || []).map(c => `
                        <div style="padding:8px;background:rgba(239,68,68,0.05);border-radius:4px;margin-bottom:4px;font-size:13px;">
                            <div style="color:var(--text-secondary);">${c.date || '-'}</div>
                            <div style="color:var(--danger);">${c.reason || '未知原因'}</div>
                        </div>
                    `).join('') || '<div style="color:var(--text-secondary);font-size:13px;">本周无失败案例 ✅</div>'}
                </div>
            </div>
            <div>
                <div style="font-size:14px;font-weight:600;margin-bottom:8px;color:var(--success);">✅ 成功案例</div>
                <div style="max-height:150px;overflow-y:auto;">
                    ${(cases.successes || []).map(c => `
                        <div style="padding:8px;background:rgba(16,185,129,0.05);border-radius:4px;margin-bottom:4px;font-size:13px;">
                            <div style="color:var(--text-secondary);">${c.date || '-'}</div>
                            <div style="color:var(--success);">${c.analysis?.suggested_action || '调整成功'}</div>
                        </div>
                    `).join('') || '<div style="color:var(--text-secondary);font-size:13px;">本周无成功调整案例</div>'}
                </div>
            </div>
        </div>

        <div style="margin-bottom:12px;">
            <div style="font-size:14px;font-weight:600;margin-bottom:8px;">📈 舆情环境</div>
            <div style="display:flex;gap:16px;font-size:13px;color:var(--text-secondary);">
                <span>平均评分: ${sentiment.avg_score?.toFixed(2) || 'N/A'}</span>
                <span>正面占比: ${(sentiment.positive_ratio * 100)?.toFixed(1) || 0}%</span>
            </div>
        </div>

        <div>
            <div style="font-size:14px;font-weight:600;margin-bottom:8px;">💡 生成的经验</div>
            ${experiences.length > 0 ? experiences.map(exp => `
                <div style="padding:10px;background:rgba(255,255,255,0.5);border-radius:6px;margin-bottom:6px;border-left:3px solid ${exp.type === 'success' ? 'var(--success)' : exp.type === 'failure' ? 'var(--danger)' : 'var(--primary)'};">
                    <div style="font-size:14px;font-weight:500;">${exp.type === 'success' ? '✅' : exp.type === 'failure' ? '⚠️' : '💡'} ${exp.title || '经验'}</div>
                    ${exp.key_insight ? `<div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">"${exp.key_insight}"</div>` : ''}
                </div>
            `).join('') : '<div style="color:var(--text-secondary);font-size:13px;">暂无生成经验</div>'}
        </div>
    `;

    document.getElementById('review-report-content').innerHTML = html;
}

/* ====== 增强功能展示模块 ====== */

async function loadEnhancedData() {
    if (!currentAutoStrategyId) {
        toast('请先创建自动策略', 'warning');
        return;
    }
    
    toast('正在加载增强数据...', 'info');
    
    await Promise.all([
        loadTechnicalIndicators(),
        loadMarketEnvironment(),
        loadRiskDashboard(),
        loadSmartExperiences(),
        loadAnomalies(),
    ]);
    
    toast('增强数据加载完成', 'success');
}

async function loadTechnicalIndicators() {
    if (!currentAutoStrategyId) {
        return;
    }
    
    try {
        const strategy = await api(`/api/auto-strategy/status?strategy_id=${currentAutoStrategyId}`);
        const allocation = strategy.data.current_allocation || {};
        const etfCodes = Object.keys(allocation);
        
        if (etfCodes.length === 0) {
            return;
        }
        
        const code = etfCodes[0];
        const data = await api(`/api/auto-strategy/enhanced/technical-indicators?etf_code=${code}`);
        renderTechnicalIndicators(data.data, code);
    } catch (e) {
        console.error('加载技术指标失败', e);
    }
}

function renderTechnicalIndicators(indicators, etfCode) {
    if (!indicators || indicators.error) {
        document.getElementById('technical-indicators-detail').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">📈</div><div>数据不足</div></div>';
        return;
    }
    
    const ma = indicators.ma || {};
    const macd = indicators.macd || {};
    const rsi = indicators.rsi || {};
    const bollinger = indicators.bollinger || {};
    const trend = indicators.trend_signal || {};
    
    const html = `
        <div style="padding:16px;">
            <!-- MA均线 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:12px;">📊 MA均线</div>
                <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;">
                    ${Object.entries(ma).map(([key, val]) => `
                        <div style="padding:12px;background:var(--bg-secondary);border-radius:6px;">
                            <div style="font-size:12px;color:var(--text-secondary);">${key.toUpperCase()}</div>
                            <div style="font-size:16px;font-weight:600;">${val.value?.toFixed(4) || 'N/A'}</div>
                            <div style="font-size:12px;color:${val.price_position === 'above' ? 'var(--success)' : 'var(--danger)'};">
                                ${val.price_position === 'above' ? '↗ 价格上方' : '↘ 价格下方'} (${val.distance_pct}%)
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
            
            <!-- MACD -->
            <div style="margin-bottom:16px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:12px;">📈 MACD指标</div>
                <div style="padding:12px;background:var(--bg-secondary);border-radius:6px;">
                    <div style="display:flex;gap:16px;margin-bottom:8px;">
                        <span>MACD: <strong>${macd.macd?.toFixed(4) || 'N/A'}</strong></span>
                        <span>信号: <strong>${macd.signal?.toFixed(4) || 'N/A'}</strong></span>
                        <span>柱状: <strong>${macd.histogram?.toFixed(4) || 'N/A'}</strong></span>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <div style="font-size:14px;font-weight:600;color:${macd.trend === 'bullish' ? 'var(--success)' : 'var(--danger)'};">
                            ${macd.trend === 'bullish' ? '金叉 ↗' : '死叉 ↘'}
                        </div>
                        <div style="font-size:12px;color:var(--text-secondary);">(${macd.strength || 'neutral'})</div>
                    </div>
                </div>
            </div>
            
            <!-- RSI -->
            <div style="margin-bottom:16px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:12px;">⚡ RSI指标</div>
                <div style="padding:12px;background:var(--bg-secondary);border-radius:6px;display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-size:24px;font-weight:700;">${rsi.value || 'N/A'}</div>
                        <div style="font-size:12px;color:var(--text-secondary);">相对强弱指数</div>
                    </div>
                    <div style="padding:8px 16px;border-radius:6px;background:${rsi.signal === 'overbought' ? 'rgba(239,68,68,0.1)' : rsi.signal === 'oversold' ? 'rgba(16,185,129,0.1)' : 'var(--bg)'};">
                        <div style="font-size:14px;font-weight:600;color:${rsi.signal === 'overbought' ? 'var(--danger)' : rsi.signal === 'oversold' ? 'var(--success)' : 'var(--text)'};">
                            ${rsi.signal === 'overbought' ? '超买 ⚠️' : rsi.signal === 'oversold' ? '超卖 ✅' : '中性'}
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 布林带 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:12px;">📊 布林带</div>
                <div style="padding:12px;background:var(--bg-secondary);border-radius:6px;">
                    <div style="font-size:13px;display:flex;gap:16px;margin-bottom:8px;">
                        <span>上轨: ${bollinger.upper?.toFixed(4) || 'N/A'}</span>
                        <span>中轨: ${bollinger.middle?.toFixed(4) || 'N/A'}</span>
                        <span>下轨: ${bollinger.lower?.toFixed(4) || 'N/A'}</span>
                    </div>
                    <div style="font-size:12px;color:var(--text-secondary);">
                        带宽: ${bollinger.bandwidth?.toFixed(2) || 'N/A'}% | 位置: ${bollinger.position_pct?.toFixed(1) || 'N/A'}%
                    </div>
                </div>
            </div>
            
            <!-- 综合趋势 -->
            <div style="padding:16px;background:linear-gradient(135deg, rgba(59,130,246,0.1) 0%, rgba(16,185,129,0.1) 100%);border-radius:8px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:8px;">🎯 综合趋势判断</div>
                <div style="font-size:20px;font-weight:700;color:${trend.trend?.includes('bullish') ? 'var(--success)' : trend.trend?.includes('bearish') ? 'var(--danger)' : 'var(--text)'};">
                    ${trend.trend === 'strong_bullish' ? '强势多头 ↗↗' : trend.trend === 'bullish' ? '多头 ↗' : trend.trend === 'strong_bearish' ? '强势空头 ↘↘' : trend.trend === 'bearish' ? '空头 ↘' : '震荡 ↔'}
                </div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:8px;">
                    看多信号: ${trend.bullish_signals || 0} | 看空信号: ${trend.bearish_signals || 0} | 置信度: ${trend.confidence || 0}%
                </div>
            </div>
        </div>
    `;
    
    document.getElementById('technical-indicators-detail').innerHTML = html;
}

async function loadMarketEnvironment() {
    try {
        const sentiment = await api('/api/auto-strategy/enhanced/market-sentiment-index');
        const regime = await api('/api/auto-strategy/enhanced/market-regime');
        
        renderMarketEnvironment(sentiment.data, regime.data);
    } catch (e) {
        document.getElementById('market-environment-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">🌍</div><div>加载失败: ' + e.message + '</div></div>';
    }
}

function renderMarketEnvironment(sentiment, regime) {
    const html = `
        <div style="padding:12px;">
            <!-- 市场情绪指数 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:12px;">📈 市场情绪指数</div>
                <div style="padding:16px;background:linear-gradient(135deg, ${sentiment.index > 60 ? 'rgba(16,185,129,0.1)' : sentiment.index < 40 ? 'rgba(239,68,68,0.1)' : 'rgba(59,130,246,0.1)'} 0%, rgba(255,255,255,0.5) 100%);border-radius:8px;text-align:center;">
                    <div style="font-size:32px;font-weight:700;color:${sentiment.index > 60 ? 'var(--success)' : sentiment.index < 40 ? 'var(--danger)' : 'var(--primary)'};">
                        ${sentiment.index || 50}
                    </div>
                    <div style="font-size:13px;color:var(--text-secondary);margin-top:6px;">
                        ${sentiment.label === 'extreme_greed' ? '极度贪婪 😱' : sentiment.label === 'greed' ? '贪婪 🙂' : sentiment.label === 'neutral' ? '中性 😐' : sentiment.label === 'fear' ? '恐惧 😟' : '极度恐慌 😨'}
                    </div>
                    <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">
                        趋势: ${sentiment.trend === 'improving' ? '改善 ↗' : sentiment.trend === 'deteriorating' ? '恶化 ↘' : '稳定 ↔'}
                    </div>
                </div>
                
                <!-- 情绪构成 -->
                <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px;">
                    <div style="padding:8px;background:rgba(255,255,255,0.5);border-radius:4px;text-align:center;">
                        <div style="font-size:16px;font-weight:600;color:var(--success);">${sentiment.components?.positive_ratio?.toFixed(1) || 0}%</div>
                        <div style="font-size:11px;color:var(--text-secondary);">正面舆情</div>
                    </div>
                    <div style="padding:8px;background:rgba(255,255,255,0.5);border-radius:4px;text-align:center;">
                        <div style="font-size:16px;font-weight:600;color:var(--danger);">${sentiment.components?.negative_ratio?.toFixed(1) || 0}%</div>
                        <div style="font-size:11px;color:var(--text-secondary);">负面舆情</div>
                    </div>
                    <div style="padding:8px;background:rgba(255,255,255,0.5);border-radius:4px;text-align:center;">
                        <div style="font-size:16px;font-weight:600;">${sentiment.components?.total_news || 0}</div>
                        <div style="font-size:11px;color:var(--text-secondary);">总新闻数</div>
                    </div>
                </div>
                
                <!-- 关键因素 -->
                ${sentiment.key_factors?.length > 0 ? `
                    <div style="margin-top:12px;">
                        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">关键影响因素:</div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;">
                            ${sentiment.key_factors.slice(0, 5).map(factor => `
                                <span style="padding:4px 8px;background:rgba(59,130,246,0.1);border-radius:4px;font-size:11px;">${factor}</span>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}
            </div>
            
            <!-- 市场阶段 -->
            <div style="padding:12px;background:linear-gradient(135deg, rgba(99,102,241,0.1) 0%, rgba(168,85,247,0.1) 100%);border-radius:8px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:8px;">🎯 市场阶段识别</div>
                <div style="font-size:16px;font-weight:700;margin-bottom:8px;">
                    ${regime.regime === 'bull_quiet' ? '牛市平稳期 ↗' : regime.regime === 'bull_volatile' ? '牛市动荡期 ↗⚠️' : regime.regime === 'bear_quiet' ? '熊市平稳期 ↘' : regime.regime === 'bear_panic' ? '熊市恐慌期 ↘😱' : regime.regime === 'crisis' ? '危机模式 🚨' : '震荡整理期 ↔'}
                </div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">
                    置信度: ${regime.confidence || 0}%
                </div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;">
                    ${regime.characteristics?.map(c => `
                        <span style="padding:4px 8px;background:rgba(255,255,255,0.5);border-radius:4px;font-size:11px;">${c}</span>
                    `).join('')}
                </div>
                <div style="padding:8px;background:rgba(255,255,255,0.3);border-radius:4px;font-size:12px;color:var(--text-secondary);">
                    💡 建议: ${regime.suggested_action || '谨慎操作'}
                </div>
            </div>
        </div>
    `;
    
    document.getElementById('market-environment-content').innerHTML = html;
}

async function loadRiskDashboard() {
    if (!currentAutoStrategyId) {
        document.getElementById('risk-dashboard-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">🛡️</div><div>请先创建策略</div></div>';
        return;
    }
    
    try {
        const data = await api(`/api/auto-strategy/enhanced/risk-dashboard?strategy_id=${currentAutoStrategyId}`);
        renderRiskDashboard(data.data);
    } catch (e) {
        document.getElementById('risk-dashboard-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">🛡️</div><div>加载失败: ' + e.message + '</div></div>';
    }
}

function renderRiskDashboard(dashboard) {
    const level = dashboard.overall_risk_level || 'unknown';
    const circuit = dashboard.circuit_breaker || {};
    const drawdown = dashboard.drawdown_protection || {};
    const budget = dashboard.risk_budget || {};
    const stress = dashboard.stress_test_summary || {};
    const alerts = dashboard.risk_alerts || [];
    
    const levelColor = level === 'critical' ? 'var(--danger)' : level === 'high' ? '#f59e0b' : level === 'medium' ? 'var(--primary)' : 'var(--success)';
    
    const html = `
        <div style="padding:16px;">
            <!-- 总体风险等级 -->
            <div style="padding:20px;background:linear-gradient(135deg, ${level === 'critical' ? 'rgba(239,68,68,0.15)' : level === 'high' ? 'rgba(245,158,11,0.15)' : level === 'medium' ? 'rgba(59,130,246,0.15)' : 'rgba(16,185,129,0.15)'} 0%, rgba(255,255,255,0.5) 100%);border-radius:12px;text-align:center;margin-bottom:16px;">
                <div style="font-size:28px;margin-bottom:8px;">${level === 'critical' ? '🚨' : level === 'high' ? '⚠️' : level === 'medium' ? '⚡' : '✅'}</div>
                <div style="font-size:20px;font-weight:700;color:${levelColor};">
                    ${level === 'critical' ? '高风险' : level === 'high' ? '较高风险' : level === 'medium' ? '中等风险' : '低风险'}
                </div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">整体风险等级</div>
            </div>
            
            <!-- 风险警报 -->
            ${alerts.length > 0 ? `
                <div style="padding:12px;background:rgba(239,68,68,0.1);border-radius:8px;margin-bottom:16px;border-left:3px solid var(--danger);">
                    <div style="font-size:14px;font-weight:600;margin-bottom:8px;">⚠️ 风险警报</div>
                    ${alerts.map(alert => `
                        <div style="font-size:13px;margin-bottom:4px;">${alert}</div>
                    `).join('')}
                </div>
            ` : ''}
            
            <!-- 风险模块 -->
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px;">
                <!-- 熔断状态 -->
                <div style="padding:12px;background:rgba(255,255,255,0.5);border-radius:8px;">
                    <div style="font-size:13px;font-weight:600;margin-bottom:6px;">🛑 熔断状态</div>
                    <div style="font-size:15px;font-weight:700;color:${circuit.status === 'triggered' ? 'var(--danger)' : 'var(--success)'};">
                        ${circuit.status === 'triggered' ? '已触发' : '正常'}
                    </div>
                    ${circuit.status === 'triggered' ? `
                        <div style="font-size:11px;color:var(--danger);margin-top:4px;">${circuit.reason || '未知原因'}</div>
                        <div style="font-size:10px;color:var(--text-secondary);">冷静期: ${circuit.cooldown_days || 0}天</div>
                    ` : `
                        <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">未触发熔断条件</div>
                    `}
                </div>
                
                <!-- 回撤保护 -->
                <div style="padding:12px;background:rgba(255,255,255,0.5);border-radius:8px;">
                    <div style="font-size:13px;font-weight:600;margin-bottom:6px;">📉 回撤监控</div>
                    <div style="font-size:15px;font-weight:700;color:${drawdown.status === 'critical' ? 'var(--danger)' : drawdown.status === 'warning' ? '#f59e0b' : 'var(--success)'};">
                        ${drawdown.drawdown_pct?.toFixed(2) || 0}% ${drawdown.status === 'critical' ? '⚠️' : drawdown.status === 'warning' ? '⚡' : ''}
                    </div>
                    <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">
                        ${drawdown.status === 'critical' ? '临界回撤' : drawdown.status === 'warning' ? '警戒回撤' : drawdown.status === 'alert' ? '预警回撤' : '安全范围'}
                    </div>
                </div>
                
                <!-- 风险预算 -->
                <div style="padding:12px;background:rgba(255,255,255,0.5);border-radius:8px;">
                    <div style="font-size:13px;font-weight:600;margin-bottom:6px;">📊 风险预算</div>
                    <div style="font-size:15px;font-weight:700;color:${budget.status === 'violation' ? 'var(--danger)' : 'var(--success)'};">
                        ${budget.status === 'violation' ? '违规' : '合规'}
                    </div>
                    <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">
                        最大持仓: ${budget.max_single_position?.toFixed(2) || 0}%
                    </div>
                </div>
            </div>
            
            <!-- 压力测试 -->
            <div style="padding:12px;background:rgba(99,102,241,0.05);border-radius:8px;">
                <div style="font-size:13px;font-weight:600;margin-bottom:8px;">💪 压力测试结果</div>
                <div style="display:flex;justify-content:space-between;align-items:center;padding:8px;background:rgba(255,255,255,0.5);border-radius:4px;margin-bottom:8px;">
                    <div>
                        <div style="font-size:11px;color:var(--text-secondary);">最坏场景</div>
                        <div style="font-size:14px;font-weight:600;">${stress.worst_case?.scenario || '市场暴跌'}</div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:11px;color:var(--text-secondary);">预估损失</div>
                        <div style="font-size:16px;font-weight:700;color:var(--danger);">
                            ${stress.worst_case?.shock_pct?.toFixed(0) || 0}%
                        </div>
                    </div>
                </div>
                <div style="font-size:12px;color:var(--text-secondary);">
                    ${stress.risk_assessment || '风险适中'}
                </div>
            </div>
        </div>
    `;
    
    document.getElementById('risk-dashboard-content').innerHTML = html;
}

async function loadSmartExperiences() {
    if (!currentAutoStrategyId) {
        document.getElementById('smart-experience-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">💡</div><div>请先创建策略</div></div>';
        return;
    }
    
    try {
        const data = await api(`/api/auto-strategy/enhanced/smart-experience-match?strategy_id=${currentAutoStrategyId}`, { method: 'POST' });
        renderSmartExperiences(data.data);
    } catch (e) {
        document.getElementById('smart-experience-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">💡</div><div>加载失败: ' + e.message + '</div></div>';
    }
}

function renderSmartExperiences(data) {
    const scenario = data.current_scenario || {};
    const matched = data.matched_experiences || [];
    
    const html = `
        <div style="padding:12px;">
            <!-- 当前场景 -->
            <div style="padding:12px;background:rgba(59,130,246,0.05);border-radius:8px;margin-bottom:16px;">
                <div style="font-size:13px;font-weight:600;margin-bottom:6px;">🎯 当前市场场景</div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    ${scenario.scenario_tags?.map(tag => `
                        <span style="padding:4px 8px;background:rgba(59,130,246,0.1);border-radius:4px;font-size:11px;">${tag}</span>
                    `).join('') || '<span style="color:var(--text-secondary);font-size:12px;">暂无场景标签</span>'}
                </div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:8px;">
                    情绪: ${scenario.sentiment_level || '未知'} | 趋势: ${scenario.trend_hint || '未知'}
                </div>
            </div>
            
            <!-- 匹配的经验 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:8px;">
                    匹配经验 (共 ${data.total_matched || 0} 条)
                </div>
                ${matched.length > 0 ? matched.slice(0, 5).map(m => `
                    <div style="padding:12px;background:rgba(255,255,255,0.5);border-radius:8px;margin-bottom:8px;border-left:3px solid ${m.experience?.experience_type === 'failure' ? 'var(--danger)' : m.experience?.experience_type === 'success' ? 'var(--success)' : 'var(--primary)'};">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                            <div style="font-size:14px;font-weight:600;">
                                ${m.experience?.experience_type === 'failure' ? '⚠️' : m.experience?.experience_type === 'success' ? '✅' : '💡'} 
                                ${m.experience?.title || '经验'}
                            </div>
                            <div style="font-size:12px;background:rgba(59,130,246,0.1);padding:4px 8px;border-radius:4px;">
                                相似度: ${m.scenario_similarity?.toFixed(2) || 0}
                            </div>
                        </div>
                        ${m.experience?.key_insight ? `
                            <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">"${m.experience.key_insight}"</div>
                        ` : ''}
                        <div style="display:flex;gap:8px;font-size:11px;color:var(--text-secondary);">
                            <span>权重: ${m.adjusted_weight?.toFixed(2) || 1}</span>
                            ${m.tags_matched?.length > 0 ? `<span>匹配标签: ${m.tags_matched.join(', ')}</span>` : ''}
                        </div>
                    </div>
                `).join('') : `
                    <div style="padding:12px;background:rgba(255,255,255,0.5);border-radius:8px;text-align:center;">
                        <div style="font-size:28px;margin-bottom:8px;opacity:0.5;">🔍</div>
                        <div style="font-size:13px;color:var(--text-secondary);">暂无匹配经验</div>
                    </div>
                `}
            </div>
            
            <!-- 操作按钮 -->
            <div style="display:flex;gap:8px;">
                <button class="btn btn-sm btn-outline" onclick="detectExperienceConflicts()">检测冲突</button>
                <button class="btn btn-sm btn-outline" onclick="updateExperienceWeights()">更新权重</button>
            </div>
        </div>
    `;
    
    document.getElementById('smart-experience-content').innerHTML = html;
}

async function detectExperienceConflicts() {
    if (!currentAutoStrategyId) return;
    
    try {
        const data = await api(`/api/auto-strategy/enhanced/experience-conflict-detection?strategy_id=${currentAutoStrategyId}`, { method: 'POST' });
        const conflicts = data.data.conflicts || [];
        
        if (conflicts.length > 0) {
            toast(`检测到${conflicts.length}个经验冲突`, 'warning');
        } else {
            toast('未发现经验冲突', 'success');
        }
    } catch (e) {
        toast('检测失败: ' + e.message, 'error');
    }
}

async function updateExperienceWeights() {
    if (!currentAutoStrategyId) return;
    
    try {
        const data = await api(`/api/auto-strategy/enhanced/update-experience-weights?strategy_id=${currentAutoStrategyId}`, { method: 'POST' });
        toast(`更新${data.data.updated_count}条经验权重`, 'success');
        loadSmartExperiences();
    } catch (e) {
        toast('更新失败: ' + e.message, 'error');
    }
}

async function loadAnomalies() {
    if (!currentAutoStrategyId) {
        document.getElementById('anomaly-detection-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">⚠️</div><div>请先创建策略</div></div>';
        return;
    }
    
    try {
        const data = await api(`/api/auto-strategy/enhanced/detect-anomalies?strategy_id=${currentAutoStrategyId}`, { method: 'POST' });
        renderAnomalies(data.data);
    } catch (e) {
        document.getElementById('anomaly-detection-content').innerHTML = 
            '<div class="empty-state-mini"><div style="font-size:28px;margin-bottom:8px;opacity:0.5;">⚠️</div><div>加载失败: ' + e.message + '</div></div>';
    }
}

function renderAnomalies(data) {
    const anomalies = data.anomalies || [];
    const shouldReview = data.should_trigger_review || false;
    
    const html = `
        <div style="padding:12px;">
            <!-- 异常状态 -->
            <div style="padding:16px;background:${anomalies.length > 0 ? 'rgba(239,68,68,0.1)' : 'rgba(16,185,129,0.1)'};border-radius:8px;text-align:center;margin-bottom:16px;">
                <div style="font-size:28px;margin-bottom:8px;">${anomalies.length > 0 ? '⚠️' : '✅'}</div>
                <div style="font-size:16px;font-weight:700;color:${anomalies.length > 0 ? 'var(--danger)' : 'var(--success)'};">
                    ${anomalies.length > 0 ? `检测到 ${anomalies.length} 个异常` : '策略运行正常'}
                </div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
                    ${shouldReview ? '建议触发复盘分析' : '无需特殊处理'}
                </div>
            </div>
            
            <!-- 异常列表 -->
            ${anomalies.length > 0 ? `
                <div style="margin-bottom:16px;">
                    <div style="font-size:14px;font-weight:600;margin-bottom:8px;">异常详情</div>
                    ${anomalies.map(a => `
                        <div style="padding:12px;background:rgba(239,68,68,0.05);border-radius:8px;margin-bottom:8px;border-left:3px solid ${a.severity === 'high' ? 'var(--danger)' : '#f59e0b'};">
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                                <div style="font-size:13px;font-weight:600;">
                                    ${a.type === 'large_loss' ? '📉 大幅亏损' : a.type === 'consecutive_failure' ? '🔄 连续失败' : a.type === 'drawdown_spike' ? '⬇️ 回撤突增' : '⚠️ 异常'}
                                </div>
                                <div style="font-size:11px;padding:4px 8px;border-radius:4px;background:${a.severity === 'high' ? 'rgba(239,68,68,0.2)' : 'rgba(245,158,11,0.2)'};">
                                    ${a.severity === 'high' ? '高严重性' : '中等严重性'}
                                </div>
                            </div>
                            <div style="font-size:12px;color:var(--text-secondary);">
                                ${a.message || '未知异常'}
                            </div>
                            ${a.date ? `
                                <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">发生时间: ${a.date}</div>
                            ` : ''}
                            ${a.loss_pct ? `
                                <div style="font-size:11px;color:var(--danger);margin-top:4px;">损失幅度: ${a.loss_pct}%</div>
                            ` : ''}
                            ${a.drawdown_pct ? `
                                <div style="font-size:11px;color:var(--danger);margin-top:4px;">回撤幅度: ${a.drawdown_pct}%</div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
                
                <!-- 操作按钮 -->
                <div style="display:flex;gap:8px;">
                    <button class="btn btn-sm btn-warning" onclick="triggerAnomalyReview()">触发异常复盘</button>
                    <button class="btn btn-sm btn-outline" onclick="suggestParameterAdjustments()">参数调优建议</button>
                </div>
            ` : `
                <div style="padding:20px;background:rgba(255,255,255,0.5);border-radius:8px;text-align:center;">
                    <div style="font-size:48px;margin-bottom:12px;">✨</div>
                    <div style="font-size:14px;color:var(--text-secondary);">策略运行健康，无异常情况</div>
                </div>
            `}
        </div>
    `;
    
    document.getElementById('anomaly-detection-content').innerHTML = html;
}

async function triggerAnomalyReview() {
    if (!currentAutoStrategyId) return;
    
    try {
        const anomalyData = await api(`/api/auto-strategy/enhanced/detect-anomalies?strategy_id=${currentAutoStrategyId}`, { method: 'POST' });
        const anomalies = anomalyData.data.anomalies || [];
        
        if (anomalies.length === 0) {
            toast('无异常需要复盘', 'info');
            return;
        }
        
        const anomalyType = anomalies[0].type;
        toast('正在触发异常复盘...', 'info');
        
        const data = await api(`/api/auto-strategy/trigger-anomaly-review?strategy_id=${currentAutoStrategyId}&anomaly_type=${anomalyType}`, { method: 'POST' });
        
        toast('异常复盘完成', 'success');
        loadAnomalies();
        loadReviewReport();
    } catch (e) {
        toast('复盘失败: ' + e.message, 'error');
    }
}

async function suggestParameterAdjustments() {
    if (!currentAutoStrategyId) return;
    
    try {
        const data = await api(`/api/auto-strategy/enhanced/suggest-parameter-adjustments?strategy_id=${currentAutoStrategyId}`, { method: 'POST' });
        const suggestions = data.data.suggestions || [];
        
        if (suggestions.length > 0) {
            let msg = '参数调优建议:\n';
            suggestions.forEach(s => {
                msg += `${s.parameter}: ${s.current} → ${s.suggested} (${s.reason})\n`;
            });
            toast(msg, 'info', 5000);
        } else {
            toast('暂无参数调整建议', 'info');
        }
    } catch (e) {
        toast('获取建议失败: ' + e.message, 'error');
    }
}

// ---- 折叠功能 ----

function toggleSentimentDetail() {
    const container = document.getElementById('sentiment-detail-container');
    const icon = document.getElementById('sentiment-toggle-icon');
    
    if (container.style.display === 'none') {
        container.style.display = 'block';
        icon.style.transform = 'rotate(180deg)';
        icon.textContent = '▲';
    } else {
        container.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
        icon.textContent = '▼';
    }
}

/* ====== 子页面加载函数 ====== */

async function loadAutoStrategyOverview() {
    await Promise.all([
        loadAutoStrategies(),
        loadSentimentSummary(),
    ]);
}

async function loadSentimentPage() {
    await Promise.all([
        loadSentimentSummaryForTable(),
        loadSentimentTable(),
        loadMarketEnvironment(),
    ]);
}

async function loadSentimentSummaryForTable() {
    try {
        const data = await api('/api/auto-strategy/sentiments/summary');
        const summary = data.data;

        document.getElementById('sentiment-total').textContent = summary.total || 0;
        document.getElementById('sentiment-positive-count').textContent = summary.positive || 0;
        document.getElementById('sentiment-negative-count').textContent = summary.negative || 0;
        document.getElementById('sentiment-avg-score').textContent = summary.avg_score || '-';
    } catch (e) {
        console.error('获取舆情汇总失败', e);
    }
}

async function loadSentimentTable() {
    try {
        const data = await api('/api/auto-strategy/sentiments?days=1');
        const sentiments = data.data.sentiments || [];

        if (sentiments.length === 0) {
            document.getElementById('sentiment-table').innerHTML = 
                '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary);">暂无舆情数据</td></tr>';
            return;
        }

        const sourceLabels = {
            'eastmoney': '东财',
            'cls': '财联社',
            'ths': '同花顺',
            'sina': '新浪',
            'jin10': '金十',
            'eastmoney_hot': '热点',
            'eastmoney_keyword': '关键词'
        };

        const html = sentiments.map((s, idx) => {
            const score = s.sentiment_score || 0;
            const scoreClass = score > 0 ? 'text-success' : score < 0 ? 'text-danger' : '';
            const labelIcon = s.sentiment_label === 'positive' ? '✅' : s.sentiment_label === 'negative' ? '⚠️' : '➖';
            const labelText = s.sentiment_label === 'positive' ? '正面' : s.sentiment_label === 'negative' ? '负面' : '中性';
            const labelClass = s.sentiment_label === 'positive' ? 'badge-success' : s.sentiment_label === 'negative' ? 'badge-danger' : '';
            
            const title = s.title || s.content?.substring(0, 60) || '无标题';
            const time = s.publish_time ? new Date(s.publish_time).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}) : '-';
            const sourceLabel = sourceLabels[s.source] || s.source;

            return `
                <tr onclick="showSentimentDetail(${idx})" style="cursor:pointer;">
                    <td><span class="badge" style="font-size:11px;background:var(--primary);color:white;">${sourceLabel}</span></td>
                    <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;">${title}</td>
                    <td><span class="badge ${labelClass}">${labelIcon} ${labelText}</span></td>
                    <td class="${scoreClass}" style="font-weight:600;">${score.toFixed(2)}</td>
                    <td style="font-size:12px;color:var(--text-secondary);">${time}</td>
                    <td><button class="btn btn-sm btn-outline" onclick="event.stopPropagation();showSentimentDetail(${idx})">详情</button></td>
                </tr>
            `;
        }).join('');

        document.getElementById('sentiment-table').innerHTML = html;
        
        window.currentSentiments = sentiments;
        
    } catch (e) {
        document.getElementById('sentiment-table').innerHTML = 
            `<tr><td colspan="6" style="text-align:center;color:var(--danger);">加载失败: ${e.message}</td></tr>`;
    }
}

function showSentimentDetail(index) {
    const sentiments = window.currentSentiments || [];
    const s = sentiments[index];
    
    if (!s) {
        toast('舆情数据不存在', 'error');
        return;
    }

    const sourceLabels = {
        'eastmoney': '东方财富',
        'cls': '财联社',
        'ths': '同花顺',
        'sina': '新浪财经',
        'jin10': '金十数据',
        'eastmoney_hot': '东方财富热点',
        'eastmoney_keyword': '东方财富关键词'
    };

    const score = s.sentiment_score || 0;
    const scoreClass = score > 0 ? 'text-success' : score < 0 ? 'text-danger' : '';
    const labelIcon = s.sentiment_label === 'positive' ? '✅' : s.sentiment_label === 'negative' ? '⚠️' : '➖';
    const labelText = s.sentiment_label === 'positive' ? '正面情绪' : s.sentiment_label === 'negative' ? '负面情绪' : '中性情绪';
    const sourceLabel = sourceLabels[s.source] || s.source;
    const relatedEtfs = s.related_etfs || [];
    const keyFactors = s.key_factors || [];
    const publishTime = s.publish_time ? new Date(s.publish_time).toLocaleString('zh-CN') : '未知时间';

    const html = `
        <div style="padding:20px;">
            <!-- 来源与时间 -->
            <div style="display:flex;gap:16px;margin-bottom:16px;font-size:13px;color:var(--text-secondary);">
                <span><strong>来源:</strong> ${sourceLabel}</span>
                <span><strong>发布时间:</strong> ${publishTime}</span>
            </div>

            <!-- 标题 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:14px;font-weight:600;color:var(--text-secondary);margin-bottom:6px;">标题</div>
                <div style="font-size:16px;font-weight:600;color:var(--text);padding:12px;background:var(--bg-secondary);border-radius:8px;">
                    ${s.title || '无标题'}
                </div>
            </div>

            <!-- 内容概要 -->
            <div style="margin-bottom:20px;">
                <div style="font-size:14px;font-weight:600;color:var(--text-secondary);margin-bottom:6px;">内容概要</div>
                <div style="font-size:14px;color:var(--text);padding:12px;background:var(--bg-secondary);border-radius:8px;line-height:1.6;">
                    ${s.content || '暂无详细内容'}
                </div>
            </div>

            <!-- 情绪分析 -->
            <div style="padding:16px;background:linear-gradient(135deg, rgba(59,130,246,0.1) 0%, rgba(16,185,129,0.1) 100%);border-radius:8px;margin-bottom:20px;">
                <div style="font-size:14px;font-weight:600;margin-bottom:12px;">🧠 AI情绪分析</div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                    <div style="text-align:center;padding:12px;background:var(--bg);border-radius:8px;">
                        <div style="font-size:12px;color:var(--text-secondary);">情绪判断</div>
                        <div style="font-size:18px;font-weight:700;margin-top:6px;">
                            <span style="${s.sentiment_label === 'positive' ? 'color:var(--success)' : s.sentiment_label === 'negative' ? 'color:var(--danger)' : 'color:var(--text)'};">
                                ${labelIcon} ${labelText}
                            </span>
                        </div>
                    </div>
                    <div style="text-align:center;padding:12px;background:var(--bg);border-radius:8px;">
                        <div style="font-size:12px;color:var(--text-secondary);">情绪评分</div>
                        <div style="font-size:24px;font-weight:700;margin-top:6px;${scoreClass ? 'color:' + scoreClass : ''}">
                            ${score.toFixed(2)}
                        </div>
                        <div style="font-size:11px;color:var(--text-secondary);">范围: -1 到 1</div>
                    </div>
                </div>
            </div>

            <!-- 相关ETF -->
            ${relatedEtfs.length > 0 ? `
                <div style="margin-bottom:16px;">
                    <div style="font-size:14px;font-weight:600;margin-bottom:8px;">📌 相关ETF</div>
                    <div style="display:flex;gap:8px;">
                        ${relatedEtfs.map(code => `
                            <span class="badge" style="background:var(--primary);color:white;">${code}</span>
                        `).join('')}
                    </div>
                </div>
            ` : ''}

            <!-- 关键因素 -->
            ${keyFactors.length > 0 ? `
                <div style="margin-bottom:16px;">
                    <div style="font-size:14px;font-weight:600;margin-bottom:8px;">💡 影响因素</div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        ${keyFactors.map(factor => `
                            <span style="padding:6px 12px;background:var(--bg-secondary);border-radius:6px;font-size:13px;">${factor}</span>
                        `).join('')}
                    </div>
                </div>
            ` : ''}
        </div>
    `;

    document.getElementById('sentiment-detail-body').innerHTML = html;
    openModal('modal-sentiment-detail');
}

async function loadSentiments() {
    await loadSentimentTable();
}

async function loadTechnicalPage() {
    await loadAllTechnicalIndicators();
}

async function loadAllTechnicalIndicators() {
    try {
        document.getElementById('technical-indicators-table').innerHTML = 
            '<tr><td colspan="9" style="text-align:center;color:var(--text-secondary);">正在加载...</td></tr>';
        
        const data = await api('/api/auto-strategy/enhanced/technical-indicators-batch');
        const indicators = data.data.indicators || [];
        
        if (indicators.length === 0) {
            document.getElementById('technical-indicators-table').innerHTML = 
                '<tr><td colspan="9" style="text-align:center;color:var(--text-secondary);">暂无数据</td></tr>';
            return;
        }
        
        const html = indicators.map(item => {
            const trendClass = item.trend.includes('bullish') ? 'text-success' : item.trend.includes('bearish') ? 'text-danger' : '';
            const trendText = item.trend === 'strong_bullish' ? '强势多头↗↗' : 
                              item.trend === 'bullish' ? '多头↗' : 
                              item.trend === 'strong_bearish' ? '强势空头↘↘' : 
                              item.trend === 'bearish' ? '空头↘' : '震荡↔';
            
            const macdClass = item.macd_signal === 'bullish' ? 'text-success' : item.macd_signal === 'bearish' ? 'text-danger' : '';
            const macdText = item.macd_signal === 'bullish' ? '金叉✓' : item.macd_signal === 'bearish' ? '死叉✗' : '中性';
            
            const rsiClass = item.rsi_signal === 'overbought' ? 'text-danger' : item.rsi_signal === 'oversold' ? 'text-success' : '';
            const rsiText = item.rsi_value != null ? fmtNum(item.rsi_value, 1) : '-';
            const rsiSignalText = item.rsi_signal === 'overbought' ? '超买⚠️' : item.rsi_signal === 'oversold' ? '超卖✅' : '中性';
            
            const ma5Text = item.ma5_above === 'above' ? '上方✓' : item.ma5_above === 'below' ? '下方✗' : '-';
            const ma5Class = item.ma5_above === 'above' ? 'text-success' : item.ma5_above === 'below' ? 'text-danger' : '';
            
            return `
                <tr>
                    <td><span class="etf-code">${item.etf_code}</span></td>
                    <td class="${trendClass}"><strong>${trendText}</strong></td>
                    <td>${item.trend_confidence}%</td>
                    <td class="${macdClass}">${macdText}</td>
                    <td>${rsiText}</td>
                    <td class="${rsiClass}">${rsiSignalText}</td>
                    <td class="${ma5Class}">${ma5Text}</td>
                    <td style="font-size:12px;color:var(--text-secondary);">${item.latest_date || '-'}</td>
                    <td><button class="btn btn-sm btn-outline" onclick="showTechnicalDetail('${item.etf_code}')">详情</button></td>
                </tr>
            `;
        }).join('');
        
        document.getElementById('technical-indicators-table').innerHTML = html;
        
    } catch (e) {
        document.getElementById('technical-indicators-table').innerHTML = 
            `<tr><td colspan="9" style="text-align:center;color:var(--danger);">加载失败: ${e.message}</td></tr>`;
    }
}

async function showTechnicalDetail(etfCode) {
    try {
        document.getElementById('technical-detail-card').style.display = 'block';
        document.getElementById('technical-detail-title').textContent = `${etfCode} 详细指标`;
        document.getElementById('technical-indicators-detail').innerHTML = 
            '<div style="text-align:center;padding:20px;color:var(--text-secondary);">正在加载...</div>';
        
        const data = await api(`/api/auto-strategy/enhanced/technical-indicators?etf_code=${etfCode}`);
        renderTechnicalIndicators(data.data, [etfCode]);
        
    } catch (e) {
        document.getElementById('technical-indicators-detail').innerHTML = 
            `<div style="text-align:center;color:var(--danger);">加载失败: ${e.message}</div>`;
    }
}

function hideTechnicalDetail() {
    document.getElementById('technical-detail-card').style.display = 'none';
}

async function loadSingleETFIndicator(etfCode) {
    await showTechnicalDetail(etfCode);
}

async function loadRiskPage() {
    await Promise.all([
        loadRiskDashboard(),
        loadAnomalies(),
        loadSmartExperiences(),
    ]);
}

async function loadReviewPage() {
    if (currentAutoStrategyId) {
        await Promise.all([
            loadReviewReport(),
            loadExecutionLogs(currentAutoStrategyId),
            loadExperiences(currentAutoStrategyId),
        ]);
    }
}

async function loadReviewData() {
    await loadReviewPage();
}

async function loadSentimentData() {
    await loadSentimentPage();
}

async function runStressTest() {
    if (!currentAutoStrategyId) {
        toast('请先创建自动策略', 'warning');
        return;
    }
    
    try {
        toast('正在运行压力测试...', 'info');
        const data = await api(`/api/auto-strategy/enhanced/stress-test?strategy_id=${currentAutoStrategyId}`);
        const result = data.data;
        
        toast(`压力测试完成: 最大潜在损失 ${result.max_potential_loss_pct?.toFixed(2) || 'N/A'}%`, 'success');
        loadRiskDashboard();
    } catch (e) {
        toast('压力测试失败: ' + e.message, 'error');
    }
}

async function checkCircuitBreaker() {
    if (!currentAutoStrategyId) {
        toast('请先创建自动策略', 'warning');
        return;
    }
    
    try {
        const data = await api(`/api/auto-strategy/enhanced/circuit-breaker-check?strategy_id=${currentAutoStrategyId}`);
        const result = data.data;
        
        if (result.triggered) {
            toast(`⚠️ 熔断触发: ${result.reason}`, 'warning');
        } else {
            toast('✅ 熔断未触发，策略运行正常', 'success');
        }
    } catch (e) {
        toast('检查失败: ' + e.message, 'error');
    }
}
