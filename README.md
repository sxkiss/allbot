# 875 协议服务（纯协议 + 人脸验证）

本仓库仅包含 875（WeChatPadProMAX）协议服务运行所需的二进制、配置与启动脚本，
**不含 AllBot 框架**。框架侧通过 `adapter/wechat_875`（AllBot dev 分支）接入。

## 组件

| 路径 | 说明 |
|---|---|
| `wechatpad875` | 875 协议服务二进制（HTTP 18062 / WS 18089） |
| `conf875/app.conf` | 875 配置（端口、Redis、人脸验证开关） |
| `wxserver_runtime/bin/pad-face-verify-linux-x64` | 人脸验证二进制（18080） |
| `wxserver_runtime/assets|static|swagger` | 运行时资源与控制台静态资源 |
| `lib/`、`lib875/` | 授权/运行库与 key |
| `08sae.dat` | 运行时数据资源 |
| `entrypoint.sh` | 启动 Redis → 部署配置 → 启动 875 |
| `Dockerfile` / `docker-compose.yml` | 协议服务镜像与编排 |

## 端口

- `18062`：875 HTTP API（带卡密 `?authcode=<key>` 或 Header `X-Access-Token`）
- `18089`：875 WebSocket（`/ws/sync`）
- `18080`：人脸验证（由 875 按 `pad_face_verifier_enabled=true` 拉起）

## 运行

```bash
cp .env.example .env   # 或自行创建：WX_TOKEN_KEY=<卡密>
docker compose up -d --build
```

`.env`：

```env
WX_TOKEN_KEY=你的卡密
# 可选：WX_REDIS_LINK=127.0.0.1:6379
```

健康检查：

```bash
curl -s http://127.0.0.1:18062/api/Login/GetLoginStatus?authcode=<key>
curl -s "http://127.0.0.1:18062/api/Login/PadFaceVerifierStatus?authcode=<key>"
```

## 说明

- 人脸验证二进制由 875 服务按配置自动拉起，入口脚本只做存在性检查与提示。
- 卡密通过环境变量注入到 `/config/app.conf` 与 `/app/conf/app.conf`，不写入仓库。
- 本仓库不再包含管理后台、插件、适配器或消息框架；这些由 AllBot 主线负责。

## 上游二进制同步（880 等新版本）

仓库不再手工拷贝厂商二进制，改为 GitHub Actions 从官方镜像提取（本机网络无法访问 Docker Hub，Runner 可以）。

工作流：`.github/workflows/sync-upstream-binaries.yml`（手动触发 `workflow_dispatch`）

- **image**：官方镜像，如 `wechatpadpro/wechatpadprobusiness:v2026.11.06.1`；留空自动取 Docker Hub 最新 tag
- **mode**：
  - `release`：把官方镜像 `/app` 全量打包为 `wppro-<version>.tar.gz` 上传 Release
  - `commit`：把主程序/人脸验证/metadata/swagger/runtime 资源按仓库布局提交回当前分支
- **expected_pad_version**：可选校验，如 `8.0.80`，不一致直接终止

映射关系：

| 官方镜像 `/app` | 仓库 |
|---|---|
| `wechatpadpromax08`（或 `wechatpad*`） | `wechatpad875` |
| `pad-face-verify-linux-x64` | `wxserver_runtime/bin/pad-face-verify-linux-x64` |
| `metadata.json` | `wxserver_runtime/metadata.json` |
| `swagger/` | `wxserver_runtime/swagger/` |
| `runtime/assets`、`runtime/static` | `wxserver_runtime/assets`、`static` |

仓库自有、不会被覆盖：`08sae.dat`、`rqtx.dat`、`version.json`、`lib/`、`lib875/`、`assets/sae.dat`、`assets/setting.json`、`static/templates/`。

同步后会生成 `wxserver_runtime/UPSTREAM_VERSION.json`（镜像 tag、版本、md5），便于核对。

> 说明：工作流需推送到 GitHub 后才能手动触发；升级后必须实机验证授权租约、18062/18089/18080 与人脸验证。
