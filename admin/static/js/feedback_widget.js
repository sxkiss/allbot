/**
 * @input: DOM elements in base.html; POST /api/feedback
 * @output: 意见反馈悬浮面板交互与提交
 * @position: 管理后台全局右侧反馈入口
 * @auto-doc: Update header and folder INDEX.md when this file changes
 */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    var openBtn = document.getElementById("feedback-widget-open");
    var panel = document.getElementById("feedback-widget-panel");
    var closeBtn = document.getElementById("feedback-widget-close");
    var cancelBtn = document.getElementById("feedback-widget-cancel");
    var submitBtn = document.getElementById("feedback-widget-submit");
    var contentEl = document.getElementById("feedback-content");
    var contactEl = document.getElementById("feedback-contact");
    var statusEl = document.getElementById("feedback-status");

    if (!openBtn || !panel || !submitBtn || !contentEl) {
      return;
    }

    function setStatus(text, type) {
      if (!statusEl) return;
      statusEl.textContent = text || "";
      statusEl.className = "form-text";
      if (type === "ok") statusEl.classList.add("text-success");
      else if (type === "err") statusEl.classList.add("text-danger");
      else statusEl.classList.add("text-muted");
    }

    function openPanel() {
      panel.classList.remove("feedback-widget-hidden");
      panel.setAttribute("aria-hidden", "false");
      setStatus("", "");
      if (contentEl) contentEl.focus();
    }

    function closePanel() {
      panel.classList.add("feedback-widget-hidden");
      panel.setAttribute("aria-hidden", "true");
    }

    openBtn.addEventListener("click", function () {
      if (panel.classList.contains("feedback-widget-hidden")) openPanel();
      else closePanel();
    });
    if (closeBtn) closeBtn.addEventListener("click", closePanel);
    if (cancelBtn) cancelBtn.addEventListener("click", closePanel);

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !panel.classList.contains("feedback-widget-hidden")) {
        closePanel();
      }
    });

    submitBtn.addEventListener("click", async function () {
      var content = (contentEl.value || "").trim();
      var contact = contactEl ? (contactEl.value || "").trim() : "";
      if (!content) {
        setStatus("请填写反馈内容", "err");
        contentEl.focus();
        return;
      }

      submitBtn.disabled = true;
      var oldHtml = submitBtn.innerHTML;
      submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>提交中';
      setStatus("正在提交...", "");

      try {
        var resp = await fetch("/api/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ content: content, contact: contact }),
        });
        var data = {};
        try {
          data = await resp.json();
        } catch (e) {
          data = {};
        }
        if (resp.ok && data && data.success) {
          setStatus(data.message || "反馈已提交，感谢支持", "ok");
          contentEl.value = "";
          if (contactEl) contactEl.value = "";
          if (typeof window.showToast === "function") {
            window.showToast("意见反馈", data.message || "提交成功", "success");
          }
          setTimeout(closePanel, 900);
        } else {
          var msg = (data && data.message) || ("提交失败（HTTP " + resp.status + "）");
          setStatus(msg, "err");
          if (typeof window.showToast === "function") {
            window.showToast("意见反馈", msg, "danger");
          }
        }
      } catch (err) {
        var emsg = "网络异常，请稍后重试";
        setStatus(emsg, "err");
        if (typeof window.showToast === "function") {
          window.showToast("意见反馈", emsg, "danger");
        }
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = oldHtml;
      }
    });
  });
})();
