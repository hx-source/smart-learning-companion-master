/**
 * 跨页面共享脚本。
 *
 * 负责导航栏用户信息、模型状态、聊天历史缓存和历史记录渲染。
 * 多个模板都会加载这个文件，因此这里的函数尽量保持通用、无页面强绑定。
 */

// 全局缓存对象：避免用户在首页/历史页之间切换时频繁请求同一批数据。
const globalCache = {
    userInfo: null,
    modelStatus: null,
    sessions: null,
    sessionDetails: {},
    lastFetch: {
        userInfo: 0,
        modelStatus: 0,
        sessions: 0
    },
    cacheTime: 10 * 60 * 1000 // 缓存时间：10分钟
};

// 从 cookie 中获取值，供需要读取 token/csrf_token 的页面复用。
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}

// 页面加载完成后执行：先显示用户和模型状态，再根据页面类型预加载数据。
function initPage() {
    loadUserInfoAndModelStatus();
    
    // 预加载数据
    if (window.location.pathname === '/') {
        // 在首页预加载其他页面可能需要的数据
        setTimeout(preloadData, 2000);
    }
    
    // 其他页面特定的初始化逻辑由页面自身处理
}

// 预加载数据：延迟加载聊天历史，避免阻塞首页首屏渲染。
async function preloadData() {
    const user = getCurrentUser();
    if (!user) return;
    
    try {
        // 预加载聊天历史
        if (!globalCache.sessions) {
            await loadChatHistory();
        }
        
        console.log('预加载数据完成');
    } catch (error) {
        console.error('预加载数据失败:', error);
    }
}

// 获取当前用户信息：localStorage 可能为空或被手动改坏，因此需要 try/catch。
function getCurrentUser() {
    try {
        return JSON.parse(localStorage.getItem('user'));
    } catch (e) {
        return null;
    }
}

// 加载用户信息和模型状态（使用综合 API）。
// 先用本地缓存快速渲染，再在后台刷新后端数据，兼顾速度和准确性。
async function loadUserInfoAndModelStatus() {
    const user = getCurrentUser();
    
    // 优先使用本地存储的用户信息，减少API调用
    if (user) {
        displayUserInfo(user);
        
        // 后台异步更新用户信息和模型状态，不阻塞页面加载
        const now = Date.now();
        if (!globalCache.userInfo || (now - globalCache.lastFetch.userInfo > globalCache.cacheTime)) {
            try {
                // 使用综合 API 端点，一次请求拿到用户信息和模型状态。
                const response = await fetch(`/api/dashboard?user_id=${user.id}`);
                const data = await response.json();
                
                if (data.code === 200) {
                    // 更新用户信息缓存
                    globalCache.userInfo = data.data.user;
                    globalCache.lastFetch.userInfo = now;
                    
                    // 更新模型状态缓存
                    globalCache.modelStatus = data.data.model_status;
                    globalCache.lastFetch.modelStatus = now;
                    
                    // 更新模型状态显示
                    updateModelStatus(data.data.model_status);
                    
                    // 更新用户信息显示
                    displayUserInfo(data.data.user);
                    
                    // 更新本地存储中的用户信息
                    localStorage.setItem('user', JSON.stringify(data.data.user));
                }
            } catch (error) {
                console.error('后台更新数据失败:', error);
                // 失败时尝试单独获取模型状态
                setTimeout(checkModelStatus, 1000);
            }
        }
    } else {
        displayUserInfo(null);
    }
}

// 显示用户信息：导航栏头像优先展示用户上传图片，否则显示用户名首字母。
function displayUserInfo(user) {
    const userInfoEl = document.getElementById('userInfo');
    const usernameEl = document.getElementById('username');
    const avatarEl = document.getElementById('navAvatar');
    
    if (user) {
        if (userInfoEl) userInfoEl.style.display = 'flex';
        if (usernameEl) usernameEl.textContent = user.username;
        if (avatarEl) {
            if (user.avatar && user.avatar.startsWith('/static/avatars/')) {
                // 添加随机参数避免浏览器缓存，头像更新后能立即看到新图。
                const avatarUrl = `${user.avatar}?t=${Date.now()}`;
                avatarEl.style.backgroundImage = `url(${avatarUrl})`;
                avatarEl.style.backgroundSize = 'cover';
                avatarEl.style.backgroundPosition = 'center';
                avatarEl.textContent = '';
            } else {
                avatarEl.style.backgroundImage = 'none';
                avatarEl.textContent = user.username.charAt(0).toUpperCase();
            }
        }
    } else {
        // 如果用户未登录，重定向到登录页面
        if (window.location.pathname !== '/login') {
            window.location.href = '/login';
        }
    }
}

// 登出
function logout() {
    localStorage.removeItem('user');
    localStorage.removeItem('token');
    document.cookie = 'token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC;';
    location.href = '/login';
}

// 检查模型状态：10 分钟内优先使用缓存，请求失败时也尽量保留旧状态展示。
async function checkModelStatus() {
    const now = Date.now();
    
    // 检查缓存是否有效（10分钟内）
    if (globalCache.modelStatus && (now - globalCache.lastFetch.modelStatus < 10 * 60 * 1000)) {
        updateModelStatus(globalCache.modelStatus);
        return;
    }
    
    try {
        const response = await fetch('/api/ollama/status');
        const data = await response.json();
        
        if (data.code === 200) {
            globalCache.modelStatus = data.data;
            globalCache.lastFetch.modelStatus = now;
            updateModelStatus(data.data);
        }
    } catch (error) {
        console.error('检查模型状态失败:', error);
        // 使用缓存数据作为后备
        if (globalCache.modelStatus) {
            updateModelStatus(globalCache.modelStatus);
        }
    }
}

// 更新模型状态：根据当前选择的模型类型显示本地/云端可用状态。
function updateModelStatus(status) {
    const statusEl = document.getElementById('modelStatus');
    if (statusEl) {
        if (currentModelType === 'api') {
            statusEl.innerHTML = '<span class="status-dot online"></span><span class="status-text">API模型: DeepSeek</span>';
        } else if (status.connected) {
            const modelName = status.configured_model || 'Ollama';
            statusEl.innerHTML = `<span class="status-dot online"></span><span class="status-text">本地模型: ${modelName}</span>`;
        } else {
            statusEl.innerHTML = '<span class="status-dot offline"></span><span class="status-text">本地模型离线</span>';
        }
    }
}

// 加载聊天历史：会话列表缓存 10 分钟，减少频繁切换页面时的数据库压力。
async function loadChatHistory() {
    // 检查缓存是否有效
    const now = Date.now();
    if (globalCache.sessions && (now - globalCache.lastFetch.sessions < globalCache.cacheTime)) {
        renderChatHistory(globalCache.sessions);
        return;
    }

    const user = getCurrentUser();
    const userId = user ? user.id : 1;
    
    try {
        const response = await fetch(`/api/records/sessions?user_id=${userId}`);
        const data = await response.json();
        if (data.code === 200) {
            globalCache.sessions = data.data;
            globalCache.lastFetch.sessions = now;
            renderChatHistory(data.data);
        }
    } catch (error) {
        console.error('加载聊天历史失败:', error);
    }
}

// 渲染聊天历史：把后端按 session 分组后的数据转换成侧边栏列表。
function renderChatHistory(sessions) {
    const historyList = document.getElementById('historyList');
    if (!historyList) return;

    if (sessions.length === 0) {
        historyList.innerHTML = `
            <div class="history-empty">
                <div class="history-empty-icon">📝</div>
                <div>暂无聊天记录</div>
            </div>
        `;
        return;
    }

    historyList.innerHTML = sessions.map(session => `
        <div class="history-item" data-session-id="${session.session_id}">
            <div class="history-item-content" onclick="loadSession('${session.session_id}')">
                <div class="history-item-title">${session.first_question}</div>
                <div class="history-item-meta">
                    <span class="history-item-count">${session.count} 条</span>
                    <span class="history-item-time">${session.time_ago}</span>
                </div>
            </div>
            <button class="history-item-delete" onclick="deleteSession('${session.session_id}')" title="删除会话">🗑️</button>
        </div>
    `).join('');
}

// 加载会话
async function loadSession(sessionId) {
    currentSessionId = sessionId;
    
    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.remove('active');
        if (item.dataset.sessionId === sessionId) {
            item.classList.add('active');
        }
    });
    
    // 检查缓存
    if (globalCache.sessionDetails[sessionId]) {
        renderSessionMessages(globalCache.sessionDetails[sessionId]);
        return;
    }
    
    const user = getCurrentUser();
    const userId = user ? user.id : 1;
    const response = await fetch(`/api/records/session/${sessionId}?user_id=${userId}`);
    const data = await response.json();
    if (data.code === 200) {
        globalCache.sessionDetails[sessionId] = data.data;
        renderSessionMessages(data.data);
    }
}

// 渲染会话消息
function renderSessionMessages(records) {
    const container = document.getElementById('chatMessages');
    if (!container) return;
    
    container.innerHTML = '';
    
    if (!records || records.length === 0) {
        container.innerHTML = '<div class="history-empty"><div class="history-empty-icon">📝</div><div>暂无消息</div></div>';
        return;
    }
    
    records.forEach(record => {
        const userMsg = document.createElement('div');
        userMsg.className = 'message user';
        userMsg.innerHTML = `
            <div class="message-avatar">👤</div>
            <div class="message-content">${escapeHtml(record.question)}</div>
            <button class="message-delete" onclick="deleteMessage(${record.id})">🗑️</button>
        `;
        container.appendChild(userMsg);
        
        const aiMsg = document.createElement('div');
        aiMsg.className = 'message ai';
        aiMsg.innerHTML = `
            <div class="message-avatar">🤖</div>
            <div class="message-content">${formatMessage(record.ai_answer)}</div>
        `;
        container.appendChild(aiMsg);
    });
    
    container.scrollTop = container.scrollHeight;
}

// 格式化消息
function formatMessage(content) {
    return escapeHtml(content)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
        .replace(/`(.+?)`/g, '<code>$1</code>')
        .replace(/\n/g, '<br>');
}

// 转义HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 删除消息
function deleteMessage(recordId) {
    if (!confirm('确定要删除这条消息吗？')) {
        return;
    }
    
    const user = getCurrentUser();
    const userId = user ? user.id : 1;
    
    console.log('删除消息开始:', recordId, currentSessionId);
    
    // 立即从UI中移除消息，提供即时反馈
    const messageElements = document.querySelectorAll(`.message-delete[onclick="deleteMessage(${recordId})"]`);
    console.log('找到消息元素:', messageElements.length);
    messageElements.forEach(button => {
        const userMessage = button.closest('.message.user');
        if (userMessage) {
            const aiMessage = userMessage.nextElementSibling;
            if (aiMessage && aiMessage.classList.contains('message') && aiMessage.classList.contains('ai')) {
                aiMessage.remove();
            }
            userMessage.remove();
            console.log('消息已从UI中移除');
        }
    });
    
    fetch(`/api/records/${recordId}`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token') || getCookie('token')}`
        },
        body: JSON.stringify({ user_id: userId })
    })
    .then(res => res.json())
    .then(async data => {
        console.log('删除响应:', data);
        
        // 清除所有相关缓存
        if (currentSessionId) {
            delete globalCache.sessionDetails[currentSessionId];
            console.log('会话缓存已清除:', currentSessionId);
        }
        globalCache.sessions = null;
        globalCache.lastFetch.sessions = 0;
        console.log('聊天历史缓存已清除');
        
        // 只重新加载聊天历史，确保聊天历史列表与后端数据一致
        // 不再重新加载会话，避免重新渲染消息
        console.log('重新加载聊天历史');
        await loadChatHistory();
        
        // 不再显示404提示，因为消息已经从UI中移除
        console.log('删除操作完成');
    })
    .catch(error => {
        console.error('删除消息失败:', error);
        // 不再显示错误提示，因为消息已经从UI中移除
        // 如果请求失败，重新加载数据以恢复UI
        if (currentSessionId) {
            delete globalCache.sessionDetails[currentSessionId];
            loadSession(currentSessionId);
        }
    });
}

// 删除会话
function deleteSession(sessionId) {
    if (!confirm('确定要删除整个会话吗？')) {
        return;
    }
    
    const user = getCurrentUser();
    const userId = user ? user.id : 1;
    
    console.log('删除会话开始:', sessionId);
    
    // 立即从UI中移除会话项，提供即时反馈
    const sessionElement = document.querySelector(`.history-item[data-session-id="${sessionId}"]`);
    if (sessionElement) {
        sessionElement.remove();
        console.log('会话项已从UI中移除');
    }
    
    // 如果当前正在查看的会话被删除，清空聊天消息区域
    if (currentSessionId === sessionId) {
        const chatMessages = document.getElementById('chatMessages');
        if (chatMessages) {
            chatMessages.innerHTML = `
                <div class="message ai">
                    <div class="message-avatar">🤖</div>
                    <div class="message-content">
                        你好！我是智学伴AI助手 👋<br><br>
                        我可以帮你：<br>
                        • 解答学习问题<br>
                        • 解释概念和公式<br>
                        • 提供学习建议<br><br>
                        请在下方输入你的问题，开始学习吧！
                    </div>
                </div>
            `;
            currentSessionId = null;
            console.log('聊天消息区域已清空');
        }
    }
    
    fetch(`/api/records/session/${sessionId}`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token') || getCookie('token')}`
        },
        body: JSON.stringify({ user_id: userId })
    })
    .then(res => res.json())
    .then(async data => {
        console.log('删除会话响应:', data);
        
        // 清除所有相关缓存
        delete globalCache.sessionDetails[sessionId];
        globalCache.sessions = null;
        globalCache.lastFetch.sessions = 0;
        console.log('会话缓存已清除:', sessionId);
        console.log('聊天历史缓存已清除');
        
        // 重新加载聊天历史，确保聊天历史列表与后端数据一致
        console.log('重新加载聊天历史');
        await loadChatHistory();
        
        console.log('删除会话操作完成');
    })
    .catch(error => {
        console.error('删除会话失败:', error);
        // 如果请求失败，重新加载数据以恢复UI
        globalCache.sessions = null;
        loadChatHistory();
    });
}

// 添加消息
function addMessage(content, type) {
    const container = document.getElementById('chatMessages');
    if (!container) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}`;
    messageDiv.innerHTML = `
        <div class="message-avatar">${type === 'user' ? '👤' : '🤖'}</div>
        <div class="message-content">${type === 'ai' ? formatMessage(content) : escapeHtml(content)}</div>
    `;
    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
}

// 添加加载消息
function addLoadingMessage() {
    const container = document.getElementById('chatMessages');
    if (!container) return null;
    
    const id = 'loading-' + Date.now();
    const loadingDiv = document.createElement('div');
    loadingDiv.id = id;
    loadingDiv.className = 'message ai';
    loadingDiv.innerHTML = `
        <div class="message-avatar">🤖</div>
        <div class="message-content">
            <div class="loading-dots"><span></span><span></span><span></span></div>
        </div>
    `;
    container.appendChild(loadingDiv);
    container.scrollTop = container.scrollHeight;
    return id;
}

// 移除加载消息
function removeLoadingMessage(id) {
    if (!id) return;
    const el = document.getElementById(id);
    if (el) el.remove();
}

// 切换模型
function switchModel(type) {
    currentModelType = type;
    document.getElementById('apiBtn').classList.toggle('active', type === 'api');
    document.getElementById('ollamaBtn').classList.toggle('active', type === 'ollama');
    localStorage.setItem('preferredModel', type);
    
    // 更新模型状态显示
    if (type === 'api') {
        const statusEl = document.getElementById('modelStatus');
        if (statusEl) {
            statusEl.innerHTML = '<span class="status-dot online"></span><span class="status-text">API模型: DeepSeek</span>';
        }
    } else {
        // 对于本地模型，强制清除缓存并重新检查状态
        globalCache.modelStatus = null;
        globalCache.lastFetch.modelStatus = 0;
        checkModelStatus();
    }
}

// 加载模型偏好设置
function loadModelPreference() {
    const preferred = localStorage.getItem('preferredModel');
    if (preferred) {
        switchModel(preferred);
    }
}

// 切换知识库开关
function toggleKnowledgeBase() {
    knowledgeBaseEnabled = !knowledgeBaseEnabled;
    const btn = document.getElementById('kbToggleBtn');
    const status = document.getElementById('kbToggleStatus');
    const kbSelector = document.getElementById('kbSelector');

    if (knowledgeBaseEnabled) {
        btn.classList.add('active');
        status.textContent = '开启';
        status.classList.add('active');
        if (kbSelector) {
            kbSelector.style.display = 'block';
        }
    } else {
        btn.classList.remove('active');
        status.textContent = '关闭';
        status.classList.remove('active');
        if (kbSelector) {
            kbSelector.style.display = 'none';
        }
    }

    // 保存偏好设置
    localStorage.setItem('knowledgeBaseEnabled', knowledgeBaseEnabled);
}

// 加载知识库偏好设置
function loadKnowledgeBasePreference() {
    const saved = localStorage.getItem('knowledgeBaseEnabled');
    // 默认开启知识库功能
    if (saved === 'true' || saved === null) {
        knowledgeBaseEnabled = true;
        const btn = document.getElementById('kbToggleBtn');
        const status = document.getElementById('kbToggleStatus');
        const kbSelector = document.getElementById('kbSelector');
        if (btn) btn.classList.add('active');
        if (status) {
            status.textContent = '开启';
            status.classList.add('active');
        }
        if (kbSelector) {
            kbSelector.style.display = 'block';
        }
        // 保存偏好设置
        localStorage.setItem('knowledgeBaseEnabled', 'true');
    } else {
        knowledgeBaseEnabled = false;
        const btn = document.getElementById('kbToggleBtn');
        const status = document.getElementById('kbToggleStatus');
        const kbSelector = document.getElementById('kbSelector');
        if (btn) btn.classList.remove('active');
        if (status) {
            status.textContent = '关闭';
            status.classList.remove('active');
        }
        if (kbSelector) {
            kbSelector.style.display = 'none';
        }
    }
    
    // 加载知识库选择偏好
    const savedKnowledgeBase = localStorage.getItem('currentKnowledgeBase');
    if (savedKnowledgeBase) {
        currentKnowledgeBase = savedKnowledgeBase;
        const select = document.getElementById('knowledgeBaseSelect');
        if (select) {
            select.value = currentKnowledgeBase;
        }
    }
}

// 处理键盘事件
function handleKeyPress(event) {
    if (event.key === 'Enter') {
        sendQuestion();
    }
}

// 发送问题
async function sendQuestion() {
    const input = document.getElementById('questionInput');
    const question = input.value.trim();

    if (!question) return;

    const user = getCurrentUser();
    const userId = user ? user.id : 1;

    addMessage(question, 'user');
    input.value = '';

    const loadingId = addLoadingMessage();

    try {
        // 根据知识库开关选择API端点
        const apiEndpoint = knowledgeBaseEnabled ? '/api/ask-with-kb' : '/api/ask';

        const response = await fetch(apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: question,
                user_id: userId,
                model_type: currentModelType,
                session_id: currentSessionId,
                knowledge_base: currentKnowledgeBase
            })
        });

        const data = await response.json();
        removeLoadingMessage(loadingId);

        if (data.code === 200) {
            // 如果使用了知识库，添加标记
            let answer = data.data.answer;
            if (data.data.knowledge_base_used) {
                answer = '🧠 **基于知识库回答**\n\n' + answer;
            }
            addMessage(answer, 'ai');
            currentSessionId = data.data.session_id;
            // 清除缓存，强制重新加载
            globalCache.sessions = null;
            loadChatHistory();
        } else {
            addMessage('抱歉，出错了：' + data.msg, 'ai');
        }
    } catch (error) {
        removeLoadingMessage(loadingId);
        addMessage('网络错误，请稍后重试', 'ai');
    }
}

// 通用工具函数
function showStatus(message, type) {
    const statusElement = document.getElementById('uploadStatus');
    if (!statusElement) return;
    
    statusElement.textContent = message;
    statusElement.className = 'upload-status ' + type;
    statusElement.style.display = 'block';

    setTimeout(() => {
        statusElement.style.display = 'none';
    }, 5000);
}

function truncateText(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

// 页面加载完成后自动执行初始化
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPage);
} else {
    initPage();
}
