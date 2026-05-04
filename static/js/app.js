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
        
        // 实盘模拟功能尚未完善提示
        if (tab === 'portfolio') {
            toast('⚠️ 实盘模拟功能正在开发中，敬请期待！', 'warning');
            return;
        }
        
        document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
        document.getElementById('tab-' + tab).classList.add('active');

        if (tab === 'market') loadMarket();
        if (tab === 'strategy') loadStrategies();
        if (tab === 'backtest') loadBacktestPage();
    });
});

// ---- Modal ----
function openModal(id) { document.getElementById(id).classList.add('active'); }
function closeModal(id) { document.getElementById(id).classList.remove('active'); }

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
                            <button class="btn btn-danger btn-sm" onclick="deleteStrategy(${s.id})" style="padding:4px 12px;">删除</button>
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

async function deleteStrategy(id) {
    if (!confirm('确定删除该策略？')) return;
    
    try {
        await api(`/api/strategy/${id}`, { method: 'DELETE' });
        toast('策略已删除', 'success');
        loadStrategies();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
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
