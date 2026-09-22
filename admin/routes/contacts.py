"""
@input: FastAPI app、require_auth、联系人数据库函数与 bot 实例解析函数
@output: 联系人列表/详情/群成员相关 API（含缓存兜底与批量详情）
@position: 管理后台联系人路由层，负责前端联系人数据聚合
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import os
import time
import asyncio
import json
from fastapi import Request, Depends
from fastapi.responses import JSONResponse
from loguru import logger


def register_contacts_routes(app, bot_instance, get_bot_instance, get_bot_status,
                             get_contacts_from_db, save_contacts_to_db, update_contact_in_db,
                             get_contact_from_db, get_contacts_count):
    """
    注册联系人管理相关路由
    
    Args:
        app: FastAPI 应用实例
        check_auth: 认证检查函数
        bot_instance: 机器人实例
        get_bot_instance: 获取机器人实例函数
        get_bot_status: 获取机器人状态函数
        get_contacts_from_db: 从数据库获取联系人函数
        save_contacts_to_db: 保存联系人到数据库函数
        update_contact_in_db: 更新数据库中联系人函数
        get_contact_from_db: 从数据库获取单个联系人函数
        get_contacts_count: 获取联系人数量函数
    """
    from admin.utils import require_auth

    def _resolve_bot_wrapper():
        current = get_bot_instance() if callable(get_bot_instance) else None
        if current is not None:
            return current
        return bot_instance

    def _resolve_wechat_client():
        wrapper = _resolve_bot_wrapper()
        if wrapper is None:
            return None
        return getattr(wrapper, "bot", wrapper)

    @app.get("/api/contacts/update_all", response_class=JSONResponse)
    async def api_update_all_contacts(request: Request, username: str = Depends(require_auth)):
        """更新数据库中所有联系人信息

        Args:
            request: 请求对象
        """
        logger.info(f"用户 {username} 请求更新数据库中所有联系人信息")

        try:
            wrapper = _resolve_bot_wrapper()
            wxapi = _resolve_wechat_client()
            # 确保bot_instance可用
            if wxapi is None:
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(content={
                    "success": False,
                    "error": "机器人实例未初始化，请确保机器人已启动"
                })

            # 检查get_contract_detail方法
            if not hasattr(wxapi, 'get_contract_detail'):
                logger.error("bot.get_contract_detail方法不存在")
                return JSONResponse(content={
                    "success": False,
                    "error": "微信API不支持获取联系人详情"
                })

            # 保存原始wxid
            original_wxid = None
            if hasattr(wxapi, 'wxid'):
                original_wxid = wxapi.wxid

            # 设置wxid
            if wrapper is not None and hasattr(wrapper, "wxid"):
                wxapi.wxid = wrapper.wxid

            # 从数据库中获取所有联系人
            from database.contacts_db import get_all_contacts
            all_contacts = get_all_contacts()

            if not all_contacts:
                logger.warning("数据库中没有联系人信息")
                return JSONResponse(content={
                    "success": False,
                    "error": "数据库中没有联系人信息"
                })

            logger.info(f"从数据库中获取到 {len(all_contacts)} 个联系人")

            # 创建异步任务列表
            import asyncio
            update_tasks = []
            updated_count = 0
            failed_count = 0

            # 每批处理的联系人数量
            batch_size = 20

            # 分批处理联系人
            for i in range(0, len(all_contacts), batch_size):
                batch = all_contacts[i:i+batch_size]

                # 处理当前批次
                for contact in batch:
                    wxid = contact.get('wxid')
                    if not wxid:
                        continue

                    try:
                        # 调用API获取联系人详情
                        if asyncio.get_event_loop().is_running():
                            detail = await wxapi.get_contract_detail(wxid)
                        else:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                detail = loop.run_until_complete(wxapi.get_contract_detail(wxid))
                            finally:
                                loop.close()

                        # 处理返回数据
                        if not detail:
                            logger.warning(f"获取联系人 {wxid} 详情失败，返回空数据")
                            failed_count += 1
                            continue

                        # 处理返回结果
                        contact_info = None
                        if isinstance(detail, list) and len(detail) > 0:
                            detail_item = detail[0]

                            # 处理昵称
                            nickname = ""
                            if 'NickName' in detail_item:
                                if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                                    nickname = detail_item['NickName']['string']
                                else:
                                    nickname = str(detail_item['NickName'])
                            elif 'nickname' in detail_item:
                                nickname = detail_item.get('nickname')

                            # 处理头像
                            avatar = ""
                            if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                                avatar = detail_item['BigHeadImgUrl']
                                logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                            elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                                avatar = detail_item['SmallHeadImgUrl']
                                logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                            elif 'avatar' in detail_item:
                                avatar = detail_item.get('avatar')

                            # 处理备注
                            remark = ""
                            if 'Remark' in detail_item:
                                if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                                    remark = detail_item['Remark']['string']
                                elif isinstance(detail_item['Remark'], str):
                                    remark = detail_item['Remark']
                            elif 'remark' in detail_item:
                                remark = detail_item.get('remark')

                            # 处理微信号
                            alias = ""
                            if 'Alias' in detail_item:
                                alias = detail_item.get('Alias')
                            elif 'alias' in detail_item:
                                alias = detail_item.get('alias')

                            # 确定联系人类型
                            contact_type = "friend"
                            if wxid.endswith("@chatroom"):
                                contact_type = "group"
                            elif wxid.startswith("gh_"):
                                contact_type = "official"

                            # 创建联系人信息对象
                            contact_info = {
                                'wxid': wxid,
                                'nickname': nickname or wxid,
                                'avatar': avatar or '',
                                'remark': remark or '',
                                'alias': alias or '',
                                'type': contact_type
                            }
                        elif isinstance(detail, dict):
                            # 如果是字典，直接使用
                            detail_item = detail

                            # 处理昵称
                            nickname = ""
                            if 'NickName' in detail_item:
                                if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                                    nickname = detail_item['NickName']['string']
                                else:
                                    nickname = str(detail_item['NickName'])
                            elif 'nickname' in detail_item:
                                nickname = detail_item.get('nickname')

                            # 处理头像
                            avatar = ""
                            if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                                avatar = detail_item['BigHeadImgUrl']
                                logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                            elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                                avatar = detail_item['SmallHeadImgUrl']
                                logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                            elif 'avatar' in detail_item:
                                avatar = detail_item.get('avatar')

                            # 处理备注
                            remark = ""
                            if 'Remark' in detail_item:
                                if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                                    remark = detail_item['Remark']['string']
                                elif isinstance(detail_item['Remark'], str):
                                    remark = detail_item['Remark']
                            elif 'remark' in detail_item:
                                remark = detail_item.get('remark')

                            # 处理微信号
                            alias = ""
                            if 'Alias' in detail_item:
                                alias = detail_item.get('Alias')
                            elif 'alias' in detail_item:
                                alias = detail_item.get('alias')

                            # 确定联系人类型
                            contact_type = "friend"
                            if wxid.endswith("@chatroom"):
                                contact_type = "group"
                            elif wxid.startswith("gh_"):
                                contact_type = "official"

                            # 创建联系人信息对象
                            contact_info = {
                                'wxid': wxid,
                                'nickname': nickname or wxid,
                                'avatar': avatar or '',
                                'remark': remark or '',
                                'alias': alias or '',
                                'type': contact_type
                            }

                        # 将联系人信息保存到数据库
                        if contact_info:
                            from database.contacts_db import update_contact_in_db
                            update_contact_in_db(contact_info)
                            updated_count += 1
                            logger.info(f"已将联系人 {wxid} 信息更新到数据库")
                        else:
                            failed_count += 1
                            logger.warning(f"无法解析联系人 {wxid} 的详情")
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"更新联系人 {wxid} 信息失败: {str(e)}")

                # 每批处理完成后等待一小段时间，避免请求过于频繁
                await asyncio.sleep(1)

            # 恢复原始wxid
            if original_wxid is not None:
                wxapi.wxid = original_wxid

            # 返回结果
            return JSONResponse(content={
                "success": True,
                "message": f"成功更新 {updated_count} 个联系人信息，失败 {failed_count} 个",
                "updated_count": updated_count,
                "failed_count": failed_count,
                "total_count": len(all_contacts)
            })

        except Exception as e:
            import traceback
            logger.error(f"更新所有联系人信息失败: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(content={
                "success": False,
                "error": f"更新所有联系人信息失败: {str(e)}"
            })

    # API: 刷新单个联系人信息
    @app.get("/api/contacts/{wxid}/refresh", response_class=JSONResponse)
    async def api_refresh_contact(wxid: str, request: Request, username: str = Depends(require_auth)):
        """刷新单个联系人信息

        Args:
            wxid: 联系人的wxid
            request: 请求对象
        """
        logger.info(f"用户 {username} 请求刷新联系人 {wxid} 的信息")

        try:
            wrapper = _resolve_bot_wrapper()
            wxapi = _resolve_wechat_client()

            # 确保 bot 实例可用
            if wxapi is None:
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(content={
                    "success": False,
                    "error": "机器人实例未初始化，请确保机器人已启动"
                })

            # 检查get_contract_detail方法
            if not hasattr(wxapi, 'get_contract_detail'):
                logger.error("bot.get_contract_detail方法不存在")
                return JSONResponse(content={
                    "success": False,
                    "error": "微信API不支持获取联系人详情"
                })

            # 保存原始wxid
            original_wxid = getattr(wxapi, "wxid", None) if hasattr(wxapi, "wxid") else None

            # 设置wxid并调用API
            if wrapper is not None and hasattr(wrapper, "wxid") and getattr(wrapper, "wxid") and hasattr(wxapi, "wxid"):
                wxapi.wxid = getattr(wrapper, "wxid")

            # 调用API获取联系人详情
            import asyncio
            if asyncio.get_event_loop().is_running():
                detail = await wxapi.get_contract_detail(wxid)
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    detail = loop.run_until_complete(wxapi.get_contract_detail(wxid))
                finally:
                    loop.close()

            # 恢复原始wxid
            if original_wxid is not None:
                try:
                    if hasattr(wxapi, "wxid"):
                        wxapi.wxid = original_wxid
                except Exception:
                    pass

            # 处理返回数据
            if not detail:
                logger.warning(f"获取联系人 {wxid} 详情失败，返回空数据")
                return JSONResponse(content={
                    "success": False,
                    "error": "获取联系人详情失败，返回空数据"
                })

            # 处理返回结果
            contact_info = None
            if isinstance(detail, list) and len(detail) > 0:
                detail_item = detail[0]
                logger.debug(f"联系人 {wxid} 详情项类型: {type(detail_item)}")

                # 处理昵称
                nickname = ""
                if 'NickName' in detail_item:
                    if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                        nickname = detail_item['NickName']['string']
                    else:
                        nickname = str(detail_item['NickName'])
                elif 'nickname' in detail_item:
                    nickname = detail_item.get('nickname')

                # 处理头像
                avatar = ""
                if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                    avatar = detail_item['BigHeadImgUrl']
                    logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                    avatar = detail_item['SmallHeadImgUrl']
                    logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                elif 'avatar' in detail_item:
                    avatar = detail_item.get('avatar')

                # 处理备注
                remark = ""
                if 'Remark' in detail_item:
                    if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                        remark = detail_item['Remark']['string']
                    elif isinstance(detail_item['Remark'], str):
                        remark = detail_item['Remark']
                elif 'remark' in detail_item:
                    remark = detail_item.get('remark')

                # 处理微信号
                alias = ""
                if 'Alias' in detail_item:
                    alias = detail_item.get('Alias')
                elif 'alias' in detail_item:
                    alias = detail_item.get('alias')

                # 确定联系人类型
                contact_type = "friend"
                if wxid.endswith("@chatroom"):
                    contact_type = "group"
                elif wxid.startswith("gh_"):
                    contact_type = "official"

                # 创建联系人信息对象
                contact_info = {
                    'wxid': wxid,
                    'nickname': nickname or wxid,
                    'avatar': avatar or '',
                    'remark': remark or '',
                    'alias': alias or '',
                    'type': contact_type
                }

                # 将联系人信息保存到数据库
                try:
                    update_contact_in_db(contact_info)
                    logger.info(f"已将联系人 {wxid} 信息更新到数据库")
                except Exception as e:
                    logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")
            elif isinstance(detail, dict):
                # 如果是字典，直接使用
                detail_item = detail
                logger.debug(f"联系人 {wxid} 详情项类型: {type(detail_item)}")

                # 处理昵称
                nickname = ""
                if 'NickName' in detail_item:
                    if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                        nickname = detail_item['NickName']['string']
                    else:
                        nickname = str(detail_item['NickName'])
                elif 'nickname' in detail_item:
                    nickname = detail_item.get('nickname')

                # 处理头像
                avatar = ""
                if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                    avatar = detail_item['BigHeadImgUrl']
                    logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                    avatar = detail_item['SmallHeadImgUrl']
                    logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                elif 'avatar' in detail_item:
                    avatar = detail_item.get('avatar')

                # 处理备注
                remark = ""
                if 'Remark' in detail_item:
                    if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                        remark = detail_item['Remark']['string']
                    elif isinstance(detail_item['Remark'], str):
                        remark = detail_item['Remark']
                elif 'remark' in detail_item:
                    remark = detail_item.get('remark')

                # 处理微信号
                alias = ""
                if 'Alias' in detail_item:
                    alias = detail_item.get('Alias')
                elif 'alias' in detail_item:
                    alias = detail_item.get('alias')

                # 确定联系人类型
                contact_type = "friend"
                if wxid.endswith("@chatroom"):
                    contact_type = "group"
                elif wxid.startswith("gh_"):
                    contact_type = "official"

                # 创建联系人信息对象
                contact_info = {
                    'wxid': wxid,
                    'nickname': nickname or wxid,
                    'avatar': avatar or '',
                    'remark': remark or '',
                    'alias': alias or '',
                    'type': contact_type
                }

                # 将联系人信息保存到数据库
                try:
                    update_contact_in_db(contact_info)
                    logger.info(f"已将联系人 {wxid} 信息更新到数据库")
                except Exception as e:
                    logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")

            if contact_info:
                return JSONResponse(content={
                    "success": True,
                    "data": contact_info
                })
            else:
                return JSONResponse(content={
                    "success": False,
                    "error": "无法解析联系人详情"
                })

        except Exception as e:
            import traceback
            logger.error(f"刷新联系人 {wxid} 信息失败: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(content={
                "success": False,
                "error": f"刷新联系人信息失败: {str(e)}"
            })

    # API: 联系人管理 (需要认证)
    @app.get("/api/contacts", response_class=JSONResponse)
    async def api_contacts(request: Request, refresh: bool = False, page: int = 0, page_size: int = 0, username: str = Depends(require_auth)):
        """获取联系人列表

        Args:
            request: 请求对象
            refresh: 是否强制刷新
            page: 页码（从1开始），设为0表示不分页，返回所有联系人
            page_size: 每页数量，设为0表示不分页，返回所有联系人
        """

        # 先尝试从数据库获取联系人列表
        try:
            # 如果不是强制刷新且数据库中有联系人数据，直接返回
            contacts_count = get_contacts_count()
            if not refresh and contacts_count > 0:
                logger.info(f"从数据库加载{contacts_count}个联系人")

                # 如果指定了分页参数，则返回分页数据
                if page > 0 and page_size > 0:
                    # 计算偏移量和限制
                    offset = (page - 1) * page_size

                    # 获取分页数据
                    contacts = get_contacts_from_db(offset=offset, limit=page_size)

                    # 计算总页数
                    total_pages = (contacts_count + page_size - 1) // page_size

                    return JSONResponse(content={
                        "success": True,
                        "data": contacts,
                        "timestamp": int(time.time()),
                        "pagination": {
                            "page": page,
                            "page_size": page_size,
                            "total": contacts_count,
                            "total_pages": total_pages
                        }
                    })
                else:
                    # 如果没有指定分页参数，返回所有数据
                    contacts = get_contacts_from_db()
                    return JSONResponse(content={
                        "success": True,
                        "data": contacts,
                        "timestamp": int(time.time())
                    })
        except Exception as e:
            logger.error(f"从数据库获取联系人失败: {str(e)}")

        # 如果数据库中没有数据或需要强制刷新，则从微信API获取
        logger.info("请求联系人列表API")

        # 使用固定wxid调用微信API获取联系人列表
        try:
            wrapper = _resolve_bot_wrapper()
            wxapi = _resolve_wechat_client()

            # 确保 bot 实例可用
            if wxapi is None:
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(content={
                    "success": False,
                    "error": "机器人实例未初始化，请确保机器人已启动",
                    "data": []
                })

            # 检查get_contract_list方法
            if not hasattr(wxapi, 'get_contract_list'):
                logger.error("bot.get_contract_list方法不存在")
                return JSONResponse(content={
                    "success": False,
                    "error": "微信API不支持获取联系人列表",
                    "data": []
                })

            # 获取API请求的URL
            if hasattr(wxapi, 'ip') and hasattr(wxapi, 'port'):
                api_url = f"http://{wxapi.ip}:{wxapi.port}/GetContractList"
                logger.info(f"请求URL: {api_url}")

            # 从 bot 状态中获取微信 ID（仅旧协议依赖）
            bot_status = get_bot_status()
            wxid = None

            # 检查bot_status中是否包含wxid
            if bot_status and "wxid" in bot_status:
                wxid = bot_status["wxid"]
                logger.info(f"从系统状态获取到wxid: {wxid}")
            else:
                # 尝试从 wrapper/bot 实例中获取 wxid
                if wrapper is not None and hasattr(wrapper, "wxid") and getattr(wrapper, "wxid"):
                    wxid = getattr(wrapper, "wxid")
                    logger.info(f"从bot包装实例获取到wxid: {wxid}")
                elif hasattr(wxapi, 'wxid') and getattr(wxapi, "wxid"):
                    wxid = getattr(wxapi, "wxid")
                    logger.info(f"从bot实例获取到wxid: {wxid}")

            request_params = {
                "Wxid": wxid,
                "CurrentWxcontactSeq": 0,
                "CurrentChatroomContactSeq": 0
            }
            logger.info(f"请求方式: POST")
            logger.info(f"请求参数: {request_params}")

            # 保存原始wxid（旧协议需要）
            original_wxid = getattr(wxapi, "wxid", None) if hasattr(wxapi, "wxid") else None
            if wxid and hasattr(wxapi, "wxid"):
                wxapi.wxid = wxid

            # 调用API获取联系人
            import asyncio
            import traceback

            # 初始化序列号
            wx_seq = 0
            chatroom_seq = 0
            all_contacts_data = {"ContactUsernameList": []}

            # 首先尝试使用新的API方法
            try:
                logger.info("尝试使用新的GetTotalContractList API获取联系人列表")
                if hasattr(wxapi, 'get_total_contract_list'):
                    if asyncio.get_event_loop().is_running():
                        contacts_data = await wxapi.get_total_contract_list(wx_seq=0, chatroom_seq=0)
                    else:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            contacts_data = loop.run_until_complete(wxapi.get_total_contract_list(wx_seq=0, chatroom_seq=0))
                        finally:
                            loop.close()
                    logger.info("成功使用新的GetTotalContractList API获取联系人列表")
                    all_contacts_data = contacts_data
                else:
                    raise AttributeError("Bot实例没有get_total_contract_list方法")
            except Exception as e:
                # 如果新API失败，回退到旧方法
                logger.warning(f"使用新API获取联系人失败: {str(e)}，回退到旧方法")
                logger.debug(traceback.format_exc())

                # 递归获取所有联系人
                iteration = 0
                total_contacts = 0

                # 不设置最大迭代次数，由系统自动识别何时已获取所有联系人
                while True:
                    iteration += 1
                    logger.info(f"获取联系人批次 {iteration}，当前序列号: wx_seq={wx_seq}, chatroom_seq={chatroom_seq}")

                    # 获取当前批次的联系人
                    if asyncio.get_event_loop().is_running():
                        batch_data = await wxapi.get_contract_list(wx_seq=wx_seq, chatroom_seq=chatroom_seq)
                    else:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            batch_data = loop.run_until_complete(wxapi.get_contract_list(wx_seq=wx_seq, chatroom_seq=chatroom_seq))
                        finally:
                            loop.close()

                    # 检查返回数据
                    if not batch_data or not isinstance(batch_data, dict) or 'ContactUsernameList' not in batch_data:
                        logger.warning(f"批次 {iteration} 返回数据无效或格式不正确")
                        break

                    # 获取当前批次的联系人数量
                    batch_contacts = batch_data.get('ContactUsernameList', [])
                    batch_count = len(batch_contacts)
                    total_contacts += batch_count
                    logger.info(f"批次 {iteration} 获取到 {batch_count} 个联系人，累计 {total_contacts} 个")

                    # 合并联系人列表
                    if iteration == 1:
                        # 第一批次，直接使用返回数据
                        all_contacts_data = batch_data
                    else:
                        # 后续批次，合并联系人列表
                        all_contacts_data['ContactUsernameList'].extend(batch_contacts)

                    # 检查是否有新的序列号
                    new_wx_seq = batch_data.get('CurrentWxcontactSeq', 0)
                    new_chatroom_seq = batch_data.get('CurrentChatroomContactSeq', 0)

                    # 如果序列号没有变化或者没有返回联系人，说明已经获取完所有联系人
                    if (new_wx_seq == wx_seq and new_chatroom_seq == chatroom_seq) or batch_count == 0:
                        logger.info(f"序列号没有变化或者没有新的联系人，结束获取")
                        break

                    # 更新序列号继续获取
                    wx_seq = new_wx_seq
                    chatroom_seq = new_chatroom_seq

                # 使用合并后的数据
                contacts_data = all_contacts_data

            # 恢复原始wxid
            if original_wxid is not None:
                try:
                    if hasattr(wxapi, "wxid"):
                        wxapi.wxid = original_wxid
                except Exception:
                    pass

            # 打印返回数据的完整结构，帮助调试
            logger.debug(f"API返回数据结构: {contacts_data}")

            # 检查返回数据
            if not contacts_data or not isinstance(contacts_data, dict):
                logger.error(f"API返回数据无效: {contacts_data}")
                return JSONResponse(content={
                    "success": False,
                    "error": "获取联系人列表失败，返回数据无效",
                    "data": []
                })

            # 检查ContactUsernameList字段 - 直接在顶层
            if 'ContactUsernameList' not in contacts_data or not isinstance(contacts_data['ContactUsernameList'], list):
                logger.error(f"返回数据中没有ContactUsernameList字段或格式不正确: {contacts_data}")
                return JSONResponse(content={
                    "success": False,
                    "error": "获取联系人列表失败，返回数据格式不正确",
                    "data": []
                })

            # 提取联系人列表
            contact_usernames = contacts_data['ContactUsernameList']
            logger.info(f"找到{len(contact_usernames)}个联系人ID")

            # 构建联系人对象
            contact_list = []

            # 检查是否支持获取联系人详情
            has_contract_detail_method = hasattr(wxapi, 'get_contract_detail')

            if has_contract_detail_method:
                logger.info("使用get_contract_detail方法获取联系人详细信息")

                # 分批获取联系人详情 (每批最多20个)
                batch_size = 20
                all_contact_details = {}

                # 计算总批次数
                total_batches = (len(contact_usernames) + batch_size - 1) // batch_size
                logger.info(f"联系人总数: {len(contact_usernames)}, 批次大小: {batch_size}, 总批次: {total_batches}")

                # 不限制批次数量，获取所有联系人
                max_batches = total_batches  # 获取所有批次

                for i in range(0, min(max_batches * batch_size, len(contact_usernames)), batch_size):
                    batch = contact_usernames[i:i+batch_size]
                    logger.debug(f"获取联系人详情批次 {i//batch_size+1}/{total_batches}: {batch}")

                    try:
                        # 调用API获取联系人详情
                        if asyncio.get_event_loop().is_running():
                            contact_details = await wxapi.get_contract_detail(batch)
                        else:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                contact_details = loop.run_until_complete(wxapi.get_contract_detail(batch))
                            finally:
                                loop.close()

                        # 改为INFO级别确保输出
                        logger.info(f"批次{i//batch_size+1}获取到联系人详情: {len(contact_details)}个")

                        # 强制打印每个批次的第一个联系人信息作为样本
                        if contact_details and len(contact_details) > 0:
                            logger.info(f"联系人详情样本结构: {json.dumps(contact_details[0], ensure_ascii=False)}")

                        # 显式记录关键字段是否存在
                        if contact_details and len(contact_details) > 0:
                            first_contact = contact_details[0]
                            logger.info(f"联系人[{first_contact.get('Username', 'unknown')}]有以下字段: {sorted(first_contact.keys())}")

                            # 检查并记录关键字段
                            for field in ['Username', 'NickName', 'Remark', 'SmallHeadImgUrl', 'BigHeadImgUrl']:
                                if field in first_contact:
                                    logger.info(f"字段[{field}]存在，值为: {first_contact[field]}")
                                else:
                                    logger.info(f"字段[{field}]不存在")

                        # 将联系人详情与wxid关联
                        for contact_detail in contact_details:
                            # 处理各种可能的用户ID字段
                            wxid_found = False

                            # 处理UserName字段
                            if 'UserName' in contact_detail and contact_detail['UserName']:
                                # 处理嵌套结构，UserName可能是{"string": "wxid"}格式
                                if isinstance(contact_detail['UserName'], dict) and 'string' in contact_detail['UserName']:
                                    wxid = contact_detail['UserName']['string']
                                    wxid_found = True
                                else:
                                    wxid = str(contact_detail['UserName'])
                                    wxid_found = True

                            # 如果没有UserName，尝试Username字段
                            elif 'Username' in contact_detail and contact_detail['Username']:
                                if isinstance(contact_detail['Username'], dict) and 'string' in contact_detail['Username']:
                                    wxid = contact_detail['Username']['string']
                                    wxid_found = True
                                else:
                                    wxid = str(contact_detail['Username'])
                                    wxid_found = True

                            # 如果没有Username，尝试wxid字段
                            elif 'wxid' in contact_detail and contact_detail['wxid']:
                                wxid = contact_detail['wxid']
                                wxid_found = True

                            # 如果没有找到任何ID字段，跳过这个联系人
                            if not wxid_found:
                                logger.warning(f"联系人缺少ID字段: {contact_detail}")
                                continue

                            all_contact_details[wxid] = contact_detail
                            if wxid in batch[:3]:  # 只记录前3个，避免日志过多
                                logger.info(f"联系人[{wxid}]头像信息: " +
                                          f"SmallHeadImgUrl={contact_detail.get('SmallHeadImgUrl', 'None')}, " +
                                          f"BigHeadImgUrl={contact_detail.get('BigHeadImgUrl', 'None')}")
                    except Exception as e:
                        logger.error(f"获取联系人详情批次失败 ({i}~{i+batch_size-1}): {e}")
                        logger.error(traceback.format_exc())

                # 根据获取的详细信息创建联系人对象
                for username in contact_usernames:
                    # 根据wxid格式确定联系人类型
                    contact_type = "friend"
                    if username.endswith("@chatroom"):
                        contact_type = "group"
                    elif username.startswith("gh_"):
                        contact_type = "official"

                    # 获取联系人详情
                    contact_detail = all_contact_details.get(username, {})

                    # 提取字段
                    nickname = ""
                    remark = ""
                    avatar = "/static/img/favicon.ico"

                    # 提取昵称 - 处理各种可能的字段名称和结构
                    nickname = ""
                    if contact_detail:
                        # 处理NickName字段
                        if 'NickName' in contact_detail:
                            if isinstance(contact_detail['NickName'], dict) and 'string' in contact_detail['NickName']:
                                nickname = contact_detail['NickName']['string']
                            else:
                                nickname = str(contact_detail['NickName'])
                        # 处理nickname字段
                        elif 'nickname' in contact_detail:
                            nickname = contact_detail.get('nickname')

                    # 如果昵称为空，使用用户名
                    if not nickname:
                        nickname = username

                    # 提取备注 - 处理各种可能的字段名称和结构
                    remark = ""
                    if contact_detail:
                        # 处理Remark字段
                        if 'Remark' in contact_detail:
                            if isinstance(contact_detail['Remark'], dict) and 'string' in contact_detail['Remark']:
                                remark = contact_detail['Remark']['string']
                            elif isinstance(contact_detail['Remark'], str):
                                remark = contact_detail['Remark']
                        # 处理remark字段
                        elif 'remark' in contact_detail:
                            remark = contact_detail.get('remark')

                    # 提取头像 URL - 处理各种可能的字段名称
                    avatar = "/static/img/favicon.ico"  # 默认头像
                    if contact_detail:
                        # 优先使用小头像
                        if 'SmallHeadImgUrl' in contact_detail and contact_detail['SmallHeadImgUrl']:
                            avatar = contact_detail['SmallHeadImgUrl']
                            logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                        # 其次使用大头像
                        elif 'BigHeadImgUrl' in contact_detail and contact_detail['BigHeadImgUrl']:
                            avatar = contact_detail['BigHeadImgUrl']
                            logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                        # 最后尝试avatar字段
                        elif 'avatar' in contact_detail and contact_detail['avatar']:
                            avatar = contact_detail['avatar']
                            logger.debug(f"使用avatar作为头像: {avatar}")

                    # 确定显示名称（优先使用备注，其次昵称，最后是wxid）
                    display_name = remark or nickname or username

                    # 创建联系人对象
                    contact = {
                        "wxid": username,
                        "name": display_name,
                        "nickname": nickname,
                        "remark": remark,
                        "avatar": avatar,
                        "type": contact_type,
                        "online": True,
                        "starred": False
                    }
                    contact_list.append(contact)
            else:
                # 回退到使用昵称API
                has_nickname_method = hasattr(wxapi, 'get_nickname')
                if has_nickname_method:
                    logger.info("使用get_nickname方法获取联系人昵称")

                    # 分批获取联系人昵称 (每批最多20个)
                    batch_size = 20
                    all_nicknames = {}

                    # 计算总批次数
                    total_batches = (len(contact_usernames) + batch_size - 1) // batch_size
                    logger.info(f"联系人总数: {len(contact_usernames)}, 批次大小: {batch_size}, 总批次: {total_batches}")

                    # 不限制批次数量，获取所有联系人
                    max_batches = total_batches  # 获取所有批次

                    # 分批处理联系人
                    for i in range(0, min(max_batches * batch_size, len(contact_usernames)), batch_size):
                        batch = contact_usernames[i:i+batch_size]
                        logger.debug(f"获取联系人昵称批次 {i//batch_size+1}/{total_batches}: {batch}")
                        try:
                            # 调用API获取昵称
                            if asyncio.get_event_loop().is_running():
                                nicknames = await wxapi.get_nickname(batch)
                            else:
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    nicknames = loop.run_until_complete(wxapi.get_nickname(batch))
                                finally:
                                    loop.close()

                            # 将昵称与wxid关联
                            for j, wxid in enumerate(batch):
                                if j < len(nicknames) and nicknames[j]:
                                    all_nicknames[wxid] = nicknames[j]
                                else:
                                    all_nicknames[wxid] = wxid
                        except Exception as e:
                            logger.error(f"获取昵称批次失败 ({i}~{i+batch_size-1}): {e}")
                            # 对失败批次使用wxid作为昵称
                            for wxid in batch:
                                all_nicknames[wxid] = wxid
                else:
                    logger.warning("bot既没有get_contract_detail也没有get_nickname方法，将使用wxid作为昵称")
                    all_nicknames = {username: username for username in contact_usernames}

                # 根据获取的昵称创建联系人对象
                for username in contact_usernames:
                    # 根据wxid格式确定联系人类型
                    contact_type = "friend"
                    if username.endswith("@chatroom"):
                        contact_type = "group"
                    elif username.startswith("gh_"):
                        contact_type = "official"

                    # 获取昵称（如果有）
                    nickname = all_nicknames.get(username, username)
                    display_name = nickname if nickname else username

                    # 创建联系人对象
                    contact = {
                        "wxid": username,
                        "name": display_name,
                        "nickname": nickname,
                        "remark": "",
                        "avatar": "/static/img/favicon.ico",
                        "type": contact_type,
                        "online": True,
                        "starred": False
                    }
                    contact_list.append(contact)

            # 将联系人列表保存到数据库
            try:
                # 开始保存前记录时间
                start_time = time.time()
                save_contacts_to_db(contact_list)
                end_time = time.time()
                logger.info(f"联系人列表已保存到数据库，共{len(contact_list)}个联系人，耗时{end_time-start_time:.2f}秒")
            except Exception as e:
                logger.error(f"保存联系人列表到数据库失败: {e}")

            # 创建响应数据
            response_data = {
                "success": True,
                "data": contact_list,
                "timestamp": int(time.time()),
                "pagination": {
                    "total": len(contact_list),
                    "page": 1,
                    "total_pages": 1
                }
            }

            # 将结果缓存到文件
            try:
                # 缓存文件路径
                cache_dir = os.path.join("data", "cache")
                os.makedirs(cache_dir, exist_ok=True)
                contacts_cache_file = os.path.join(cache_dir, "contacts_cache.json")

                with open(contacts_cache_file, 'w', encoding='utf-8') as f:
                    json.dump(response_data, f, ensure_ascii=False, indent=2)
                logger.info(f"联系人列表已缓存至 {contacts_cache_file}")
            except Exception as e:
                logger.error(f"缓存联系人列表失败: {e}")

            # 返回联系人列表
            logger.success(f"成功获取到{len(contact_list)}个联系人")
            return JSONResponse(content=response_data)

        except Exception as e:
            logger.error(f"获取联系人列表失败: {e}")
            logger.error(traceback.format_exc())

            # 返回空列表和错误信息
            return JSONResponse(content={
                "success": False,
                "error": f"获取联系人列表失败: {str(e)}",
                "data": []
            })

    # 添加一个请求计数器，但不限制请求频率
    request_counters = {}
    request_timestamps = {}
    REQUEST_RATE_LIMIT = 999999  # 设置一个极高的值，实际上不限制请求

    @app.post('/api/contacts/details', response_class=JSONResponse)
    async def api_contacts_details(request: Request, username: str = Depends(require_auth)):
        logger.info("收到联系人详情批量请求")
        # 获取客户端IP
        client_ip = request.client.host
        current_time = time.time()

        # 检查请求频率
        if client_ip in request_counters:
            # 清除10秒前的请求记录
            request_timestamps[client_ip] = [t for t in request_timestamps[client_ip] if current_time - t < 10]

            # 检查10秒内的请求次数
            if len(request_timestamps[client_ip]) >= REQUEST_RATE_LIMIT:
                logger.warning(f"客户端 {client_ip} 请求频率过高，已限制")
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '请求过于频繁，请稍后再试',
                        'rate_limited': True
                    }
                )
        else:
            request_counters[client_ip] = 0
            request_timestamps[client_ip] = []

        # 更新请求计数和时间戳
        request_counters[client_ip] += 1
        request_timestamps[client_ip].append(current_time)

        logger.info(f"客户端 {client_ip} 联系人详情请求计数: {request_counters[client_ip]}")

        try:
            # 获取请求的联系人ID列表
            data = await request.json()
            if not data or 'wxids' not in data:
                logger.warning("请求缺少wxids参数")
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '缺少wxids参数'
                    }
                )

            wxids = data['wxids']
            # 验证wxids是否为列表且不为空
            if not isinstance(wxids, list) or len(wxids) == 0:
                logger.warning(f"wxids参数格式错误: {wxids}")
                return JSONResponse(
                    content={
                        'success': False,
                        'error': 'wxids必须是非空列表'
                    }
                )

            # 限制每次请求最多20个
            if len(wxids) > 20:
                logger.warning(f"请求的wxids数量超过限制: {len(wxids)}")
                wxids = wxids[:20]

            logger.info(f"正在获取 {len(wxids)} 个联系人的详情")

            # 尝试首先从数据库中获取联系人信息
            cached_contacts = {}
            try:
                # 从数据库加载所有联系人
                contacts_from_db = get_contacts_from_db()
                for contact in contacts_from_db:
                    if 'wxid' in contact:
                        cached_contacts[contact['wxid']] = contact
                logger.info(f"从数据库加载了 {len(cached_contacts)} 个联系人")
            except Exception as e:
                logger.error(f"从数据库获取联系人失败: {str(e)}")

            # 获取机器人实例（统一从 app_setup 注入，不再依赖历史会话反序列化）
            wxapi = _resolve_wechat_client()
            if not wxapi:
                logger.error("无法获取机器人实例")
                # 如果有缓存数据，尝试从缓存返回
                if cached_contacts:
                    results = []
                    missing_wxids = []
                    for wxid in wxids:
                        if wxid in cached_contacts:
                            contact = cached_contacts[wxid]
                            results.append({
                                'wxid': wxid,
                                'nickname': contact.get('nickname', wxid),
                                'avatar': contact.get('avatar', ''),
                                'remark': contact.get('remark', ''),
                                'alias': contact.get('alias', ''),
                                'from_cache': True
                            })
                        else:
                            missing_wxids.append(wxid)
                            results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情未找到', 'from_cache': True})

                    if missing_wxids:
                        logger.warning(f"以下 {len(missing_wxids)} 个wxid在缓存中未找到: {missing_wxids}")

                    logger.info(f"从缓存返回 {len(results)} 个联系人详情")
                    return JSONResponse(
                        content={
                            'success': True,
                            'data': results,
                            'from_cache': True
                        }
                    )
                else:
                    return JSONResponse(
                        content={
                            'success': False,
                            'error': '无法获取微信机器人实例，且没有缓存数据'
                        }
                    )

            # 获取联系人详情
            results = []
            success_count = 0
            cached_count = 0
            api_count = 0

            for wxid in wxids:
                logger.debug(f"正在获取联系人详情: {wxid}")
                # 首先检查缓存
                if wxid in cached_contacts:
                    cached_contact = cached_contacts[wxid]
                    contact_info = {
                        'wxid': wxid,
                        'nickname': cached_contact.get('nickname', wxid),
                        'avatar': cached_contact.get('avatar', ''),
                        'remark': cached_contact.get('remark', ''),
                        'alias': cached_contact.get('alias', ''),
                        'from_cache': True
                    }
                    results.append(contact_info)
                    success_count += 1
                    cached_count += 1
                    continue

                # 缓存中没有，尝试从API获取
                try:
                    # 调用API获取联系人详情
                    detail = await wxapi.get_contract_detail(wxid)

                    # 处理返回结果
                    if detail:
                        # 检查detail是否为列表
                        if isinstance(detail, list) and len(detail) > 0:
                            # 如果是列表，获取第一个元素
                            detail_item = detail[0]
                            if isinstance(detail_item, dict):
                                # 提取信息并添加到结果中
                                # 处理API返回的字段名称不一致的问题
                                nickname = ""
                                if 'NickName' in detail_item:
                                    if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                                        nickname = detail_item['NickName']['string']
                                    else:
                                        nickname = str(detail_item['NickName'])
                                elif 'nickname' in detail_item:
                                    nickname = detail_item.get('nickname')

                                # 记录详细的字段信息以便调试
                                logger.debug(f"联系人 {wxid} 详情字段: {detail_item.keys()}")
                                if 'NickName' in detail_item:
                                    logger.debug(f"联系人 {wxid} NickName类型: {type(detail_item['NickName'])}, 值: {detail_item['NickName']}")

                                # 处理备注
                                remark = ""
                                if 'Remark' in detail_item:
                                    if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                                        remark = detail_item['Remark']['string']
                                    elif isinstance(detail_item['Remark'], str):
                                        remark = detail_item['Remark']
                                elif 'remark' in detail_item:
                                    remark = detail_item.get('remark')

                                # 处理头像
                                avatar = ""
                                if 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                                    avatar = detail_item['SmallHeadImgUrl']
                                    logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                                elif 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                                    avatar = detail_item['BigHeadImgUrl']
                                    logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                                elif 'avatar' in detail_item:
                                    avatar = detail_item.get('avatar')

                                # 处理微信号
                                alias = ""
                                if 'Alias' in detail_item:
                                    alias = detail_item.get('Alias')
                                elif 'alias' in detail_item:
                                    alias = detail_item.get('alias')

                                contact_info = {
                                    'wxid': wxid,
                                    'nickname': nickname or wxid,
                                    'avatar': avatar or '',
                                    'remark': remark or '',
                                    'alias': alias or ''
                                }

                                # 将联系人信息保存到数据库
                                try:
                                    update_contact_in_db(contact_info)
                                    logger.debug(f"已将联系人 {wxid} 信息保存到数据库")
                                except Exception as e:
                                    logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")

                                results.append(contact_info)
                                success_count += 1
                                api_count += 1
                            else:
                                logger.warning(f"联系人 {wxid} 详情项不是字典: {detail_item}")
                                # 尝试从缓存获取备用数据
                                if wxid in cached_contacts:
                                    cached_contact = cached_contacts[wxid]
                                    contact_info = {
                                        'wxid': wxid,
                                        'nickname': cached_contact.get('nickname', wxid),
                                        'avatar': cached_contact.get('avatar', ''),
                                        'remark': cached_contact.get('remark', ''),
                                        'alias': cached_contact.get('alias', ''),
                                        'from_cache': True
                                    }
                                    results.append(contact_info)
                                    success_count += 1
                                    cached_count += 1
                                else:
                                    results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情格式错误'})
                        elif isinstance(detail, dict):
                            # 如果是字典，直接使用
                            # 处理API返回的字段名称不一致的问题

                            # 记录详细的字段信息以便调试
                            logger.debug(f"联系人 {wxid} 详情字段: {detail.keys()}")

                            nickname = ""
                            if 'NickName' in detail:
                                if isinstance(detail['NickName'], dict) and 'string' in detail['NickName']:
                                    nickname = detail['NickName']['string']
                                    logger.debug(f"联系人 {wxid} NickName类型: {type(detail['NickName'])}, 值: {detail['NickName']}")
                                else:
                                    nickname = str(detail['NickName'])
                            elif 'nickname' in detail:
                                nickname = detail.get('nickname')

                            # 处理备注
                            remark = ""
                            if 'Remark' in detail:
                                if isinstance(detail['Remark'], dict) and 'string' in detail['Remark']:
                                    remark = detail['Remark']['string']
                                elif isinstance(detail['Remark'], str):
                                    remark = detail['Remark']
                            elif 'remark' in detail:
                                remark = detail.get('remark')

                            # 处理头像
                            avatar = ""
                            if 'SmallHeadImgUrl' in detail and detail['SmallHeadImgUrl']:
                                avatar = detail['SmallHeadImgUrl']
                                logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                            elif 'BigHeadImgUrl' in detail and detail['BigHeadImgUrl']:
                                avatar = detail['BigHeadImgUrl']
                                logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                            elif 'avatar' in detail:
                                avatar = detail.get('avatar')

                            # 处理微信号
                            alias = ""
                            if 'Alias' in detail:
                                alias = detail.get('Alias')
                            elif 'alias' in detail:
                                alias = detail.get('alias')

                            contact_info = {
                                'wxid': wxid,
                                'nickname': nickname or wxid,
                                'avatar': avatar or '',
                                'remark': remark or '',
                                'alias': alias or ''
                            }

                            # 将联系人信息保存到数据库
                            try:
                                update_contact_in_db(contact_info)
                                logger.debug(f"已将联系人 {wxid} 信息保存到数据库")
                            except Exception as e:
                                logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")

                            results.append(contact_info)
                            success_count += 1
                            api_count += 1
                        else:
                            logger.warning(f"联系人 {wxid} 详情格式不支持: {type(detail)}")
                            # 尝试从缓存获取备用数据
                            if wxid in cached_contacts:
                                cached_contact = cached_contacts[wxid]
                                contact_info = {
                                    'wxid': wxid,
                                    'nickname': cached_contact.get('nickname', wxid),
                                    'avatar': cached_contact.get('avatar', ''),
                                    'remark': cached_contact.get('remark', ''),
                                    'alias': cached_contact.get('alias', ''),
                                    'from_cache': True
                                }
                                results.append(contact_info)
                                success_count += 1
                                cached_count += 1
                            else:
                                results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情格式不支持'})
                    else:
                        logger.warning(f"联系人 {wxid} 详情为空")
                        # 尝试从缓存获取备用数据
                        if wxid in cached_contacts:
                            cached_contact = cached_contacts[wxid]
                            contact_info = {
                                'wxid': wxid,
                                'nickname': cached_contact.get('nickname', wxid),
                                'avatar': cached_contact.get('avatar', ''),
                                'remark': cached_contact.get('remark', ''),
                                'alias': cached_contact.get('alias', ''),
                                'from_cache': True
                            }
                            results.append(contact_info)
                            success_count += 1
                            cached_count += 1
                        else:
                            results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情为空'})
                except Exception as e:
                    logger.error(f"获取联系人 {wxid} 详情时出错: {str(e)}")
                    # 尝试从缓存获取备用数据
                    if wxid in cached_contacts:
                        cached_contact = cached_contacts[wxid]
                        contact_info = {
                            'wxid': wxid,
                            'nickname': cached_contact.get('nickname', wxid),
                            'avatar': cached_contact.get('avatar', ''),
                            'remark': cached_contact.get('remark', ''),
                            'alias': cached_contact.get('alias', ''),
                            'from_cache': True
                        }
                        results.append(contact_info)
                        success_count += 1
                        cached_count += 1
                    else:
                        results.append({'wxid': wxid, 'nickname': wxid, 'error': str(e)})

            logger.info(f"成功获取 {success_count}/{len(wxids)} 个联系人详情 (缓存: {cached_count}, API: {api_count})")
            return JSONResponse(
                content={
                    'success': True,
                    'data': results
                }
            )

        except Exception as e:
            logger.error(f"处理联系人详情批量请求时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JSONResponse(
                content={
                    'success': False,
                    'error': f'服务器错误: {str(e)}'
                }
            )

    # 在setup_routes()函数内部添加群聊API
    @app.post("/api/group/members", response_class=JSONResponse)
    async def api_group_members(request: Request, username: str = Depends(require_auth)):
        """获取群聊成员列表"""
        try:
            # 解析请求数据
            data = await request.json()
            wxid = data.get("wxid")

            if not wxid:
                return JSONResponse(
                    content={"success": False, "error": "缺少群聊ID(wxid)参数"}
                )

            # 只有群才能获取成员
            if not wxid.endswith("@chatroom"):
                return JSONResponse(
                    content={"success": False, "error": "无效的群ID，只有群聊才能获取成员"}
                )

            # 确保机器人实例可用
            bot_instance = get_bot_instance()
            if not bot_instance:
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(
                    content={"success": False, "error": "机器人实例未初始化，请确保机器人已启动"}
                )

            # 调用API获取群成员
            try:
                logger.info(f"正在获取群 {wxid} 的成员列表")
                import asyncio

                # 调用API获取群成员
                if asyncio.get_event_loop().is_running():
                    members = await bot_instance.get_chatroom_member_list(wxid)
                else:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        members = loop.run_until_complete(bot_instance.get_chatroom_member_list(wxid))
                    finally:
                        loop.close()

                logger.info(f"成功获取群 {wxid} 的成员列表，共 {len(members)} 个成员")

                # 处理成员数据，确保字段名称一致性
                processed_members = []
                for member in members:
                    processed_member = {}

                    # 处理wxid字段
                    if 'UserName' in member:
                        processed_member['wxid'] = member['UserName']
                    elif 'Wxid' in member:
                        processed_member['wxid'] = member['Wxid']
                    elif 'wxid' in member:
                        processed_member['wxid'] = member['wxid']
                    else:
                        processed_member['wxid'] = ''

                    # 处理昵称字段
                    if 'DisplayName' in member and member['DisplayName']:
                        processed_member['display_name'] = member['DisplayName']
                    elif 'display_name' in member:
                        processed_member['display_name'] = member['display_name']
                    else:
                        processed_member['display_name'] = ''

                    if 'NickName' in member and member['NickName']:
                        processed_member['nickname'] = member['NickName']
                    elif 'nickname' in member:
                        processed_member['nickname'] = member['nickname']
                    else:
                        processed_member['nickname'] = processed_member['wxid']

                    # 处理头像字段
                    if 'BigHeadImgUrl' in member and member['BigHeadImgUrl']:
                        processed_member['avatar'] = member['BigHeadImgUrl']
                    elif 'SmallHeadImgUrl' in member and member['SmallHeadImgUrl']:
                        processed_member['avatar'] = member['SmallHeadImgUrl']
                    elif 'HeadImgUrl' in member and member['HeadImgUrl']:
                        processed_member['avatar'] = member['HeadImgUrl']
                    elif 'avatar' in member:
                        processed_member['avatar'] = member['avatar']
                    else:
                        processed_member['avatar'] = '/static/img/default-avatar.png'

                    # 处理邀请人字段
                    if 'InviterUserName' in member and member['InviterUserName']:
                        processed_member['inviter_wxid'] = member['InviterUserName']
                    elif 'inviter_wxid' in member:
                        processed_member['inviter_wxid'] = member['inviter_wxid']
                    else:
                        processed_member['inviter_wxid'] = ''

                    # 保留原始数据以便调试
                    processed_member['raw_data'] = member

                    processed_members.append(processed_member)

                # 尝试将群成员保存到数据库
                try:
                    from database.group_members_db import save_group_members_to_db
                    save_result = save_group_members_to_db(wxid, members)
                    if save_result:
                        logger.info(f"成功将群 {wxid} 的 {len(members)} 个成员保存到数据库")
                    else:
                        logger.warning(f"将群 {wxid} 的成员保存到数据库失败")
                except Exception as e:
                    logger.error(f"将群成员保存到数据库时出错: {str(e)}")

                # 返回处理后的成员列表
                return JSONResponse(
                    content={
                        "success": True,
                        "data": processed_members
                    }
                )
            except Exception as e:
                logger.error(f"获取群成员列表时出错: {e}")
                return JSONResponse(
                    content={"success": False, "error": f"获取群成员列表失败: {str(e)}"}
                )

        except Exception as e:
            logger.error(f"处理获取群成员请求时出错: {str(e)}")
            return JSONResponse(
                content={"success": False, "error": f"服务器错误: {str(e)}"}
            )

    @app.post("/api/group/member/detail", response_class=JSONResponse)
    async def api_group_member_detail(request: Request, username: str = Depends(require_auth)):
        """获取群成员详细信息"""
        try:
            # 获取请求数据
            data = await request.json()
            group_wxid = data.get("group_wxid")
            member_wxid = data.get("member_wxid")

            if not group_wxid or not member_wxid:
                return JSONResponse(
                    content={"success": False, "error": "缺少必要参数"}
                )

            logger.info(f"获取群 {group_wxid} 成员 {member_wxid} 的详细信息")

            # 先从数据库中获取成员信息
            from database.group_members_db import get_group_member_from_db
            member_info = get_group_member_from_db(group_wxid, member_wxid)

            if member_info:
                logger.info(f"从数据库中获取到群成员信息: {member_info}")
                return JSONResponse(
                    content={
                        "success": True,
                        "data": member_info,
                        "source": "database"
                    }
                )

            # 如果数据库中没有，尝试从微信API获取
            logger.info(f"数据库中没有群成员信息，尝试从微信API获取")

            # 获取机器人实例
            bot_instance = get_bot_instance()
            if not bot_instance:
                return JSONResponse(
                    content={"success": False, "error": "机器人实例不存在"}
                )

            # 先获取群成员列表
            import asyncio
            if asyncio.get_event_loop().is_running():
                members = await bot_instance.get_chatroom_member_list(group_wxid)
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    members = loop.run_until_complete(bot_instance.get_chatroom_member_list(group_wxid))
                finally:
                    loop.close()

            # 在群成员列表中查找指定成员
            member_info = None
            for member in members:
                member_id = member.get("wxid") or member.get("Wxid") or member.get("UserName") or ""
                if member_id == member_wxid:
                    member_info = member
                    break

            if not member_info:
                return JSONResponse(
                    content={"success": False, "error": f"在群 {group_wxid} 中未找到成员 {member_wxid}"}
                )

            # 处理成员信息
            processed_member = {}

            # 处理wxid字段
            processed_member['wxid'] = member_wxid

            # 处理昵称字段
            if 'DisplayName' in member_info and member_info['DisplayName']:
                processed_member['display_name'] = member_info['DisplayName']
            elif 'display_name' in member_info:
                processed_member['display_name'] = member_info['display_name']
            else:
                processed_member['display_name'] = ''

            if 'NickName' in member_info and member_info['NickName']:
                processed_member['nickname'] = member_info['NickName']
            elif 'nickname' in member_info:
                processed_member['nickname'] = member_info['nickname']
            else:
                processed_member['nickname'] = member_wxid

            # 处理头像字段
            if 'BigHeadImgUrl' in member_info and member_info['BigHeadImgUrl']:
                processed_member['avatar'] = member_info['BigHeadImgUrl']
            elif 'SmallHeadImgUrl' in member_info and member_info['SmallHeadImgUrl']:
                processed_member['avatar'] = member_info['SmallHeadImgUrl']
            elif 'HeadImgUrl' in member_info and member_info['HeadImgUrl']:
                processed_member['avatar'] = member_info['HeadImgUrl']
            elif 'avatar' in member_info:
                processed_member['avatar'] = member_info['avatar']
            else:
                processed_member['avatar'] = '/static/img/default-avatar.png'

            # 处理邀请人字段
            if 'InviterUserName' in member_info and member_info['InviterUserName']:
                processed_member['inviter_wxid'] = member_info['InviterUserName']
            elif 'inviter_wxid' in member_info:
                processed_member['inviter_wxid'] = member_info['inviter_wxid']
            else:
                processed_member['inviter_wxid'] = ''

            # 保存到数据库
            from database.group_members_db import update_group_member_in_db
            update_result = update_group_member_in_db(group_wxid, processed_member)
            if update_result:
                logger.info(f"成功将群成员信息保存到数据库")
            else:
                logger.warning(f"将群成员信息保存到数据库失败")

            # 返回处理后的成员信息
            return JSONResponse(
                content={
                    "success": True,
                    "data": processed_member,
                    "source": "api"
                }
            )

        except Exception as e:
            logger.error(f"获取群成员详细信息时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JSONResponse(
                content={"success": False, "error": f"获取群成员详细信息失败: {str(e)}"}
            )
