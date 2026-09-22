#!/usr/bin/env python3
"""
@input: admin/routes/registry.py、admin/templates、admin/static 中的 `/api/*` 引用
@output: 路由重复项与前后端契约缺失项的静态审计结果
@position: 管理后台契约自检工具，负责在不启动应用的前提下校验 API 接线完整性
@auto-doc: Update header and folder INDEX.md when this file changes

路由契约自检工具（静态分析）

目标：
- 不依赖 FastAPI/运行环境，仅通过 AST 扫描代码
- 对照前端（admin/templates + admin/static）中出现的 /api/* 引用
  检查后端（admin/routes/registry.py 指定的文件清单）是否提供对应端点
- 输出缺失端点与重复端点（method+path 冲突）

用法：
  python3 tools/route_audit.py
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_FILE = ROOT / "admin" / "routes" / "registry.py"
TEMPLATE_DIRS = [
    ROOT / "admin" / "templates",
    ROOT / "admin" / "static",
]


METHOD_ATTRS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "options": "OPTIONS",
    "head": "HEAD",
    "websocket": "WEBSOCKET",
}


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _load_registered_files(registry_path: Path) -> List[Path]:
    if not registry_path.exists():
        raise SystemExit(f"registry.py 不存在: {registry_path}")

    tree = ast.parse(_read_text(registry_path), filename=str(registry_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "REGISTERED_ROUTE_FILES":
                if not isinstance(node.value, (ast.List, ast.Tuple)):
                    raise SystemExit("REGISTERED_ROUTE_FILES 不是 list/tuple")
                items: List[Path] = []
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        items.append(ROOT / elt.value)
                return items

    raise SystemExit("未在 registry.py 中找到 REGISTERED_ROUTE_FILES")


def _join(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if not path:
        return prefix
    if prefix.endswith("/") and path.startswith("/"):
        return prefix[:-1] + path
    if (not prefix.endswith("/")) and (not path.startswith("/")):
        return prefix + "/" + path
    return prefix + path


class RouteExtractor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._router_prefix: Dict[str, str] = {}
        self.routes: List[Tuple[str, str, int]] = []  # (method, path, lineno)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Detect: xxx = APIRouter(prefix="...")
        if isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "APIRouter":
                prefix = ""
                for kw in node.value.keywords:
                    if (
                        kw.arg == "prefix"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        prefix = kw.value.value
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._router_prefix[target.id] = prefix
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_decorators(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_decorators(node)
        self.generic_visit(node)

    def _handle_decorators(self, node: ast.AST) -> None:
        decos = getattr(node, "decorator_list", [])
        for dec in decos:
            if not isinstance(dec, ast.Call):
                continue
            if not isinstance(dec.func, ast.Attribute):
                continue

            attr = dec.func.attr
            if attr not in METHOD_ATTRS and attr not in {"api_route", "route"}:
                continue

            # app / router / bp
            obj = dec.func.value
            obj_name = obj.id if isinstance(obj, ast.Name) else None
            prefix = self._router_prefix.get(obj_name, "") if obj_name else ""

            path = None
            if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                path = dec.args[0].value
            for kw in dec.keywords:
                if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    path = kw.value.value

            if path is None:
                continue

            methods: Sequence[str]
            if attr == "api_route":
                methods = ["GET"]
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        ms = [
                            e.value.upper()
                            for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
                        methods = ms or ["GET"]
            elif attr == "route":
                # Starlette 风格，默认 GET
                methods = ["GET"]
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        ms = [
                            e.value.upper()
                            for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
                        methods = ms or ["GET"]
            else:
                methods = [METHOD_ATTRS[attr]]

            full_path = _join(prefix, path)
            lineno = getattr(node, "lineno", 0)
            for m in methods:
                self.routes.append((m, full_path, lineno))


PARAM_RE = re.compile(r"\{[^}]+\}")


def _route_to_regex(route: str) -> re.Pattern:
    escaped = ""
    idx = 0
    for m in PARAM_RE.finditer(route):
        escaped += re.escape(route[idx : m.start()])
        escaped += r"[^/]+"
        idx = m.end()
    escaped += re.escape(route[idx:])
    return re.compile("^" + escaped + "$")


def _extract_routes(files: Sequence[Path]) -> Tuple[List[Tuple[str, str, Path, int]], Dict[Tuple[str, str], List[Tuple[Path, int]]]]:
    routes: List[Tuple[str, str, Path, int]] = []
    dup_map: Dict[Tuple[str, str], List[Tuple[Path, int]]] = defaultdict(list)

    for fp in files:
        if not fp.exists():
            raise SystemExit(f"路由文件不存在: {fp}")
        tree = ast.parse(_read_text(fp), filename=str(fp))
        ex = RouteExtractor()
        ex.visit(tree)
        for method, path, lineno in ex.routes:
            routes.append((method, path, fp, lineno))
            dup_map[(method, path)].append((fp, lineno))

    return routes, dup_map


JS_TEMPLATE_RE = re.compile(r"\$\{[^}]+\}")
API_REF_RE = re.compile(r"/api/[^\s\"'`<>，。、]+")


def _extract_api_refs(dirs: Sequence[Path]) -> Dict[str, Set[Path]]:
    refs: Dict[str, Set[Path]] = defaultdict(set)
    trim_chars = ")],>;\"'"

    for base in dirs:
        if not base.exists():
            continue
        for fp in base.rglob("*"):
            if fp.suffix.lower() not in {".html", ".js"}:
                continue
            text = _read_text(fp)
            for m in API_REF_RE.finditer(text):
                raw = m.group(0)
                raw = raw.split("?", 1)[0]
                raw = JS_TEMPLATE_RE.sub("{param}", raw)
                raw = raw.rstrip(trim_chars)
                # 兼容：/api/system/logs${queryString} 这种“拼接查询字符串”的场景
                if raw.endswith("{param}") and (len(raw) > len("{param}")) and raw[-len("{param}") - 1] != "/":
                    raw = raw[: -len("{param}")]
                # 过滤明显的中文注释串（避免误判）
                if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
                    continue
                refs[raw].add(fp)
    return refs


def _match_ref_to_routes(ref: str, route_paths: Sequence[str], route_regexes: Sequence[re.Pattern]) -> bool:
    if ref in route_paths:
        return True

    # ref 本身含 {param} 时，允许匹配任意单段
    if "{param}" in ref:
        rx = re.compile("^" + re.escape(ref).replace(re.escape("{param}"), r"[^/]+") + "$")
        return any(rx.match(r) for r in route_paths)

    # ref 是具体路径时，尝试匹配带 {xxx} 的 route pattern
    return any(rx.match(ref) for rx in route_regexes)


def main() -> int:
    registered_files = _load_registered_files(REGISTRY_FILE)
    api_refs = _extract_api_refs(TEMPLATE_DIRS)

    routes, dup_map = _extract_routes(registered_files)
    route_paths = sorted({path for _, path, _, _ in routes})
    route_regexes = [_route_to_regex(p) for p in route_paths if "{" in p and "}" in p]

    # 1) 重复路由（method+path）
    duplicates = {k: v for k, v in dup_map.items() if len(v) > 1}

    # 2) 缺失路由（前端引用但后端未提供）
    missing: List[Tuple[str, List[Path]]] = []
    for ref in sorted(api_refs.keys()):
        if not _match_ref_to_routes(ref, route_paths, route_regexes):
            missing.append((ref, sorted(api_refs[ref])))

    print("route_audit")
    print(f"- registered_files: {len(registered_files)}")
    print(f"- routes_found: {len(routes)}")
    print(f"- api_refs_found: {len(api_refs)}")
    print(f"- duplicates(method+path): {len(duplicates)}")
    print(f"- missing(api refs): {len(missing)}")

    if duplicates:
        print("\n[duplicates]")
        for (method, path), defs in sorted(duplicates.items(), key=lambda x: (x[0][1], x[0][0])):
            print(f"- {method:9} {path} ({len(defs)})")
            for fp, ln in defs:
                rel = fp.relative_to(ROOT)
                print(f"  - {rel}:{ln}")

    if missing:
        print("\n[missing]")
        for ref, files in missing:
            print(f"- {ref}")
            for fp in files[:10]:
                rel = fp.relative_to(ROOT)
                print(f"  - {rel}")

    return 1 if duplicates or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
