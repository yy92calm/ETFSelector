/**
 * 认证模块 - 密码校验 + token 管理
 * 未登录时统一跳转到独立登录页 /login
 */
const Auth = {
    TOKEN_KEY: 'etf_auth_token',

    init() {
        if (this.isLoggedIn()) return;
        if (this.isLoginPage()) {
            this.bindLoginForm();
        } else {
            window.location.replace('/login');
        }
    },

    isLoginPage() {
        return window.location.pathname.startsWith('/login');
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

    bindLoginForm() {
        const input = document.getElementById('login-password');
        if (!input) return;
        const btn = document.getElementById('login-submit');
        if (btn) btn.addEventListener('click', () => this.submitLogin());
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.submitLogin();
        });
        input.focus();
    },

    async submitLogin() {
        const input = document.getElementById('login-password');
        const errorEl = document.getElementById('login-error');
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
                window.location.replace('/');
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
        window.location.replace('/login');
    },

    getAuthHeaders() {
        const token = this.getToken();
        return token ? { 'Authorization': `Bearer ${token}` } : {};
    }
};

const _origFetch = window.fetch;
window.fetch = async function(url, options = {}) {
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
    const resp = await _origFetch.call(this, url, options);
    if (resp.status === 401 && typeof url === 'string' && url.startsWith('/api/') && !url.startsWith('/api/auth/')) {
        Auth.clearToken();
        if (!Auth.isLoginPage()) window.location.replace('/login');
    }
    return resp;
};

document.addEventListener('DOMContentLoaded', () => Auth.init());
