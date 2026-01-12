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

        // 监听README按钮点击事件
        document.addEventListener('click', function(event) {
            // 检查是否点击了README按钮
            if (event.target.closest('.btn-readme')) {
                const button = event.target.closest('.btn-readme');
                const pluginId = button.getAttribute('data-plugin-id');

                // 检查是否是DOW框架插件
                if (pluginId && (pluginId.startsWith('dow_plugin_') || pluginId.startsWith('DOW'))) {
                    // 阻止默认事件处理
                    event.preventDefault();

                    // 调用DOW插件README处理函数
                    handleDowPluginReadme(pluginId);
                }
            }

            // 检查是否点击了配置按钮
            if (event.target.closest('.btn-config')) {
                const button = event.target.closest('.btn-config');
                const pluginId = button.getAttribute('data-plugin-id');

                // 检查是否是DOW框架插件
                if (pluginId && (pluginId.startsWith('dow_plugin_') || pluginId.startsWith('DOW'))) {
                    // 阻止默认事件处理
                    event.preventDefault();

                    // 调用DOW插件配置处理函数
                    handleDowPluginConfig(pluginId);
                }
            }
        });

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

                            // 保存DOW插件列表到专用全局变量
                            window.dow_plugins = plugins;
                            console.log("已保存DOW插件列表到全局变量dow_plugins:", window.dow_plugins);

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
                                <input class="form-check-input dow-plugin-toggle" type="checkbox" id="toggle-${plugin.id}" ${plugin.enabled ? 'checked' : ''} data-plugin-id="${plugin.id}">
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

            // 绑定DOW插件开关事件
            const dowToggles = document.querySelectorAll('.dow-plugin-toggle');
            console.log(`找到 ${dowToggles.length} 个DOW插件开关`);

            dowToggles.forEach(toggle => {
                console.log(`为DOW插件开关绑定事件: ${toggle.id}, data-plugin-id=${toggle.getAttribute('data-plugin-id')}`);
                toggle.addEventListener('change', function() {
                    console.log(`DOW插件开关事件触发: ${this.id}`);
                    const pluginId = this.getAttribute('data-plugin-id');
                    console.log(`切换DOW插件: ${pluginId}, 当前状态: ${this.checked}`);
                    toggleDowPlugin(pluginId);
                });
            });
        }

        // 等待页面完全加载后再应用修复
        setTimeout(fixDowFrameworkTabs, 500);

        console.log("DOW框架插件修复脚本加载完成");
    }

    /**
     * 处理DOW框架插件的README
     * @param {string} pluginId 插件ID
     */
    async function handleDowPluginReadme(pluginId) {
        console.log(`处理DOW框架插件README: ${pluginId}`);

        try {
            // 获取README模态框元素
            const modalEl = document.getElementById('plugin-readme-modal');
            if (!modalEl) {
                throw new Error('找不到README模态框元素');
            }

            // 重置状态
            document.getElementById('plugin-readme-loading').style.display = 'block';
            document.getElementById('plugin-readme-error').classList.add('d-none');
            document.getElementById('plugin-readme-content').innerHTML = '';

            // 设置标题
            document.getElementById('plugin-readme-title').textContent = `插件使用说明`;

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
            const response = await fetch(`/api/dow_plugin_readme?plugin_id=${encodeURIComponent(pluginId)}`);
            const data = await response.json();

            // 隐藏加载状态
            document.getElementById('plugin-readme-loading').style.display = 'none';

            if (data.success) {
                // 使用marked将Markdown渲染为HTML
                const readmeHtml = marked.parse(data.readme);
                document.getElementById('plugin-readme-content').innerHTML = readmeHtml;
            } else {
                // 显示错误信息
                const errorEl = document.getElementById('plugin-readme-error');
                errorEl.classList.remove('d-none');
                errorEl.querySelector('span').textContent = data.error || '该插件暂无使用说明';
            }
        } catch (error) {
            console.error(`打开DOW插件README失败: ${error}`);

            // 隐藏加载状态
            document.getElementById('plugin-readme-loading').style.display = 'none';

            // 显示错误信息
            const errorEl = document.getElementById('plugin-readme-error');
            errorEl.classList.remove('d-none');
            errorEl.querySelector('span').textContent = error.message || '该插件暂无使用说明';
        }
    }

    /**
     * 处理DOW框架插件的配置
     * @param {string} pluginId 插件ID
     */
    async function handleDowPluginConfig(pluginId) {
        console.log(`处理DOW框架插件配置: ${pluginId}`);

        try {
            // 获取配置模态框元素
            const modalEl = document.getElementById('plugin-config-modal');
            if (!modalEl) {
                throw new Error('找不到配置模态框元素');
            }

            // 重置表单状态
            document.getElementById('plugin-config-loading').style.display = 'block';
            document.getElementById('plugin-config-error').style.display = 'none';
            document.getElementById('plugin-config-form').innerHTML = '';

            // 设置标题
            document.getElementById('plugin-config-title').textContent = `插件配置文件`;

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
                // 直接获取配置文件内容
                const response = await fetch(`/api/dow_plugin_config_content?plugin_id=${pluginId}`);
                const data = await response.json();

                if (data.success) {
                    // 创建文本编辑器
                    const formContainer = document.getElementById('plugin-config-form');

                    // 如果需要创建配置文件，显示提示信息
                    if (data.needs_creation) {
                        formContainer.innerHTML = `
                            <div class="alert alert-warning mb-3">
                                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                                ${data.message || '未找到配置文件，将创建默认配置文件'}
                            </div>
                            <div class="alert alert-info mb-3">
                                <i class="bi bi-info-circle-fill me-2"></i>
                                将创建配置文件: ${data.config_file}
                            </div>
                            <div class="mb-3">
                                <textarea id="config-editor" class="form-control" style="min-height: 300px; font-family: monospace;">${data.content}</textarea>
                            </div>
                        `;
                    } else {
                        formContainer.innerHTML = `
                            <div class="alert alert-info mb-3">
                                <i class="bi bi-info-circle-fill me-2"></i>
                                正在编辑: ${data.config_file}
                            </div>
                            <div class="mb-3">
                                <textarea id="config-editor" class="form-control" style="min-height: 300px; font-family: monospace;">${data.content}</textarea>
                            </div>
                        `;
                    }

                    // 存储当前配置文件路径，用于保存
                    document.getElementById('plugin-config-save').setAttribute('data-config-file', data.config_file);
                    document.getElementById('plugin-config-save').textContent = '保存';

                    document.getElementById('plugin-config-loading').style.display = 'none';
                } else {
                    throw new Error(data.error || '无法获取配置文件内容');
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

    // 辅助函数：显示提示
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

    /**
     * 切换DOW插件状态
     * @param {string} pluginId 插件ID
     */
    async function toggleDowPlugin(pluginId) {
        console.log(`切换DOW插件状态: ${pluginId}`);

        // 查找插件 - 使用全局dow_plugins变量
        const plugins = window.dow_plugins || [];
        console.log("可用DOW插件列表:", plugins);

        const plugin = plugins.find(p => p.id === pluginId);
        if (!plugin) {
            console.error(`找不到插件: ${pluginId}`);
            showToast(`找不到插件: ${pluginId}`, 'danger');
            return;
        }

        // 获取当前状态
        const action = plugin.enabled ? 'disable' : 'enable';

        // 显示加载状态
        const toggle = document.getElementById(`toggle-${pluginId}`);
        if (toggle) {
            toggle.disabled = true;
        }

        // 发送请求
        console.log(`发送请求到 /api/dow_plugins/${pluginId}/${action}`);
        try {
            const response = await fetch(`/api/dow_plugins/${pluginId}/${action}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            console.log(`API响应状态: ${response.status}`);
            const result = await response.json();
            console.log(`API响应内容:`, result);

            if (result.success) {
                // 更新本地状态
                plugin.enabled = !plugin.enabled;

                // 更新UI
                if (toggle) {
                    toggle.checked = plugin.enabled;
                    toggle.disabled = false;
                }

                // 更新卡片状态
                const card = toggle.closest('.card');
                if (card) {
                    if (plugin.enabled) {
                        card.classList.remove('disabled');
                    } else {
                        card.classList.add('disabled');
                    }
                }

                // 更新状态标签
                const statusBadge = card.querySelector('.status-badge');
                if (statusBadge) {
                    statusBadge.className = `badge bg-${plugin.enabled ? 'success' : 'secondary'} status-badge`;
                    statusBadge.textContent = plugin.enabled ? '已启用' : '已禁用';
                }

                // 更新配置按钮状态
                const configBtn = card.querySelector('.btn-config');
                if (configBtn) {
                    configBtn.disabled = !plugin.enabled;
                }

                // 显示提示
                showToast(`插件已${action === 'enable' ? '启用' : '禁用'}`, 'success');
            } else {
                // 恢复UI状态
                if (toggle) {
                    toggle.checked = plugin.enabled;
                    toggle.disabled = false;
                }

                throw new Error(result.error || `操作失败`);
            }
        } catch (error) {
            console.error('切换DOW插件状态失败:', error);
            showToast(`操作失败: ${error.message}`, 'danger');
        }
    }
})();