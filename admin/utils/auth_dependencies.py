"""
认证依赖注入模块

提供 FastAPI 依赖注入装饰器，用于简化路由中的认证检查。
遵循 DRY 原则，消除重复的认证代码。
"""
from fastapi import Request, HTTPException, Depends
from typing import Optional


# 全局 check_auth 函数引用
_check_auth_func = None


def init_auth_dependencies(check_auth_func):
    """
    初始化认证依赖模块

    Args:
        check_auth_func: 认证检查函数（来自 auth_helper.check_auth）
    """
    global _check_auth_func
    _check_auth_func = check_auth_func


async def require_auth(request: Request) -> str:
    """
    FastAPI 依赖注入函数：要求用户必须已认证

    用法：
        @app.get("/api/some_endpoint")
        async def some_endpoint(username: str = Depends(require_auth)):
            # username 已经通过认证，可以直接使用
            pass

    Args:
        request: FastAPI 请求对象

    Returns:
        str: 认证成功的用户名

    Raises:
        HTTPException: 认证失败时抛出 401 错误
    """
    if _check_auth_func is None:
        # 记录详细错误信息
        import traceback
        from loguru import logger
        logger.error("认证系统未初始化！调用栈：")
        logger.error(traceback.format_stack())
        raise HTTPException(
            status_code=500,
            detail="认证系统未初始化"
        )

    try:
        username = await _check_auth_func(request)
        if not username:
            raise HTTPException(
                status_code=401,
                detail="未认证"
            )
        return username
    except HTTPException:
        raise
    except Exception as e:
        from loguru import logger
        logger.error(f"认证检查时发生异常: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"认证检查失败: {str(e)}"
        )


async def optional_auth(request: Request) -> Optional[str]:
    """
    FastAPI 依赖注入函数：可选认证（不强制要求）

    用法：
        @app.get("/api/some_endpoint")
        async def some_endpoint(username: Optional[str] = Depends(optional_auth)):
            if username:
                # 用户已登录
                pass
            else:
                # 用户未登录，但仍可访问
                pass

    Args:
        request: FastAPI 请求对象

    Returns:
        Optional[str]: 认证成功返回用户名，否则返回 None
    """
    if _check_auth_func is None:
        return None

    return await _check_auth_func(request)


async def require_auth_page(request: Request) -> Optional[str]:
    """
    FastAPI 依赖注入函数：页面路由专用认证检查

    与 require_auth 的区别：
    - require_auth: 认证失败时抛出 HTTPException (适用于 API)
    - require_auth_page: 认证失败时返回 None (适用于页面，由路由函数处理重定向)

    用法：
        @app.get("/some_page", response_class=HTMLResponse)
        async def some_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
            if not username:
                return RedirectResponse(url="/login")
            # 用户已认证，继续处理
            pass

    Args:
        request: FastAPI 请求对象

    Returns:
        Optional[str]: 认证成功返回用户名，否则返回 None
    """
    if _check_auth_func is None:
        from loguru import logger
        logger.warning("认证系统未初始化，页面认证检查返回 None")
        return None

    try:
        return await _check_auth_func(request)
    except Exception as e:
        from loguru import logger
        logger.error(f"页面认证检查时发生异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
