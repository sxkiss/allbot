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

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    loadAdapters();

    // 绑定重启按钮事件
    const restartBtn = document.getElementById('btn-restart-service');
    if (restartBtn) {
        restartBtn.addEventListener('click', restartService);
    }
});

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

        if (adapters.length === 0) {
            container.innerHTML = `
                <div class="col-12 text-center py-5">
                    <i class="bi bi-inbox text-muted" style="font-size: 3rem;"></i>
                    <p class="mt-3 text-muted">未找到任何适配器</p>
                </div>
            `;
            return;
        }

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

        // 开关按钮
        const switchInput = card.querySelector('.adapter-switch');
        switchInput.id = `adapter-switch-${adapter.name}`;
        switchInput.checked = adapter.enabled;
        switchInput.dataset.adapterName = adapter.name;

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
