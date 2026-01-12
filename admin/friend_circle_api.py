"""
朋友圈API模块
提供朋友圈相关的API路由和功能
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

# 创建路由器
router = APIRouter(prefix="/api/friend_circle", tags=["friend_circle"])

# 缓存目录
CACHE_DIR = Path(__file__).parent / "cache" / "friend_circle"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 缓存过期时间（秒）
CACHE_EXPIRY = 3600  # 1小时

# 获取bot实例的函数（将在注册路由时设置）
get_bot_instance = None

# 认证检查函数（将在注册路由时设置）
check_auth = None

async def get_friend_circle_list(wxid: str = None, refresh: bool = False, max_id: int = 0) -> Dict[str, Any]:
    """
    获取朋友圈列表

    Args:
        wxid: 要获取朋友圈的用户wxid，如果为None则获取自己的朋友圈
        refresh: 是否强制刷新缓存
        max_id: 朋友圈ID，用于分页获取

    Returns:
        Dict: 朋友圈数据
    """
    bot = get_bot_instance()
    if not bot:
        logger.error("获取朋友圈失败：机器人未初始化")
        raise HTTPException(status_code=500, detail="机器人未初始化")

    # 确定缓存文件路径
    cache_file = CACHE_DIR / f"{wxid or 'self'}_{max_id}.json"

    # 如果不强制刷新且缓存存在且未过期，则使用缓存
    if not refresh and cache_file.exists():
        try:
            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
            cache_time = cache_data.get("cache_time", 0)

            # 检查缓存是否过期
            if time.time() - cache_time < CACHE_EXPIRY:

                return cache_data
        except Exception as e:
            pass

    # 缓存不存在、已过期或强制刷新，从API获取数据
    try:


        # 构建API请求参数
        params = {
            "Wxid": bot.wxid,
            "Fristpagemd5": "",
            "Maxid": max_id
        }

        # 如果指定了wxid，则获取该用户的朋友圈
        if wxid:
            params["Towxid"] = wxid
            result = await bot.bot.get_pyq_detail(wxid=bot.wxid, Towxid=wxid, max_id=max_id)
        else:
            # 否则获取自己的朋友圈
            result = await bot.bot.get_pyq_list(wxid=bot.wxid, max_id=max_id)

        # 添加缓存时间
        result["cache_time"] = time.time()

        # 保存到缓存
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

        return result
    except Exception as e:
        pass
        raise HTTPException(status_code=500, detail=f"获取朋友圈数据失败: {str(e)}")

async def parse_friend_circle_data(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    解析朋友圈数据

    Args:
        data: 原始朋友圈数据

    Returns:
        List[Dict]: 解析后的朋友圈列表
    """
    try:
        items = []

        # 获取朋友圈列表 - 先尝试 ObjectList，如果不存在再尝试 SnsObjectList
        sns_object_list = data.get("ObjectList", data.get("SnsObjectList", []))

        for sns_object in sns_object_list:
            try:
                # 基本信息
                item = {
                    "id": sns_object.get("Id", ""),
                    "wxid": sns_object.get("Username", ""),
                    "nickname": sns_object.get("Nickname", "未知用户"),
                    "create_time": sns_object.get("CreateTime", 0),
                    "create_time_str": datetime.fromtimestamp(sns_object.get("CreateTime", 0)).strftime("%Y-%m-%d %H:%M:%S"),
                    "like_count": sns_object.get("LikeCount", 0),
                    "comment_count": sns_object.get("CommentCount", 0),
                    "media_list": [],
                    "content": "",
                    "raw_data": sns_object
                }

                # 尝试从 ObjectDesc 中提取内容
                object_desc = sns_object.get("ObjectDesc", {})
                if isinstance(object_desc, dict) and "buffer" in object_desc:
                    # 尝试从 XML 中提取 contentDesc
                    buffer = object_desc.get("buffer", "")

                    if "<contentDesc>" in buffer and "</contentDesc>" in buffer:
                        content_start = buffer.find("<contentDesc>") + len("<contentDesc>")
                        content_end = buffer.find("</contentDesc>")
                        if content_start > 0 and content_end > content_start:
                            content = buffer[content_start:content_end]
                            logger.info(f"Raw content: {content}")

                            # 如果内容包含 CDATA
                            if content.startswith("<![CDATA[") and content.endswith("]]>"):
                                content = content[9:-3]  # 移除 CDATA 标记
                                logger.info(f"Processed content: {content}")

                            item["content"] = content
                            logger.info(f"Final content set: {item['content']}")

                    # 提取媒体列表
                    if "<mediaList>" in buffer and "</mediaList>" in buffer:
                        media_start = buffer.find("<mediaList>") + len("<mediaList>")
                        media_end = buffer.find("</mediaList>")
                        if media_start > 0 and media_end > media_start:
                            media_content = buffer[media_start:media_end]

                            # 提取所有媒体项
                            import re
                            media_regex = re.compile(r"<media>(.*?)</media>", re.DOTALL)
                            for match in media_regex.finditer(media_content):
                                media_item = match.group(1)

                                # 提取URL
                                url = ''
                                if "<url>" in media_item and "</url>" in media_item:
                                    url_start = media_item.find("<url>") + len("<url>")
                                    url_end = media_item.find("</url>")
                                    if url_start > 0 and url_end > url_start:
                                        url = media_item[url_start:url_end]
                                elif "<thumb>" in media_item and "</thumb>" in media_item:
                                    thumb_start = media_item.find("<thumb>") + len("<thumb>")
                                    thumb_end = media_item.find("</thumb>")
                                    if thumb_start > 0 and thumb_end > thumb_start:
                                        url = media_item[thumb_start:thumb_end]

                                # 确定媒体类型
                                media_type = 1  # 默认图片
                                if "<type>6</type>" in media_item or "<mediaType>4</mediaType>" in media_item:
                                    media_type = 2  # 视频

                                if url:
                                    # 处理CDATA标记
                                    if url.startswith('<![CDATA[') and url.endswith(']]>'):
                                        url = url[9:-3]  # 移除CDATA标记


                                    item["media_list"].append({"url": url, "type": media_type})

                # 添加到列表
                items.append(item)
            except Exception:
                pass
                continue

        return items
    except Exception:
        pass
        return []

def register_friend_circle_routes(app, auth_func, bot_instance_func):
    """
    注册朋友圈相关路由

    Args:
        app: FastAPI应用实例
        auth_func: 认证检查函数
        bot_instance_func: 获取bot实例的函数
    """
    global check_auth, get_bot_instance
    check_auth = auth_func
    get_bot_instance = bot_instance_func

    # 注册路由
    app.include_router(router)

    # 注意：朋友圈页面路由已移至server.py中直接定义

# API路由

@router.post("/sync")
async def api_sync_friend_circle(request: Request):
    """
    同步朋友圈

    Args:
        request: 请求对象

    Returns:
        JSONResponse: 同步结果
    """
    try:
        # 检查认证状态
        await check_auth(request)

        # 获取bot实例
        bot = get_bot_instance()
        if not bot:
            return JSONResponse(status_code=500, content={"success": False, "error": "机器人未初始化"})

        # 同步朋友圈
        result = await bot.bot.pyq_sync(wxid=bot.wxid)

        return {
            "success": True,
            "data": result
        }
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
    except Exception as e:
        pass
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@router.post("/detail")
async def api_friend_circle_detail(request: Request):
    """
    获取特定人的朋友圈详情

    Args:
        request: 请求对象

    Returns:
        JSONResponse: 朋友圈详情
    """
    try:
        # 检查认证状态
        await check_auth(request)

        # 获取请求数据
        data = await request.json()
        wxid = data.get("wxid", "")
        towxid = data.get("towxid", "")
        maxid = data.get("maxid", 0)



        # 获取bot实例
        bot = get_bot_instance()
        if not bot:
            return JSONResponse(status_code=500, content={"success": False, "error": "机器人未初始化"})

        # 调用API获取朋友圈详情
        # 根据错误日志，移除Fristpagemd5参数
        result = await bot.bot.get_pyq_detail(wxid=wxid, Towxid=towxid, max_id=maxid)

        # 解析朋友圈数据
        items = await parse_friend_circle_data(result)

        return {
            "success": True,
            "data": {
                "ObjectList": items,
                "raw_data": result
            }
        }
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
    except Exception as e:
        pass
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@router.get("/list")
async def api_friend_circle_list(
    request: Request,
    wxid: Optional[str] = None,
    keyword: Optional[str] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    refresh: bool = False,
    max_id: int = 0,
    page: int = 1,
    limit: int = 20
):
    """
    获取朋友圈列表

    Args:
        request: 请求对象
        wxid: 要获取朋友圈的用户wxid，如果为None则获取自己的朋友圈
        keyword: 搜索关键词
        date_start: 开始日期（YYYY-MM-DD）
        date_end: 结束日期（YYYY-MM-DD）
        refresh: 是否强制刷新缓存
        max_id: 朋友圈ID，用于分页获取
        page: 页码
        limit: 每页数量

    Returns:
        JSONResponse: 朋友圈列表
    """
    try:
        # 检查认证状态
        await check_auth(request)

        # 获取朋友圈数据
        result = await get_friend_circle_list(wxid, refresh, max_id)

        # 解析朋友圈数据
        items = await parse_friend_circle_data(result)

        # 过滤数据
        if keyword:
            items = [item for item in items if keyword.lower() in item.get("content", "").lower()]

        if date_start:
            start_timestamp = int(datetime.strptime(date_start, "%Y-%m-%d").timestamp())
            items = [item for item in items if item.get("create_time", 0) >= start_timestamp]

        if date_end:
            end_timestamp = int(datetime.strptime(date_end, "%Y-%m-%d").timestamp()) + 86400  # 加一天
            items = [item for item in items if item.get("create_time", 0) <= end_timestamp]

        # 分页
        total = len(items)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        items = items[start_idx:end_idx]

        # 获取最后一条朋友圈ID，用于下一页
        last_id = 0
        if items:
            last_id = items[-1].get("id", 0)

        return {
            "success": True,
            "data": {
                "items": items,
                "total": total,
                "page": page,
                "limit": limit,
                "last_id": last_id,
                "has_more": total > page * limit
            }
        }
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
    except Exception as e:
        pass
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@router.get("/user/{wxid}")
async def api_user_friend_circle(
    request: Request,
    wxid: str,
    refresh: bool = False,
    max_id: int = 0,
    page: int = 1,
    limit: int = 20
):
    """
    获取特定用户的朋友圈

    Args:
        request: 请求对象
        wxid: 用户wxid
        refresh: 是否强制刷新缓存
        max_id: 朋友圈ID，用于分页获取

    Returns:
        JSONResponse: 用户朋友圈
    """
    try:
        # 检查认证状态
        await check_auth(request)

        # 获取用户朋友圈
        result = await get_friend_circle_list(wxid, refresh, max_id)

        # 解析朋友圈数据
        items = await parse_friend_circle_data(result)

        # 分页
        total = len(items)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        items = items[start_idx:end_idx]

        # 获取最后一条朋友圈ID，用于下一页
        last_id = 0
        if items:
            last_id = items[-1].get("id", 0)

        return {
            "success": True,
            "data": {
                "items": items,
                "total": total,
                "page": page,
                "limit": limit,
                "last_id": last_id,
                "has_more": total > page * limit
            }
        }
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
    except Exception as e:
        logger.error(f"获取用户朋友圈失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@router.post("/like/{id}")
async def api_like_friend_circle(
    request: Request,
    id: str
):
    """
    点赞朋友圈

    Args:
        request: 请求对象
        id: 朋友圈ID

    Returns:
        JSONResponse: 点赞结果
    """
    try:
        # 检查认证状态
        await check_auth(request)

        # 获取bot实例
        bot = get_bot_instance()
        if not bot:
            return JSONResponse(status_code=500, content={"success": False, "error": "机器人未初始化"})

        # 点赞朋友圈
        result = await bot.bot.put_pyq_comment(wxid=bot.wxid, id=id, type=1)

        return {
            "success": True,
            "data": result
        }
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
    except Exception as e:
        pass
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@router.post("/comment/{id}")
async def api_comment_friend_circle(
    request: Request,
    id: str,
    content: str
):
    """
    评论朋友圈

    Args:
        request: 请求对象
        id: 朋友圈ID
        content: 评论内容

    Returns:
        JSONResponse: 评论结果
    """
    try:
        # 检查认证状态
        await check_auth(request)

        # 获取bot实例
        bot = get_bot_instance()
        if not bot:
            return JSONResponse(status_code=500, content={"success": False, "error": "机器人未初始化"})

        # 评论朋友圈
        result = await bot.bot.put_pyq_comment(wxid=bot.wxid, id=id, type=2, content=content)

        return {
            "success": True,
            "data": result
        }
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
    except Exception as e:
        pass
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
