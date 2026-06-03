/**
 * 智学伴 - 前端公共 JavaScript
 *
 * 这个文件提供页面通用的小工具：时间格式化、Toast 提示、
 * 登录态读写、登出和带认证头的 API 请求封装。
 */

// API基础URL
const API_BASE = '';



// 格式化时间：把后端时间转换成更适合历史记录展示的相对时间。
function formatTime(date) {
    const d = new Date(date);
    const now = new Date();
    const diff = now - d;
    
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
    if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
    if (diff < 604800000) return Math.floor(diff / 86400000) + '天前';
    
    return d.toLocaleDateString('zh-CN');
}

// 显示提示消息：动态创建 Toast，3 秒后自动消失。
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add('show');
    }, 10);
    
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// 跳转到首页
function goHome() {
    location.href = '/';
}

// 认证相关：token/user 同时保存在 localStorage，token 也写入 Cookie 供后端页面路由使用。
function getToken() {
    return localStorage.getItem('token');
}

function getCurrentUser() {
    const userStr = localStorage.getItem('user');
    return userStr ? JSON.parse(userStr) : null;
}

function setAuth(token, user) {
    localStorage.setItem('token', token);
    localStorage.setItem('user', JSON.stringify(user));
    // 同时设置 cookie，以便后端 auth_required 页面认证装饰器可以获取。
    document.cookie = `token=${token}; path=/;`;
}

function clearAuth() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
}

function isLoggedIn() {
    return !!getToken();
}

async function logout() {
    try {
        // 写操作需要 CSRF Token；即使登出接口失败，也会在 finally 中清理本地登录态。
        const csrfRes = await fetch('/api/auth/csrf-token');
        if (!csrfRes.ok) throw new Error('CSRF获取失败');
        const csrfData = await csrfRes.json();
        const csrfToken = csrfData.csrf_token;

        const token = getToken();
        const headers = {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrfToken
        };
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        await fetch('/api/auth/logout', {
            method: 'POST',
            headers: headers
        });
    } catch (e) {
        console.error('登出请求失败', e);
    } finally {
        clearAuth();
        location.href = '/login';
    }
}

function requireAuth() {
    if (!isLoggedIn()) {
        location.href = '/login';
        return false;
    }
    return true;
}

async function apiRequest(url, options = {}) {
    const token = getToken();
    const csrfToken = document.cookie.replace(/(?:(?:^|.*;\s*)csrf_token\s*\=\s*([^;]*).*$)|^.*$/, '$1');

    // 统一补齐 JSON Content-Type 和 Authorization，减少页面内重复写 fetch 头部。
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    try {
        const response = await fetch(API_BASE + url, {
            ...options,
            headers: headers
        });

        if (response.status === 401) {
            // 后端认为令牌无效时，立即清理本地状态并跳回登录页。
            clearAuth();
            location.href = '/login';
            return { code: 401, msg: '请重新登录' };
        }

        return await response.json();
    } catch (error) {
        console.error('API请求失败:', error);
        return { code: 500, msg: '网络错误' };
    }
}
