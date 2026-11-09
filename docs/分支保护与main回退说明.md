# 分支保护规则与 main 回退说明

> 记录 2026-10-15 的 main 分支污染事件、回退操作，以及已配置的 GitHub 分支保护规则。

## 一、背景：main 被 875 分支内容污染

### 现象
`main` 分支上出现了大量 875 协议/卡密相关提交（62 个提交 + 4 个 merge 节点），
这些内容本应只存在于 `feat/wxserver875-license` 分支。

### 根因
查 `main` 的 reflog，发现是**在 main 分支上主动执行了 merge**：

```
40a5dd7 main@{0}: merge origin/feat/wxserver875-license: Merge made by the 'ort' strategy.
b2a79e5 main@{1}: merge origin/dev: Merge made by the 'ort' strategy.
```

即：不是 `feat/wxserver875-license` 分支自己把内容推到了 main，而是 **main 分支
主动 merge 了 875 分支**（merge commit `40a5dd7`），导致 875 的内容进入 main。

### 影响范围
`40a5dd7` 这个 merge 把仓库从 869/874 协议整体改造为 875 协议：
- 删除 `plugins/Protocol869Demo` 整个插件
- 删除 `ROUTE_MAP_874`（87 行路由映射）
- 删除 869 免扫码重登录（`_maybe_relogin_869`）等
- `bot_core/login_handler.py` 砍掉 634 行、`bot_core/message_listener.py` 砍掉 470 行
- `WechatAPI/Client/core.py` 默认协议 869 → 875
- 新增 `protocol875.py`、`padface875`（41MB）、`libv08.so` 等二进制

## 二、回退操作

按要求把 `main` 回退到 `6ae550d`，且保持 `feat/wxserver875-license` 不变。

### 回退前核实
| 检查项 | 结果 |
|---|---|
| 目标 commit | `6ae550d485cf4774600dfc36888ca48ff2f96b09`（与要求逐位一致） |
| main 领先的 66 个提交构成 | 62 个来自 875 分支 + 4 个 merge 节点，**main 自身无独立提交** |
| `6ae550d` 是否在 dev 上 | 是 → 回退不影响 dev |
| 875 分支是否含 `6ae550d` | 否 → 875 是独立线，回退 main 不影响它 |

### 执行命令
```bash
git branch -f main 6ae550d                      # 移动指针，不碰工作区
git push --force-with-lease origin main         # 安全强推（带 lease 防并发覆盖）
```

### 回退后状态
| 分支 | 结果 |
|---|---|
| `origin/main` | `6ae550d` ✅ |
| `origin/feat/wxserver875-license` | `7db5b59`（原样未动）✅ |
| `origin/dev` | `344794b`（原样未动）✅ |
| main 上 AssistantPlugin | 回到 1600 行（875 内容已撤） |

> 如需撤销本次回退：`git branch -f main 40a5dd7 && git push --force-with-lease origin main`

## 三、已配置的 GitHub 分支保护规则

通过 GitHub API 为 `main` 分支设置了保护：

```bash
curl -X PUT -H "Authorization: token <TOKEN>" \
  https://api.github.com/repos/sxkiss/allbot/branches/main/protection \
  -d '{
    "required_status_checks": null,
    "enforce_admins": false,
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "restrictions": null,
    "allow_force_pushes": false,
    "allow_deletions": false
  }'
```

### 生效规则
| 规则 | 状态 | 作用 |
|---|---|---|
| 禁止强制推送（force push） | ✅ 已禁止 | 防止历史被改写 |
| 禁止删除分支 | ✅ 已禁止 | 防止误删 main |
| 必须走 PR | ✅ 已启用 | 防止直接 push/merge 到 main |
| 管理员豁免 | ❌ 未开启 | 管理员同样受保护约束 |

> 注：`required_approving_review_count=0` 表示需要走 PR 流程，但不强制要求他人审批
> （单人项目友好）。如需强制审批，把该值改为 1。

## 四、日常规范建议

1. **功能开发一律在特性分支**，通过 PR 合并到 main，不要直接在 main 上 merge。
2. **875 协议相关内容只留在 `feat/wxserver875-license`**，不要 merge 进 main。
3. 合并前先确认目标分支的基线（869 vs 875），避免协议层冲突。
4. 强推前务必用 `--force-with-lease`，并先核对旧值。
5. 定期用 `git log --oneline main..<分支>` 检查各分支领先情况，及时发现误合并。

## 五、时间线

- 2026-10-14 17:01 — `40a5dd7`：main 误 merge 875 分支（污染发生）
- 2026-10-15 19:xx — 发现污染，核实回退方案
- 2026-10-15 19:xx — `main` 回退到 `6ae550d` 并强推
- 2026-10-15 19:xx — 配置 main 分支保护规则
