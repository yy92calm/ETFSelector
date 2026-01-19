// 应用主入口
const API_BASE_URL = 'http://localhost:8000/api';

// 页面初始化
document.addEventListener('DOMContentLoaded', function() {
    console.log('应用初始化...');
    
    // 检查API健康状态
    checkAPIHealth();
    
    // 绑定导航事件
    bindNavigation();
});

// 检查API健康状态
async function checkAPIHealth() {
    try {
        const response = await fetch('http://localhost:8000/health');
        if (response.ok) {
            updateStatusIndicator(true);
            console.log('API 连接成功');
        } else {
            updateStatusIndicator(false);
        }
    } catch (error) {
        console.error('API 连接失败:', error);
        updateStatusIndicator(false);
    }
}

// 更新状态指示器
function updateStatusIndicator(online) {
    const indicator = document.getElementById('status-indicator');
    if (online) {
        indicator.textContent = '在线';
        indicator.className = 'online';
    } else {
        indicator.textContent = '离线';
        indicator.className = 'offline';
    }
}

// 绑定导航事件
function bindNavigation() {
    const navLinks = document.querySelectorAll('nav a');
    navLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const sectionId = this.getAttribute('href').substring(1);
            showSection(sectionId);
        });
    });
}

// 显示指定section
function showSection(sectionId) {
    // 隐藏所有section
    document.querySelectorAll('.section').forEach(section => {
        section.classList.remove('active');
    });
    
    // 显示指定section
    const section = document.getElementById(sectionId);
    if (section) {
        section.classList.add('active');
    }
}

// 获取ETF行情
async function fetchQuote() {
    const etfCode = document.getElementById('etf-code').value.trim();
    
    if (!etfCode) {
        alert('请输入ETF代码');
        return;
    }
    
    try {
        const resultDiv = document.getElementById('quote-result');
        resultDiv.innerHTML = '<p>加载中...</p>';
        
        // 先从Qtrade获取数据
        const fetchResponse = await fetch(`${API_BASE_URL}/etf/fetch-quotes`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                etf_codes: [etfCode]
            })
        });
        
        if (!fetchResponse.ok) {
            throw new Error('获取数据失败');
        }
        
        // 稍等后获取数据库中的数据
        await new Promise(resolve => setTimeout(resolve, 500));
        
        const response = await fetch(`${API_BASE_URL}/etf/detail/${etfCode}`);
        const data = await response.json();
        
        if (response.ok && data.data) {
            const detail = data.data.detail;
            resultDiv.innerHTML = `
                <div class="quote-item">
                    <h3>${detail.etf_name} (${detail.etf_code})</h3>
                    <p class="price">¥${detail.last_price.toFixed(2)}</p>
                    <p class="change ${detail.change_rate >= 0 ? 'up' : 'down'}">
                        ${detail.change_rate >= 0 ? '+' : ''}${detail.change_rate.toFixed(2)}%
                    </p>
                    <p>成交量: ${(detail.volume / 100000000).toFixed(2)} 亿</p>
                    <p>成交额: ¥${(detail.amount / 100000000).toFixed(2)} 亿</p>
                </div>
            `;
        } else {
            resultDiv.innerHTML = `<p style="color: red;">获取行情失败: ${data.message}</p>`;
        }
    } catch (error) {
        console.error('错误:', error);
        document.getElementById('quote-result').innerHTML = `<p style="color: red;">获取行情异常: ${error.message}</p>`;
    }
}

// 回车键获取行情
document.addEventListener('DOMContentLoaded', function() {
    const etfInput = document.getElementById('etf-code');
    if (etfInput) {
        etfInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                fetchQuote();
            }
        });
    }
});
