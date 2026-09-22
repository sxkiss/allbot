<!-- AUTO-DOC: Update me when project structure or architecture changes -->

# Architecture

AllBot is a multi-protocol bot with a FastAPI admin server and a plugin/adapter ecosystem.
Runtime updates are handled by the admin update subsystem (download -> backup -> apply).
The adapter layer now includes QQ, Telegram, Web, Win, wx-filehelper-api, ocwx clawbot, WeChat Observatory public API, and WeCom aibot long-connection bridges (wecom_bot defaults off; voice AMR-NB transcode + link template cards).
Default runtime ships with only the web adapter enabled and all plugins disabled.
Adapters are started inside bot core initialization before message listening starts.
Wechat login runs asynchronously to avoid blocking adapter message ingestion.
Wechat protocol access is encapsulated in `WechatAPI/`, including a dedicated 869 client.
869 login recovery now uses a shared state machine across startup and QR helper APIs: token/poll restore -> cached auth probe -> wakeup -> same-auth QR -> last-resort new auth.
Config loading accepts both section-scoped and legacy top-level transport keys for WechatAPI connectivity.
Admin security now enforces non-default credentials, challenge-protected 869 login helpers, authenticated WebSocket access, and file access whitelists.
Admin UI includes a right-side feedback widget that posts to a fixed xxtui channel (title: allbot反馈).
869 operational methods are exposed to plugins through the runtime client rather than admin HTTP debug endpoints.
Plugin installation is routed through a guarded service that validates GitHub URLs, blocks ZIP Slip/symlink payloads, and disables dependency installation by default.
Static contract checks live in `tools/route_audit.py`, which now correctly parses multiple `/api/*` references on the same line.
Built-in plugins also cover lightweight live information retrieval such as official typhoon tracking summaries, official page screenshots, satellite cloud imagery delivery, and multi-source disaster warning pushes.

- `adapter/INDEX.md`: Multi-platform adapters and queue bridge contracts
- `admin/INDEX.md`: Admin server, update pipeline, and related APIs
- `bot_core/INDEX.md`: Core orchestrator, login, and message pipeline
- `plugins/INDEX.md`: Built-in plugins and lifecycle integration points
- `tests/INDEX.md`: Project regression tests
- `WechatAPI/INDEX.md`: Wechat protocol clients (legacy + 869)
- `utils/INDEX.md`: Shared utilities (config, protocol mapping, etc.)
