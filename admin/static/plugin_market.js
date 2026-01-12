// 全局变量
let marketPlugins = [];
let currentCategory = 'all';
let installedPlugins = []; // 存储已安装的插件信息

// 插件市场API配置（统一走本地同源代理，避免 CORS 和端口暴露问题）
const PLUGIN_MARKET_API = {
    BASE_URL: '',
    LIST: '/api/plugin_market',
    SUBMIT: '/api/plugin_market/submit',
    INSTALL: '/api/plugin_market/install',
    CATEGORIES: '/api/plugin_market/categories',
    CACHE_KEY: 'xybot_plugin_market_cache',
    CACHE_EXPIRY: 3600000 // 缓存有效期1小时（毫秒）
};

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    console.log('插件市场页面加载完成');

    // 先加载分类，再加载本地插件，最后加载市场插件
    loadCategories().then(() => {
        loadLocalPlugins().then(() => {
            console.log('已安装插件数量:', installedPlugins.length);
            // 加载插件市场数据
            loadPluginMarket();
        });
    });

    // 初始化推荐插件卡片的点击事件
    initRecommendedPluginsEvents();

    // 为刷新市场按钮添加点击事件
    const refreshMarketBtn = document.getElementById('btn-refresh-market');
    if (refreshMarketBtn) {
        refreshMarketBtn.addEventListener('click', function() {
            loadPluginMarket(true); // 强制刷新
        });
    }

    // 搜索功能
    const searchInput = document.getElementById('plugin-search-input');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            searchMarketPlugins(this.value);
        });
    }

    // 提交插件按钮点击事件
    const submitBtn = document.getElementById('btn-submit-plugin');
    if (submitBtn) {
        submitBtn.addEventListener('click', function() {
            // 显示模态框
            const modal = new bootstrap.Modal(document.getElementById('submitPluginModal'));
            modal.show();
        });
    }

    // 提交插件表单按钮点击事件
    const submitPluginBtn = document.getElementById('submitPluginBtn');
    if (submitPluginBtn) {
        submitPluginBtn.addEventListener('click', function() {
            submitPlugin();
        });
    }

    // 标签云折叠/展开按钮
    const toggleTagCloudBtn = document.getElementById('toggle-tag-cloud');
    const tagCloudContainer = document.getElementById('tag-cloud-container');

    if (toggleTagCloudBtn && tagCloudContainer) {
        toggleTagCloudBtn.addEventListener('click', function() {
            this.classList.toggle('collapsed');

            if (tagCloudContainer.style.maxHeight) {
                tagCloudContainer.style.maxHeight = null;
                this.querySelector('i').classList.remove('bi-chevron-right');
                this.querySelector('i').classList.add('bi-chevron-down');
            } else {
                tagCloudContainer.style.maxHeight = '0px';
                this.querySelector('i').classList.remove('bi-chevron-down');
                this.querySelector('i').classList.add('bi-chevron-right');
            }
        });
    }
});

// 加载分类列表
async function loadCategories() {
    try {
        console.log('开始加载分类列表...');
        const response = await fetch(PLUGIN_MARKET_API.CATEGORIES);

        if (!response.ok) {
            throw new Error(`获取分类列表失败: ${response.status}`);
        }

        const data = await response.json();

        if (!data.success || !data.categories) {
            console.warn('分类数据格式不正确:', data);
            return;
        }

        // 渲染分类按钮
        renderCategories(data.categories);
        console.log('分类列表加载完成，共', data.categories.length, '个分类');

    } catch (error) {
        console.error('加载分类列表失败:', error);
        // 失败时保持HTML中的默认分类
    }
}

// 渲染分类按钮
function renderCategories(categories) {
    const categoryContainer = document.querySelector('.btn-group[aria-label="插件分类"]');

    if (!categoryContainer) {
        console.error('找不到分类按钮容器');
        return;
    }

    // 清空现有按钮
    categoryContainer.innerHTML = '';

    // 按 sort_order 排序
    categories.sort((a, b) => a.sort_order - b.sort_order);

    // 渲染每个分类按钮
    categories.forEach((category, index) => {
        if (!category.is_active) return; // 跳过未激活的分类

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-outline-primary';
        button.setAttribute('data-category', category.value);

        // 第一个按钮默认激活
        if (index === 0) {
            button.classList.add('active');
            currentCategory = category.value;
        }

        // 添加图标和文本
        button.innerHTML = `<i class="bi ${category.icon} me-1"></i> ${category.label}`;

        // 添加点击事件
        button.addEventListener('click', function() {
            // 移除所有按钮的active类
            categoryContainer.querySelectorAll('button').forEach(btn => btn.classList.remove('active'));
            // 添加当前按钮的active类
            this.classList.add('active');
            // 获取分类
            const categoryValue = this.getAttribute('data-category');
            currentCategory = categoryValue;
            // 过滤插件
            filterMarketPlugins(categoryValue);
        });

        categoryContainer.appendChild(button);
    });

    console.log('分类按钮渲染完成');
}

// 加载本地已安装的插件
async function loadLocalPlugins() {
    try {
        console.log('开始加载本地插件...');
        const response = await fetch('/api/plugins');
        if (!response.ok) {
            throw new Error(`获取本地插件失败: ${response.status}`);
        }

        const data = await response.json();
        console.log('API 返回数据:', data);

        // 检查数据格式
        if (!data.success) {
            console.warn('API 返回失败状态:', data);
            return [];
        }

        // 检查数据结构
        if (!data.data || !data.data.plugins) {
            console.warn('API 返回数据结构不符合预期:', data);
            // 尝试从不同的位置获取插件数据
            if (data.plugins && Array.isArray(data.plugins)) {
                installedPlugins = data.plugins;
            } else {
                installedPlugins = [];
            }
        } else {
            installedPlugins = data.data.plugins;
        }

        console.log('已安装插件数量:', installedPlugins.length);

        // 输出调试信息
        installedPlugins.forEach(plugin => {
            console.log(`已安装: ${plugin.name} v${plugin.version}`);
        });

        return installedPlugins;
    } catch (error) {
        console.error('加载本地插件失败:', error);
        return [];
    }
}

// 检查插件状态
function getPluginStatus(plugin) {
    try {
        // 检查已安装插件数组是否有效
        if (!installedPlugins || !Array.isArray(installedPlugins) || installedPlugins.length === 0) {
            console.warn(`检查插件状态: ${plugin.name} - 没有已安装的插件数据`);
            return { installed: false, hasUpdate: false };
        }

        console.log(`检查插件 ${plugin.name} 是否已安装，已安装插件数量: ${installedPlugins.length}`);

        // 寻找对应的已安装插件 - 主要基于名称匹配
        const installed = installedPlugins.find(p => {
            const nameMatch = p.name && plugin.name &&
                              p.name.toLowerCase() === plugin.name.toLowerCase();

            const urlMatch = p.github_url && plugin.github_url &&
                             p.github_url.toLowerCase() === plugin.github_url.toLowerCase();

            if (nameMatch) {
                console.log(`找到名称匹配: ${p.name} = ${plugin.name}`);
            }

            if (urlMatch) {
                console.log(`找到URL匹配: ${p.github_url} = ${plugin.github_url}`);
            }

            return nameMatch || urlMatch;
        });

        if (!installed) {
            console.log(`插件 ${plugin.name} 未安装`);
            return { installed: false, hasUpdate: false };
        }

        // 检查版本是否有更新
        const hasUpdate = compareVersions(plugin.version, installed.version || '1.0.0') > 0;

        console.log(`插件状态检查: ${plugin.name} - 已安装(${installed.version || '1.0.0'}) - 市场版本(${plugin.version}) - 需要更新: ${hasUpdate}`);

        return {
            installed: true,
            hasUpdate: hasUpdate,
            localVersion: installed.version || '1.0.0'
        };
    } catch (error) {
        console.error('检查插件状态出错:', error, plugin);
        return { installed: false, hasUpdate: false };
    }
}

// 处理标签显示
function processPluginTags(plugin) {
    let tagsHtml = '';
    let tagsArray = [];

    try {
        if (plugin.tags) {
            // 处理各种可能的标签格式
            if (Array.isArray(plugin.tags)) {
                tagsArray = plugin.tags;
            } else if (typeof plugin.tags === 'string') {
                tagsArray = plugin.tags.split(',');
            } else if (typeof plugin.tags === 'object') {
                // 处理可能的对象类型标签
                tagsArray = Object.values(plugin.tags).filter(tag => tag !== null && tag !== undefined);
            }

            // 生成标签HTML
            tagsArray.forEach(tag => {
                if (tag === null || tag === undefined) {
                    return;
                }

                let tagText = '';
                if (typeof tag === 'string') {
                    tagText = tag.trim();
                } else if (typeof tag === 'object' && tag.name) {
                    // 处理 {name: "标签名"} 格式
                    tagText = tag.name;
                } else {
                    // 其他情况尝试转换为字符串
                    tagText = String(tag);
                }

                // 避免显示 [object Object]
                if (tagText && !tagText.includes('[object Object]') && tagText.length > 0) {
                    // 根据标签内容自动分类
                    const tagClass = getTagClass(tagText);
                    tagsHtml += `<span class="plugin-tag ${tagClass}" data-tag="${tagText.toLowerCase()}">${tagText}</span>`;
                }
            });
        }

        // 只有当没有任何标签时，才添加分类标签
        if (!tagsHtml) {
            const categoryName = getCategoryName(plugin.category || 'other');
            const categoryClass = `tag-${(plugin.category || 'other').toLowerCase()}`;
            tagsHtml = `<span class="plugin-tag ${categoryClass}" data-tag="${plugin.category?.toLowerCase() || 'other'}">${categoryName}</span>`;
        }
    } catch (e) {
        console.warn('处理标签出错:', e, plugin);
    }

    return tagsHtml;
}

// 根据标签内容获取对应的CSS类
function getTagClass(tagText) {
    const lowerTag = tagText.toLowerCase();

    // 预定义的标签类别映射
    const tagMappings = {
        'ai': ['ai', '人工智能', '智能', 'gpt', 'chatgpt', 'openai', 'llm', '大模型', '语言模型'],
        'tools': ['工具', 'tool', '实用', 'utility', '功能', '助手', 'helper', '管理', 'manager'],
        'entertainment': ['娱乐', '游戏', 'game', 'fun', '趣味', '音乐', 'music', '视频', 'video', '电影', 'movie']
    };

    // 检查标签是否匹配预定义类别
    for (const [category, keywords] of Object.entries(tagMappings)) {
        if (keywords.some(keyword => lowerTag.includes(keyword))) {
            return `tag-${category}`;
        }
    }

    // 默认返回其他类别
    return 'tag-other';
}

// 加载插件市场数据
async function loadPluginMarket(forceRefresh = false) {
    try {
        // 显示加载中
        document.getElementById('plugin-market-list').innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-5">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                    <p class="mt-3 text-muted">加载插件市场中...</p>
                </td>
            </tr>
        `;

        // 检查缓存
        if (!forceRefresh) {
            const cachedData = loadCachedPluginMarket();
            if (cachedData) {
                console.log('使用缓存的插件市场数据');
                marketPlugins = cachedData;
                renderMarketPlugins(marketPlugins);
                return;
            }
        }

        // 发起API请求
        const response = await fetch(`${PLUGIN_MARKET_API.BASE_URL}${PLUGIN_MARKET_API.LIST}`);
        if (!response.ok) {
            throw new Error(`插件市场请求失败: ${response.status}`);
        }

        const data = await response.json();

        // 保存数据到全局变量
        // 检查数据格式，适配远程API的响应格式
        let plugins = [];
        if (data.plugins) {
            // 新格式，直接使用
            plugins = data.plugins;
        } else if (Array.isArray(data)) {
            // 旧格式，直接是数组
            plugins = data;
        } else {
            console.warn('未知的API响应格式:', data);
            plugins = [];
        }

        marketPlugins = plugins;

        // 数据格式化与验证
        marketPlugins = marketPlugins.map(plugin => {
            // 处理标签格式
            let tags = plugin.tags || [];
            // 如果标签是对象数组（远程API格式），提取name属性
            if (Array.isArray(tags) && tags.length > 0 && typeof tags[0] === 'object' && tags[0].name) {
                tags = tags.map(tag => tag.name);
            }

            // 确保必要字段存在
            return {
                id: plugin.id || generateTempId(plugin),
                name: plugin.name || 'Unknown Plugin',
                version: plugin.version || '1.0.0',
                description: plugin.description || '',
                author: plugin.author || '未知作者',
                tags: tags,
                category: plugin.category || 'other',
                github_url: plugin.github_url || '',
                update_time: plugin.update_time || new Date().toISOString()
            };
        });

        // 缓存数据
        cachePluginMarketData(marketPlugins);

        // 渲染插件列表
        renderMarketPlugins(marketPlugins);

    } catch (error) {
        console.error('加载插件市场失败:', error);

        // 尝试使用缓存数据
        const cachedData = loadCachedPluginMarket();
        if (cachedData) {
            console.log('使用缓存的插件市场数据（请求失败）');
            marketPlugins = cachedData;
            renderMarketPlugins(marketPlugins);
            return;
        }

        // 如果没有缓存，显示错误
        document.getElementById('plugin-market-list').innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-5">
                    <i class="bi bi-exclamation-triangle-fill text-warning" style="font-size: 2rem;"></i>
                    <p class="mt-3 text-muted">无法加载插件市场数据，请检查网络连接后重试。</p>
                    <button class="btn btn-sm btn-outline-primary mt-2" onclick="loadPluginMarket(true)">
                        <i class="bi bi-arrow-clockwise me-1"></i>重试
                    </button>
                </td>
            </tr>
        `;
    }
}

// 生成临时ID
function generateTempId(plugin) {
    return `temp_${plugin.name ? plugin.name.replace(/\s+/g, '_').toLowerCase() : 'plugin'}_${Math.random().toString(36).substring(2, 9)}`;
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
    const cachedData = localStorage.getItem(PLUGIN_MARKET_API.CACHE_KEY);
    if (!cachedData) return null;

    try {
        const cache = JSON.parse(cachedData);
        const now = Date.now();

        // 检查缓存是否过期
        if (now - cache.timestamp > PLUGIN_MARKET_API.CACHE_EXPIRY) {
            console.log('插件市场缓存已过期');
            return null;
        }

        return cache.plugins;
    } catch (error) {
        console.error('解析插件市场缓存失败:', error);
        return null;
    }
}

// 生成标签云
function generateTagCloud() {
    const tagCloudContainer = document.getElementById('tag-cloud-container');
    if (!tagCloudContainer) return;

    // 清空容器
    tagCloudContainer.innerHTML = '';

    // 如果没有插件数据，显示提示
    if (!marketPlugins || !Array.isArray(marketPlugins) || marketPlugins.length === 0) {
        tagCloudContainer.innerHTML = '<div class="text-center py-2"><span class="text-muted">暂无标签数据</span></div>';
        return;
    }

    // 收集所有标签
    const tagFrequency = {};
    const predefinedCategories = ['ai', 'tools', 'entertainment', 'adapter', 'other'];

    // 添加预定义分类
    predefinedCategories.forEach(category => {
        const categoryName = getCategoryName(category);
        tagFrequency[categoryName] = {
            text: categoryName,
            category: category,
            count: 0,
            isPredefined: true
        };
    });

    // 收集插件标签
    marketPlugins.forEach(plugin => {
        // 计数分类
        const category = plugin.category || 'other';
        const categoryName = getCategoryName(category);
        if (tagFrequency[categoryName]) {
            tagFrequency[categoryName].count++;
        }

        // 处理插件标签
        if (plugin.tags) {
            let tagsArray = [];

            if (Array.isArray(plugin.tags)) {
                tagsArray = plugin.tags;
            } else if (typeof plugin.tags === 'string') {
                tagsArray = plugin.tags.split(',');
            } else if (typeof plugin.tags === 'object') {
                tagsArray = Object.values(plugin.tags).filter(tag => tag !== null && tag !== undefined);
            }

            tagsArray.forEach(tag => {
                let tagText = '';
                if (typeof tag === 'string') {
                    tagText = tag.trim();
                } else if (typeof tag === 'object' && tag.name) {
                    tagText = tag.name;
                } else {
                    tagText = String(tag);
                }

                if (tagText && !tagText.includes('[object Object]') && tagText.length > 0) {
                    if (!tagFrequency[tagText]) {
                        tagFrequency[tagText] = {
                            text: tagText,
                            count: 0,
                            category: getTagClass(tagText).replace('tag-', '')
                        };
                    }
                    tagFrequency[tagText].count++;
                }
            });
        }
    });

    // 转换为数组并排序
    const sortedTags = Object.values(tagFrequency)
        .sort((a, b) => {
            // 预定义分类排在前面
            if (a.isPredefined && !b.isPredefined) return -1;
            if (!a.isPredefined && b.isPredefined) return 1;
            // 然后按计数排序
            return b.count - a.count;
        });

    // 生成标签HTML
    sortedTags.forEach(tag => {
        if (tag.count > 0 || tag.isPredefined) {
            const tagElement = document.createElement('span');
            tagElement.className = `plugin-tag tag-${tag.category}`;
            tagElement.setAttribute('data-tag', tag.text.toLowerCase());
            tagElement.textContent = tag.isPredefined ? tag.text : `${tag.text} (${tag.count})`;
            tagElement.style.margin = '0.25rem';

            // 添加点击事件
            tagElement.addEventListener('click', function() {
                const tagText = this.getAttribute('data-tag');
                if (tag.isPredefined) {
                    filterMarketPlugins(tag.category);
                } else {
                    filterPluginsByTag(tagText);
                }
            });

            // 添加动画效果
            tagElement.style.opacity = '0';
            tagElement.style.transform = 'scale(0.8)';
            tagElement.style.transition = 'all 0.3s ease';

            tagCloudContainer.appendChild(tagElement);

            // 错开时间添加动画
            setTimeout(() => {
                tagElement.style.opacity = '1';
                tagElement.style.transform = 'scale(1)';
            }, Math.random() * 500); // 随机延迟，使标签出现更自然
        }
    });

    // 添加整体容器的动画
    animateElement(tagCloudContainer.parentElement, 'fadeIn', 500);
}

// 添加动画效果
function animateElement(element, animation, duration = 300) {
    return new Promise((resolve) => {
        if (!element) {
            resolve();
            return;
        }

        element.style.animation = `${animation} ${duration}ms ease`;

        const onAnimationEnd = () => {
            element.style.animation = '';
            element.removeEventListener('animationend', onAnimationEnd);
            resolve();
        };

        element.addEventListener('animationend', onAnimationEnd);
    });
}

// 添加CSS动画
function addAnimationStyles() {
    if (document.getElementById('plugin-market-animations')) {
        return;
    }

    const styleEl = document.createElement('style');
    styleEl.id = 'plugin-market-animations';
    styleEl.textContent = `
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes fadeInDown {
            from {
                opacity: 0;
                transform: translateY(-20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes scaleIn {
            from {
                opacity: 0;
                transform: scale(0.9);
            }
            to {
                opacity: 1;
                transform: scale(1);
            }
        }

        @keyframes slideInRight {
            from {
                opacity: 0;
                transform: translateX(30px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }

        .staggered-item {
            opacity: 0;
        }

        .staggered-item.animated {
            animation: fadeInUp 0.5s ease forwards;
        }
    `;

    document.head.appendChild(styleEl);
}

// 初始化推荐插件卡片的点击事件
function initRecommendedPluginsEvents() {
    // 为推荐插件卡片中的安装按钮添加点击事件
    const recommendedButtons = document.querySelectorAll('#recommended-plugins .btn-gold');
    recommendedButtons.forEach(button => {
        button.addEventListener('click', function() {
            // 获取插件名称
            const card = this.closest('.plugin-card');
            const pluginName = card.querySelector('.plugin-card-title').textContent;

            // 查找对应的插件数据
            const plugin = marketPlugins.find(p => p.name === pluginName);

            if (plugin) {
                // 获取插件状态
                const status = getPluginStatus(plugin);
                // 安装插件
                installPlugin(plugin, status);
            } else {
                // 如果在市场数据中找不到，使用卡片中的数据
                const version = card.querySelector('.plugin-card-version').textContent.replace('v', '');
                const description = card.querySelector('.plugin-card-description').textContent;
                const author = card.querySelector('.plugin-card-author').textContent.replace('来自: ', '');

                // 创建临时插件对象
                const tempPlugin = {
                    id: generateTempId({name: pluginName}),
                    name: pluginName,
                    version: version,
                    description: description,
                    author: author,
                    github_url: "https://github.com/sxkiss/allbot",
                    tags: []
                };

                // 安装插件
                installPlugin(tempPlugin, {installed: false, hasUpdate: false});
            }
        });
    });

    // 为推荐插件卡片中的文档按钮添加点击事件
    const docButtons = document.querySelectorAll('#recommended-plugins .btn-outline-info');
    docButtons.forEach(button => {
        button.addEventListener('click', function() {
            // 获取插件名称
            const card = this.closest('.plugin-card');
            const pluginName = card.querySelector('.plugin-card-title').textContent;

            // 查找对应的插件数据
            const plugin = marketPlugins.find(p => p.name === pluginName);

            if (plugin && plugin.github_url) {
                // 打开GitHub链接
                window.open(plugin.github_url, '_blank');
            } else {
                // 如果在市场数据中找不到，使用默认GitHub链接
                const defaultUrl = "https://github.com/sxkiss/allbot";
                window.open(defaultUrl, '_blank');
                showToast('使用默认文档链接，可能不准确', 'warning');
            }
        });
    });
}

// 随机选择推荐插件
function updateRecommendedPlugins(plugins) {
    if (!plugins || !Array.isArray(plugins) || plugins.length === 0) {
        return;
    }

    const recommendedContainer = document.getElementById('recommended-plugins');
    if (!recommendedContainer) {
        return;
    }

    // 清空现有的推荐插件
    const existingCards = recommendedContainer.querySelectorAll('.col-md-4');
    if (existingCards.length === 0) {
        // 如果没有现有卡片，说明使用的是静态HTML，保留它们
        return;
    }

    // 清空容器
    recommendedContainer.innerHTML = '';

    // 随机选择3个插件
    const shuffled = [...plugins].sort(() => 0.5 - Math.random());
    const selected = shuffled.slice(0, 3);

    // 渲染推荐插件卡片
    selected.forEach(plugin => {
        // 处理标签
        let tagsHtml = '';
        if (plugin.tags) {
            let tagsArray = [];

            if (Array.isArray(plugin.tags)) {
                tagsArray = plugin.tags;
            } else if (typeof plugin.tags === 'string') {
                tagsArray = plugin.tags.split(',');
            } else if (typeof plugin.tags === 'object') {
                tagsArray = Object.values(plugin.tags).filter(tag => tag !== null && tag !== undefined);
            }

            tagsArray.slice(0, 2).forEach(tag => {
                let tagText = '';
                if (typeof tag === 'string') {
                    tagText = tag.trim();
                } else if (typeof tag === 'object' && tag.name) {
                    tagText = tag.name;
                } else {
                    tagText = String(tag);
                }

                if (tagText && !tagText.includes('[object Object]') && tagText.length > 0) {
                    tagsHtml += `<span class="plugin-card-tag">${tagText}</span>`;
                }
            });
        }

        // 只有当没有任何标签时，才添加分类标签
        if (!tagsHtml) {
            tagsHtml = `<span class="plugin-card-tag">${getCategoryName(plugin.category || 'other')}</span>`;
        }

        // 处理描述文本，确保不会太长
        let description = plugin.description || '暂无描述';
        if (description.length > 80) {
            description = description.substring(0, 77) + '...';
        }

        // 处理作者名称，确保不会太长
        let author = plugin.author || '未知作者';
        if (author.length > 12) {
            author = author.substring(0, 9) + '...';
        }

        // 处理插件名称，确保不会太长
        let name = plugin.name || 'Unknown Plugin';
        if (name.length > 18) {
            name = name.substring(0, 15) + '...';
        }

        // 创建卡片HTML
        const cardHtml = `
            <div class="col-md-4 mb-4">
                <div class="plugin-card position-relative">
                    <span class="recommended-badge">推荐</span>
                    <div class="plugin-card-header">
                        <h5 class="plugin-card-title" title="${plugin.name}">${name}</h5>
                        <span class="plugin-card-version">v${plugin.version}</span>
                    </div>
                    <div class="plugin-card-body">
                        <p class="plugin-card-description" title="${plugin.description}">${description}</p>
                        <div class="plugin-card-tags">
                            ${tagsHtml}
                        </div>
                    </div>
                    <div class="plugin-card-footer">
                        <span class="plugin-card-author" title="来自: ${plugin.author}">来自: ${author}</span>
                        <div class="plugin-actions">
                            <button class="btn btn-sm btn-gold">
                                <i class="bi bi-download me-1"></i>安装
                            </button>
                            <button class="btn btn-sm btn-outline-info">
                                <i class="bi bi-file-text"></i> 文档
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // 添加到容器
        recommendedContainer.innerHTML += cardHtml;
    });

    // 重新初始化推荐插件卡片的点击事件
    initRecommendedPluginsEvents();
}

// 渲染插件市场列表
function renderMarketPlugins(plugins) {
    try {
        // 添加动画样式
        addAnimationStyles();

        const container = document.getElementById('plugin-market-list');
        if (!container) {
            console.error('找不到插件列表容器元素');
            return;
        }

        // 更新推荐插件
        updateRecommendedPlugins(plugins);

        // 生成标签云
        generateTagCloud();

        // 计算已安装和可更新的插件数量
        let installedCount = 0;
        let updateCount = 0;

        plugins.forEach(plugin => {
            const status = getPluginStatus(plugin);
            if (status.installed) {
                installedCount++;
                if (status.hasUpdate) {
                    updateCount++;
                }
            }
        });

        console.log(`插件统计: 总数=${plugins.length}, 已安装=${installedCount}, 可更新=${updateCount}`);

        // 更新插件数量
        const countElement = document.getElementById('plugin-count');
        const installedCountElement = document.getElementById('installed-count');
        const updatesCountElement = document.getElementById('updates-count');

        if (countElement) {
            countElement.textContent = plugins.length;
        }

        if (installedCountElement) {
            installedCountElement.textContent = installedCount;
            // 如果没有已安装插件，隐藏标签
            installedCountElement.style.display = installedCount > 0 ? 'inline-flex' : 'none';
        }

        if (updatesCountElement) {
            updatesCountElement.textContent = updateCount;
            // 如果没有可更新插件，隐藏标签
            updatesCountElement.style.display = updateCount > 0 ? 'inline-flex' : 'none';
        }

        // 添加或更新已安装插件数量提示
        const existingAlert = document.querySelector('.alert.alert-info.installed-count');
        if (existingAlert) {
            existingAlert.innerHTML = `<i class="bi bi-info-circle-fill me-2"></i>已安装插件数量: ${installedCount}`;
        } else {
            const installedCountElement = document.createElement('div');
            installedCountElement.className = 'alert alert-info mt-3 installed-count';
            installedCountElement.innerHTML = `<i class="bi bi-info-circle-fill me-2"></i>已安装插件数量: ${installedCount}`;

            // 将元素添加到页面上
            const tableParent = container.parentNode;
            if (tableParent) {
                tableParent.insertAdjacentElement('beforebegin', installedCountElement);
            }
        }

        // 如果没有插件
        if (!plugins || !Array.isArray(plugins) || plugins.length === 0) {
            container.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-5">
                        <i class="bi bi-emoji-frown text-muted" style="font-size: 2rem;"></i>
                        <p class="mt-3 text-muted">暂无可用插件</p>
                    </td>
                </tr>
            `;
            return;
        }

        // 清空容器
        container.innerHTML = '';

        // 获取当前日期，用于格式化更新时间
        const now = new Date();
        const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit'
        });

        // 渲染每个插件行
        plugins.forEach((plugin, index) => {
            try {
                if (!plugin || typeof plugin !== 'object') {
                    console.warn('跳过无效的插件数据:', plugin);
                    return;
                }

                // 更新日期格式化
                let updateDateStr = '未知';
                try {
                    const updateDate = plugin.update_time ? new Date(plugin.update_time) : now;
                    updateDateStr = isNaN(updateDate.getTime()) ? '未知' : dateFormatter.format(updateDate);
                } catch (e) {
                    console.warn('日期格式化错误:', e);
                }

                // 确定插件状态
                const status = getPluginStatus(plugin);

                // 创建插件行
                const row = document.createElement('tr');

                // 如果插件有更新，添加高亮类
                if (status.installed && status.hasUpdate) {
                    row.classList.add('has-update');
                }

                // 处理标签显示
                const tagsHtml = processPluginTags(plugin);

                // 安全获取插件属性
                const name = plugin.name || 'Unknown Plugin';
                const version = plugin.version || '1.0.0';
                const description = plugin.description || '暂无描述';
                const author = plugin.author || '未知作者';

                // 创建状态标识
                let statusBadges = '';
                if (status.installed) {
                    statusBadges += `<span class="badge bg-success me-1" title="已安装版本: v${status.localVersion}">已安装</span>`;
                    if (status.hasUpdate) {
                        statusBadges += `<span class="badge bg-warning me-1" title="可更新到: v${version}">可更新</span>`;
                    }
                }

                row.innerHTML = `
                    <td>
                        <div class="plugin-name d-flex align-items-center">
                            ${name}
                            ${statusBadges ? `<div class="ms-2">${statusBadges}</div>` : ''}
                        </div>
                    </td>
                    <td>
                        <div class="plugin-description">${description}</div>
                    </td>
                    <td>${author}</td>
                    <td>v${version}${status.installed ? `<br><small class="text-muted">(已安装: v${status.localVersion})</small>` : ''}</td>
                    <td>${updateDateStr}</td>
                    <td>
                        ${tagsHtml}
                    </td>
                    <td>
                        <div class="plugin-actions">
                            ${status.installed ?
                                (status.hasUpdate ?
                                    `<button class="btn-update" data-plugin-id="${plugin.id}" title="从 v${status.localVersion} 更新到 v${version}">
                                        <i class="bi bi-arrow-repeat"></i> 更新
                                    </button>` :
                                    `<button class="btn-install" data-plugin-id="${plugin.id}" title="重新安装 v${status.localVersion}">
                                        <i class="bi bi-arrow-repeat"></i> 重新安装
                                    </button>`) :
                                `<button class="btn-install" data-plugin-id="${plugin.id}">
                                    <i class="bi bi-download"></i> 安装
                                </button>`
                            }
                            <button class="btn-document" data-plugin-id="${plugin.id}">
                                <i class="bi bi-file-text"></i> 文档
                            </button>
                        </div>
                    </td>
                `;

                // 添加安装/更新按钮点击事件
                const actionBtn = row.querySelector('button[data-plugin-id]');
                if (actionBtn) {
                    actionBtn.addEventListener('click', function() {
                        installPlugin(plugin, status);
                    });
                }

                // 添加文档按钮点击事件
                const docBtn = row.querySelector('.btn-document');
                if (docBtn) {
                    docBtn.addEventListener('click', function() {
                        // 如果有github_url，打开github链接
                        if (plugin.github_url) {
                            window.open(plugin.github_url, '_blank');
                        } else {
                            showToast('此插件未提供文档', 'warning');
                        }
                    });
                }

                // 添加标签点击事件
                const tagElements = row.querySelectorAll('.plugin-tag');
                tagElements.forEach(tagElement => {
                    tagElement.addEventListener('click', function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        const tagText = this.textContent.trim();
                        const tagData = this.getAttribute('data-tag');
                        filterPluginsByTag(tagData || tagText);
                    });
                });

                // 添加动画类
                row.classList.add('staggered-item');

                // 添加到容器
                container.appendChild(row);

                // 延迟添加动画效果，实现错落有致的动画
                setTimeout(() => {
                    row.classList.add('animated');
                }, index * 50); // 每行错开50ms
            } catch (err) {
                console.error('渲染插件行出错:', err, plugin);
            }
        });
    } catch (error) {
        console.error('渲染插件市场列表出错:', error);
        const container = document.getElementById('plugin-market-list');
        if (container) {
            container.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-5">
                        <i class="bi bi-exclamation-triangle-fill text-danger" style="font-size: 2rem;"></i>
                        <p class="mt-3 text-muted">渲染插件列表时发生错误</p>
                        <button class="btn btn-sm btn-outline-primary mt-2" onclick="loadPluginMarket(true)">
                            <i class="bi bi-arrow-clockwise me-1"></i>重试
                        </button>
                    </td>
                </tr>
            `;
        }
    }
}

// 按分类过滤插件
function filterMarketPlugins(category) {
    // 更新分类按钮状态
    const categoryButtons = document.querySelectorAll('.btn-group[aria-label="插件分类"] button');
    categoryButtons.forEach(btn => {
        if (btn.getAttribute('data-category') === category) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    // 保存当前分类
    currentCategory = category;

    if (category === 'all') {
        renderMarketPlugins(marketPlugins);
    } else {
        const filtered = marketPlugins.filter(plugin => {
            // 检查插件分类
            if ((plugin.category || 'other').toLowerCase() === category.toLowerCase()) {
                return true;
            }

            // 检查插件标签
            if (plugin.tags) {
                let tagsArray = [];

                if (Array.isArray(plugin.tags)) {
                    tagsArray = plugin.tags;
                } else if (typeof plugin.tags === 'string') {
                    tagsArray = plugin.tags.split(',');
                } else if (typeof plugin.tags === 'object') {
                    tagsArray = Object.values(plugin.tags).filter(tag => tag !== null && tag !== undefined);
                }

                // 检查标签是否匹配当前分类
                return tagsArray.some(tag => {
                    const tagText = typeof tag === 'string' ? tag.trim().toLowerCase() :
                                  (typeof tag === 'object' && tag.name ? tag.name.toLowerCase() : '');

                    // 检查标签是否直接匹配分类
                    if (tagText === category.toLowerCase() ||
                        tagText === getCategoryName(category).toLowerCase()) {
                        return true;
                    }

                    // 使用标签映射检查
                    const tagMappings = {
                        'ai': ['ai', '人工智能', '智能', 'gpt', 'chatgpt', 'openai', 'llm', '大模型', '语言模型'],
                        'tools': ['工具', 'tool', '实用', 'utility', '功能', '助手', 'helper', '管理', 'manager'],
                        'entertainment': ['娱乐', '游戏', 'game', 'fun', '趣味', '音乐', 'music', '视频', 'video', '电影', 'movie']
                    };

                    const keywords = tagMappings[category.toLowerCase()] || [];
                    return keywords.some(keyword => tagText.includes(keyword));
                });
            }

            return false;
        });

        renderMarketPlugins(filtered);
    }
}

// 按标签过滤插件
function filterPluginsByTag(tag) {
    if (!tag) {
        filterMarketPlugins(currentCategory);
        return;
    }

    const tagLower = tag.toLowerCase();

    // 检查标签是否匹配预定义分类
    const categoryMapping = {
        'ai': ['ai', '人工智能', '智能', 'gpt', 'chatgpt', 'openai', 'llm', '大模型', '语言模型'],
        'tools': ['工具', 'tool', '实用', 'utility', '功能', '助手', 'helper', '管理', 'manager'],
        'entertainment': ['娱乐', '游戏', 'game', 'fun', '趣味', '音乐', 'music', '视频', 'video', '电影', 'movie']
    };

    for (const [category, keywords] of Object.entries(categoryMapping)) {
        if (keywords.includes(tagLower) || category === tagLower) {
            filterMarketPlugins(category);
            return;
        }
    }

    // 如果不是预定义分类，按标签文本过滤
    const filtered = marketPlugins.filter(plugin => {
        // 检查插件标签
        if (plugin.tags) {
            let tagsArray = [];

            if (Array.isArray(plugin.tags)) {
                tagsArray = plugin.tags;
            } else if (typeof plugin.tags === 'string') {
                tagsArray = plugin.tags.split(',');
            } else if (typeof plugin.tags === 'object') {
                tagsArray = Object.values(plugin.tags).filter(t => t !== null && t !== undefined);
            }

            return tagsArray.some(t => {
                const tagText = typeof t === 'string' ? t.trim().toLowerCase() :
                              (typeof t === 'object' && t.name ? t.name.toLowerCase() : '');
                return tagText === tagLower || tagText.includes(tagLower);
            });
        }

        return false;
    });

    // 更新UI状态但不更改当前分类
    const categoryButtons = document.querySelectorAll('.btn-group[aria-label="插件分类"] button');
    categoryButtons.forEach(btn => btn.classList.remove('active'));

    // 显示过滤结果
    renderMarketPlugins(filtered);

    // 显示过滤提示
    showToast(`已按标签 "${tag}" 过滤插件`, 'info');
}

// 搜索插件
function searchMarketPlugins(keyword) {
    if (!keyword || keyword.trim() === '') {
        filterMarketPlugins(currentCategory);
        return;
    }

    keyword = keyword.toLowerCase().trim();

    const filtered = marketPlugins.filter(plugin => {
        return (
            (plugin.name && plugin.name.toLowerCase().includes(keyword)) ||
            (plugin.description && plugin.description.toLowerCase().includes(keyword)) ||
            (plugin.author && plugin.author.toLowerCase().includes(keyword))
        );
    });

    renderMarketPlugins(filtered);
}

// 安装插件
async function installPlugin(plugin, status) {
    return new Promise(async (resolve, reject) => {
        try {
            const isUpdate = status && status.installed && status.hasUpdate;
            const isReinstall = status && status.installed && !status.hasUpdate;
            const actionType = isUpdate ? '更新' : (isReinstall ? '重新安装' : '安装');

            const confirmMsg = isUpdate
                ? `确定要将插件 "${plugin.name}" 从 v${status.localVersion} 更新到 v${plugin.version} 吗？`
                : (isReinstall
                    ? `确定要重新安装插件 "${plugin.name}" 吗？`
                    : `确定要安装插件 "${plugin.name}" 吗？`);

            if (!confirm(confirmMsg)) {
                return;
            }

            const installBtn = document.querySelector(`button[data-plugin-id="${plugin.id}"]`);

            if (installBtn) {
                // 禁用按钮并显示加载状态
                installBtn.disabled = true;
                installBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${actionType}中...`;
            }

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

            console.log(`正在向本地后端发送${actionType}请求...`);

            // 发送安装请求到本地后端（与原本插件市场保持一致）
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
                        description: plugin.description || '',
                        author: plugin.author || '未知作者',
                        version: plugin.version || '1.0.0',
                        github_url: cleanGithubUrl,
                        type: plugin.category === 'adapter' ? 'adapter' : 'plugin',
                        config: {},
                        requirements: []
                    }
                })
            });

            if (!response.ok) {
                throw new Error(`${actionType}失败: HTTP ${response.status}`);
            }

            const result = await response.json();

            if (result.success) {
                // 更新按钮状态
                if (installBtn) {
                    installBtn.innerHTML = `<i class="bi bi-check-circle-fill me-1"></i>${actionType}成功`;
                    installBtn.classList.remove('btn-update', 'btn-install');
                    installBtn.classList.add('btn-success');
                }

                // 显示成功提示
                showToast(`插件 ${plugin.name} ${actionType}成功`, 'success');

                // 重新加载本地插件和市场数据
                await loadLocalPlugins();

                // 延迟刷新市场数据，确保本地插件已加载完成
                setTimeout(() => {
                    loadPluginMarket(true);
                }, 1000);

                // 提示用户刷新插件管理页面查看新安装的插件
                setTimeout(() => {
                    const goToPlugins = confirm(`插件${actionType}成功！是否前往插件管理页面查看？`);
                    if (goToPlugins) {
                        window.location.href = '/plugins';
                    } else {
                        // 恢复按钮状态
                        if (installBtn) {
                            if (isUpdate || isReinstall) {
                                installBtn.innerHTML = `<i class="bi bi-arrow-repeat me-1"></i>${isUpdate ? '更新' : '重新安装'}`;
                                installBtn.classList.remove('btn-success');
                                installBtn.classList.add('btn-update');
                            } else {
                                installBtn.innerHTML = `<i class="bi bi-download me-1"></i>安装`;
                                installBtn.classList.remove('btn-success');
                                installBtn.classList.add('btn-install');
                            }
                            installBtn.disabled = false;
                        }
                    }
                }, 1500);

                // 解决Promise
                resolve(result);
            } else {
                throw new Error(result.error || `${actionType}失败`);
            }
        } catch (error) {
            console.error('安装插件失败:', error);

            // 恢复按钮状态
            if (installBtn) {
                installBtn.disabled = false;
                installBtn.innerHTML = originalBtnText || (status && status.hasUpdate
                    ? '<i class="bi bi-arrow-repeat me-1"></i>更新'
                    : (status && status.installed
                        ? '<i class="bi bi-arrow-repeat me-1"></i>重新安装'
                        : '<i class="bi bi-download me-1"></i>安装'));
            }

            // 显示错误提示
            showToast(`安装失败: ${error.message}`, 'error');

            // 拒绝Promise
            reject(error);
        }
    });
}

// 比较版本号
function compareVersions(version1, version2) {
    if (!version1) return -1;
    if (!version2) return 1;

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

// 获取分类名称
function getCategoryName(category) {
    const categories = {
        'tools': '工具',
        'ai': 'AI',
        'entertainment': '娱乐',
        'adapter': '适配器',
        'other': '其他'
    };
    return categories[category.toLowerCase()] || '其他';
}

// 显示提示消息
function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        // 创建toast容器
        const container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'position-fixed bottom-0 end-0 p-3';
        container.style.zIndex = '1050';
        document.body.appendChild(container);
    }

    // 创建toast元素
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${getToastBackground(type)} border-0`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');

    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <i class="bi ${getToastIcon(type)} me-2"></i>
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;

    // 添加到容器
    document.getElementById('toast-container').appendChild(toast);

    // 初始化toast
    const bsToast = new bootstrap.Toast(toast, {
        animation: true,
        autohide: true,
        delay: 3000
    });

    // 显示toast
    bsToast.show();

    // 监听关闭事件，移除DOM元素
    toast.addEventListener('hidden.bs.toast', function () {
        toast.remove();
    });
}

// 获取Toast背景色
function getToastBackground(type) {
    const backgrounds = {
        'success': 'success',
        'error': 'danger',
        'warning': 'warning',
        'info': 'primary'
    };
    return backgrounds[type] || 'primary';
}

// 获取Toast图标
function getToastIcon(type) {
    const icons = {
        'success': 'bi-check-circle-fill',
        'error': 'bi-exclamation-triangle-fill',
        'warning': 'bi-exclamation-circle-fill',
        'info': 'bi-info-circle-fill'
    };
    return icons[type] || 'bi-info-circle-fill';
}

// 提交插件到市场
async function submitPlugin() {
    console.log('==================== 开始提交流程 ====================');
    console.log('提交审核按钮被点击');
    const submitBtn = document.getElementById('submitPluginBtn');

    // 添加加载状态
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>提交中...`;
    submitBtn.disabled = true;

    try {
        const form = document.getElementById('submitPluginForm');

        // 验证表单
        if (!validatePluginForm()) {
            console.log('表单验证失败');
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
            return;
        }

        console.log('表单验证通过，准备提交');

        // 获取表单数据
        const formData = new FormData(form);

        // 转换为JSON对象，与老插件市场使用相同的方法
        const pluginData = {
            name: formData.get('pluginName'),
            description: formData.get('pluginDescription'),
            author: formData.get('pluginAuthor'),
            version: formData.get('pluginVersion'),
            github_url: formData.get('pluginGithubUrl'),
            tags: formData.get('pluginTags') ? formData.get('pluginTags').split(',').map(tag => tag.trim()) : [],
            requirements: formData.get('pluginRequirements') ? formData.get('pluginRequirements').split('\n').map(req => req.trim()).filter(req => req) : [],
            icon: null // 图标将作为Base64处理
        };

        // 处理图标文件
        const iconFile = formData.get('pluginIcon');
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
            credentials: 'include',  // 包含凭证，确保会话Cookie被发送
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
            const modalEl = document.getElementById('submitPluginModal');
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
            setTimeout(() => loadPluginMarket(true), 1000);
        } else {
            throw new Error(responseData?.error || '提交失败');
        }
    } catch (error) {
        console.error('提交插件失败:', error);

        // 显示错误提示
        showToast(`提交失败: ${error.message}`, 'error');
    } finally {
        // 恢复按钮状态
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
    }
}

// 验证插件表单
function validatePluginForm() {
    console.log('开始验证表单字段...');

    // 基本字段验证
    const requiredFields = [
        { id: 'pluginName', label: '插件名称' },
        { id: 'pluginDescription', label: '插件描述' },
        { id: 'pluginAuthor', label: '作者' },
        { id: 'pluginVersion', label: '版本' },
        { id: 'pluginGithubUrl', label: 'GitHub 仓库地址' }
    ];

    for (const field of requiredFields) {
        const input = document.getElementById(field.id);
        if (!input || !input.value.trim()) {
            showToast(`${field.label}不能为空`, 'error');
            return false;
        }
    }

    // 版本格式验证
    const versionInput = document.getElementById('pluginVersion');
    if (versionInput) {
        const version = versionInput.value.trim();
        const versionPattern = /^\d+(\.\d+)*$/;  // 例如: 1.0.0, 2.1, 1
        if (!versionPattern.test(version)) {
            showToast('版本格式不正确，应为数字和点组成，如: 1.0.0', 'error');
            return false;
        }
    }

    // GitHub URL验证
    const githubUrlInput = document.getElementById('pluginGithubUrl');
    const githubUrl = githubUrlInput ? githubUrlInput.value.trim() : '';

    if (!githubUrl.startsWith('https://github.com/') && !githubUrl.startsWith('https://raw.githubusercontent.com/')) {
        showToast('GitHub链接必须以 https://github.com/ 或 https://raw.githubusercontent.com/ 开头', 'error');
        return false;
    }

    console.log('表单验证通过');
    return true;
}

// 读取文件为DataURL
function readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}
