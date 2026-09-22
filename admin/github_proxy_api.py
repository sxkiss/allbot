"""
@input: main_config.toml、github.akams.cn 页面 JS 节点源（兼容 contribute 列表）、admin/cache 磁盘节点缓存、requests/FastAPI
@output: GitHub 反代节点查询、检测与写入配置的 API 路由（上游失败时回退磁盘/内存缓存）
@position: 管理后台 GitHub 反代配置层，为插件/框架下载提供代理节点管理
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import shutil
import time
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

import requests
import tomllib
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/github-proxy", tags=["github-proxy"])

_check_auth = None

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "main_config.toml"
_NODE_SOURCE_URL = "https://github.akams.cn/"
_DEFAULT_PROXY_NODE = "https://github.akams.cn/"
_CACHE_DIR = Path(__file__).resolve().parent / "cache"
_NODES_DISK_CACHE_PATH = _CACHE_DIR / "github_proxy_nodes.json"

_NODES_CACHE_TTL_SECONDS = 600
_nodes_cache: Dict[str, Any] = {"ts": 0, "nodes": []}

_TEST_GITHUB_URL = "https://raw.githubusercontent.com/microsoft/vscode/refs/heads/main/extensions/markdown-math/icon.png"


def _default_nodes() -> List[Dict[str, Any]]:
    return [
        {
            "url": _normalize_proxy_url(_DEFAULT_PROXY_NODE),
            "latency": None,
            "speed": None,
            "tag": "默认",
        }
    ]


def _sanitize_nodes(nodes: Any) -> List[Dict[str, Any]]:
    if not isinstance(nodes, list):
        return []

    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for item in nodes:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        try:
            proxy_url = _normalize_proxy_url(url)
        except Exception:
            continue
        if proxy_url in seen:
            continue
        seen.add(proxy_url)
        cleaned.append(
            {
                "url": proxy_url,
                "latency": item.get("latency"),
                "speed": item.get("speed"),
                "tag": str(item.get("tag") or "贡献"),
            }
        )
    return cleaned


def _is_default_only_nodes(nodes: List[Dict[str, Any]]) -> bool:
    if len(nodes) != 1:
        return False
    return nodes[0].get("url") == _normalize_proxy_url(_DEFAULT_PROXY_NODE)


def _load_disk_nodes_cache() -> List[Dict[str, Any]]:
    try:
        if not _NODES_DISK_CACHE_PATH.exists():
            return []
        data = json.loads(_NODES_DISK_CACHE_PATH.read_text(encoding="utf-8"))
        nodes = _sanitize_nodes(data.get("nodes") if isinstance(data, dict) else data)
        if nodes and not _is_default_only_nodes(nodes):
            return nodes
    except Exception as e:
        logger.warning(f"读取 GitHub 反代节点磁盘缓存失败: {e}")
    return []


def _save_disk_nodes_cache(nodes: List[Dict[str, Any]]) -> None:
    cleaned = _sanitize_nodes(nodes)
    if not cleaned or _is_default_only_nodes(cleaned):
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": int(time.time()),
            "source": _NODE_SOURCE_URL,
            "nodes": cleaned,
        }
        _NODES_DISK_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"写入 GitHub 反代节点磁盘缓存失败: {e}")


def _set_memory_nodes_cache(nodes: List[Dict[str, Any]], *, touch_ts: bool = True) -> None:
    cleaned = _sanitize_nodes(nodes)
    if not cleaned:
        return
    if touch_ts:
        _nodes_cache["ts"] = int(time.time())
    _nodes_cache["nodes"] = cleaned


def _get_memory_nodes_cache(allow_stale: bool = False) -> List[Dict[str, Any]]:
    nodes = _sanitize_nodes(_nodes_cache.get("nodes"))
    if not nodes:
        return []
    if allow_stale:
        return nodes
    cached_ts = int(_nodes_cache.get("ts") or 0)
    if cached_ts and int(time.time()) - cached_ts < _NODES_CACHE_TTL_SECONDS:
        return nodes
    return []


def _resolve_fallback_nodes() -> List[Dict[str, Any]]:
    # 优先内存里的上次成功结果，再读磁盘；默认节点最后兜底
    for nodes in (
        _get_memory_nodes_cache(allow_stale=True),
        _load_disk_nodes_cache(),
        _default_nodes(),
    ):
        cleaned = _sanitize_nodes(nodes)
        if cleaned:
            return cleaned
    return _default_nodes()


async def _require_auth(request: Request) -> Optional[str]:
    if _check_auth is None:
        return None
    username = await _check_auth(request)
    if not username:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return username


def _normalize_proxy_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("代理地址必须以 http:// 或 https:// 开头")
    if not url.endswith("/"):
        url += "/"
    return url


def _read_current_github_proxy() -> str:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
        value = config.get("AllBot", {}).get("github-proxy", "")
        return _normalize_proxy_url(value) if value else ""
    except Exception as e:
        logger.warning(f"读取当前 github-proxy 失败: {e}")
        return ""


def _write_github_proxy(value: str) -> None:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError("main_config.toml 不存在")

    normalized = _normalize_proxy_url(value) if value else ""

    backup_path = _CONFIG_PATH.with_suffix(_CONFIG_PATH.suffix + ".bak")
    try:
        shutil.copy2(_CONFIG_PATH, backup_path)
    except Exception as e:
        logger.warning(f"备份 main_config.toml 失败: {e}")

    content = _CONFIG_PATH.read_text(encoding="utf-8")
    value_literal = normalized.replace('"', '\\"')
    replacement_line = f'github-proxy = "{value_literal}"'

    section_re = re.compile(r"(?ms)^\[AllBot\]\s*$.*?(?=^\[|\Z)")
    match = section_re.search(content)
    if match:
        section = match.group(0)
        if re.search(r"(?m)^\s*github-proxy\s*=", section):
            section = re.sub(
                r"(?m)^(\s*github-proxy\s*=\s*).*$",
                lambda m: m.group(1) + f'"{value_literal}"',
                section,
            )
        else:
            lines = section.splitlines(True)
            if lines:
                lines.insert(1, replacement_line + "\n")
                section = "".join(lines)
            else:
                section = f"[AllBot]\n{replacement_line}\n"

        content = content[: match.start()] + section + content[match.end() :]
    else:
        suffix = "\n" if content and not content.endswith("\n") else ""
        content = content + suffix + f"\n[AllBot]\n{replacement_line}\n"

    _CONFIG_PATH.write_text(content, encoding="utf-8")


def _looks_like_proxy_domain(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    if text.startswith("http://") or text.startswith("https://"):
        return True
    # 节点源目前给的是域名/主机名
    if " " in text or "/" in text:
        return False
    if "." not in text:
        return False
    # 过滤明显无关域名
    blocked = (
        "google.com",
        "recaptcha",
        "github.com",
        "nextjs.org",
        "react.dev",
        "w3.org",
        "cloudflare.com",
        "giphy.com",
        "iconify.design",
        "jsdelivr.net",
        "akams.cn",
    )
    return not any(item in text for item in blocked)


def _extract_nodes_from_js(js_text: str) -> List[Dict[str, Any]]:
    if not js_text:
        return []

    tag_map = {
        "default": "默认",
        "contribute": "贡献",
        "survey": "测绘",
    }
    nodes: List[Dict[str, Any]] = []
    seen = set()

    # 兼容旧格式 + 当前 github.akams.cn 的 contribute 节点
    patterns = (
        r'\{label:"([^"]+)",value:"([^"]+)"\}',
        r"\{label:'([^']+)',value:'([^']+)'\}",
        r'\{value:"([^"]+)",label:"([^"]+)"\}',
        r"\{value:'([^']+)',label:'([^']+)'\}",
    )
    pairs: List[tuple[str, str]] = []
    for pattern in patterns:
        for left, right in re.findall(pattern, js_text):
            # 兼容 value/label 顺序互换
            if pattern.startswith(r'\{value:'):
                domain, label = left, right
            else:
                label, domain = left, right
            pairs.append((label, domain))

    for label, domain in pairs:
        domain = (domain or "").strip()
        label = (label or "").strip() or "contribute"
        if not _looks_like_proxy_domain(domain):
            continue
        try:
            if domain.startswith("http://") or domain.startswith("https://"):
                proxy_url = _normalize_proxy_url(domain)
            else:
                proxy_url = _normalize_proxy_url(f"https://{domain}/")
        except Exception:
            continue
        if proxy_url in seen:
            continue
        seen.add(proxy_url)
        nodes.append(
            {
                "url": proxy_url,
                "latency": None,
                "speed": None,
                "tag": tag_map.get(label, label),
            }
        )
    return nodes


def _fetch_nodes_from_upstream(force_refresh: bool = False) -> List[Dict[str, Any]]:
    if not force_refresh:
        cached = _get_memory_nodes_cache(allow_stale=False)
        if cached:
            return cached

        # 冷启动时先尝试磁盘缓存，避免上游抖动直接空白
        disk_cached = _load_disk_nodes_cache()
        if disk_cached:
            _set_memory_nodes_cache(disk_cached)
            return disk_cached

    nodes: List[Dict[str, Any]] = []
    try:
        # 从 github.akams.cn 页面的 JS bundle 中提取节点列表
        page_resp = requests.get(
            _NODE_SOURCE_URL,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        page_resp.raise_for_status()
        html = page_resp.text

        # 提取页面中所有 JS chunk URL
        chunk_paths = re.findall(
            r'<script[^>]*src="(/_next/static/chunks/[^"]+)"[^>]*>', html
        )

        # 遍历 chunk，找到包含节点数据的那个
        for path in chunk_paths:
            chunk_url = f"https://github.akams.cn{path}"
            try:
                chunk_resp = requests.get(
                    chunk_url,
                    timeout=8,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if chunk_resp.status_code != 200:
                    continue
                extracted = _extract_nodes_from_js(chunk_resp.text)
                if extracted:
                    nodes = extracted
                    break
            except Exception:
                continue

        if nodes:
            _set_memory_nodes_cache(nodes)
            _save_disk_nodes_cache(nodes)
            return nodes

        logger.warning("上游未解析到 GitHub 反代节点，回退缓存")
    except Exception as e:
        logger.warning(f"从上游获取 GitHub 反代节点失败: {e}")

    # 兜底：优先上次成功缓存，再默认节点；不把默认节点写成成功缓存
    fallback = _resolve_fallback_nodes()
    if not _is_default_only_nodes(fallback):
        logger.info(f"使用 GitHub 反代节点缓存兜底，共 {len(fallback)} 个")
        _set_memory_nodes_cache(fallback, touch_ts=False)
    else:
        _set_memory_nodes_cache(fallback)
    return fallback


def _probe_proxy(proxy_url: str, max_retries: int = 2) -> Dict[str, Any]:
    """
    检测单个代理节点的可用性
    
    Args:
        proxy_url: 代理URL
        max_retries: 最大重试次数，默认2次
    
    Returns:
        包含检测结果的字典
    """
    proxy_url = _normalize_proxy_url(proxy_url)
    test_url = f"{proxy_url}{_TEST_GITHUB_URL}"
    headers = {"Range": "bytes=0-2048"}
    
    last_error = None
    for attempt in range(max_retries + 1):
        start = time.perf_counter()
        try:
            # 使用元组超时：(连接超时, 读取超时)
            resp = requests.get(
                test_url,
                headers=headers,
                timeout=(3, 5),
                allow_redirects=True
            )
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            
            # 验证状态码：200 或 206（Range 请求的部分内容）
            status_ok = resp.status_code in (200, 206)
            
            # 验证 Content-Type
            content_type = resp.headers.get("Content-Type", "").lower()
            content_type_ok = "image/" in content_type or "octet-stream" in content_type
            
            # 验证内容长度
            content_length = len(resp.content)
            content_length_ok = content_length > 0
            
            # 综合判断
            ok = status_ok and content_type_ok and content_length_ok
            
            return {
                "ok": ok,
                "status_code": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "final_url": resp.url,
                "content_length": content_length,
            }
            
        except requests.Timeout as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            last_error = {
                "ok": False,
                "status_code": None,
                "elapsed_ms": elapsed_ms,
                "error": "连接超时",
            }
            # 超时不重试，直接返回
            return last_error
            
        except requests.ConnectionError as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            last_error = {
                "ok": False,
                "status_code": None,
                "elapsed_ms": elapsed_ms,
                "error": "连接失败",
            }
            # 连接错误重试
            if attempt < max_retries:
                time.sleep(0.1)  # 短暂延迟后重试
                continue
                
        except requests.HTTPError as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            last_error = {
                "ok": False,
                "status_code": getattr(e.response, "status_code", None),
                "elapsed_ms": elapsed_ms,
                "error": f"HTTP错误: {str(e)}",
            }
            return last_error
            
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            last_error = {
                "ok": False,
                "status_code": None,
                "elapsed_ms": elapsed_ms,
                "error": str(e),
            }
            # 其他错误重试
            if attempt < max_retries:
                time.sleep(0.1)
                continue
    
    # 所有重试都失败，返回最后一次错误
    return last_error if last_error else {
        "ok": False,
        "status_code": None,
        "elapsed_ms": 0,
        "error": "未知错误",
    }


def _probe_proxies_batch(proxy_urls: List[str], max_workers: int = 5) -> List[Dict[str, Any]]:
    """
    并发批量检测多个代理节点
    
    Args:
        proxy_urls: 代理URL列表
        max_workers: 最大并发工作线程数，默认5
    
    Returns:
        结果列表，每个元素包含 url 和检测结果
    """
    results = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有检测任务
        future_to_url = {
            executor.submit(_probe_proxy, url): url
            for url in proxy_urls
        }
        
        # 按提交顺序收集结果
        for url in proxy_urls:
            # 找到对应的 future
            future = None
            for f, u in future_to_url.items():
                if u == url:
                    future = f
                    break
            
            if future:
                try:
                    result = future.result()
                    results.append({
                        "url": _normalize_proxy_url(url),
                        **result
                    })
                except Exception as e:
                    logger.error(f"检测代理 {url} 时发生异常: {e}")
                    results.append({
                        "url": _normalize_proxy_url(url),
                        "ok": False,
                        "status_code": None,
                        "elapsed_ms": 0,
                        "error": f"检测异常: {str(e)}"
                    })
            else:
                results.append({
                    "url": _normalize_proxy_url(url),
                    "ok": False,
                    "status_code": None,
                    "elapsed_ms": 0,
                    "error": "未找到检测任务"
                })
    
    return results


@router.get("/current")
async def get_current_proxy(request: Request):
    await _require_auth(request)
    return JSONResponse({"success": True, "data": {"github_proxy": _read_current_github_proxy()}})


@router.get("/nodes")
async def get_nodes(request: Request, refresh: bool = False):
    await _require_auth(request)

    try:
        nodes = await run_in_threadpool(_fetch_nodes_from_upstream, refresh)
        return JSONResponse({"success": True, "data": {"nodes": nodes}})
    except Exception as e:
        logger.error(f"获取 GitHub 反代节点失败: {e}")
        fallback = _resolve_fallback_nodes()
        if fallback:
            return JSONResponse(
                {
                    "success": True,
                    "data": {"nodes": fallback},
                    "message": "上游获取失败，已返回缓存节点",
                }
            )
        return JSONResponse({"success": False, "error": f"获取节点失败: {e}"}, status_code=500)


@router.post("/check")
async def check_node(request: Request):
    await _require_auth(request)

    payload = await request.json()
    url = payload.get("url") if isinstance(payload, dict) else ""
    try:
        result = await run_in_threadpool(_probe_proxy, url)
        return JSONResponse({"success": True, "data": {"url": _normalize_proxy_url(url), **result}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@router.post("/check-batch")
async def check_nodes_batch(request: Request):
    """
    批量检测多个代理节点
    
    请求体格式:
    {
        "urls": ["url1", "url2", ...]
    }
    
    响应格式:
    {
        "success": true,
        "data": {
            "results": [
                {"url": "url1", "ok": true, ...},
                {"url": "url2", "ok": false, ...}
            ]
        }
    }
    """
    await _require_auth(request)
    
    try:
        payload = await request.json()
        urls = payload.get("urls") if isinstance(payload, dict) else []
        
        # 验证输入
        if not isinstance(urls, list):
            return JSONResponse(
                {"success": False, "error": "urls 必须是数组"},
                status_code=400
            )
        
        if len(urls) == 0:
            return JSONResponse(
                {"success": True, "data": {"results": []}},
                status_code=200
            )
        
        if len(urls) > 10:
            return JSONResponse(
                {"success": False, "error": "单次最多检测10个节点"},
                status_code=400
            )
        
        # 执行批量检测
        results = await run_in_threadpool(_probe_proxies_batch, urls)
        
        return JSONResponse({
            "success": True,
            "data": {"results": results}
        })
        
    except Exception as e:
        logger.error(f"批量检测代理节点失败: {e}")
        return JSONResponse(
            {"success": False, "error": f"批量检测失败: {str(e)}"},
            status_code=500
        )


@router.post("/apply")
async def apply_node(request: Request):
    await _require_auth(request)

    payload = await request.json()
    url = payload.get("url") if isinstance(payload, dict) else ""
    try:
        normalized = _normalize_proxy_url(url) if url else ""
        await run_in_threadpool(_write_github_proxy, normalized)
        return JSONResponse(
            {
                "success": True,
                "message": "已更新 github-proxy 配置（需要重启服务生效）",
                "data": {"github_proxy": normalized},
            }
        )
    except Exception as e:
        logger.error(f"更新 github-proxy 失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


def register_github_proxy_routes(app, check_auth):
    global _check_auth
    _check_auth = check_auth
    app.include_router(router)
    logger.info("GitHub 反代节点 API 路由已注册")
