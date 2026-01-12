import tomllib

import requests
from loguru import logger


def get_github_proxy() -> str:
    """
    从main_config.toml获取GitHub加速服务配置

    Returns:
        str: GitHub加速服务URL，如果未配置则返回空字符串
    """
    try:
        # 读取main_config.toml配置文件
        with open("main_config.toml", "rb") as f:
            config = tomllib.load(f)

        # 获取GitHub加速服务配置
        github_proxy = config.get("XYBot", {}).get("github-proxy", "")

        # 确保如果配置了加速服务，URL以"/"结尾
        if github_proxy and not github_proxy.endswith("/"):
            github_proxy += "/"
            logger.warning(f"GitHub加速服务URL未以'/'结尾，已自动添加: {github_proxy}")

        logger.info(f"GitHub加速服务配置: {github_proxy if github_proxy else '直连'}")
        return github_proxy
    except Exception as e:
        logger.error(f"读取GitHub加速服务配置失败: {e}")
        return ""  # 出错时返回空字符串，表示直连


def check_github_proxy(proxy_url: str) -> bool:
    """
    检查GitHub加速服务是否可用

    Args:
        proxy_url (str): GitHub加速服务URL

    Returns:
        bool: 加速服务是否可用
    """
    if not proxy_url:
        return False

    try:
        # 构建测试URL - 使用一个小文件进行测试
        test_url = f"{proxy_url}https://raw.githubusercontent.com/NanSsye/xxxbot-pad/main/version.json"

        # 设置较短的超时时间
        response = requests.get(test_url, timeout=5)

        # 检查响应状态码
        if response.status_code == 200:
            logger.info(f"GitHub加速服务 {proxy_url} 可用")
            return True
        else:
            logger.warning(
                f"GitHub加速服务 {proxy_url} 返回状态码: {response.status_code}"
            )
            return False
    except Exception as e:
        logger.warning(f"GitHub加速服务 {proxy_url} 不可用: {e}")
        return False


def get_github_url(url: str) -> str:
    """
    根据配置，返回可能经过加速服务的GitHub URL

    Args:
        url (str): 原始GitHub URL

    Returns:
        str: 可能经过加速服务的GitHub URL
    """
    github_proxy = get_github_proxy()

    # 如果未配置加速服务，直接返回原始URL
    if not github_proxy:
        return url

    # 如果URL已经包含加速服务，直接返回
    for proxy in ["ghfast.top", "gh-proxy.com", "mirror.ghproxy.com"]:
        if proxy in url:
            return url

    # 检查加速服务是否可用
    # 使用静态变量缓存检查结果，避免重复检查
    if not hasattr(get_github_url, "_proxy_available"):
        get_github_url._proxy_available = {}

    if github_proxy not in get_github_url._proxy_available:
        get_github_url._proxy_available[github_proxy] = check_github_proxy(github_proxy)

    # 如果加速服务不可用，直接返回原始URL
    if not get_github_url._proxy_available[github_proxy]:
        logger.warning(f"GitHub加速服务 {github_proxy} 不可用，使用直连")
        return url

    # 如果URL以"https://github.com"开头，添加加速服务
    if url.startswith("https://github.com"):
        # 对于大多数加速服务，正确的格式是直接将加速服务URL放在原始URL前面
        return f"{github_proxy}{url}"

    return url
