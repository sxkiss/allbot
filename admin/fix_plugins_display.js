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

        // 重新加载当前选择的框架插件
        const activeFrameworkButton = document.querySelector('[data-framework].active');
        if (activeFrameworkButton) {
            const framework = activeFrameworkButton.getAttribute('data-framework');
            console.log(`检测到当前选择的框架: ${framework}`);

            // 强制重新加载插件
            window.plugins = [];
            loadPlugins(framework);
        } else {
            console.log("未检测到活动的框架按钮，使用默认框架");
            window.plugins = [];
            loadPlugins('original');
        }

        // 重写filterPlugins函数，确保正确渲染
        const originalFilterPlugins = window.filterPlugins;

        window.filterPlugins = function (filter) {
            console.log(`应用过滤器: ${filter}, 插件数量: ${plugins.length}`);
            let filteredPlugins = [];

            if (filter === 'all') {
                filteredPlugins = plugins;
            } else if (filter === 'enabled') {
                filteredPlugins = plugins.filter(plugin => plugin.enabled);
            } else if (filter === 'disabled') {
                filteredPlugins = plugins.filter(plugin => !plugin.enabled);
            }

            console.log(`过滤后的插件数量: ${filteredPlugins.length}`);
            renderPluginList(filteredPlugins);

            // 如果没有找到插件，强制尝试一次"all"过滤
            if (filteredPlugins.length === 0 && filter !== 'all' && plugins.length > 0) {
                console.log("未找到插件，尝试显示所有插件");
                renderPluginList(plugins);
            }
        };

        // 确保框架选择按钮工作正常
        const frameworkButtons = document.querySelectorAll('[data-framework]');
        frameworkButtons.forEach(button => {
            button.addEventListener('click', function () {
                const framework = this.getAttribute('data-framework');
                console.log(`切换到框架: ${framework}`);

                // 清空当前插件缓存
                window.plugins = [];

                // 加载新框架的插件
                loadPlugins(framework);

                // 更新按钮状态
                frameworkButtons.forEach(btn => btn.classList.remove('active'));
                this.classList.add('active');
            }, true);
        });

        console.log("插件显示修复完成!");
    }
})(); 