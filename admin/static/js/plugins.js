// 全局变量
let plugins = [];
let currentPluginId = null;
let currentFilter = 'all';
let currentFramework = 'original'; // 默认显示原始框架
let configModal = null;
let uploadModal = null;
let marketPlugins = [];
let sortedPlugins = []; // 添加全局变量

// 一些辅助函数
function getFieldLabel(field) {
    const labels = {
        'name': '插件名称',
        'description': '描述',
        'author': '作者',
        'version': '版本',
        'github_url': 'GitHub链接',
        'icon': '图标'
    };
    return labels[field] || field;
}

// 比较版本号
function compareVersions(version1, version2) {
    // 将版本号分解为数字数组
    const v1 = version1.split('.').map(Number);
    const v2 = version2.split('.').map(Number);

    // 补全数组长度，使其长度相同
    const maxLength = Math.max(v1.length, v2.length);
    while (v1.length < maxLength) v1.push(0);
    while (v2.length < maxLength) v2.push(0);

    // 依次比较每个部分
    for (let i = 0; i < maxLength; i++) {
        if (v1[i] > v2[i]) return 1;  // version1 更新
        if (v1[i] < v2[i]) return -1; // version2 更新
    }

    return 0; // 版本相同
}

// 手动打开模态框
function openModalManually(modalId) {
    try {
        const modalEl = document.getElementById(modalId);
        if (!modalEl) {
            console.error(`找不到模态框: ${modalId}`);
            return false;
        }

        // 使用Bootstrap API
        const modalInstance = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
        modalInstance.show();

        console.log(`使用Bootstrap API打开模态框成功: ${modalId}`);
        return true;
    } catch (error) {
        console.error(`打开模态框失败: ${modalId}`, error);
        return false;
    }
}

// 手动关闭模态框
function closeModalManually(modalId) {
    try {
        const modalEl = document.getElementById(modalId);
        if (!modalEl) {
            console.error(`找不到模态框: ${modalId}`);
            return false;
        }

        // 使用Bootstrap API
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) {
            modalInstance.hide();
        }

        console.log(`使用Bootstrap API关闭模态框成功: ${modalId}`);
        return true;
    } catch (error) {
        console.error(`关闭模态框失败: ${modalId}`, error);
        return false;
    }
}

// 插件市场API配置（统一走本地同源代理，避免 CORS 和端口暴露问题）
const PLUGIN_MARKET_API = {
    BASE_URL: '',
    LIST: '/api/plugin_market',
    SUBMIT: '/api/plugin_market/submit',
    INSTALL: '/api/plugin_market/install',
    HEALTH: '/api/plugin_market',
    CACHE_KEY: 'xybot_plugin_market_cache',
    CACHE_EXPIRY: 3600000 // 缓存有效期1小时（毫秒）
};

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    console.log('页面加载完成');
    console.log('Bootstrap版本:', typeof bootstrap !== 'undefined' ? (bootstrap.version || '存在但无版本信息') : '未加载');
    console.log('jQuery版本:', typeof $ !== 'undefined' ? ($.fn.jquery || '存在但无版本信息') : '未加载');
    console.log('模态框元素:', document.getElementById('upload-plugin-modal'));

    // 先加载插件市场，然后再加载本地插件
    loadPluginMarket().then(() => {
        // 市场数据加载完成后再加载本地插件
        loadPlugins();
    });

    // 为插件过滤按钮添加点击事件
    const filterButtons = document.querySelectorAll('.filter-section .btn-group .btn');
    if (filterButtons.length > 0) {
        filterButtons.forEach(button => {
            button.addEventListener('click', function() {
                // 确定是过滤器按钮还是框架按钮
                const isFilterButton = this.hasAttribute('data-filter');
                const targetButtons = isFilterButton ? 
                    document.querySelectorAll('.filter-section .btn-group .btn[data-filter]') : 
                    document.querySelectorAll('.filter-section .btn-group .btn[data-framework]');
                
                // 只移除相同类型按钮的active类
                targetButtons.forEach(btn => btn.classList.remove('active'));
                
                // 添加当前按钮的active类
                this.classList.add('active');
                
                // 获取过滤类型或框架类型
                if (isFilterButton) {
                    const filter = this.getAttribute('data-filter');
                    // 更新当前过滤器
                    currentFilter = filter;
                    // 过滤插件
                    filterPlugins(filter);
                } else {
                    const framework = this.getAttribute('data-framework');
                    // 加载指定框架的插件
                    loadPlugins(framework);
                }
            });
        });
        console.log('插件过滤按钮事件已添加');
    } else {
        console.warn('找不到插件过滤按钮，无法添加事件监听器');
    }

    // 自动加载插件列表
    loadPlugins();

    // 为刷新市场按钮添加点击事件
    const refreshMarketBtn = document.getElementById('btn-refresh-market');
    if (refreshMarketBtn) {
        refreshMarketBtn.addEventListener('click', function() {
            loadPluginMarket();
        });
    } else {
        console.warn('找不到刷新市场按钮，无法添加事件监听器');
    }

    console.log('提交按钮:', document.getElementById('btn-upload-plugin'));

    // 尝试添加内联点击事件
    console.log('尝试添加内联点击事件');

    // 初始化上传模态框
    initUploadModal();

    console.log('找到提交审核按钮:', document.getElementById('btn-submit-plugin'));

    // 添加提交事件监听器
    const submitBtn = document.getElementById('btn-submit-plugin');
    if (submitBtn) {
        submitBtn.addEventListener('click', function() {
            submitPlugin();
        });
    } else {
        console.error('找不到提交审核按钮，无法添加事件监听器');
    }

    // 添加市场搜索事件监听器
    const marketSearchInput = document.getElementById('search-market');
    if (marketSearchInput) {
        marketSearchInput.addEventListener('input', function() {
            searchMarketPlugins(this.value);
        });
    } else {
        console.warn('找不到市场搜索输入框，搜索功能不可用');
    }

    // 添加插件搜索事件监听器
    const pluginSearchInput = document.getElementById('plugin-search-input');
    if (pluginSearchInput) {
        pluginSearchInput.addEventListener('input', function() {
            searchPlugins(this.value);
        });
    } else {
        console.warn('找不到插件搜索输入框，搜索功能不可用');
    }

    // 检查并处理离线提交的插件
    checkConnection().then(online => {
        if (online) {
            processOfflineQueue();
        }
    });

    // 删除紧急按钮（如果存在）
    const emergencyButton = document.getElementById('emergency-backdrop-cleaner');
    if (emergencyButton) {
        emergencyButton.remove();
    }

    // 已删除添加插件按钮

    // 配置保存按钮点击事件
    document.getElementById('plugin-config-save').addEventListener('click', function() {
        // 直接调用保存函数，不依赖于pluginId属性
        savePluginConfig();
    });

    // 监听模态框关闭事件
    const configModal = document.getElementById('plugin-config-modal');
    configModal.addEventListener('hidden.bs.modal', function() {
        // 清理表单
        document.getElementById('plugin-config-form').innerHTML = '';
        // 重置错误状态
        document.getElementById('plugin-config-error').style.display = 'none';
    });

    // 框架选择按钮的事件监听器现在已经合并到过滤按钮的事件处理中
});

// 检查网络连接
async function checkConnection() {
    try {
        const url = `${PLUGIN_MARKET_API.BASE_URL}${PLUGIN_MARKET_API.HEALTH || '/health'}`;
        const response = await fetch(url, {
            method: 'GET'
        });
        return response.ok;
    } catch (error) {
        console.warn('连接检查失败:', error);
        return false;
    }
}

// 处理离线队列
async function processOfflineQueue() {
    const offlineQueue = JSON.parse(localStorage.getItem('xybot_offline_plugins') || '[]');

    if (offlineQueue.length === 0) return;

    let successCount = 0;
    let failCount = 0;

    for (const item of offlineQueue) {
        try {
            const response = await fetch(`${PLUGIN_MARKET_API.BASE_URL}${PLUGIN_MARKET_API.SUBMIT}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Client-ID': getBotClientId(),
                    'X-Submission-Id': item.id
                },
                body: JSON.stringify(item.data)
            });

            const data = await response.json();

            if (data.success) {
                successCount++;
            } else {
                failCount++;
                console.error('同步提交失败:', data.error);
            }
        } catch (error) {
            failCount++;
            console.error('同步提交出错:', error);
        }
    }

    // 清空已处理的队列
    localStorage.removeItem('xybot_offline_plugins');

    if (successCount > 0) {
        showToast(`成功同步${successCount}个离线提交的插件`, 'success');
    }

    return { successCount, failCount };
}

// 加载插件列表
async function loadPlugins(framework = 'original') {
    const pluginList = document.getElementById('plugin-list');

    try {
        pluginList.innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mt-3 text-muted">加载插件中...</p>
            </div>
        `;

        // 根据框架选择不同的API端点
        let apiEndpoint = '/api/plugins'; // 默认原始框架
        if (framework === 'dow') {
            apiEndpoint = '/api/dow_plugins'; // DOW框架
        } else if (framework === 'all') {
            apiEndpoint = '/api/all_plugins'; // 所有框架
        }

        currentFramework = framework; // 保存当前框架类型

        const response = await fetch(apiEndpoint);
        const data = await response.json();

        if (data.success) {
            const allPlugins = data.data.plugins || [];
            const adapterCount = allPlugins.filter(plugin => plugin.type === 'adapter').length;
            plugins = allPlugins.filter(plugin => plugin.type !== 'adapter');
            console.log('插件信息:', plugins); // 调试输出
            document.getElementById('plugin-count').textContent = plugins.length;

            const adapterAlert = document.getElementById('adapter-migration-alert');
            if (adapterAlert) {
                if (adapterCount > 0) {
                    const countElement = adapterAlert.querySelector('.adapter-count');
                    if (countElement) {
                        countElement.textContent = adapterCount;
                    }
                    adapterAlert.classList.remove('d-none');
                } else {
                    adapterAlert.classList.add('d-none');
                }
            }

            // 检查插件更新状态
            let updateCount = 0;
            let updatablePlugins = [];

            if (marketPlugins && Array.isArray(marketPlugins) && marketPlugins.length > 0) {
                // 检查是否有插件需要更新
                plugins.forEach(plugin => {
                    const marketPlugin = marketPlugins.find(p => p.name === plugin.name);
                    if (marketPlugin && marketPlugin.version) {
                        // 比较版本号
                        if (compareVersions(marketPlugin.version, plugin.version || '1.0.0') > 0) {
                            updateCount++;
                            updatablePlugins.push({
                                name: plugin.name,
                                currentVersion: plugin.version || '1.0.0',
                                newVersion: marketPlugin.version
                            });
                            console.log(`插件 ${plugin.name} 有更新，当前版本: ${plugin.version || '1.0.0'}，最新版本: ${marketPlugin.version}`);
                        }
                    }
                });

                // 如果有插件需要更新，显示提示
                if (updateCount > 0) {
                    // 创建或更新右上角更新提示
                    createUpdateNotification(updateCount, updatablePlugins);
                }
            }

            filterPlugins(currentFilter);
        } else {
            throw new Error(data.error || '加载插件失败');
        }
    } catch (error) {
        console.error('加载插件列表失败:', error);
        pluginList.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                加载插件列表失败: ${error.message}
            </div>
        `;
    }
}

// 过滤插件
function filterPlugins(filter) {
    let filteredPlugins = [];

    if (filter === 'all') {
        filteredPlugins = plugins;
    } else if (filter === 'enabled') {
        filteredPlugins = plugins.filter(plugin => plugin.enabled);
    } else if (filter === 'disabled') {
        filteredPlugins = plugins.filter(plugin => !plugin.enabled);
    } else {
        filteredPlugins = plugins;
    }

    renderPluginList(filteredPlugins);
}

// 渲染插件列表
function renderPluginList(pluginsList) {
    const pluginList = document.getElementById('plugin-list');

    if (pluginsList.length === 0) {
        pluginList.innerHTML = `
            <div class="alert alert-info text-center">
                <i class="bi bi-info-circle-fill me-2"></i>
                未找到匹配的插件
            </div>
        `;
        return;
    }

    // 清空容器，保留plugin-grid类以使用CSS网格布局
    pluginList.innerHTML = '';

    // 为每个插件创建卡片并添加到网格布局中
    pluginsList.forEach(plugin => {
        const statusIcon = plugin.enabled ?
            '<i class="bi bi-check-circle-fill text-success status-icon" title="已启用"></i>' :
            '<i class="bi bi-x-circle-fill text-secondary status-icon" title="已禁用"></i>';

        // 检查插件是否有更新
        let hasUpdate = false;
        let marketVersion = '';

        if (marketPlugins && Array.isArray(marketPlugins)) {
            const marketPlugin = marketPlugins.find(p => p.name === plugin.name);
            if (marketPlugin && marketPlugin.version) {
                marketVersion = marketPlugin.version;
                // 比较版本号
                hasUpdate = compareVersions(marketVersion, plugin.version || '1.0.0') > 0;
            }
        }

        // 根据插件名生成稳定的渐变色
        const colors = [
            ['#1abc9c', '#16a085'], // 绿松石
            ['#3498db', '#2980b9'], // 蓝色
            ['#9b59b6', '#8e44ad'], // 紫色
            ['#e74c3c', '#c0392b'], // 红色
            ['#f1c40f', '#f39c12'], // 黄色
            ['#2ecc71', '#27ae60']  // 绿色
        ];
        const colorIndex = Math.abs(hashCode(plugin.name) % colors.length);
        const gradientColors = colors[colorIndex];

        // 创建卡片元素
        const cardElement = document.createElement('div');
        cardElement.className = 'card h-100 shadow border-0 rounded-4 overflow-hidden';
        if (!plugin.enabled) {
            cardElement.classList.add('disabled');
        }

        // 获取框架标识
        let frameworkBadge = '';
        if (plugin.framework) {
            const frameworkName = plugin.framework === 'original' ? '原框架' : 'DOW框架';
            const badgeColor = plugin.framework === 'original' ? 'info' : 'primary';
            frameworkBadge = `<span class="badge bg-${badgeColor} me-2" title="来自${frameworkName}">${frameworkName}</span>`;
        }

        // 生成卡片HTML
        cardElement.innerHTML = `
            <div class="card-header p-3 pt-4 pb-4 bg-gradient-light border-0 position-relative" style="background: linear-gradient(135deg, #f8f9fa, #e9ecef);">
                <div class="plugin-status-container">
                    ${frameworkBadge}
                    ${hasUpdate ? `<i class="bi bi-arrow-up-circle-fill text-warning status-icon me-2" title="可更新到: v${marketVersion}"></i>` : ''}
                    ${statusIcon}
                    <div class="form-check form-switch plugin-switch ms-2">
                        <input class="form-check-input plugin-toggle" type="checkbox" id="toggle-${plugin.id}" ${plugin.enabled ? 'checked' : ''} data-plugin-id="${plugin.id}">
                        <label class="form-check-label visually-hidden" for="toggle-${plugin.id}">启用/禁用</label>
                    </div>
                </div>
                <div class="d-flex align-items-center">
                    <div class="plugin-icon rounded-circle shadow-sm" style="background: linear-gradient(135deg, ${gradientColors[0]}, ${gradientColors[1]});">
                        <i class="bi ${isAdapter ? 'bi-plug' : 'bi-puzzle'}"></i>
                    </div>
                    <div class="ms-3" style="min-width: 0; flex: 1;">
                        <h5 class="card-title mb-0 fw-bold text-truncate" title="${plugin.name}">${plugin.name}</h5>
                        <div class="text-muted small">v${plugin.version || '1.0.0'}</div>
                    </div>
                </div>
            </div>
            <div class="card-body p-3 d-flex flex-column">
                <p class="card-text text-truncate-2" title="${plugin.description}">${plugin.description || '暂无描述'}</p>
                <div class="mt-auto pt-3">
                    <div class="text-muted small text-truncate mb-2" title="${plugin.author || '未知作者'}">
                        <i class="bi bi-person me-1"></i>${plugin.author || '未知作者'}
                    </div>
                    <div class="d-flex flex-wrap gap-2 justify-content-start align-items-center">
                        <div class="d-flex gap-1">
                            <button class="btn btn-sm btn-outline-secondary rounded-pill btn-readme" data-plugin-id="${plugin.id}">
                                <i class="bi bi-book me-1"></i>说明
                            </button>
                            <button class="btn btn-sm btn-outline-primary rounded-pill btn-config" data-plugin-id="${plugin.id}" ${!plugin.enabled ? 'disabled' : ''}>
                                <i class="bi bi-gear-fill me-1"></i>配置
                            </button>
                            ${hasUpdate ? `
                            <button class="btn btn-sm btn-outline-warning rounded-pill btn-update" data-plugin-id="${plugin.id}" data-plugin-name="${plugin.name}" data-market-version="${marketVersion}">
                                <i class="bi bi-arrow-up-circle me-1"></i>更新
                            </button>` : ''}
                            ${plugin.id !== 'ManagePlugin' ? `
                            <button class="btn btn-sm btn-outline-danger rounded-pill btn-delete" data-plugin-id="${plugin.id}">
                                <i class="bi bi-trash me-1"></i>删除
                            </button>` : ''}
                        </div>
                    </div>
                </div>
            </div>
        `;

        // 直接添加到插件列表容器（使用grid布局）
        pluginList.appendChild(cardElement);
    });

    // 添加CSS样式
    if (!document.querySelector('#plugin-list-styles')) {
        const styleEl = document.createElement('style');
        styleEl.id = 'plugin-list-styles';
        styleEl.textContent = `
            #plugin-list {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                gap: 1.5rem;
                width: 100%;
            }
            .text-truncate-2 {
                display: -webkit-box;
                -webkit-line-clamp: 2;
                -webkit-box-orient: vertical;
                overflow: hidden;
                text-overflow: ellipsis;
                max-height: 3em;
            }
            #plugin-list .plugin-icon {
                width: 45px;
                height: 45px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-size: 1.2rem;
                flex-shrink: 0;
                border-radius: 50%;
                overflow: hidden;
                box-shadow: 0 3px 6px rgba(0,0,0,0.1);
            }
            #plugin-list .card {
                height: 100%;
                transition: transform 0.3s, box-shadow 0.3s;
                box-shadow: 0 5px 15px rgba(0,0,0,0.05);
            }
            #plugin-list .card:hover {
                transform: translateY(-5px);
                box-shadow: 0 15px 30px rgba(0,0,0,0.1) !important;
            }
            #plugin-list .badge {
                font-weight: 500;
                padding: 0.4em 0.8em;
            }
            #plugin-list .btn-sm {
                padding: 0.25rem 0.75rem;
                font-size: 0.75rem;
                white-space: nowrap;
            }
            #plugin-list .plugin-status-container {
                position: absolute;
                top: 12px;
                right: 12px;
                display: flex;
                align-items: center;
                z-index: 10;
                background-color: rgba(255, 255, 255, 0.8);
                border-radius: 20px;
                padding: 2px 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            }
            #plugin-list .plugin-switch {
                position: relative;
                margin: 0;
                padding: 0;
            }
            #plugin-list .form-check-input {
                cursor: pointer;
                width: 2.5em;
                height: 1.25em;
                margin: 0;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            #plugin-list .status-icon {
                font-size: 1.2rem;
                margin-right: 4px;
                line-height: 1;
                display: flex;
                align-items: center;
            }
            #plugin-list .bi-check-circle-fill,
            #plugin-list .bi-x-circle-fill, 
            #plugin-list .bi-arrow-up-circle-fill {
                font-size: 1.1rem;
                line-height: 1;
                display: flex;
                align-items: center;
                justify-content: center;
                width: 22px;
                height: 22px;
                cursor: help;
            }
            #plugin-list .text-truncate {
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
                max-width: 100%;
            }
            #plugin-list .card-title.text-truncate {
                max-width: 100%;
                display: block;
            }
            #plugin-list .d-flex.align-items-center {
                width: 100%;
            }
            #plugin-list .gap-1 {
                gap: 0.25rem !important;
            }
            #plugin-list .gap-2 {
                gap: 0.5rem !important;
            }
            #plugin-list .disabled {
                opacity: 0.7;
            }
            .bg-gradient-light {
                background: linear-gradient(135deg, #f8f9fa, #e9ecef);
            }

            /* 响应式布局 */
            @media (min-width: 1400px) {
                #plugin-list {
                    grid-template-columns: repeat(4, 1fr);
                }
            }
            @media (min-width: 992px) and (max-width: 1399px) {
                #plugin-list {
                    grid-template-columns: repeat(3, 1fr);
                }
            }
            @media (min-width: 768px) and (max-width: 991px) {
                #plugin-list {
                    grid-template-columns: repeat(2, 1fr);
                }
            }
            @media (max-width: 767px) {
                #plugin-list {
                    grid-template-columns: 1fr;
                }
            }
        `;
        document.head.appendChild(styleEl);
    }

    // 绑定事件
    document.querySelectorAll('.plugin-toggle').forEach(toggle => {
        toggle.addEventListener('change', function() {
            const pluginId = this.getAttribute('data-plugin-id');
            togglePlugin(pluginId);
        });
    });

    document.querySelectorAll('.btn-config').forEach(button => {
        button.addEventListener('click', function() {
            const pluginId = this.getAttribute('data-plugin-id');
            openConfigModal(pluginId);
        });
    });

    document.querySelectorAll('.btn-readme').forEach(button => {
        button.addEventListener('click', function() {
            const pluginId = this.getAttribute('data-plugin-id');
            openReadmeModal(pluginId);
        });
    });

    document.querySelectorAll('.btn-delete').forEach(button => {
        button.addEventListener('click', function() {
            const pluginId = this.getAttribute('data-plugin-id');
            confirmDeletePlugin(pluginId);
        });
    });

    // 绑定更新按钮事件
    document.querySelectorAll('.btn-update').forEach(button => {
        button.addEventListener('click', function() {
            const pluginId = this.getAttribute('data-plugin-id');
            const pluginName = this.getAttribute('data-plugin-name');
            const marketVersion = this.getAttribute('data-market-version');

            // 在插件市场中查找对应的插件
            const marketPlugin = marketPlugins.find(p => p.name === pluginName);
            if (marketPlugin) {
                // 调用安装插件函数进行更新
                installPlugin(marketPlugin);
                showToast(`正在更新插件 ${pluginName} 到版本 ${marketVersion}`, 'info');
            } else {
                showToast(`无法找到插件 ${pluginName} 的市场信息`, 'danger');
            }
        });
    });
}

// 切换插件状态
async function togglePlugin(pluginId) {
    const plugin = plugins.find(p => p.id === pluginId);
    if (!plugin) return;

    try {
        const action = plugin.enabled ? 'disable' : 'enable';
        const response = await fetch(`/api/plugins/${pluginId}/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (result.success) {
            // 更新本地状态
            plugin.enabled = !plugin.enabled;

            // 刷新UI
            filterPlugins(currentFilter);

            // 显示提示
            showToast(`插件已${action === 'enable' ? '启用' : '禁用'}`, 'success');
        } else {
            throw new Error(result.error || `操作失败`);
        }
    } catch (error) {
        console.error('切换插件状态失败:', error);
        showToast(`操作失败: ${error.message}`, 'danger');
    }
}

// 打开README模态框
async function openReadmeModal(pluginId) {
    try {
        console.log(`正在获取插件 ${pluginId} 的README.md内容`);

        const plugin = plugins.find(p => p.id === pluginId);
        if (!plugin) {
            showToast('插件不存在', 'danger');
            return;
        }

        // 获取模态框元素
        const modalEl = document.getElementById('plugin-readme-modal');
        if (!modalEl) {
            throw new Error('找不到README模态框元素');
        }

        // 重置状态
        document.getElementById('plugin-readme-loading').style.display = 'block';
        document.getElementById('plugin-readme-error').classList.add('d-none');
        document.getElementById('plugin-readme-content').innerHTML = '';

        // 设置标题
        document.getElementById('plugin-readme-title').textContent = `${plugin.name} 使用说明`;

        // 确保销毁旧的模态框实例
        const oldModal = bootstrap.Modal.getInstance(modalEl);
        if (oldModal) {
            oldModal.dispose();
        }

        // 创建新的模态框实例
        const modal = new bootstrap.Modal(modalEl);

        // 显示模态框
        modal.show();

        // 获取README内容
        console.log(`发送请求到 /api/plugin_readme?plugin_id=${encodeURIComponent(pluginId)}`);
        const response = await fetch(`/api/plugin_readme?plugin_id=${encodeURIComponent(pluginId)}`);
        const data = await response.json();

        console.log(`插件 ${pluginId} 的README.md响应:`, data);

        // 隐藏加载状态
        document.getElementById('plugin-readme-loading').style.display = 'none';

        if (data.success) {
            console.log(`成功获取插件 ${pluginId} 的README.md内容，长度: ${data.readme.length}`);
            // 使用marked将Markdown渲染为HTML
            const readmeHtml = marked.parse(data.readme);
            document.getElementById('plugin-readme-content').innerHTML = readmeHtml;
        } else {
            console.error(`获取插件 ${pluginId} 的README.md失败:`, data.message || data.error);
            // 显示错误信息
            const errorEl = document.getElementById('plugin-readme-error');
            errorEl.classList.remove('d-none');
            errorEl.querySelector('span').textContent = data.message || data.error || '该插件暂无使用说明';
        }
    } catch (error) {
        console.error(`打开插件 ${pluginId} 的README模态框失败:`, error);

        // 隐藏加载状态
        document.getElementById('plugin-readme-loading').style.display = 'none';

        // 显示错误信息
        const errorEl = document.getElementById('plugin-readme-error');
        errorEl.classList.remove('d-none');
        errorEl.querySelector('span').textContent = error.message || '该插件暂无使用说明';
    }
}

// 打开配置模态框
async function openConfigModal(pluginId) {
    try {
        const plugin = plugins.find(p => p.id === pluginId);
        if (!plugin) {
            showToast('插件不存在', 'danger');
            return;
        }

        // 获取模态框元素
        const modalEl = document.getElementById('plugin-config-modal');
        if (!modalEl) {
            throw new Error('找不到配置模态框元素');
        }

        // 重置表单状态
        document.getElementById('plugin-config-loading').style.display = 'block';
        document.getElementById('plugin-config-error').style.display = 'none';
        document.getElementById('plugin-config-form').innerHTML = '';

        // 设置标题
        document.getElementById('plugin-config-title').textContent = `${plugin.name} 配置文件`;

        // 确保销毁旧的模态框实例
        const oldModal = bootstrap.Modal.getInstance(modalEl);
        if (oldModal) {
            oldModal.dispose();
        }

        // 创建新的模态框实例
        const modal = new bootstrap.Modal(modalEl, {
            backdrop: 'static',
            keyboard: true
        });

        // 显示模态框
        modal.show();

        try {
            // 获取配置文件路径
            const response = await fetch(`/api/plugin_config_file?plugin_id=${pluginId}`);
            const data = await response.json();

            if (data.success && data.config_file) {
                // 获取文件内容
                const contentResponse = await fetch(`/api/files/read?path=${encodeURIComponent(data.config_file)}`);
                const contentData = await contentResponse.json();

                if (contentData.success) {
                    // 创建文本编辑器
                    const formContainer = document.getElementById('plugin-config-form');
                    formContainer.innerHTML = `
                        <div class="alert alert-info mb-3">
                            <i class="bi bi-info-circle-fill me-2"></i>
                            正在编辑: ${data.config_file}
                        </div>
                        <div class="mb-3">
                            <textarea id="config-editor" class="form-control" style="min-height: 300px; font-family: monospace;">${contentData.content}</textarea>
                        </div>
                    `;

                    // 存储当前配置文件路径，用于保存
                    document.getElementById('plugin-config-save').setAttribute('data-config-file', data.config_file);
                    document.getElementById('plugin-config-save').textContent = '保存';

                    document.getElementById('plugin-config-loading').style.display = 'none';
                } else {
                    throw new Error(contentData.error || '无法读取配置文件内容');
                }
            } else {
                throw new Error(data.error || '无法获取配置文件');
            }
        } catch (error) {
            console.error('加载配置文件失败:', error);
            document.getElementById('plugin-config-loading').style.display = 'none';
            document.getElementById('plugin-config-error').style.display = 'block';
            document.getElementById('plugin-config-error').querySelector('span').textContent = `加载配置失败: ${error.message}`;
        }
    } catch (error) {
        console.error('打开配置失败:', error);
        showToast(`配置界面加载失败: ${error.message}`, 'danger');
    }
}

// 保存配置
async function savePluginConfig() {
    try {
        // 获取配置文件路径
        const configFile = document.getElementById('plugin-config-save').getAttribute('data-config-file');
        if (!configFile) {
            throw new Error('未找到配置文件路径');
        }

        // 获取编辑器内容
        let content = document.getElementById('config-editor').value;

        // 检查是否是JSON文件
        if (configFile.toLowerCase().endsWith('.json')) {
            try {
                // 尝试解析JSON
                const jsonObj = JSON.parse(content);
                // 重新格式化JSON，确保格式正确
                content = JSON.stringify(jsonObj, null, 4);
                console.log("JSON格式化成功");
            } catch (jsonError) {
                throw new Error(`JSON格式错误: ${jsonError.message}`);
            }
        }

        // 显示保存中状态
        const saveBtn = document.getElementById('plugin-config-save');
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>保存中...';

        // 发送保存请求
        const response = await fetch('/api/files/write', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                path: configFile,
                content: content
            })
        });

        const data = await response.json();

        if (data.success) {
            showToast('配置已保存', 'success');
            // 关闭模态框
            const modalEl = document.getElementById('plugin-config-modal');
            const modalInstance = bootstrap.Modal.getInstance(modalEl);
            modalInstance.hide();
        } else {
            throw new Error(data.error || '保存失败');
        }
    } catch (error) {
        console.error('保存配置失败:', error);
        showToast(`保存配置失败: ${error.message}`, 'danger');
    } finally {
        // 恢复保存按钮状态
        const saveBtn = document.getElementById('plugin-config-save');
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
    }
}

// 监听原生配置界面
function setupConfigContainerObserver() {
    // 监听配置容器的变化
    const configContainer = document.querySelector('#config-container');
    if (configContainer) {
        console.log('已找到配置容器');
    }
}

// 显示提示
function showToast(message, type = 'info') {
    // 查找或创建toast容器
    let toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }

    // 创建toast
    const id = 'toast-' + Date.now();
    const html = `
        <div id="${id}" class="toast align-items-center text-white bg-${type}" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;

    toastContainer.insertAdjacentHTML('beforeend', html);

    // 显示toast
    const toastEl = document.getElementById(id);
    const toast = new bootstrap.Toast(toastEl, { autohide: true, delay: 3000 });
    toast.show();

    // 清理
    toastEl.addEventListener('hidden.bs.toast', function() {
        this.remove();
    });
}

// 创建右上角更新提示
function createUpdateNotification(updateCount, updatablePlugins) {
    // 移除现有的更新提示
    const existingNotification = document.getElementById('update-notification');
    if (existingNotification) {
        existingNotification.remove();
    }

    if (updateCount <= 0) return;

    // 创建更新提示元素
    const notification = document.createElement('div');
    notification.id = 'update-notification';
    notification.className = 'position-fixed top-0 end-0 p-3';
    notification.style.zIndex = '1050';

    // 生成插件更新列表HTML
    let pluginsListHtml = '';
    updatablePlugins.forEach(plugin => {
        pluginsListHtml += `
            <div class="d-flex justify-content-between align-items-center border-bottom pb-2 mb-2">
                <div>
                    <strong>${plugin.name}</strong>
                    <div class="small text-muted">${plugin.currentVersion} → ${plugin.newVersion}</div>
                </div>
                <button class="btn btn-sm btn-outline-warning rounded-pill btn-update-plugin"
                        data-plugin-name="${plugin.name}"
                        data-current-version="${plugin.currentVersion}"
                        data-new-version="${plugin.newVersion}">
                    <i class="bi bi-arrow-up-circle me-1"></i>更新
                </button>
            </div>
        `;
    });

    // 生成完整的提示HTML
    notification.innerHTML = `
        <div class="toast show" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header bg-warning text-white">
                <i class="bi bi-arrow-up-circle-fill me-2"></i>
                <strong class="me-auto">插件更新可用</strong>
                <small>${updateCount} 个插件可更新</small>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body">
                <div class="update-plugins-list">
                    ${pluginsListHtml}
                </div>
                <div class="d-flex justify-content-between mt-3">
                    <button class="btn btn-sm btn-outline-secondary" id="dismiss-update-notification">
                        <i class="bi bi-x-circle me-1"></i>关闭
                    </button>
                    <button class="btn btn-sm btn-warning" id="update-all-plugins">
                        <i class="bi bi-arrow-repeat me-1"></i>更新全部
                    </button>
                </div>
            </div>
        </div>
    `;

    // 添加到文档中
    document.body.appendChild(notification);

    // 添加样式
    if (!document.getElementById('update-notification-styles')) {
        const styleEl = document.createElement('style');
        styleEl.id = 'update-notification-styles';
        styleEl.textContent = `
            #update-notification .toast {
                min-width: 350px;
                box-shadow: 0 0.5rem 1rem rgba(0, 0, 0, 0.15);
                border: none;
                border-radius: 0.5rem;
            }
            #update-notification .toast-header {
                background-color: #ffc107;
                color: #212529;
                border-top-left-radius: 0.5rem;
                border-top-right-radius: 0.5rem;
                border-bottom: 1px solid rgba(0,0,0,0.05);
            }
            #update-notification .update-plugins-list {
                max-height: 200px;
                overflow-y: auto;
            }
            #update-notification .btn-sm {
                padding: 0.25rem 0.5rem;
                font-size: 0.75rem;
            }
        `;
        document.head.appendChild(styleEl);
    }

    // 添加事件监听器
    // 关闭按钮
    document.getElementById('dismiss-update-notification').addEventListener('click', function() {
        document.getElementById('update-notification').remove();
    });

    // 更新全部按钮
    document.getElementById('update-all-plugins').addEventListener('click', function() {
        updateAllPlugins(updatablePlugins);
    });

    // 单个插件更新按钮
    document.querySelectorAll('.btn-update-plugin').forEach(button => {
        button.addEventListener('click', function() {
            const pluginName = this.getAttribute('data-plugin-name');
            const currentVersion = this.getAttribute('data-current-version');
            const newVersion = this.getAttribute('data-new-version');

            // 在插件市场中查找对应的插件
            const marketPlugin = marketPlugins.find(p => p.name === pluginName);
            if (marketPlugin) {
                // 调用安装插件函数进行更新
                installPlugin(marketPlugin);
                showToast(`正在更新插件 ${pluginName} 从 ${currentVersion} 到 ${newVersion}`, 'info');
            } else {
                showToast(`无法找到插件 ${pluginName} 的市场信息`, 'danger');
            }
        });
    });
}

// 更新所有插件
function updateAllPlugins(updatablePlugins) {
    if (!updatablePlugins || updatablePlugins.length === 0) return;

    showToast(`正在更新 ${updatablePlugins.length} 个插件...`, 'info');

    // 创建一个更新队列
    const updateQueue = [...updatablePlugins];

    // 递归更新插件
    function processNextPlugin() {
        if (updateQueue.length === 0) {
            // 全部更新完成
            showToast('所有插件更新完成', 'success');
            // 移除更新提示
            const notification = document.getElementById('update-notification');
            if (notification) notification.remove();
            return;
        }

        const plugin = updateQueue.shift();
        const marketPlugin = marketPlugins.find(p => p.name === plugin.name);

        if (marketPlugin) {
            // 安装插件
            installPlugin(marketPlugin)
                .then(() => {
                    console.log(`插件 ${plugin.name} 更新成功`);
                    // 继续处理下一个插件
                    setTimeout(processNextPlugin, 1000); // 添加延迟，避免请求过快
                })
                .catch(error => {
                    console.error(`插件 ${plugin.name} 更新失败:`, error);
                    showToast(`插件 ${plugin.name} 更新失败: ${error.message}`, 'danger');
                    // 继续处理下一个插件
                    setTimeout(processNextPlugin, 1000);
                });
        } else {
            console.error(`无法找到插件 ${plugin.name} 的市场信息`);
            showToast(`无法找到插件 ${plugin.name} 的市场信息`, 'danger');
            // 继续处理下一个插件
            setTimeout(processNextPlugin, 1000);
        }
    }

    // 开始处理第一个插件
    processNextPlugin();
}

// 确认删除插件
function confirmDeletePlugin(pluginId) {
    const plugin = plugins.find(p => p.id === pluginId);
    if (!plugin) {
        showToast('插件不存在', 'danger');
        return;
    }

    // 检查是否是核心插件
    if (pluginId === 'ManagePlugin') {
        showToast('无法删除核心插件', 'danger');
        return;
    }

    // 创建确认对话框
    if (!document.getElementById('delete-plugin-modal')) {
        const modalHtml = `
            <div class="modal fade" id="delete-plugin-modal" tabindex="-1" aria-labelledby="delete-plugin-modal-label" aria-hidden="true">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="delete-plugin-modal-label">确认删除插件</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body">
                            <div class="alert alert-warning">
                                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                                <span>警告：删除插件将会移除插件的所有文件，包括配置文件。此操作不可恢复！</span>
                            </div>
                            <p>您确定要删除插件 <strong id="delete-plugin-name"></strong> 吗？</p>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                            <button type="button" class="btn btn-danger" id="confirm-delete-btn">
                                <i class="bi bi-trash me-1"></i>确认删除
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        document.body.insertAdjacentHTML('beforeend', modalHtml);
    }

    // 设置插件名称
    document.getElementById('delete-plugin-name').textContent = plugin.name;

    // 移除旧的事件监听器
    const confirmBtn = document.getElementById('confirm-delete-btn');
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);

    // 添加新的事件监听器
    newConfirmBtn.addEventListener('click', function() {
        deletePlugin(pluginId);
    });

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('delete-plugin-modal'));
    modal.show();
}

// 删除插件
async function deletePlugin(pluginId) {
    try {
        // 显示加载状态
        const confirmBtn = document.getElementById('confirm-delete-btn');
        const originalHtml = confirmBtn.innerHTML;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>正在删除...';
        confirmBtn.disabled = true;

        // 发送删除请求
        // 尝试使用备用路由
        const response = await fetch('/api/plugin/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plugin_id: pluginId })
        });

        const result = await response.json();

        // 隐藏模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('delete-plugin-modal'));
        modal.hide();

        if (result.success) {
            // 从插件列表中移除
            plugins = plugins.filter(p => p.id !== pluginId);

            // 更新插件计数
            document.getElementById('plugin-count').textContent = plugins.length;

            // 刷新UI
            filterPlugins(currentFilter);

            // 显示成功提示
            showToast(result.message || '插件已成功删除', 'success');
        } else {
            // 恢复按钮状态
            confirmBtn.innerHTML = originalHtml;
            confirmBtn.disabled = false;

            // 显示错误提示
            showToast(result.error || '删除插件失败', 'danger');
        }
    } catch (error) {
        console.error('删除插件失败:', error);

        // 隐藏模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('delete-plugin-modal'));
        modal.hide();

        // 显示错误提示
        showToast(`删除插件失败: ${error.message}`, 'danger');
    }
}

// 搜索插件
function searchPlugins(keyword) {
    if (!keyword.trim()) {
        filterPlugins(currentFilter);
        return;
    }

    const lowerKeyword = keyword.toLowerCase().trim();
    const results = plugins.filter(plugin => {
        return (
            plugin.name.toLowerCase().includes(lowerKeyword) ||
            (plugin.description && plugin.description.toLowerCase().includes(lowerKeyword)) ||
            (plugin.author && plugin.author.toLowerCase().includes(lowerKeyword))
        );
    });

    renderPluginList(results);
}

// 加载插件市场
async function loadPluginMarket() {
    const marketList = document.getElementById('market-list');

    try {
        // 先显示加载中的状态
        marketList.innerHTML = `
            <div class="col">
                <div class="text-center py-5">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                    <p class="mt-3 text-muted">加载插件市场中...</p>
                </div>
            </div>
        `;

        console.log('开始加载插件市场数据');

        // 检查网络连接
        const online = await checkConnection();

        if (!online) {
            // 如果离线，尝试加载缓存数据
            const loaded = loadCachedPluginMarket();
            if (!loaded) {
                throw new Error('无法连接到插件市场服务器，且没有缓存数据');
            }
            return;
        }

        console.log('API端点:', `${PLUGIN_MARKET_API.BASE_URL}${PLUGIN_MARKET_API.LIST}`);

        // 获取插件市场数据
        const response = await fetch(`${PLUGIN_MARKET_API.BASE_URL}${PLUGIN_MARKET_API.LIST}`);

        console.log('服务器响应状态:', response.status);

        if (!response.ok) {
            throw new Error(`服务器返回错误: ${response.status}`);
        }

        const data = await response.json();
        console.log('服务器返回数据:', data);

        // 将API响应转换为我们需要的格式
        marketPlugins = data.plugins.map(plugin => {
            return {
                id: plugin.id,
                name: plugin.name,
                description: plugin.description,
                author: plugin.author,
                version: plugin.version,
                github_url: plugin.github_url,
                tags: plugin.tags.map(tag => tag.name).join(', ')
            };
        });

        // 缓存数据
        cachePluginMarketData(marketPlugins);

        // 渲染插件
        renderMarketPlugins(marketPlugins);
    } catch (error) {
        console.error('加载插件市场失败:', error);

        // 尝试从缓存加载
        const loaded = loadCachedPluginMarket();

        if (!loaded) {
            // 显示错误信息
            marketList.innerHTML = `
                <div class="col">
                    <div class="alert alert-danger">
                        <i class="bi bi-exclamation-triangle-fill me-2"></i>
                        加载插件市场失败: ${error.message}
                    </div>
                </div>
            `;
        }
    }
}

// 缓存插件市场数据
function cachePluginMarketData(plugins) {
    const cacheData = {
        timestamp: Date.now(),
        plugins: plugins
    };

    localStorage.setItem(PLUGIN_MARKET_API.CACHE_KEY, JSON.stringify(cacheData));
}

// 加载缓存的插件市场数据
function loadCachedPluginMarket() {
    try {
        const cacheData = localStorage.getItem(PLUGIN_MARKET_API.CACHE_KEY);
        if (!cacheData) {
            const marketList = document.getElementById('market-list');
            marketList.innerHTML = `
                <div class="col">
                    <div class="alert alert-info text-center">
                        <i class="bi bi-info-circle-fill me-2"></i>
                        没有可用的缓存数据，请检查网络连接并刷新
                    </div>
                </div>
            `;
            return false;
        }

        const parsedData = JSON.parse(cacheData);
        const cacheAge = Date.now() - parsedData.timestamp;

        marketPlugins = parsedData.plugins;
        renderMarketPlugins(marketPlugins);

        // 显示缓存提示
        const marketList = document.getElementById('market-list');
        if (marketList.children.length > 0) {
            const alertDiv = document.createElement('div');
            alertDiv.className = 'col-12 mb-3';

            if (cacheAge > PLUGIN_MARKET_API.CACHE_EXPIRY) {
                alertDiv.innerHTML = `
                    <div class="alert alert-warning">
                        <i class="bi bi-exclamation-triangle-fill me-2"></i>
                        显示的是缓存数据 (${formatTimeAgo(parsedData.timestamp)})，可能已过期
                    </div>
                `;
            } else {
                alertDiv.innerHTML = `
                    <div class="alert alert-info">
                        <i class="bi bi-info-circle-fill me-2"></i>
                        显示的是缓存数据 (${formatTimeAgo(parsedData.timestamp)})
                    </div>
                `;
            }

            marketList.insertBefore(alertDiv, marketList.firstChild);
        }

        return true;
    } catch (error) {
        console.error('加载缓存数据失败:', error);
        const marketList = document.getElementById('market-list');
        marketList.innerHTML = `
            <div class="col">
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>
                    缓存数据加载失败: ${error.message}
                </div>
            </div>
        `;
        return false;
    }
}

// 渲染插件市场列表
function renderMarketPlugins(marketPluginsList) {
    const marketList = document.getElementById('market-list');

    if (marketPluginsList.length === 0) {
        marketList.innerHTML = `
            <div class="col">
                <div class="alert alert-info text-center">
                    <i class="bi bi-info-circle-fill me-2"></i>
                    暂无插件，请点击"提交插件"按钮添加新插件
                </div>
            </div>
        `;
        return;
    }

    // 对插件列表进行排序
    // 1. 需要更新的插件放在前面
    // 2. 然后按照返回顺序的倒序排列（最新添加的先显示）
    sortedPlugins = [...marketPluginsList].sort((a, b) => {
        // 检查是否有插件需要更新
        const aInstalled = plugins && Array.isArray(plugins) ? plugins.find(p => p.name === a.name) : null;
        const bInstalled = plugins && Array.isArray(plugins) ? plugins.find(p => p.name === b.name) : null;
        
        const aHasUpdate = aInstalled && compareVersions(a.version, aInstalled.version || '1.0.0') > 0;
        const bHasUpdate = bInstalled && compareVersions(b.version, bInstalled.version || '1.0.0') > 0;
        
        // 如果一个有更新，一个没有，有更新的放前面
        if (aHasUpdate && !bHasUpdate) return -1;
        if (!aHasUpdate && bHasUpdate) return 1;
        
        // 如果更新状态相同，按照原始列表的倒序排列（最新的先显示）
        // 假设列表中的顺序代表了添加时间，原始索引越大表示越新
        return marketPluginsList.indexOf(b) - marketPluginsList.indexOf(a);
    });

    // 重置市场列表HTML
    marketList.innerHTML = '';

    // 创建一个网格容器
    const gridContainer = document.createElement('div');
    gridContainer.id = 'market-grid';
    marketList.appendChild(gridContainer);

    sortedPlugins.forEach((plugin, index) => {
        // 将标签字符串转换为数组
        const tags = plugin.tags ? plugin.tags.split(',') : [];

        // 生成标签HTML
        let tagsHtml = '';
        if (tags.length > 0) {
            tagsHtml = '<div class="plugin-tags mb-2 d-flex flex-wrap">';
            tags.forEach(tag => {
                if (tag.trim()) {
                    tagsHtml += `<span class="badge bg-light text-dark me-1 mb-1">${tag.trim()}</span>`;
                }
            });
            tagsHtml += '</div>';
        }

        // 检查插件是否已安装，并比较版本
        let isInstalled = false;
        let hasUpdate = false;
        let installedVersion = '';

        if (plugins && Array.isArray(plugins)) {
            const installedPlugin = plugins.find(p => p.name === plugin.name);
            if (installedPlugin) {
                isInstalled = true;
                installedVersion = installedPlugin.version || '1.0.0';
                // 比较版本号
                hasUpdate = compareVersions(plugin.version, installedVersion) > 0;
            }
        }

        // 生成状态图标HTML
        const statusIcons = isInstalled ? 
            (hasUpdate ? 
                `<i class="bi bi-arrow-up-circle-fill text-warning status-icon me-2" title="可更新到: v${plugin.version}，当前版本: v${installedVersion}"></i>
                 <i class="bi bi-check-circle-fill text-success status-icon" title="已安装"></i>` : 
                `<i class="bi bi-check-circle-fill text-success status-icon" title="已安装"></i>`) 
            : '';

        // 添加特殊标记 - 更新提示
        let updateBadge = '';
        if (hasUpdate) {
            updateBadge = `<div class="position-absolute start-0 top-0 p-2">
                <span class="badge bg-warning">可更新</span>
            </div>`;
        }

        // 生成渐变色背景（根据插件名生成一个稳定的颜色）
        const colors = [
            ['#1abc9c', '#16a085'], // 绿松石
            ['#3498db', '#2980b9'], // 蓝色
            ['#9b59b6', '#8e44ad'], // 紫色
            ['#e74c3c', '#c0392b'], // 红色
            ['#f1c40f', '#f39c12'], // 黄色
            ['#2ecc71', '#27ae60']  // 绿色
        ];
        const colorIndex = Math.abs(hashCode(plugin.name) % colors.length);
        const gradientColors = colors[colorIndex];

        // 创建卡片元素
        const cardElement = document.createElement('div');
        cardElement.className = 'market-card';
        
        // 添加额外的类以便于样式区分
        if (hasUpdate) {
            cardElement.classList.add('has-update');
        }

        // 生成卡片HTML
        cardElement.innerHTML = `
            <div class="card h-100 shadow border-0 rounded-4 overflow-hidden ${hasUpdate ? 'border border-warning' : ''}">
                <div class="card-header p-3 bg-gradient-light border-0 position-relative" style="background: linear-gradient(135deg, #f8f9fa, #e9ecef);">
                    ${statusIcons ? `<div class="plugin-status-container">${statusIcons}</div>` : ''}
                    ${updateBadge}
                    <div class="d-flex align-items-center">
                        <div class="plugin-icon rounded-circle shadow-sm" style="background: linear-gradient(135deg, ${gradientColors[0]}, ${gradientColors[1]});">
                            <i class="bi bi-puzzle"></i>
                        </div>
                        <div class="ms-3" style="min-width: 0; flex: 1;">
                            <h5 class="card-title mb-0 fw-bold text-truncate" title="${plugin.name}">${plugin.name}</h5>
                            <div class="text-muted small">v${plugin.version}</div>
                        </div>
                    </div>
                </div>
                <div class="card-body p-3 d-flex flex-column">
                    <p class="card-text text-truncate-2" title="${plugin.description}">${plugin.description}</p>
                    ${tagsHtml}
                    <div class="mt-auto pt-3">
                        <div class="text-muted small text-truncate mb-2" title="${plugin.author}">
                            <i class="bi bi-person me-1"></i>${plugin.author}
                        </div>
                        <div class="d-flex justify-content-end">
                            ${plugin.github_url ? `
                            <a href="${plugin.github_url}" target="_blank" class="btn btn-sm btn-outline-secondary rounded-pill me-2" title="访问GitHub仓库">
                                <i class="bi bi-github me-1"></i>GitHub
                            </a>` : ''}
                            <button class="btn ${isInstalled ? (hasUpdate ? 'btn-outline-warning' : 'btn-outline-primary') : 'btn-primary'} btn-sm rounded-pill btn-install-plugin" data-plugin-index="${index}">
                                <i class="bi ${isInstalled ? (hasUpdate ? 'bi-arrow-up-circle' : 'bi-arrow-repeat') : 'bi-download'} me-1"></i>${isInstalled ? (hasUpdate ? '更新' : '重新安装') : '安装'}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // 添加到网格容器
        gridContainer.appendChild(cardElement);
    });

    // 添加CSS样式
    const styleEl = document.createElement('style');
    styleEl.textContent = `
        #market-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1.5rem;
            width: 100%;
        }
        .text-truncate-2 {
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
            max-height: 3em;
        }
        #market-list .plugin-icon {
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 1.2rem;
            flex-shrink: 0;
            border-radius: 50%;
            overflow: hidden;
            box-shadow: 0 3px 6px rgba(0,0,0,0.1);
        }
        #market-list .card {
            transition: transform 0.3s, box-shadow 0.3s;
            box-shadow: 0 5px 15px rgba(0,0,0,0.05);
        }
        #market-list .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 30px rgba(0,0,0,0.1) !important;
        }
        #market-list .plugin-tags {
            min-height: 28px;
            display: flex;
            flex-wrap: wrap;
        }
        #market-list .badge {
            font-weight: 500;
            padding: 0.4em 0.8em;
        }
        #market-list .btn-sm {
            padding: 0.25rem 0.75rem;
            font-size: 0.75rem;
            white-space: nowrap;
        }
        #market-list .text-truncate {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            max-width: 100%;
        }
        #market-list .card-title.text-truncate {
            max-width: 100%;
            display: block;
        }
        #market-list .d-flex.align-items-center {
            width: 100%;
        }
        .bg-gradient-light {
            background: linear-gradient(135deg, #f8f9fa, #e9ecef);
        }
        #market-list .plugin-status-icons {
            display: flex;
            align-items: center;
        }
        #market-list .status-icon {
            font-size: 1.1rem;
            line-height: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 22px;
            height: 22px;
            cursor: help;
        }
        #market-list .bi-check-circle-fill,
        #market-list .bi-x-circle-fill, 
        #market-list .bi-arrow-up-circle-fill {
            font-size: 1.1rem;
            line-height: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 22px;
            height: 22px;
            cursor: help;
        }
        #market-list .plugin-status-container {
            position: absolute;
            top: 12px;
            right: 12px;
            display: flex;
            align-items: center;
            z-index: 10;
            background-color: rgba(255, 255, 255, 0.8);
            border-radius: 20px;
            padding: 2px 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        #market-list .has-update .card {
            box-shadow: 0 5px 15px rgba(255, 193, 7, 0.2);
            border: 1px solid rgba(255, 193, 7, 0.5) !important;
        }
        #market-list .has-update .card:hover {
            box-shadow: 0 15px 30px rgba(255, 193, 7, 0.3) !important;
        }
        #market-list .plugin-tags {
            min-height: 28px;
            display: flex;
            flex-wrap: wrap;
        }
    `;
    document.head.appendChild(styleEl);

    // 绑定安装按钮事件
    document.querySelectorAll('.btn-install-plugin').forEach(button => {
        button.addEventListener('click', function() {
            const index = parseInt(this.getAttribute('data-plugin-index'));
            const plugin = sortedPlugins[index];
            if (plugin) {
                installPlugin(plugin);
            }
        });
    });
}

// 搜索插件市场
function searchMarketPlugins(keyword) {
    if (!keyword) {
        renderMarketPlugins(marketPlugins);
        return;
    }

    const searchTerm = keyword.toLowerCase();
    const filteredPlugins = marketPlugins.filter(plugin => {
        return (
            plugin.name.toLowerCase().includes(searchTerm) ||
            plugin.description.toLowerCase().includes(searchTerm) ||
            plugin.author.toLowerCase().includes(searchTerm) ||
            (plugin.tags && plugin.tags.toLowerCase().includes(searchTerm))
        );
    });

    renderMarketPlugins(filteredPlugins);
}

// 提交插件到市场
async function submitPlugin() {
    console.log('==================== 开始提交流程 ====================');
    console.log('提交审核按钮被点击');
    const submitBtn = document.getElementById('btn-submit-plugin');
    const spinner = submitBtn.querySelector('.spinner-border');
    if (spinner) {
        spinner.classList.remove('d-none');
    }
    const errorDiv = document.getElementById('upload-error');

    // 显示加载状态
    submitBtn.disabled = true;

    try {
        const form = document.getElementById('upload-plugin-form');

        // 验证表单
        if (!validatePluginForm(form)) {
            console.log('表单验证失败');
            submitBtn.disabled = false;
            if (spinner) {
                spinner.classList.add('d-none');
            }
            return;
        }

        console.log('表单验证通过，准备提交');

        // 获取表单数据
        const formData = new FormData(form);

        // 转换为JSON对象
        const pluginData = {
            name: formData.get('name'),
            description: formData.get('description'),
            author: formData.get('author'),
            version: formData.get('version'),
            github_url: formData.get('github_url'),
            tags: formData.get('tags') ? formData.get('tags').split(',').map(tag => tag.trim()) : [],
            requirements: formData.get('requirements') ? formData.get('requirements').split(',').map(req => req.trim()) : [],
            icon: null // 图标将作为Base64处理
        };

        // 处理图标文件
        const iconFile = formData.get('icon');
        if (iconFile && iconFile.size > 0) {
            const iconBase64 = await readFileAsDataURL(iconFile);
            pluginData.icon = iconBase64;
        }

        console.log('正在提交插件数据:', pluginData);

        // 发送到服务器，使用PLUGIN_MARKET_API配置
        const response = await fetch(`${PLUGIN_MARKET_API.BASE_URL}${PLUGIN_MARKET_API.SUBMIT}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify(pluginData),
            credentials: 'omit',  // 使用omit模式避免凭证问题
            signal: AbortSignal.timeout(10000) // 10秒超时
        });

        console.log('服务器响应:', response.status);
        let responseText = '';
        let responseData = null;

        try {
            responseText = await response.text();
            responseData = responseText ? JSON.parse(responseText) : {};
            console.log('响应数据:', responseData);
        } catch (e) {
            console.error('解析响应失败:', e, '原始文本:', responseText);
        }

        if (response.ok && responseData && responseData.success) {
            console.log('提交成功');

            // 使用统一的模态窗口管理方式关闭模态框
            const modalEl = document.getElementById('upload-plugin-modal');
            if (modalEl) {
                const modalInstance = bootstrap.Modal.getInstance(modalEl);
                if (modalInstance) {
                    modalInstance.hide();
                    // 等待模态窗口完全关闭后再重置表单
                    modalEl.addEventListener('hidden.bs.modal', function onHidden() {
                        // 重置表单
                        form.reset();
                        // 移除事件监听器
                        modalEl.removeEventListener('hidden.bs.modal', onHidden);
                    });
                }
            }

            // 提示成功
            showToast('插件提交成功，等待审核', 'success');

            // 刷新插件市场
            setTimeout(() => loadPluginMarket(), 1000);
        } else {
            throw new Error(responseData?.error || '提交失败');
        }
    } catch (error) {
        console.error('提交插件失败:', error);

        // 显示错误信息
        errorDiv.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                提交失败: ${error.message}
            </div>
        `;
        errorDiv.style.display = 'block';
    } finally {
        // 恢复按钮状态
        submitBtn.disabled = false;
        if (spinner) {
            spinner.classList.add('d-none');
        }
    }
}

// 验证插件表单
function validatePluginForm(form) {
    console.log('开始验证表单字段...');
    console.log('表单元素:', form);
    const errorDiv = document.getElementById('upload-error');
    console.log('错误显示区域:', errorDiv);

    // 基本字段验证
    const requiredFields = ['name', 'description', 'author', 'version', 'github_url'];
    console.log('检查必填字段:', requiredFields);

    for (const field of requiredFields) {
        const input = form.querySelector(`[name="${field}"]`);
        console.log(`检查字段 ${field}:`, input ? '找到元素' : '未找到元素');

        if (!input) {
            console.error(`表单中缺少字段: ${field}`);
            errorDiv.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>
                    表单缺少必要字段: ${getFieldLabel(field)}
                </div>
            `;
            errorDiv.style.display = 'block';
            return false;
        }

        console.log(`字段 ${field} 的值:`, input.value);
        if (!input.value.trim()) {
            console.log(`字段 ${field} 为空，验证失败`);
            errorDiv.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>
                    ${getFieldLabel(field)}不能为空
                </div>
            `;
            errorDiv.style.display = 'block';
            return false;
        }
    }

    // 版本格式验证
    const versionInput = form.querySelector('[name="version"]');
    if (versionInput) {
        const version = versionInput.value.trim();
        const versionPattern = /^\d+(\.\d+)*$/;  // 例如: 1.0.0, 2.1, 1
        if (!versionPattern.test(version)) {
            console.log('版本格式不正确:', version);
            errorDiv.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>
                    版本格式不正确，应为数字和点组成，如: 1.0.0
                </div>
            `;
            errorDiv.style.display = 'block';
            return false;
        }
    }

    // GitHub URL验证
    const githubUrlInput = form.querySelector('[name="github_url"]');
    const githubUrl = githubUrlInput ? githubUrlInput.value.trim() : '';
    console.log('GitHub URL:', githubUrl);

    if (!githubUrl.startsWith('https://github.com/') && !githubUrl.startsWith('https://raw.githubusercontent.com/')) {
        console.log('GitHub URL格式不正确');
        errorDiv.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                GitHub链接必须以 https://github.com/ 或 https://raw.githubusercontent.com/ 开头
            </div>
        `;
        errorDiv.style.display = 'block';
        return false;
    }

    console.log('表单验证通过');
    return true;
}

// 存储离线提交的插件
function storeOfflineSubmission(pluginData, tempId) {
    let offlineQueue = JSON.parse(localStorage.getItem('xybot_offline_plugins') || '[]');

    offlineQueue.push({
        id: tempId,
        data: pluginData,
        timestamp: Date.now()
    });

    localStorage.setItem('xybot_offline_plugins', JSON.stringify(offlineQueue));
}

// 安装插件
async function installPlugin(plugin) {
    return new Promise(async (resolve, reject) => {
        // 查找安装按钮（如果存在）
        let index = -1;
        let button = null;
        
        // 安全地查找按钮索引
        if (Array.isArray(sortedPlugins) && sortedPlugins.length > 0) {
            index = sortedPlugins.findIndex(p => p.name === plugin.name);
            if (index >= 0) {
                button = document.querySelector(`.btn-install-plugin[data-plugin-index="${index}"]`);
            }
        }
        
        const originalText = button ? button.innerHTML : null;

        // 显示加载状态
        if (button) {
            button.disabled = true;
            button.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>正在安装...`;
        }

        try {
            // 获取 GitHub URL
            const githubUrl = plugin.github_url;
            if (!githubUrl) {
                throw new Error('插件缺少 GitHub 地址');
            }

            // 处理 GitHub URL
            let cleanGithubUrl = githubUrl;
            // 移除 .git 后缀（如果存在）
            if (cleanGithubUrl.endsWith('.git')) {
                cleanGithubUrl = cleanGithubUrl.slice(0, -4);
            }

            // 检查是否已安装（重新安装）
            const isReinstall = plugins && Array.isArray(plugins) && plugins.some(p => p.name === plugin.name);

            // 发送安装请求到本地后端
            console.log(`正在向本地后端发送${isReinstall ? '重新' : ''}安装请求...`);
            const response = await fetch('/api/plugin_market/install', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify({
                    plugin_id: plugin.name,
                    plugin_data: {
                        name: plugin.name,
                        description: plugin.description,
                        author: plugin.author,
                        version: plugin.version,
                        github_url: cleanGithubUrl,
                        config: {},
                        requirements: []
                    }
                })
            });

            if (!response.ok) {
                throw new Error(`安装失败: HTTP ${response.status}`);
            }

            const result = await response.json();

            if (result.success) {
                // 更新按钮状态（如果存在）
                if (button) {
                    button.innerHTML = `<i class="bi bi-check-circle-fill me-1"></i>${isReinstall ? '已重新安装' : '已安装'}`;
                    button.classList.remove('btn-primary');
                    button.classList.add('btn-outline-success');
                }

                // 显示成功提示
                showToast(`插件 ${plugin.name} ${isReinstall ? '重新' : ''}安装成功`, 'success');

                // 重置按钮状态并重新显示重新安装按钮（如果存在）
                if (button) {
                    setTimeout(() => {
                        button.innerHTML = `<i class="bi bi-arrow-repeat me-1"></i>重新安装`;
                        button.classList.remove('btn-outline-success');
                        button.classList.add('btn-outline-primary');
                        button.disabled = false;
                    }, 3000);
                }

                // 刷新本地插件列表
                setTimeout(() => {
                    loadPlugins();
                }, 1000);

                // 解决Promise
                resolve(result);
            } else {
                throw new Error(result.error || '安装失败');
            }
        } catch (error) {
            console.error('安装插件失败:', error);

            // 恢复按钮状态（如果存在）
            if (button) {
                button.disabled = false;
                button.innerHTML = originalText;
            }

            // 显示错误提示
            showToast(`安装失败: ${error.message}`, 'danger');

            // 拒绝Promise
            reject(error);
        }
    });
}

// 获取客户端唯一标识
function getBotClientId() {
    let clientId = localStorage.getItem('xybot_client_id');

    if (!clientId) {
        // 生成UUID v4
        clientId = 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
        localStorage.setItem('xybot_client_id', clientId);
    }

    return clientId;
}

// 获取Bot版本信息
function getBotVersion() {
    // 从页面元数据或全局变量获取
    return document.querySelector('meta[name="bot-version"]')?.content || '1.0.0';
}

// 获取平台信息
function getPlatformInfo() {
    return {
        os: navigator.userAgent.includes('Win') ? 'Windows' :
           navigator.userAgent.includes('Mac') ? 'MacOS' :
           navigator.userAgent.includes('Linux') ? 'Linux' : 'Unknown',
        browser: navigator.userAgent
    };
}

// 格式化时间为多久以前
function formatTimeAgo(timestamp) {
    const seconds = Math.floor((Date.now() - timestamp) / 1000);

    const intervals = {
        年: 31536000,
        月: 2592000,
        周: 604800,
        天: 86400,
        小时: 3600,
        分钟: 60,
        秒: 1
    };

    for (const [unit, secondsInUnit] of Object.entries(intervals)) {
        const interval = Math.floor(seconds / secondsInUnit);
        if (interval > 1) {
            return `${interval} ${unit}前`;
        }
    }

    return '刚刚';
}

// 辅助函数：字符串转简单哈希码
function hashCode(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        hash = ((hash << 5) - hash) + str.charCodeAt(i);
        hash = hash & hash; // 转换为32位整数
    }
    return hash;
}

// 初始化上传模态框
function initUploadModal() {
    console.log('初始化上传模态框');
    const uploadModalEl = document.getElementById('upload-plugin-modal');
    console.log('上传模态框元素:', uploadModalEl);

    try {
        // 如果已存在实例，先销毁
        const existingModal = bootstrap.Modal.getInstance(uploadModalEl);
        if (existingModal) {
            existingModal.dispose();
        }

        // 初始化模态窗口
        uploadModal = new bootstrap.Modal(uploadModalEl, {
            backdrop: true,
            keyboard: true
        });

        console.log('上传模态框初始化成功');

        // 为模态窗口添加事件
        uploadModalEl.addEventListener('hidden.bs.modal', function() {
            console.log('上传模态窗口已隐藏，重置表单');
            // 重置表单
            const form = document.getElementById('upload-plugin-form');
            if (form) form.reset();
        });
    } catch (error) {
        console.error('上传模态框初始化失败:', error);
    }

    // 绑定事件
    const uploadButton = document.getElementById('btn-upload-plugin');
    if (uploadButton) {
        // 清除可能存在的旧事件
        const newUploadButton = uploadButton.cloneNode(true);
        uploadButton.parentNode.replaceChild(newUploadButton, uploadButton);

        // 添加新事件
        newUploadButton.addEventListener('click', function(e) {
            console.log('点击提交插件按钮');
            e.preventDefault();

            // 使用Bootstrap API显示模态窗口
            if (uploadModal) {
                uploadModal.show();
            } else {
                console.error('模态窗口实例不存在');
                // 尝试重新初始化
                uploadModal = new bootstrap.Modal(uploadModalEl);
                uploadModal.show();
            }
        });
    } else {
        console.error('找不到上传插件按钮');
    }
}
