/**
 * XYBotV2 文件管理器脚本
 * 实现文件浏览、创建、编辑、删除等功能
 */

// 当前路径
let currentPath = '/';
// 当前选中的文件/文件夹
let selectedItem = null;
// 添加分页相关变量
let currentPage = 1;
let totalPages = 1;
let pageSize = 100;
// 添加全局错误恢复标志
let isRecovering = false;
// 添加加载状态标志
let isLoading = false;

// 定义可编辑的文本文件扩展名
const textFileExtensions = [
    'txt', 'md', 'markdown', 'html', 'htm', 'css', 'js', 'json', 'xml', 'yaml', 'yml',
    'py', 'c', 'cpp', 'h', 'java', 'php', 'rb', 'sh', 'bat', 'ps1', 'ini', 'conf', 'cfg'
];

// 全局超时计时器
let globalTimeoutTimer = null;

// DOM元素引用
const elements = {
    fileList: document.getElementById('file-list'),
    fileListContainer: document.getElementById('file-list-container'),
    folderTree: document.getElementById('folder-tree'),
    pathBreadcrumb: document.getElementById('path-breadcrumb'),
    btnNewFile: document.getElementById('btn-new-file'),
    btnNewFileHeader: document.getElementById('btn-new-file-header'),
    btnNewFolder: document.getElementById('btn-new-folder'),
    btnNewFolderHeader: document.getElementById('btn-new-folder-header'),
    btnRefreshFiles: document.getElementById('btn-refresh-files'),
    btnRefreshFilesHeader: document.getElementById('btn-refresh-files-header'),
    btnGoUp: document.getElementById('btn-go-up'),
    fileEditor: document.getElementById('file-editor'),
    fileEditorContainer: document.getElementById('file-editor-container'),
    editorFilename: document.getElementById('editor-filename'),
    btnSaveFile: document.getElementById('btn-save-file'),
    btnCloseEditor: document.getElementById('btn-close-editor'),
    fileCount: document.getElementById('file-count'),
    selectionInfo: document.getElementById('selection-info'),
    statusInfo: document.getElementById('status-info'),
    newFileModal: document.getElementById('new-file-modal'),
    newFileNameInput: document.getElementById('new-file-name'),
    newFileContentInput: document.getElementById('new-file-content'),
    btnCreateFile: document.getElementById('btn-create-file'),
    newFolderModal: document.getElementById('new-folder-modal'),
    newFolderNameInput: document.getElementById('new-folder-name'),
    btnCreateFolder: document.getElementById('btn-create-folder'),
    btnConfirmDelete: document.getElementById('btn-confirm-delete'),
    deleteConfirmModal: document.getElementById('delete-confirm-modal'),
    deleteItemName: document.getElementById('delete-item-name'),
    deleteWarningMessage: document.getElementById('delete-warning-message'),
    renameModal: document.getElementById('rename-modal'),
    renameItemName: document.getElementById('rename-item-name'),
    btnConfirmRename: document.getElementById('btn-confirm-rename'),
    uploadFileModal: document.getElementById('upload-file-modal'),
    uploadPath: document.getElementById('upload-path'),
    uploadFiles: document.getElementById('upload-files'),
    uploadProgressContainer: document.getElementById('upload-progress-container'),
    uploadProgressBar: document.getElementById('upload-progress-bar'),
    uploadStatus: document.getElementById('upload-status'),
    btnStartUpload: document.getElementById('btn-start-upload'),
    btnUploadFile: document.getElementById('btn-upload-file'),
    uploadTypeFiles: document.getElementById('upload-type-files'),
    uploadTypeFolder: document.getElementById('upload-type-folder'),
    uploadTypeZip: document.getElementById('upload-type-zip'),
    fileSelector: document.getElementById('file-selector'),
    folderSelector: document.getElementById('folder-selector'),
    zipSelector: document.getElementById('zip-selector'),
    uploadFolder: document.getElementById('upload-folder'),
    uploadZip: document.getElementById('upload-zip'),
    autoExtract: document.getElementById('auto-extract'),
    extractOption: document.getElementById('extract-option'),
    extractFileModal: document.getElementById('extract-modal'),
    extractFilePath: document.getElementById('extract-file-path'),
    extractDestination: document.getElementById('extract-destination'),
    extractOverwrite: document.getElementById('extract-overwrite'),
    extractProgressContainer: document.getElementById('extract-progress-container'),
    extractProgressBar: document.getElementById('extract-progress-bar'),
    extractStatus: document.getElementById('extract-status'),
    btnStartExtract: document.getElementById('btn-start-extract'),
    loadingFiles: document.getElementById('loading-files'),
    emptyFolderMessage: document.getElementById('empty-folder-message'),
    errorMessage: document.getElementById('error-message'),
    errorDetails: document.getElementById('error-details'),
    btnRetry: document.getElementById('btn-retry'),
    lineNumbers: document.getElementById('line-numbers'),
    btnRefreshAddress: document.getElementById('btn-refresh-address')
};

// 模态框实例
const modals = {
    newFile: elements.newFileModal ? new bootstrap.Modal(elements.newFileModal) : null,
    newFolder: elements.newFolderModal ? new bootstrap.Modal(elements.newFolderModal) : null,
    deleteConfirm: elements.deleteConfirmModal ? new bootstrap.Modal(elements.deleteConfirmModal) : null,
    rename: elements.renameModal ? new bootstrap.Modal(elements.renameModal) : null,
    uploadFile: elements.uploadFileModal ? new bootstrap.Modal(elements.uploadFileModal) : null,
    extractFile: elements.extractFileModal ? new bootstrap.Modal(elements.extractFileModal) : null
};

// 页面上下文和状态管理
// let pageContext = {
//     currentPath: '/',
//     fileCount: 0,
//     selectedItem: null,
//     isLoading: false,
//     lastRefresh: Date.now(),
//     timeoutId: null,
//     pagination: {
//         currentPage: 1,
//         totalPages: 1,
//         totalItems: 0,
//         limit: 100
//     }
// };

// 初始化函数
function init() {
    console.log('初始化文件管理器...版本 1.0.3');
    console.log('当前页面路径:', window.location.pathname);
    console.log('DOM元素检查开始');

    // 检查必要元素是否存在
    if (!elements.fileList) {
        console.error('缺少必要的DOM元素: file-list');
        document.body.innerHTML = '<div class="alert alert-danger m-3">文件管理器加载失败：缺少必要的DOM元素</div>';
        return;
    }

    console.log('DOM元素检查完成，关键元素存在');

    // 初始化模态框
    console.log('开始初始化模态框');
    initModals();

    // 设置编辑器UI
    console.log('设置编辑器UI');
    setupEditorUI();

    // 注册事件处理程序
    console.log('注册事件处理程序');
    registerEventHandlers();

    // 加载首页文件
    console.log('加载首页文件:', currentPath);
    loadFiles(currentPath);

    // 加载文件夹树
    console.log('加载文件夹树');
    loadFolderTree();

    // 设置初始化标志
    window.fileManagerInitialized = true;

    // 触发加载完成事件
    window.dispatchEvent(new Event('fileManagerLoaded'));

    console.log('文件管理器初始化完成');
}

// 初始化模态框
function initModals() {
    try {
        console.log('初始化模态框');

        // 使用window中存储的解压模态框实例
        if (window.extractModalInstance) {
            console.log('使用全局解压模态框实例');
            modals.extractFile = window.extractModalInstance;
        }

            // 新建文件模态框
            if (elements.newFileModal) {
                try {
                    modals.newFile = new bootstrap.Modal(elements.newFileModal);
                    console.log('新建文件模态框初始化成功');
                } catch (e) {
                    console.error('初始化新建文件模态框失败:', e);
                }
            }

            // 新建文件夹模态框
            if (elements.newFolderModal) {
                try {
                    modals.newFolder = new bootstrap.Modal(elements.newFolderModal);
                    console.log('新建文件夹模态框初始化成功');
                } catch (e) {
                    console.error('初始化新建文件夹模态框失败:', e);
                }
            }

            // 删除确认模态框
            if (elements.deleteConfirmModal) {
                try {
                    modals.deleteConfirm = new bootstrap.Modal(elements.deleteConfirmModal);
                    console.log('删除确认模态框初始化成功');
                } catch (e) {
                    console.error('初始化删除确认模态框失败:', e);
                }
            }

            // 重命名模态框
            if (elements.renameModal) {
                try {
                    modals.rename = new bootstrap.Modal(elements.renameModal);
                    console.log('重命名模态框初始化成功');
                } catch (e) {
                    console.error('初始化重命名模态框失败:', e);
                }
            }
    } catch (e) {
        console.error('模态框初始化失败:', e);
    }
}

// 注册所有事件处理程序
function registerEventHandlers() {
    console.log('注册事件处理程序');

    // 文件操作事件
    try {
        // 新建文件按钮 - 兼容两个不同的按钮ID
        if (elements.btnNewFile) {
            elements.btnNewFile.addEventListener('click', showNewFileModal);
        }
        if (elements.btnNewFileHeader) {
            elements.btnNewFileHeader.addEventListener('click', showNewFileModal);
        }

        // 新建文件夹按钮 - 兼容两个不同的按钮ID
        if (elements.btnNewFolder) {
            elements.btnNewFolder.addEventListener('click', showNewFolderModal);
        }
        if (elements.btnNewFolderHeader) {
            elements.btnNewFolderHeader.addEventListener('click', showNewFolderModal);
        }

        // 刷新按钮 - 兼容两个不同的按钮ID
        if (elements.btnRefreshFiles) {
            elements.btnRefreshFiles.addEventListener('click', () => loadFiles(currentPath));
        }
        if (elements.btnRefreshFilesHeader) {
            elements.btnRefreshFilesHeader.addEventListener('click', () => loadFiles(currentPath));
        }

        // 上一级按钮
        if (elements.btnGoUp) {
            elements.btnGoUp.addEventListener('click', navigateUp);
        }

        // 刷新地址栏按钮
        if (elements.btnRefreshAddress) {
            elements.btnRefreshAddress.addEventListener('click', () => loadFiles(currentPath));
        }

        // 创建文件按钮
        if (elements.btnCreateFile) {
            elements.btnCreateFile.addEventListener('click', createNewFile);
        }

        // 创建文件夹按钮
        if (elements.btnCreateFolder) {
            elements.btnCreateFolder.addEventListener('click', createNewFolder);
        }

        // 确认删除按钮
        if (elements.btnConfirmDelete) {
            elements.btnConfirmDelete.addEventListener('click', deleteItem);
        }

        // 确认重命名按钮
        if (elements.btnConfirmRename) {
            elements.btnConfirmRename.addEventListener('click', renameItem);
        }

        // 保存文件按钮
        if (elements.btnSaveFile) {
            elements.btnSaveFile.addEventListener('click', saveFile);
        }

        // 关闭编辑器按钮
        if (elements.btnCloseEditor) {
            elements.btnCloseEditor.addEventListener('click', closeEditor);
        }

        // 文件编辑器输入事件
        if (elements.fileEditor) {
            elements.fileEditor.addEventListener('input', () => {
                // 启用保存按钮
                if (elements.btnSaveFile) {
                    elements.btnSaveFile.disabled = false;
                }

                // 更新行号
                updateLineNumbers();
            });

            // 编辑器滚动同步行号
            elements.fileEditor.addEventListener('scroll', () => {
                if (elements.lineNumbers) {
                    elements.lineNumbers.scrollTop = elements.fileEditor.scrollTop;
                }
            });
        }

        // 处理新建文件模态框输入事件
        if (elements.newFileNameInput) {
            elements.newFileNameInput.addEventListener('keyup', function(event) {
                if (event.key === 'Enter') {
                    createNewFile();
                }
            });
        }

        // 处理新建文件夹模态框输入事件
        if (elements.newFolderNameInput) {
            elements.newFolderNameInput.addEventListener('keyup', function(event) {
                if (event.key === 'Enter') {
                    createNewFolder();
                }
            });
        }

        // 处理重命名模态框输入事件
        if (elements.renameItemName) {
            elements.renameItemName.addEventListener('keyup', function(event) {
                if (event.key === 'Enter') {
                    renameItem();
                }
            });
        }

        console.log('事件处理程序注册完成');
    } catch (error) {
        console.error('注册事件处理程序失败:', error);
    }

    // 文件上传相关事件
    if (elements.btnUploadFile) {
        elements.btnUploadFile.addEventListener('click', function() {
            // 设置上传路径为当前路径
            if (elements.uploadPath) {
                elements.uploadPath.value = currentPath;
            }
            // 显示上传模态框
            if (modals.uploadFile) {
                modals.uploadFile.show();
            } else {
                console.error('上传模态框实例未创建');
                showToast('错误', '无法打开上传窗口，请刷新页面后重试', 'error');
            }
        });
    }

    if (elements.btnStartUpload) {
        elements.btnStartUpload.addEventListener('click', uploadFiles);
    }

    // 添加上传类型切换处理
    if (elements.uploadTypeFiles && elements.uploadTypeFolder && elements.uploadTypeZip) {
        // 文件类型切换
        elements.uploadTypeFiles.addEventListener('change', function() {
            if (this.checked) {
                updateUploadUI();
            }
        });

        // 文件夹类型切换
        elements.uploadTypeFolder.addEventListener('change', function() {
            if (this.checked) {
                updateUploadUI();
            }
        });

        // 压缩包类型切换
        elements.uploadTypeZip.addEventListener('change', function() {
            if (this.checked) {
                updateUploadUI();
            }
        });
    }

    // 添加解压文件按钮事件处理
    if (elements.btnStartExtract) {
        elements.btnStartExtract.addEventListener('click', extractArchive);
    }
}

// 设置全局错误处理
window.addEventListener('error', function(event) {
    console.error('全局错误:', event.error);
    handleGlobalError(event.error);
});

window.addEventListener('unhandledrejection', function(event) {
    console.error('未处理的Promise拒绝:', event.reason);
    handleGlobalError(event.reason);
});

// 全局错误处理函数
function handleGlobalError(error) {
    if (isRecovering) return; // 防止重复恢复

    isRecovering = true;
    console.warn('触发错误恢复机制');

    try {
        showToast('系统错误', '发生意外错误，正在尝试恢复...', 'warning');

        // 清除任何可能的超时计时器
        if (globalTimeoutTimer) {
            clearTimeout(globalTimeoutTimer);
            globalTimeoutTimer = null;
        }

        // 重设UI状态
        resetUIState();

        // 重新加载当前路径，但重置到第一页
        currentPage = 1;
        setTimeout(() => {
            loadFiles(currentPath);
            isRecovering = false;
        }, 1000);
    } catch (e) {
        console.error('恢复过程中出错:', e);
        showToast('致命错误', '无法恢复应用状态，请刷新页面', 'danger');
    }
}

// 重设UI状态
function resetUIState() {
    // 隐藏所有可能的加载和错误指示器
    if (elements.loadingFiles) elements.loadingFiles.style.display = 'none';
    if (elements.emptyFolderMessage) elements.emptyFolderMessage.style.display = 'none';
    if (elements.errorMessage) elements.errorMessage.style.display = 'none';

    // 重置选择状态
    selectedItem = null;
    if (elements.selectionInfo) elements.selectionInfo.textContent = '';

    // 清空文件列表
    if (elements.fileList) {
        while (elements.fileList.firstChild) {
            if (elements.fileList.firstChild === elements.loadingFiles ||
                elements.fileList.firstChild === elements.emptyFolderMessage ||
                elements.fileList.firstChild === elements.errorMessage) {
                elements.fileList.firstChild.style.display = 'none';
            } else {
                elements.fileList.removeChild(elements.fileList.firstChild);
            }
        }
    }

    // 更新状态信息
    if (elements.statusInfo) {
        elements.statusInfo.textContent = '正在恢复...';
        elements.statusInfo.className = 'text-warning';
    }
}

// 设置全局超时处理
function setupGlobalTimeout(milliseconds = 30000) {
    // 清除现有计时器
    if (globalTimeoutTimer) {
        clearTimeout(globalTimeoutTimer);
    }

    // 设置新计时器
    globalTimeoutTimer = setTimeout(() => {
        console.warn(`操作超时 (${milliseconds}ms)`);
        handleGlobalError(new Error('操作超时'));
    }, milliseconds);
}

// 清除全局超时
function clearGlobalTimeout() {
    if (globalTimeoutTimer) {
        clearTimeout(globalTimeoutTimer);
        globalTimeoutTimer = null;
    }
}

// 加载文件
function loadFiles(path, page = 1) {
    // 更新当前路径
    currentPath = path;
    console.log(`加载文件夹: ${path}, 页码: ${page}`);

    // 显示加载状态
    showLoading();

    // 标记为加载中
    isLoading = true;

    // 发送请求获取文件列表
    fetch(`/api/files/list?path=${encodeURIComponent(path)}&page=${page}`)
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.message || `HTTP错误! 状态: ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            isLoading = false;
            // 隐藏加载状态
            hideLoading();

            console.log('文件数据加载成功:', data);

            if (data.success) {
                // 更新当前路径（防止异步请求返回时路径已变）
                if (currentPath === path) {
                    // 更新面包屑导航
                    updateBreadcrumb(path);

                    // 检查items数组是否有效
                    if (!data.items || !Array.isArray(data.items)) {
                        console.error('API返回无效文件列表:', data);
                        showError('服务器返回的文件列表格式无效');
                        return;
                    }

                    // 显示文件列表
                    displayFiles(data.items);

                    // 更新文件计数
                    updateFileCount(data.items.length);

                    // 更新分页信息
                    if (data.pagination) {
                        updatePagination(data.pagination);
                    }
                }
            } else {
                throw new Error(data.message || '加载文件失败');
            }
        })
        .catch(error => {
            isLoading = false;
            // 隐藏加载状态
            hideLoading();

            console.error('加载文件失败:', error);
            showError(error.message);
        });
}

// 显示文件列表
function displayFiles(files) {
    if (!elements.fileList) return;

    // 确保加载指示器被隐藏
    hideLoading();

    // 确保files是数组类型
    if (!files || !Array.isArray(files)) {
        console.error('文件列表数据无效:', files);
        showError('文件列表数据格式无效');
        return;
    }

    // 清空现有内容
    while (elements.fileList.firstChild) {
        if (elements.fileList.firstChild === elements.loadingFiles ||
            elements.fileList.firstChild === elements.emptyFolderMessage ||
            elements.fileList.firstChild === elements.errorMessage) {
            elements.fileList.firstChild.style.display = 'none';
        } else {
            elements.fileList.removeChild(elements.fileList.firstChild);
        }
    }

    // 文件列表为空
    if (files.length === 0) {
        if (elements.emptyFolderMessage) elements.emptyFolderMessage.style.display = 'flex';
        return;
    }

    // 创建文件列表项
    files.forEach(file => {
        const itemEl = document.createElement('div');
        itemEl.className = 'file-list-item';
        itemEl.dataset.path = file.path;
        itemEl.dataset.name = file.name;
        itemEl.dataset.type = file.type;

        // 图标类
        let iconClass;
        if (file.type === 'directory') {
            iconClass = 'bi-folder-fill file-icon-folder';
        } else {
            // 根据文件扩展名选择图标
            const extension = file.name.split('.').pop().toLowerCase();
            switch (extension) {
                case 'py': iconClass = 'bi-filetype-py file-icon-py'; break;
                case 'js': iconClass = 'bi-filetype-js file-icon-js'; break;
                case 'html': iconClass = 'bi-filetype-html file-icon-html'; break;
                case 'css': iconClass = 'bi-filetype-css file-icon-css'; break;
                case 'json': iconClass = 'bi-filetype-json file-icon-json'; break;
                case 'md': iconClass = 'bi-filetype-md file-icon-md'; break;
                case 'txt': iconClass = 'bi-filetype-txt file-icon-txt'; break;
                case 'zip':
                case 'rar':
                case '7z':
                case 'tar':
                case 'gz':
                case 'tgz': iconClass = 'bi-file-earmark-zip file-icon-zip'; break;
                default: iconClass = 'bi-file-earmark file-icon-txt';
            }
        }

        itemEl.innerHTML = `
            <div class="file-info">
                <i class="bi ${iconClass}"></i>
                <div class="file-name">${file.name}</div>
                <div class="file-size">${formatFileSize(file.size)}</div>
                <div class="file-date">${formatDate(file.modified)}</div>
            </div>
            <div class="file-actions">
                <button class="btn btn-sm btn-outline-primary action-edit" title="编辑" ${file.type === 'directory' ? 'disabled' : ''}>
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-success action-rename" title="重命名">
                    <i class="bi bi-pencil-square"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger action-delete" title="删除">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        `;

        // 添加事件处理程序
        itemEl.addEventListener('click', (e) => {
            // 忽略点击操作按钮的情况
            if (e.target.closest('.file-actions')) return;

            selectItem(itemEl);

            // 如果是文件夹，导航到该文件夹
            if (file.type === 'directory') {
                loadFiles(file.path);
            }
        });

        // 添加右键菜单事件
        itemEl.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            selectItem(itemEl);
            showContextMenu(e, itemEl);
        });

        // 编辑按钮
        const editBtn = itemEl.querySelector('.action-edit');
        if (editBtn) {
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (file.type !== 'directory') {
                    openEditor(file.path, file.name);
                }
            });
        }

        // 重命名按钮
        const renameBtn = itemEl.querySelector('.action-rename');
        if (renameBtn) {
            renameBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                showRenameModal(file.path, file.name);
            });
        }

        // 删除按钮
        const deleteBtn = itemEl.querySelector('.action-delete');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                showDeleteModal(file.path, file.name, file.type === 'directory');
            });
        }

        elements.fileList.appendChild(itemEl);
    });
}

// 加载文件夹树
function loadFolderTree() {
    if (!elements.folderTree) return;

    // 显示加载中
    elements.folderTree.innerHTML = `
        <div class="d-flex align-items-center">
            <div class="spinner-border spinner-border-sm text-primary me-2" role="status">
                <span class="visually-hidden">加载中...</span>
            </div>
            <span>加载文件夹...</span>
        </div>
    `;

    fetch('/api/files/tree')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.success && data.tree) {
                console.log('成功加载文件夹树');

                // 创建根文件夹
                elements.folderTree.innerHTML = '';
                const rootLi = document.createElement('li');
                rootLi.className = 'folder-item';
                rootLi.dataset.path = '/';

                // 创建根文件夹的内容区域
                const itemContent = document.createElement('div');
                itemContent.className = 'folder-item-content';
                itemContent.innerHTML = `
                    <span class="folder-toggle"><i class="bi bi-chevron-down"></i></span>
                    <i class="bi bi-folder-fill file-icon-folder"></i>
                    <span>根目录</span>
                `;

                rootLi.appendChild(itemContent);

                // 点击根目录
                itemContent.addEventListener('click', (e) => {
                    e.stopPropagation();
                    selectFolderTreeItem(rootLi);
                    loadFiles('/');
                });

                elements.folderTree.appendChild(rootLi);

                // 构建子文件夹
                if (data.tree.children && data.tree.children.length > 0) {
                    rootLi.classList.add('expanded');

                    const ul = document.createElement('ul');
                    data.tree.children.forEach(child => {
                        if (child.type === 'directory') {
                            const li = createFolderTreeItem(child);
                            ul.appendChild(li);
                        }
                    });

                    rootLi.appendChild(ul);

                    // 添加展开/折叠事件
                    const toggler = itemContent.querySelector('.folder-toggle');
                    if (toggler) {
                        toggler.addEventListener('click', (e) => {
                            e.stopPropagation();
                            rootLi.classList.toggle('expanded');
                        });
                    }
                }
            } else {
                console.error('加载文件夹树失败，无效的响应数据:', data);
                elements.folderTree.innerHTML = `
                    <div class="text-danger">
                        <i class="bi bi-exclamation-triangle me-1"></i>
                        加载文件夹失败
                    </div>
                `;
            }
        })
        .catch(error => {
            console.error('加载文件夹树失败:', error);
            elements.folderTree.innerHTML = `
                <div class="text-danger">
                    <i class="bi bi-exclamation-triangle me-1"></i>
                    加载文件夹失败: ${error.message}
                </div>
            `;
        });
}

// 创建文件夹树项
function createFolderTreeItem(folder) {
    const li = document.createElement('li');
    li.className = 'folder-item';
    li.dataset.path = folder.path;

    // 文件夹项的基本结构，包含图标和名称
    const itemContent = document.createElement('div');
    itemContent.className = 'folder-item-content';
    itemContent.innerHTML = `
        <span class="folder-toggle"><i class="bi bi-chevron-down"></i></span>
        <i class="bi bi-folder-fill file-icon-folder"></i>
        <span>${folder.name}</span>
    `;

    li.appendChild(itemContent);

    // 点击文件夹项导航到该文件夹
    itemContent.addEventListener('click', (e) => {
        e.stopPropagation();
        selectFolderTreeItem(li);
        loadFiles(folder.path);
    });

    // 如果有子文件夹，添加展开/折叠功能
    if (folder.children && folder.children.length > 0) {
        // 创建子文件夹列表
        const ul = document.createElement('ul');

        // 修复递归问题：防止无限递归
        const maxDepth = 10; // 最大递归深度
        function addChildrenWithDepthLimit(children, parent, depth) {
            if (depth > maxDepth) return; // 防止过深递归

            children.forEach(child => {
                if (child.type === 'directory') {
                    const childLi = createFolderTreeItem(child);
                    parent.appendChild(childLi);
                }
            });
        }

        // 使用有深度限制的函数添加子文件夹
        addChildrenWithDepthLimit(folder.children, ul, 1);
        li.appendChild(ul);

        // 添加展开/折叠事件
        const toggler = itemContent.querySelector('.folder-toggle');
        if (toggler) {
            toggler.addEventListener('click', (e) => {
                e.stopPropagation();
                li.classList.toggle('expanded');
            });
        }
    }

    return li;
}

// 选择文件夹树项
function selectFolderTreeItem(item) {
    // 移除之前选中的项
    const activeItems = elements.folderTree.querySelectorAll('.active');
    activeItems.forEach(el => el.classList.remove('active'));

    // 添加新的选中项
    item.classList.add('active');
}

// 更新面包屑导航
function updateBreadcrumb(path) {
    if (!elements.pathBreadcrumb) return;

    // 清空现有内容
    elements.pathBreadcrumb.innerHTML = '';

    // 添加根目录
    const rootLi = document.createElement('li');
    rootLi.className = 'breadcrumb-item';
    rootLi.innerHTML = '<a href="#" data-path="/">根目录</a>';
    const rootLink = rootLi.querySelector('a');
    rootLink.addEventListener('click', (e) => {
        e.preventDefault();
        loadFiles('/');
    });
    elements.pathBreadcrumb.appendChild(rootLi);

    // 如果是根目录，直接返回
    if (path === '/') {
        rootLi.classList.add('active');
        return;
    }

    // 分割路径
    const parts = path.split('/').filter(part => part);
    let currentPath = '';

    // 添加每一部分
    parts.forEach((part, index) => {
        currentPath += '/' + part;

        const li = document.createElement('li');
        li.className = 'breadcrumb-item';

        if (index === parts.length - 1) {
            li.classList.add('active');
            li.textContent = part;
        } else {
            const targetPath = currentPath; // 保存当前路径供点击回调使用
            li.innerHTML = `<a href="#" data-path="${targetPath}">${part}</a>`;
            const link = li.querySelector('a');
            link.addEventListener('click', (e) => {
                e.preventDefault();
                // 导航到对应路径
                loadFiles(targetPath);
            });
        }

        elements.pathBreadcrumb.appendChild(li);
    });
}

// 导航到上一级目录
function navigateUp() {
    // 如果已经在根目录，不进行操作
    if (currentPath === '/' || isLoading) {
        return;
    }

    // 获取上一级路径
    const path = currentPath;
    const parts = path.split('/').filter(Boolean);
    parts.pop();
    const parentPath = parts.length > 0 ? '/' + parts.join('/') : '/';

    // 加载上一级目录
    loadFiles(parentPath);
}

// 选择文件/文件夹
function selectItem(item) {
    // 移除之前选中的项
    const activeItems = elements.fileList.querySelectorAll('.active');
    activeItems.forEach(el => el.classList.remove('active'));

    // 添加新的选中项
    item.classList.add('active');
    selectedItem = item;

    // 更新选中信息
    updateSelectionInfo(item.dataset.name, item.dataset.type);
}

// 更新选中信息
function updateSelectionInfo(name, type) {
    if (!elements.selectionInfo) return;

    elements.selectionInfo.textContent = `已选中: ${name} (${type === 'directory' ? '文件夹' : '文件'})`;
}

// 更新文件计数
function updateFileCount(count) {
    if (!elements.fileCount) return;

    elements.fileCount.textContent = count;
}

// 显示加载状态
function showLoading() {
    if (!elements.loadingFiles) return;

    // 隐藏其他消息
    if (elements.emptyFolderMessage) elements.emptyFolderMessage.style.display = 'none';
    if (elements.errorMessage) elements.errorMessage.style.display = 'none';

    // 显示加载消息
    elements.loadingFiles.style.display = 'flex';
}

// 隐藏加载状态
function hideLoading() {
    if (!elements.loadingFiles) return;

    // 隐藏加载消息
    elements.loadingFiles.style.display = 'none';
}

// 显示错误消息
function showError(message) {
    console.error('文件管理器错误:', message);

    // 隐藏加载中动画
    if (elements.loadingFiles) {
        elements.loadingFiles.style.display = 'none';
    }

    // 显示错误消息
    if (elements.errorMessage) {
        elements.errorMessage.style.display = 'block';
        if (elements.errorDetails) {
            elements.errorDetails.textContent = message || '未知错误';
        }
    }

    // 显示Toast消息
    try {
        showToast('错误', message || '操作失败', 'danger');
    } catch (e) {
        console.error('显示Toast失败:', e);
    }

    // 确保空文件夹提示被隐藏
    if (elements.emptyFolderMessage) {
        elements.emptyFolderMessage.style.display = 'none';
    }
}

// 显示新建文件模态框
function showNewFileModal() {
    console.log('尝试显示新建文件模态框');

    if (!elements.newFileModal) {
        console.error('找不到新建文件模态框元素!');
        alert('找不到新建文件窗口元素，请刷新页面再试');
        return;
    }

    // 重置输入
    if (elements.newFileNameInput) elements.newFileNameInput.value = '';
    if (elements.newFileContentInput) elements.newFileContentInput.value = '';

    // 使用Bootstrap API显示模态框
    try {
        const modalInstance = bootstrap.Modal.getInstance(elements.newFileModal) || new bootstrap.Modal(elements.newFileModal);
        modalInstance.show();
    } catch (error) {
        console.error('显示新建文件模态框失败:', error);
        showToast('错误', '无法显示新建文件窗口，请刷新页面后重试', 'error');
    }
}

// 显示新建文件夹模态框
function showNewFolderModal() {
    console.log('尝试显示新建文件夹模态框');

    if (!elements.newFolderModal) {
        console.error('找不到新建文件夹模态框元素!');
        alert('找不到新建文件夹窗口元素，请刷新页面再试');
        return;
    }

    // 重置输入
    if (elements.newFolderNameInput) elements.newFolderNameInput.value = '';

    // 使用Bootstrap API显示模态框
    try {
        const modalInstance = bootstrap.Modal.getInstance(elements.newFolderModal) || new bootstrap.Modal(elements.newFolderModal);
        modalInstance.show();
    } catch (error) {
        console.error('显示新建文件夹模态框失败:', error);
        showToast('错误', '无法显示新建文件夹窗口，请刷新页面后重试', 'error');
    }
}

// 显示删除确认模态框
function showDeleteModal(path, name, isDirectory) {
    if (!modals.deleteConfirm) return;

    // 设置项目信息
    if (elements.deleteItemName) elements.deleteItemName.textContent = name;

    // 设置特殊警告
    if (elements.deleteWarningMessage) {
        if (isDirectory) {
            elements.deleteWarningMessage.textContent = '此操作将永久删除该文件夹及其中的所有内容，且无法恢复。';
        } else {
            elements.deleteWarningMessage.textContent = '此操作将永久删除该文件，且无法恢复。';
        }
    }

    // 保存路径到按钮
    if (elements.btnConfirmDelete) {
        elements.btnConfirmDelete.dataset.path = path;
        elements.btnConfirmDelete.dataset.isDirectory = isDirectory;
    }

    // 显示模态框
    modals.deleteConfirm.show();
}

// 显示重命名模态框
function showRenameModal(path, name) {
    if (!modals.rename) return;

    // 设置输入值
    if (elements.renameItemName) elements.renameItemName.value = name;

    // 保存路径到按钮
    if (elements.btnConfirmRename) {
        elements.btnConfirmRename.dataset.path = path;
        elements.btnConfirmRename.dataset.oldName = name;
    }

    // 显示模态框
    modals.rename.show();
}

// 创建新文件
function createNewFile() {
    if (!elements.newFileNameInput || !elements.newFileContentInput) return;

    const fileName = elements.newFileNameInput.value.trim();
    const fileContent = elements.newFileContentInput.value;

    if (!fileName) {
        alert('请输入文件名');
        return;
    }

    // 构建完整路径
    let filePath = currentPath;
    if (filePath !== '/' && !filePath.endsWith('/')) filePath += '/';
    filePath += fileName;

    // 发送请求创建文件
    fetch('/api/files/create', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            path: filePath,
            content: fileContent,
            type: 'file'
        }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.message || `HTTP错误! 状态: ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            // 关闭模态框
            if (modals.newFile) {
                modals.newFile.hide();
            }

            // 重新加载文件
            loadFiles(currentPath);

            // 显示成功消息
            showToast('成功', `文件 ${fileName} 已创建`, 'success');
        } else {
            throw new Error(data.message || '创建文件失败');
        }
    })
    .catch(error => {
        console.error('创建文件失败:', error);
        alert(`创建文件失败: ${error.message}`);
    });
}

// 创建新文件夹
function createNewFolder() {
    if (!elements.newFolderNameInput) return;

    const folderName = elements.newFolderNameInput.value.trim();

    if (!folderName) {
        alert('请输入文件夹名');
        return;
    }

    // 构建完整路径
    let folderPath = currentPath;
    if (folderPath !== '/' && !folderPath.endsWith('/')) folderPath += '/';
    folderPath += folderName;

    // 发送请求创建文件夹
    fetch('/api/files/create', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            path: folderPath,
            content: '',
            type: 'directory'
        }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.message || `HTTP错误! 状态: ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            // 关闭模态框
            if (modals.newFolder) {
                modals.newFolder.hide();
            }

            // 重新加载文件和文件夹树
            loadFiles(currentPath);
            loadFolderTree();

            // 显示成功消息
            showToast('成功', `文件夹 ${folderName} 已创建`, 'success');
        } else {
            throw new Error(data.message || '创建文件夹失败');
        }
    })
    .catch(error => {
        console.error('创建文件夹失败:', error);
        alert(`创建文件夹失败: ${error.message}`);
    });
}

// 删除文件/文件夹
function deleteItem() {
    if (!elements.btnConfirmDelete) return;

    const path = elements.btnConfirmDelete.dataset.path;
    const isDirectory = elements.btnConfirmDelete.dataset.isDirectory === 'true';

    // 发送请求删除文件/文件夹
    fetch('/api/files/delete', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            path: path
        }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.message || `HTTP错误! 状态: ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            // 关闭模态框
            if (modals.deleteConfirm) {
                modals.deleteConfirm.hide();
            }

            // 重新加载文件和文件夹树
            loadFiles(currentPath);
            if (isDirectory) {
                loadFolderTree();
            }

            // 显示成功消息
            showToast('删除成功', `已成功删除 ${isDirectory ? '文件夹' : '文件'}`, 'success');
        } else {
            throw new Error(data.message || '删除失败');
        }
    })
    .catch(error => {
        console.error('删除失败:', error);
        alert(`删除失败: ${error.message}`);
    });
}

// 重命名文件/文件夹
function renameItem() {
    if (!elements.btnConfirmRename || !elements.renameItemName) return;

    const path = elements.btnConfirmRename.dataset.path;
    const oldName = elements.btnConfirmRename.dataset.oldName;
    const newName = elements.renameItemName.value.trim();

    if (!newName) {
        alert('请输入新名称');
        return;
    }

    // 构建新路径
    const pathParts = path.split('/');
    pathParts.pop();
    let newPath = pathParts.join('/');
    if (newPath !== '/' && !newPath.endsWith('/')) newPath += '/';
    newPath += newName;

    // 发送请求重命名文件/文件夹
    fetch('/api/files/rename', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            old_path: path,
            new_path: newPath
        }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.message || `HTTP错误! 状态: ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            // 关闭模态框
            if (modals.rename) {
                modals.rename.hide();
            }

            // 重新加载文件和文件夹树
            loadFiles(currentPath);
            const isDirectory = path.split('.').length === 1;
            if (isDirectory) {
                loadFolderTree();
            }

            // 显示成功消息
            showToast('成功', `已将 ${oldName} 重命名为 ${newName}`, 'success');
        } else {
            throw new Error(data.message || '重命名失败');
        }
    })
    .catch(error => {
        console.error('重命名失败:', error);
        alert(`重命名失败: ${error.message}`);
    });
}

// 打开文件编辑器 - 修复编辑功能
function openEditor(path, name) {
    if (!elements.fileEditorContainer || !elements.fileEditor || !elements.editorFilename) {
        console.error('文件编辑器DOM元素不存在!');
        alert('文件编辑器加载失败，请刷新页面再试');
        return;
    }

    // 设置正在加载的提示
    elements.editorFilename.textContent = `加载中: ${name}`;
    elements.fileEditor.value = '加载文件内容中，请稍候...';
    elements.fileEditor.disabled = true;

    // 显示编辑器
    elements.fileEditorContainer.style.display = 'flex';

    // 请求文件内容
    fetch(`/api/files/read?path=${encodeURIComponent(path)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                // 设置编辑器内容
                elements.editorFilename.textContent = name;
                elements.fileEditor.value = data.content;
                elements.fileEditor.disabled = false;

                // 设置文件路径到编辑器和保存按钮
                elements.fileEditor.dataset.path = path;
                elements.fileEditor.dataset.name = name;
                if (elements.btnSaveFile) {
                    elements.btnSaveFile.dataset.path = path;
                    elements.btnSaveFile.disabled = false;
                }

                // 更新行号
                updateLineNumbers();

                // 聚焦编辑器
                elements.fileEditor.focus();
            } else {
                throw new Error(data.message || '读取文件失败');
            }
        })
        .catch(error => {
            console.error('读取文件失败:', error);
            alert(`无法读取文件: ${error.message}`);
            closeEditor();
        });
}

// 关闭编辑器
function closeEditor() {
    if (!elements.fileEditorContainer) return;

    // 隐藏编辑器
    elements.fileEditorContainer.style.display = 'none';

    // 清空编辑器内容
    if (elements.fileEditor) elements.fileEditor.value = '';

    // 清空行号
    if (elements.lineNumbers) elements.lineNumbers.innerHTML = '';
}

// 保存文件
function saveFile() {
    if (!elements.fileEditor || !elements.btnSaveFile) return;

    const path = elements.btnSaveFile.dataset.path;
    const content = elements.fileEditor.value;

    // 禁用保存按钮，防止重复点击
    elements.btnSaveFile.disabled = true;

    // 发送请求保存文件
    fetch('/api/files/write', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            path: path,
            content: content
        }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.message || `HTTP错误! 状态: ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            // 显示成功消息
            showToast('成功', '文件已保存', 'success');
        } else {
            throw new Error(data.message || '保存文件失败');
        }
    })
    .catch(error => {
        console.error('保存文件失败:', error);
        alert(`保存文件失败: ${error.message}`);
    })
    .finally(() => {
        // 恢复保存按钮
        elements.btnSaveFile.disabled = false;
    });
}

// 更新行号 - 修复长文件显示问题
function updateLineNumbers() {
    if (!elements.lineNumbers || !elements.fileEditor) return;

    const content = elements.fileEditor.value;
    const lines = content.split('\n');
    const lineCount = lines.length;

    // 生成行号HTML
    let lineNumbersHTML = '';
    for (let i = 1; i <= lineCount; i++) {
        lineNumbersHTML += `${i}<br>`;
    }

    // 设置行号
    elements.lineNumbers.innerHTML = lineNumbersHTML;

    // 改进编辑器高度计算方式
    // 不再使用行高乘以行数，而是使用自适应高度
    const editorContainer = elements.fileEditorContainer;
    if (editorContainer) {
        // 计算编辑器容器高度，减去其他UI元素高度
        const containerHeight = window.innerHeight - 100; // 为顶部导航和底部留出空间
        const toolbarHeight = editorContainer.querySelector('.editor-toolbar')?.offsetHeight || 0;

        // 设置编辑器主区域高度
        const editorMainArea = editorContainer.querySelector('.editor-main');
        if (editorMainArea) {
            editorMainArea.style.height = `${containerHeight - toolbarHeight}px`;

            // 编辑器和行号区高度自适应
            elements.fileEditor.style.height = '100%';
            elements.lineNumbers.style.height = '100%';
        }
    }

    // 确保滚动同步
    elements.fileEditor.addEventListener('scroll', () => {
        if (elements.lineNumbers) {
            elements.lineNumbers.scrollTop = elements.fileEditor.scrollTop;
        }
    });
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === null || bytes === undefined) return 'N/A';
    if (bytes === 0) return '0 B';

    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));

    return parseFloat((bytes / Math.pow(1024, i)).toFixed(2)) + ' ' + units[i];
}

// 格式化日期
function formatDate(timestamp) {
    if (!timestamp) return 'N/A';

    const date = new Date(timestamp * 1000);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// 显示通知
function showToast(title, message, type = 'success') {
    try {
        const toastContainer = document.querySelector('.toast-container');
        if (!toastContainer) {
            console.error('找不到toast容器元素');
            return;
        }

        const toastId = 'toast-' + Date.now();
        const toastEl = document.createElement('div');
        toastEl.className = 'toast';
        toastEl.id = toastId;
        toastEl.setAttribute('role', 'alert');
        toastEl.setAttribute('aria-live', 'assertive');
        toastEl.setAttribute('aria-atomic', 'true');

        toastEl.innerHTML = `
            <div class="toast-header">
                <strong class="me-auto">${title}</strong>
                <small class="text-muted">刚刚</small>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body">
                ${message}
            </div>
        `;

        if (type === 'success') {
            toastEl.classList.add('bg-success', 'text-white');
        } else if (type === 'error') {
            toastEl.classList.add('bg-danger', 'text-white');
        } else if (type === 'warning') {
            toastEl.classList.add('bg-warning', 'text-white');
        } else {
            toastEl.classList.add('bg-info', 'text-white');
        }

        toastContainer.appendChild(toastEl);

        const toast = new bootstrap.Toast(toastEl, {
            autohide: true,
            delay: 5000
        });

        toast.show();

        // 自动删除
        toastEl.addEventListener('hidden.bs.toast', () => {
            toastEl.remove();
        });
    } catch (error) {
        console.error('显示通知失败:', error);
    }
}

// 更新分页控件
function updatePagination(pagination) {
    const paginationContainer = document.getElementById('pagination');
    if (!paginationContainer) return;

    // 清空现有分页控件
    paginationContainer.innerHTML = '';

    // 如果只有一页，不显示分页
    if (pagination.total_pages <= 1) {
        paginationContainer.style.display = 'none';
        return;
    }

    paginationContainer.style.display = 'flex';

    // 创建分页控件
    const nav = document.createElement('nav');
    nav.setAttribute('aria-label', '文件列表分页');

    const ul = document.createElement('ul');
    ul.className = 'pagination pagination-sm mb-0';

    // 添加"上一页"按钮
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${pagination.page <= 1 ? 'disabled' : ''}`;

    const prevLink = document.createElement('a');
    prevLink.className = 'page-link';
    prevLink.href = '#';
    prevLink.innerHTML = '&laquo;';
    prevLink.setAttribute('aria-label', '上一页');

    if (pagination.page > 1) {
        prevLink.addEventListener('click', (e) => {
            e.preventDefault();
            loadFiles(currentPath, pagination.page - 1);
        });
    }

    prevLi.appendChild(prevLink);
    ul.appendChild(prevLi);

    // 添加页码按钮（最多显示5个页码）
    const maxVisiblePages = 5;
    let startPage = Math.max(1, pagination.page - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(pagination.total_pages, startPage + maxVisiblePages - 1);

    if (endPage - startPage + 1 < maxVisiblePages) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }

    // 添加第一页和省略号（如果需要）
    if (startPage > 1) {
        const firstPageLi = document.createElement('li');
        firstPageLi.className = 'page-item';

        const firstPageLink = document.createElement('a');
        firstPageLink.className = 'page-link';
        firstPageLink.href = '#';
        firstPageLink.textContent = '1';

        firstPageLink.addEventListener('click', (e) => {
            e.preventDefault();
            loadFiles(currentPath, 1);
        });

        firstPageLi.appendChild(firstPageLink);
        ul.appendChild(firstPageLi);

        if (startPage > 2) {
            const ellipsisLi = document.createElement('li');
            ellipsisLi.className = 'page-item disabled';

            const ellipsisSpan = document.createElement('span');
            ellipsisSpan.className = 'page-link';
            ellipsisSpan.innerHTML = '&hellip;';

            ellipsisLi.appendChild(ellipsisSpan);
            ul.appendChild(ellipsisLi);
        }
    }

    // 添加页码
    for (let i = startPage; i <= endPage; i++) {
        const pageLi = document.createElement('li');
        pageLi.className = `page-item ${i === pagination.page ? 'active' : ''}`;

        const pageLink = document.createElement('a');
        pageLink.className = 'page-link';
        pageLink.href = '#';
        pageLink.textContent = i;

        if (i !== pagination.page) {
            pageLink.addEventListener('click', (e) => {
                e.preventDefault();
                loadFiles(currentPath, i);
            });
        }

        pageLi.appendChild(pageLink);
        ul.appendChild(pageLi);
    }

    // 添加最后一页和省略号（如果需要）
    if (endPage < pagination.total_pages) {
        if (endPage < pagination.total_pages - 1) {
            const ellipsisLi = document.createElement('li');
            ellipsisLi.className = 'page-item disabled';

            const ellipsisSpan = document.createElement('span');
            ellipsisSpan.className = 'page-link';
            ellipsisSpan.innerHTML = '&hellip;';

            ellipsisLi.appendChild(ellipsisSpan);
            ul.appendChild(ellipsisLi);
        }

        const lastPageLi = document.createElement('li');
        lastPageLi.className = 'page-item';

        const lastPageLink = document.createElement('a');
        lastPageLink.className = 'page-link';
        lastPageLink.href = '#';
        lastPageLink.textContent = pagination.total_pages;

        lastPageLink.addEventListener('click', (e) => {
            e.preventDefault();
            loadFiles(currentPath, pagination.total_pages);
        });

        lastPageLi.appendChild(lastPageLink);
        ul.appendChild(lastPageLi);
    }

    // 添加"下一页"按钮
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${pagination.page >= pagination.total_pages ? 'disabled' : ''}`;

    const nextLink = document.createElement('a');
    nextLink.className = 'page-link';
    nextLink.href = '#';
    nextLink.innerHTML = '&raquo;';
    nextLink.setAttribute('aria-label', '下一页');

    if (pagination.page < pagination.total_pages) {
        nextLink.addEventListener('click', (e) => {
            e.preventDefault();
            loadFiles(currentPath, pagination.page + 1);
        });
    }

    nextLi.appendChild(nextLink);
    ul.appendChild(nextLi);

    nav.appendChild(ul);
    paginationContainer.appendChild(nav);
}

// 编辑器UI调整
function setupEditorUI() {
    const editorContainer = elements.fileEditorContainer;
    if (!editorContainer) return;

    // 设置编辑器容器样式
    editorContainer.style.position = 'fixed';
    editorContainer.style.top = '60px'; // 导航栏下方
    editorContainer.style.left = '0';
    editorContainer.style.right = '0';
    editorContainer.style.bottom = '0';
    editorContainer.style.backgroundColor = '#fff';
    editorContainer.style.zIndex = '1050';
    editorContainer.style.display = 'none';

    // 设置编辑器工具栏样式
    const toolbar = editorContainer.querySelector('.editor-toolbar');
    if (toolbar) {
        toolbar.style.padding = '10px';
        toolbar.style.borderBottom = '1px solid #dee2e6';
        toolbar.style.backgroundColor = '#f8f9fa';
    }

    // 设置编辑器主区域样式
    const editorMain = editorContainer.querySelector('.editor-main');
    if (editorMain) {
        editorMain.style.display = 'flex';
        editorMain.style.height = 'calc(100% - 60px)'; // 减去工具栏高度
        editorMain.style.overflow = 'hidden';
    }

    // 设置行号区域样式
    if (elements.lineNumbers) {
        elements.lineNumbers.style.width = '50px';
        elements.lineNumbers.style.padding = '10px 5px';
        elements.lineNumbers.style.overflow = 'hidden';
        elements.lineNumbers.style.textAlign = 'right';
        elements.lineNumbers.style.color = '#6c757d';
        elements.lineNumbers.style.backgroundColor = '#f8f9fa';
        elements.lineNumbers.style.borderRight = '1px solid #dee2e6';
        elements.lineNumbers.style.userSelect = 'none';
    }

    // 设置编辑器样式
    if (elements.fileEditor) {
        elements.fileEditor.style.flex = '1';
        elements.fileEditor.style.padding = '10px';
        elements.fileEditor.style.border = 'none';
        elements.fileEditor.style.resize = 'none';
        elements.fileEditor.style.outline = 'none';
        elements.fileEditor.style.fontSize = '1rem';
        elements.fileEditor.style.lineHeight = '1.5';
        elements.fileEditor.style.fontFamily = 'monospace';
        elements.fileEditor.style.whiteSpace = 'pre';
        elements.fileEditor.style.overflowX = 'auto';
    }
}

// 提取完整的extractArchive函数
function extractArchive() {
    console.log('开始执行extractArchive函数');

    // 直接获取DOM元素，避免引用问题
    const extractFilePath = document.getElementById('extract-file-path');
    const extractDestination = document.getElementById('extract-destination');
    const extractOverwrite = document.getElementById('extract-overwrite');
    const extractProgressContainer = document.getElementById('extract-progress-container');
    const extractStatus = document.getElementById('extract-status');
    const btnStartExtract = document.getElementById('btn-start-extract');

    console.log('DOM元素直接获取状态:', {
        extractFilePath: extractFilePath,
        extractDestination: extractDestination,
        extractOverwrite: extractOverwrite
    });

    // 尝试从sessionStorage获取路径作为备选
    let filePath = extractFilePath ? extractFilePath.value : null;
    if (!filePath && sessionStorage.getItem('extractFilePath')) {
        filePath = sessionStorage.getItem('extractFilePath');
        console.log('从sessionStorage获取文件路径:', filePath);

        // 如果input元素存在但为空，设置从sessionStorage获取的值
        if (extractFilePath && !extractFilePath.value) {
            extractFilePath.value = filePath;
            console.log('已设置从sessionStorage获取的文件路径到input:', filePath);
        }
    }

    console.log('解压函数获取到的文件路径:', filePath);

    if (!filePath) {
        console.error('未指定压缩文件路径');
        showToast('解压失败', '未指定压缩文件', 'error');
        return;
    }

    let destination = extractDestination ? extractDestination.value || '' : '';
    const overwrite = extractOverwrite ? extractOverwrite.checked : false;
    console.log('目标路径:', destination, '覆盖文件:', overwrite);

    // 如果不是以当前路径开始，添加当前路径前缀
    if (destination && !destination.startsWith('/')) {
        destination = currentPath + (currentPath.endsWith('/') ? '' : '/') + destination;
    } else if (!destination) {
        destination = currentPath;
    }

    console.log('最终解压参数:', {
        文件路径: filePath,
        目标路径: destination,
        覆盖已有文件: overwrite
    });

    // 禁用按钮防止重复提交
    if (btnStartExtract) btnStartExtract.disabled = true;

    // 显示进度容器
    if (extractProgressContainer) {
        extractProgressContainer.style.display = 'block';
    }

    // 显示状态信息
    if (extractStatus) {
        extractStatus.textContent = '正在解压文件...';
        extractStatus.style.display = 'block';
    }

    // 创建并发送请求
    const formData = new FormData();
    formData.append('file_path', filePath);
    formData.append('destination', destination);
    formData.append('overwrite', overwrite);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/files/extract', true);

    // 设置进度处理
    xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) {
            const percent = Math.round((e.loaded / e.total) * 100);
            const progressBar = document.getElementById('extract-progress-bar');
            if (progressBar) {
                progressBar.style.width = percent + '%';
                progressBar.setAttribute('aria-valuenow', percent);
            }
        }
    };

    // 设置完成处理
    xhr.onload = function() {
        console.log('解压请求完成，状态:', xhr.status);

        // 启用按钮
        if (btnStartExtract) btnStartExtract.disabled = false;

        if (xhr.status === 200) {
            try {
                const response = JSON.parse(xhr.responseText);
                console.log('解压响应:', response);

                if (response.success) {
                    // 解压成功
                    if (extractStatus) {
                        extractStatus.textContent = '解压完成!';
                        extractStatus.className = 'alert alert-success';
                    }

                    showToast('解压成功', '文件已解压到 ' + destination, 'success');

                    // 3秒后关闭模态框并刷新文件列表
                    setTimeout(function() {
                        if (window.extractModalInstance) {
                            window.extractModalInstance.hide();
                        } else {
                            const extractModal = document.getElementById('extractModal');
                            if (extractModal) {
                                const modal = bootstrap.Modal.getInstance(extractModal);
                                if (modal) modal.hide();
                            }
                        }
                        loadFiles(currentPath);
                    }, 3000);
                } else {
                    // 解压失败
                    if (extractStatus) {
                        extractStatus.textContent = '解压失败: ' + (response.message || response.error || '未知错误');
                        extractStatus.className = 'alert alert-danger';
                    }

                    showToast('解压失败', response.message || response.error || '未知错误', 'error');
                }
            } catch (e) {
                console.error('解析响应出错:', e);
                if (extractStatus) {
                    extractStatus.textContent = '解压过程中发生错误: ' + e.message;
                    extractStatus.className = 'alert alert-danger';
                }

                showToast('解压失败', '解析响应出错: ' + e.message, 'error');
            }
        } else {
            // HTTP错误
            console.error('HTTP错误:', xhr.status);
            if (extractStatus) {
                extractStatus.textContent = '服务器错误: ' + xhr.status;
                extractStatus.className = 'alert alert-danger';
            }

            showToast('解压失败', '服务器错误: ' + xhr.status, 'error');
        }
    };

    // 设置错误处理
    xhr.onerror = function() {
        console.error('网络错误');

        // 启用按钮
        if (btnStartExtract) btnStartExtract.disabled = false;

        if (extractStatus) {
            extractStatus.textContent = '网络错误，请重试';
            extractStatus.className = 'alert alert-danger';
        }

        showToast('解压失败', '网络错误，请重试', 'error');
    };

    // 设置取消处理
    xhr.onabort = function() {
        console.log('解压请求已取消');

        // 启用按钮
        if (btnStartExtract) btnStartExtract.disabled = false;

        if (extractStatus) {
            extractStatus.textContent = '解压已取消';
            extractStatus.className = 'alert alert-warning';
        }
    };

    // 发送请求
    try {
        xhr.send(formData);
        console.log('解压请求已发送');
    } catch (e) {
        console.error('发送解压请求失败:', e);

        // 启用按钮
        if (btnStartExtract) btnStartExtract.disabled = false;

        if (extractStatus) {
            extractStatus.textContent = '发送请求失败: ' + e.message;
            extractStatus.className = 'alert alert-danger';
        }

        showToast('解压失败', '发送请求失败: ' + e.message, 'error');
    }
}

// 重置解压表单状态
function resetExtractForm(resetFilePath = true) {
    console.log('重置解压表单，重置文件路径:', resetFilePath);

    // 获取DOM元素
    const extractProgressContainer = document.getElementById('extract-progress-container');
    const extractStatus = document.getElementById('extract-status');
    const extractFilePath = document.getElementById('extract-file-path');

    // 隐藏进度条
    if (extractProgressContainer) {
        extractProgressContainer.style.display = 'none';
    }

    // 清除状态消息
    if (extractStatus) {
        extractStatus.textContent = '';
    }

    // 根据参数决定是否清除文件路径
    if (resetFilePath && extractFilePath) {
        extractFilePath.value = '';
        console.log('已清除文件路径');
    }
}

// 显示解压对话框
function showExtractDialog(filePath) {
    console.log('开始显示解压对话框，文件路径:', filePath);

    // 从sessionStorage获取路径，以防传递中丢失
    if (!filePath && sessionStorage.getItem('extractFilePath')) {
        filePath = sessionStorage.getItem('extractFilePath');
        console.log('从sessionStorage获取解压文件路径:', filePath);
    }

    if (!filePath) {
        console.error('解压对话框未指定文件路径');
        showToast('解压错误', '未指定文件路径', 'error');
        return;
    }

    // 获取DOM元素
    const extractFilePathInput = document.getElementById('extract-file-path');
    console.log('解压文件路径Input元素:', extractFilePathInput);

    // 设置文件路径到隐藏的input中
    if (extractFilePathInput) {
        extractFilePathInput.value = filePath;
        console.log('已设置解压文件路径:', filePath);
    } else {
        console.error('找不到extract-file-path元素');
    }

    // 设置默认解压目标为当前目录
    const extractDestination = document.getElementById('extract-destination');
    if (extractDestination) {
        // 默认解压到当前目录
        extractDestination.value = '';
        console.log('设置默认解压目标为当前目录');
    }

    // 重置表单状态，但不清除文件路径
    resetExtractForm(false);

    // 显示模态窗口
    try {
        const extractModal = document.getElementById('extractModal');
        console.log('解压模态窗口元素:', extractModal);

        if (window.extractModalInstance) {
            console.log('使用Bootstrap Modal实例显示窗口');
            window.extractModalInstance.show();
        } else if (extractModal) {
            console.log('尝试初始化并显示Modal');
            const modal = new bootstrap.Modal(extractModal);
            window.extractModalInstance = modal;
            modal.show();
        } else {
            console.error('找不到extractModal元素');
        }
    } catch (error) {
        console.error('显示解压模态窗口时出错:', error);
        // 尝试使用jQuery作为备选方案
        try {
            $('#extractModal').modal('show');
            console.log('使用jQuery显示Modal');
        } catch (e) {
            console.error('jQuery模态窗口显示失败:', e);
            showToast('错误', '无法显示解压对话框', 'error');
        }
    }
}

// 修改上传文件函数
function uploadFiles() {
    // 确定当前选择的上传类型
    let uploadType = "files";
    let uploadFiles = null;

    if (elements.uploadTypeFiles && elements.uploadTypeFiles.checked) {
        uploadType = "files";
        uploadFiles = elements.uploadFiles;
    } else if (elements.uploadTypeFolder && elements.uploadTypeFolder.checked) {
        uploadType = "folder";
        uploadFiles = elements.uploadFolder;
    } else if (elements.uploadTypeZip && elements.uploadTypeZip.checked) {
        uploadType = "zip";
        uploadFiles = elements.uploadZip;
    } else {
        // 默认使用文件上传
        uploadFiles = elements.uploadFiles;
    }

    if (!uploadFiles || !uploadFiles.files.length) {
        showToast('上传失败', '请选择要上传的文件', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('path', currentPath);
    formData.append('upload_type', uploadType);

    // 添加自动解压选项（如果选中）
    if (uploadType === "zip" && elements.autoExtract && elements.autoExtract.checked) {
        formData.append('auto_extract', 'true');
    }

    // 添加所有选择的文件
    for (let i = 0; i < uploadFiles.files.length; i++) {
        formData.append('files', uploadFiles.files[i]);
    }

    // 显示更详细的信息
    console.log('准备上传文件:', {
        路径: currentPath,
        类型: uploadType,
        文件数量: uploadFiles.files.length,
        文件列表: Array.from(uploadFiles.files).map(f => f.name)
    });

    // 显示进度条
    elements.uploadProgressContainer.classList.remove('d-none');
    elements.uploadStatus.classList.remove('d-none');
    elements.uploadStatus.classList.remove('alert-success', 'alert-danger');
    elements.uploadStatus.classList.add('alert-info');
    elements.uploadStatus.textContent = '正在上传文件...';

    // 设置上传按钮为禁用状态
    elements.btnStartUpload.disabled = true;

    // 根据文件管理器其他API调用模式，扩展尝试的URL列表
    const possibleUrls = [
        // 优先尝试新添加的简化上传API路径
        '/upload',
        '/api/upload',
        // 然后是其他可能的路径
        '/api/files/upload',
        '../api/files/upload',
        'api/files/upload',
        '/file-manager/api/files/upload',
        '/apis/files/upload',
        `${window.location.protocol}//${window.location.host}/upload`,
        `${window.location.protocol}//${window.location.host}/api/upload`,
        `/api/files/upload?path=${encodeURIComponent(currentPath)}`
    ];

    // 将本地存储的成功URL放在最前面尝试
    const lastSuccessfulUrl = localStorage.getItem('lastSuccessfulUploadUrl');
    if (lastSuccessfulUrl) {
        possibleUrls.unshift(lastSuccessfulUrl);
        console.log('使用之前成功的URL:', lastSuccessfulUrl);
    }

    // 尝试所有可能的URL
    tryUploadWithUrls(formData, possibleUrls, 0);
}

// 逐个尝试所有可能的URL进行上传
function tryUploadWithUrls(formData, urlList, index) {
    if (index >= urlList.length) {
        // 所有URL都已尝试且失败
        elements.uploadStatus.classList.remove('alert-info');
        elements.uploadStatus.classList.add('alert-danger');
        elements.uploadStatus.textContent = '所有上传尝试均失败，可能原因: 服务器上传API未启用或路径不匹配';
        elements.btnStartUpload.disabled = false;

        // 显示详细错误信息和建议
        console.error('所有上传URL尝试均失败', {
            尝试的URL列表: urlList,
            可能的原因: [
                '服务器上的上传API未正确实现或注册',
                'API路径与尝试的所有路径都不匹配',
                '服务器配置问题或防火墙限制',
                '会话验证失败或权限问题'
            ],
            建议解决方案: [
                '检查服务器日志以确定确切的上传端点路径',
                '在服务器中实现一个简单的上传API进行测试',
                '使用开发者工具检查网络请求，查看其他API的确切路径'
            ]
        });

        showToast('上传失败', '无法找到有效的上传API，请联系管理员检查服务器日志', 'error');
        return;
    }

    const currentUrl = urlList[index];
    console.log(`尝试使用URL(${index + 1}/${urlList.length}): ${currentUrl}`);
    elements.uploadStatus.textContent = `尝试API路径 ${index + 1}/${urlList.length}...`;

    // 创建新的XMLHttpRequest对象
    const xhr = new XMLHttpRequest();

    // 监听上传进度
    xhr.upload.addEventListener('progress', function(e) {
        if (e.lengthComputable) {
            const percentComplete = Math.round((e.loaded / e.total) * 100);
            elements.uploadProgressBar.style.width = percentComplete + '%';
            elements.uploadProgressBar.setAttribute('aria-valuenow', percentComplete);
            console.log(`上传进度: ${percentComplete}%`);
        }
    });

    // 上传完成事件
    xhr.addEventListener('load', function() {
        console.log('上传请求完成:', {
            URL: currentUrl,
            状态码: xhr.status,
            状态文本: xhr.statusText,
            响应文本: xhr.responseText.substring(0, 200) + (xhr.responseText.length > 200 ? '...' : '')
        });

        if (xhr.status === 404) {
            // 404错误，尝试下一个URL
            console.warn(`URL ${currentUrl} 返回404，尝试下一个URL`);
            elements.uploadStatus.textContent = `API路径 ${index + 1}/${urlList.length} 不存在，尝试其他路径...`;
            tryUploadWithUrls(formData, urlList, index + 1);
            return;
        }

        if (xhr.status >= 200 && xhr.status < 300) {
            try {
                const response = JSON.parse(xhr.responseText);
                console.log('解析响应成功:', response);

                // 保存成功的URL以供将来使用
                localStorage.setItem('lastSuccessfulUploadUrl', currentUrl);
                console.log('已保存成功的上传URL:', currentUrl);

                if (response.success) {
                    elements.uploadStatus.classList.remove('alert-info');
                    elements.uploadStatus.classList.add('alert-success');
                    elements.uploadStatus.textContent = response.message;

                    // 重新加载当前目录
                    setTimeout(function() {
                        loadFiles(currentPath);

                        // 在上传成功后1.5秒关闭模态框
                        setTimeout(function() {
                            const uploadModal = bootstrap.Modal.getInstance(elements.uploadFileModal);
                            if (uploadModal) {
                                uploadModal.hide();
                            }

                            // 重置上传表单
                            resetUploadForm();
                        }, 1500);
                    }, 500);

                    showToast('上传成功', response.message, 'success');
                } else {
                    elements.uploadStatus.classList.remove('alert-info');
                    elements.uploadStatus.classList.add('alert-danger');
                    elements.uploadStatus.textContent = response.message || '上传失败';
                    showToast('上传失败', response.message || '上传失败', 'error');
                }
            } catch (e) {
                console.error('解析响应失败:', e, '原始响应:', xhr.responseText);
                elements.uploadStatus.classList.remove('alert-info');
                elements.uploadStatus.classList.add('alert-danger');
                elements.uploadStatus.textContent = '解析响应失败: ' + e.message;
                showToast('上传失败', '解析响应失败: ' + e.message, 'error');
            }
        } else {
            console.error('上传失败，HTTP状态码:', xhr.status, xhr.statusText);
            elements.uploadStatus.classList.remove('alert-info');
            elements.uploadStatus.classList.add('alert-danger');
            elements.uploadStatus.textContent = '上传失败: ' + xhr.status + ' ' + xhr.statusText;

            // 如果是服务器错误(5xx)，尝试下一个URL
            if (xhr.status >= 500) {
                console.warn(`URL ${currentUrl} 返回服务器错误，尝试下一个URL`);
                tryUploadWithUrls(formData, urlList, index + 1);
                return;
            }

            // 如果是认证错误(401/403)，可能需要登录
            if (xhr.status === 401 || xhr.status === 403) {
                console.warn(`URL ${currentUrl} 返回认证错误，可能需要重新登录`);
                elements.uploadStatus.textContent = '认证失败，请重新登录后再试';
                showToast('上传失败', '认证失败，请重新登录后再试', 'error');
                return;
            }

            showToast('上传失败', '状态码: ' + xhr.status + ' - ' + xhr.statusText, 'error');
        }

        // 重置上传按钮状态
        elements.btnStartUpload.disabled = false;
    });

    // 上传错误事件
    xhr.addEventListener('error', function(e) {
        console.error('网络错误:', e);
        elements.uploadStatus.classList.remove('alert-info');
        elements.uploadStatus.classList.add('alert-danger');
        elements.uploadStatus.textContent = '网络错误，尝试其他URL...';

        // 网络错误，尝试下一个URL
        console.warn(`URL ${currentUrl} 发生网络错误，尝试下一个URL`);
        tryUploadWithUrls(formData, urlList, index + 1);
    });

    // 上传被中止
    xhr.addEventListener('abort', function() {
        console.warn('上传已中止');
        elements.uploadStatus.classList.remove('alert-info');
        elements.uploadStatus.classList.add('alert-danger');
        elements.uploadStatus.textContent = '上传已中止';

        // 重置上传按钮状态
        elements.btnStartUpload.disabled = false;
    });

    // 发送请求
    try {
        xhr.open('POST', currentUrl, true);
        // 设置可能需要的跨域相关头部
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.send(formData);
    } catch (e) {
        console.error('发送请求时出错:', e);
        elements.uploadStatus.classList.remove('alert-info');
        elements.uploadStatus.classList.add('alert-danger');
        elements.uploadStatus.textContent = '发送请求失败，尝试其他URL...';

        // 发送失败，尝试下一个URL
        tryUploadWithUrls(formData, urlList, index + 1);
    }
}

// 重置上传表单
function resetUploadForm() {
    if (elements.uploadFiles) {
        elements.uploadFiles.value = '';
    }

    elements.uploadProgressContainer.classList.add('d-none');
    elements.uploadStatus.classList.add('d-none');
    elements.uploadProgressBar.style.width = '0%';
    elements.uploadProgressBar.setAttribute('aria-valuenow', 0);
    elements.btnStartUpload.disabled = false;
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    console.log('文件管理器DOM加载完成，准备初始化...');

    setTimeout(() => {
        try {
            // 检查Bootstrap是否存在
            if (typeof bootstrap === 'undefined') {
                console.error('Bootstrap未加载！模态框将无法正常工作');
                alert('系统错误：Bootstrap未加载，部分功能可能无法正常工作。请刷新页面或联系管理员。');

                // 尝试重新加载Bootstrap
                const scriptEl = document.createElement('script');
                scriptEl.src = 'https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js';
                document.body.appendChild(scriptEl);

                scriptEl.onload = () => {
                    console.log('Bootstrap重新加载成功，尝试初始化...');
                    init();
                };

                return;
            }

            // 检查关键DOM元素是否存在
            const elementCheck = {
                'fileList': elements.fileList,
                'folderTree': elements.folderTree,
                'newFileModal': elements.newFileModal,
                'newFolderModal': elements.newFolderModal,
                'btnNewFile': elements.btnNewFile,
                'btnNewFolder': elements.btnNewFolder
            };

            // 记录缺失的元素
            const missingElements = Object.entries(elementCheck)
                .filter(([_, element]) => !element || (Array.isArray(element) && element.length === 0))
                .map(([name]) => name);

            if (missingElements.length > 0) {
                console.error('以下DOM元素未找到:', missingElements.join(', '));

                // 特别检查按钮选择器
                if (missingElements.includes('btnNewFile') || missingElements.includes('btnNewFolder')) {
                    console.info('尝试使用类选择器重新获取按钮元素...');

                    // 尝试通过类选择器重新获取按钮
                    elements.btnNewFile = document.querySelectorAll('.btn-new-file');
                    elements.btnNewFolder = document.querySelectorAll('.btn-new-folder');

                    console.log('通过类选择器找到的新建文件按钮:', elements.btnNewFile?.length || 0);
                    console.log('通过类选择器找到的新建文件夹按钮:', elements.btnNewFolder?.length || 0);
                }
            }

            // 初始化
            init();

            // 添加全局调试函数
            window.debugFileManager = {
                showNewFileModal: () => {
                    console.log('手动调用显示新建文件模态框');
                    showNewFileModal();
                },
                showNewFolderModal: () => {
                    console.log('手动调用显示新建文件夹模态框');
                    showNewFolderModal();
                },
                getElements: () => {
                    console.log('当前文件管理器元素状态:', elements);
                    return elements;
                },
                getModals: () => {
                    console.log('当前模态框状态:', modals);
                    return modals;
                },
                reinitModals: () => {
                    console.log('尝试重新初始化所有模态框');
                    if (elements.newFileModal) modals.newFile = new bootstrap.Modal(elements.newFileModal);
                    if (elements.newFolderModal) modals.newFolder = new bootstrap.Modal(elements.newFolderModal);
                    if (elements.deleteConfirmModal) modals.deleteConfirm = new bootstrap.Modal(elements.deleteConfirmModal);
                    if (elements.renameModal) modals.rename = new bootstrap.Modal(elements.renameModal);
                    return modals;
                }
            };

            console.log('文件管理器初始化完成，可以通过window.debugFileManager访问调试功能');
        } catch (error) {
            console.error('文件管理器初始化失败:', error);
            alert(`文件管理器初始化失败: ${error.message}`);
        }
    }, 500); // 延迟500毫秒确保DOM完全加载
});

// 创建文件/文件夹的上下文菜单
function createContextMenu(fileItem) {
    // 记录正在创建上下文菜单与文件项
    const fileItemData = fileItem.dataset;
    console.log('创建上下文菜单:', fileItemData);
    console.log('文件名:', fileItemData.name);
    console.log('文件路径:', fileItemData.path);
    console.log('文件类型:', fileItemData.type);

    // 创建菜单
    const menu = document.createElement('div');
    menu.className = 'context-menu';
    menu.id = 'context-menu';

    // 默认选项
    const menuItems = [
        { text: '下载', action: () => downloadFile(fileItemData.path) },
        { text: '重命名', action: () => showRenameDialog(fileItemData.path) },
        { text: '删除', action: () => showDeleteConfirmation(fileItemData.path) },
    ];

    // 检查是否是可编辑的文本文件
    if (fileItemData.type === 'file' && isTextFile(fileItemData.name)) {
        menuItems.unshift({ text: '编辑', action: () => editTextFile(fileItemData.path) });
    }

    // 解压选项 - 检查是否是压缩文件
    const archiveExtensions = ['.zip', '.tar', '.gz', '.7z', '.rar'];
    const isArchive = archiveExtensions.some(ext => fileItemData.name.toLowerCase().endsWith(ext));

    if (fileItemData.type === 'file' && isArchive) {
        menuItems.push({
            text: '解压',
            action: () => {
                // 保存文件路径到sessionStorage，避免传递问题
                console.log('准备解压文件:', fileItemData.path);
                sessionStorage.setItem('extractFilePath', fileItemData.path);
                showExtractDialog(fileItemData.path);
            }
        });
    }

    // ... 其余菜单项代码保持不变 ...
    // 将菜单项加入菜单
    menuItems.forEach(item => {
        const menuItem = document.createElement('div');
        menuItem.className = 'context-menu-item';
        menuItem.textContent = item.text;
        menuItem.addEventListener('click', () => {
            item.action();
            hideContextMenu();
        });
        menu.appendChild(menuItem);
    });

    return menu;
}

// 隐藏上下文菜单
function hideContextMenu() {
    const contextMenu = document.querySelector('.context-menu.show');
    if (contextMenu) {
        contextMenu.classList.remove('show');
        contextMenu.remove();
    }
}

// 显示上下文菜单
function showContextMenu(e, item) {
    // 先隐藏已有的上下文菜单
    hideContextMenu();

    // 创建新的上下文菜单
    const contextMenu = createContextMenu(item);

    // 设置菜单位置并显示
    contextMenu.style.position = 'absolute';
    contextMenu.style.left = `${e.pageX}px`;
    contextMenu.style.top = `${e.pageY}px`;
    contextMenu.classList.add('show');

    // 添加到文档
    document.body.appendChild(contextMenu);

    // 点击其他地方时隐藏菜单
    setTimeout(() => {
        document.addEventListener('click', hideContextMenu, { once: true });
    }, 0);
}

// 在文件结尾添加初始化函数，确保上传类型选择器正常工作
document.addEventListener('DOMContentLoaded', function() {
    // 初始化上传选项
    initUploadOptions();

    // 全局标记文件管理器已初始化
    window.fileManagerInitialized = true;
    window.dispatchEvent(new Event('fileManagerLoaded'));
});

// 初始化上传选项函数
function initUploadOptions() {
    console.log('初始化上传选项');

    // 检查是否支持目录上传
    const testInput = document.createElement('input');
    testInput.type = 'file';
    const isDirSupported = 'webkitdirectory' in testInput || 'directory' in testInput;

    if (!isDirSupported) {
        console.warn('当前浏览器不支持文件夹上传功能');

        // 如果存在文件夹上传选项，设置警告信息
        if (elements.uploadTypeFolder) {
            elements.uploadTypeFolder.disabled = true;

            // 添加提示
            const folderOption = elements.uploadTypeFolder.closest('label');
            if (folderOption) {
                folderOption.setAttribute('title', '当前浏览器不支持此功能');
                folderOption.style.opacity = '0.6';
                folderOption.style.cursor = 'not-allowed';
            }
        }
    }

    // 确保上传选项正确初始化
    updateUploadUI();
}

// 更新上传UI函数
function updateUploadUI() {
    // 确保上传类型对应的选择器正确显示
    if (elements.uploadTypeFiles && elements.uploadTypeFiles.checked) {
        if (elements.fileSelector) elements.fileSelector.style.display = 'block';
        if (elements.folderSelector) elements.folderSelector.style.display = 'none';
        if (elements.zipSelector) elements.zipSelector.style.display = 'none';
        if (elements.extractOption) elements.extractOption.style.display = 'none';
    } else if (elements.uploadTypeFolder && elements.uploadTypeFolder.checked) {
        if (elements.fileSelector) elements.fileSelector.style.display = 'none';
        if (elements.folderSelector) elements.folderSelector.style.display = 'block';
        if (elements.zipSelector) elements.zipSelector.style.display = 'none';
        if (elements.extractOption) elements.extractOption.style.display = 'none';
    } else if (elements.uploadTypeZip && elements.uploadTypeZip.checked) {
        if (elements.fileSelector) elements.fileSelector.style.display = 'none';
        if (elements.folderSelector) elements.folderSelector.style.display = 'none';
        if (elements.zipSelector) elements.zipSelector.style.display = 'block';
        if (elements.extractOption) elements.extractOption.style.display = 'block';
    }

    console.log('上传UI已更新');
}

// 确保在DOMContentLoaded后重新获取DOM元素引用
document.addEventListener('DOMContentLoaded', function() {
    console.log('文档加载完成，重新获取DOM元素引用');

    // 重新获取解压相关元素
    elements.extractFileModal = document.getElementById('extract-modal');
    elements.extractFilePath = document.getElementById('extract-file-path');
    elements.extractDestination = document.getElementById('extract-destination');
    elements.extractOverwrite = document.getElementById('extract-overwrite');
    elements.extractProgressContainer = document.getElementById('extract-progress-container');
    elements.extractProgressBar = document.getElementById('extract-progress-bar');
    elements.extractStatus = document.getElementById('extract-status');
    elements.btnStartExtract = document.getElementById('btn-start-extract');

    console.log('DOM元素引用已更新', {
        extractFileModal: elements.extractFileModal,
        extractFilePath: elements.extractFilePath,
        extractDestination: elements.extractDestination,
        extractOverwrite: elements.extractOverwrite,
        btnStartExtract: elements.btnStartExtract
    });

    // 重新初始化解压按钮点击事件
    if (elements.btnStartExtract) {
        console.log('重新绑定解压按钮事件');
        elements.btnStartExtract.addEventListener('click', function() {
            console.log('解压按钮被点击');
            extractArchive();
        });
    }
});

// 在文件顶部的CSS部分添加
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOMContentLoaded事件触发，开始绑定事件和初始化控件');

    // 添加上下文菜单的样式
    const style = document.createElement('style');
    style.textContent = `
        .context-menu {
            position: absolute;
            background-color: #ffffff;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
            padding: 5px 0;
            min-width: 150px;
            z-index: 1000;
        }

        .context-menu-item {
            padding: 8px 16px;
            cursor: pointer;
            transition: background-color 0.2s;
        }

        .context-menu-item:hover {
            background-color: #f0f0f0;
        }
    `;
    document.head.appendChild(style);

    // 获取解压相关DOM元素并绑定事件
    const btnStartExtract = document.getElementById('btn-start-extract');
    if (btnStartExtract) {
        console.log('找到解压按钮，绑定点击事件');
        btnStartExtract.addEventListener('click', extractArchive);
    } else {
        console.error('未找到解压按钮元素');
    }

    // 绑定其他文件管理按钮事件
    // ...
});

// 定义文本文件检查函数
function isTextFile(filename) {
    if (!filename) return false;

    // 获取文件扩展名
    const extension = filename.split('.').pop().toLowerCase();

    // 检查是否在可编辑文本文件扩展名列表中
    return textFileExtensions.includes(extension);
}

// 辅助函数：下载文件
function downloadFile(path) {
    if (!path) return;

    console.log('下载文件:', path);

    // 创建一个临时的a标签进行下载
    const a = document.createElement('a');
    a.href = `/api/files/download?path=${encodeURIComponent(path)}`;
    a.download = path.split('/').pop();
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    showToast('下载', '正在下载文件...', 'info');
}

// 辅助函数：编辑文本文件
function editTextFile(path) {
    if (!path) return;

    console.log('编辑文本文件:', path);

    // 获取文件名
    const filename = path.split('/').pop();

    // 调用已有的编辑器函数
    openEditor(path, filename);
}

// 辅助函数：显示重命名对话框
function showRenameDialog(path) {
    if (!path) return;

    console.log('显示重命名对话框:', path);

    // 获取文件名
    const filename = path.split('/').pop();

    // 调用已有的重命名函数
    showRenameModal(path, filename);
}

// 辅助函数：显示删除确认对话框
function showDeleteConfirmation(path) {
    if (!path) return;

    console.log('显示删除确认对话框:', path);

    // 获取文件名和类型
    const filename = path.split('/').pop();
    const isDirectory = path.endsWith('/');

    // 调用已有的删除函数
    showDeleteModal(path, filename, isDirectory);
}

// 添加额外的DOMContentLoaded事件处理程序，确保模态框元素被正确获取
document.addEventListener('DOMContentLoaded', function() {
    console.log('模态框元素检查和重新获取');

    // 重新获取模态框元素
    elements.newFileModal = document.getElementById('new-file-modal');
    elements.newFolderModal = document.getElementById('new-folder-modal');
    elements.deleteConfirmModal = document.getElementById('delete-confirm-modal');
    elements.renameModal = document.getElementById('rename-modal');
    elements.uploadFileModal = document.getElementById('upload-file-modal');

    console.log('模态框元素状态:', {
        newFileModal: elements.newFileModal ? '已找到' : '未找到',
        newFolderModal: elements.newFolderModal ? '已找到' : '未找到',
        deleteConfirmModal: elements.deleteConfirmModal ? '已找到' : '未找到',
        renameModal: elements.renameModal ? '已找到' : '未找到',
        uploadFileModal: elements.uploadFileModal ? '已找到' : '未找到'
    });

    // 如果找到了模态框元素，重新初始化模态框实例
    if (elements.newFileModal && typeof bootstrap !== 'undefined') {
        try {
            modals.newFile = new bootstrap.Modal(elements.newFileModal);
            console.log('新建文件模态框重新初始化成功');
        } catch (e) {
            console.error('重新初始化新建文件模态框失败:', e);
        }
    }

    if (elements.newFolderModal && typeof bootstrap !== 'undefined') {
        try {
            modals.newFolder = new bootstrap.Modal(elements.newFolderModal);
            console.log('新建文件夹模态框重新初始化成功');
        } catch (e) {
            console.error('重新初始化新建文件夹模态框失败:', e);
        }
    }
});