"""
认证辅助模块：提供认证相关的功能
"""
import logging
import time
from fastapi import Request

# 设置日志
logger = logging.getLogger("auth_helper")

# 全局变量
serializer = None
config = None

def init_auth(config_dict, secret_key_serializer):
    """
    初始化认证辅助模块
    
    Args:
        config_dict: 配置字典
        secret_key_serializer: 用于会话的序列化器
    """
    global serializer, config
    serializer = secret_key_serializer
    config = config_dict
    logger.info("认证辅助模块初始化完成")

async def check_auth(request: Request):
    """
    检查用户是否已认证
    
    Args:
        request: FastAPI请求对象
        
    Returns:
        str: 认证成功返回用户名，否则返回None
    """
    global serializer, config
    
    # 确保已初始化
    if serializer is None or config is None:
        logger.error("认证辅助模块未初始化")
        return None
        
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
            session_data = serializer(config["secret_key"], "session").loads(session_cookie)

            # 输出会话数据，辅助调试
            logger.debug(f"解析会话数据成功: {session_data}")

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