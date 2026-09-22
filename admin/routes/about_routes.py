"""
关于页面路由：负责处理关于页面的请求和转换Markdown为HTML

@input: markdown, pathlib, FastAPI Request/Depends, admin.utils.optional_auth
@output: /about 页面与 favicon 路由，自动收录项目文档并渲染为 HTML
@position: 管理后台“关于”文档中心
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import os
import re
import logging
from pathlib import Path
from fastapi import Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from starlette.templating import Jinja2Templates

logger = logging.getLogger("about_routes")

project_root = Path(__file__).parent.parent.parent

try:
    import markdown
    has_markdown = True
except ImportError:
    has_markdown = False
    logger.warning("markdown库未安装，将使用简单的HTML转换")


# 优先展示顺序；未列出的 docs/*.md 会自动追加到末尾
DOC_CATALOG = [
    {
        "id": "readme",
        "title": "项目总述",
        "path": "README.md",
        "icon": "bi-info-circle-fill",
        "group": "入门",
        "description": "项目简介、功能特性与快速开始",
    },
    {
        "id": "user-manual",
        "title": "用户手册",
        "path": "docs/用户手册.md",
        "icon": "bi-book",
        "group": "入门",
        "description": "管理后台使用与常见操作说明",
    },
    {
        "id": "config-guide",
        "title": "配置指南",
        "path": "docs/配置指南.md",
        "icon": "bi-sliders",
        "group": "入门",
        "description": "主配置、插件与适配器配置说明",
    },
    {
        "id": "docker-guide",
        "title": "Docker 部署",
        "path": "docs/docker本地构建.md",
        "icon": "bi-box-seam",
        "group": "入门",
        "description": "官方镜像与本地构建部署方式",
    },
    {
        "id": "webchat",
        "title": "Web 对话说明",
        "path": "docs/webchat功能说明.md",
        "icon": "bi-chat-dots",
        "group": "功能",
        "description": "后台 Web 对话架构与使用方式",
    },
    {
        "id": "image-recognition",
        "title": "引用图片识别",
        "path": "引用图片识别功能说明.md",
        "icon": "bi-image",
        "group": "功能",
        "description": "引用图片消息触发 AI 识别的能力说明",
    },
    {
        "id": "plugin-list",
        "title": "插件列表",
        "path": "docs/插件列表.md",
        "icon": "bi-collection",
        "group": "插件",
        "description": "内置插件分类与功能概览",
    },
    {
        "id": "plugin-dev",
        "title": "插件开发指南",
        "path": "docs/插件开发指南.md",
        "icon": "bi-code-slash",
        "group": "插件",
        "description": "插件结构、接口与开发规范",
    },
    {
        "id": "plugins-readme",
        "title": "插件目录说明",
        "path": "plugins/README.md",
        "icon": "bi-folder2-open",
        "group": "插件",
        "description": "plugins 目录结构与约定",
    },
    {
        "id": "multi-platform",
        "title": "多平台适配器",
        "path": "docs/multi-platform-adapter.md",
        "icon": "bi-diagram-3",
        "group": "架构",
        "description": "多平台消息接入与回复分发机制",
    },
    {
        "id": "architecture",
        "title": "系统架构",
        "path": "docs/系统架构文档.md",
        "icon": "bi-layers",
        "group": "架构",
        "description": "系统模块划分与整体架构",
    },
    {
        "id": "api-doc",
        "title": "API 文档",
        "path": "docs/API文档.md",
        "icon": "bi-braces",
        "group": "开发",
        "description": "管理后台 HTTP API 接口说明",
    },
    {
        "id": "api-guide",
        "title": "API 文档使用指南",
        "path": "docs/API_DOCUMENTATION_GUIDE.md",
        "icon": "bi-journal-code",
        "group": "开发",
        "description": "Swagger / ReDoc 使用说明",
    },
]


def convert_markdown_to_html(markdown_content):
    """将Markdown内容转换为HTML"""
    if has_markdown:
        html_content = markdown.markdown(
            markdown_content,
            extensions=[
                "markdown.extensions.extra",
                "markdown.extensions.codehilite",
                "markdown.extensions.tables",
                "markdown.extensions.toc",
            ],
        )
    else:
        html_content = markdown_content.replace("\n\n", "</p><p>")
        html_content = f"<p>{html_content}</p>"
        html_content = html_content.replace("\n", "<br>")

    html_content = re.sub(r'(src|href)="admin/static/', r'\1="/admin/static/', html_content)
    return html_content


def read_markdown_file(file_path):
    """读取Markdown文件内容"""
    try:
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"读取Markdown文件失败: {e}")
        return None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return slug or "doc"


def _extract_title(markdown_content: str, fallback: str) -> str:
    for line in markdown_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return re.sub(r"^#+\s*", "", stripped).strip() or fallback
    return fallback


def _extract_description(markdown_content: str, fallback: str = "") -> str:
    for line in markdown_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("|"):
            continue
        if stripped.startswith(">") or stripped.startswith("-") or stripped.startswith("*"):
            stripped = re.sub(r"^[\->\*\s]+", "", stripped).strip()
        if stripped:
            return stripped[:120]
    return fallback


def discover_about_documents(root: Path = project_root):
    """发现关于页应展示的 Markdown 文档列表。"""
    # 自动发现时跳过内部审查/临时报告，避免污染用户文档中心
    auto_exclude_keywords = ("审查", "整改报告", "TODO", "WIP", "draft")
    documents = []
    seen = set()

    for meta in DOC_CATALOG:
        rel_path = meta["path"]
        abs_path = root / rel_path
        content = read_markdown_file(abs_path)
        if content is None:
            continue

        documents.append(
            {
                "id": meta["id"],
                "title": meta.get("title") or _extract_title(content, Path(rel_path).stem),
                "path": rel_path,
                "icon": meta.get("icon", "bi-file-earmark-text"),
                "group": meta.get("group", "其他"),
                "description": meta.get("description") or _extract_description(content),
                "html": convert_markdown_to_html(content),
            }
        )
        seen.add(Path(rel_path).resolve() if Path(rel_path).is_absolute() else abs_path.resolve())

    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.glob("*.md"), key=lambda p: p.name.lower()):
            if path.resolve() in seen:
                continue
            if any(k.lower() in path.name.lower() for k in auto_exclude_keywords):
                continue
            content = read_markdown_file(path)
            if content is None:
                continue
            rel_path = str(path.relative_to(root)).replace("\\", "/")
            title = _extract_title(content, path.stem)
            documents.append(
                {
                    "id": f"auto-{_slugify(path.stem)}",
                    "title": title,
                    "path": rel_path,
                    "icon": "bi-file-earmark-text",
                    "group": "其他",
                    "description": _extract_description(content, rel_path),
                    "html": convert_markdown_to_html(content),
                }
            )
            seen.add(path.resolve())

    return documents


def register_about_routes(app):
    """
    注册关于页面相关路由

    Args:
        app: FastAPI应用实例
    """
    from typing import Optional
    from admin.utils import optional_auth

    templates = app.state.templates if hasattr(app.state, "templates") else None

    if templates is None:
        try:
            from admin.server import templates as server_templates
            templates = server_templates
        except ImportError:
            logger.error("无法导入模板对象")
            templates = Jinja2Templates(
                directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
            )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        """处理网站图标请求"""
        static_dir = Path(__file__).parent.parent / "static" / "img"
        favicon_path = static_dir / "favicon.ico"

        if favicon_path.exists():
            return FileResponse(favicon_path)

        logger.warning(f"favicon.ico文件不存在: {favicon_path}")
        return RedirectResponse(url="/static/img/favicon.ico")

    @app.get("/about", response_class=HTMLResponse)
    async def about_page(request: Request, username: Optional[str] = Depends(optional_auth)):
        """处理访问关于页面的请求"""
        if username is None:
            username = "未知用户"

        try:
            documents = discover_about_documents(project_root)
            groups = []
            grouped = {}
            for doc in documents:
                grouped.setdefault(doc["group"], []).append(doc)
            for group_name, items in grouped.items():
                groups.append({"name": group_name, "docs": items})

            return templates.TemplateResponse(
                "about.html",
                {
                    "request": request,
                    "username": username,
                    "documents": documents,
                    "document_groups": groups,
                    "document_count": len(documents),
                    # 兼容旧模板字段，避免缓存模板报错
                    "readme_content_html": next((d["html"] for d in documents if d["id"] == "readme"), ""),
                    "image_recognition_html": next(
                        (d["html"] for d in documents if d["id"] == "image-recognition"), ""
                    ),
                    "plugins_readme_html": next(
                        (d["html"] for d in documents if d["id"] == "plugins-readme"), ""
                    ),
                },
            )
        except Exception as e:
            logger.error(f"渲染关于页面失败: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return HTMLResponse(
                content=f"""
                <html>
                    <head><title>错误</title></head>
                    <body>
                        <h1>处理请求时出错</h1>
                        <p>很抱歉，处理您的请求时发生错误。</p>
                        <p>错误详情: {str(e)}</p>
                        <p><a href="/">返回首页</a></p>
                    </body>
                </html>
                """,
                status_code=500,
            )

    logger.info("关于页面路由注册成功")
