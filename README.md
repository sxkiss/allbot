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
