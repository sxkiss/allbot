"""
关于页面路由：负责处理关于页面的请求和转换Markdown为HTML
"""
import os
import logging
from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from starlette.templating import Jinja2Templates

# 设置日志
logger = logging.getLogger("about_routes")

# 创建路由器
router = APIRouter()

# 定义项目根路径
project_root = Path(__file__).parent.parent.parent

# 尝试导入Markdown库
try:
    import markdown
    has_markdown = True
except ImportError:
    has_markdown = False
    logger.warning("markdown库未安装，将使用简单的HTML转换")

def convert_markdown_to_html(markdown_content):
    """将Markdown内容转换为HTML"""
    if has_markdown:
        # 使用markdown库进行转换，启用额外功能
        return markdown.markdown(
            markdown_content,
            extensions=[
                'markdown.extensions.extra',
                'markdown.extensions.codehilite',
                'markdown.extensions.tables',
                'markdown.extensions.toc'
            ]
        )
    else:
        # 如果没有markdown库，进行简单的转换
        html_content = markdown_content.replace('\n\n', '</p><p>')
        html_content = f"<p>{html_content}</p>"
        html_content = html_content.replace('\n', '<br>')
        return html_content

def read_markdown_file(file_path):
    """读取Markdown文件内容"""
    try:
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return f"文件不存在: {file_path}"
            
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"读取Markdown文件失败: {e}")
        return f"无法读取文件: {file_path}。错误: {str(e)}"

def register_about_routes(app, check_auth):
    """
    注册关于页面相关路由
    
    Args:
        app: FastAPI应用实例
        check_auth: 认证检查函数
    """
    templates = app.state.templates if hasattr(app.state, "templates") else None
    
    if templates is None:
        # 如果模板未在app.state中定义，获取templates实例
        try:
            from admin.server import templates as server_templates
            templates = server_templates
        except ImportError:
            logger.error("无法导入模板对象")
            templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))
    
    # 添加favicon.ico路由
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        """处理网站图标请求"""
        static_dir = Path(__file__).parent.parent / "static" / "img"
        favicon_path = static_dir / "favicon.ico"
        
        if favicon_path.exists():
            return FileResponse(favicon_path)
        else:
            logger.warning(f"favicon.ico文件不存在: {favicon_path}")
            # 返回一个302重定向到/static/img/favicon.ico
            return RedirectResponse(url="/static/img/favicon.ico")
    
    # 定义关于页面路由 - 不使用依赖项，手动处理认证
    @app.get("/about", response_class=HTMLResponse)
    async def about_page(request: Request):
        """处理访问关于页面的请求"""
        # 手动处理认证
        try:
            username = await check_auth(request)
        except Exception as e:
            logger.error(f"认证检查失败: {e}")
            username = "未知用户"  # 提供一个默认值
        
        try:
            # 读取三个Markdown文件的内容
            readme_path = project_root / "README.md"
            image_recognition_path = project_root / "引用图片识别功能说明.md"
            plugins_readme_path = project_root / "plugins" / "README.md"
            
            # 读取并转换为HTML
            readme_content = read_markdown_file(readme_path)
            image_recognition_content = read_markdown_file(image_recognition_path)
            plugins_readme_content = read_markdown_file(plugins_readme_path)
            
            # 转换为HTML
            readme_html = convert_markdown_to_html(readme_content)
            image_recognition_html = convert_markdown_to_html(image_recognition_content)
            plugins_readme_html = convert_markdown_to_html(plugins_readme_content)
            
            # 渲染模板
            return templates.TemplateResponse("about.html", {
                "request": request,
                "username": username,
                "readme_content_html": readme_html,
                "image_recognition_html": image_recognition_html,
                "plugins_readme_html": plugins_readme_html
            })
        except Exception as e:
            logger.error(f"渲染关于页面失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 返回一个简单的错误页面
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
                status_code=500
            )
    
    logger.info("关于页面路由注册成功") 