/**
 * 认证模块 - 密码校验 + token 管理
 */
const Auth = {
    TOKEN_KEY: 'etf_auth_token',

    init() {
        if (!this.isLoggedIn()) {
            this.showLoginOverlay();
        }
    },

    isLoggedIn() {
        return !!localStorage.getItem(this.TOKEN_KEY);
    },

    getToken() {
        return localStorage.getItem(this.TOKEN_KEY);
    },

    setToken(token) {
        localStorage.setItem(this.TOKEN_KEY, token);
    },

    clearToken() {
        localStorage.removeItem(this.TOKEN_KEY);
    },

    showLoginOverlay() {
        let overlay = document.getElementById('auth-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'auth-overlay';
            overlay.className = 'auth-overlay';
            overlay.innerHTML = `
                <div class="auth-box">
                    <h2>ETF量化选择系统</h2>
                    <p>请输入访问密码</p>
                    <input type="password" id="auth-password" placeholder="密码" autocomplete="current-password">
                    <button id="auth-submit" onclick="Auth.submitLogin()">进入</button>
                    <div id="auth-error" class="auth-error"></div>
                </div>
            `;
            document.body.appendChild(overlay);

            const input = document.getElementById('auth-password');
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.submitLogin();
            });
            input.focus();
        }
        overlay.style.display = 'flex';
    },

    hideLoginOverlay() {
        const overlay = document.getElementById('auth-overlay');
        if (overlay) overlay.style.display = 'none';
    },

    async submitLogin() {
        const input = document.getElementById('auth-password');
        const errorEl = document.getElementById('auth-error');
        const password = input.value.trim();

        if (!password) {
            errorEl.textContent = '请输入密码';
            return;
        }

        try {
            const resp = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });
            const data = await resp.json();

            if (data.code === 200 && data.data && data.data.token) {
                this.setToken(data.data.token);
                this.hideLoginOverlay();
                window.location.reload();
            } else {
                errorEl.textContent = data.message || '密码错误';
                input.value = '';
                input.focus();
            }
        } catch (err) {
            errorEl.textContent = '网络错误，请重试';
        }
    },

    logout() {
        this.clearToken();
        window.location.reload();
    },

    getAuthHeaders() {
        const token = this.getToken();
        return token ? { 'Authorization': `Bearer ${token}` } : {};
    }
};

const _origFetch = window.fetch;
window.fetch = function(url, options = {}) {
    if (typeof url === 'string' && url.startsWith('/api/') && !url.startsWith('/api/auth/')) {
        options.headers = options.headers || {};
        const token = Auth.getToken();
        if (token) {
            if (options.headers instanceof Headers) {
                options.headers.set('Authorization', `Bearer ${token}`);
            } else {
                options.headers['Authorization'] = `Bearer ${token}`;
            }
        }
    }
    return _origFetch.call(this, url, options);
};

document.addEventListener('DOMContentLoaded', () => Auth.init());
