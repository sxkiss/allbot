// DOW框架插件修复脚本
(function () {
    console.log("应用DOW框架插件修复脚本...");

    // 确保页面已加载完成
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyDowFix);
    } else {
        applyDowFix();
    }

    function applyDowFix() {
        console.log("开始修复DOW框架插件显示...");

        // 确保初始化时DOW标签有正确的事件绑定
        function fixDowFrameworkTabs() {
            // 找到DOW框架标签
            const dowButton = document.querySelector('[data-framework="dow"]');
            if (!dowButton) {
                console.error("找不到DOW框架按钮");
                return;
            }

            console.log("找到DOW框架按钮:", dowButton);

            // 强制重新绑定DOW标签的点击事件
            const newDowButton = dowButton.cloneNode(true);
            dowButton.parentNode.replaceChild(newDowButton, dowButton);

            // 为新按钮添加点击事件
            newDowButton.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();

                console.log("DOW框架按钮点击事件触发");

                // 更新按钮状态
                document.querySelectorAll('[data-framework]').forEach(btn => {
                    btn.classList.remove('active');
                });
                this.classList.add('active');

                // 强制设置当前框架为DOW
                window.currentFramework = 'dow';

                // 显示加载中状态
                const pluginList = document.getElementById('plugin-list');
                if (pluginList) {
                    pluginList.innerHTML = `
                        <div class="text-center py-5">
                            <div class="spinner-border text-primary" role="status">
                                <span class="visually-hidden">Loading...</span>
                            </div>
                            <p class="mt-3 text-muted">加载DOW框架插件中...</p>
                        </div>
                    `;
                }

                // 直接请求DOW插件API
                console.log("直接请求DOW插件API...");
                fetch('/api/dow_plugins')
                    .then(response => response.json())
                    .then(data => {
                        console.log("DOW插件API响应:", data);
                        if (data.success) {
                            // 获取插件列表
                            const plugins = data.data.plugins || [];
                            console.log(`DOW插件数量: ${plugins.length}`);

                            // 更新插件列表
                            window.plugins = plugins;

                            // 更新插件计数
                            if (document.getElementById('plugin-count')) {
                                document.getElementById('plugin-count').textContent = plugins.length;
                            }

                            // 渲染插件列表
                            if (typeof window.renderPluginList === 'function') {
                                window.renderPluginList(plugins);
                            } else if (typeof window.renderDowPlugins === 'function') {
                                window.renderDowPlugins(plugins);
                            } else {
                                console.error("找不到渲染插件的函数");
                                // 提供一个简单的备用渲染实现
                                renderPluginsBackup(plugins);
                            }
                        } else {
                            console.error("获取DOW插件失败:", data.error);
                            if (pluginList) {
                                pluginList.innerHTML = `
                                    <div class="alert alert-danger text-center">
                                        <i class="bi bi-exclamation-triangle-fill me-2"></i>
                                        加载DOW框架插件失败: ${data.error || '未知错误'}
                                    </div>
                                `;
                            }
                        }
                    })
                    .catch(error => {
                        console.error("请求DOW插件API出错:", error);
                        if (pluginList) {
                            pluginList.innerHTML = `
                                <div class="alert alert-danger text-center">
                                    <i class="bi bi-exclamation-triangle-fill me-2"></i>
                                    请求DOW框架插件时出错: ${error.message || '未知错误'}
                                </div>
                            `;
                        }
                    });
            });

            console.log("已重新绑定DOW框架按钮事件");
        }

        // 备用的插件渲染函数
        function renderPluginsBackup(plugins) {
            console.log("使用备用函数渲染DOW插件");
            const pluginList = document.getElementById('plugin-list');
            if (!pluginList) {
                console.error("找不到插件列表容器");
                return;
            }

            if (!plugins || plugins.length === 0) {
                pluginList.innerHTML = `
                    <div class="alert alert-info text-center">
                        <i class="bi bi-info-circle-fill me-2"></i>
                        未找到DOW框架插件
                    </div>
                `;
                return;
            }

            // 清空容器
            pluginList.innerHTML = '';

            // 为每个插件创建卡片
            plugins.forEach(plugin => {
                const statusClass = plugin.enabled ? 'success' : 'secondary';
                const statusText = plugin.enabled ? '已启用' : '已禁用';

                // 创建卡片元素
                const cardElement = document.createElement('div');
                cardElement.className = 'card h-100 shadow border-0 rounded-4 overflow-hidden mb-4';
                if (!plugin.enabled) {
                    cardElement.classList.add('disabled');
                }

                // 生成卡片HTML
                cardElement.innerHTML = `
                    <div class="card-header p-3 bg-gradient-light border-0 position-relative" style="background: linear-gradient(135deg, #f8f9fa, #e9ecef);">
                        <div class="plugin-status-container">
                            <span class="badge bg-primary me-2" title="来自DOW框架">DOW框架</span>
                            <span class="badge bg-${statusClass} status-badge">${statusText}</span>
                            <div class="form-check form-switch plugin-switch ms-2">
                                <input class="form-check-input plugin-toggle" type="checkbox" id="toggle-${plugin.id}" ${plugin.enabled ? 'checked' : ''} data-plugin-id="${plugin.id}">
                                <label class="form-check-label visually-hidden" for="toggle-${plugin.id}">启用/禁用</label>
                            </div>
                        </div>
                        <div class="d-flex align-items-center">
                            <div class="plugin-icon rounded-circle shadow-sm" style="background: linear-gradient(135deg, #3498db, #2980b9);">
                                <i class="bi bi-puzzle"></i>
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
                                    <button class="btn btn-sm btn-outline-danger rounded-pill btn-delete" data-plugin-id="${plugin.id}">
                                        <i class="bi bi-trash me-1"></i>删除
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                `;

                // 添加到容器
                pluginList.appendChild(cardElement);
            });
        }

        // 等待页面完全加载后再应用修复
        setTimeout(fixDowFrameworkTabs, 500);

        console.log("DOW框架插件修复脚本加载完成");
    }
})(); 