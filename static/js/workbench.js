/**
 * 工作台仪表盘逻辑
 */

/**
 * 解析后端返回的时间字符串（后端存储为 UTC，无时区后缀）
 * 将无时区后缀的时间按 UTC 解析并转换为本地时间，返回 Date 对象
 */
function parseServerTime(str) {
    if (!str) return null;
    let s = String(str).trim();
    if (!/[zZ]|[+-]\d{2}:\d{2}$/.test(s)) s += 'Z';
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
}

const Workbench = {
    currentView: 'overview',
    _etfNameMap: {},

    init() {
        this.bindViewTabs();
        this.initSidebarResize();
        this.loadOverview();
        // 每60秒刷新
        setInterval(() => this.refresh(), 60000);
    },

    buildNameMap(quotes) {
        quotes.forEach(q => {
            if (q.etf_code && q.etf_name) this._etfNameMap[q.etf_code] = q.etf_name;
        });
    },

    etfLabel(code) {
        const name = this._etfNameMap[code];
        return name ? `${code} ${name}` : code;
    },

    bindViewTabs() {
        document.querySelectorAll('.view-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.currentView = tab.dataset.view;
                this.switchView(this.currentView);
            });
        });
    },

    switchView(view) {
        document.querySelectorAll('.view-panel').forEach(p => p.style.display = 'none');
        const panel = document.getElementById(`view-${view}`);
        if (panel) panel.style.display = 'block';

        if (view === 'overview') this.loadOverview();
        if (view === 'market') { this.initMarketToolbar(); this.loadMarket(); }
        if (view === 'strategies') { this.loadStrategies(); this.loadStrategiesExtras(); }
        if (view === 'sentiment') this.loadSentimentView();
        if (view === 'tasks') this.loadTasksView();
        if (view === 'analyses') this.loadAnalysesView();
    },

    async loadOverview() {
        try {
            const [ovResp, quotesResp, sentiResp, stratResp, quantResp] = await Promise.all([
                fetch('/api/workbench/overview').then(r => r.json()),
                fetch('/api/etf/overview?limit=2000').then(r => r.json()),
                fetch('/api/auto-strategy/sentiments/summary').then(r => r.json()).catch(() => ({ code: 500 })),
                fetch('/api/strategy/list').then(r => r.json()),
                fetch('/api/workbench/quant-summary').then(r => r.json()).catch(() => ({ code: 500 })),
            ]);

            const ov = ovResp.code === 200 ? ovResp.data : null;
            const quotes = quotesResp.code === 200 ? (quotesResp.data.quotes || []) : [];
            const senti = sentiResp.code === 200 ? sentiResp.data : null;
            const strategies = stratResp.code === 200 ? (stratResp.data.strategies || []) : [];
            const quant = quantResp.code === 200 ? quantResp.data : null;

            this.buildNameMap(quotes);
            this.renderStatCards(ov, quotes, senti);
            this.renderDistChart(quotes);
            this.renderMovers(quotes);
            this.renderQuantAnalysis(quant);
            this.updateFooter(ov);
        } catch (e) {
            console.error('加载工作台概览失败:', e);
        }
    },

    computeBreadth(quotes) {
        let up = 0, down = 0, flat = 0;
        quotes.forEach(q => {
            if (q.change_pct == null) return;
            if (q.change_pct > 0) up++;
            else if (q.change_pct < 0) down++;
            else flat++;
        });
        return { up, down, flat, total: up + down + flat };
    },

    renderStatCards(ov, quotes, senti) {
        const el = document.getElementById('stat-cards');
        if (!el) return;

        const b = this.computeBreadth(quotes);
        const upRatio = b.total ? Math.round(b.up / b.total * 100) : 0;

        const sentiScore = senti && senti.total > 0 ? senti.avg_score : null;
        const sentiLabel = sentiScore == null ? '' : (sentiScore > 0.2 ? '偏多' : sentiScore < -0.2 ? '偏空' : '中性');
        const sentiColor = sentiScore == null ? 'var(--text-secondary)' : sentiScore > 0 ? 'var(--danger)' : sentiScore < 0 ? 'var(--success)' : 'var(--warning)';

        const active = ov ? ov.strategies.active : 0;
        const autoRun = ov ? ov.strategies.auto_running : 0;
        const total = ov ? ov.strategies.total : 0;
        const etfCount = ov ? ov.data.etf_count : 0;
        const latestDate = ov && ov.data.latest_quote_date ? ov.data.latest_quote_date : '无';
        const aiCount = ov ? ov.ai.recent_actions.length : 0;
        const pending = ov ? ov.ai.pending_approvals : 0;

        el.innerHTML = `
            <div class="wb-card stat-lead">
                <div class="card-label">市场涨跌</div>
                <div class="card-value"><span class="text-up">${b.up}</span><span class="stat-sep">/</span><span class="text-down">${b.down}</span></div>
                <div class="breadth-bar"><span style="width:${upRatio}%"></span></div>
                <div class="card-sub">上涨占比 ${upRatio}% · 平盘 ${b.flat}</div>
            </div>
            <div class="wb-card">
                <div class="card-label">舆情温度</div>
                <div class="card-value" style="color:${sentiColor}">${sentiScore == null ? '—' : (sentiScore > 0 ? '+' : '') + sentiScore}</div>
                <div class="card-sub">${senti && senti.total > 0 ? `${sentiLabel} · 正面 ${senti.positive} / 负面 ${senti.negative}` : '今日未采集舆情'}</div>
            </div>
            <div class="wb-card">
                <div class="card-label">活跃策略</div>
                <div class="card-value">${active}</div>
                <div class="card-sub">自动运行 ${autoRun} / 共 ${total}</div>
            </div>
            <div class="wb-card">
                <div class="card-label">ETF池</div>
                <div class="card-value">${etfCount}</div>
                <div class="card-sub">最新数据 ${latestDate}</div>
            </div>
            <div class="wb-card">
                <div class="card-label">AI决策</div>
                <div class="card-value ${pending > 0 ? 'text-down' : ''}">${aiCount}</div>
                <div class="card-sub">${pending > 0 ? `⚠ ${pending} 项待审批` : '近7天 · 无待审批'}</div>
            </div>
        `;
    },

    renderDistChart(quotes) {
        const container = document.getElementById('market-dist-chart');
        if (!container || typeof echarts === 'undefined') return;

        const valid = quotes.filter(q => q.change_pct != null);
        const dateEl = document.getElementById('dist-date');
        const latest = quotes.find(q => q.trade_date);
        if (dateEl) dateEl.textContent = latest ? latest.trade_date : '';

        if (valid.length === 0) {
            container.innerHTML = '<div class="empty-hint" style="padding-top:90px;">暂无行情数据，请先同步行情</div>';
            return;
        }

        const labels = ['<-3%', '-3~-2%', '-2~-1%', '-1~0%', '0~1%', '1~2%', '2~3%', '>3%'];
        const colors = ['#15803d', '#22c55e', '#86efac', '#bbf7d0', '#fecaca', '#f87171', '#ef4444', '#b91c1c'];
        const counts = new Array(8).fill(0);
        valid.forEach(q => {
            const p = q.change_pct;
            let idx;
            if (p < -3) idx = 0;
            else if (p < -2) idx = 1;
            else if (p < -1) idx = 2;
            else if (p < 0) idx = 3;
            else if (p < 1) idx = 4;
            else if (p < 2) idx = 5;
            else if (p < 3) idx = 6;
            else idx = 7;
            counts[idx]++;
        });

        if (this._distChart) this._distChart.dispose();
        this._distChart = echarts.init(container);
        this._distChart.setOption({
            grid: { left: 40, right: 16, top: 24, bottom: 32 },
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                backgroundColor: '#ffffff', borderColor: '#dfe6ee',
                textStyle: { color: '#1c2b3a', fontSize: 12 },
            },
            xAxis: {
                type: 'category', data: labels,
                axisLabel: { color: '#5c6f82', fontSize: 11 },
                axisLine: { lineStyle: { color: '#d5dee8' } },
                axisTick: { show: false },
            },
            yAxis: {
                type: 'value',
                axisLabel: { color: '#5c6f82', fontSize: 11 },
                splitLine: { lineStyle: { color: '#e8eef4' } },
            },
            series: [{
                type: 'bar', barWidth: '58%',
                data: counts.map((c, i) => ({ value: c, itemStyle: { color: colors[i], borderRadius: [3, 3, 0, 0] } })),
            }],
        });
    },

    renderMovers(quotes) {
        const el = document.getElementById('movers-content');
        if (!el) return;
        const valid = quotes.filter(q => q.change_pct != null && q.close_price != null);
        if (valid.length === 0) {
            el.innerHTML = '<div class="empty-hint">暂无行情数据</div>';
            return;
        }
        const sorted = [...valid].sort((a, b) => b.change_pct - a.change_pct);
        const gainers = sorted.slice(0, 5);
        const losers = sorted.slice(-5).reverse();

        const item = (q, rank) => `
            <div class="mover-item">
                <span class="mover-rank ${rank <= 3 ? 'rank-top' : ''}">${rank}</span>
                <div class="mover-info">
                    <div class="mover-name">${this.esc(q.etf_name)}</div>
                    <div class="mover-code">${q.etf_code} · ${q.close_price}</div>
                </div>
                <div class="mover-pct ${q.change_pct >= 0 ? 'text-up' : 'text-down'}">${q.change_pct >= 0 ? '+' : ''}${q.change_pct.toFixed(2)}%</div>
            </div>
        `;

        el.innerHTML = `
            <div class="movers-col">
                <div class="movers-title text-up">▲ 领涨</div>
                ${gainers.map((q, i) => item(q, i + 1)).join('')}
            </div>
            <div class="movers-col">
                <div class="movers-title text-down">▼ 领跌</div>
                ${losers.map((q, i) => item(q, i + 1)).join('')}
            </div>
        `;
    },

    renderQuantAnalysis(quant) {
        if (!quant || quant.error) {
            const trendEl = document.getElementById('trend-dist-chart');
            const momEl = document.getElementById('momentum-content');
            const volEl = document.getElementById('volatility-content');
            if (trendEl) trendEl.innerHTML = '<div class="empty-hint" style="padding-top:80px;">暂无数据</div>';
            if (momEl) momEl.innerHTML = '<div class="empty-hint">暂无数据</div>';
            if (volEl) volEl.innerHTML = '<div class="empty-hint">暂无数据</div>';
            return;
        }
        this.renderTrendDistChart(quant.trend_distribution, quant.total_analyzed);
        this.renderMomentumList(quant.momentum_top, quant.momentum_bottom);
        this.renderVolatilityPanel(quant.market_rsi, quant.market_volatility, quant.volatility_top);
        const dateEl = document.getElementById('quant-date');
        if (dateEl) dateEl.textContent = quant.latest_date || '';
    },

    renderTrendDistChart(dist, total) {
        const container = document.getElementById('trend-dist-chart');
        if (!container || typeof echarts === 'undefined') return;
        if (!dist || total === 0) {
            container.innerHTML = '<div class="empty-hint" style="padding-top:80px;">暂无数据</div>';
            return;
        }
        const labels = ['强势多头', '多头', '中性', '空头', '强势空头'];
        const keys = ['strong_bullish', 'bullish', 'neutral', 'bearish', 'strong_bearish'];
        // A股惯例: 红涨绿跌，多头红，空头绿
        const colors = ['#b91c1c', '#ef4444', '#d97706', '#22c55e', '#15803d'];
        const data = keys.map((k, i) => ({ value: dist[k] || 0, itemStyle: { color: colors[i], borderRadius: [3, 3, 0, 0] } }));

        if (this._trendChart) this._trendChart.dispose();
        this._trendChart = echarts.init(container);
        this._trendChart.setOption({
            grid: { left: 36, right: 12, top: 20, bottom: 28 },
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                backgroundColor: '#ffffff', borderColor: '#dfe6ee',
                textStyle: { color: '#1c2b3a', fontSize: 12 },
                formatter: p => `${p[0].name}<br/>${p[0].value}只 (${total ? (p[0].value / total * 100).toFixed(1) : 0}%)`,
            },
            xAxis: {
                type: 'category', data: labels,
                axisLabel: { color: '#5c6f82', fontSize: 10 },
                axisLine: { lineStyle: { color: '#d5dee8' } },
                axisTick: { show: false },
            },
            yAxis: {
                type: 'value',
                axisLabel: { color: '#5c6f82', fontSize: 11 },
                splitLine: { lineStyle: { color: '#e8eef4' } },
            },
            series: [{ type: 'bar', barWidth: '55%', data }],
        });
    },

    renderMomentumList(top, bottom) {
        const el = document.getElementById('momentum-content');
        if (!el) return;
        if (!top || top.length === 0) {
            el.innerHTML = '<div class="empty-hint">暂无数据</div>';
            return;
        }
        const item = (m) => `
            <div class="mover-item">
                <div class="mover-info">
                    <div class="mover-name">${this.esc(m.name || '')}${m.name && m.code ? ' ' : ''}${m.code || ''}</div>
                    <div class="mover-code">20日${m.chg_20d >= 0 ? '+' : ''}${m.chg_20d}%</div>
                </div>
                <div class="mover-pct ${m.chg_5d >= 0 ? 'text-up' : 'text-down'}">${m.chg_5d >= 0 ? '+' : ''}${m.chg_5d}%</div>
            </div>
        `;
        el.innerHTML = `
            <div class="movers-col">
                <div class="movers-title text-up">▲ 最强</div>
                ${top.slice(0, 5).map(item).join('')}
            </div>
            <div class="movers-col">
                <div class="movers-title text-down">▼ 最弱</div>
                ${(bottom || []).slice(0, 5).map(item).join('')}
            </div>
        `;
    },

    renderVolatilityPanel(rsi, vol, volTop) {
        const el = document.getElementById('volatility-content');
        if (!el) return;
        if (rsi == null && vol == null) {
            el.innerHTML = '<div class="empty-hint">暂无数据</div>';
            return;
        }
        const rsiLabel = rsi == null ? '—' : (rsi > 70 ? '超买' : rsi < 30 ? '超卖' : '中性');
        const rsiColor = rsi == null ? 'var(--text-secondary)' : rsi > 70 ? 'var(--danger)' : rsi < 30 ? 'var(--success)' : 'var(--warning)';
        const volLabel = vol == null ? '—' : (vol > 30 ? '高波动' : vol > 15 ? '中波动' : '低波动');

        let html = `
            <div style="display:flex;gap:12px;margin-bottom:12px;">
                <div style="flex:1;text-align:center;">
                    <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">市场RSI</div>
                    <div style="font-size:20px;font-weight:700;color:${rsiColor};">${rsi != null ? rsi : '—'}</div>
                    <div style="font-size:11px;color:${rsiColor};">${rsiLabel}</div>
                </div>
                <div style="flex:1;text-align:center;">
                    <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">年化波动率</div>
                    <div style="font-size:20px;font-weight:700;color:var(--accent);">${vol != null ? vol + '%' : '—'}</div>
                    <div style="font-size:11px;color:var(--text-secondary);">${volLabel}</div>
                </div>
            </div>
        `;
        if (volTop && volTop.length > 0) {
            html += '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">高波动Top5</div>';
            html += volTop.slice(0, 5).map(v => `
                <div class="mover-item" style="padding:4px 4px;">
                    <div class="mover-info">
                        <div class="mover-name" style="font-size:12px;">${this.esc(v.name || '')}${v.name && v.code ? ' ' : ''}${v.code || ''}</div>
                    </div>
                    <div class="mover-pct" style="font-size:12px;color:var(--warning);">${v.ann_vol}%</div>
                </div>
            `).join('');
        }
        el.innerHTML = html;
    },

    freqLabel(f) {
        const map = { daily: '每日', weekly: '每周', monthly: '每月', quarterly: '每季', yearly: '每年', none: '不再平衡' };
        return map[f] || f || '—';
    },

    renderOverviewStrategies(strategies) {
        const el = document.getElementById('ov-strategies');
        if (!el) return;
        const sub = document.getElementById('strategy-count-sub');
        if (sub) sub.textContent = strategies.length ? `共 ${strategies.length} 个` : '';

        if (strategies.length === 0) {
            el.innerHTML = '<div class="empty-hint">暂无策略，可通过AI对话创建</div>';
            return;
        }

        const segColors = ['#0284c7', '#16a34a', '#d97706', '#7c3aed', '#dc2626', '#0891b2'];

        el.innerHTML = strategies.map(s => {
            const alloc = s.allocation_config ? Object.entries(s.allocation_config) : [];
            const segments = alloc.map(([code, w], i) =>
                `<span class="ov-seg" style="width:${(w*100).toFixed(1)}%;background:${segColors[i % segColors.length]}" title="${this.etfLabel(code)} ${(w*100).toFixed(0)}%"></span>`
            ).join('');
            const badge = s.status === 'active'
                ? '<span class="s-badge s-active">活跃</span>'
                : '<span class="s-badge s-paused">已暂停</span>';
            const profitHtml = (s.latest_profit_pct != null)
                ? `<span class="ov-profit ${s.latest_profit_pct >= 0 ? 'text-up' : 'text-down'}">${s.latest_profit_pct >= 0 ? '+' : ''}${s.latest_profit_pct.toFixed(2)}%</span>`
                : '';
            return `
                <div class="ov-strat" id="ov-strat-${s.id}">
                    <div class="ov-strat-bar" onclick="Workbench.toggleStrategyDetail(${s.id})">
                        <span class="ov-strat-icon" id="ov-strat-icon-${s.id}">▸</span>
                        <div class="ov-strat-info">
                            <div class="ov-strat-top">
                                <span class="ov-strat-name">${this.esc(s.name)}</span>
                                ${badge}
                            </div>
                            <div class="ov-strat-allocbar">${segments || '<span class="alloc-none">未配置</span>'}</div>
                        </div>
                        <div class="ov-strat-nums">
                            <span class="ov-asset">¥${(s.latest_asset || s.initial_capital || 0).toLocaleString()}</span>
                            ${profitHtml}
                        </div>
                    </div>
                    <div class="ov-strat-detail" id="ov-strat-detail-${s.id}" style="display:none">
                        <div class="ov-detail-loading">加载持仓中...</div>
                    </div>
                </div>`;
        }).join('');
    },

    async toggleStrategyDetail(id) {
        const detail = document.getElementById(`ov-strat-detail-${id}`);
        const icon = document.getElementById(`ov-strat-icon-${id}`);
        const block = document.getElementById(`ov-strat-${id}`);
        if (!detail) return;

        const isOpen = detail.style.display !== 'none';
        document.querySelectorAll('.ov-strat-detail').forEach(d => { d.style.display = 'none'; });
        document.querySelectorAll('.ov-strat-icon').forEach(i => { i.textContent = '▸'; });
        document.querySelectorAll('.ov-strat').forEach(b => b.classList.remove('expanded'));

        if (isOpen) return;

        detail.style.display = 'block';
        if (icon) icon.textContent = '▾';
        if (block) block.classList.add('expanded');
        detail.innerHTML = '<div class="ov-detail-loading">加载持仓中...</div>';

        try {
            const codes = (this._strategies || []).find(s => s.id === id);
            const allocCodes = codes && codes.allocation_config ? Object.keys(codes.allocation_config) : [];
            const [holdResp, histResp, indResp] = await Promise.all([
                fetch(`/api/portfolio/${id}/holdings`).then(r => r.json()),
                fetch(`/api/portfolio/${id}/history`).then(r => r.json()),
                fetch(`/api/workbench/market-indicators?limit=300`).then(r => r.json()).catch(() => ({code: 500})),
            ]);
            const holdings = (holdResp.code === 200 ? holdResp.data.holdings : []);
            const snapshots = (histResp.code === 200 ? histResp.data.snapshots : []);
            const allIndicators = (indResp.code === 200 ? indResp.data.rows : []);
            const indMap = {};
            allIndicators.forEach(r => { indMap[r.etf_code] = r; });
            this.renderStrategyDetail(detail, holdings, snapshots, indMap, allocCodes);
        } catch (e) {
            detail.innerHTML = '<div class="ov-detail-loading" style="color:var(--danger)">加载失败</div>';
        }
    },

    renderStrategyDetail(el, holdings, snapshots, indMap, allocCodes) {
        let html = '';

        const displayCodes = holdings.length > 0 ? holdings.map(h => h.etf_code) : allocCodes;

        if (displayCodes.length > 0) {
            html += `<div class="ov-hold-table">
                <div class="ov-hold-row ov-hold-head ov-hold-wide">
                    <span>ETF</span><span class="num">数量</span><span class="num">成本</span><span class="num">现价</span><span class="num">市值</span><span class="num">得分</span><span class="num">5日</span><span class="num">趋势</span><span class="num">波动</span>
                </div>
                ${displayCodes.map(code => {
                    const h = holdings.find(x => x.etf_code === code);
                    const ind = indMap[code];
                    const totalMv = holdings.reduce((s, x) => s + (x.market_value || 0), 0);
                    const pct = h && totalMv > 0 ? (h.market_value / totalMv * 100) : 0;
                    const pnl = h && h.current_price && h.avg_cost ? ((h.current_price / h.avg_cost - 1) * 100) : null;
                    return `<div class="ov-hold-row ov-hold-wide">
                        <span class="ov-hold-etf">${this.etfLabel(code)}${pct > 0 ? ` <em class="hold-pct">${pct.toFixed(0)}%</em>` : ''}</span>
                        <span class="num">${h ? h.quantity : '-'}</span>
                        <span class="num">${h ? (h.avg_cost || 0).toFixed(3) : '-'}</span>
                        <span class="num">${h ? (h.current_price || 0).toFixed(3) : '-'}${pnl != null ? ` <em class="${pnl >= 0 ? 'text-up' : 'text-down'}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}%</em>` : ''}</span>
                        <span class="num">${h ? '¥' + (h.market_value || 0).toLocaleString() : '-'}</span>
                        <span class="num score-num">${ind ? ind.composite_score : '-'}</span>
                        <span class="num ${ind && ind.momentum_5d >= 0 ? 'text-up' : 'text-down'}">${ind ? (ind.momentum_5d >= 0 ? '+' : '') + ind.momentum_5d + '%' : '-'}</span>
                        <span class="num">${ind ? this.trendDots(ind.trend_strength) : '-'}</span>
                        <span class="num">${ind ? ind.volatility_20d + '%' : '-'}</span>
                    </div>`;
                }).join('')}
            </div>`;
        } else {
            html += '<div class="ov-detail-loading">暂无持仓记录</div>';
        }

        if (snapshots.length > 1) {
            const recent = snapshots.slice(-20);
            const first = recent[0].total_asset;
            const last = recent[recent.length - 1].total_asset;
            const chg = first > 0 ? ((last / first - 1) * 100) : 0;
            html += `<div class="ov-detail-footer">
                <span>近${recent.length}个交易日</span>
                <span class="${chg >= 0 ? 'text-up' : 'text-down'}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span>
                <span>最新资产 ¥${last.toLocaleString()}</span>
            </div>`;
        }

        el.innerHTML = html || '<div class="ov-detail-loading">暂无数据</div>';
    },

    async loadStrategiesExtras() {
        try {
            const ovResp = await fetch('/api/workbench/overview').then(r => r.json()).catch(() => ({code: 500}));
            const ov = ovResp.code === 200 ? ovResp.data : null;
            this.renderTimeline(ov ? ov.ai.recent_actions : []);
        } catch (e) {
            console.error('加载策略附加面板失败:', e);
        }
    },

    initSidebarResize() {
        const handle = document.getElementById('sidebar-resize');
        const sidebar = document.getElementById('wb-sidebar');
        if (!handle || !sidebar || handle._bound) return;
        handle._bound = true;

        let dragging = false;
        handle.addEventListener('mousedown', (e) => {
            if (sidebar.classList.contains('collapsed')) return;
            dragging = true;
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            sidebar.classList.add('resizing');
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!dragging) return;
            const w = Math.max(280, Math.min(700, window.innerWidth - e.clientX));
            sidebar.style.width = w + 'px';
        });
        document.addEventListener('mouseup', () => {
            if (!dragging) return;
            dragging = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            sidebar.classList.remove('resizing');
            localStorage.setItem('sidebar-width', sidebar.style.width);
        });

        const saved = localStorage.getItem('sidebar-width');
        if (saved) sidebar.style.width = saved;
    },

    async loadSentimentPanel() {
        const el = document.getElementById('senti-content');
        if (!el) return;
        el.innerHTML = '<div class="empty-hint">加载中...</div>';

        try {
            const [sumResp, listResp] = await Promise.all([
                fetch('/api/auto-strategy/sentiments/summary').then(r => r.json()).catch(() => ({code: 500})),
                fetch('/api/auto-strategy/sentiments?days=3').then(r => r.json()).catch(() => ({code: 500})),
            ]);

            const summary = sumResp.code === 200 ? sumResp.data : null;
            const sentiments = listResp.code === 200 ? (listResp.data.sentiments || []) : [];

            let html = '';

            if (summary && summary.total > 0) {
                const score = summary.avg_score || 0;
                const label = score > 0.2 ? '偏多' : score < -0.2 ? '偏空' : '中性';
                const color = score > 0 ? 'var(--danger)' : score < 0 ? 'var(--success)' : 'var(--warning)';
                const posPct = summary.total > 0 ? Math.round((summary.positive || 0) / summary.total * 100) : 0;
                html += `
                    <div class="senti-gauge">
                        <div class="senti-score" style="color:${color}">${score > 0 ? '+' : ''}${score.toFixed(2)}</div>
                        <div class="senti-label">${label}</div>
                        <div class="senti-bar">
                            <span class="senti-bar-pos" style="width:${posPct}%"></span>
                        </div>
                        <div class="senti-counts">正面 ${summary.positive || 0} · 负面 ${summary.negative || 0} · 共 ${summary.total}</div>
                    </div>`;
                const sub = document.getElementById('senti-date-sub');
                if (sub && summary.date) sub.textContent = summary.date;
            } else {
                html += '<div class="senti-gauge"><div class="senti-label" style="margin:8px 0">暂无舆情数据</div></div>';
            }

            if (sentiments.length > 0) {
                html += '<div class="senti-list">';
                html += sentiments.slice(0, 15).map(s => {
                    const sColor = s.sentiment_label === 'positive' ? 'var(--danger)' : s.sentiment_label === 'negative' ? 'var(--success)' : 'var(--text-secondary)';
                    const sIcon = s.sentiment_label === 'positive' ? '▲' : s.sentiment_label === 'negative' ? '▼' : '—';
                    const time = s.created_at ? new Date(s.created_at).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
                    return `<div class="senti-item">
                        <span class="senti-icon" style="color:${sColor}">${sIcon}</span>
                        <div class="senti-body">
                            <div class="senti-title">${this.esc(s.title || '')}</div>
                            <div class="senti-meta">${time}${s.key_factors && s.key_factors.length ? ' · ' + this.esc(s.key_factors.slice(0,3).join('/')) : ''}</div>
                        </div>
                    </div>`;
                }).join('');
                html += '</div>';
            }

            el.innerHTML = html || '<div class="empty-hint">暂无舆情</div>';
        } catch (e) {
            el.innerHTML = '<div class="empty-hint" style="color:var(--danger)">加载失败</div>';
        }
    },

    async loadSentimentView() {
        if (!this._sentiCalDate) this._sentiCalDate = new Date();
        await this.renderSentiCalendar();
        if (!this._sentiSelectedDate) {
            this._sentiSelectedDate = this._sentiLatestDate || this._dateStr(new Date());
        }
        await this.loadSentimentForDate(this._sentiSelectedDate);
    },

    _dateStr(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    },

    async renderSentiCalendar() {
        const calDate = this._sentiCalDate || new Date();
        const year = calDate.getFullYear();
        const month = calDate.getMonth();

        const titleEl = document.getElementById('senti-cal-title');
        const calEl = document.getElementById('senti-calendar');
        if (!calEl) return;
        if (titleEl) titleEl.textContent = `${year}年${month + 1}月`;

        const startDate = this._dateStr(new Date(year, month - 1, 1));
        const endDate = this._dateStr(new Date(year, month + 2, 0));

        try {
            const resp = await fetch(`/api/auto-strategy/sentiments/calendar?start_date=${startDate}&end_date=${endDate}`).then(r => r.json());
            const days = resp.code === 200 ? (resp.data.days || []) : [];
            const dayMap = {};
            days.forEach(d => { dayMap[d.date] = d; });

            const latest = days.map(d => d.date).sort().pop();
            if (latest) this._sentiLatestDate = latest;

            const startWeekday = new Date(year, month, 1).getDay();
            const daysInMonth = new Date(year, month + 1, 0).getDate();
            const today = this._dateStr(new Date());
            const selected = this._sentiSelectedDate;

            let html = ['日', '一', '二', '三', '四', '五', '六']
                .map(w => `<div class="sc-head">${w}</div>`).join('');
            for (let i = 0; i < startWeekday; i++) html += '<div></div>';

            for (let d = 1; d <= daysInMonth; d++) {
                const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
                const dayData = dayMap[dateStr];
                const isSelected = dateStr === selected;
                const isToday = dateStr === today;

                let cls = 'sc-day';
                let scoreHtml = '';
                if (dayData && dayData.avg_score != null) {
                    const score = dayData.avg_score;
                    if (score > 0.3) cls += ' sc-pos-2';
                    else if (score > 0) cls += ' sc-pos-1';
                    else if (score < -0.3) cls += ' sc-neg-2';
                    else if (score < 0) cls += ' sc-neg-1';
                    else cls += ' sc-neu';
                    if (!isSelected) scoreHtml = `<div class="sc-score">${score.toFixed(1)}</div>`;
                }
                if (isSelected) cls += ' sc-selected';
                else if (isToday) cls += ' sc-today';

                const tip = dayData ? `${dateStr} | 均分:${dayData.avg_score != null ? dayData.avg_score.toFixed(2) : '-'} | 共${dayData.total}条` : dateStr;
                html += `<div class="${cls}" title="${tip}" onclick="Workbench.selectSentimentDate('${dateStr}')">
                    <div>${d}</div>${scoreHtml}
                </div>`;
            }
            calEl.innerHTML = html;
        } catch (e) {
            calEl.innerHTML = '<div class="empty-hint" style="color:var(--danger)">日历加载失败</div>';
        }
    },

    sentiCalPrev() {
        const d = this._sentiCalDate || new Date();
        d.setMonth(d.getMonth() - 1);
        this._sentiCalDate = d;
        this.renderSentiCalendar();
    },

    sentiCalNext() {
        const d = this._sentiCalDate || new Date();
        d.setMonth(d.getMonth() + 1);
        this._sentiCalDate = d;
        this.renderSentiCalendar();
    },

    async selectSentimentDate(dateStr) {
        this._sentiSelectedDate = dateStr;
        await this.renderSentiCalendar();
        await this.loadSentimentForDate(dateStr);
    },

    async loadSentimentForDate(dateStr) {
        const sumEl = document.getElementById('senti-view-summary');
        const listEl = document.getElementById('senti-view-list');
        if (!sumEl || !listEl) return;
        sumEl.innerHTML = '<div class="empty-hint">加载中...</div>';
        listEl.innerHTML = '<div class="empty-hint">加载中...</div>';

        try {
            const [sumResp, listResp] = await Promise.all([
                fetch(`/api/auto-strategy/sentiments/summary?target_date=${dateStr}`).then(r => r.json()).catch(() => ({code: 500})),
                fetch(`/api/auto-strategy/sentiments/by-date?target_date=${dateStr}`).then(r => r.json()).catch(() => ({code: 500})),
            ]);

            const summary = sumResp.code === 200 ? sumResp.data : null;
            const sentiments = listResp.code === 200 ? (listResp.data.sentiments || []) : [];

            const dateEl = document.getElementById('senti-view-date');
            if (dateEl) dateEl.textContent = dateStr;
            const countEl = document.getElementById('senti-view-count');
            if (countEl) countEl.textContent = `${dateStr} · ${sentiments.length} 条`;

            if (summary && summary.total > 0) {
                const score = summary.avg_score || 0;
                const label = score > 0.2 ? '偏多' : score < -0.2 ? '偏空' : '中性';
                const color = score > 0 ? 'var(--danger)' : score < 0 ? 'var(--success)' : 'var(--warning)';
                const posPct = Math.round((summary.positive || 0) / summary.total * 100);
                const negPct = Math.round((summary.negative || 0) / summary.total * 100);
                const neuPct = 100 - posPct - negPct;
                sumEl.innerHTML = `
                    <div class="sv-gauge-row">
                        <div class="sv-score-block">
                            <div class="sv-score" style="color:${color}">${score > 0 ? '+' : ''}${score.toFixed(2)}</div>
                            <div class="sv-score-label">${label}</div>
                        </div>
                        <div class="sv-dist-block">
                            <div class="sv-dist-bar">
                                <span class="sv-seg sv-seg-pos" style="width:${posPct}%"></span>
                                <span class="sv-seg sv-seg-neu" style="width:${neuPct}%"></span>
                                <span class="sv-seg sv-seg-neg" style="width:${negPct}%"></span>
                            </div>
                            <div class="sv-dist-legend">
                                <span><i class="sv-dot" style="background:var(--danger)"></i>正面 ${summary.positive || 0}</span>
                                <span><i class="sv-dot" style="background:var(--text-secondary)"></i>中性 ${summary.total - (summary.positive||0) - (summary.negative||0)}</span>
                                <span><i class="sv-dot" style="background:var(--success)"></i>负面 ${summary.negative || 0}</span>
                            </div>
                        </div>
                    </div>
                    <div class="sv-stats-row">
                        <div class="sv-stat"><div class="sv-stat-val">${summary.total}</div><div class="sv-stat-label">总条数</div></div>
                        <div class="sv-stat"><div class="sv-stat-val">${posPct}%</div><div class="sv-stat-label">正面占比</div></div>
                        <div class="sv-stat"><div class="sv-stat-val">${summary.related_etf_count || '-'}</div><div class="sv-stat-label">关联ETF</div></div>
                    </div>`;
            } else {
                sumEl.innerHTML = '<div class="empty-hint">该日期暂无舆情数据</div>';
            }

            if (sentiments.length > 0) {
                listEl.innerHTML = sentiments.map(s => {
                    const sColor = s.sentiment_label === 'positive' ? 'var(--danger)' : s.sentiment_label === 'negative' ? 'var(--success)' : 'var(--text-secondary)';
                    const sIcon = s.sentiment_label === 'positive' ? '▲' : s.sentiment_label === 'negative' ? '▼' : '—';
                    const time = s.created_at ? new Date(s.created_at).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
                    const etfs = (s.related_etfs || []).slice(0, 6);
                    const scoreBadge = s.sentiment_score != null
                        ? `<span class="sv-item-score" style="color:${sColor}">${s.sentiment_score > 0 ? '+' : ''}${s.sentiment_score.toFixed(2)}</span>` : '';
                    const hasDetail = s.content || (s.key_factors && s.key_factors.length) || etfs.length;
                    return `<div class="sv-item ${hasDetail ? 'sv-expandable' : ''}" ${hasDetail ? 'onclick="this.classList.toggle(\'sv-open\')"' : ''}>
                        <div class="sv-item-head">
                            <span class="sv-item-icon" style="color:${sColor}">${sIcon}</span>
                            <span class="sv-item-title">${this.esc(s.title || '')}</span>
                            ${scoreBadge}
                            ${hasDetail ? '<span class="sv-item-arrow">▾</span>' : ''}
                        </div>
                        <div class="sv-item-foot">
                            <span class="sv-item-time">${time}</span>
                            <span class="sv-item-source">${this.esc(s.source || '')}</span>
                        </div>
                        ${hasDetail ? `<div class="sv-item-detail">
                            ${s.content ? `<div class="sv-detail-content">${this.esc(s.content)}</div>` : ''}
                            ${s.key_factors && s.key_factors.length ? `<div class="sv-detail-factors"><span class="sv-detail-label">关键因素</span>${s.key_factors.map(f => `<span class="sv-factor-tag">${this.esc(f)}</span>`).join('')}</div>` : ''}
                            ${etfs.length ? `<div class="sv-detail-etfs"><span class="sv-detail-label">关联ETF</span>${etfs.map(e => `<span class="sv-etf-tag">${this.etfLabel(e)}</span>`).join('')}</div>` : ''}
                        </div>` : ''}
                    </div>`;
                }).join('');
            } else {
                listEl.innerHTML = '<div class="empty-hint">该日期暂无舆情记录</div>';
            }
        } catch (e) {
            if (sumEl) sumEl.innerHTML = '<div class="empty-hint" style="color:var(--danger)">加载失败</div>';
            if (listEl) listEl.innerHTML = '';
        }
    },

    async loadTasksView() {
        const listEl = document.getElementById('tasks-list');
        const statsEl = document.getElementById('tasks-stats');
        const historyEl = document.getElementById('tasks-history');
        if (!statsEl || !historyEl) return;
        if (listEl) listEl.innerHTML = '<div class="empty-hint">加载中...</div>';
        statsEl.innerHTML = '<div class="empty-hint">加载中...</div>';
        historyEl.innerHTML = '<div class="empty-hint">加载中...</div>';

        try {
            const [listResp, statsResp, historyResp] = await Promise.all([
                fetch('/api/tasks/list').then(r => r.json()).catch(() => ({code: 500})),
                fetch('/api/tasks/stats?days=7').then(r => r.json()).catch(() => ({code: 500})),
                fetch('/api/tasks/history?days=7&limit=50').then(r => r.json()).catch(() => ({code: 500})),
            ]);

            const listData = listResp.code === 200 ? listResp.data : null;
            const statsData = statsResp.code === 200 ? statsResp.data : null;
            const historyData = historyResp.code === 200 ? historyResp.data : null;

            // task_name 中文映射（含阶段名）
            const taskNames = {
                'daily_pipeline': '每日管道',
                'weekly_review': '每周复盘',
                'auto_fetch_quotes': '行情补全',
                'sentiment_collect': '舆情采集',
                'sentiment_collect_10': '舆情采集(10:00)',
                'sentiment_collect_12': '舆情采集(12:00)',
                'sentiment_collect_14': '舆情采集(14:00)',
                'daily_pipeline.net_value': '阶段·净值更新',
                'daily_pipeline.quotes': '阶段·行情更新',
                'daily_pipeline.rebalance': '阶段·组合再平衡',
                'daily_pipeline.sentiment': '阶段·舆情采集',
                'daily_pipeline.policy_flow': '阶段·政策与资金流',
                'daily_pipeline.market_scan': '阶段·市场扫描',
                'daily_pipeline.rotation_review': '阶段·轮动复盘',
                'daily_pipeline.autonomous': '阶段·自主决策',
            };

            // 渲染任务列表（含手动触发按钮）
            if (listEl) {
                if (listData && listData.tasks && listData.tasks.length > 0) {
                    listEl.innerHTML = listData.tasks.map(t => {
                        const lastRun = t.last_run ? parseServerTime(t.last_run) : null;
                        const lastRunStr = lastRun ? lastRun.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '从未执行';
                        const running = (t.running_count || 0) > 0;
                        const statusBadge = running
                            ? '<span class="task-badge task-badge-running">运行中</span>'
                            : (t.failed_count > 0 ? `<span class="task-badge task-badge-warn">失败 ${t.failed_count}</span>` : '<span class="task-badge task-badge-ok">正常</span>');
                        const triggerBtn = t.trigger
                            ? `<button class="btn-trigger" data-trigger="${t.trigger}" data-name="${this.esc(t.name)}">▶ 立即执行</button>`
                            : '<span class="task-badge task-badge-muted">仅定时</span>';
                        return `<div class="task-row">
                            <div class="task-row-main">
                                <div class="task-row-name">${this.esc(t.name)} ${statusBadge}</div>
                                <div class="task-row-desc">${this.esc(t.description || '')}</div>
                                <div class="task-row-meta">
                                    <span>📅 ${this.esc(t.schedule || '')}</span>
                                    <span>🔢 总${t.total_executions||0} · 成${t.success_count||0} · 败${t.failed_count||0}</span>
                                    <span>🕒 上次: ${lastRunStr}</span>
                                </div>
                            </div>
                            <div class="task-row-action">${triggerBtn}</div>
                        </div>`;
                    }).join('');

                    // 绑定触发按钮
                    listEl.querySelectorAll('.btn-trigger').forEach(btn => {
                        btn.addEventListener('click', () => this.triggerTask(btn.dataset.trigger, btn.dataset.name));
                    });
                } else {
                    listEl.innerHTML = '<div class="empty-hint">暂无已注册任务</div>';
                }
            }

            // 渲染统计
            if (statsData && statsData.stats) {
                const subEl = document.getElementById('tasks-stats-sub');
                if (subEl) subEl.textContent = `近${statsData.days}天 · ${statsData.total_executions}次执行`;

                statsEl.innerHTML = `<div class="task-stats-grid">
                    ${Object.entries(statsData.stats).map(([name, s]) => `
                        <div class="task-stat-card">
                            <div class="task-stat-name">${taskNames[name] || name}</div>
                            <div class="task-stat-row">
                                <span class="task-stat-label">总计</span>
                                <span class="task-stat-val">${s.total}</span>
                            </div>
                            <div class="task-stat-row">
                                <span class="task-stat-label">成功</span>
                                <span class="task-stat-val text-up">${s.success}</span>
                            </div>
                            <div class="task-stat-row">
                                <span class="task-stat-label">失败</span>
                                <span class="task-stat-val text-down">${s.failed}</span>
                            </div>
                            <div class="task-stat-row">
                                <span class="task-stat-label">平均耗时</span>
                                <span class="task-stat-val">${s.avg_duration}s</span>
                            </div>
                        </div>
                    `).join('')}
                </div>`;
            } else {
                statsEl.innerHTML = '<div class="empty-hint">暂无统计数据</div>';
            }

            // 渲染历史
            if (historyData && historyData.logs && historyData.logs.length > 0) {
                const subEl = document.getElementById('tasks-history-sub');
                if (subEl) subEl.textContent = `${historyData.total}条记录`;

                historyEl.innerHTML = historyData.logs.map(log => {
                    const statusColor = log.status === 'success' ? 'var(--success)' : log.status === 'failed' ? 'var(--danger)' : 'var(--warning)';
                    const statusIcon = log.status === 'success' ? '✓' : log.status === 'failed' ? '✕' : '…';
                    const time = parseServerTime(log.started_at) ? parseServerTime(log.started_at).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
                    const duration = log.duration_seconds ? `${log.duration_seconds.toFixed(1)}s` : '-';

                    return `<div class="task-log-item">
                        <div class="task-log-head">
                            <span class="task-log-icon" style="color:${statusColor}">${statusIcon}</span>
                            <span class="task-log-name">${taskNames[log.task_name] || log.task_name}</span>
                            <span class="task-log-duration">${duration}</span>
                            <span class="task-log-time">${time}</span>
                        </div>
                        ${log.result_summary ? `<div class="task-log-summary">${this.esc(JSON.stringify(log.result_summary))}</div>` : ''}
                        ${log.error_message ? `<div class="task-log-error">${this.esc(log.error_message)}</div>` : ''}
                    </div>`;
                }).join('');
            } else {
                historyEl.innerHTML = '<div class="empty-hint">暂无执行记录</div>';
            }
        } catch (e) {
            if (listEl) listEl.innerHTML = '<div class="empty-hint" style="color:var(--danger)">加载失败</div>';
            statsEl.innerHTML = '<div class="empty-hint" style="color:var(--danger)">加载失败</div>';
            historyEl.innerHTML = '';
        }
    },

    async triggerTask(trigger, name) {
        if (!trigger) return;
        if (!confirm(`确定要立即执行「${name || trigger}」吗？\n任务将在后台异步运行，可在执行历史中查看结果。`)) return;
        try {
            const resp = await fetch(`/api/tasks/trigger/${trigger}`, {method: 'POST'});
            const data = await resp.json();
            if (data.code === 200) {
                alert(`✓ ${name || trigger} 已提交后台执行`);
                // 刷新视图
                setTimeout(() => this.loadTasksView(), 1000);
            } else {
                alert(`✕ 触发失败: ${data.message || '未知错误'}`);
            }
        } catch (e) {
            alert(`✕ 触发失败: ${e.message}`);
        }
    },

    renderTimeline(actions) {
        const el = document.getElementById('ai-timeline');
        if (!el) return;

        if (!actions || actions.length === 0) {
            el.innerHTML = '<div style="color:var(--text-secondary);font-size:13px;padding:12px 0;">暂无AI决策记录</div>';
            return;
        }

        const triggerLabel = { daily: '每日管道', condition: '条件触发', manual: '手动触发' };
        const statusMeta = {
            completed: { icon: '✓', cls: 'tl-done', label: '已完成' },
            failed: { icon: '✕', cls: 'tl-fail', label: '失败' },
            pending_approval: { icon: '…', cls: 'tl-pending', label: '待审批' },
        };

        el.innerHTML = actions.map((a, idx) => {
            const meta = statusMeta[a.status] || statusMeta.completed;
            const time = a.created_at ? new Date(a.created_at) : null;
            const timeStr = time ? time.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
            const relStr = time ? this.relativeTime(time) : '';
            const reasoning = a.reasoning || '';
            const tools = (a.tools_called || []).map(t => typeof t === 'string' ? t : (t.tool || t.name || '')).filter(Boolean);
            const actionsTaken = (a.actions_taken || []).map(t => typeof t === 'string' ? t : (t.action || t.tool || '')).filter(Boolean);
            const chips = [...new Set([...actionsTaken, ...tools])].slice(0, 6);
            const pending = a.approval_status === 'pending';
            const hasDetail = reasoning.length > 40 || chips.length > 0;

            return `
                <div class="tl-item ${idx === 0 ? 'tl-latest' : ''} ${hasDetail ? 'tl-expandable' : ''}"
                     ${hasDetail ? `onclick="this.classList.toggle('tl-open')"` : ''}>
                    <div class="tl-node ${meta.cls}"><span>${meta.icon}</span></div>
                    <div class="tl-body">
                        <div class="tl-head">
                            <span class="tl-trigger">${triggerLabel[a.trigger_type] || a.trigger_type}</span>
                            <span class="tl-status ${meta.cls}">${meta.label}</span>
                            ${pending ? '<span class="tl-status tl-pending">待审批</span>' : ''}
                            <span class="tl-time" title="${timeStr}">${relStr || timeStr}</span>
                            ${hasDetail ? '<span class="tl-arrow">▾</span>' : ''}
                        </div>
                        <div class="tl-summary">${this.esc(reasoning.split('\n')[0].substring(0, 50)) || '<span class="tl-empty">无推理记录</span>'}${reasoning.length > 50 ? '…' : ''}</div>
                        <div class="tl-detail">
                            <div class="tl-reasoning">${this.esc(reasoning)}</div>
                            ${chips.length ? `<div class="tl-chips">${chips.map(c => `<span class="tl-chip">${this.esc(c)}</span>`).join('')}</div>` : ''}
                        </div>
                    </div>
                </div>`;
        }).join('');
    },

    relativeTime(dt) {
        const diff = Date.now() - dt.getTime();
        const min = Math.floor(diff / 60000);
        if (min < 1) return '刚刚';
        if (min < 60) return `${min}分钟前`;
        const hr = Math.floor(min / 60);
        if (hr < 24) return `${hr}小时前`;
        const day = Math.floor(hr / 24);
        if (day < 7) return `${day}天前`;
        return dt.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit'});
    },

    _mktSort: 'composite_score',
    _mktDesc: true,
    _mktQuery: '',
    _mktPage: 1,
    _mktPageSize: 50,
    _mktDate: '',
    _mktDates: [],

    async loadMarket() {
        const el = document.getElementById('market-content');
        if (!el) return;
        el.innerHTML = '<div class="empty-hint">加载中...</div>';

        try {
            const params = new URLSearchParams({
                sort_by: this._mktSort,
                desc: this._mktDesc,
                q: this._mktQuery,
                page: this._mktPage,
                page_size: this._mktPageSize,
            });
            if (this._mktDate) params.set('date', this._mktDate);
            const resp = await fetch(`/api/workbench/market-indicators?${params}`);
            const data = await resp.json();
            if (data.code !== 200) { el.innerHTML = '<div>加载失败</div>'; return; }

            const rows = data.data.rows || [];
            const total = data.data.total || 0;
            // 同步日期显示
            if (data.data.date) {
                this._mktDate = data.data.date;
                const picker = document.getElementById('mkt-date-picker');
                if (picker && picker.value !== data.data.date) picker.value = data.data.date;
            }

            this.renderMarketTable(el, rows);
            this.renderMarketPagination(total);

            if (rows.length === 0) { el.innerHTML = '<div class="empty-hint">无匹配数据</div>'; }
        } catch (e) {
            el.innerHTML = '<div style="color:var(--danger)">请求失败</div>';
        }
    },

    renderMarketTable(el, rows) {
        if (rows.length === 0) return;
        const maxScore = Math.max(...rows.map(r => r.composite_score), 1);

        const sortArrow = (key) => this._mktSort === key
            ? `<span class="sort-arrow">${this._mktDesc ? '▼' : '▲'}</span>` : '';
        const thSortable = (key, label) => `<th class="num sortable" data-sort="${key}" onclick="Workbench.sortMarket('${key}')">${label}${sortArrow(key)}</th>`;

        el.innerHTML = `
            <table class="mkt-table">
                <thead><tr>
                    <th>#</th>
                    <th class="sortable" data-sort="etf_name" onclick="Workbench.sortMarket('etf_name')">ETF${sortArrow('etf_name')}</th>
                    ${thSortable('close_price', '最新价')}
                    ${thSortable('change_pct', '今日')}
                    ${thSortable('momentum_5d', '5日动量')}
                    ${thSortable('momentum_20d', '20日动量')}
                    ${thSortable('trend_strength', '趋势')}
                    ${thSortable('volatility_20d', '波动率')}
                    ${thSortable('vol_ratio', '量比')}
                    ${thSortable('composite_score', '综合得分')}
                </tr></thead>
                <tbody>${rows.map((r, i) => `
                    <tr class="mkt-row ${i < 3 ? 'mkt-top' : ''}" onclick="Workbench.toggleEtfDetail('${r.etf_code}', this)">
                        <td class="mkt-rank">${r.rank || ((this._mktPage - 1) * this._mktPageSize + i + 1)}</td>
                            <td class="mkt-etf">
                                <span class="mkt-name">${this.esc(r.etf_name || '-')}</span>
                                <span class="mkt-code">${r.etf_code}</span>
                            </td>
                            <td class="num">${r.close_price != null ? r.close_price.toFixed(3) : '-'}</td>
                            <td class="num ${(r.change_pct||0) >= 0 ? 'text-up' : 'text-down'}">${r.change_pct != null ? (r.change_pct >= 0 ? '+' : '') + r.change_pct.toFixed(2) + '%' : '-'}</td>
                            <td class="num ${(r.momentum_5d||0) >= 0 ? 'text-up' : 'text-down'}">${r.momentum_5d >= 0 ? '+' : ''}${r.momentum_5d}%</td>
                            <td class="num ${(r.momentum_20d||0) >= 0 ? 'text-up' : 'text-down'}">${r.momentum_20d >= 0 ? '+' : ''}${r.momentum_20d}%</td>
                            <td class="num"><span class="trend-dots">${this.trendDots(r.trend_strength)}</span></td>
                            <td class="num">${r.volatility_20d != null ? r.volatility_20d + '%' : '-'}</td>
                            <td class="num ${r.vol_ratio >= 1.2 ? 'text-up' : r.vol_ratio <= 0.8 ? 'text-down' : ''}">${r.vol_ratio != null ? r.vol_ratio.toFixed(2) : '-'}</td>
                            <td class="score-col">
                                <div class="score-bar-wrap">
                                    <span class="score-bar" style="width:${(r.composite_score / maxScore * 100).toFixed(0)}%"></span>
                                    <span class="score-val">${r.composite_score}</span>
                                </div>
                            </td>
                        </tr>
                        <tr class="mkt-detail-row" id="mkt-detail-${r.etf_code}" style="display:none">
                            <td colspan="10">
                                <div class="mkt-detail-body">
                                    <div class="mkt-detail-grid">
                                        <div class="mkt-detail-item"><span class="mkt-dl">MA5</span><span class="mkt-dv">${r.ma5 != null ? r.ma5.toFixed(3) : '-'}</span></div>
                                        <div class="mkt-detail-item"><span class="mkt-dl">MA10</span><span class="mkt-dv">${r.ma10 != null ? r.ma10.toFixed(3) : '-'}</span></div>
                                        <div class="mkt-detail-item"><span class="mkt-dl">MA20</span><span class="mkt-dv">${r.ma20 != null ? r.ma20.toFixed(3) : '-'}</span></div>
                                        <div class="mkt-detail-item"><span class="mkt-dl">5日均成交额</span><span class="mkt-dv">${r.amount != null ? (r.amount / 1e8).toFixed(2) + '亿' : '-'}</span></div>
                                        <div class="mkt-detail-item"><span class="mkt-dl">全市场排名</span><span class="mkt-dv">#${r.rank || '-'}</span></div>
                                        <div class="mkt-detail-item"><span class="mkt-dl">趋势强度</span><span class="mkt-dv">${r.trend_strength}/3</span></div>
                                    </div>
                                    <div class="mkt-detail-verdict">
                                        ${r.trend_strength >= 3 && r.momentum_5d > 0 ? '<span class="mkt-tag mkt-tag-bull">强势多头</span>' : ''}
                                        ${r.trend_strength <= 0 && r.momentum_5d < 0 ? '<span class="mkt-tag mkt-tag-bear">弱势空头</span>' : ''}
                                        ${r.vol_ratio >= 1.5 ? '<span class="mkt-tag mkt-tag-vol">放量异动</span>' : ''}
                                        ${r.volatility_20d > 40 ? '<span class="mkt-tag mkt-tag-warn">高波动</span>' : ''}
                                    </div>
                                </div>
                            </td>
                        </tr>
                    `).join('')}</tbody>
                </table>
            `;
    },

    renderMarketPagination(total) {
        const pag = document.getElementById('mkt-pagination');
        if (!pag) return;
        if (total <= this._mktPageSize) {
            pag.innerHTML = '';
            return;
        }
        const totalPages = Math.max(1, Math.ceil(total / this._mktPageSize));
        const cur = Math.min(this._mktPage, totalPages);
        const btn = (label, target, cls, disabled) => `<button class="pg-btn ${cls}" ${disabled ? 'disabled' : ''} onclick="Workbench.goMarketPage(${target})">${label}</button>`;
        const pageBtns = [];
        const start = Math.max(1, cur - 2);
        const end = Math.min(totalPages, start + 4);
        for (let p = start; p <= end; p++) {
            pageBtns.push(`<button class="pg-btn ${p === cur ? 'active' : ''}" onclick="Workbench.goMarketPage(${p})">${p}</button>`);
        }
        pag.innerHTML = `
            <span class="pg-info">共 ${total} 条 · 第 ${cur}/${totalPages} 页</span>
            <div class="pg-btns">
                ${btn('‹', cur - 1, '', cur <= 1)}
                ${pageBtns.join('')}
                ${btn('›', cur + 1, '', cur >= totalPages)}
            </div>
            <select class="pg-size" onchange="Workbench.setMarketPageSize(this.value)">
                <option value="20" ${this._mktPageSize === 20 ? 'selected' : ''}>20/页</option>
                <option value="50" ${this._mktPageSize === 50 ? 'selected' : ''}>50/页</option>
                <option value="100" ${this._mktPageSize === 100 ? 'selected' : ''}>100/页</option>
            </select>
        `;
    },

    goMarketPage(p) {
        this._mktPage = Math.max(1, p);
        this.loadMarket();
    },

    setMarketPageSize(size) {
        this._mktPageSize = parseInt(size, 10);
        this._mktPage = 1;
        this.loadMarket();
    },

    sortMarket(key) {
        if (this._mktSort === key) {
            this._mktDesc = !this._mktDesc;
        } else {
            this._mktSort = key;
            this._mktDesc = key === 'etf_name' ? false : true;
        }
        this._mktPage = 1;
        this.syncMktSortUI(key);
        this.loadMarket();
    },

    syncMktSortUI(key) {
        document.querySelectorAll('.mkt-sort').forEach(b => {
            const isActive = b.dataset.sort === this._mktSort;
            b.classList.toggle('active', isActive);
            b.textContent = b.textContent.replace(/ [↑↓]$/, '');
            if (isActive) b.textContent += this._mktDesc ? ' ↓' : ' ↑';
        });
    },

    trendDots(strength) {
        const colors = ['var(--danger)', 'var(--warning)', 'var(--success)'];
        let html = '';
        for (let i = 0; i < 3; i++) {
            html += `<span class="t-dot" style="background:${i < strength ? colors[Math.min(strength - 1, 2)] : 'var(--border)'}"></span>`;
        }
        return html;
    },

    toggleEtfDetail(code, rowEl) {
        const detailRow = document.getElementById(`mkt-detail-${code}`);
        if (!detailRow) return;
        const isOpen = detailRow.style.display !== 'none';
        document.querySelectorAll('.mkt-detail-row').forEach(r => { r.style.display = 'none'; });
        document.querySelectorAll('.mkt-row').forEach(r => r.classList.remove('row-active'));
        if (!isOpen) {
            detailRow.style.display = 'table-row';
            if (rowEl) rowEl.classList.add('row-active');
        }
    },

    initMarketToolbar() {
        const searchEl = document.getElementById('mkt-search');
        if (searchEl && !searchEl._bound) {
            searchEl._bound = true;
            let timer = null;
            searchEl.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(() => {
                    this._mktQuery = searchEl.value.trim();
                    this._mktPage = 1;
                    this.loadMarket();
                }, 400);
            });
        }

        document.querySelectorAll('.mkt-sort').forEach(btn => {
            if (btn._bound) return;
            btn._bound = true;
            btn.addEventListener('click', () => {
                const sort = btn.dataset.sort;
                if (this._mktSort === sort) {
                    this._mktDesc = !this._mktDesc;
                } else {
                    this._mktSort = sort;
                    this._mktDesc = true;
                }
                this._mktPage = 1;
                this.syncMktSortUI(sort);
                this.loadMarket();
            });
        });

        // 日期选择器
        this._initDatePicker();
    },

    async _initDatePicker() {
        const picker = document.getElementById('mkt-date-picker');
        const prevBtn = document.getElementById('mkt-date-prev');
        const nextBtn = document.getElementById('mkt-date-next');
        if (!picker) return;

        // 加载可用日期列表
        try {
            const resp = await fetch('/api/workbench/market-dates');
            const data = await resp.json();
            if (data.code === 200) this._mktDates = data.data.dates || [];
        } catch (e) { /* ignore */ }

        // 设置 picker 的 min/max
        if (this._mktDates.length > 0) {
            picker.max = this._mktDates[0];
            picker.min = this._mktDates[this._mktDates.length - 1];
        }

        // 初始值：默认最新日期
        if (!this._mktDate && this._mktDates.length > 0) {
            this._mktDate = this._mktDates[0];
        }
        if (this._mktDate) picker.value = this._mktDate;

        // 绑定事件（只绑一次）
        if (picker._bound) return;
        picker._bound = true;

        picker.addEventListener('change', () => {
            this._mktDate = picker.value;
            this._mktPage = 1;
            this.loadMarket();
        });

        if (prevBtn) prevBtn.addEventListener('click', () => this._mktNavDate(1));
        if (nextBtn) nextBtn.addEventListener('click', () => this._mktNavDate(-1));
    },

    _mktNavDate(offset) {
        if (this._mktDates.length === 0) return;
        const cur = this._mktDate || this._mktDates[0];
        const idx = this._mktDates.indexOf(cur);
        const newIdx = idx + offset;
        if (newIdx < 0 || newIdx >= this._mktDates.length) return;
        this._mktDate = this._mktDates[newIdx];
        const picker = document.getElementById('mkt-date-picker');
        if (picker) picker.value = this._mktDate;
        this._mktPage = 1;
        this.loadMarket();
    },

    async loadStrategies() {
        const el = document.getElementById('strategies-content');
        if (!el) return;
        el.innerHTML = '<div style="color:var(--text-secondary)">加载中...</div>';

        try {
            if (Object.keys(this._etfNameMap).length === 0) {
                const etfResp = await fetch('/api/etf/list').then(r => r.json());
                if (etfResp.code === 200) this.buildNameMap(etfResp.data.etfs || []);
            }
            const resp = await fetch('/api/strategy/list');
            const data = await resp.json();
            if (data.code !== 200) { el.innerHTML = '<div>加载失败</div>'; return; }

            const list = data.data.strategies || [];
            this._strategies = list;
            if (list.length === 0) {
                el.innerHTML = '<div style="color:var(--text-secondary);padding:16px 0;">暂无策略，可通过AI对话创建</div>';
                return;
            }

            el.innerHTML = list.map(s => {
                const allocSrc = s.pending_allocation || s.allocation_config;
                const alloc = allocSrc ? Object.entries(allocSrc).slice(0,3).map(([k,v]) => `${this.etfLabel(k)} ${(v*100).toFixed(0)}%`).join(' · ') : '-';
                const statusBadge = s.status === 'active'
                    ? '<span class="s-badge s-active">活跃</span>'
                    : '<span class="s-badge s-paused">已暂停</span>';
                const pendingBadge = s.pending_allocation
                    ? `<span class="s-badge s-pending" title="提交于 ${s.pending_set_date || '-'}，下一交易日生效">调仓待生效</span>`
                    : '';
                return `
                <div class="strat-block expanded" id="strat-block-${s.id}">
                    <div class="strat-block-head" onclick="Workbench.toggleBacktest(${s.id})">
                        <div class="strat-block-left">
                            <span class="strat-expand-icon" id="strat-icon-${s.id}">▾</span>
                            <span class="strat-block-name">${this.esc(s.name)}</span>
                            ${statusBadge}
                            ${pendingBadge}
                            <span class="strat-block-type">${s.strategy_type || '-'}</span>
                            ${s.holding_start_date ? `<span class="strat-hold-date" title="持仓起始日，用于跟踪实际收益">建仓 ${s.holding_start_date}</span>` : ''}
                        </div>
                        <div class="strat-block-alloc">${alloc}</div>
                    </div>
                    <div class="bt-inline" id="bt-panel-${s.id}" style="display:block">
                        <div class="sh-wrap">
                            <div class="sh-panel">
                                <div class="sh-title">当前持仓</div>
                                <div class="sh-body" id="strat-holdings-${s.id}"><div class="empty-hint">加载中...</div></div>
                            </div>
                            <div class="sh-panel">
                                <div class="sh-title">调仓动态</div>
                                <div class="sh-body" id="strat-adjust-${s.id}"><div class="empty-hint">加载中...</div></div>
                            </div>
                        </div>
                        ${s.holding_start_date ? `
                        <div class="bt-actual" id="bt-actual-${s.id}">
                            <div class="bt-actual-title">实际持仓收益（自 ${s.holding_start_date} 建仓）</div>
                            <div class="bt-actual-body" id="bt-actual-body-${s.id}">加载中...</div>
                        </div>` : ''}
                        <div class="bt-form">
                            <div class="bt-field"><label>开始日期</label><input type="date" id="bt-start-${s.id}"></div>
                            <div class="bt-field"><label>结束日期</label><input type="date" id="bt-end-${s.id}"></div>
                            <div class="bt-field"><label>初始资金</label><input type="number" id="bt-capital-${s.id}" step="10000" value="${s.initial_capital || 100000}"></div>
                            <button class="wb-btn-primary" id="bt-run-${s.id}" onclick="Workbench.runBacktest(${s.id})">开始回测</button>
                        </div>
                        <div id="bt-result-${s.id}" style="display:none">
                            <div class="bt-stats-grid" id="bt-stats-${s.id}"></div>
                            <div class="bt-charts-row">
                                <div class="bt-chart-panel">
                                    <div class="bt-chart-title">收益曲线</div>
                                    <div id="bt-chart-${s.id}" style="height:340px"></div>
                                </div>
                                <div class="bt-chart-panel">
                                    <div class="bt-chart-title">回撤曲线</div>
                                    <div id="bt-dd-chart-${s.id}" style="height:340px"></div>
                                </div>
                            </div>
                            <div class="bt-chart-panel">
                                <div class="bt-chart-title">月度收益</div>
                                <div id="bt-monthly-${s.id}" style="overflow-x:auto"></div>
                            </div>
                        </div>
                    </div>
                </div>`;
            }).join('');

            list.forEach(s => {
                const startEl = document.getElementById(`bt-start-${s.id}`);
                const endEl = document.getElementById(`bt-end-${s.id}`);
                if (startEl && !startEl.value) { const d = new Date(); d.setFullYear(d.getFullYear() - 1); startEl.value = d.toISOString().slice(0, 10); }
                if (endEl && !endEl.value) endEl.value = new Date().toISOString().slice(0, 10);
                this.loadStrategyHoldings(s);
                if (s.holding_start_date) this.loadActualReturn(s.id, s.holding_start_date);
            });
        } catch (e) {
            el.innerHTML = '<div style="color:var(--danger)">请求失败</div>';
        }
    },

    async loadStrategyHoldings(s) {
        const holdEl = document.getElementById(`strat-holdings-${s.id}`);
        const adjEl = document.getElementById(`strat-adjust-${s.id}`);

        // 当前持仓
        if (holdEl) {
            try {
                const resp = await fetch(`/api/portfolio/${s.id}/holdings`).then(r => r.json());
                const holdings = resp.code === 200 ? (resp.data.holdings || []) : [];
                if (holdings.length === 0) {
                    holdEl.innerHTML = '<div class="empty-hint">尚未建仓</div>';
                } else {
                    const total = holdings.reduce((sum, h) => sum + (h.market_value || 0), 0);
                    holdEl.innerHTML = `<table class="wb-table sh-table">
                        <thead><tr><th>代码</th><th>数量</th><th>成本</th><th>现价</th><th>市值</th><th>占比</th></tr></thead>
                        <tbody>${holdings.map(h => {
                            const pct = total > 0 ? (h.market_value / total * 100).toFixed(1) : '0.0';
                            return `<tr>
                                <td>${this.etfLabel(h.etf_code)}</td>
                                <td>${h.quantity}</td>
                                <td>${Number(h.avg_cost).toFixed(3)}</td>
                                <td>${Number(h.current_price).toFixed(3)}</td>
                                <td>${Math.round(h.market_value).toLocaleString()}</td>
                                <td>${pct}%</td>
                            </tr>`;
                        }).join('')}</tbody>
                    </table>
                    <div class="sh-total">持仓市值 ¥${Math.round(total).toLocaleString()}</div>`;
                }
            } catch (e) {
                holdEl.innerHTML = '<div class="empty-hint" style="color:var(--danger)">加载失败</div>';
            }
        }

        // 调仓动态：待生效配置对比 + 最近交易记录
        if (adjEl) {
            try {
                let html = '';
                if (s.pending_allocation) {
                    const oldAlloc = s.allocation_config || {};
                    const newAlloc = s.pending_allocation || {};
                    const codes = [...new Set([...Object.keys(oldAlloc), ...Object.keys(newAlloc)])];
                    html += `<div class="sh-pending">
                        <div class="sh-pending-title"><span class="s-badge s-pending">待生效</span> 提交于 ${s.pending_set_date || '-'}，下一交易日执行</div>
                        <table class="wb-table sh-table">
                            <thead><tr><th>代码</th><th>当前</th><th>目标</th><th>变化</th></tr></thead>
                            <tbody>${codes.map(c => {
                                const ov = oldAlloc[c] || 0;
                                const nv = newAlloc[c] || 0;
                                const diff = nv - ov;
                                const diffCls = diff > 0 ? 'text-up' : diff < 0 ? 'text-down' : '';
                                const diffTxt = diff === 0 ? '-' : `${diff > 0 ? '+' : ''}${(diff * 100).toFixed(1)}%`;
                                return `<tr>
                                    <td>${this.etfLabel(c)}</td>
                                    <td>${(ov * 100).toFixed(1)}%</td>
                                    <td>${(nv * 100).toFixed(1)}%</td>
                                    <td class="${diffCls}">${diffTxt}</td>
                                </tr>`;
                            }).join('')}</tbody>
                        </table>
                    </div>`;
                }

                const resp = await fetch(`/api/portfolio/${s.id}/trades`).then(r => r.json());
                const trades = resp.code === 200 ? (resp.data.trades || []) : [];
                if (trades.length > 0) {
                    html += `<div class="sh-trades-title">最近交易</div>
                        <div class="sh-trades">${trades.slice(0, 6).map(t => {
                            const dirCls = t.direction === 'buy' ? 'text-up' : 'text-down';
                            const dirTxt = t.direction === 'buy' ? '买入' : '卖出';
                            return `<div class="sh-trade-item">
                                <span class="sh-trade-date">${t.trade_date.slice(5)}</span>
                                <span class="sh-trade-dir ${dirCls}">${dirTxt}</span>
                                <span class="sh-trade-code">${this.etfLabel(t.etf_code)}</span>
                                <span class="sh-trade-amt">${t.quantity}股 @${Number(t.price).toFixed(3)}</span>
                            </div>`;
                        }).join('')}</div>`;
                }

                adjEl.innerHTML = html || '<div class="empty-hint">暂无调仓记录</div>';
            } catch (e) {
                adjEl.innerHTML = '<div class="empty-hint" style="color:var(--danger)">加载失败</div>';
            }
        }
    },

    toggleBacktest(id) {
        const panel = document.getElementById(`bt-panel-${id}`);
        const icon = document.getElementById(`strat-icon-${id}`);
        if (!panel) return;

        const isOpen = panel.style.display !== 'none';
        document.querySelectorAll('.bt-inline').forEach(p => { p.style.display = 'none'; });
        document.querySelectorAll('.strat-expand-icon').forEach(i => { i.textContent = '▸'; });
        document.querySelectorAll('.strat-block').forEach(b => b.classList.remove('expanded'));

        if (!isOpen) {
            panel.style.display = 'block';
            if (icon) icon.textContent = '▾';
            const block = document.getElementById(`strat-block-${id}`);
            if (block) block.classList.add('expanded');

            const s = (this._strategies || []).find(x => x.id === id);
            this._btStrategy = s;

            if (s && s.holding_start_date) {
                this.loadActualReturn(id, s.holding_start_date);
            }

            const end = new Date();
            const start = new Date(); start.setFullYear(start.getFullYear() - 1);
            const startEl = document.getElementById(`bt-start-${id}`);
            const endEl = document.getElementById(`bt-end-${id}`);
            if (startEl && !startEl.value) startEl.value = start.toISOString().slice(0, 10);
            if (endEl && !endEl.value) endEl.value = end.toISOString().slice(0, 10);

            panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    },

    async loadActualReturn(id, startDate) {
        const bodyEl = document.getElementById(`bt-actual-body-${id}`);
        if (!bodyEl) return;
        try {
            const histResp = await fetch(`/api/portfolio/${id}/history?start_date=${startDate}`).then(r => r.json());
            const snapshots = (histResp.code === 200 ? histResp.data.snapshots : []);
            if (snapshots.length === 0) {
                bodyEl.innerHTML = '<span class="text-secondary">暂无该日期之后的快照数据</span>';
                return;
            }
            const first = snapshots[0].total_asset;
            const last = snapshots[snapshots.length - 1].total_asset;
            const days = snapshots.length;
            const totalPct = first > 0 ? (last / first - 1) * 100 : 0;

            let peak = first;
            let maxDrawdown = 0;
            snapshots.forEach(s => {
                if (s.total_asset > peak) peak = s.total_asset;
                const dd = peak > 0 ? (s.total_asset / peak - 1) * 100 : 0;
                if (dd < maxDrawdown) maxDrawdown = dd;
            });

            const firstDate = snapshots[0].trade_date;
            const lastDate = snapshots[snapshots.length - 1].trade_date;
            bodyEl.innerHTML = `
                <div class="bt-actual-stats">
                    <div class="bt-actual-stat"><label>区间</label><span>${firstDate} → ${lastDate}</span></div>
                    <div class="bt-actual-stat"><label>交易日</label><span>${days} 天</span></div>
                    <div class="bt-actual-stat"><label>期末资产</label><span>¥${last.toLocaleString()}</span></div>
                    <div class="bt-actual-stat"><label>累计收益</label><span class="${totalPct >= 0 ? 'text-up' : 'text-down'}">${totalPct >= 0 ? '+' : ''}${totalPct.toFixed(2)}%</span></div>
                    <div class="bt-actual-stat"><label>最大回撤</label><span class="text-down">${maxDrawdown.toFixed(2)}%</span></div>
                </div>
                <div class="bt-actual-chart" id="bt-actual-chart-${id}" style="height:240px"></div>
            `;
            this.renderActualChart(id, snapshots);
        } catch (e) {
            bodyEl.innerHTML = '<span style="color:var(--danger)">加载失败</span>';
        }
    },

    renderActualChart(id, snapshots) {
        const dom = document.getElementById(`bt-actual-chart-${id}`);
        if (!dom || typeof echarts === 'undefined') return;
        const dates = snapshots.map(s => s.trade_date);
        const pcts = snapshots.map(s => Number(s.profit_pct));
        const assets = snapshots.map(s => s.total_asset);
        if (this._btActualChart) this._btActualChart.dispose();
        this._btActualChart = echarts.init(dom);
        this._btActualChart.setOption({
            tooltip: {
                trigger: 'axis', backgroundColor: '#ffffff', borderColor: '#dfe6ee',
                textStyle: { color: '#1c2b3a', fontSize: 12 },
                formatter: p => `${p[0].axisValue}<br/>资产: ¥${Number(assets[p[0].dataIndex]).toLocaleString()}<br/>收益: ${Number(pcts[p[0].dataIndex]).toFixed(2)}%`,
            },
            grid: { left: 48, right: 48, top: 24, bottom: 28 },
            xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: '#5c6f82', fontSize: 11 }, axisLine: { lineStyle: { color: '#d5dee8' } } },
            yAxis: { type: 'value', name: '累计收益%', axisLabel: { color: '#5c6f82', fontSize: 11, formatter: v => v + '%' }, splitLine: { lineStyle: { color: '#e8eef4' } } },
            series: [{
                type: 'line', data: pcts, smooth: true, symbol: 'none',
                lineStyle: { color: '#0284c7', width: 2 },
                areaStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(2,132,199,0.18)' },
                        { offset: 1, color: 'rgba(2,132,199,0)' },
                    ]),
                },
                markPoint: {
                    data: [
                        { type: 'max', name: '最高', itemStyle: { color: '#16a34a' } },
                        { type: 'min', name: '最低', itemStyle: { color: '#dc2626' } },
                    ],
                },
            }],
        });
    },

    async runBacktest(id) {        const s = (this._strategies || []).find(x => x.id === id) || this._btStrategy;
        if (!s) return;
        const btn = document.getElementById(`bt-run-${id}`);
        if (btn) { btn.disabled = true; btn.textContent = '回测中...'; }
        const body = {
            strategy_id: s.id,
            start_date: document.getElementById(`bt-start-${id}`).value,
            end_date: document.getElementById(`bt-end-${id}`).value,
            initial_capital: Number(document.getElementById(`bt-capital-${id}`).value),
        };
        try {
            const resp = await fetch('/api/backtest/run', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
            });
            const res = await resp.json();
            if (!resp.ok || res.code !== 200) throw new Error(res.detail || res.message || '回测失败');
            this.renderBacktestResult(res.data, id);
        } catch (e) {
            const r = document.getElementById(`bt-result-${id}`);
            if (r) { r.style.display = 'block'; r.innerHTML = `<div style="color:var(--danger);padding:12px 0">回测失败：${this.esc(e.message)}</div>`; }
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '开始回测'; }
        }
    },

    renderBacktestResult(d, id) {
        const wrap = document.getElementById(`bt-result-${id}`);
        if (!wrap) return;
        wrap.style.display = 'block';
        const ret = d.total_return_pct || 0;
        const annRet = d.annualized_return_pct || 0;
        const stats = document.getElementById(`bt-stats-${id}`);
        if (stats) {
            stats.innerHTML = [
                ['最终资产', '¥' + (d.final_asset || 0).toLocaleString(), ''],
                ['总收益率', (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%', ret >= 0 ? 'text-up' : 'text-down'],
                ['年化收益', (annRet >= 0 ? '+' : '') + annRet.toFixed(2) + '%', annRet >= 0 ? 'text-up' : 'text-down'],
                ['最大回撤', (d.max_drawdown_pct || 0).toFixed(2) + '%', 'text-down'],
                ['回撤持续', (d.max_drawdown_duration || 0) + '天', ''],
                ['年化波动率', d.annualized_volatility != null ? d.annualized_volatility.toFixed(2) + '%' : '-', ''],
                ['Sharpe', d.sharpe_ratio != null ? Number(d.sharpe_ratio).toFixed(2) : '-', ''],
                ['Sortino', d.sortino_ratio != null ? Number(d.sortino_ratio).toFixed(2) : '-', ''],
                ['Calmar', d.calmar_ratio != null ? Number(d.calmar_ratio).toFixed(2) : '-', ''],
                ['胜率', d.win_rate != null ? d.win_rate.toFixed(1) + '%' : '-', ''],
                ['交易次数', d.trade_count || 0, ''],
            ].map(([l, v, c]) => `<div class="bt-stat"><div class="bt-stat-label">${l}</div><div class="bt-stat-value ${c}">${v}</div></div>`).join('');
        }
        this.renderBacktestChart(d.daily_data || [], id);
        this.renderDrawdownChart(d.drawdown_curve || [], d.daily_data || [], id);
        this.renderMonthlyReturns(d.period_returns || [], id);
    },

    renderBacktestChart(daily, id) {
        const dom = document.getElementById(`bt-chart-${id}`);
        if (!dom || typeof echarts === 'undefined') return;
        if (!daily.length) { dom.innerHTML = '<div class="empty-hint">无每日数据</div>'; return; }
        const dates = daily.map(x => x.date);
        const assets = daily.map(x => x.total_asset);
        const rets = daily.map(x => x.profit_pct);
        if (this._btChart) this._btChart.dispose();
        this._btChart = echarts.init(dom);
        this._btChart.setOption({
            tooltip: {
                trigger: 'axis', backgroundColor: '#ffffff', borderColor: '#dfe6ee',
                textStyle: { color: '#1c2b3a', fontSize: 12 },
                formatter: p => `${p[0].axisValue}<br/>资产: ¥${Number(assets[p[0].dataIndex]).toLocaleString()}<br/>收益: ${Number(rets[p[0].dataIndex]).toFixed(2)}%`,
            },
            legend: { data: ['总资产', '收益率'], textStyle: { color: '#5c6f82', fontSize: 12 }, top: 0 },
            grid: { left: 56, right: 56, top: 36, bottom: 32 },
            xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: '#5c6f82', fontSize: 11 }, axisLine: { lineStyle: { color: '#d5dee8' } } },
            yAxis: [
                { type: 'value', name: '资产', axisLabel: { color: '#5c6f82', fontSize: 11, formatter: v => v >= 1e4 ? (v / 1e4).toFixed(0) + '万' : v }, splitLine: { lineStyle: { color: '#e8eef4' } } },
                { type: 'value', name: '收益%', axisLabel: { color: '#5c6f82', fontSize: 11, formatter: v => v + '%' }, splitLine: { show: false } },
            ],
            series: [
                { name: '总资产', type: 'line', data: assets, smooth: true, symbol: 'none', lineStyle: { width: 2, color: '#0284c7' }, areaStyle: { color: 'rgba(2,132,199,0.08)' } },
                { name: '收益率', type: 'line', yAxisIndex: 1, data: rets, smooth: true, symbol: 'none', lineStyle: { width: 1.5, color: '#16a34a' } },
            ],
        });
    },

    renderDrawdownChart(ddCurve, daily, id) {
        const dom = document.getElementById(`bt-dd-chart-${id}`);
        if (!dom || typeof echarts === 'undefined') return;
        if (!ddCurve.length || !daily.length) { dom.innerHTML = '<div class="empty-hint">无回挹数据</div>'; return; }
        const dates = daily.map(x => x.date);
        const ddVals = ddCurve.map(p => -p.dd_pct); // 取负值显示为向下面积
        if (this._btDdChart) this._btDdChart.dispose();
        this._btDdChart = echarts.init(dom);
        this._btDdChart.setOption({
            tooltip: {
                trigger: 'axis', backgroundColor: '#ffffff', borderColor: '#dfe6ee',
                textStyle: { color: '#1c2b3a', fontSize: 12 },
                formatter: p => `${p[0].axisValue}<br/>回挹: ${(-p[0].value).toFixed(2)}%`,
            },
            grid: { left: 48, right: 16, top: 24, bottom: 32 },
            xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: '#5c6f82', fontSize: 11 }, axisLine: { lineStyle: { color: '#d5dee8' } } },
            yAxis: {
                type: 'value', name: '回挹%',
                axisLabel: { color: '#5c6f82', fontSize: 11, formatter: v => v + '%' },
                splitLine: { lineStyle: { color: '#e8eef4' } },
            },
            series: [{
                type: 'line', data: ddVals, smooth: true, symbol: 'none',
                lineStyle: { width: 1.5, color: '#dc2626' },
                areaStyle: { color: 'rgba(220,38,38,0.08)' },
            }],
        });
    },

    renderMonthlyReturns(monthly, id) {
        const el = document.getElementById(`bt-monthly-${id}`);
        if (!el) return;
        if (!monthly || monthly.length === 0) { el.innerHTML = '<div class="empty-hint">无月度数据</div>'; return; }
        el.innerHTML = `
            <table class="wb-table" style="font-size:12px;">
                <thead><tr><th>月份</th><th>月初资产</th><th>月末资产</th><th>收益率</th></tr></thead>
                <tbody>${monthly.map(m => {
                    const cls = m.return_pct >= 0 ? 'text-up' : 'text-down';
                    return `<tr>
                        <td>${m.period}</td>
                        <td>¥${(m.start_asset||0).toLocaleString()}</td>
                        <td>¥${(m.end_asset||0).toLocaleString()}</td>
                        <td class="${cls}">${m.return_pct >= 0 ? '+' : ''}${m.return_pct.toFixed(2)}%</td>
                    </tr>`;
                }).join('')}</tbody>
            </table>
        `;
    },

    updateFooter(d) {
        const el = document.getElementById('footer-status');
        if (el && d) {
            el.textContent = `ETF: ${d.data.etf_count} | 策略: ${d.strategies.active} | 数据: ${d.data.latest_quote_date || '无'}`;
        }
    },

    refresh() {
        if (this.currentView === 'overview') this.loadOverview();
        if (this.currentView === 'analyses') this.loadAnalysesView();
    },

    // === 分析视图 ===

    async loadAnalysesView() {
        const dailyEl = document.getElementById('analyses-daily-list');
        const rulesEl = document.getElementById('analyses-rules-list');
        const allocEl = document.getElementById('analyses-allocation-timeline');
        if (dailyEl) dailyEl.innerHTML = '<div class="empty-hint">加载中...</div>';

        // 确保 ETF 名称映射已加载
        if (Object.keys(this._etfNameMap).length === 0) {
            try {
                const etfResp = await fetch('/api/etf/list').then(r => r.json());
                if (etfResp.code === 200) this.buildNameMap(etfResp.data.etfs || []);
            } catch (e) { /* ignore */ }
        }

        try {
            const [dailyResp, rulesResp] = await Promise.all([
                fetch('/api/workbench/daily-analysis?days=60').then(r => r.json()).catch(() => ({ code: 500 })),
                fetch('/api/workbench/rules?days=60').then(r => r.json()).catch(() => ({ code: 500 })),
            ]);

            if (dailyResp.code === 200) {
                const analyses = dailyResp.data.analyses || [];
                document.getElementById('analyses-count-sub').textContent = `${analyses.length}天`;
                this.renderDailyAnalyses(analyses);
            } else {
                dailyEl.innerHTML = '<div class="empty-hint">加载失败</div>';
            }

            if (rulesResp.code === 200) {
                const rules = rulesResp.data.rules || [];
                const allocHist = rulesResp.data.allocation_history || [];
                document.getElementById('rules-count-sub').textContent = `${rules.length}条规则`;
                this.renderRules(rules);
                this.renderAllocationTimeline(allocHist);
            } else {
                rulesEl.innerHTML = '<div class="empty-hint">加载失败</div>';
            }
        } catch (e) {
            console.error('加载分析视图失败:', e);
            if (dailyEl) dailyEl.innerHTML = '<div class="empty-hint">加载异常</div>';
        }
    },

    renderDailyAnalyses(analyses) {
        const el = document.getElementById('analyses-daily-list');
        if (!el) return;
        if (!analyses.length) {
            el.innerHTML = '<div class="empty-hint">暂无分析数据</div>';
            return;
        }

        const regimeMap = {
            bull: { label: '牛市', cls: 'regime-bull' },
            bull_volatile: { label: '牛市震荡', cls: 'regime-bull-vol' },
            neutral: { label: '中性', cls: 'regime-neutral' },
            bear: { label: '熊市', cls: 'regime-bear' },
            bear_volatile: { label: '熊市震荡', cls: 'regime-bear-vol' },
        };
        const actionMap = {
            rebalance: { label: '调仓', cls: 'action-rebalance' },
            hold: { label: '持有', cls: 'action-hold' },
            reduce: { label: '减仓', cls: 'action-reduce' },
            increase: { label: '加仓', cls: 'action-increase' },
        };
        const riskMap = { low: '低风险', medium: '中风险', high: '高风险' };

        el.innerHTML = analyses.map(a => {
            const rm = regimeMap[a.market_regime] || { label: a.market_regime || '-', cls: '' };
            const am = actionMap[a.suggested_action] || { label: a.suggested_action || '-', cls: '' };
            const risk = riskMap[a.risk_level] || a.risk_level || '-';
            const alloc = a.suggested_allocation || {};
            const allocStr = Object.entries(alloc)
                .sort((x, y) => y[1] - x[1])
                .slice(0, 4)
                .map(([k, v]) => `${this.etfLabel(k)} ${(v * 100).toFixed(0)}%`)
                .join(' · ');
            const signals = (a.key_signals || []).slice(0, 3);

            return `
                <div class="analysis-card">
                    <div class="ac-header">
                        <span class="ac-date">${this.esc(a.log_date)}</span>
                        <span class="ac-badge ${rm.cls}">${this.esc(rm.label)}</span>
                        <span class="ac-badge ${am.cls}">${this.esc(am.label)}</span>
                        <span class="ac-risk">${this.esc(risk)}</span>
                    </div>
                    <div class="ac-body">
                        <div class="ac-alloc">${this.esc(allocStr)}</div>
                        <div class="ac-reason">${this.esc(a.action_reason || '')}</div>
                        ${signals.length ? '<div class="ac-signals">' + signals.map(s => `<div class="ac-signal">${this.esc(s)}</div>`).join('') + '</div>' : ''}
                    </div>
                    <div class="ac-footer">
                        <span class="ac-meta">技术:${this.esc(a.technical_trend || '-')} 情绪:${this.esc(a.sentiment || '-')} 宏观:${this.esc(a.macro_phase || '-')}</span>
                    </div>
                </div>
            `;
        }).join('');
    },

    renderRules(rules) {
        const el = document.getElementById('analyses-rules-list');
        if (!el) return;
        if (!rules.length) {
            el.innerHTML = '<div class="empty-hint">暂无规则</div>';
            return;
        }

        el.innerHTML = rules.map(rule => {
            const items = rule.items || [];
            let detailHtml = '';
            if (rule.id === 'regime_action') {
                detailHtml = items.map(it => `
                    <div class="rule-row">
                        <span class="rule-key">${this.esc(it.regime)}</span>
                        <span class="rule-val">${this.esc(it.top_action)} (${(it.top_action_ratio * 100).toFixed(0)}%)</span>
                        <span class="rule-count">${it.count}天</span>
                    </div>
                `).join('');
            } else if (rule.id === 'tech_trend_change') {
                detailHtml = items.map(it => `
                    <div class="rule-row">
                        <span class="rule-key">${this.esc(it.tech_trend)}</span>
                        <span class="rule-val">均变 ${(it.avg_change * 100).toFixed(1)}% 极值 ${(it.max_change * 100).toFixed(1)}%</span>
                        <span class="rule-count">${it.count}次</span>
                    </div>
                `).join('');
            } else if (rule.id === 'sentiment_defensive') {
                detailHtml = items.map(it => `
                    <div class="rule-row">
                        <span class="rule-key">${this.esc(it.sentiment)}</span>
                        <span class="rule-val">防御 ${(it.avg_defensive_weight * 100).toFixed(0)}%</span>
                        <span class="rule-count">${it.count}天</span>
                    </div>
                `).join('');
            } else if (rule.id === 'vol_timing') {
                detailHtml = items.map(it => `
                    <div class="rule-row">
                        <span class="rule-key">${this.esc(it.vol_regime)}</span>
                        <span class="rule-val">${this.esc(it.top_timing_decision)}</span>
                        <span class="rule-count">${it.count}次</span>
                    </div>
                `).join('');
            } else if (rule.id === 'agreement_action') {
                detailHtml = items.map(it => {
                    const dist = it.action_distribution || {};
                    const parts = Object.entries(dist).map(([k, v]) => `${k}:${v}`).join(' ');
                    return `
                        <div class="rule-row">
                            <span class="rule-key">${this.esc(it.agreement_level)}</span>
                            <span class="rule-val">${this.esc(parts)}</span>
                            <span class="rule-count">${it.count}天</span>
                        </div>
                    `;
                }).join('');
            } else {
                detailHtml = items.map(it => `
                    <div class="rule-row"><pre class="rule-raw">${this.esc(JSON.stringify(it, null, 1))}</pre></div>
                `).join('');
            }

            return `
                <div class="rule-card">
                    <div class="rc-header">${this.esc(rule.name)}</div>
                    <div class="rc-desc">${this.esc(rule.description)}</div>
                    <div class="rc-body">${detailHtml}</div>
                </div>
            `;
        }).join('');
    },

    renderAllocationTimeline(history) {
        const el = document.getElementById('analyses-allocation-timeline');
        if (!el) return;
        if (!history.length) {
            el.innerHTML = '<div class="empty-hint">暂无数据</div>';
            return;
        }

        // 收集所有出现过的 ETF 代码
        const allCodes = new Set();
        history.forEach(h => {
            Object.keys(h.allocation || {}).forEach(c => allCodes.add(c));
            Object.keys(h.holdings || {}).forEach(c => allCodes.add(c));
        });
        const codes = [...allCodes].sort();

        // 颜色映射
        const colors = ['#0284c7', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#059669', '#db2777', '#2563eb'];
        const colorMap = {};
        codes.forEach((c, i) => { colorMap[c] = colors[i % colors.length]; });

        const fmtPct = (v) => (v * 100).toFixed(0) + '%';

        el.innerHTML = `
            <div class="alloc-legend">
                ${codes.map(c => `<span class="alloc-legend-item"><span class="alloc-dot" style="background:${colorMap[c]}"></span>${this.etfLabel(c)}</span>`).join('')}
            </div>
            <div class="alloc-legend-hint">
                <span class="alloc-hint-item"><span class="alloc-bar-sample alloc-bar-sample--solid"></span>日初持仓</span>
                <span class="alloc-hint-item"><span class="alloc-bar-sample alloc-bar-sample--stripe"></span>AI建议</span>
            </div>
            <div class="alloc-bars">
                ${history.map(h => {
                    const alloc = h.allocation || {};
                    const holdings = h.holdings || {};
                    const allocTotal = Object.values(alloc).reduce((s, v) => s + v, 0) || 1;
                    const holdTotal = Object.values(holdings).reduce((s, v) => s + v, 0) || 1;

                    // 上行：日初持仓（实心条）
                    const holdBars = codes.map(c => {
                        const v = (holdings[c] || 0) / holdTotal * 100;
                        return v > 0 ? `<div class="alloc-bar alloc-bar--solid" style="width:${v}%;background:${colorMap[c]}" title="${this.etfLabel(c)} 日初 ${fmtPct(holdings[c] || 0)}"></div>` : '';
                    }).join('');

                    // 下行：AI建议（斜线条纹）
                    const allocBars = codes.map(c => {
                        const v = (alloc[c] || 0) / allocTotal * 100;
                        return v > 0 ? `<div class="alloc-bar alloc-bar--stripe" style="width:${v}%;background:${colorMap[c]}" title="${this.etfLabel(c)} AI建议 ${fmtPct(alloc[c] || 0)}"></div>` : '';
                    }).join('');

                    return `
                        <div class="alloc-row">
                            <span class="alloc-date">${this.esc(h.date.slice(5))}</span>
                            <div class="alloc-pair">
                                <div class="alloc-bar-row">${holdBars}</div>
                                <div class="alloc-bar-row">${allocBars}</div>
                            </div>
                            <span class="alloc-action">${this.esc(h.action || '-')}</span>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    },

    esc(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};

document.addEventListener('DOMContentLoaded', () => {
    Workbench.init();
    window.addEventListener('resize', () => {
        if (Workbench._distChart) Workbench._distChart.resize();
        if (Workbench._trendChart) Workbench._trendChart.resize();
        if (Workbench._btChart) Workbench._btChart.resize();
        if (Workbench._btDdChart) Workbench._btDdChart.resize();
    });
});
