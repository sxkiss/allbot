/*
 * Web 对话悬浮窗口（单会话）
 *
 * 设计目标：
 * - 后台全局悬浮图标
 * - 点击打开悬浮窗口，点击最小化恢复为图标
 * - 使用固定会话ID，与后端单会话模式一致
 */

(function () {
    const FIXED_SESSION_ID = "webchat";
    const POLL_INTERVAL_MS = 1500;

    let polling = false;
    let sending = false;
    let uploading = false;
    let pollTimer = null;

    let renderedMessageCount = 0;

    const fab = document.getElementById("webchat-widget-fab");
    const openBtn = document.getElementById("webchat-widget-open");
    const win = document.getElementById("webchat-widget-window");
    const header = document.getElementById("webchat-widget-header");
    const minimizeBtn = document.getElementById("webchat-widget-minimize");
    const messagesEl = document.getElementById("webchat-widget-messages");
    const uploadBtn = document.getElementById("webchat-widget-upload");
    const fileInput = document.getElementById("webchat-widget-file");
    const input = document.getElementById("webchat-widget-input");
    const sendBtn = document.getElementById("webchat-widget-send");

    if (!fab || !openBtn || !win || !header || !minimizeBtn || !messagesEl || !uploadBtn || !fileInput || !input || !sendBtn) {
        return;
    }

    function showToast(type, message) {
        if (typeof window.showToast === "function") {
            window.showToast(type === "error" ? "错误" : "提示", message, type === "error" ? "danger" : "info");
            return;
        }
        alert(message);
    }

    function isOpen() {
        if (win.classList.contains("webchat-widget-hidden")) {
            return false;
        }
        const style = window.getComputedStyle(win);
        return style.display !== "none" && style.visibility !== "hidden";
    }

    function resetWindowPosition() {
        win.style.left = "";
        win.style.top = "";
        win.style.right = "";
        win.style.bottom = "";
        win.style.transform = "";
        win.style.width = "";
        win.style.height = "";
        delete win.dataset.userMoved;
    }

    function openWindow() {
        try {
            fab.classList.add("webchat-widget-hidden");
            fab.style.display = "none";
            win.classList.remove("webchat-widget-hidden");
            win.style.display = "flex";
            win.style.visibility = "visible";
            win.style.opacity = "1";
            win.style.pointerEvents = "auto";
            win.setAttribute("aria-hidden", "false");
            if (!win.dataset.userMoved) {
                resetWindowPosition();
            }
            input.focus();
            const forceFull = renderedMessageCount === 0;
            if (forceFull) {
                renderLoading("加载中...");
            }
            refreshMessages({ forceFull });
            startPolling();
        } catch (err) {
            console.error("webchat open failed", err);
        }
    }

    function minimizeWindow(event) {
        if (event) {
            try {
                event.preventDefault();
                event.stopPropagation();
            } catch (_) {}
        }
        try {
            stopPolling();
        } catch (_) {}
        dragging = false;
        try {
            document.removeEventListener("mousemove", onMouseMove);
            document.removeEventListener("mouseup", onMouseUp);
            document.removeEventListener("pointermove", onMouseMove);
            document.removeEventListener("pointerup", onMouseUp);
        } catch (_) {}

        // 强制关闭：class + 内联样式双保险
        win.classList.add("webchat-widget-hidden");
        win.setAttribute("aria-hidden", "true");
        win.style.display = "none";
        win.style.visibility = "hidden";
        win.style.pointerEvents = "none";
        resetWindowPosition();

        fab.classList.remove("webchat-widget-hidden");
        fab.style.display = "";
        fab.style.visibility = "visible";
        fab.style.pointerEvents = "auto";
    }

    function scrollToBottom() {
        try {
            win.querySelector("#webchat-widget-body").scrollTop = win.querySelector("#webchat-widget-body").scrollHeight;
        } catch (_) {
            // ignore
        }
    }

    function isNearBottom(thresholdPx = 48) {
        try {
            const body = win.querySelector("#webchat-widget-body");
            if (!body) {
                return true;
            }
            return body.scrollTop + body.clientHeight >= body.scrollHeight - thresholdPx;
        } catch (_) {
            return true;
        }
    }

    function renderLoading(text) {
        messagesEl.innerHTML = `
            <div class="text-center text-muted py-4">
                <div class="spinner-border text-primary" role="status" aria-hidden="true"></div>
                <div class="mt-2">${text}</div>
            </div>
        `;
        renderedMessageCount = 0;
    }

    function renderEmpty() {
        messagesEl.innerHTML = `
            <div class="text-center text-muted py-4">
                <i class="bi bi-chat-dots" style="font-size: 2rem;"></i>
                <div class="mt-2">暂无消息</div>
            </div>
        `;
        renderedMessageCount = 0;
    }

    function appendMessage(message) {
        const role = message?.role || "bot";
        const type = message?.type || "text";
        const content = message?.content || "";
        const mediaUrl = message?.media_url || "";
        const filename = message?.filename || "";
        const timestamp = message?.timestamp || Math.floor(Date.now() / 1000);
        const timeStr = new Date(timestamp * 1000).toLocaleTimeString();

        const wrapper = document.createElement("div");
        wrapper.className = `webchat-widget-msg ${role}`;

        const body = document.createElement("div");

        if (type === "image" && mediaUrl) {
            const img = document.createElement("img");
            img.src = mediaUrl;
            img.alt = filename || "image";
            img.className = "webchat-widget-media";
            body.appendChild(img);
        } else if (type === "video" && mediaUrl) {
            const video = document.createElement("video");
            video.controls = true;
            video.src = mediaUrl;
            video.className = "webchat-widget-media";
            body.appendChild(video);
        } else if (type === "voice" && mediaUrl) {
            const audio = document.createElement("audio");
            audio.controls = true;
            audio.src = mediaUrl;
            body.appendChild(audio);
        } else if (type === "file" && mediaUrl) {
            const link = document.createElement("a");
            link.href = mediaUrl;
            link.target = "_blank";
            link.rel = "noopener";
            link.textContent = filename || content || "下载文件";
            body.appendChild(link);
        } else {
            body.textContent = content;
        }

        const time = document.createElement("div");
        time.className = "webchat-widget-time";
        time.textContent = timeStr;

        wrapper.appendChild(body);
        wrapper.appendChild(time);
        messagesEl.appendChild(wrapper);
    }

    function setInputEnabled(enabled) {
        input.disabled = !enabled;
        sendBtn.disabled = !enabled || sending || uploading;
        uploadBtn.disabled = !enabled || sending || uploading;
    }

    async function checkStatus() {
        try {
            const response = await fetch("/api/webchat/status", { credentials: "include" });
            const result = await response.json();
            if (!result.success || !result.data?.enabled) {
                setInputEnabled(false);
                renderedMessageCount = 0;
                messagesEl.innerHTML = `
                    <div class="text-center text-muted py-4">
                        <i class="bi bi-exclamation-triangle" style="font-size: 2rem; color: #dc3545;"></i>
                        <div class="mt-2 text-danger">Web适配器未启用</div>
                        <div class="mt-1">请先在适配器管理中启用 Web 适配器</div>
                    </div>
                `;
                return false;
            }
            setInputEnabled(true);
            return true;
        } catch (error) {
            setInputEnabled(false);
            renderedMessageCount = 0;
            showToast("error", "检查 Web 对话状态失败: " + (error?.message || error));
            return false;
        }
    }

    async function refreshMessages(options = {}) {
        if (!isOpen()) {
            return;
        }

        const ok = await checkStatus();
        if (!ok) {
            return;
        }

        if (polling) {
            return;
        }
        polling = true;
        try {
            const response = await fetch(`/api/webchat/sessions/${FIXED_SESSION_ID}`, { credentials: "include" });
            const result = await response.json();
            if (!result.success) {
                throw new Error(result.error || "加载消息失败");
            }
            const messages = result.data?.messages || [];

            const forceFull = Boolean(options.forceFull);
            const needFullRender = forceFull || renderedMessageCount === 0 || messages.length < renderedMessageCount;
            const shouldScroll = isNearBottom();

            if (needFullRender) {
                messagesEl.innerHTML = "";
                renderedMessageCount = 0;
                if (!messages.length) {
                    renderEmpty();
                    return;
                }
                for (const msg of messages) {
                    appendMessage(msg);
                }
                renderedMessageCount = messages.length;
                scrollToBottom();
                return;
            }

            for (let i = renderedMessageCount; i < messages.length; i++) {
                appendMessage(messages[i]);
            }
            if (messages.length !== renderedMessageCount) {
                renderedMessageCount = messages.length;
                if (shouldScroll) {
                    scrollToBottom();
                }
            }
        } catch (error) {
            messagesEl.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="bi bi-exclamation-triangle" style="font-size: 2rem; color: #dc3545;"></i>
                    <div class="mt-2">加载对话失败: ${String(error?.message || error)}</div>
                </div>
            `;
            renderedMessageCount = 0;
        } finally {
            polling = false;
        }
    }

    function startPolling() {
        stopPolling();
        pollTimer = window.setInterval(() => {
            refreshMessages();
        }, POLL_INTERVAL_MS);
    }

    function stopPolling() {
        if (pollTimer) {
            window.clearInterval(pollTimer);
            pollTimer = null;
        }
        polling = false;
    }

    async function sendText() {
        if (sending || uploading) {
            return;
        }
        const content = (input.value || "").trim();
        if (!content) {
            return;
        }
        sending = true;
        setInputEnabled(true);
        input.value = "";
        try {
            const response = await fetch("/api/webchat/send", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "include",
                body: JSON.stringify({ content, session_id: FIXED_SESSION_ID, msg_type: 1 }),
            });
            const result = await response.json();
            if (!result.success) {
                throw new Error(result.error || "发送失败");
            }
            await refreshMessages();
        } catch (error) {
            showToast("error", "发送失败: " + (error?.message || error));
        } finally {
            sending = false;
            setInputEnabled(true);
            input.focus();
        }
    }

    async function sendFile(file) {
        if (!file || uploading || sending) {
            return;
        }
        uploading = true;
        setInputEnabled(true);
        try {
            const form = new FormData();
            form.append("session_id", FIXED_SESSION_ID);
            form.append("file", file);

            const response = await fetch("/api/webchat/send_file", {
                method: "POST",
                credentials: "include",
                body: form,
            });
            const result = await response.json();
            if (!result.success) {
                throw new Error(result.error || "上传失败");
            }
            await refreshMessages();
        } catch (error) {
            showToast("error", "上传失败: " + (error?.message || error));
        } finally {
            uploading = false;
            setInputEnabled(true);
            fileInput.value = "";
            input.focus();
        }
    }

    // 拖拽窗口
    let dragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;

    function onMouseDown(event) {
        if (!isOpen()) {
            return;
        }
        // 点到按钮/输入时不进入拖拽，避免最小化点不了
        const target = event.target;
        if (target && target.closest && target.closest("button, a, input, textarea, select, label")) {
            return;
        }
        dragging = true;
        const rect = win.getBoundingClientRect();
        dragOffsetX = event.clientX - rect.left;
        dragOffsetY = event.clientY - rect.top;
        // 用 left/top 绝对定位，并去掉 CSS transform，避免拖不动/关不掉
        win.style.transform = "none";
        win.style.left = rect.left + "px";
        win.style.top = rect.top + "px";
        win.style.right = "auto";
        win.style.bottom = "auto";
        win.dataset.userMoved = "1";
        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
        event.preventDefault();
    }

    function onMouseMove(event) {
        if (!dragging) {
            return;
        }
        const maxLeft = Math.max(0, window.innerWidth - win.offsetWidth);
        const maxTop = Math.max(0, window.innerHeight - win.offsetHeight);
        const nextLeft = Math.min(Math.max(0, event.clientX - dragOffsetX), maxLeft);
        const nextTop = Math.min(Math.max(0, event.clientY - dragOffsetY), maxTop);
        win.style.left = nextLeft + "px";
        win.style.top = nextTop + "px";
    }

    function onMouseUp() {
        dragging = false;
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
    }

    function bindCloseControl(el) {
        if (!el) return;
        const handler = (event) => minimizeWindow(event);
        // capture 阶段，避免被 header 拖拽逻辑吞掉
        ["click", "pointerup", "mouseup"].forEach((type) => {
            el.addEventListener(type, handler, true);
        });
        el.addEventListener("mousedown", (event) => {
            event.preventDefault();
            event.stopPropagation();
        }, true);
        el.addEventListener("pointerdown", (event) => {
            event.preventDefault();
            event.stopPropagation();
        }, true);
    }

    openBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openWindow();
    });

    bindCloseControl(minimizeBtn);
    bindCloseControl(document.getElementById("webchat-widget-close"));

    sendBtn.addEventListener("click", () => {
        sendText();
    });

    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            sendText();
        }
    });

    uploadBtn.addEventListener("click", () => {
        fileInput.value = "";
        fileInput.click();
    });

    fileInput.addEventListener("change", async () => {
        const file = fileInput.files && fileInput.files[0];
        if (file) {
            await sendFile(file);
        }
    });

    // 只允许在标题文字区域拖拽，按钮区不绑定拖拽
    const dragHandle = document.getElementById("webchat-widget-title") || header;
    dragHandle.addEventListener("mousedown", onMouseDown);
    dragHandle.addEventListener("pointerdown", onMouseDown);

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && isOpen()) {
            minimizeWindow(event);
        }
    });

    // 默认不自动打开，避免所有页面加载都请求 API
    renderLoading("点击右侧按钮开始对话");
})();
