# Colab 服务器全量监控与远程管控台 · 技术规格书 (v2.0)

> **状态**：v2.0 Reviewed（已按首轮架构与安全审计修订定版，可进入 Phase 1 实施）  
> **演进基线**：`colab-web-monitor` v1.2（含 [ADOPTION_CONTRACT.md](./ADOPTION_CONTRACT.md)，已通过 10 项自动化回归测试：Sage v1.1.0 Token/对比度、本地 OFL-1.1 字体与 `OFL.txt`、`Host`/`Origin` 防 DNS rebinding、`--allow-remote` 保护、单飞轮询、弹窗焦点恢复与 CLI `--shutdown` 确认）  
> **适用范围**：`sample_studio`、`sample_batch_project`、脱机队列批跑笔记本，以及 `Colab Notebooks/` 下的 Kokoro 有声书、Whisper 转写等全部 Google Colab 运行时实例。

---

## 1. 背景、目标与边界

### 1.1 演进目标
现有 v1.2 监控看板仅绑定单个 H3 在线推理网关的 `active_endpoint.json`，只具备单实例观测与基础保活/立即关机能力。本规格书将其升级为**通用的多实例 Colab 服务器监控与管控平面（Monitor & Control Plane）**：

1. **通用零依赖探针接入**：提供单文件轻量探针 `agent/colab_agent.py`（硬件与进程采集仅用 Python 标准库 + `/proc` + `nvidia-smi`，不强依赖 `psutil`/`pynvml`），一行代码即可接入任意 Colab 笔记本（无论是否启动 FastAPI 网关或公网隧道）。
2. **双通道可靠控制**：构建「HTTPS 实时隧道（通道 A）+ Google Drive 带外命令信箱（通道 B）」双通道机制——即使 `trycloudflare.com` 断流，或实例处于无隧道的脱机批跑/音频合成模式，本地依然能可靠执行暂停、调参、重建隧道与安全关机。
3. **全生命周期与深度运维管理**：覆盖排空关机（跑完即停）、保活租约、动态空闲策略、隧道自愈、托管子服务（如 ComfyUI）启停与清显存、受控进程管理、安全磁盘清理、批跑队列编排及白名单日志检视。
4. **100% 向后兼容**：未改造的旧版笔记本（仅写 `active_endpoint.json` 并暴露 `/health`、`/v1/metrics`）仍可作为 Legacy 实例被自动发现并正常工作；既有 v1 HTTP 路由与 CLI 参数保持兼容。

### 1.2 物理边界与非目标（Non-Goals）
- **不提供无浏览器冷启动**：Google Colab 官方未开放从外部直接冷启动分配新 VM 的公开 API（自动化模拟登录违反平台限制且极易风控）。本系统接管范围为**笔记本启动并加载 `colab_agent.py` 之后的完整运行期生命周期**。
- **严禁任意远程代码/Shell 执行（No Arbitrary RCE）**：因穿透隧道暴露于公网，Colab 端拒绝任何自由文本命令或任意脚本注入；所有管理能力必须封装为**受控枚举动作（Action Enum）+ 严格参数校验 + 统一鉴权验签**。
- **本地控制端零第三方依赖**：本地服务端与 CLI 继续仅使用 Python 3.11+ 标准库（`http.server`、`urllib`、`sqlite3`、`json`、`hmac`、`threading`），确保跨 macOS / Linux / Windows 开箱即用。
- **数据库绝不落云盘**：高频写入的本地 SQLite 时序与审计库必须存放在**纯本地非云盘目录**，禁止建在 Google Drive 同步目录内，避免 WAL 锁冲突与文件损坏。

---

## 2. 总体架构与目录规划

### 2.1 系统拓扑与双通道数据流

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Google Colab VM 实例 (G4 / L4 / A100 / T4 / CPU)                            │
│                                                                             │
│  ┌──────────────────────────┐      ┌─────────────────────────────────────┐  │
│  │ 业务负载                 │◄────►│ colab_agent.py (零依赖探针与执行器) │  │
│  │ - H3 FastAPI + ComfyUI   │      │ - nvidia-smi / /proc / Drive 采集器 │  │
│  │ - 脱机批跑队列循环       │      │ - 1h 内存环形时序缓冲 & 脱敏日志    │  │
│  │ - Kokoro / Whisper 循环  │      │ - 双通道共享幂等表 + HMAC 验签      │  │
│  └──────────────────────────┘      └──────────┬───────────────────┬──────┘  │
└───────────────────────────────────────────────┼───────────────────┼─────────┘
                                                │                   │
                     通道 A：实时 HTTPS 隧道     │                   │ 通道 B：云盘带外同步 (15~60s)
                  (trycloudflare / Bearer+TTL)  │                   │ (FUSE 或 OAuth v3 API + HMAC)
                                                ▼                   ▼
                                     ┌──────────────────┐ ┌───────────────────────────────────┐
                                     │ /agent/v1/* API  │ │ <DRIVE_WORKSPACE>/_colab_monitor│
                                     └──────────┬───────┘ │  ├── instances/<id>.json (心跳)   │
                                                │         │  ├── secrets/<id>.json   (密钥)   │
                                                │         │  ├── commands/<id>/*.json(指令)   │
                                                │         │  └── acks/<id>/*.json    (回执)   │
                                                │         └─────────────────┬─────────────────┘
                                                ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 本地管控服务端 (server.py，默认仅绑定 127.0.0.1:8765)                       │
│  - 后台调度线程（独立于浏览器页面：15s 常规采样 / 有在途指令时 5s 追 ACK）  │
│  - 多实例发现引擎（聚合 _colab_monitor/instances/ + Legacy 去重适配）       │
│  - 双通道指令路由器（优先通道 A；超时/失败复用同 cmd_id 自动降级通道 B）    │
│  - 纯本地状态库 (~/.local/state 或 ~/Library/Application Support/sqlite3)   │
│  - Sage v1.1.0 Web 管控台 + CLI 总控工具 + 系统告警通知 + 云盘 GC 清理器    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 目录结构与存储边界规划

```text
# 1) Google Drive 云盘共享交换目录（跨本机与 Colab 的松耦合信箱）
<DRIVE_WORKSPACE>/_colab_monitor/
├── instances/                         # 实例心跳快照 (<instance_id>.json)
├── secrets/                           # 各实例鉴权与 HMAC 密钥 (<instance_id>.json，受云盘账号隔离，离线 24h 自动清理)
├── commands/                          # 本地投递的带外控制指令 (<instance_id>/<cmd_id>.json)
├── acks/                              # Colab 端写回的指令执行回执 (<instance_id>/<cmd_id>.json)
├── snapshots/                         # 远程一键转存的排障日志快照包
└── audit/                             # 按月归档的脱敏审计流水 (audit_YYYY-MM.jsonl)

# 2) 纯本地状态目录（严禁置于 Google Drive / CloudStorage 同步目录内！）
#   macOS:   ~/Library/Application Support/h3-web-monitor/
#   Linux:   ~/.local/state/h3-web-monitor/
#   Windows: %LOCALAPPDATA%\h3-web-monitor\
#   （可通过 --state-dir 或环境变量 H3_MONITOR_STATE_DIR 覆盖；若检测到位于云盘目录则启动报错拒绝）
<LOCAL_STATE_DIR>/
├── monitor_state.sqlite3              # SQLite WAL 模式：14 天时序、命令队列与告警事件
├── uptime_ledger.json                 # 多实例并发开机账本（首次启动自动从旧版 ep 同级账本一次性迁移）
└── shutdown_markers.json              # 本地短时关机追踪标记

# 3) 代码仓库目录（平滑扩展现有入口，外部脚本零破坏）
<REPO_ROOT>/                           # 当前基线目录（后续统一合并至 h3-web-monitor）
├── agent/
│   └── colab_agent.py                 # [新增] Colab 端零依赖单文件 Agent（内置 FUSE & OAuth DriveBackend）
├── control/
│   ├── registry.py                    # [新增] 多实例发现、Legacy 扫描与同机去重适配器
│   ├── router.py                      # [新增] 隧道 + 云盘双通道路由器、HMAC 签名与同 cmd_id 降级
│   ├── collector.py                   # [新增] 本地后台采样、ACK 追踪、多实例 Uptime 追踪与云盘 GC
│   ├── storage.py                     # [新增] 纯本地 SQLite 时序存储、命令历史与审计日志
│   └── notifier.py                    # [新增] macOS 原生通知与 Webhook (Bark/ntfy/Telegram) 推送
├── server.py                          # [升级] 统一 Host/Origin 校验，兼容 v1 路由并挂载 /api/v2/*
├── h3_cli_monitor.py                  # [升级] 兼容 v1 参数并扩展 v2 多实例与管控子命令
├── web/                               # [升级] Sage v1.1.0 多实例顶栏、精简工具栏、运维卡片、日志/进程抽屉与 v2 Demo 模式
├── docs/
│   ├── ADOPTION_CONTRACT.md           # 既有 Sage UI 适配契约（v1.2 基线）
│   └── COLAB_SERVER_CONTROL_SPEC.md   # 本技术规格书（v2.0 Reviewed）
└── tests/
    ├── test_dashboard.py              # 既有 10 项 UI/安全/契约回归测试（持续 100% 通过）
    └── test_control_plane.py          # [新增] Fake Agent、双通道降级幂等、HMAC/防重放、多实例 Uptime 与安全拦截测试
```

---

## 3. 数据契约与通信协议

### 3.1 实例注册与心跳快照 (`_colab_monitor/instances/<instance_id>.json`)

- `instance_id` 格式：`inst_YYYYMMDD_HHMMSS_<8位随机hex>`（如 `inst_20261121_101500_7c9e4a1b`），避免同秒启动冲突。
- 心跳周期：默认 `30s`（可配置 `15..60s`）。
- **Drive 写入约定**：
  - **OAuth v3 API 模式**：缓存 `<instance_id>.json` 的 `fileId`，每次刷新走 `files().update(fileId=...)` 原地原子更新，杜绝 Drive 产生同名副本（`inst_xxx (1).json`）。读取端若发现同名多文件，按 `modifiedTime` 最新者为准并清理旧副本。
  - **FUSE / 本地 Google Drive Desktop 模式**：禁止在同步目录内长期残留 `.tmp.<pid>` 引发多余同步；写入前先写至独立临时名（以 `._tmp_` 开头，读写端均显式忽略 `._tmp_*`），经 `flush` + `os.replace` 重试（最多 3 次，退避 20ms）覆盖目标文件，失败时回退直接覆写并立即清理临时文件。
- **`api_key` 严格隔离**：`instances/` 严禁包含 `api_key`，密钥仅写入 `_colab_monitor/secrets/<instance_id>.json`（结构：`{"schema_version": "2.0", "instance_id": "...", "api_key": "...", "created_at": "..."}`）。
- **`capabilities` 1:1 统一命名**：`capabilities` 数组直接使用 §4 中的精确 `action` 枚举名，以及 3 项只读能力键（`read.processes`、`read.logs`、`read.timeseries`），前后端按完全一致的字符串判断能力显隐。注：`network.start_gateway`（仅脱机/未启网关实例宣告）与 `network.stop_gateway`（仅已启网关实例宣告）按当前 `run_mode` 动态互斥宣告。

```json
{
  "schema_version": "2.0",
  "instance_id": "inst_20261121_101500_7c9e4a1b",
  "project": "sample_studio",
  "label": "Sample Studio · L4 在线网关",
  "notebook_name": "sample_gateway_colab.ipynb",
  "run_mode": "online_gateway",
  "started_at": "2026-11-21T02:15:00Z",
  "heartbeat_at": "2026-11-21T02:45:30Z",
  "heartbeat_interval_sec": 30,
  "capabilities": [
    "lifecycle.keepalive",
    "lifecycle.set_keepalive_lease",
    "lifecycle.set_idle_policy",
    "lifecycle.set_deadline",
    "lifecycle.drain_shutdown",
    "lifecycle.cancel_drain",
    "lifecycle.shutdown_now",
    "network.rebuild_tunnel",
    "network.stop_gateway",
    "ops.service_control",
    "ops.kill_process",
    "ops.cleanup_disk",
    "workload.pause_queue",
    "workload.resume_queue",
    "workload.reload_drive_queue",
    "workload.queue_mutate",
    "storage.flush_and_verify",
    "logs.snapshot_to_drive",
    "read.processes",
    "read.logs",
    "read.timeseries"
  ],
  "managed_services": ["comfyui"],
  "tunnel": {
    "enabled": true,
    "healthy": true,
    "base_url": "https://example-tunnel.trycloudflare.com",
    "agent_prefix": "/agent/v1",
    "updated_at": "2026-11-21T02:16:12Z"
  },
  "legacy_ep_hint": "sample_studio/active_endpoint.json",
  "lifecycle": {
    "state": "running",
    "idle_seconds": 142,
    "idle_timeout_minutes": 60,
    "keepalive_lease_until": null,
    "deadline_shutdown_at": null,
    "auto_shutdown_after_batch": true,
    "drain_requested": false
  },
  "hardware": {
    "gpu": {
      "available": true,
      "name": "NVIDIA L4",
      "count": 1,
      "util_gpu_pct": 84.0,
      "util_mem_pct": 41.0,
      "vram_used_mib": 16420.5,
      "vram_total_mib": 23034.0,
      "vram_pct": 71.3,
      "temp_c": 63.0,
      "power_draw_w": 68.4,
      "power_limit_w": 72.0,
      "clock_sm_mhz": 2040,
      "throttle_reasons": "none"
    },
    "cpu": {
      "cores_logical": 12,
      "util_pct": 28.5,
      "load_avg_1m": 2.41
    },
    "ram": {
      "used_gib": 18.42,
      "total_gib": 52.96,
      "pct": 34.8,
      "cgroup_limit_gib": 52.96,
      "oom_kill_count": 0
    },
    "disk": {
      "mount": "/",
      "used_gib": 64.2,
      "free_gib": 48.5,
      "total_gib": 112.7,
      "pct": 57.0
    },
    "drive": {
      "mode": "oauth_silent",
      "healthy": true,
      "latency_ms": 185,
      "pending_uploads": 0,
      "last_verified_at": "2026-11-21T02:45:00Z"
    }
  },
  "services": {
    "comfyui": { "status": "running", "pid": 1842, "port": 8188, "uptime_sec": 1810 },
    "cloudflared": { "status": "running", "pid": 1995, "uptime_sec": 1758 },
    "gateway": { "status": "running", "port": 8000, "uptime_sec": 1760 }
  },
  "workload": {
    "kind": "job_queue",
    "paused": false,
    "running": 1,
    "queued": 4,
    "succeeded": 19,
    "failed": 0,
    "progress": {
      "stage": "离线/在线队列生成",
      "current_item": "task_020_courtyard_4k",
      "done": 19,
      "total": 24,
      "eta_sec": 215
    }
  }
}
```

### 3.2 实例在线状态判定状态机（Liveness State Machine）

考虑到 Google Drive Desktop / macOS File Provider 常见 `30–90s` 同步抖动以及本机与 Colab 间可能存在的轻量时钟偏差，本地以 UTC `heartbeat_at` 为唯一权威时钟基准计算 `raw_age = now_utc - heartbeat_at`（**严禁用本地文件 `mtime` 覆盖过期的 `heartbeat_at`**，避免重启本地服务或云盘重新同步文件时把已离线实例误判为在线）：
- 若 `-60s <= raw_age <= 120s`：记有效延迟 `age = max(0, raw_age)`，视为心跳新鲜。
- 若 `raw_age < -60s`（远端时钟超前本机超过 60 秒）：归入 `DEGRADED_STALE` 并告警提示时钟异常，不盲信为正常在线。
- 若 `raw_age > 120s`：按下方阈值逐级降级至 `DEGRADED_STALE` / `OFFLINE`。

| 状态枚举 | 判定条件 | 可用监控数据源 | 可用控制通道 | UI 状态指示 |
|---|---|---|---|---|
| `ONLINE_LIVE` | 隧道启用且 `GET /agent/v1/health` 或 `/agent/v1/metrics`（Legacy 为 `/health`）响应正常 | 实时秒级采样 + 本地归档 | **通道 A（毫秒级）**，失败复用同 `cmd_id` 回退通道 B | 绿点 · 在线（实时隧道） |
| `ONLINE_DRIVE_ONLY` | 无隧道或隧道不可达，且 `-60s <= raw_age <= 120s` | 云盘心跳快照（30s 粒度） | **通道 B（云盘信箱，约 15–60s 生效）** | 青/绿点 · 在线（云盘信箱管控） |
| `DRAINING` | 在线（任一通道）且 `lifecycle.drain_requested == true` | 同上 | 通道 A 或 B（支持取消排空或升级立即关机） | 琥珀点 · 排空停机等待中 |
| `DEGRADED_STALE` | 隧道不可达，且 `120s < raw_age <= 300s`（或 `raw_age < -60s` 时钟异常） | 最后一次已知快照（标明延迟秒数） | 允许投递通道 B（附同步延迟与超时提示） | 琥珀点 · 心跳延迟 / 同步滞后 |
| `SHUTTING_DOWN` | 收到关机 ACK 或心跳写入 `state == "shutting_down"`（180s 内） | 冻结最后快照 | 已终止，不接受新指令 | 灰点 · 正在释放实例 |
| `OFFLINE` | `raw_age > 300s` 或实例写明 `state == "terminated"` | 归档至历史会话列表 | 禁用全部远程操作按钮 | 红/灰点 · 已离线 |

### 3.3 统一控制指令与回执信封 (`ActionEnvelope` & `ActionAck`)

无论是走通道 A（`POST /agent/v1/actions`）还是通道 B（写入 `_colab_monitor/commands/<instance_id>/<cmd_id>.json`），请求与响应结构完全一致，且**通道 A 降级到通道 B 时必须复用同一个 `cmd_id`**。

**指令请求 (`ActionEnvelope`)**：
```json
{
  "schema_version": "2.0",
  "cmd_id": "cmd_20261121_103015_91ab4f2c",
  "instance_id": "inst_20261121_101500_7c9e4a1b",
  "action": "lifecycle.drain_shutdown",
  "params": {
    "sync_drive": true,
    "include_queued": false,
    "timeout_minutes": 120
  },
  "issued_at": "2026-11-21T02:30:15Z",
  "ttl_sec": 300,
  "expires_at": "2026-11-21T02:35:15Z",
  "issued_by": "local_web_ui",
  "sig": "hmac-sha256:<hex_digest>"
}
```

- **HMAC 签名规则（双通道统一）**：将去除 `sig` 字段后的 `ActionEnvelope` 序列化为规范 JSON（`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`），使用 `_colab_monitor/secrets/<instance_id>.json` 中的 `api_key` 计算 `HMAC-SHA256`，前缀为 `hmac-sha256:`。Colab 端必须使用 `hmac.compare_digest` 恒定时间比对。

**执行回执 (`ActionAck`)**：
```json
{
  "schema_version": "2.0",
  "cmd_id": "cmd_20261121_103015_91ab4f2c",
  "instance_id": "inst_20261121_101500_7c9e4a1b",
  "action": "lifecycle.drain_shutdown",
  "status": "succeeded",
  "accepted_at": "2026-11-21T02:30:16Z",
  "finished_at": "2026-11-21T02:30:16Z",
  "message": "已进入排空关机模式：待当前 1 项运行中任务完成并同步云盘后将自动释放实例。",
  "data": {
    "drain_requested": true,
    "remaining_running_jobs": 1
  }
}
```

- **`ActionAck.status` 完整枚举**：
  - `queued`（本地已写入云盘信箱等待消费）
  - `accepted` / `running`（Agent 已受理，异步长动作执行中，如 `rebuild_tunnel`、`restart comfyui`、`cleanup_disk`）
  - `succeeded` / `failed`（执行终态）
  - `rejected_expired`（超时失效）
  - `rejected_unauthorized`（Bearer 鉴权或 HMAC 验签失败）
  - `rejected_invalid`（动作不在白名单、参数非法或命中受保护目标）
  - `timed_out`（本地等待云盘回执超过默认 `120s` 后标记，不自动重试）

> **双通道统一防误杀与幂等校验（通道 A 与通道 B 强制共用同一执行引擎与带锁去重表）**：
> 1. **身份与验签**：通道 A 校验 `Authorization: Bearer <api_key>`（`hmac.compare_digest`），双通道均校验 `envelope.sig` HMAC；不通过则返回 `401` / 写入 `rejected_unauthorized`。
> 2. **实例匹配**：`envelope.instance_id != current_instance_id` 时拒绝执行（通道 A 返回 `409`；通道 B 直接移入清理，绝不跨实例误执行）。
> 3. **有效期与时钟容差**：若 `now_utc > expires_at + 60s` 或 `issued_at < instance.started_at - 60s`（默认 `ttl_sec = 300`），拒绝执行并返回 `410` / 写入 `rejected_expired` 并删除命令文件，彻底杜绝上次会话遗留指令误关新机。
> 4. **双通道共享幂等（At-Most-Once）**：同一 `cmd_id` 若已在内存/本地持久化去重表中存在，直接返回该 `cmd_id` 的已有 `ActionAck`（不重复执行副作用），确保通道 A 超时降级通道 B 绝对安全。

> **Legacy 实例路由降级约束**：尚未接入 `colab_agent.py` 的旧版 Legacy 实例无 `_colab_monitor/secrets/` 与 `/agent/v1/actions` 能力；路由器对其仅支持将 `lifecycle.keepalive` 映射至旧版 `/v1/keepalive`（或降级路由）、将 `lifecycle.shutdown_now` 映射至旧版 `/v1/shutdown`，其余 17 项 v2 动作一律直接返回 HTTP `501`（`status="rejected_unsupported_legacy"`），绝不尝试写入云盘信箱。

### 3.4 Colab 端通道 A HTTP API 契约 (`/agent/v1/*`)

当实例启用 FastAPI 网关时，`agent.bind_fastapi(app, ...)` 挂载以下路由。除 `/agent/v1/health` 外，全部要求 `Authorization: Bearer <api_key>`（只读接口不重置 `STATE["last_active"]`）：

| 方法与路径 | 鉴权与只读属性 | 查询参数 / 请求体 | 响应体与错误码 |
|---|---|---|---|
| `GET /agent/v1/health` | 公开只读（不重置空闲计时） | 无 | `200`: `{"ok": true, "schema_version": "2.0", "instance_id": "...", "state": "...", "idle_seconds": ..., "idle_timeout_minutes": ..., "active_jobs": ...}` |
| `GET /agent/v1/metrics` | Bearer 只读（不重置空闲计时） | 无 | `200`: 与 §3.1 一致的完整实例快照 JSON（不含 `api_key`）；`401` 鉴权失败 |
| `GET /agent/v1/timeseries` | Bearer 只读 | `window=15m\|1h`（默认 `1h`） | `200`: `{"instance_id": "...", "samples": [...]}`（来自内存 1h 环形缓冲） |
| `GET /agent/v1/processes` | Bearer 只读 | `limit=20`（最大 `50`） | `200`: `{"instance_id": "...", "processes": [{"pid", "ppid", "create_time", "name", "cmdline", "rss_mib", "cpu_pct", "killable", "protected_reason"}]}` |
| `GET /agent/v1/logs` | Bearer 只读 | `source=comfyui\|cloudflared\|gateway\|agent&tail=200`（上限 `2000` 行且 `<= 512 KiB`） | `200`: `{"source": "...", "lines": [...], "truncated": bool, "redacted": true}`；`400` 非白名单 `source` |
| `POST /agent/v1/actions` | Bearer + HMAC 写接口 | 请求体为 §3.3 `ActionEnvelope` | `200`（同步完成返回终态 `ActionAck`）或 `202`（异步长动作返回 `accepted`/`running` `ActionAck`）；`401` / `403` / `409` / `410` / `422` |
| `GET /agent/v1/actions/<cmd_id>` | Bearer 只读 | 无 | `200`: 最新 `ActionAck`；`404` 未知 `cmd_id` |

---

## 4. Colab 远程管理动作全集（枚举白名单）

所有远程写操作严格限定在以下 **5 大类 19 项枚举动作**内；只读扩展能力独立声明为 3 项 `read.*` 键（`read.processes` 对应 `/agent/v1/processes`、`read.logs` 对应 `/agent/v1/logs`、`read.timeseries` 对应 `/agent/v1/timeseries`）。前端根据实例心跳中的 `capabilities` 字段决定按钮与面板显隐及可用态：

### 4.1 生命周期与算力止损 (`lifecycle.*`, 7 项)

| 动作枚举 (`action`) | 参数 (`params`) | 执行语义与副作用 | 确认等级 |
|---|---|---|---|
| `lifecycle.keepalive` | `{}` | 立即重置 `STATE["last_active"] = now`，零显存开销 | 单击即发 |
| `lifecycle.set_keepalive_lease` | `{"duration_minutes": int}` (`0` 或 `15..240`) | 设置保活租约到期时间 `keepalive_lease_until`；租约期内空闲检查器暂停释放倒计时，到期自动恢复正常空闲计时（传 `0` 取消租约） | 单击选择 |
| `lifecycle.set_idle_policy` | `{"idle_timeout_minutes": 0..360, "auto_shutdown_after_batch": bool}` | 在线热更新空闲释放分钟数与脱机/批跑完成后的自动关机开关，立即写入心跳；**`idle_timeout_minutes = 0` 表示禁用空闲自动释放（绝非立即关机）** | 单击保存 |
| `lifecycle.set_deadline` | `{"shutdown_after_minutes": int \| null, "mode": "drain" \| "immediate"}` | 设置单次会话最长运行死线（防忘关机烧算力），到点触发排空或立即关机 | 二次确认 |
| `lifecycle.drain_shutdown` | `{"sync_drive": true, "include_queued": false, "timeout_minutes": 120}` | **跑完即停**：置 `drain_requested=true`，拒绝新请求；`include_queued=false` 时跑完当前在途项即停，`true` 时跑完当前队列全部待办项后停；`timeout_minutes`（可选 `5..360`，默认 `120`）为最长等待兜底超时；停机前执行云盘落盘校验 + 退机序列（见 §8.5） | 二次确认 |
| `lifecycle.cancel_drain` | `{}` | 在实例尚未真正调用 `runtime.unassign()` 前撤回排空关机状态，恢复正常接单 | 单击即发 |
| `lifecycle.shutdown_now` | `{"force": bool}` | 写入 `state="shutting_down"` 心跳与 ACK，执行刷盘保护窗口后调用 `runtime.unassign()` 释放 Colab VM | 强二次确认（展示在途任务数） |

### 4.2 隧道与网关网络管理 (`network.*`, 3 项)

| 动作枚举 (`action`) | 参数 (`params`) | 执行语义与副作用 | 确认等级 |
|---|---|---|---|
| `network.rebuild_tunnel` | `{}` | 异步执行（立即返回 `accepted`）：终止现有 `cloudflared` 进程，重新拉起 `trycloudflare.com` 隧道，解析新 URL 更新至 `_colab_monitor/instances/<id>.json` 及兼容 `active_endpoint.json`（支持由云盘通道触发） | 单击/确认 |
| `network.start_gateway` | `{"port": 8000, "with_tunnel": true}` | 在原本处于脱机批跑模式的实例上动态拉起 FastAPI 网关与 `cloudflared` 隧道。**强制落盘顺序**：① 确认或生成 `api_key` 并写入 `_colab_monitor/secrets/<id>.json`（等待写完成）；② 启动 FastAPI 与隧道；③ 更新 `instances/<id>.json` 的 `tunnel.base_url` 与 ACK（含 `secret_ready: true`），确保本地先拿到密钥再连通道 A | 二次确认 |
| `network.stop_gateway` | `{}` | 关闭公网 `cloudflared` 隧道以收敛暴露面，仅保留云盘带外信箱管控 | 二次确认 |

### 4.3 子服务、进程与显存/磁盘运维 (`ops.*`, 3 项)

| 动作枚举 (`action`) | 参数 (`params`) | 执行语义与副作用 | 确认等级 |
|---|---|---|---|
| `ops.service_control` | `{"service": "comfyui", "op": "restart" \| "start" \| "stop" \| "interrupt" \| "free_vram"}` | `interrupt`: 调 ComfyUI `POST /interrupt` 中止卡死推理；`free_vram`: 仅调用 ComfyUI `POST /free` (`unload_models=true, free_memory=true`，设 10s 超时；若正在推理返回明确提示，不依赖 Agent 进程无效的 `torch.cuda.empty_cache()`)；`restart`: 异步重启后台 `127.0.0.1:8188` 进程 | `interrupt`/`free_vram` 轻确认；`restart`/`stop` 二次确认 |
| `ops.kill_process` | `{"pid": int, "create_time": float, "signal": "TERM" \| "KILL"}` | 核对 `(pid, create_time)` 一致后发送信号。**采用白名单优先 + 核心进程硬保护**：仅允许终止已注册托管服务进程或当前业务负载子进程树内的进程（如 `comfyui`、`ffmpeg`、`python` 子 worker）；硬性禁止终止 PID 1、`ipykernel_launcher`、`colab_agent` 宿主进程、`drive`/`fusermount`、`colab-fileshim`、`dap_multiplexer`、`node`、`tini`、`sshd`（越权返回 `403`） | 强二次确认 |
| `ops.cleanup_disk` | `{"targets": list[str], "dry_run": bool}` | `targets` 仅限严格枚举子目录：`comfy_temp`（`ComfyUI/temp` 与未归档缓存）、`pip_cache`（`/root/.cache/pip`）、`hf_incomplete`（`~/.cache/huggingface` 内 `*.incomplete` 分片）、`agent_scratch`（仅 `/tmp/colab_agent_*` 及注册的项目临时子目录；**禁止泛扫 `/tmp/*`** 避免误删 Colab/Jupyter 运行时 socket）。全部目标先 `realpath` 校验，**硬性禁止触碰 `/content/drive` 与正式 `outputs/`** | 先 `dry_run` 预览，再二次确认执行 |

### 4.4 任务队列与通用长循环管控 (`workload.*`, 4 项)

| 动作枚举 (`action`) | 参数 (`params`) | 执行语义与副作用 | 确认等级 |
|---|---|---|---|
| `workload.pause_queue` | `{}` | 挂起队列调度器或长任务 `agent.checkpoint()`：当前正在生成的单张图/单章音频完成后暂停拉取下一项 | 单击即发 |
| `workload.resume_queue` | `{}` | 解除挂起状态，继续执行后续待办任务 | 单击即发 |
| `workload.reload_drive_queue` | `{"queue_file": "offline_batch_queue.json"}` | 重新读取项目云盘目录下的队列 JSON，按 `task_id` / `output_stem` 幂等过滤已完成项后追加至待跑队列。**路径校验**：`queue_file` 必须匹配 `^[A-Za-z0-9_.-]+\.json$`（禁止 `/`、`\`、`..`），解析后 `is_relative_to(project_drive_dir)`，非法返回 `422` | 单击即发 |
| `workload.queue_mutate` | `{"op": "cancel_job" \| "prioritize_job" \| "retry_failed" \| "clear_pending", "job_id": str \| null}` | 取消指定待跑任务、置顶待跑任务、重试失败任务，或清空全部未开始排队项 | `clear_pending` 需二次确认 |

### 4.5 云盘落盘校验与日志快照 (`storage.*` & `logs.*`, 2 项)

| 动作枚举 (`action`) | 参数 (`params`) | 执行语义与副作用 | 确认等级 |
|---|---|---|---|
| `storage.flush_and_verify` | `{}` | OAuth 模式通过 Drive API 校验探针与产物计数；FUSE 模式执行 `os.sync()` + 探针写入回读校验，对比本地与云盘产物计数并返回完整性报告 | 单击即发 |
| `logs.snapshot_to_drive` | `{"sources": ["comfyui", "cloudflared", "gateway", "agent"]}` | 读取白名单日志尾部（每源最多 2000 行且 `<= 512 KiB`，全量执行密钥脱敏）与最近硬件快照，打包写入 `_colab_monitor/snapshots/<instance_id>_<ts>.json` | 单击即发 |

---

## 5. Colab 端通用接入 SDK 规范 (`agent/colab_agent.py`)

### 5.1 零依赖原则与 `DriveBackend` 适配协议
- **零第三方依赖采集**：`colab_agent.py` 内置采集器仅依赖 Python 标准库、`nvidia-smi --query-gpu=... --format=csv,noheader,nounits` 与 Linux `/proc`（`/proc/meminfo`、`/proc/stat`、`/proc/loadavg`、`/proc/<pid>/stat`、`/sys/fs/cgroup`），无需安装 `psutil`/`pynvml`。
- **云盘后端解耦（解决现有 `DriveSyncManager` 接口不匹配问题）**：
  - `colab_agent.py` 内置统一的 `DriveBackend` 抽象协议（`ensure_dir`、`put_json`、`get_json`、`list_json`、`delete_file`），并提供两个内置实现：
    1. `FuseDriveBackend(monitor_root: Path)`：用于已挂载 `/content/drive/MyDrive/<DRIVE_WORKSPACE>/_colab_monitor` 的笔记本。
    2. `OAuthDriveBackend(service_factory, monitor_folder_path="<DRIVE_WORKSPACE>/_colab_monitor")`：可直接接收现有 `DriveSyncManager._service`（或独立 OAuth credentials），自行解析并缓存 `_colab_monitor/` 及其子目录与文件的 `fileId`，**不依赖项目目录锁定句柄，也无需重写现有笔记本的 `sync_file()`**。

### 5.2 方式一：嵌入已有 FastAPI 网关（适用于 H3 系列笔记本）
```python
from colab_agent import ColabAgent, OAuthDriveBackend

drive_backend = OAuthDriveBackend(service_factory=GDRIVE._service)  # 或 FuseDriveBackend(...)
agent = ColabAgent(
    project="sample_studio",
    label="Sample Studio · G4/L4",
    drive_backend=drive_backend,
    project_drive_dir="<DRIVE_WORKSPACE>/sample_studio",
    legacy_ep_file=local_ep_file,      # 同步维护旧版 active_endpoint.json（附加 instance_id 便于去重）
)
agent.register_service("comfyui", check_fn=is_comfy_alive, restart_fn=restart_comfy, log_path="/content/comfyui_server.log")
agent.bind_fastapi(app, api_key_getter=lambda: API_KEY, state_ref=STATE, jobs_ref=JOBS)
agent.start_background_workers()
```

### 5.3 方式二：无网关通用循环接入（适用于 Kokoro / Whisper / 脱机批跑）
```python
from colab_agent import ColabAgent, FuseDriveBackend

agent = ColabAgent(
    project="kokoro_audiobook",
    label="有声书批量合成示例",
    drive_backend=FuseDriveBackend("/content/drive/MyDrive/<DRIVE_WORKSPACE>/_colab_monitor"),
)
agent.start_background_workers()  # 开启 30s 云盘心跳 + 15s 云盘指令监听

for idx, chapter in enumerate(agent.track(chapters, stage="章节语音合成")):
    synthesize_chapter(chapter)
    # agent.track 在每项结束时自动：
    # 1) 更新进度 (done, total, ETA)
    # 2) 若收到 workload.pause_queue 则在此阻塞等待恢复（维持心跳；捕获 KeyboardInterrupt 安全退出而不误触发关机）
    # 3) 若收到 lifecycle.drain_shutdown 则按 §8.5 刷盘并安全调用 runtime.unassign()
```

---

## 6. 本地服务端接口、持久化与 CLI 规范

### 6.1 HTTP 路由清单（默认仅绑定 `127.0.0.1:8765`）

**全局安全前置（v1 与 v2 全部路由统一执行）**：
- 默认仅允许绑定回环地址（`127.0.0.1` / `localhost` / `::1`）；非回环 `--host` 未显式传 `--allow-remote` 时拒绝启动（退出码 `2`）。
- **全部 `GET` / `POST` 路由（含 v1 兼容路由）**均强制校验 `Host` 与（如存在）`Origin` 属于合法回环主机，防 DNS rebinding 与跨站绕过（非法返回 `403`）。

| 方法与路径 | 用途说明 | 安全与鉴权要求 |
|---|---|---|
| `GET /api/snapshot` | **兼容保留 v1**：返回默认/选中实例的 v1 格式快照（按 v2→v1 映射表转换，如 `hardware.gpu.power_draw_w` → `metrics.gpu.power_w`） | 统一 `Host`/`Origin` 校验，严格只读，零密钥外泄 |
| `POST /api/keepalive` / `POST /api/shutdown` | **兼容保留 v1**：转发至统一动作路由器（v2 实例走双通道，Legacy 实例走旧逻辑） | 统一 `Host`/`Origin` 校验 + `X-H3-Action` 头 |
| `GET /api/v2/instances` | 返回全部已发现实例（已完成 v2 与 Legacy 去重）、统一在线状态枚举与全局多实例 `uptime_summary` | 严格只读 |
| `GET /api/v2/instances/<id>/snapshot` | 获取指定实例完整快照、近期产物与在途异步指令列表 | 严格只读，全量脱敏 |
| `GET /api/v2/instances/<id>/timeseries` | 查询指定时间窗（`15m` / `1h` / `6h` / `24h`）的 GPU/VRAM/RAM/Disk/Temp 时序点 | 严格只读 |
| `GET /api/v2/instances/<id>/processes` | 拉取实例受控进程快照（在线隧道模式实时返回，云盘模式返回心跳附带 Top 5） | 严格只读 |
| `GET /api/v2/instances/<id>/logs` | 查询白名单日志（`source=comfyui\|cloudflared\|gateway\|agent&tail=200`，上限 2000 行 / 512 KiB） | 白名单键校验，禁止传路径，全量脱敏 |
| `POST /api/v2/instances/<id>/actions` | 向指定实例下发枚举管理动作（通道 A 优先，失败/超时复用同 `cmd_id` 降级通道 B） | 统一 `Host`/`Origin` + `X-Colab-Action: <action>`（须与请求体 `action` 一致） |
| `GET /api/v2/commands/<cmd_id>` | 轮询某条指令的最新投递通道与 ACK 状态 | 严格只读 |
| `POST /api/v2/fleet/actions` | 多实例批量操作（`fleet.drain_all` / `fleet.shutdown_all`）。请求体：`{"action": "...", "instance_ids": [...] \| null, "params": {...}}`；响应返回逐实例结果数组 `{"ok": bool, "results": [{"instance_id", "cmd_id", "channel", "status", "message"}]}`（明确区分部分成功/部分入队/部分失败） | 统一 `Host`/`Origin` + `X-Colab-Action: <action>` + 前端强二次确认（列出每台受影响实例与活跃任务数） |

**v1 兼容路由的默认实例选择与字段映射规则**：
- **默认实例选择顺序**：若请求携带 `?instance_id=<id>` 则精确命中该实例；未传时按确定性优先级选取：① 与 `--ep` 匹配的 Legacy/关联 v2 实例；② 最近活跃的在线实例（`ONLINE_LIVE` > `DRAINING` > `ONLINE_DRIVE_ONLY`）；③ 最近更新的历史实例。
- **v2 → v1 `/api/snapshot` 兼容映射表**：

| v1 `/api/snapshot` 字段 | v2 实例快照来源 | 说明 |
|---|---|---|
| `online` | `liveness_state in ("ONLINE_LIVE", "ONLINE_DRIVE_ONLY", "DRAINING")` | 任一通道在线即为 `true` |
| `health.idle_seconds` / `idle_timeout_minutes` / `active_jobs` | `lifecycle.idle_seconds` / `lifecycle.idle_timeout_minutes` / `workload.running` | 字段语义完全兼容 |
| `metrics.gpu.name` / `util_gpu_pct` / `vram_*` / `temp_c` | `hardware.gpu.*` | 同名直出 |
| `metrics.gpu.power_w` | `hardware.gpu.power_draw_w` | 字段名兼容转换 |
| `metrics.ram.*` / `metrics.disk.*` | `hardware.ram.*` / `hardware.disk.*` | 透传 `used_gib` / `total_gib` / `free_gib` / `pct` |
| `metrics.jobs.{running,queued,succeeded,failed}` | `workload.{running,queued,succeeded,failed}` | 同名直出 |
| `uptime_summary` | 多实例聚合后的全局 `uptime_summary` | 保留原 4 周期结构 |

### 6.2 后台调度器、多实例发现去重与持久化设计

1. **本地后台调度线程（Phase 1 起启用，`control/collector.py`）**：
   - 与浏览器轮询解耦，避免关掉浏览器标签后 ACK 追踪、开机时长、时序采样和告警停摆。
   - 常规每 `15s` 刷新实例发现与在线快照；存在在途云盘指令时提频至 `5s` 轮询 `acks/`；默认每 `1h` 执行一次云盘 GC。
2. **Legacy 发现与同机去重（`control/registry.py`）**：
   - Legacy 扫描源：显式 `--ep` 参数 + 默认项目目录列表（`sample_studio`、`sample_batch_project`，可通过 `--scan-root` 或本地配置文件扩展）。
   - **去重规则**：若某 v2 实例的 `tunnel.base_url` 或 `legacy_ep_hint` 与某个 `active_endpoint.json` 匹配（或 `active_endpoint.json` 内含匹配的 `instance_id`），两者合并为同一个 v2 实例条目，严禁顶栏出现重复实例卡片。
3. **多实例并发 `uptime_ledger.json` 升级**：
   - 改造既有 `UptimeTracker`：取消全局单活跃段假设（不再用 `_deactivate_all_locked()` 误关其他在线实例），改为**按 `instance_id`（Legacy 用 `session_key`）独立维护各自活跃段**，支持多台 Colab（如 1 台 L4 + 1 台 CPU Only）同时在线准确累加，同时保持 `uptime_ledger.json` 字段向后兼容。
   - **存储位置升级**：v2 主账本存放于 `<LOCAL_STATE_DIR>/uptime_ledger.json`（彻底消除云盘同步冲突与覆盖风险）；首次启动若 `<LOCAL_STATE_DIR>/uptime_ledger.json` 不存在且旧版 `--ep` 同级目录存在 `uptime_ledger.json`，自动执行一次性只读导入迁移。
4. **纯本地 SQLite 存储 (`<LOCAL_STATE_DIR>/monitor_state.sqlite3`)**：
   - 强制开启 WAL 模式并启动校验路径不在 `CloudStorage` / `GoogleDrive` / `MyDrive` 下。
   - `metrics_samples(instance_id, sampled_at_ts, gpu_util, vram_used_mib, ram_used_gib, disk_free_gib, temp_c, power_w, active_jobs)`：保留 14 天明细，超期降采样归档。
   - `command_history(cmd_id PRIMARY KEY, instance_id, action, params_json, channel, status, issued_at, acked_at, message)`：驱动前端异步进度条与历史审计。
   - `alert_events(id, instance_id, rule_id, severity, message, triggered_at, resolved_at, notified_channels)`：支持空闲 `< 5min`、VRAM/RAM `> 92%`、磁盘 `< 5 GiB`、Drive 掉挂载、心跳失联告警，内置 Cooldown 防刷屏。
5. **云盘 `_colab_monitor/` GC 清理策略**：
   - `commands/`：超 `24h` 清理；Agent 启动时立即清理早于本次 `started_at` 的本实例残留命令。
   - `acks/`：超 `7d` 清理。
   - `instances/`：持续 `OFFLINE` 超 `7d` 清理（已落入本地 SQLite 与 `uptime_ledger.json`）。
   - `secrets/`：对应实例持续 `OFFLINE` 或 `terminated` 超 `24h` 后自动删除，收敛密钥暴露周期。
   - `snapshots/`：保留最近 `30` 份或 `14d`。

### 6.3 CLI v2 子命令规范 (`h3_cli_monitor.py`)

保留既有全部 v1 参数（`--watch`、`--json`、`--web`、`--keepalive`、`--shutdown`、`-y/--yes`、`--ep`、`--host`、`--port`、`--allow-remote`），并扩展 v2 多实例管控命令：

- `python3 h3_cli_monitor.py instances [--json]`：列出所有已发现实例（含 `instance_id`、状态枚举、项目、GPU、通道与进度）。
- `python3 h3_cli_monitor.py status --instance <id> [--watch N] [--json]`：查看指定实例实时状态。
- `python3 h3_cli_monitor.py action --instance <id> <action> [--params '{"..."}'] [--wait 120] [--yes] [--json]`：通过本地路由或直连下发枚举动作；高危动作（`shutdown_now`、`drain_shutdown`、`kill_process`、`cleanup_disk`、`clear_pending`）在交互终端需二次确认，非交互环境未传 `--yes` 返回退出码 `1` 拒绝执行。
- `python3 h3_cli_monitor.py logs --instance <id> --source <comfyui|cloudflared|gateway|agent> [--tail 200]`：拉取脱敏后的白名单日志。

### 6.4 笔记本启动器与安全注入器（方案 A，`control/injector.py` & `control/launcher.py`）

为解决普通 Colab 无法无浏览器冷启及多笔记本手动改代码繁琐的问题，新增合规的「一键唤起 + 幂等注入」能力：

- **单一共享探针**：执行写入注入时自动将最新 `agent/colab_agent.py` 同步至 `_colab_monitor/colab_agent.py`，全部笔记本统一按单一路径加载。
- **自动扫描与安全边界**：
  - `GET /api/v2/notebooks?include_backups=0|1`：扫描本地挂载云盘中的 `Colab Notebooks/`、`sample_studio/colab/` 及 `--notebook-root` 目录下的 `.ipynb`（默认隐藏 `_backup` 与冲突副本 `(1)`），返回模式检测（`oauth_silent` / `fuse_mounted` / `standalone_mount`）、注入状态与已绑定链接。
  - `POST /api/v2/notebooks/inject`（`X-Colab-Action: notebooks.inject`）：支持 `dry_run=true` 预览插入位置与单元源码；`dry_run=false` 时先在 `<LOCAL_STATE_DIR>/notebook_backups/` 备份原文件，再以 `metadata.colab_monitor_injected` 标记幂等插入或原地升级独立单元（绝不改动原业务单元）。
  - `POST /api/v2/notebooks/remove`（`X-Colab-Action: notebooks.remove`）：备份后干净移除所有带标记的注入单元，一键还原。
  - `POST /api/v2/notebooks/bind-url`（`X-Colab-Action: notebooks.bind_url`）：将绑定关系持久化在纯本地 `<LOCAL_STATE_DIR>/notebooks_launcher.json`；严格限制 URL 协议为 `https` 且域名仅限 `colab.research.google.com` 或 `drive.google.com`（非法返回 `422`）。
  - `POST /api/v2/notebooks/launch`（`X-Colab-Action: notebooks.launch`）：通过本机默认浏览器打开已绑定 Colab 链接（未绑定时打开 Google Drive 文件名搜索页），由用户在已登录浏览器中按 `⌘+F9` 全部运行，完全零风控。
  - 所有接口均强制校验目标 `.ipynb` 位于允许扫描根目录内（越界返回 `403`）。

---

## 7. 前端信息架构与交互规范（遵循 Sage v1.1.0）

所有新增界面严格遵守 [ADOPTION_CONTRACT.md](./ADOPTION_CONTRACT.md) 的全部视觉与交互门禁（默认 `sage` + 可选 `ash-marble`、本地 `.woff2` + `OFL.txt`、纯安全 DOM API 零 `innerHTML`、单飞轮询、数据源切换防竞态、`--critical-hover` `>= 4.5:1`、弹窗关闭焦点恢复、缺失能力显示 `—` 并禁用对应控件）：

1. **多实例切换顶栏（`.instance-bar`，位于 `.masthead` 下方，非吸顶）**：
   - 仅 1 个实例时保持紧凑单行摘要；多个实例时展示横向可滚动实例胶囊选项卡（含状态点、项目简称、GPU 型号、进度百分比）。
   - 右侧仅当 `>= 2` 台实例在线时显示「全部排空关机」按钮（点击弹出列出全部实例与活跃任务数的强确认 `<dialog>`）。
2. **精简吸顶工具栏（`.filters`）与策略调节分层**：
   - 为避免小屏（`<= 980px` / `390px`）吸顶栏膨胀遮挡内容，`.filters` 仅保留高频项：数据源（实时/演示）、轮询间隔、归档条数、立即刷新、**「手动保活」**、**「跑完即停（Drain）」**、**「立即关机」**与显示设置。
   - 低频策略控件（保活租约 `30m/1h/2h`、空闲阈值 `15m..120m`、会话死线设置）收归至**生命周期策略卡片或原生 `<dialog>`**。
3. **双通道状态横幅与异步指令进度条（`.status-banner`）**：
   - 处于 `ONLINE_DRIVE_ONLY` 时提示“实时隧道不可达，已切换至云盘带外通道（约 15–60s 生效）”，右侧提供一键「通过云盘重建隧道」。
   - 存在在途云盘指令时，展示非阻塞式进度条（`已写入云盘信箱 -> 等待实例响应 (12s) -> 已完成/超时`）。
4. **服务器深度运维卡片组（新增 12 列网格卡片）**：
   - **子服务、策略与存储运维卡（`span-6`）**：展示生命周期策略设置、`ComfyUI` / `Cloudflared` / `Gateway` / `Google Drive` 状态及快捷按钮（重启 ComfyUI、中断生图、释放显存、云盘同步校验、磁盘 `Dry-Run` 预览清理弹窗）。
   - **队列与长任务编排卡（`span-6`）**：展示当前批跑/章节合成进度条、ETA 及「暂停 / 恢复 / 热重载云盘队列 / 失败重试 / 清空排队」控制按钮。
   - **日志与受控进程检视面板（`span-12` 可折叠或 `<dialog>`）**：支持白名单日志源切换、关键词过滤、暂停滚底、保存快照至云盘；进程页签展示 Top 进程与受控终止按钮（仅 `killable=true` 项可点，受保护进程置灰并标明原因）。
5. **v2 演示样本模式（Demo Mode）**：
   - 演示模式内置 2 台虚拟实例（1 台 `ONLINE_LIVE` NVIDIA L4 在线网关 + 1 台 `ONLINE_DRIVE_ONLY` Tesla T4 脱机队列），点击保活/租约/Drain/重建隧道/磁盘 `dry_run` 均在前端本地模拟状态流转与异步回执动画，**绝不触碰真实云盘或真实 Colab**，且切入时严格递增 `requestSeq` 并中止在途请求。

---

## 8. 安全与防故障设计清单

### 8.1 全量密钥脱敏与零泄露
- `active_endpoint.json` 与 `_colab_monitor/secrets/<id>.json` 中的 `api_key` 仅驻留本地 Python 进程内存。
- 所有 `/api/*` 响应、白名单日志查询与日志快照均执行**双重脱敏**：① 对所有已知实例 `api_key` 原文精确替换；② 正则脱敏 `Bearer\s+\S+`、`sk-[A-Za-z0-9_-]+`、`ya29\.[A-Za-z0-9._-]+`、`hf_[A-Za-z0-9]+`、`AIza[A-Za-z0-9_-]+` 与 JSON 中的 `"refresh_token"`/`"access_token"` 字段。
- 单次日志接口返回强制封顶 `2000` 行且 `<= 512 KiB`。

### 8.2 本地服务同源与暴露面防护
- v1 与 v2 所有路由统一强制校验 `Host`/`Origin` 为回环主机；非回环绑定须显式传 `--allow-remote`；所有写接口强制校验 `X-H3-Action` / `X-Colab-Action` 且与请求体 `action` 一致。

### 8.3 双通道防重放与 HMAC 完整性校验
- 通道 A（Bearer + HMAC）与通道 B（HMAC）共用同一 `instance_id` + `expires_at`（±60s 容差）+ `cmd_id` 去重表；降级切换通道复用同一 `cmd_id`；`secrets/` 依赖 Google 账号隔离并在实例离线 24h 后自动清理。

### 8.4 Google Drive I/O 韧性
- OAuth 模式缓存 `fileId` 走 `files().update` 原地更新并去重同名副本；FUSE/Desktop 模式忽略 `._tmp_*` 临时文件并带 `os.replace` 重试回退；SQLite 与主开机账本严格存放于纯本地非云盘目录。

### 8.5 安全退机与刷盘保护序列
- 执行 `shutdown_now` 或 `drain_shutdown` 最终停机时按顺序执行：① 写 `state="shutting_down"` 心跳与 ACK；② 若为 OAuth 模式，等待所有在途上传 API 返回 `200`；若为 FUSE 模式，执行 `os.sync()` 并等待探针回读/宽限窗口（默认 `5s`）；③ 延迟 `2s` 后调用 `runtime.unassign()`，防止截断最后产物或最后一条 ACK。

---

## 9. 分阶段落地路线图与验收标准

### 阶段划分

- **Phase 0（基线合并，前置准备）**：
  - 统一 `colab-web-monitor`（v1.2，10/10 测试通过）与 `h3-web-monitor` 基线，确认双目录不再分叉。

- **Phase 1（M1 · 核心管控闭环，最优先落地）**：
  1. 实现 `agent/colab_agent.py`（零依赖硬件采集、`FuseDriveBackend` + `OAuthDriveBackend`、30s 心跳、15s 云盘命令消费、`/agent/v1/*` 路由、双通道统一 HMAC 验签与共享 `cmd_id` 幂等表）。
  2. 实现 `control/registry.py`、`control/router.py` 与 `control/collector.py`（多实例发现 + Legacy 去重、隧道优先同 `cmd_id` 降级云盘、独立后台采样/ACK 追踪/云盘 GC、多实例并发 `UptimeTracker`）。
  3. 落地首批高价值管理动作：`keepalive`、`set_keepalive_lease`、`set_idle_policy`、`set_deadline`、`drain_shutdown` / `cancel_drain`、`shutdown_now`、`rebuild_tunnel`、`service_control(comfyui)`、`pause_queue` / `resume_queue`、`flush_and_verify`、白名单脱敏 `logs` 查看。
  4. 升级 Web UI 与 CLI v2：多实例切换栏、精简工具栏 + 策略卡片、云盘异步指令进度条、子服务与日志检视面板、v2 Demo 模式。
  5. 先改造 `sample_studio/colab/sample_gateway_colab.ipynb` 与 1 个 Kokoro 笔记本完成端到端实测。

- **Phase 2（M2 · 深度资源运维与队列细粒度编排）**：
  1. 落地 `ops.cleanup_disk`（含 `dry_run` 预览与 `realpath` 边界锁）、`ops.kill_process`（白名单托管进程树优先 + 核心进程硬保护）、`network.start_gateway`（先落 `secrets/` 再启隧道）、`network.stop_gateway`、`workload.reload_drive_queue`（文件名正则校验）、`workload.queue_mutate` 与 `POST /api/v2/fleet/actions`。
  2. 推广接入 `sample_batch_project` 脱机批跑与其余 Whisper / Kokoro 笔记本。

- **Phase 3（M3 · 纯本地历史时序曲线与后台常驻告警）**：
  1. 接入 `control/storage.py`（纯本地非云盘目录 SQLite 14 天时序库 + Colab 端 1h 环形缓冲补齐断连空窗）并在前端绘制 Sage 规范时序折线图。
  2. 接入 `control/notifier.py`（macOS 系统通知 + Bark / ntfy / Telegram 可选推送）及 macOS `launchd` 一键安装后台常驻脚本。

### 验收测试门禁（Acceptance Gates）
- [x] **向后兼容门禁**：既有 `tests/test_dashboard.py` 全部 **10 项测试**（Sage Token、Ash Marble 与 `--critical-hover` 对比度、`OFL.txt`、HTML 语义、无 `innerHTML`、`Host`/`Origin` 与 `--allow-remote`、单飞轮询/竞态/焦点恢复、旧版 `/api/snapshot`、`/api/keepalive`、`/api/shutdown`）100% 通过。
- [x] **双通道降级与幂等门禁**：`tests/test_control_plane.py` 模拟隧道中断时，`POST /api/v2/instances/<id>/actions` 在 `< 3s` 内**复用同一 `cmd_id`** 切换写入云盘 `commands/`；重复投递同一 `cmd_id` 时 Fake Agent 保证仅执行一次并返回已有 ACK。
- [x] **多实例发现、去重与 Uptime 并发门禁**：同时运行 2 台在线实例（L4 + CPU Only）时，各自在线时长独立累加互不干扰；v2 实例与同期 `active_endpoint.json` 自动合并去重不出现重复卡片。
- [x] **安全与路径边界门禁**：自动化验证（1）过期指令（`expires_at` 超容差）被 `rejected_expired` 拒绝；（2）跨 `instance_id` 指令被拒绝；（3）无 HMAC 或错误 HMAC 指令被 `rejected_unauthorized` 拒绝；（4）v1 与 v2 非回环 `Host`/`Origin` 及缺少 `X-Colab-Action` 均返回 `403`；（5）终止非白名单或核心保护进程返回 `403`；（6）`queue_file` 含 `../`、非白名单 `logs` source、越界 `cleanup_disk` 目标均被拦截；（7）日志与全部 API 响应零 `api_key` / `Bearer` / `ya29.*` / `hf_*` 明文泄露；（8）SQLite 路径位于云盘目录时启动拒绝。
- [x] **前端语法与静态门禁**：`node --check web/theme-init.js` 与 `node --check web/app.js` 零报错。
