"""
@input: FastAPI app、认证依赖、路径安全校验、项目文件白名单根目录
@output: 文件管理 API（白名单目录读写、上传下载、插件配置初始化）
@position: 管理后台文件访问边界层，负责将高权限文件操作限制在受控目录内
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import os
import json
import time
import zipfile
import mimetypes
import shutil
from datetime import datetime
from pathlib import Path
from typing import List
from fastapi import Request, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse, FileResponse
from loguru import logger
from itsdangerous import URLSafeSerializer


def register_files_routes(app, current_dir):
    """
    注册文件管理相关路由

    Args:
        app: FastAPI 应用实例
        current_dir: 当前目录路径
    """
    from admin.utils import require_auth, validate_path_safety

    root_dir = Path(current_dir).parent.resolve()
    allowed_roots = [
        root_dir / "plugins",
        root_dir / "adapter",
        root_dir / "database",
        root_dir / "resource",
        root_dir / "files",
        root_dir / "logs",
        root_dir / "temp",
        root_dir / "admin" / "_cache",
        root_dir / "main_config.toml",
        root_dir / "version.json",
        root_dir / "docker-compose.yml",
        root_dir / "docker-compose.local.yml",
    ]
    allowed_root_map = {
        "/" + str(path.relative_to(root_dir)).replace("\\", "/"): path.resolve(strict=False)
        for path in allowed_roots
    }

    def _normalize_relative_path(raw_path: str) -> str:
        path = str(raw_path or "").strip().replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path
        return path

    def _resolve_allowed_path(raw_path: str, path_description: str = "文件"):
        path = _normalize_relative_path(raw_path)
        full_path = (root_dir / path.lstrip("/")).resolve(strict=False)
        for allowed_root in allowed_roots:
            allowed_root_resolved = allowed_root.resolve(strict=False)
            error = validate_path_safety(full_path, allowed_root_resolved, path_description)
            if error is None:
                return path, full_path
        return path, None

    @app.get("/media/files/{filename:path}")
    async def public_media_file(filename: str):
        """公开访问 files 目录中的媒体文件（仅单文件名，禁止路径穿越）"""
        safe_name = os.path.basename(filename or "")
        if not safe_name or safe_name != filename:
            return JSONResponse(status_code=400, content={"success": False, "message": "非法文件名"})

        root_dir = Path(current_dir).parent
        file_path = root_dir / "files" / safe_name
        if not file_path.exists() or not file_path.is_file():
            return JSONResponse(status_code=404, content={"success": False, "message": "媒体文件不存在"})

        media_type, _ = mimetypes.guess_type(str(file_path))
        return FileResponse(
            path=str(file_path),
            filename=safe_name,
            media_type=media_type or "application/octet-stream",
        )

    @app.get("/api/files/list")
    async def api_files_list(request: Request, path: str = "/", page: int = 1, limit: int = 100, username: str = Depends(require_auth)):
        """获取文件列表，支持分页加载"""
        # 使用会话验证
        try:
            # 记录调试日志
            logger.debug(f"请求获取文件列表：路径 {path}，页码 {page}，每页数量 {limit}")

            # 处理相对路径
            path = _normalize_relative_path(path)
            if path == "/":
                items = []
                for rel_path, allowed_path in sorted(allowed_root_map.items()):
                    if not allowed_path.exists():
                        continue
                    item_type = 'directory' if allowed_path.is_dir() else 'file'
                    item_stat = allowed_path.stat()
                    items.append({
                        'name': rel_path.strip('/'),
                        'path': rel_path,
                        'type': item_type,
                        'size': item_stat.st_size if allowed_path.is_file() else 0,
                        'modified': int(item_stat.st_mtime),
                    })
                return JSONResponse(content={
                    'success': True,
                    'items': items,
                    'pagination': {
                        'page': 1,
                        'limit': limit,
                        'total_items': len(items),
                        'total_pages': 1,
                    }
                })

            path, full_path = _resolve_allowed_path(path, "目录")

            logger.debug(f"处理文件列表路径: {path} -> {full_path}")

            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许访问受控目录或配置文件'
                })

            # 检查路径是否存在
            if not os.path.exists(full_path):
                logger.warning(f"路径不存在: {full_path}")
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '路径不存在'
                })

            # 检查路径是否是目录
            if not os.path.isdir(full_path):
                logger.warning(f"路径不是目录: {full_path}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '路径不是一个目录'
                })

            # 获取目录内容
            items = []
            total_items = 0

            try:
                # 设置超时控制
                start_time = time.time()
                MAX_TIME = 3  # 最多允许3秒处理时间

                # 首先计算总数
                dir_items = []
                for item in os.listdir(full_path):
                    # 检查是否超时
                    if time.time() - start_time > MAX_TIME:
                        logger.warning(f"获取文件列表超时：路径 {path}")
                        break

                    item_path = os.path.join(full_path, item)
                    try:
                        is_dir = os.path.isdir(item_path)
                        dir_items.append((item, item_path, is_dir))
                    except (PermissionError, OSError) as e:
                        logger.warning(f"无法访问文件: {item_path}, 错误: {str(e)}")
                        continue

                # 按类型和名称排序（文件夹在前）
                dir_items.sort(key=lambda x: (0 if x[2] else 1, x[0].lower()))

                # 计算总数和分页
                total_items = len(dir_items)
                total_pages = (total_items + limit - 1) // limit if total_items > 0 else 1

                # 验证页码有效性
                if page < 1:
                    page = 1
                if page > total_pages:
                    page = total_pages

                # 计算分页索引
                start_idx = (page - 1) * limit
                end_idx = min(start_idx + limit, total_items)

                # 提取当前页的项目
                page_items = dir_items[start_idx:end_idx]

                # 转换为应答格式
                for item, item_path, is_dir in page_items:
                    try:
                        item_stat = os.stat(item_path)

                        # 构建相对路径
                        item_rel_path = os.path.join(path, item).replace('\\', '/')
                        if not item_rel_path.startswith('/'):
                            item_rel_path = '/' + item_rel_path

                        # 添加项目信息
                        items.append({
                            'name': item,
                            'path': item_rel_path,
                            'type': 'directory' if is_dir else 'file',
                            'size': item_stat.st_size,
                            'modified': int(item_stat.st_mtime)
                        })
                    except (PermissionError, OSError) as e:
                        logger.warning(f"无法获取文件信息: {item_path}, 错误: {str(e)}")
                        continue

            except Exception as e:
                logger.error(f"列出目录内容时出错: {str(e)}")
                return JSONResponse(status_code=500, content={
                    'success': False,
                    'message': f'列出目录内容时出错: {str(e)}'
                })

            logger.debug(f"成功获取路径 {path} 的文件列表，共 {total_items} 项，当前页 {page}/{(total_items + limit - 1) // limit if total_items > 0 else 1}，返回 {len(items)} 项")

            # 返回结果包含分页信息
            return JSONResponse(content={
                'success': True,
                'items': items,
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total_items': total_items,
                    'total_pages': (total_items + limit - 1) // limit if total_items > 0 else 1
                }
            })

        except Exception as e:
            logger.error(f"获取文件列表失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'获取文件列表失败: {str(e)}'
            })

    @app.get("/api/files/tree")
    async def api_files_tree(request: Request, username: str = Depends(require_auth)):
        """获取文件夹树结构"""
        # 使用会话验证
        try:
            # 递归构建文件夹树
            def build_tree(dir_path, rel_path='/'):
                tree = {
                    'name': os.path.basename(dir_path) or 'root',
                    'path': rel_path,
                    'type': 'directory',
                    'children': []
                }

                try:
                    for item in os.listdir(dir_path):
                        item_path = os.path.join(dir_path, item)
                        item_rel_path = os.path.join(rel_path, item).replace('\\', '/')
                        if not item_rel_path.startswith('/'):
                            item_rel_path = '/' + item_rel_path

                        # 只包含文件夹
                        if os.path.isdir(item_path):
                            # 排除某些目录
                            if item not in ['.git', '__pycache__', 'node_modules', 'venv', 'env', '.venv', '.env']:
                                tree['children'].append(build_tree(item_path, item_rel_path))
                except Exception as e:
                    logger.error(f"读取目录 {dir_path} 失败: {str(e)}")

                # 按名称排序子文件夹
                tree['children'].sort(key=lambda x: x['name'].lower())

                return tree

            tree = {
                'name': 'allowed',
                'path': '/',
                'type': 'directory',
                'children': [],
            }
            for rel_path, allowed_path in sorted(allowed_root_map.items()):
                if not allowed_path.exists() or not allowed_path.is_dir():
                    continue
                tree['children'].append(build_tree(str(allowed_path), rel_path))

            return JSONResponse(content={'success': True, 'tree': tree})

        except Exception as e:
            logger.error(f"获取文件夹树失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'获取文件夹树失败: {str(e)}'
            })

    @app.get("/api/files/read")
    async def api_files_read(request: Request, path: str = None, username: str = Depends(require_auth)):
        """读取文件内容"""
        # 使用会话验证
        try:
            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供文件路径'
                })

            # 处理相对路径
            path, full_path = _resolve_allowed_path(path, "文件")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许读取受控目录或配置文件'
                })

            # 检查文件是否存在
            if not os.path.exists(full_path):
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '文件不存在'
                })

            # 检查是否是文件
            if not os.path.isfile(full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '路径不是一个文件'
                })

            # 读取文件内容
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            return JSONResponse(content={'success': True, 'content': content})

        except Exception as e:
            logger.error(f"读取文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'读取文件失败: {str(e)}'
            })

    @app.post("/api/files/write")
    async def api_files_write(request: Request, username: str = Depends(require_auth)):
        """写入文件内容"""
        # 使用会话验证
        try:
            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到写入文件请求: {data}")

            path = data.get('path')
            content = data.get('content')

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供文件路径'
                })

            # 处理相对路径
            path, full_path = _resolve_allowed_path(path, "文件")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许写入受控目录或配置文件'
                })

            # 确保父目录存在
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # 写入文件内容
            # 我们不再自动转义TOML文件中的双引号，因为这会破坏文件格式
            # 对于通知设置等特定API，我们在API层面处理双引号转义

            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.info(f"成功写入文件: {path}")
            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"写入文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'写入文件失败: {str(e)}'
            })

    @app.post("/api/files/create")
    async def api_files_create(request: Request, username: str = Depends(require_auth)):
        """创建文件或文件夹"""
        # 使用会话验证
        try:
            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到创建文件/文件夹请求: {data}")

            path = data.get('path')
            content = data.get('content', '')
            type = data.get('type', 'file')

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供路径'
                })

            path, full_path = _resolve_allowed_path(path, "文件")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许在受控目录中创建文件或文件夹'
                })

            # 检查文件是否已存在
            if os.path.exists(full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '文件或文件夹已存在'
                })

            # 创建文件夹或文件
            if type == 'directory':
                os.makedirs(full_path, exist_ok=True)
                logger.info(f"成功创建文件夹: {path}")
            else:
                # 确保父文件夹存在
                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                # 创建文件并写入内容
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                logger.info(f"成功创建文件: {path}")

            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"创建文件或文件夹失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'创建文件或文件夹失败: {str(e)}'
            })

    @app.post("/api/files/init_plugin_config")
    async def api_init_plugin_config(request: Request, username: str = Depends(require_auth)):
        """初始化插件配置文件。"""
        try:
            data = await request.json()
            path = data.get('path')
            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供文件路径'
                })

            path, full_path = _resolve_allowed_path(path, "文件")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许初始化受控目录中的插件配置文件'
                })

            rel_path = str(full_path.relative_to(root_dir)).replace("\\", "/")
            if not rel_path.startswith("plugins/") or not rel_path.endswith("config.toml"):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '仅支持初始化 plugins/*/config.toml'
                })

            if full_path.exists():
                return JSONResponse(content={'success': True, 'message': '配置文件已存在'})

            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write("# 插件配置文件\n\n[basic]\n# 是否启用插件\nenable = true\n")

            logger.info(f"已初始化插件配置文件: {path}")
            return JSONResponse(content={'success': True, 'message': '配置文件已创建'})
        except Exception as e:
            logger.error(f"初始化插件配置文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'初始化插件配置文件失败: {str(e)}'
            })

    @app.post("/api/files/delete")
    async def api_files_delete(request: Request, username: str = Depends(require_auth)):
        """删除文件或文件夹"""
        # 使用会话验证
        try:
            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到删除文件/文件夹请求: {data}")

            path = data.get('path')

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供路径'
                })

            # 处理相对路径
            path, full_path = _resolve_allowed_path(path, "文件")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许删除受控目录或配置文件'
                })

            # 检查文件或文件夹是否存在
            if not os.path.exists(full_path):
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '文件或文件夹不存在'
                })

            # 删除文件或文件夹
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
                logger.info(f"成功删除文件夹: {path}")
            else:
                os.remove(full_path)
                logger.info(f"成功删除文件: {path}")

            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"删除文件或文件夹失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'删除文件或文件夹失败: {str(e)}'
            })

    @app.post("/api/files/rename")
    async def api_files_rename(request: Request, username: str = Depends(require_auth)):
        """重命名文件或文件夹"""
        # 使用会话验证
        try:
            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到重命名文件/文件夹请求: {data}")

            old_path = data.get('old_path')
            new_path = data.get('new_path')

            if not old_path or not new_path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供旧路径或新路径'
                })

            # 处理相对路径
            old_path, old_full_path = _resolve_allowed_path(old_path, "文件")
            new_path, new_full_path = _resolve_allowed_path(new_path, "文件")
            if old_full_path is None or new_full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许重命名受控目录或配置文件'
                })

            # 检查旧文件是否存在
            if not os.path.exists(old_full_path):
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '原文件或文件夹不存在'
                })

            # 检查新文件是否已存在
            if os.path.exists(new_full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '目标文件或文件夹已存在'
                })

            # 确保父文件夹存在
            os.makedirs(os.path.dirname(new_full_path), exist_ok=True)

            # 重命名文件或文件夹
            shutil.move(old_full_path, new_full_path)

            logger.info(f"成功将 {old_path} 重命名为 {new_path}")
            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"重命名文件或文件夹失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'重命名文件或文件夹失败: {str(e)}'
            })

    @app.post("/api/files/upload")
    async def api_files_upload(request: Request, path: str = Form(...), files: List[UploadFile] = File(...), username: str = Depends(require_auth)):
        """上传文件到指定路径"""
        # 使用会话验证
        try:
            # 处理相对路径
            path, full_path = _resolve_allowed_path(path, "位置")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许上传到受控目录'
                })

            logger.debug(f"用户 {username} 请求上传文件到路径: {path} -> {full_path}")

            # 检查路径是否存在且是目录
            if not os.path.exists(full_path):
                logger.warning(f"上传目标路径不存在: {full_path}")
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '上传目标路径不存在'
                })

            if not os.path.isdir(full_path):
                logger.warning(f"上传目标路径不是目录: {full_path}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '上传目标路径不是一个目录'
                })

            # 获取表单数据中的上传类型
            form = await request.form()
            upload_type = "files"  # 默认为普通文件上传

            # 检查是否是文件夹上传请求
            if "upload_type" in form:
                upload_type = form["upload_type"]
                logger.debug(f"上传类型: {upload_type}")

            # 处理上传的文件
            uploaded_files = []
            errors = []

            # 处理文件夹上传
            is_folder_upload = False
            for file in files:
                if '/' in file.filename or '\\' in file.filename:
                    is_folder_upload = True
                    break

            if is_folder_upload or upload_type == "folder":
                logger.info(f"检测到文件夹上传请求 ({len(files)} 个文件)")

                # 按文件路径分组文件
                folder_files = {}
                for file in files:
                    # 处理文件路径，统一分隔符
                    file_path = file.filename.replace('\\', '/')
                    folder_files[file_path] = file

                # 创建必要的目录结构并保存文件
                for file_path, file in folder_files.items():
                    try:
                        # 如果是隐藏文件或临时文件，跳过
                        if file_path.startswith('.') or file_path.endswith('~'):
                            logger.debug(f"跳过隐藏或临时文件: {file_path}")
                            continue

                        # 获取目录部分
                        dir_part = os.path.dirname(file_path)

                        # 构建目标路径
                        target_dir = full_path
                        if dir_part:
                            target_dir = os.path.join(full_path, dir_part)
                            # 确保目录存在
                            os.makedirs(target_dir, exist_ok=True)

                        # 构建完整的文件路径
                        target_file_path = os.path.join(full_path, file_path)

                        # 检查文件是否已存在
                        if os.path.exists(target_file_path):
                            logger.warning(f"文件已存在: {target_file_path}")
                            errors.append({
                                'filename': file_path,
                                'error': '文件已存在'
                            })
                            continue

                        # 保存文件
                        logger.debug(f"保存上传的文件: {target_file_path}")
                        content = await file.read()
                        with open(target_file_path, "wb") as f:
                            f.write(content)

                        # 记录成功上传的文件
                        file_stats = os.stat(target_file_path)
                        uploaded_files.append({
                            'filename': file_path,
                            'size': file_stats.st_size,
                            'modified': file_stats.st_mtime
                        })

                        logger.info(f"成功上传文件: {file_path} 到 {target_file_path}")

                    except Exception as e:
                        logger.error(f"上传文件 {file_path} 失败: {str(e)}")
                        errors.append({
                            'filename': file_path,
                            'error': str(e)
                        })

                # 返回上传结果
                return JSONResponse(content={
                    'success': True if uploaded_files else False,
                    'message': f'成功上传文件夹中的 {len(uploaded_files)} 个文件，失败 {len(errors)} 个',
                    'uploaded_files': uploaded_files,
                    'errors': errors
                })
            else:
                # 普通文件上传处理（原有代码）
                for file in files:
                    try:
                        # 构建目标文件路径
                        target_file_path = full_path / file.filename

                        # 检查文件是否已存在
                        if os.path.exists(target_file_path):
                            logger.warning(f"文件已存在: {target_file_path}")
                            errors.append({
                                'filename': file.filename,
                                'error': '文件已存在'
                            })
                            continue

                        # 保存文件
                        logger.debug(f"保存上传的文件: {target_file_path}")
                        content = await file.read()
                        with open(target_file_path, "wb") as f:
                            f.write(content)

                        # 记录成功上传的文件
                        file_stats = os.stat(target_file_path)
                        uploaded_files.append({
                            'filename': file.filename,
                            'size': file_stats.st_size,
                            'modified': file_stats.st_mtime
                        })

                        logger.info(f"用户 {username} 成功上传文件: {file.filename} 到 {path}")

                        # 检查是否需要自动解压
                        if "auto_extract" in form and form["auto_extract"] == "true":
                            if file.filename.lower().endswith(('.zip', '.rar', '.7z', '.tar', '.gz', '.tar.gz')):
                                logger.info(f"请求自动解压文件: {file.filename}")
                                # 调用解压函数
                                extract_result = await extract_archive(
                                    archive_path=str(target_file_path),
                                    destination=str(full_path),
                                    overwrite=False
                                )
                                # 合并解压结果
                                if extract_result.get('success', False):
                                    logger.info(f"自动解压成功: {file.filename}")
                                else:
                                    logger.warning(f"自动解压失败: {file.filename}, {extract_result.get('message', '')}")

                    except Exception as e:
                        logger.error(f"上传文件 {file.filename} 失败: {str(e)}")
                        errors.append({
                            'filename': file.filename,
                            'error': str(e)
                        })

                # 返回上传结果
                return JSONResponse(content={
                    'success': True if uploaded_files else False,
                    'message': f'成功上传 {len(uploaded_files)} 个文件，失败 {len(errors)} 个' if uploaded_files else '上传失败',
                    'uploaded_files': uploaded_files,
                    'errors': errors
                })

        except Exception as e:
            logger.error(f"文件上传过程中发生错误: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"服务器错误: {str(e)}"}
            )

    # 添加文件下载API
    @app.get("/api/files/download")
    async def api_files_download(request: Request, path: str = None, username: str = Depends(require_auth)):
        """下载文件"""
        # 使用会话验证
        try:
            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供文件路径'
                })

            # 处理相对路径
            path, full_path = _resolve_allowed_path(path, "文件")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许下载受控目录或配置文件'
                })

            # 检查文件是否存在
            if not os.path.exists(full_path):
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '文件不存在'
                })

            # 检查是否是文件
            if not os.path.isfile(full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '路径不是一个文件'
                })

            # 获取文件名
            filename = os.path.basename(full_path)

            # 使用FileResponse发送文件
            logger.info(f"下载文件: {path}")
            return FileResponse(
                path=full_path,
                filename=filename,
                media_type='application/octet-stream'
            )

        except Exception as e:
            logger.error(f"下载文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'下载文件失败: {str(e)}'
            })
    # 添加压缩包解压API
    @app.post("/api/files/extract")
    async def api_files_extract(
        request: Request,
        file_path: str = Form(...),
        destination: str = Form(...),
        overwrite: bool = Form(False),
        username: str = Depends(require_auth)
    ):
        """解压压缩文件到指定目录"""
        try:
            # 处理相对路径
            file_path, full_file_path = _resolve_allowed_path(file_path, "文件")
            destination, full_destination = _resolve_allowed_path(destination, "位置")
            if full_file_path is None or full_destination is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许在受控目录内解压文件'
                })

            logger.debug(f"用户 {username} 请求解压文件: {file_path} -> {destination}")

            # 确保目标目录存在
            os.makedirs(full_destination, exist_ok=True)

            # 调用解压函数
            return await extract_archive(
                archive_path=str(full_file_path),
                destination=str(full_destination),
                overwrite=overwrite
            )

        except Exception as e:
            logger.error(f"解压文件过程中发生错误: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"服务器错误: {str(e)}"}
            )

    @app.post("/upload")
    async def simple_upload(request: Request, files: List[UploadFile] = File(...), path: str = Form("/")):
        """简化的文件上传API，保存到用户指定的路径"""
        try:
            # 从 Cookie 中获取会话数据
            session_cookie = request.cookies.get("session")
            if not session_cookie:
                logger.debug("未找到会话 Cookie")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 解码会话数据
            try:
                serializer = URLSafeSerializer(config["secret_key"], "session")
                session_data = serializer.loads(session_cookie)

                # 检查会话是否已过期
                expires = session_data.get("expires", 0)
                if expires < time.time():
                    logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
                    return JSONResponse(status_code=401, content={
                        'success': False,
                        'message': '会话已过期，请重新登录'
                    })

                # 会话有效
                username = session_data.get("username")
                logger.debug(f"用户 {username} 访问文件上传API")
            except Exception as e:
                logger.error(f"解析会话数据失败: {str(e)}")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '会话解析失败，请重新登录'
                })

            # 处理相对路径
            path, full_path = _resolve_allowed_path(path, "位置")
            if full_path is None:
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '仅允许上传到受控目录'
                })

            # 确保目录存在
            os.makedirs(full_path, exist_ok=True)

            logger.debug(f"用户 {username} 请求上传文件到路径: {path} -> {full_path}")

            # 处理上传的文件
            uploaded_files = []
            errors = []

            for file in files:
                try:
                    # 构建目标文件路径
                    target_file_path = os.path.join(full_path, file.filename)

                    # 检查文件是否已存在
                    if os.path.exists(target_file_path):
                        logger.warning(f"文件已存在: {target_file_path}")
                        # 添加时间戳后缀，避免覆盖
                        filename_parts = os.path.splitext(file.filename)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        new_filename = f"{filename_parts[0]}_{timestamp}{filename_parts[1]}"
                        target_file_path = os.path.join(full_path, new_filename)
                        logger.debug(f"自动重命名为: {new_filename}")

                    # 保存文件
                    logger.debug(f"保存上传的文件: {target_file_path}")
                    content = await file.read()
                    with open(target_file_path, "wb") as f:
                        f.write(content)

                    # 记录成功上传的文件
                    file_stats = os.stat(target_file_path)
                    uploaded_files.append({
                        'filename': os.path.basename(target_file_path),
                        'size': file_stats.st_size,
                        'modified': file_stats.st_mtime
                    })

                    logger.info(f"用户 {username} 成功上传文件: {os.path.basename(target_file_path)}")

                except Exception as e:
                    logger.error(f"上传文件 {file.filename} 失败: {str(e)}")
                    errors.append({
                        'filename': file.filename,
                        'error': str(e)
                    })

            # 返回上传结果
            return JSONResponse(content={
                'success': True if uploaded_files else False,
                'message': f'成功上传 {len(uploaded_files)} 个文件，失败 {len(errors)} 个' if uploaded_files else '上传失败',
                'uploaded_files': uploaded_files,
                'errors': errors
            })

        except Exception as e:
            logger.error(f"简化上传API处理文件时出错: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"服务器错误: {str(e)}"}
            )

# 添加另一个备用上传API路径，确保多种路径都能正常工作
    @app.post("/api/upload")
    async def api_upload(request: Request, files: List[UploadFile] = File(...), path: str = Form("/")):
        """备用上传API路径 - 转发到简化上传API"""
        return await simple_upload(request, files, path)

# 通知设置页面
