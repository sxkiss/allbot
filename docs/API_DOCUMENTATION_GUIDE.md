# AllBot API 文档使用指南

## 📖 概述

AllBot 管理后台已启用 FastAPI 自动生成的 API 文档，提供两种文档界面：

- **Swagger UI**：交互式 API 测试界面
- **ReDoc**：清晰美观的文档阅读界面

---

## 🚀 快速开始

### 1. 启动管理后台

确保 AllBot 管理后台正在运行：

```bash
# 方式一：通过主程序启动
python main.py

# 方式二：独立启动管理后台
python admin/run_server.py
```

默认访问地址：`http://localhost:9090`

### 2. 访问 API 文档

启动成功后，可以通过以下 URL 访问文档：

| 文档类型 | URL | 说明 |
|---------|-----|------|
| **Swagger UI** | http://localhost:9090/docs | 交互式 API 测试界面 |
| **ReDoc** | http://localhost:9090/redoc | 美观的文档阅读界面 |
| **OpenAPI Schema** | http://localhost:9090/openapi.json | OpenAPI JSON 规范文件 |

---

## 📚 Swagger UI 使用指南

### 界面功能

Swagger UI 提供了完整的 API 测试环境，主要功能：

1. **API 分组**：按模块（系统、插件、账号等）组织 API
2. **参数说明**：查看每个参数的类型、约束、默认值
3. **在线测试**：直接在浏览器中测试 API
4. **响应示例**：查看成功和错误的响应格式
5. **认证支持**：输入认证信息后测试需要权限的 API

### 使用步骤

#### 步骤 1：选择 API

点击想要测试的 API 端点，例如 `GET /api/system/status`

#### 步骤 2：查看参数

展开的面板会显示：
- **参数列表**：每个参数的名称、类型、是否必填
- **请求示例**：示例请求数据
- **响应示例**：示例响应数据

#### 步骤 3：配置认证（如需要）

点击右上角的 `Authorize` 按钮：
- 选择 `HTTPBasic` 认证方式
- 输入用户名和密码（默认：admin / admin123）
- 点击 `Authorize` 确认

#### 步骤 4：填写参数

- **Query 参数**：在对应的输入框中填写
- **Path 参数**：在 URL 路径中填写
- **Body 参数**：在请求体编辑器中填写 JSON

#### 步骤 5：执行请求

点击 `Try it out` 按钮，然后点击 `Execute`

#### 步骤 6：查看响应

执行后会显示：
- **Curl 命令**：等效的 curl 命令
- **请求 URL**：实际请求的完整 URL
- **响应状态码**：200、401、404 等
- **响应头**：Content-Type、Set-Cookie 等
- **响应体**：JSON 格式的响应数据

---

## 📖 ReDoc 使用指南

### 界面特点

ReDoc 提供了更加清晰的文档阅读体验：

1. **左侧导航**：快速跳转到不同的 API 分组
2. **右侧示例**：实时显示请求和响应示例
3. **搜索功能**：快速查找 API 端点
4. **代码生成**：查看多种编程语言的调用代码

### 使用场景

- 📚 **学习 API**：了解 API 的功能和用法
- 📋 **复制示例**：复制请求/响应示例到代码中
- 🔍 **查找端点**：快速定位需要的 API
- 📝 **编写文档**：为前端团队提供参考

---

## 🔑 API 认证说明

### 认证方式

AllBot 管理后台支持两种认证方式：

1. **HTTP Basic Auth**：通过 HTTP 头传递用户名和密码
2. **Session Cookie**：登录后通过 Cookie 保持会话

### 如何获取认证

#### 方式一：HTTP Basic Auth

在请求头中添加 `Authorization` 字段：

```bash
# Curl 示例
curl -X GET "http://localhost:9090/api/system/status" \
     -H "Authorization: Basic YWRtaW46YWRtaW4xMjM="
```

```python
# Python 示例
import requests
from requests.auth import HTTPBasicAuth

response = requests.get(
    "http://localhost:9090/api/system/status",
    auth=HTTPBasicAuth("admin", "admin123")
)
```

#### 方式二：Session Cookie

先调用登录 API 获取 Session：

```bash
# 1. 登录获取 Cookie
curl -X POST "http://localhost:9090/api/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin123"}' \
     -c cookies.txt

# 2. 使用 Cookie 调用其他 API
curl -X GET "http://localhost:9090/api/system/status" \
     -b cookies.txt
```

### 默认凭证

| 字段 | 默认值 | 配置位置 |
|------|--------|----------|
| 用户名 | `admin` | `main_config.toml` → `[Admin].username` |
| 密码 | `admin123` | `main_config.toml` → `[Admin].password` |

⚠️ **安全提示**：生产环境请务必修改默认密码！

---

## 🏷️ API 分组说明

### 系统（System）
- `GET /api/system/status`：获取机器人状态
- `GET /api/system/stats`：获取系统统计（CPU/内存/消息）
- `GET /api/system/info`：获取系统信息
- `GET /api/system/config`：获取系统配置
- `POST /api/system/config`：保存系统配置

### 插件（Plugin）
- `GET /api/plugins/list`：获取插件列表
- `POST /api/plugins/toggle`：启用/禁用插件
- `POST /api/plugins/reload`：重载插件
- `GET /api/plugins/config`：获取插件配置
- `POST /api/plugins/config`：保存插件配置

### 账号（Account）
- `GET /api/accounts/list`：获取账号列表
- `POST /api/accounts/switch`：切换账号
- `POST /api/accounts/logout`：登出账号

### 文件（File）
- `GET /api/files/list`：获取文件列表
- `POST /api/files/upload`：上传文件
- `DELETE /api/files/delete`：删除文件
- `GET /api/files/download`：下载文件

### 联系人（Contact）
- `GET /api/contacts/list`：获取联系人列表
- `GET /api/contacts/search`：搜索联系人
- `GET /api/contacts/detail`：获取联系人详情

### 朋友圈（Moment）
- `GET /api/pyq/list`：获取朋友圈列表
- `POST /api/pyq/like`：点赞朋友圈
- `POST /api/pyq/comment`：评论朋友圈

### 提醒（Reminder）
- `GET /api/reminders/list`：获取提醒列表
- `POST /api/reminders/add`：添加提醒
- `DELETE /api/reminders/delete`：删除提醒

### 适配器（Adapter）
- `GET /api/adapters/list`：获取适配器列表
- `GET /api/adapters/status`：获取适配器状态
- `POST /api/adapters/config`：配置适配器

### AI 平台（AI Platform）
- `GET /api/ai-platforms/list`：获取 AI 平台列表
- `POST /api/ai-platforms/config`：配置 AI 平台密钥

---

## 💡 常见问题 (FAQ)

### Q1: 为什么访问文档返回 404？

**A**: 请确保：
1. 管理后台已启动（检查端口 9090 是否监听）
2. 使用正确的 URL（`/docs` 不是 `/api/docs`）
3. FastAPI 版本 >= 0.110.0

### Q2: 为什么看不到部分 API？

**A**: 可能原因：
1. API 路由未正确注册到 FastAPI 应用
2. 路由装饰器缺少 `@app.xxx` 而是使用 `@router.xxx`（需要注册 router）
3. API 被设置为 `include_in_schema=False`（隐藏文档）

### Q3: 如何导出 API 文档？

**A**: 多种方式：
1. **OpenAPI JSON**：访问 `/openapi.json` 并保存
2. **Swagger UI HTML**：浏览器访问 `/docs` 并保存网页
3. **ReDoc HTML**：浏览器访问 `/redoc` 并保存网页
4. **使用工具**：使用 `swagger-codegen` 或 `openapi-generator` 生成客户端 SDK

### Q4: 如何自定义文档样式？

**A**: FastAPI 支持自定义 Swagger UI 和 ReDoc 的配置：

```python
app = FastAPI(
    swagger_ui_parameters={
        "deepLinking": True,
        "displayRequestDuration": True,
        "filter": True,
    },
    redoc_url="/redoc",
)
```

### Q5: 测试 API 时提示 401 未认证？

**A**: 请先点击 Swagger UI 右上角的 `Authorize` 按钮，输入用户名和密码进行认证。

### Q6: 如何禁用 API 文档？

**A**: 在生产环境如需禁用文档，修改 `admin/server.py`：

```python
app = FastAPI(
    docs_url=None,  # 禁用 Swagger UI
    redoc_url=None,  # 禁用 ReDoc
    openapi_url=None,  # 禁用 OpenAPI Schema
)
```

---

## 🔧 开发者指南

### 如何改进现有 API 文档

参考 `admin/api_documentation_example.py` 中的示例，为每个 API 添加：

1. **tags**：API 分组标签
2. **summary**：简短描述
3. **description**：详细说明（支持 Markdown）
4. **response_model**：响应数据模型（Pydantic）
5. **responses**：多种响应状态码示例

### 如何添加新的 Pydantic 模型

在 `admin/models.py` 中定义新模型：

```python
class MyResponse(BaseModel):
    """我的响应模型"""
    field1: str = Field(..., description="字段1说明")
    field2: int = Field(0, description="字段2说明", ge=0)

    class Config:
        json_schema_extra = {
            "example": {
                "field1": "示例值",
                "field2": 123
            }
        }
```

### 如何生成客户端 SDK

使用 OpenAPI Generator 自动生成客户端代码：

```bash
# 1. 导出 OpenAPI Schema
curl http://localhost:9090/openapi.json > openapi.json

# 2. 生成 Python 客户端
openapi-generator generate \
    -i openapi.json \
    -g python \
    -o ./allbot-client

# 3. 生成 JavaScript 客户端
openapi-generator generate \
    -i openapi.json \
    -g javascript \
    -o ./allbot-client-js
```

---

## 📞 支持与反馈

- **问题反馈**：GitHub Issues
- **功能建议**：GitHub Discussions
- **文档贡献**：提交 Pull Request

---

**更新时间**：2026-01-18
**适用版本**：AllBot 1.0.0+
