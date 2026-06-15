// Shared frontend helpers.
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
    cacheTime: 10 * 60 * 1000 // ?????10??
};

function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}

function initPage() {
    loadUserInfoAndModelStatus();
    
    if (window.location.pathname === '/') {
        // 在首页预加载其他页面可能需要的数据
        setTimeout(preloadData, 2000);
    }
    
}

async function preloadData() {
    const user = getCurrentUser();
    if (!user) return;
    
    try {
        if (!globalCache.sessions) {
            await loadChatHistory();
        }
        
        console.log('preload complete');
    } catch (error) {
        console.error('preload failed', error);
    }
}

function getCurrentUser() {
    try {
        return JSON.parse(localStorage.getItem('user'));
    } catch (e) {
        return null;
    }
}

async function loadUserInfoAndModelStatus() {
    const user = getCurrentUser();
    
    // 优先使用本地存储的用户信息，减少API调用
    if (user) {
        displayUserInfo(user);
        
        const now = Date.now();
        if (!globalCache.userInfo || (now - globalCache.lastFetch.userInfo > globalCache.cacheTime)) {
            try {
                const response = await fetch(`/api/dashboard?user_id=${user.id}`);
                const data = await response.json();
                
                if (data.code === 200) {
                    // 更新用户信息缓存
                    globalCache.userInfo = data.data.user;
                    globalCache.lastFetch.userInfo = now;
                    
                    globalCache.modelStatus = data.data.model_status;
                    globalCache.lastFetch.modelStatus = now;
                    
                    updateModelStatus(data.data.model_status);
                    
                    // 更新用户信息显示
                    displayUserInfo(data.data.user);
                    
                    // 更新本地存储中的用户信息
                    localStorage.setItem('user', JSON.stringify(data.data.user));
                }
            } catch (error) {
                console.error('后台更新数据失败:', error);
                setTimeout(checkModelStatus, 1000);
            }
        }
    } else {
        displayUserInfo(null);
    }
}

function displayUserInfo(user) {
    const userInfoEl = document.getElementById('userInfo');
    const usernameEl = document.getElementById('username');
    const avatarEl = document.getElementById('navAvatar');
    const adminNavItem = document.getElementById('adminNavItem');
    
    if (user) {
        if (userInfoEl) userInfoEl.style.display = 'flex';
        if (usernameEl) usernameEl.textContent = user.username;
        if (adminNavItem) {
            const canOpenAdmin = user.role === 'admin' || user.is_admin;
            adminNavItem.style.display = canOpenAdmin ? 'flex' : 'none';
            adminNavItem.style.pointerEvents = canOpenAdmin ? 'auto' : 'none';
        }
        if (avatarEl) {
            if (user.avatar && user.avatar.startsWith('/static/avatars/')) {
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
        if (adminNavItem) adminNavItem.style.display = 'none';
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
        console.error('????????', error);
        // 使用缓存数据作为后备
        if (globalCache.modelStatus) {
            updateModelStatus(globalCache.modelStatus);
        }
    }
}

function updateModelStatus(status) {
    const statusEl = document.getElementById('modelStatus');
    if (statusEl) {
        if (status.connected) {
            const modelName = status.configured_model || 'Ollama';
            statusEl.innerHTML = `<span class="status-dot online"></span><span class="status-text">本地模型: ${modelName}</span>`;
        } else {
            statusEl.innerHTML = '<span class="status-dot offline"></span><span class="status-text">本地模型离线</span>';
        }
    }
}

async function loadChatHistory() {
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

function renderChatHistory(sessions) {
    const historyList = document.getElementById('historyList');
    if (!historyList) return;

    if (sessions.length === 0) {
        historyList.innerHTML = `
            <div class="history-empty">
                <div class="history-empty-icon">DOC</div>
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
                    <span class="history-item-count">${session.count} \u6761</span>
                    <span class="history-item-time">${session.time_ago}</span>
                </div>
            </div>
            <button class="history-item-delete" onclick="deleteSession('${session.session_id}')" title="\u5220\u9664\u4f1a\u8bdd">\u5220\u9664</button>
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
        container.innerHTML = '<div class="history-empty"><div class="history-empty-icon">DOC</div><div>\u6682\u65e0\u6d88\u606f</div></div>';
        return;
    }
    
    records.forEach(record => {
        const userMsg = document.createElement('div');
        userMsg.className = 'message user';
        userMsg.innerHTML = `
            <div class="message-avatar">U</div>
            <div class="message-content">${escapeHtml(record.question)}</div>
            <button class="message-delete" onclick="deleteMessage(${record.id})" title="\u5220\u9664\u6d88\u606f">\u5220\u9664</button>
        `;
        container.appendChild(userMsg);
        
        const aiMsg = document.createElement('div');
        aiMsg.className = 'message ai';
        aiMsg.innerHTML = `
            <div class="message-avatar">AI</div>
            <div class="message-content">${formatMessage(record.ai_answer)}</div>
        `;
        container.appendChild(aiMsg);
    });
    
    container.scrollTop = container.scrollHeight;
}

function formatMessage(content) {
    let formatted = escapeHtml(content || '');
    formatted = formatted.replace(new RegExp('\\*\\*(.+?)\\*\\*', 'g'), '<strong>$1</strong>');
    formatted = formatted.replace(new RegExp('```([\\s\\S]*?)```', 'g'), '<pre><code>$1</code></pre>');
    formatted = formatted.replace(new RegExp('`(.+?)`', 'g'), '<code>$1</code>');
    formatted = formatted.replace(new RegExp('\\n', 'g'), '<br>');
    return formatted;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 删除消息
function deleteMessage(recordId) {
    if (!confirm('\u786e\u5b9a\u8981\u5220\u9664\u8fd9\u6761\u6d88\u606f\u5417\uff1f')) {
        return;
    }
    
    const user = getCurrentUser();
    const userId = user ? user.id : 1;
    
    console.log('delete message start', recordId, currentSessionId);
    
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
            console.log('message removed from UI');
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
        
        if (currentSessionId) {
            delete globalCache.sessionDetails[currentSessionId];
            console.log('session cache cleared', currentSessionId);
        }
        globalCache.sessions = null;
        globalCache.lastFetch.sessions = 0;
        console.log('chat history cache cleared');
        
        console.log('reload chat history');
        await loadChatHistory();
        
        console.log('delete message complete');
    })
    .catch(error => {
        console.error('删除消息失败:', error);
        // 不再显示错误提示，因为消息已经从UI中移�?        // 如果请求失败，重新加载数据以恢复UI
        if (currentSessionId) {
            delete globalCache.sessionDetails[currentSessionId];
            loadSession(currentSessionId);
        }
    });
}

// 删除会话
function deleteSession(sessionId) {
    if (!confirm('\u786e\u5b9a\u8981\u5220\u9664\u6574\u4e2a\u4f1a\u8bdd\u5417\uff1f')) {
        return;
    }
    
    const user = getCurrentUser();
    const userId = user ? user.id : 1;
    
    console.log('delete session start', sessionId);
    
    const sessionElement = document.querySelector(`.history-item[data-session-id="${sessionId}"]`);
    if (sessionElement) {
        sessionElement.remove();
        console.log('session item removed from UI');
    }
    
    if (currentSessionId === sessionId) {
        const chatMessages = document.getElementById('chatMessages');
        if (chatMessages) {
            chatMessages.innerHTML = `
                <div class="message ai">
                    <div class="message-avatar">AI</div>
                    <div class="message-content">
                        \u4f60\u597d\uff01\u6211\u662f\u667a\u5b66\u4f34AI\u52a9\u624b<br><br>
                        \u6211\u53ef\u4ee5\u5e2e\u4f60\uff1a<br>
                        - \u89e3\u7b54\u5b66\u4e60\u95ee\u9898<br>
                        - \u89e3\u91ca\u6982\u5ff5\u548c\u516c\u5f0f<br>
                        - \u63d0\u4f9b\u5b66\u4e60\u5efa\u8bae<br><br>
                        \u8bf7\u5728\u4e0b\u65b9\u8f93\u5165\u4f60\u7684\u95ee\u9898\uff0c\u5f00\u59cb\u5b66\u4e60\u5427\u3002
                </div>
            `;
            currentSessionId = null;
            console.log('chat messages cleared');
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
        
        delete globalCache.sessionDetails[sessionId];
        globalCache.sessions = null;
        globalCache.lastFetch.sessions = 0;
        console.log('session cache cleared', sessionId);
        console.log('chat history cache cleared');
        
        console.log('reload chat history');
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

function addStreamingMessage() {
    const container = document.getElementById('chatMessages');
    if (!container) return null;

    const messageDiv = document.createElement('div');
    messageDiv.className = 'message ai';
    messageDiv.innerHTML = `
        <div class="message-avatar">🤖</div>
        <div class="message-content"></div>
    `;
    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
    return messageDiv.querySelector('.message-content');
}

function updateStreamingMessage(contentEl, text) {
    if (!contentEl) return;
    contentEl.innerHTML = formatMessage(text || '');
    const container = document.getElementById('chatMessages');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function updateStreamingStatus(contentEl, text) {
    if (!contentEl) return;
    contentEl.innerHTML = `
        <div class="stream-status">
            <div class="loading-dots"><span></span><span></span><span></span></div>
            <span>${escapeHtml(text || '\u6b63\u5728\u5904\u7406...')}</span>
        </div>
    `;
    const container = document.getElementById('chatMessages');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function renderAnswerSources(contentEl, sources) {
    if (!contentEl || !Array.isArray(sources) || sources.length === 0) return;

    const sourceBox = document.createElement('div');
    sourceBox.className = 'answer-sources';
    sourceBox.innerHTML = `
        <div class="answer-sources-title">\u53c2\u8003\u6765\u6e90</div>
        ${sources.map((source, index) => {
            const fileName = escapeHtml(source.file_name || '\u672a\u77e5\u6587\u4ef6');
            const knowledgeBase = escapeHtml(source.knowledge_base_name || source.knowledge_base || '');
            const chunkText = source.chunk_index !== null && source.chunk_index !== undefined
                ? ` \u00b7 \u7247\u6bb5 ${escapeHtml(String(source.chunk_index))}`
                : '';
            const snippetText = source.content ? String(source.content).slice(0, 220) : '';
            const snippet = snippetText
                ? `<div class="answer-source-content">${escapeHtml(snippetText)}</div>`
                : '';
            return `
                <div class="answer-source-item">
                    <div class="answer-source-head">${index + 1}. ${fileName}</div>
                    <div class="answer-source-meta">\u77e5\u8bc6\u5e93\uff1a${knowledgeBase}${chunkText}</div>
                    ${snippet}
                </div>
            `;
        }).join('')}
    `;

    contentEl.appendChild(sourceBox);
    const container = document.getElementById('chatMessages');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
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
        <div class="message-avatar">AI</div>
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
    currentModelType = 'ollama';
    localStorage.setItem('preferredModel', 'ollama');
    const ollamaBtn = document.getElementById('ollamaBtn');
    if (ollamaBtn) ollamaBtn.classList.add('active');
    globalCache.modelStatus = null;
    globalCache.lastFetch.modelStatus = 0;
    checkModelStatus();
}

// 加载模型偏好设置
function loadModelPreference() {
    if (localStorage.getItem('preferredModel') === 'api') {
        localStorage.removeItem('preferredModel');
    }
    switchModel('ollama');
}

function toggleKnowledgeBase() {
    knowledgeBaseEnabled = !knowledgeBaseEnabled;
    const btn = document.getElementById('kbToggleBtn');
    const status = document.getElementById('kbToggleStatus');
    const kbSelector = document.getElementById('kbSelector');

    if (knowledgeBaseEnabled) {
        btn.classList.add('active');
        status.textContent = '\u5f00\u542f';
        status.classList.add('active');
        if (kbSelector) {
            kbSelector.style.display = 'block';
        }
    } else {
        btn.classList.remove('active');
        status.textContent = '\u5173\u95ed';
        status.classList.remove('active');
        if (kbSelector) {
            kbSelector.style.display = 'none';
        }
    }

    // 保存偏好设置
    localStorage.setItem('knowledgeBaseEnabled', knowledgeBaseEnabled);
}

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
            status.textContent = '\u5f00\u542f';
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

// Send question
async function sendQuestion() {
    const input = document.getElementById('questionInput');
    const question = input.value.trim();

    if (!question) return;

    const user = getCurrentUser();
    const userId = user ? user.id : 1;

    addMessage(question, 'user');
    input.value = '';

    const loadingId = addLoadingMessage();
    const selectedKnowledgeBase = currentKnowledgeBase || '';
    const useKnowledgeBase = knowledgeBaseEnabled && Boolean(selectedKnowledgeBase);

    try {
        const response = await fetch('/api/ask-with-kb/stream', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('token') || getCookie('token') || ''}`
            },
            body: JSON.stringify({
                question: question,
                user_id: userId,
                model_type: 'ollama',
                session_id: currentSessionId,
                knowledge_base: selectedKnowledgeBase,
                knowledge_base_enabled: useKnowledgeBase
            })
        });

        removeLoadingMessage(loadingId);

        if (!response.ok || !response.body) {
            addMessage('\u62b1\u6b49\uff0c\u51fa\u9519\u4e86\uff1a\u65e0\u6cd5\u5efa\u7acb\u6d41\u5f0f\u8fde\u63a5', 'ai');
            return;
        }

        const contentEl = addStreamingMessage();
        updateStreamingStatus(contentEl, useKnowledgeBase ? '\u6b63\u5728\u68c0\u7d22\u77e5\u8bc6\u5e93...' : '\u6b63\u5728\u8c03\u7528\u672c\u5730\u6a21\u578b...');
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let answer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.trim()) continue;

                const event = JSON.parse(line);
                if (event.type === 'token') {
                    answer += event.content || '';
                    updateStreamingMessage(contentEl, answer);
                } else if (event.type === 'status') {
                    if (!answer) {
                        updateStreamingStatus(contentEl, event.content);
                    }
                } else if (event.type === 'done') {
                    currentSessionId = event.data.session_id;
                    if (event.data.knowledge_base_used && event.data.sources && event.data.sources.length) {
                        renderAnswerSources(contentEl, event.data.sources);
                    }
                    globalCache.sessions = null;
                    loadChatHistory();
                } else if (event.type === 'error') {
                    answer += `\\n\\n\u62b1\u6b49\uff0c\u51fa\u9519\u4e86\uff1a${event.msg}`;
                    updateStreamingMessage(contentEl, answer);
                }
            }
        }
    } catch (error) {
        removeLoadingMessage(loadingId);
        addMessage('\u7f51\u7edc\u9519\u8bef\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5', 'ai');
    }
}

// Utilities
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
