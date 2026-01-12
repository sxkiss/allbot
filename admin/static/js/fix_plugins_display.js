// 修复插件显示问题
(function () {
    console.log("应用插件显示修复脚本...");

    // 检查是否在插件页面
    if (!window.location.pathname.includes('/plugins')) {
        console.log("非插件页面，不应用修复");
        return;
    }

    // 确保页面已加载完成
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fixPluginsDisplay);
    } else {
        fixPluginsDisplay();
    }

    function fixPluginsDisplay() {
        console.log("开始修复插件显示...");

        // 修复loadPlugins函数，增强数据处理能力
        if (typeof window.loadPlugins === 'function') {
            const originalLoadPlugins = window.loadPlugins;

            window.loadPlugins = async function (framework = 'original') {
                console.log(`修复版loadPlugins执行，框架类型: ${framework}`);
                const pluginList = document.getElementById('plugin-list');

                if (!pluginList) {
                    console.error("插件列表容器不存在!");
                    return;
                }

                try {
                    pluginList.innerHTML = `
                        <div class="text-center py-5">
                            <div class="spinner-border text-primary" role="status">
                                <span class="visually-hidden">Loading...</span>
                            </div>
                            <p class="mt-3 text-muted">加载${framework === 'original' ? '原始' : framework === 'dow' ? 'DOW' : '所有'}框架插件中...</p>
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

                    console.log(`请求API端点: ${apiEndpoint}`);
                    const response = await fetch(apiEndpoint);
                    const data = await response.json();
                    console.log(`API响应:`, data);

                    if (data.success) {
                        // 确保plugins是一个数组
                        window.plugins = Array.isArray(data.data.plugins) ? data.data.plugins : [];
                        console.log(`成功加载${framework}插件, 数量: ${window.plugins.length}`);

                        // 如果是DOW框架但未找到插件，显示友好提示
                        if (framework === 'dow' && (!window.plugins || window.plugins.length === 0)) {
                            console.warn("未找到DOW框架插件");
                            pluginList.innerHTML = `
                                <div class="alert alert-info text-center">
                                    <i class="bi bi-info-circle-fill me-2"></i>
                                    未找到DOW框架插件。如果您确定已安装DOW框架插件，请检查配置或联系管理员。
                                </div>
                            `;
                            return;
                        }

                        if (document.getElementById('plugin-count')) {
                            document.getElementById('plugin-count').textContent = window.plugins.length;
                        }

                        // 调用过滤函数显示插件
                        if (typeof window.filterPlugins === 'function') {
                            window.filterPlugins('all');
                        } else {
                            console.error("filterPlugins函数不存在");
                            if (typeof window.renderPluginList === 'function') {
                                window.renderPluginList(window.plugins);
                            }
                        }
                    } else {
                        throw new Error(data.error || `加载${framework}插件失败`);
                    }
                } catch (error) {
                    console.error(`加载${framework}插件列表失败:`, error);
                    pluginList.innerHTML = `
                        <div class="alert alert-danger">
                            <i class="bi bi-exclamation-triangle-fill me-2"></i>
                            加载${framework === 'original' ? '原始' : framework === 'dow' ? 'DOW' : '所有'}框架插件失败: ${error.message}
                        </div>
                    `;
                }
            };
        }

        // 修复渲染插件列表函数
        if (typeof window.renderPluginList === 'function') {
            const originalRenderPluginList = window.renderPluginList;

            window.renderPluginList = function (pluginsList) {
                console.log("修复版renderPluginList执行，插件数量: ", pluginsList ? pluginsList.length : 0);

                const pluginList = document.getElementById('plugin-list');

                if (!pluginList) {
                    console.error("插件列表容器不存在!");
                    return;
                }

                if (!pluginsList || pluginsList.length === 0) {
                    console.warn("插件列表为空，显示提示信息");
                    let frameworkName = "";
                    if (window.currentFramework === 'original') {
                        frameworkName = "原始框架";
                    } else if (window.currentFramework === 'dow') {
                        frameworkName = "DOW框架";
                    } else if (window.currentFramework === 'all') {
                        frameworkName = "所有框架";
                    }

                    pluginList.innerHTML = `
                        <div class="alert alert-info text-center">
                            <i class="bi bi-info-circle-fill me-2"></i>
                            未找到匹配的${frameworkName}插件
                        </div>
                    `;
                    return;
                }

                try {
                    // 调用原始渲染函数
                    originalRenderPluginList(pluginsList);

                    // 检查渲染结果
                    setTimeout(() => {
                        if (pluginList.children.length === 0 ||
                            (pluginList.children.length === 1 && pluginList.querySelector('.alert'))) {
                            console.log("检测到渲染失败，尝试手动重新渲染");
                            manualRenderPlugins(pluginsList, pluginList);
                        }
                    }, 500);
                } catch (error) {
                    console.error("原始渲染函数出错，使用备用渲染: ", error);
                    manualRenderPlugins(pluginsList, pluginList);
                }
            };
        } else {
            console.error("未找到renderPluginList函数!");
        }

        // 手动渲染插件列表的备用实现
        function manualRenderPlugins(pluginsList, container) {
            console.log("执行手动渲染插件列表，数量: ", pluginsList.length);

            // 清空容器
            container.innerHTML = '';

            // 为每个插件创建卡片
            pluginsList.forEach(plugin => {
                // 检查必要的属性
                if (!plugin || !plugin.id) {
                    console.warn("发现无效的插件数据:", plugin);
                    return;
                }

                const statusClass = plugin.enabled ? 'success' : 'secondary';
                const statusText = plugin.enabled ? '已启用' : '已禁用';

                // 获取框架标识
                let frameworkBadge = '';
                if (plugin.framework) {
                    const frameworkName = plugin.framework === 'original' ? '原框架' : 'DOW框架';
                    const badgeColor = plugin.framework === 'original' ? 'info' : 'primary';
                    frameworkBadge = `<span class="badge bg-${badgeColor} me-2" title="来自${frameworkName}">${frameworkName}</span>`;
                }

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
                            ${frameworkBadge}
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
                                    ${plugin.id !== 'ManagePlugin' ? `
                                    <button class="btn btn-sm btn-outline-danger rounded-pill btn-delete" data-plugin-id="${plugin.id}">
                                        <i class="bi bi-trash me-1"></i>删除
                                    </button>` : ''}
                                </div>
                            </div>
                        </div>
                    </div>
                `;

                // 添加到容器
                container.appendChild(cardElement);
            });

            // 绑定事件
            bindEvents(container);
        }

        // 绑定事件处理函数
        function bindEvents(container) {
            // 启用/禁用开关
            container.querySelectorAll('.plugin-toggle').forEach(toggle => {
                toggle.addEventListener('change', function () {
                    const pluginId = this.getAttribute('data-plugin-id');
                    if (typeof window.togglePlugin === 'function') {
                        window.togglePlugin(pluginId);
                    } else {
                        console.error("togglePlugin函数不存在!");
                    }
                });
            });

            // 配置按钮
            container.querySelectorAll('.btn-config').forEach(button => {
                button.addEventListener('click', function () {
                    const pluginId = this.getAttribute('data-plugin-id');
                    if (typeof window.openConfigModal === 'function') {
                        window.openConfigModal(pluginId);
                    } else {
                        console.error("openConfigModal函数不存在!");
                    }
                });
            });

            // 说明按钮
            container.querySelectorAll('.btn-readme').forEach(button => {
                button.addEventListener('click', function () {
                    const pluginId = this.getAttribute('data-plugin-id');
                    if (typeof window.openReadmeModal === 'function') {
                        window.openReadmeModal(pluginId);
                    } else {
                        console.error("openReadmeModal函数不存在!");
                    }
                });
            });

            // 删除按钮
            container.querySelectorAll('.btn-delete').forEach(button => {
                button.addEventListener('click', function () {
                    const pluginId = this.getAttribute('data-plugin-id');
                    if (typeof window.confirmDeletePlugin === 'function') {
                        window.confirmDeletePlugin(pluginId);
                    } else {
                        console.error("confirmDeletePlugin函数不存在!");
                    }
                });
            });
        }

        // 确保框架选择按钮工作正常
        const frameworkButtons = document.querySelectorAll('[data-framework]');
        frameworkButtons.forEach(button => {
            const framework = button.getAttribute('data-framework');
            console.log(`为框架按钮绑定事件: ${framework}`);

            // 首先移除所有原有事件（避免事件重复绑定）
            const newButton = button.cloneNode(true);
            button.parentNode.replaceChild(newButton, button);

            // 为新按钮添加点击事件
            newButton.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();

                console.log(`切换到框架: ${framework} (点击事件触发)`);

                // 更新按钮状态
                document.querySelectorAll('[data-framework]').forEach(btn => {
                    btn.classList.remove('active');
                });
                this.classList.add('active');

                // 强制设置当前框架
                window.currentFramework = framework;

                // 强制清空并重新加载插件
                console.log(`清空插件列表并加载${framework}框架插件...`);
                window.plugins = [];

                // 延迟执行，确保DOM更新
                setTimeout(() => {
                    if (typeof window.loadPlugins === 'function') {
                        window.loadPlugins(framework);
                    } else {
                        console.error("loadPlugins函数不存在!");
                    }
                }, 100);
            });
        });

        // 立即尝试加载当前选中框架的插件
        const activeFrameworkButton = document.querySelector('[data-framework].active');
        if (activeFrameworkButton) {
            const framework = activeFrameworkButton.getAttribute('data-framework');
            console.log(`自动加载当前选择的框架插件: ${framework}`);

            // 使用setTimeout等待原始事件处理完成
            setTimeout(() => {
                if (typeof window.loadPlugins === 'function') {
                    window.loadPlugins(framework);
                }
            }, 500);
        }

        console.log("插件显示修复完成!");
    }
})(); 