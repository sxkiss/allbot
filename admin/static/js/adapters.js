/**
 * 适配器管理页面 JavaScript
 *
 * 功能:
 * - 加载适配器列表
 * - 切换适配器启用状态
 * - 显示重启提示
 */

// 全局变量
let adapters = [];
let needRestart = false;
let adapterConfigModal = null;
let adapterConfigController = null;
const SAVE_CONFIG_BTN_DEFAULT_HTML = '<i class="bi bi-save me-1"></i>保存配置';

function ensureAdapterConfigController() {
    if (adapterConfigController || !window.ConfigForm) {
        return adapterConfigController;
    }
    adapterConfigController = window.ConfigForm.createController({
        formContainer: '#adapter-config-form-root',
        rawEditor: '#adapter-config-content',
        modeVisualBtn: '#adapter-btn-mode-visual',
        modeRawBtn: '#adapter-btn-mode-raw',
        advancedToggle: '#adapter-toggle-advanced-fields',
        visualPane: '#adapter-visual-config-pane',
        rawPane: '#adapter-raw-config-pane',
    });
    return adapterConfigController;
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    loadAdapters();

    // 绑定重启按钮事件
    const restartBtn = document.getElementById('btn-restart-service');
    if (restartBtn) {
        restartBtn.addEventListener('click', restartService);
    }

    // 绑定适配器操作事件
    document.addEventListener('click', handleAdapterActions);

    // 绑定适配器配置保存按钮
    const saveBtn = document.getElementById('save-adapter-config-btn');
    if (saveBtn) {
        saveBtn.addEventListener('click', function() {
            const adapterName = this.dataset.adapterName;
            if (adapterName) {
                saveAdapterConfig(adapterName);
            }
        });
    }

    const modalElement = document.getElementById('adapter-config-modal');
    if (modalElement) {
        modalElement.addEventListener('hidden.bs.modal', function() {
            const content = document.getElementById('adapter-config-content');
            if (content) {
                content.value = '';
            }
            const root = document.getElementById('adapter-config-form-root');
            if (root) {
                root.innerHTML = '';
            }
            const pathLabel = document.getElementById('adapter-config-path-label');
            if (pathLabel) {
                pathLabel.textContent = '';
            }
        });
    }
});

function handleAdapterActions(event) {
    const configBtn = event.target.closest('.btn-config-adapter');
    if (configBtn) {
        const card = configBtn.closest('.adapter-card');
        if (card && card.dataset.adapterName) {
            openAdapterConfig(card.dataset.adapterName);
        }
        return;
    }

    const deleteBtn = event.target.closest('.btn-delete-adapter');
    if (deleteBtn) {
        const card = deleteBtn.closest('.adapter-card');
        if (card && card.dataset.adapterName) {
            deleteAdapter(card.dataset.adapterName);
        }
    }
}

/**
 * 加载适配器列表
 */
async function loadAdapters() {
    const container = document.getElementById('adapters-container');

    try {
        const response = await fetch('/api/adapters');
        const result = await response.json();

        if (!result.success) {
            throw new Error(result.message || '加载适配器列表失败');
        }

        adapters = result.data || [];

        // 渲染适配器卡片
        renderAdapters(adapters);

    } catch (error) {
        console.error('加载适配器列表失败:', error);
        container.innerHTML = `
            <div class="col-12">
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    加载适配器列表失败: ${error.message}
                </div>
            </div>
        `;
    }
}

/**
 * 渲染适配器列表
 */
function renderAdapters(adapters) {
    const container = document.getElementById('adapters-container');
    const template = document.getElementById('adapter-card-template');

    container.innerHTML = '';

    if (!adapters || adapters.length === 0) {
        container.innerHTML = `
            <div class="col-12 text-center py-5">
                <i class="bi bi-inbox text-muted" style="font-size: 3rem;"></i>
                <p class="mt-3 text-muted">未找到任何适配器</p>
            </div>
        `;
        return;
    }

    adapters.forEach(adapter => {
        // 克隆模板
        const card = template.content.cloneNode(true);

        // 填充数据
        const cardElement = card.querySelector('.adapter-card');
        cardElement.dataset.adapterName = adapter.name;

        // 设置卡片状态样式
        if (adapter.enabled) {
            cardElement.classList.add('enabled');
        } else {
            cardElement.classList.add('disabled');
        }

        // 适配器名称
        card.querySelector('.adapter-name').textContent = adapter.name;

        // 状态徽章
        const statusBadge = card.querySelector('.adapter-status-badge');
        if (adapter.enabled) {
            statusBadge.textContent = '已启用';
            statusBadge.classList.add('bg-success');
        } else {
            statusBadge.textContent = '已禁用';
            statusBadge.classList.add('bg-secondary');
        }

        // 平台名称
        card.querySelector('.adapter-platform').textContent = adapter.platform || adapter.name;

        // 配置文件路径
        const configPath = adapter.config_path || '';
        const shortPath = configPath.split('/').slice(-3).join('/');
        card.querySelector('.adapter-config-path').textContent = shortPath;

        // 说明文档
        const descSpan = card.querySelector('.adapter-desc');
        const docButton = card.querySelector('.btn-view-doc');
        if (adapter.description) {
            descSpan.textContent = adapter.description;
            descSpan.title = adapter.description;
        }
        if (docButton) {
            if (adapter.doc_available) {
                docButton.style.display = 'block';
                docButton.addEventListener('click', () => viewAdapterDoc(adapter.name));
            } else {
                docButton.style.display = 'none';
            }
        }

        // 开关按钮
        const switchInput = card.querySelector('.adapter-switch');
        switchInput.id = `adapter-switch-${adapter.name}`;
        switchInput.checked = adapter.enabled;
        switchInput.dataset.adapterName = adapter.name;

        const configButton = card.querySelector('.btn-config-adapter');
        if (configButton) {
            configButton.dataset.adapterName = adapter.name;
        }

        const deleteButton = card.querySelector('.btn-delete-adapter');
        if (deleteButton) {
            deleteButton.dataset.adapterName = adapter.name;
        }

        // 绑定开关事件
        switchInput.addEventListener('change', function(e) {
            toggleAdapter(adapter.name, e.target.checked);
        });

        // 更新label的for属性
        const label = card.querySelector('.form-check-label');
        label.setAttribute('for', switchInput.id);

        container.appendChild(card);
    });
}

/**
 * 查看适配器说明文档
 */
async function viewAdapterDoc(adapterName) {
    if (!adapterName) return;

    try {
        const response = await fetch(`/api/adapters/${adapterName}/doc`);
        const result = await response.json();

        if (!result.success) {
            throw new Error(result.message || '获取说明文档失败');
        }

        const content = result.data.content || '';
        if (!content) {
            showToast('info', '说明文档为空');
            return;
        }

        // 使用 marked 渲染 Markdown
        const htmlContent = window.marked
            ? `<div class="markdown-body">${marked.parse(content)}</div>`
            : `<pre class="mb-0" style="white-space: pre-wrap;">${content}</pre>`;

        const modalHtml = `
            <div class="modal fade" id="adapter-doc-modal" tabindex="-1" aria-hidden="true">
                <div class="modal-dialog modal-dialog-centered modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">${adapterName} 适配器说明</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="关闭"></button>
                        </div>
                        <div class="modal-body" style="max-height: 70vh; overflow-y: auto;">
                            ${htmlContent}
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = modalHtml;
        document.body.appendChild(wrapper);
        const modalEl = wrapper.querySelector('#adapter-doc-modal');
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
        modalEl.addEventListener('hidden.bs.modal', () => {
            wrapper.remove();
        });
    } catch (error) {
        console.error('查看说明文档失败:', error);
        showToast('error', `查看说明文档失败: ${error.message}`);
    }
}

/**
 * 切换适配器状态
 */
async function toggleAdapter(adapterName, enabled) {
    const switchInput = document.querySelector(`#adapter-switch-${adapterName}`);
    const originalState = !enabled;

    try {
        // 显示加载状态
        switchInput.disabled = true;

        const response = await fetch(`/api/adapters/${adapterName}/toggle`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ enabled })
        });

        const result = await response.json();

        if (!result.success) {
            throw new Error(result.message || '切换适配器状态失败');
        }

        // 更新UI
        updateAdapterCard(adapterName, enabled);

        // 显示成功消息
        showToast('success', result.message);

        // 如果需要重启,显示重启按钮
        if (result.need_restart) {
            needRestart = true;
            showRestartButton();
        }

    } catch (error) {
        console.error('切换适配器状态失败:', error);

        // 恢复开关状态
        switchInput.checked = originalState;

        // 显示错误消息
        showToast('error', `切换失败: ${error.message}`);

    } finally {
        switchInput.disabled = false;
    }
}

/**
 * 更新适配器卡片UI
 */
function updateAdapterCard(adapterName, enabled) {
    const card = document.querySelector(`.adapter-card[data-adapter-name="${adapterName}"]`);
    if (!card) return;

    // 更新卡片样式
    if (enabled) {
        card.classList.remove('disabled');
        card.classList.add('enabled');
    } else {
        card.classList.remove('enabled');
        card.classList.add('disabled');
    }

    // 更新状态徽章
    const statusBadge = card.querySelector('.adapter-status-badge');
    if (enabled) {
        statusBadge.textContent = '已启用';
        statusBadge.classList.remove('bg-secondary');
        statusBadge.classList.add('bg-success');
    } else {
        statusBadge.textContent = '已禁用';
        statusBadge.classList.remove('bg-success');
        statusBadge.classList.add('bg-secondary');
    }
}

/**
 * 显示重启按钮
 */
function showRestartButton() {
    const restartBtn = document.getElementById('btn-restart-service');
    if (restartBtn) {
        restartBtn.style.display = 'inline-block';

        // 添加脉冲动画
        restartBtn.classList.add('pulse-animation');
    }
}

/**
 * 重启服务
 */
async function restartService() {
    if (!confirm('确定要重启服务吗？重启期间服务将暂时不可用。')) {
        return;
    }

    try {
        const response = await fetch('/api/restart', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (result.success) {
            showToast('success', '服务正在重启，请稍候...');

            // 隐藏重启按钮
            const restartBtn = document.getElementById('btn-restart-service');
            if (restartBtn) {
                restartBtn.style.display = 'none';
            }

            needRestart = false;

            // 3秒后刷新页面
            setTimeout(() => {
                window.location.reload();
            }, 3000);
        } else {
            throw new Error(result.message || '重启服务失败');
        }

    } catch (error) {
        console.error('重启服务失败:', error);
        showToast('error', `重启失败: ${error.message}`);
    }
}

/**
 * 显示提示消息
 */
function showToast(type, message) {
    // 如果页面有全局的toast函数,使用它
    if (typeof window.showToast === 'function') {
        window.showToast(type, message);
        return;
    }

    // 否则使用简单的alert
    const icon = type === 'success' ? '✓' : '✗';
    alert(`${icon} ${message}`);
}

/**
 * 删除适配器
 */
async function deleteAdapter(adapterName) {
    if (!adapterName) {
        return;
    }

    if (!confirm(`确定要删除适配器 ${adapterName} 吗？此操作不可恢复！`)) {
        return;
    }

    try {
        const response = await fetch(`/api/adapters/${adapterName}/delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (!result.success) {
            throw new Error(result.error || result.message || '删除适配器失败');
        }

        adapters = adapters.filter(item => item.name !== adapterName);
        renderAdapters(adapters);
        showToast('success', result.message || '适配器删除成功');
    } catch (error) {
        console.error('删除适配器失败:', error);
        showToast('error', `删除适配器失败: ${error.message}`);
    }
}

/**
 * 打开适配器配置
 */
async function openAdapterConfig(adapterName) {
    if (!adapterName) {
        return;
    }

    const modalElement = document.getElementById('adapter-config-modal');
    const contentElement = document.getElementById('adapter-config-content');
    const titleElement = document.getElementById('adapter-config-modal-title');
    const saveBtn = document.getElementById('save-adapter-config-btn');
    const pathLabel = document.getElementById('adapter-config-path-label');

    if (!modalElement || !titleElement || !saveBtn) {
        console.error('适配器配置模态框未正确初始化');
        return;
    }

    if (!window.ConfigForm) {
        showToast('error', '配置表单组件未加载，请刷新页面后重试');
        return;
    }

    const controller = ensureAdapterConfigController();
    if (!controller) {
        showToast('error', '配置表单初始化失败');
        return;
    }

    titleElement.textContent = `配置适配器: ${adapterName}`;
    if (pathLabel) pathLabel.textContent = '';
    saveBtn.dataset.adapterName = adapterName;
    if (contentElement) {
        contentElement.value = '';
        contentElement.disabled = true;
    }
    saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>加载中';
    saveBtn.disabled = true;

    if (!adapterConfigModal) {
        adapterConfigModal = new bootstrap.Modal(modalElement);
    }
    adapterConfigModal.show();

    try {
        const response = await fetch(`/api/adapters/${encodeURIComponent(adapterName)}/config`);
        const result = await response.json();

        if (!result.success) {
            throw new Error(result.error || result.message || '获取适配器配置失败');
        }

        controller.load({
            schema: result.schema || [],
            values: result.data || result.values || {},
            raw: result.raw || result.config || '',
        });
        controller.setMode('visual');

        if (pathLabel && result.path) {
            pathLabel.textContent = String(result.path).split('/').slice(-3).join('/');
        }
        if (contentElement) {
            contentElement.disabled = false;
        }
        saveBtn.innerHTML = SAVE_CONFIG_BTN_DEFAULT_HTML;
        saveBtn.disabled = false;
    } catch (error) {
        console.error('打开适配器配置失败:', error);
        showToast('error', `打开配置失败: ${error.message}`);
        if (contentElement) {
            contentElement.value = '';
            contentElement.disabled = false;
        }
        saveBtn.innerHTML = SAVE_CONFIG_BTN_DEFAULT_HTML;
        saveBtn.disabled = false;
    }
}

/**
 * 保存适配器配置
 */
async function saveAdapterConfig(adapterName) {
    const contentElement = document.getElementById('adapter-config-content');
    const saveBtn = document.getElementById('save-adapter-config-btn');

    if (!adapterName || !saveBtn) {
        return;
    }

    try {
        const controller = ensureAdapterConfigController();
        if (!controller) {
            throw new Error('配置表单未初始化');
        }

        const payload = controller.collect();
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>保存中...';
        saveBtn.disabled = true;
        if (contentElement) {
            contentElement.disabled = true;
        }

        const body = payload.mode === 'raw'
            ? { mode: 'raw', content: payload.content }
            : { mode: 'visual', values: payload.values };

        const response = await fetch(`/api/adapters/${encodeURIComponent(adapterName)}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const result = await response.json();

        if (!result.success) {
            throw new Error(result.error || result.message || '保存适配器配置失败');
        }

        showToast('success', '适配器配置已保存');
        needRestart = true;
        showRestartButton();

        if (adapterConfigModal) {
            adapterConfigModal.hide();
        }
    } catch (error) {
        console.error('保存适配器配置失败:', error);
        showToast('error', `保存配置失败: ${error.message}`);
    } finally {
        if (contentElement) {
            contentElement.disabled = false;
        }
        if (saveBtn) {
            saveBtn.innerHTML = SAVE_CONFIG_BTN_DEFAULT_HTML;
            saveBtn.disabled = false;
        }
    }
}

// 添加脉冲动画样式
const style = document.createElement('style');
style.textContent = `
    @keyframes pulse {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.05); }
    }

    .pulse-animation {
        animation: pulse 1.5s ease-in-out infinite;
    }
`;
document.head.appendChild(style);
