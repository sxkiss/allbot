/**
 * Bootstrap模态窗口管理工具
 * 用于统一管理模态窗口，避免backdrop堆积问题
 */

// 初始化模态窗口管理器
document.addEventListener('DOMContentLoaded', function() {
    console.log('初始化模态窗口管理器');

    // 为所有模态窗口添加事件监听器
    const modals = document.querySelectorAll('.modal');

    // 确保所有模态框都有modal-dialog-centered类
    modals.forEach(modal => {
        const dialog = modal.querySelector('.modal-dialog');
        if (dialog && !dialog.classList.contains('modal-dialog-centered')) {
            console.log('添加modal-dialog-centered类到模态框:', modal.id);
            dialog.classList.add('modal-dialog-centered');
        }
    });

    modals.forEach(modal => {
        // 在模态窗口显示前
        modal.addEventListener('show.bs.modal', function(event) {
            console.log(`模态窗口即将显示: ${this.id}`);

            // 获取页面中所有已显示的模态窗口
            const visibleModals = document.querySelectorAll('.modal.show');

            // 如果有其他模态窗口已显示，先隐藏它们
            if (visibleModals.length > 0) {
                console.log('检测到其他模态窗口已打开，先关闭它们');
                visibleModals.forEach(visibleModal => {
                    // 跳过当前正在打开的模态窗口
                    if (visibleModal.id !== this.id) {
                        const modalInstance = bootstrap.Modal.getInstance(visibleModal);
                        if (modalInstance) {
                            // 临时禁用动画以快速关闭
                            modalInstance._config.backdrop = false;
                            modalInstance.hide();
                            // 恢复原有设置
                            setTimeout(() => {
                                modalInstance._config.backdrop = true;
                            }, 300);
                        }
                    }
                });
            }
        });

        // 在模态窗口隐藏后
        modal.addEventListener('hidden.bs.modal', function(event) {
            console.log(`模态窗口已隐藏: ${this.id}`);

            // 确保body状态正确
            setTimeout(() => {
                const visibleModals = document.querySelectorAll('.modal.show');
                if (visibleModals.length === 0) {
                    // 没有显示的模态窗口，恢复body状态
                    document.body.classList.remove('modal-open');
                    document.body.style.overflow = '';
                    document.body.style.paddingRight = '';

                    // 确保不存在残留的backdrop
                    const backdrops = document.querySelectorAll('.modal-backdrop');
                    if (backdrops.length > 0) {
                        console.log('检测到残留的backdrop，正在清理');
                        backdrops.forEach(backdrop => {
                            backdrop.remove();
                        });
                    }
                }
            }, 100);
        });
    });

    // 替换页面中可能存在的手动打开/关闭模态窗口的函数
    window.openModalManually = function(modalId) {
        const modalEl = document.getElementById(modalId);
        if (!modalEl) {
            console.error(`找不到模态框: ${modalId}`);
            return false;
        }

        try {
            // 使用Bootstrap API
            const modalInstance = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
            modalInstance.show();
            return true;
        } catch (error) {
            console.error(`打开模态框失败: ${modalId}`, error);
            return false;
        }
    };

    window.closeModalManually = function(modalId) {
        const modalEl = document.getElementById(modalId);
        if (!modalEl) {
            console.error(`找不到模态框: ${modalId}`);
            return false;
        }

        try {
            // 使用Bootstrap API
            const modalInstance = bootstrap.Modal.getInstance(modalEl);
            if (modalInstance) {
                modalInstance.hide();
            }
            return true;
        } catch (error) {
            console.error(`关闭模态框失败: ${modalId}`, error);
            return false;
        }
    };

    // 添加修复残留backdrop的函数
    window.cleanupModalBackdrops = function() {
        // 检查并移除残留的backdrop
        const backdrops = document.querySelectorAll('.modal-backdrop');
        if (backdrops.length > 0) {
            console.log(`清理${backdrops.length}个残留的backdrop`);
            backdrops.forEach(backdrop => backdrop.remove());
        }

        // 检查body状态
        const visibleModals = document.querySelectorAll('.modal.show');
        if (visibleModals.length === 0 && document.body.classList.contains('modal-open')) {
            console.log('修复body状态');
            document.body.classList.remove('modal-open');
            document.body.style.overflow = '';
            document.body.style.paddingRight = '';
        }
    };

    // 添加ESC键全局处理（确保只有最上层的模态窗口响应）
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            const visibleModals = document.querySelectorAll('.modal.show');
            if (visibleModals.length > 0) {
                // 只关闭z-index最高的模态窗口
                let highestModal = visibleModals[0];
                let highestZIndex = parseInt(getComputedStyle(highestModal).zIndex, 10) || 0;

                visibleModals.forEach(modal => {
                    const zIndex = parseInt(getComputedStyle(modal).zIndex, 10) || 0;
                    if (zIndex > highestZIndex) {
                        highestZIndex = zIndex;
                        highestModal = modal;
                    }
                });

                const modalInstance = bootstrap.Modal.getInstance(highestModal);
                if (modalInstance) {
                    modalInstance.hide();
                }

                // 阻止事件传播，避免多个模态窗口同时关闭
                event.stopPropagation();
            }
        }
    });

    // 每隔5秒检查一次是否有残留的backdrop
    setInterval(window.cleanupModalBackdrops, 5000);

    console.log('模态窗口管理器初始化完成');
});