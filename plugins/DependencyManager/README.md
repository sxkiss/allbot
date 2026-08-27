# 🔧 依赖包管理器 (DependencyManager)

> 🚀 通过微信命令直接管理 Python 依赖包，无需登录服务器！
> **本插件是 [AllBotv2](https://github.com/HenryXiaoYang/AllBotv2) 的一个插件。**

 <img src="https://github.com/user-attachments/assets/a2627960-69d8-400d-903c-309dbeadf125" width="400" height="600">

## ✨ 功能特点

- 📦 **包管理** - 远程安装、更新、查询和卸载 Python 包
- 🔒 **安全控制** - 仅限管理员使用，防止未授权操作
- 🛡️ **包白名单** - 可选启用允许安装的包列表，提高安全性
- 🔍 **导入检查** - 检查包是否可以成功导入，快速诊断问题
- 📋 **列表展示** - 清晰显示已安装的所有包及其版本
- 📊 **详细输出** - 提供完整的安装/卸载过程信息和错误报告
- ⚙️ **版本控制** - 支持安装特定版本的包，灵活应对兼容性需求
- 💬 **简单易用** - 通过直观的命令快速管理依赖

## 📋 使用指南

## 📋 使用指南

### 从 GitHub 安装插件

使用 github 唤醒词安装（必需）：

```
github https://github.com/sxkiss/allbot.git
```

或使用简化格式：

```
github sxkiss/allbot
```

快捷命令安装插件：

```
github allbot
```

获取 GitHub 安装帮助：

```
github help
```

### 插件市场查询

每页固定返回 5 个插件（分页）：

```
插件查询 1
```

```
插件查询 2
```

### 插件提交

提交插件到插件市场（按字段顺序）：

```
插件提交 名称|简介|作者|版本|GitHub地址|标签(可选)|依赖(可选)
```

示例：

```
插件提交 依赖管理器|用于安装插件|老夏|1.0.0|https://github.com/xxx/yyy|工具,运维|requests>=2.32
```

### 安装包

安装最新版本的包：

```
!pip install 包名
```

安装特定版本的包：

```
!pip install 包名==1.2.3
```

### 获取帮助

```
!pip help
```

或简单地：

```
!pip
```

## 🔄 使用流程示例

1. 检查某个包是否已安装：`!pip show numpy`
2. 安装新版本的包：`!pip install pandas`
3. 安装特定版本的包：`!pip install tensorflow==2.9.0`
4. 检查包是否可以导入：`!import tensorflow`
5. 如需更新已安装的包：`!pip install --upgrade pillow`
6. 卸载不再需要的包：`!pip uninstall tensorflow`
7. 安装 GitHub 上的插件（完整 URL）：`github https://github.com/NanSsye/GeminiImage.git`
8. 安装 GitHub 上的插件（简化格式）：`github NanSsye/GeminiImage`
9. 使用快捷命令安装 GeminiImage 插件：`github gemini`
10. 测试插件是否正常工作：`!test dm`

## ⚙️ 配置说明

在`config.toml`中设置：

```toml
[basic]
# 是否启用插件
enable = true

# 管理员列表，只有这些用户可以使用此插件
# 这里填写管理员的微信ID
admin_list = ["wxid_lnbsshdobq7y22", "xianan96928"]

# 安全设置
# 是否检查包是否在允许列表中（true/false）
check_allowed = false

# 允许安装的包列表（如果check_allowed=true）
allowed_packages = [
    "akshare",
    "requests",
    "pillow",
    "matplotlib",
    "numpy",
    "pandas",
    "lxml",
    "beautifulsoup4",
    "aiohttp"
]

[commands]
# 命令前缀配置
install = "!pip install"
show = "!pip show"
list = "!pip list"
uninstall = "!pip uninstall"
# GitHub插件安装命令前缀
github_install = "github" 
# 插件市场查询命令前缀（分页：插件查询 1 / 插件查询 2 ...）
market_query = "插件查询"
# 插件市场提交命令前缀（提交插件审核）
market_submit = "插件提交"
```

### 安全设置说明

为了保护服务器安全，您可以启用包白名单功能：

1. 将`check_allowed`设置为`true`
2. 在`allowed_packages`列表中添加允许安装的包名
3. 任何不在列表中的包将被拒绝安装

这可以防止安装潜在有害的包或占用过多系统资源的大型包。

## 🔒 安全性注意事项

- 仅向可信任的管理员提供访问权限
- 考虑启用包白名单功能，限制可安装的包
- 定期检查已安装的包，确保系统安全
- 此插件可以执行系统命令，请谨慎使用

## 📊 适用场景

- 远程服务器维护，无需 SSH 登录
- 快速安装新依赖以支持其他插件
- 紧急修复生产环境中的依赖问题
- 检查和诊断包导入失败的问题
- 清理不再需要的包以释放空间

## 📝 开发日志

- v1.0.0: 初始版本发布，支持基本的包管理功能

## 👨‍💻 作者

**老夏的金库** ©️ 2024

**开源不易，感谢打赏支持！**
![image](https://github.com/user-attachments/assets/2dde3b46-85a1-4f22-8a54-3928ef59b85f)

## �� 许可证

MIT License
