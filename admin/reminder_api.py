import os
import json
import logging
import sqlite3
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import time
from itsdangerous import URLSafeSerializer

logger = logging.getLogger("admin")

# 获取server.py中的配置
try:
    from server import config
except ImportError:
    # 如果无法导入，使用默认值
    config = {"secret_key": "xybotv2_admin_secret_key"}

def get_reminder_file_path(wxid: str) -> str:
    """获取提醒文件路径"""
    # 获取当前文件所在目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 获取项目根目录 (比当前目录高一级)
    root_dir = os.path.dirname(current_dir)
    # 使用项目根目录下的reminder_data目录
    reminders_dir = os.path.join(root_dir, "reminder_data")
    
    if not os.path.exists(reminders_dir):
        logger.warning(f"提醒数据目录不存在，将创建: {reminders_dir}")
        os.makedirs(reminders_dir, exist_ok=True)
    
    # 使用与系统其他部分兼容的文件命名格式
    return os.path.join(reminders_dir, f"user_{wxid}.db")

def init_reminder_db(db_path):
    """初始化提醒数据库"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 创建提醒表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY,
            wxid TEXT,
            content TEXT,
            reminder_type TEXT,
            reminder_time TEXT,
            chat_id TEXT,
            is_done INTEGER DEFAULT 0
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"成功初始化提醒数据库: {db_path}")
        return True
    except Exception as e:
        logger.error(f"初始化提醒数据库失败: {str(e)}")
        return False

def load_reminders_from_db(db_path, wxid):
    """从数据库加载提醒数据"""
    try:
        if not os.path.exists(db_path):
            logger.warning(f"提醒数据库不存在: {db_path}")
            return []
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT id, content, reminder_type, reminder_time, chat_id, is_done 
        FROM reminders 
        WHERE wxid = ? AND is_done = 0
        """, (wxid,))
        
        results = cursor.fetchall()
        reminders = []
        
        for row in results:
            reminders.append({
                "id": row["id"],
                "wxid": wxid,
                "content": row["content"],
                "reminder_type": row["reminder_type"],
                "reminder_time": row["reminder_time"],
                "chat_id": row["chat_id"],
                "is_done": row["is_done"]
            })
        
        conn.close()
        logger.info(f"成功从数据库加载 {len(reminders)} 条提醒")
        return reminders
    except Exception as e:
        logger.error(f"从数据库加载提醒失败: {str(e)}")
        return []

def save_reminder_to_db(db_path, reminder):
    """保存提醒到数据库"""
    try:
        # 确保数据库初始化
        if not os.path.exists(db_path):
            if not init_reminder_db(db_path):
                return False
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO reminders (id, wxid, content, reminder_type, reminder_time, chat_id, is_done)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            reminder["id"],
            reminder["wxid"],
            reminder["content"],
            reminder["reminder_type"],
            reminder["reminder_time"],
            reminder["chat_id"],
            reminder["is_done"]
        ))
        
        conn.commit()
        conn.close()
        logger.info(f"成功保存提醒到数据库: ID={reminder['id']}")
        return True
    except Exception as e:
        logger.error(f"保存提醒到数据库失败: {str(e)}")
        return False

def update_reminder_in_db(db_path, reminder):
    """更新数据库中的提醒"""
    try:
        if not os.path.exists(db_path):
            logger.warning(f"提醒数据库不存在: {db_path}")
            return False
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        UPDATE reminders 
        SET content = ?, reminder_type = ?, reminder_time = ?, chat_id = ?, is_done = ?
        WHERE id = ? AND wxid = ?
        """, (
            reminder["content"],
            reminder["reminder_type"],
            reminder["reminder_time"],
            reminder["chat_id"],
            reminder["is_done"],
            reminder["id"],
            reminder["wxid"]
        ))
        
        if cursor.rowcount == 0:
            conn.close()
            logger.warning(f"未找到ID为 {reminder['id']} 的提醒，无法更新")
            return False
        
        conn.commit()
        conn.close()
        logger.info(f"成功更新数据库中的提醒: ID={reminder['id']}")
        return True
    except Exception as e:
        logger.error(f"更新数据库中的提醒失败: {str(e)}")
        return False

def delete_reminder_from_db(db_path, wxid, reminder_id):
    """从数据库删除提醒"""
    try:
        if not os.path.exists(db_path):
            logger.warning(f"提醒数据库不存在: {db_path}")
            return False
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        DELETE FROM reminders 
        WHERE id = ? AND wxid = ?
        """, (reminder_id, wxid))
        
        if cursor.rowcount == 0:
            conn.close()
            logger.warning(f"未找到ID为 {reminder_id} 的提醒，无法删除")
            return False
        
        conn.commit()
        conn.close()
        logger.info(f"成功从数据库删除提醒: ID={reminder_id}")
        return True
    except Exception as e:
        logger.error(f"从数据库删除提醒失败: {str(e)}")
        return False

def get_next_reminder_id(db_path, wxid):
    """获取下一个可用的提醒ID"""
    try:
        if not os.path.exists(db_path):
            return 1
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT MAX(id) FROM reminders WHERE wxid = ?
        """, (wxid,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result[0] is None:
            return 1
        else:
            return result[0] + 1
    except Exception as e:
        logger.error(f"获取下一个提醒ID失败: {str(e)}")
        return 1

def remove_existing_reminder_routes(app: FastAPI):
    """移除已存在的提醒API路由，防止冲突"""
    routes_to_remove = []
    
    # 找到所有提醒相关的路由
    for route in app.routes:
        if hasattr(route, 'path') and 'api/reminders' in route.path:
            routes_to_remove.append(route)
    
    # 从app中移除这些路由
    for route in routes_to_remove:
        app.routes.remove(route)
        logger.info(f"移除了已存在的路由: {route.path} [{route.methods}]")
    
    return len(routes_to_remove)

# 内部认证检查函数，与server.py中的check_auth逻辑一致
async def _check_auth(request: Request):
    """检查用户是否已认证"""
    try:
        # 从Cookie中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话Cookie")
            return None
        
        # 调试日志
        logger.debug(f"获取到会话Cookie: {session_cookie[:15]}...")
        
        # 解码会话数据
        try:
            serializer = URLSafeSerializer(config["secret_key"], "session")
            session_data = serializer.loads(session_cookie)
            
            # 检查会话是否已过期
            expires = session_data.get("expires", 0)
            if expires < time.time():
                logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
                return None
            
            # 会话有效
            logger.debug(f"会话有效，用户: {session_data.get('username')}")
            return session_data.get("username")
        except Exception as e:
            logger.error(f"解析会话数据失败: {str(e)}")
            return None
    except Exception as e:
        logger.error(f"检查认证失败: {str(e)}")
        return None

def register_reminder_routes(app: FastAPI, check_auth=None):
    """注册提醒相关路由"""
    
    # 首先移除已存在的路由，防止冲突
    removed_count = remove_existing_reminder_routes(app)
    logger.info(f"移除了 {removed_count} 个已存在的提醒API路由")
    
    # 如果未提供check_auth函数，使用我们定义的_check_auth
    if check_auth is None:
        check_auth = _check_auth
        logger.info("使用内部认证检查函数")
    else:
        logger.info("使用外部提供的认证检查函数")
        
    @app.get("/api/reminders", response_class=JSONResponse)
    async def api_get_all_reminders(request: Request):
        """获取所有用户的所有提醒"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("获取所有提醒失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
        
        try:
            logger.info(f"用户 {username} 获取所有提醒")
            
            # 获取reminder_data目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            root_dir = os.path.dirname(current_dir)
            reminders_dir = os.path.join(root_dir, "reminder_data")
            
            if not os.path.exists(reminders_dir):
                logger.warning(f"提醒数据目录不存在: {reminders_dir}")
                return JSONResponse(content={"success": True, "reminders": []})
            
            # 获取目录中的所有db文件
            all_reminders = []
            db_files = [f for f in os.listdir(reminders_dir) if f.startswith("user_") and f.endswith(".db")]
            
            logger.info(f"找到 {len(db_files)} 个提醒数据库文件")
            
            for db_file in db_files:
                # 从文件名中提取wxid
                wxid = db_file[5:-3]  # 移除"user_"前缀和".db"后缀
                
                # 获取完整文件路径
                db_path = os.path.join(reminders_dir, db_file)
                
                # 加载该用户的提醒
                user_reminders = load_reminders_from_db(db_path, wxid)
                
                # 添加到总列表
                all_reminders.extend(user_reminders)
            
            logger.info(f"成功加载所有提醒，总数: {len(all_reminders)}")
            return JSONResponse(content={"success": True, "reminders": all_reminders})
            
        except Exception as e:
            logger.exception(f"获取所有提醒失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"获取所有提醒失败: {str(e)}"})
    
    @app.get("/api/reminders/{wxid}", response_class=JSONResponse)
    async def api_get_reminders(wxid: str, request: Request):
        """获取用户的所有提醒"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("获取提醒列表失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
        
        try:
            logger.info(f"用户 {username} 获取 {wxid} 的提醒列表")
            
            # 判断是否是群聊
            is_chatroom = "@chatroom" in wxid
            
            if is_chatroom:
                logger.info(f"查询群聊 {wxid} 的提醒列表")
                
                # 获取reminder_data目录
                current_dir = os.path.dirname(os.path.abspath(__file__))
                root_dir = os.path.dirname(current_dir)
                reminders_dir = os.path.join(root_dir, "reminder_data")
                
                if not os.path.exists(reminders_dir):
                    logger.warning(f"提醒数据目录不存在: {reminders_dir}")
                    return JSONResponse(content={"success": True, "reminders": []})
                
                # 所有与该群聊相关的提醒
                all_group_reminders = []
                
                # 获取目录中的所有db文件
                db_files = [f for f in os.listdir(reminders_dir) if f.startswith("user_") and f.endswith(".db")]
                logger.info(f"找到 {len(db_files)} 个提醒数据库文件")
                
                for db_file in db_files:
                    # 从文件名中提取wxid
                    owner_wxid = db_file[5:-3]  # 移除"user_"前缀和".db"后缀
                    
                    # 获取完整文件路径
                    db_path = os.path.join(reminders_dir, db_file)
                    
                    try:
                        # 检查数据库是否存在并初始化
                        if not os.path.exists(db_path):
                            continue
                            
                        # 尝试连接数据库
                        conn = sqlite3.connect(db_path)
                        conn.row_factory = sqlite3.Row
                        cursor = conn.cursor()
                        
                        # 查询该群聊的提醒 (chat_id等于群聊ID的记录)
                        cursor.execute("""
                        SELECT * FROM reminders 
                        WHERE chat_id = ? AND is_done = 0
                        """, (wxid,))
                        
                        results = cursor.fetchall()
                        conn.close()
                        
                        # 添加到总列表
                        for row in results:
                            reminder = {
                                "id": row["id"],
                                "wxid": row["wxid"],
                                "content": row["content"],
                                "reminder_type": row["reminder_type"],
                                "reminder_time": row["reminder_time"],
                                "chat_id": row["chat_id"],
                                "is_done": row["is_done"],
                                "owner_id": owner_wxid  # 额外记录真正设置提醒的用户ID
                            }
                            all_group_reminders.append(reminder)
                            
                    except Exception as e:
                        logger.error(f"查询数据库 {db_file} 时出错: {str(e)}")
                        continue
                
                logger.info(f"为群聊 {wxid} 找到 {len(all_group_reminders)} 条提醒")
                return JSONResponse(content={"success": True, "reminders": all_group_reminders})
            else:
                # 普通用户提醒处理
                reminders_file = get_reminder_file_path(wxid)
                logger.info(f"尝试从 {reminders_file} 加载提醒数据")
                
                # 从数据库加载提醒
                reminders = load_reminders_from_db(reminders_file, wxid)
                
                # 添加所有者ID
                for reminder in reminders:
                    reminder["owner_id"] = wxid
                
                logger.info(f"从数据库成功加载提醒，条目数: {len(reminders)}")
                return JSONResponse(content={"success": True, "reminders": reminders})
            
        except Exception as e:
            logger.exception(f"获取用户 {wxid} 的提醒列表失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"获取提醒列表失败: {str(e)}"})

    @app.get("/api/reminders/{wxid}/{id}", response_class=JSONResponse)
    async def api_get_reminder(wxid: str, id: int, request: Request):
        """获取提醒详情"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("获取提醒详情失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
        
        try:
            logger.info(f"用户 {username} 获取 {wxid} 的提醒 {id} 详情")
            
            # 获取提醒文件路径
            reminders_file = get_reminder_file_path(wxid)
            
            # 从数据库加载所有提醒
            reminders = load_reminders_from_db(reminders_file, wxid)
            
            # 查找指定ID的提醒
            for reminder in reminders:
                if reminder.get("id") == id:
                    return JSONResponse(content={"success": True, "reminder": reminder})
            
            # 未找到指定提醒
            logger.warning(f"未找到ID为 {id} 的提醒")
            return JSONResponse(content={"success": False, "error": "未找到指定提醒"})
                
        except Exception as e:
            logger.exception(f"获取用户 {wxid} 的提醒 {id} 详情失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"获取提醒详情失败: {str(e)}"})

    @app.post("/api/reminders/{wxid}", response_class=JSONResponse)
    async def api_add_reminder(wxid: str, request: Request):
        """添加新提醒"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("添加提醒失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
        
        try:
            data = await request.json()
            content = data.get("content")
            reminder_type = data.get("reminder_type")
            reminder_time = data.get("reminder_time")
            chat_id = data.get("chat_id")
            
            logger.info(f"用户 {username} 为 {wxid} 添加提醒: {content}, 类型: {reminder_type}, 时间: {reminder_time}, 聊天ID: {chat_id}")
            
            if not all([content, reminder_type, reminder_time, chat_id]):
                logger.warning(f"添加提醒缺少必要参数: content={content}, type={reminder_type}, time={reminder_time}, chat_id={chat_id}")
                return JSONResponse(content={"success": False, "error": "缺少必要参数"})
            
            # 获取提醒文件路径
            reminders_file = get_reminder_file_path(wxid)
            
            # 获取下一个可用的提醒ID
            new_id = get_next_reminder_id(reminders_file, wxid)
            
            # 创建新提醒
            new_reminder = {
                "id": new_id,
                "wxid": wxid,
                "content": content,
                "reminder_type": reminder_type,
                "reminder_time": reminder_time,
                "chat_id": chat_id,
                "is_done": 0
            }
            
            # 保存到数据库
            if save_reminder_to_db(reminders_file, new_reminder):
                logger.info(f"成功为用户 {wxid} 添加提醒，ID: {new_id}")
                return JSONResponse(content={"success": True, "id": new_id})
            else:
                logger.error(f"保存提醒到数据库失败")
                return JSONResponse(content={"success": False, "error": "保存提醒失败"})
        
        except Exception as e:
            logger.exception(f"添加提醒失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"添加提醒失败: {str(e)}"})

    @app.put("/api/reminders/{wxid}/{id}", response_class=JSONResponse)
    async def api_update_reminder(wxid: str, id: int, request: Request):
        """更新提醒"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("更新提醒失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
        
        try:
            data = await request.json()
            content = data.get("content")
            reminder_type = data.get("reminder_type")
            reminder_time = data.get("reminder_time")
            chat_id = data.get("chat_id")
            owner_id = data.get("owner_id")  # 获取提醒的真正所有者ID
            
            logger.info(f"用户 {username} 请求更新提醒 ID={id}, wxid={wxid}, owner_id={owner_id}")
            
            if not all([content, reminder_type, reminder_time, chat_id]):
                logger.warning(f"更新提醒缺少必要参数")
                return JSONResponse(content={"success": False, "error": "缺少必要参数"})
            
            # 判断是否是群聊提醒
            is_chatroom = "@chatroom" in chat_id
            
            # 对于群聊提醒，尝试找到真正的所有者
            if is_chatroom and not owner_id:
                # 查找提醒以获取真正的所有者
                result = find_reminder_in_all_dbs(id, chat_id)
                if result:
                    owner_id, _ = result
                    logger.info(f"找到提醒的真正所有者: {owner_id}")
                else:
                    logger.warning(f"未找到ID为 {id} 的群聊提醒，无法确定所有者")
                    return JSONResponse(content={"success": False, "error": "未找到指定提醒"})
            
            # 使用所有者ID或默认为请求中的wxid
            target_wxid = owner_id if owner_id else wxid
            
            # 获取正确的数据库文件
            reminders_file = get_reminder_file_path(target_wxid)
            
            # 创建更新后的提醒对象
            updated_reminder = {
                "id": id,
                "wxid": target_wxid,
                "content": content,
                "reminder_type": reminder_type,
                "reminder_time": reminder_time,
                "chat_id": chat_id,
                "is_done": 0
            }
            
            # 更新数据库中的提醒
            if update_reminder_in_db(reminders_file, updated_reminder):
                logger.info(f"成功更新提醒 ID={id}")
                return JSONResponse(content={"success": True})
            else:
                logger.warning(f"未找到ID为 {id} 的提醒，无法更新")
                return JSONResponse(content={"success": False, "error": "未找到指定提醒"})
            
        except Exception as e:
            logger.exception(f"更新提醒失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"更新提醒失败: {str(e)}"})

    @app.delete("/api/reminders/{wxid}/{id}", response_class=JSONResponse)
    async def api_delete_reminder(wxid: str, id: int, request: Request):
        """删除提醒"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("删除提醒失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
        
        try:
            logger.info(f"用户 {username} 请求删除提醒 ID={id}, wxid={wxid}")
            
            # 判断是否是群聊ID
            is_chatroom = "@chatroom" in wxid
            
            # 对于群聊的提醒，需要在所有数据库中查找
            if is_chatroom:
                logger.info(f"这是群聊提醒，在所有数据库中查找...")
                result = find_reminder_in_all_dbs(id, wxid)
                
                if result:
                    owner_wxid, reminder = result
                    logger.info(f"找到提醒，所有者是 {owner_wxid}，在数据库中删除")
                    
                    # 获取所有者的数据库文件
                    owner_db_file = get_reminder_file_path(owner_wxid)
                    
                    # 删除提醒
                    if delete_reminder_from_db(owner_db_file, owner_wxid, id):
                        logger.info(f"成功删除群聊 {wxid} 中ID为 {id} 的提醒")
                        return JSONResponse(content={"success": True})
                    else:
                        logger.error(f"无法删除群聊 {wxid} 中ID为 {id} 的提醒")
                        return JSONResponse(content={"success": False, "error": "删除提醒失败"})
                else:
                    logger.warning(f"未在任何数据库中找到群聊 {wxid} 的提醒 ID={id}")
                    return JSONResponse(content={"success": False, "error": "未找到指定提醒"})
            else:
                # 对于个人提醒，直接使用个人数据库
                reminders_file = get_reminder_file_path(wxid)
                logger.info(f"尝试从 {reminders_file} 删除提醒 ID={id}")
                
                # 从数据库删除提醒
                if delete_reminder_from_db(reminders_file, wxid, id):
                    logger.info(f"成功删除用户 {wxid} 的提醒 ID={id}")
                    return JSONResponse(content={"success": True})
                else:
                    logger.warning(f"未找到ID为 {id} 的提醒，无法删除")
                    return JSONResponse(content={"success": False, "error": "未找到指定提醒"})
                
        except Exception as e:
            logger.exception(f"删除提醒失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"删除提醒失败: {str(e)}"})

def find_reminder_in_all_dbs(reminder_id, target_chat_id=None):
    """在所有数据库中查找特定ID的提醒
    
    Args:
        reminder_id: 要查找的提醒ID
        target_chat_id: 可选，目标聊天ID
        
    Returns:
        (wxid, reminder) 元组，如果找到；None 如果未找到
    """
    try:
        # 获取reminder_data目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(current_dir)
        reminders_dir = os.path.join(root_dir, "reminder_data")
        
        if not os.path.exists(reminders_dir):
            logger.warning(f"提醒数据目录不存在: {reminders_dir}")
            return None
        
        # 获取目录中的所有db文件
        db_files = [f for f in os.listdir(reminders_dir) if f.startswith("user_") and f.endswith(".db")]
        
        logger.info(f"在 {len(db_files)} 个提醒数据库文件中查找ID为 {reminder_id} 的提醒")
        
        for db_file in db_files:
            # 从文件名中提取wxid
            wxid = db_file[5:-3]  # 移除"user_"前缀和".db"后缀
            
            # 获取完整文件路径
            db_path = os.path.join(reminders_dir, db_file)
            
            try:
                # 连接到数据库
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # 查询条件
                query = "SELECT * FROM reminders WHERE id = ?"
                params = [reminder_id]
                
                # 如果指定了聊天ID，添加到查询条件
                if target_chat_id:
                    query += " AND chat_id = ?"
                    params.append(target_chat_id)
                
                # 执行查询
                cursor.execute(query, params)
                result = cursor.fetchone()
                conn.close()
                
                if result:
                    logger.info(f"在数据库 {db_file} 中找到ID为 {reminder_id} 的提醒")
                    reminder = {
                        "id": result["id"],
                        "wxid": result["wxid"],
                        "content": result["content"],
                        "reminder_type": result["reminder_type"],
                        "reminder_time": result["reminder_time"],
                        "chat_id": result["chat_id"],
                        "is_done": result["is_done"],
                        "owner_id": wxid  # 记录真正的所有者ID
                    }
                    return (wxid, reminder)
            except Exception as e:
                logger.error(f"查询数据库 {db_file} 时出错: {str(e)}")
                continue
        
        logger.warning(f"在所有数据库中未找到ID为 {reminder_id} 的提醒")
        return None
    except Exception as e:
        logger.error(f"在所有数据库中查找提醒时出错: {str(e)}")
        return None