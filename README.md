# Colab Web Management — Server Monitor & Control Plane (v2.0)

零第三方依赖的 Google Colab 多实例监控与远程管控台（Python 3.11+ 标准库 + 原生前端），界面遵循 [Sage Design Paradigm v1.1.0](https://github.com/amkkrr/sage_design_paradigm)（默认 Sage 鼠尾草绿主题，支持可选 **Ash Marble** 苍白/苍灰主题及 `auto → light → dark`，全量本地托管 OFL-1.1 字体）。

## 核心特性

- **多实例发现与双通道可靠控制**：
  - **通道 A（HTTPS 实时隧道）**：通过 `agent/colab_agent.py` 暴露 `/agent/v1/*` 接口，毫秒级采集与执行控制动作。
  - **通道 B（Google Drive 带外信箱）**：当隧道断流或实例处于脱机批跑/音频合成模式时，自动复用同一 `cmd_id` 降级写入 `_colab_monitor/commands/` 并追踪 `acks/` 回执。
  - **向后兼容 Legacy**：自动发现旧版 `active_endpoint.json` 并与 v2 实例同机去重。
- **全生命周期与深度运维（5 大类 19 项白名单动作，严禁任意 RCE）**：
  - 生命周期：手动保活、保活租约（30m / 1h / 2h）、空闲阈值热更新、会话死线、**排空关机（跑完即停）**、撤回排空、强二次确认立即关机与多实例批量排空。
  - 运维与队列：隧道重建、ComfyUI 中断/清显存/重启、白名单受管进程查看与终止、安全磁盘清理（先 `dry_run` 预览，硬隔离云盘与 `outputs/`）、任务队列暂停/恢复/热重载/失败重试、云盘落盘校验、脱敏白名单日志检视与快照转存。
- **方案 A：笔记本一键唤起与安全幂等注入**：
  - 扫描本地挂载云盘 `.ipynb`，自动识别 OAuth 静默 / FUSE 挂载 / 独立挂载模式。
  - 支持先预览再以独立标记单元幂等写入（修改前自动在纯本地目录备份原文件）、一键升级与一键卸载还原，并绑定 Colab URL 实现浏览器一键唤起。
- **纵深安全与零用户数据入库保障**：
  - 本地服务默认仅绑定 `127.0.0.1:8765`（非回环需显式 `--allow-remote`），全路由强制校验 `Host`/`Origin` 防 DNS rebinding，所有写接口校验 `X-H3-Action` / `X-Colab-Action`，并下发 `CSP` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer`。
  - 双通道统一校验 `HMAC-SHA256`、`instance_id`、`expires_at`（±60s 容差）与共享 `cmd_id` 幂等去重；全接口与日志自动脱敏 `api_key` 及常见 Token。
  - SQLite 时序库、主开机账本、链接配置与笔记本备份均强制存于纯本地状态目录（检测到位于 Google Drive / CloudStorage 目录时启动拒绝）。
  - 内置 `.gitignore`、`scripts/privacy_guard.py`、本地 Git `pre-commit`/`pre-push` 钩子、`.gitleaks.toml` 及 GitHub Actions 自动安全流水线（Privacy Guard + Gitleaks + Bandit + CodeQL + 全量回归测试）。

## 快速启动

```bash
# 1. 启动 Web 监控与管控台（默认绑定 127.0.0.1:8765 并自动打开浏览器）
python3 server.py

# 自定义参数示例
python3 server.py --monitor-root /path/to/_colab_monitor --ep /path/to/active_endpoint.json --port 8765 --no-open
```

> 即使当前没有在线 Colab 实例，打开界面后点击顶部 **「演示样本」** 即可体验双实例切换（在线隧道 + 云盘带外信箱）、生命周期策略、子服务管控、队列编排、白名单日志/受控进程及笔记本注入预览。

### CLI 用法

```bash
# v2 多实例管控命令
python3 h3_cli_monitor.py instances
python3 h3_cli_monitor.py status --instance <instance_id>
python3 h3_cli_monitor.py action --instance <instance_id> lifecycle.keepalive
python3 h3_cli_monitor.py action --instance <instance_id> lifecycle.drain_shutdown --yes
python3 h3_cli_monitor.py logs --instance <instance_id> --source agent --tail 200

# 兼容 v1 单实例命令
python3 h3_cli_monitor.py --watch 3
python3 h3_cli_monitor.py --keepalive
python3 h3_cli_monitor.py --shutdown
```

## 环境变量（可选）

| 环境变量 | 说明 |
|---|---|
| `COLAB_MONITOR_ROOT` | 指定云盘 `_colab_monitor` 共享交换目录路径 |
| `COLAB_MONITOR_EP_FILE` | 指定默认 Legacy `active_endpoint.json` 路径 |
| `COLAB_MONITOR_DRIVE_SUBDIR` | 注入单元格时默认使用的 Google Drive 相对目录（默认自动从挂载路径推断） |
| `H3_MONITOR_STATE_DIR` | 指定纯本地 SQLite 与配置目录（严禁指向云盘同步目录） |

## 开发与安全校验

```bash
# 安装本地防隐私泄露 Git 钩子（pre-commit + pre-push）
./scripts/install_git_hooks.sh

# 运行全量测试、前端语法与隐私扫描
node --check web/theme-init.js
node --check web/app.js
python3 -B -m unittest discover -s tests -v
python3 -B scripts/privacy_guard.py --all
```

详见 [docs/COLAB_SERVER_CONTROL_SPEC.md](docs/COLAB_SERVER_CONTROL_SPEC.md)、[docs/ADOPTION_CONTRACT.md](docs/ADOPTION_CONTRACT.md) 与 [SECURITY.md](SECURITY.md)。
