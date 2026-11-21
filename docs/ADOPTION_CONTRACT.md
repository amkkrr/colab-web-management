# MiniMax-H3 Colab L4 监控看板 · Sage 项目适配契约

> 依据 `https://github.com/amkkrr/sage_design_paradigm` (`main` @ `de3fec6`, v1.1.0, 含 `docs/DESIGN_PARADIGM.md`、`docs/ASH_MARBLE.md`、`docs/ADOPTION_TEMPLATE.md`、`docs/audits/AUDIT-0002-modern-web-guidance-2026-11-13.md` 与 `docs/audits/AUDIT-0003-chart-palette-alignment-2026-11-15.md`) 校准编写，并已按当前代码实现逐项核对修订。

## 1. 定位、权威来源与安全边界

- **项目/使用者/主要任务**：MiniMax-H3 Colab L4 推理网关与本地 Google Drive 归档监控看板；供本地开发者实时观测隧道存活、空闲自动释放余量、GPU/RAM/磁盘资源、任务队列、分硬件累计在线时长及历史出图耗时。
- **采用 Sage 版本**：`1.1.0`（默认 `sage` 鼠尾草绿主题，支持切换可选 `ash-marble` 苍白/苍灰主题及 `auto -> light -> dark`）。
- **运行模式**：
  - 实时轮询快照：只读 `GET /health`、`GET /v1/metrics`（单次上游超时 `5s`）+ 本地读取 `outputs/h3_*.json`；默认轮询间隔 `10s`，归档默认读取 `12` 条。
  - 显式写操作：手动保活（Web `POST /api/keepalive` / CLI `--keepalive`）与远程关机（Web `POST /api/shutdown` 经 `<dialog>` 二次确认 / CLI `--shutdown` 交互确认或 `--yes`）。
  - 演示样本模式：显式独立切换的离线预览数据，切入时立即递增 `requestSeq` 并中止在途实时请求，与真实监控严格隔离、绝不冒充真实监控。
- **能力边界**：自动轮询**严格只读**，不会重置 Colab 空闲关机计时器，也不会提交任何推理任务或触发关机；仅当用户主动触发「手动保活」/「一键关机」或执行 CLI `--keepalive` / `--shutdown` 时，服务端才向 Colab 发送对应鉴权操作请求。`api_key` 仅保留在 Python 服务端，不向浏览器下发。
- **威胁模型与安全边界**：
  - **监听假设**：`server.py` 默认仅绑定 `127.0.0.1:8765`，假定仅本机可信进程访问。若尝试绑定非回环地址（如 `--host 0.0.0.0`），服务端默认拒绝启动（退出码 `2`），仅在显式传入 `--allow-remote` 时才允许在受信任内网监听（仍无内置鉴权）。
  - **跨站与 DNS Rebinding 防护**：本地模式下服务端对所有 `GET`/`POST` 校验 `Host` 与（如存在）`Origin` 必须属于回环主机（`127.0.0.1` / `localhost` / `::1`），非回环或非法来源一律返回 `403`；写接口（`/api/keepalive`、`/api/shutdown`）同时强制校验自定义请求头 `X-H3-Action`（缺失或不匹配返回 `403`）。
  - **关机确认边界**：Web 端「一键关机」通过原生 `#shutdown-dialog` 提供二次确认并在存在运行/排队任务时高亮警示；CLI `h3_cli_monitor.py --shutdown` 在交互终端下需输入 `y/yes` 确认，非交互环境下未显式传 `--yes` (`-y`) 则拒绝执行。
  - **凭证与路径隔离**：`/api/snapshot`、`/api/keepalive`、`/api/shutdown` 响应均不含 `api_key`；`/api/output-image` 对文件名施加 `^h3_[A-Za-z0-9_.-]+\.png$` 正则与 `is_relative_to(outputs)` 路径遍历双重校验；静态资源限定于 `web/` 目录内。
- **源码入口**：
  - 采集与 CLI 核心：`h3_cli_monitor.py`
  - 轻量标准库 Web 服务：`server.py`
  - 页面、样式与脚本：`web/index.html`、`web/styles/fonts.css`、`web/styles/base.css`、`web/styles/ash-marble.css`、`web/theme-init.js`、`web/app.js`
  - 本地静态资源：`web/assets/ash-marble.svg`、`web/assets/fonts/*.woff2`（107 个文件，约 4.6 MB）与 `web/assets/fonts/OFL.txt`

## 2. 信息架构与组件

- **页面区块顺序**：
  1. `页头 (.masthead)`：标题 + 当前隧道地址摘要、采样时间与只读声明
  2. `粘性控制栏 (.filters)`：数据源（实时端点 / 演示样本）、轮询间隔（手动 / 3s / 5s / 10s / 30s，默认 `10s`）、归档显示条数（最近 6 条 / 最近 12 条 / 全部，默认 `12`）、立即刷新按钮（`#refresh-now`）、手动保活按钮（`#keepalive-now`）、一键关机按钮（`#shutdown-now` + `#shutdown-dialog` 确认框）+ 右侧显示设置（Sage / Ash Marble、苍白/苍灰、石纹/纯色、主题模式）
  3. `状态与口径说明 (.display-status + .data-scope)`：显示模式回退状态、端点来源文件、`updated_at` 与时区说明
  4. `连接/采集边界提示 (.status-banner)`：仅当实例离线、关机释放中或 `/v1/metrics` 返回 404/错误时出现，明示原因、局部缺失边界，并提供「刷新检测/重试」与「查看演示样本」按钮
  5. `5 格 KPI 指标区 (.metrics)`：
     - 主指标（`.kpi--primary`）：距自动释放剩余时间（分钟）；离线或无超时配置时显示 `—`
     - 次指标 2：GPU 核心利用率（整数 `%`）
     - 次指标 3：GPU 显存占用率（`%`，1 位小数），辅助行展示精确 MiB 分子/分母
     - 次指标 4：系统内存占用率（`%`，1 位小数），辅助行展示 RAM GiB 与磁盘剩余/总 GiB
     - 次指标 5：当前网关进程已完成任务（`jobs.succeeded`，Colab 重启后归零），辅助行展示运行中/排队/失败计数
  6. `12 列卡片网格 (.grid)`：
     - `span-7`：GPU 与系统资源 Meter（5 项容量条 + 环境参数 KV + `<details>` 精确表）
     - `span-5`：任务队列与归档模式分布（队列分段条 + 当前已读取归档按 `mode` 水平排行条与平均耗时 + `<details>` 精确表）
     - `span-12`：可切换式机器累计开机时间（分 GPU 含 `CPU Only`）（支持「全部累计 / 今日 / 近 7 天 / 当前会话」4 周期切换，由 `server.py` 在处理 `/api/snapshot` 及写操作请求时被动追踪并持久化至 `uptime_ledger.json`，含构成占比条、水平排行条与 `<details>` 精确表）
     - `span-12`：最近归档出图 GPU 耗时趋势（零基线柱状图，点击或回车可查看单次出图详情）
     - `span-12`：云盘归档出图明细表（模式筛选、Prompt/文件名检索、列排序 `aria-sort`、粘性表头与首列、点击打开原生 `<dialog>` 详情含完整 Prompt 复制与可选 PNG 预览）
  7. `页脚 (footer)` 与 `<noscript>` 降级提示：数据源路径、只读与写操作边界、Sage v1.1.0 及本地字体声明

| 内容区域 | 数量上限 | 聚合规则 | 长内容/小屏策略 | 精确值入口 |
|---|---|---|---|---|
| 资源 Meter | 固定 5 项（GPU、VRAM、RAM、Disk、Idle） | 无聚合 | 标签与数值换行 | 卡片内 `<details>` 精确参数表 |
| 模式分布 | 超过 5 个独立模式时，展示**前 4 项 + 合并尾部为“其他”**（`<= 5` 项全量直出） | 基于当前已读取的 `recent_outputs` 计数与均值汇总（分母为当前读取条数） | 百分比 1 位小数；排行条均值 1 位小数，`<details>` 表均值 2 位小数 | 卡片内 `<details>` 全量模式精确统计表（含合计行） |
| 机器累计开机时间 | 固定保留 `CPU Only` 独立展示；排行条中非 CPU 项超过 5 项时，取**前 4 项 + 合并尾部为“其他 GPU”**；顶部构成占比条与图例当前按未合并的活跃项全量展示 | 由浏览器请求被动驱动记录在线会话线段，首次观测新会话时按 §3 回填初始时长；持久化至 `uptime_ledger.json`（超过 2000 段且早于 30 天时折叠入 `archived_totals`） | `.table-scroll` 滚动 + 周期分段控件自适应换行 | 卡片内 `<details>` 分硬件精确秒数、会话数与最近在线时间表 |
| 耗时趋势 | 跟随顶部条数（6 / 12 / 全部）；「全部」发送 `limit=0` 拉取全量供明细表筛选，但趋势柱状图前端封顶绘制**最近 200 条**（`MAX_CHART_BARS = 200`）并在图注中明示截断范围，按时间正序排列 | 不聚合单次出图 | `.chart-scroll` 容器内横向滚动 | 悬停/聚焦提示 + 点击打开 `<dialog>` |
| 归档明细表 | 跟随顶部条数，默认 12（「全部」不限条数） | 不聚合 | `.table-scroll` 容器内横向滚动，首列 sticky | 原生 `<dialog>` 完整 Prompt/路径/Seed |

## 3. 指标与数据契约

| 指标 | 来源 / 公式 | 单位 / 精度 | 分母 / 筛选范围 | 零 / 缺失 / 异常 | 时间 / 时区 |
|---|---|---|---|---|---|
| 空闲剩余时间 | `/health`: `max(0, idle_timeout_minutes - idle_seconds / 60)` | 分钟，1 位小数 | `idle_timeout_minutes` | 离线或 `timeout <= 0` 显示 `—` | 服务端采集瞬间快照 |
| 空闲释放计时占比 | `/health`: `clamp(0..100, idle_seconds / (idle_timeout_minutes * 60) * 100)` | `%`，1 位小数（辅以 `s / s` 原值） | `idle_timeout_minutes * 60` | 离线或 `timeout <= 0` 显示 `—` | 采集瞬间 |
| GPU 利用率 | `/v1/metrics`: `gpu.util_gpu_pct` | `%`，**整数**（`0` 位小数） | `100%` | `gpu.available=false` 或 `/v1/metrics` 不可用显示 `—` | 采集瞬间 |
| GPU 显存 | `/v1/metrics`: `gpu.vram_pct` 与 `vram_used_mib / vram_total_mib` | `MiB`（1024 进制），百分比 1 位小数 | `vram_total_mib`（为 0 时不可用） | `gpu.available=false` 或缺失显示 `—` | 采集瞬间 |
| 系统内存 (RAM) | `/v1/metrics`: `ram.pct`, `ram.used_gib`, `ram.total_gib` | `GiB`（1024 进制），百分比 1 位小数 | `ram.total_gib` | 缺失显示 `—` | 采集瞬间 |
| 磁盘空间 (Disk) | `/v1/metrics`: `disk.used_gib`, `disk.free_gib`, `disk.total_gib`；进度条 `clamp(0..100, used_gib / total_gib * 100)` | `GiB`（1024 进制） | `disk.total_gib` | 缺失或 `total_gib <= 0` 时进度条置空、文案显示 `—` | 采集瞬间 |
| 任务队列与已完成计数 | `/v1/metrics`: `jobs.succeeded`、`running`、`queued`、`failed` | 整数（KPI 5 单位 `次`） | 当前 Colab 网关进程生命周期累计（**实例重启后归零**） | `/v1/metrics` 不可用时 KPI 与总计显示 `—` | 采集瞬间 |
| 归档模式分布 | `outputs/h3_*.json`（经 `limit` 截取后的 `recent_outputs`）按 `mode` 聚合 | 条数（整数）、占比 `%`（1 位小数）、均值 `s`（排行条 1 位 / `<details>` 表 2 位） | **当前已读取的 `recent_outputs.length`**（非全盘 `total_output_files`） | 无记录时显示空态提示；缺 `mode` 归为 `"未知"` | 随 `recent_outputs` 实时重算 |
| 本地归档总数 | `outputs/h3_*.json` 文件计数（`total_output_files`） | 整数 | `ep_path.parent / "outputs"` 目录全量 | 目录不存在时为 `0` | 每次快照采集时扫描 |
| 归档出图耗时 | `outputs/h3_*.json`: `elapsed_sec` | 秒 (`s`)，详情/表 2 位小数，排行条均值 1 位小数 | 按模式均值分母为该模式有效数值 `elapsed_sec` 样本数 | `elapsed_sec` 为 `null` 或 `NaN` 显示 `—`，不计入均值 | 原始 `created_at` 无时区偏移；表格按字符截取 `[11:19]` 原样展示，详情保留 `created_at_full` 原文 |
| 离线原因与关机态 | `fetch_snapshot`: `offline_reason` 枚举 + `shutdown_pending` 布尔值 | 枚举：`missing_config` / `invalid_config` / `shutdown_confirmed` / `tunnel_closed` / `unreachable` | 仅离线快照含 `offline_reason`；关机指令发出后 45s 内若 `/health` 仍可达则标记 `shutdown_pending=true` | 关机标记保存在本机系统临时目录 `minimax_h3_shutdown_state.json`，仅记录 `base_url -> epoch` | UTC / 本机单调 epoch |
| 机器累计开机时间 | `server.py` 在 `/api/snapshot` 与写操作返回时对在线会话累加线段并落盘 `uptime_ledger.json`；硬件分类：`gpu.available=true` → `gpu.name`（`gpu`），`false` → `CPU Only`（`cpu_only`），无法识别且无同会话历史 → `待识别硬件`（`unknown`，统计入 `unknown_seconds`）；**首次见到未记录的 `session_key` 时**按 `uptime_seconds`（上限 `86400s` / 24h）回填起始时间，若缺失则回退 `idle_seconds`（上限 `7200s` / 2h） | 摘要与条形图 `h/m/s`（四舍五入整秒），`<details>` 表与 JSON 保留 `0.1s`；占比 `%` 1 位小数 | 所选周期内总在线秒数（`all` 含 `archived_totals`；`today` 为本地时区零点起；`7d` 为**滚动 `7 × 86400s`** 而非 7 个自然日；`current` 为当前在线会话） | 周期内无在线时长显示 `0s`（`0.0%`）；超过 `300s` 心跳间隔截断不计入；临时 `/v1/metrics` 404 沿用同会话已知非 `unknown` 硬件；`idle_seconds` 回退仅为近似下界（见已知限制） | 「今日」按本地时区 `00:00:00` 起算；`updated_at` 与 `last_seen_at` 存 UTC，前端按本地时区展示 |

## 4. 状态和交互矩阵

| 操作 | 更新区域 | 保留状态 | 重置状态 | 竞态、超时与焦点策略 |
|---|---|---|---|---|
| 数据源切换（实时 / 演示） | KPI、全部卡片、Scope、横幅、轮询定时器 | 显示设置、归档条数、表格排序与筛选（模式不存在时安全回退） | 清除关机轮询定时器与 `isShuttingDown`；切入演示时重置 `demoOnline=true` 与示例 `demoIdleSeconds` | 切入任一模式均先清除轮询定时、递增 `requestSeq` 并 `abort()` 在途请求，同时重置加载态，防止迟到实时响应覆盖演示界面 |
| 轮询间隔切换（手动 / 3s / 5s / 10s / 30s） | `setTimeout` 轮询调度与按钮状态 | 业务数据、筛选、排序、展开的 `<details>` | 无 | 采用**单飞（single-flight）+ 完成后调度**（`scheduleNextPoll`）：轮询触发时若已有在途快照或保活请求则跳过本轮，避免慢网下饿死与服务端线程堆积；`visibilitychange` 回到前台时以 `isPoll: true` 触发一次单飞刷新；服务端上游单次超时 `5s` |
| 立即刷新 / 异常横幅「刷新检测」「查看演示」 | 全部业务区块（或切换数据源） | 显示设置、筛选、排序 | 隐藏图表悬浮提示 | 递增 `requestSeq` 并中止在途快照请求 |
| 归档条数（6 / 12 / 全部） | 趋势图、模式分布、明细表（重拉 `/api/snapshot?limit=...`） | 显示设置、表格筛选与排序 | 无 | 与轮询共享 `requestSeq` 与 `AbortController` |
| 手动保活（`#keepalive-now`） | 按钮 busy/禁用态、全局提示 `#notice`、全量刷新快照（演示模式仅本地将 `demoIdleSeconds` 置 0） | 显示设置、表格筛选与排序 | 重置空闲倒计时（实时经 `POST /v1/keepalive` 或兼容降级 `GET /v1/jobs/__keepalive__`） | 置 `isKeepingAlive=true` 防重入；递增 `requestSeq` 并 `abort()` 在途轮询请求；完成后恢复按钮态 |
| 一键关机（`#shutdown-now` → `#shutdown-dialog` → 确认） | 弹出确认模态框（有运行/排队任务时显示红色警示）；确认后发送 `POST /api/shutdown`、更新按钮为「释放中…」/「已关机」、更新横幅与 KPI（演示模式仅本地置 `demoOnline=false`） | 显示设置、历史归档明细 | 确认后清除旧关机定时器；实时成功响应后触发 `2.5s–30s` 共 9 次补拉，并在 `32s` 仍在线时提示手动执行 Step 7 | 置 `isShuttingDown=true` 防重入；递增 `requestSeq` 并 `abort()` 在途请求；服务端写 45s 临时关机标记；关闭确认框后焦点回到 `#shutdown-now` |
| 开机时间周期切换（全部累计 / 今日 / 近 7 天 / 当前会话） | 累计开机卡片内汇总 KV、占比条、排行条与 `<details>` 明细表 | 轮询、显示设置、其余卡片与表格状态 | 无 | 直接基于内存中最新 `uptime_summary.windows` 重绘并同步滑动指示器，不发网络请求 |
| 模式筛选 / 搜索 / 排序 | 明细表行数、顺序与表头 `aria-sort` | KPI、硬件卡片、趋势图、累计开机卡片 | 切换数据源/刷新时保留排序与搜索关键字 | 搜索在每次 `input` 事件同步过滤重绘（**当前无防抖**）；`compositionstart` / `compositionend` 维护 `isComposing`，避免中文输入法组合期间提前过滤（全页除 `Escape` 关闭 tooltip 外无其他全局快捷键） |
| 打开/关闭归档详情 `<dialog>` | Top-layer 模态框（含可选 PNG 预览与 Prompt 异步复制） | 后台轮询继续执行 | 关闭时清理触发引用 | 若原触发节点仍挂载（`isConnected`）则直接聚焦；若轮询重建了表格/柱状图 DOM，则按 `data-detail-file` + `data-detail-source` 重新定位对应触发按钮，记录已被筛选隐藏时回退至 `#output-search` |
| 切换显示设置（Palette / Tone / Material / Theme） | `:root` `data-*` 属性、控件状态与 `.display-status` 文字 | 所有业务数据、展开的 `<details>`、表格过滤 | 回到 `auto` 时删除显式 `mode` 存储键 | 通过 `storage` 事件跨标签同步；监听 `prefers-color-scheme` 与 `forced-colors` 媒体查询变化 |

## 5. 设计覆盖项

- **默认主题**：`palette = "sage"`，`mode = "auto"`（支持切换至 `ash-marble`、`pale` / `gray`、`marble` / `flat` 与 `auto -> light -> dark`），存储键前缀为 `h3-monitor-display:`，回到 `auto` 时删除显式 `mode` 键并经 `storage` 事件跨标签同步。
- **Ash Marble opt-in 范围**：仅 5 张大卡片（`.card.ash-marble-card`）在 `ash-marble` + `light` + `marble` 下叠加 `0.12` 透明度的 `web/assets/ash-marble.svg`；画布、KPI 区、工具栏按钮、输入框、分段控件、表格单元、粘性表头/表尾与 `<dialog>` 均严格保持纯色无石纹。
- **Ash 双浅色调图表校准 (`AUDIT-0003`)**：`pale`（`surface-2: #E4E9EC`）与 `gray`（`surface-2: #DCE1E2`）分别配置独立 `--s1..s5`、`--other`、`--data-prior-*`/`--data-current-period` 与 8 档单调 `--seq-0..7`、`--heat-0..7`；`.trend-visual` 保留 `padding-block-start: 0.5rem` 防止横滚裁切纵轴首刻度。
- **稳定实体色槽映射（确定性，非唯一防撞）**：
  - 生成模式（`mode`）：`edit` → `s1`、`t2i` → `s2`、`i2i` → `s3`、`upscale` → `s4`、`其他` → `other`；未见模式按名称确定性哈希映射至 `s1..s5`（不随排序变化，但**可能与固定模式或其他未知模式撞色**）。
  - 机器硬件（`uptime_summary`）：`CPU Only` → `s3`、名称含 `L4` → `s1`（注意子串匹配亦会命中 `L40`/`L40S`）、含 `A100` → `s2`、含 `T4` → `s4`、含 `V100`/`H100`/`RTX`/`G4` → `s5`（同组多卡共用 `s5`）、`unknown`（「待识别硬件」）与折叠项 `其他 GPU` → `other`；其余未命中型号确定性哈希至 `s1/s2/s4/s5`。
- **KPI 强调色与危险按钮约束**：仅主 KPI（`.kpi--primary :is(.kpi__value, .metric-value)`）在 Ash Marble 浅色下使用 `#345F78`（`--accent-hero`），其余数字使用 `--ink-1`。工具栏「一键关机」按钮（`.danger-button`）使用高对比 `--ink-1` 文字配 `--critical` 混合描边；仅弹窗确认主动作（`.danger-solid-button`）使用白字红底（常态 `--critical: #d03b3b` 对比度 `4.80:1`，`:hover` 使用显式加深色 `--critical-hover: #b32d2d`，对比度 `6.24:1`，不再降低 `opacity`）。
- **字体策略**：声明 `"Space Grotesk", "Noto Sans SC", ...` 与 `"Space Mono", ...`（配 `font-size-adjust: from-font` 与 `<meta name="text-scale" content="scale">`）；已通过 `web/styles/fonts.css` 本地托管 107 个 `.woff2` 字体分片至 `web/assets/fonts/`，并随目录打包 `web/assets/fonts/OFL.txt`（含三套字体的原始版权声明与 SIL OFL-1.1 全文），无需外网字体 CDN 且跨平台不回退微软雅黑。

| 默认项 | 本项目值 | 修改原因 | 验证范围 |
|---|---|---|---|
| 默认配色与主题 | `sage` + `auto`（可切换 `ash-marble`） | 保持 Sage v1.1.0 默认识别度并支持冷灰可选主题 | `tests/test_dashboard.py` 自动化 CSS/Token 与 Ash Marble 双浅色调对比度校验 |
| 字体及授权/托管 | 本地托管 OFL-1.1 `Space Grotesk` / `Noto Sans SC` / `Space Mono` `.woff2`（107 文件 + `OFL.txt`） | 确保 Windows/macOS 离线渲染一致、不回退微软雅黑且符合 OFL-1.1 再分发要求 | `tests/test_dashboard.py` 校验 `fonts.css` 声明、全部 `.woff2` 及 `OFL.txt` 存在与归属 |
| KPI 数量与写操作 | 5 格 KPI + 手动保活 / 远程关机（Web `<dialog>` 二次确认，CLI 交互确认 / `--yes`） | 适应 Colab L4 网关运维边界，自动轮询保持严格只读 | 后端集成测试覆盖 `Host`/`Origin` 与 `X-H3-Action` 403 门禁、`--allow-remote` 限制、CLI 非交互拒绝、凭证隔离、保活降级与关机代理；**前端二次确认弹窗交互未由浏览器自动化覆盖** |
| Token/对比度与现代 Web | 对齐 `design-tokens.json` & `ash-marble.json` v1.1.0 | 满足 `AUDIT-0002` 与 `AUDIT-0003` 校准门禁 | `tests/test_dashboard.py` 自动化校验（含 `--critical` 与 `--critical-hover` `>= 4.5:1`；未离线重验外部仓库源 JSON） |

## 6. 验收矩阵与已知限制

### 已通过自动化验证
- [x] `base.css`、`ash-marble.css` 的 `@layer` 声明、Ash Marble 双浅色调对比度（正文 `>= 4.5:1`、图表/连续色阶 `>= 3.0:1`、轨道/卡片 `>= 1.15:1`）、`--critical` / `--critical-hover` 白字对比度 `>= 4.5:1`、石纹 `opacity <= 0.12` 与 SVG 安全规则已通过 `tests/test_dashboard.py`（10/10 用例）校验。
- [x] HTML 语义、唯一 ID、ARIA 引用、本地资源不越界（含 `fonts.css`、全部 107 个 `.woff2` 与 `OFL.txt`）、`type="button"`、`rem` 字号、零 `*` 全局重置、安全 DOM API（无 `innerHTML` / `eval`）及 `node --check` 语法检查已通过。
- [x] 服务端只读轮询、`/api/output-image` 路径遍历拦截、`api_key` 响应零泄露、`Host`/`Origin` DNS rebinding 403 拦截、`--allow-remote` 非回环拒绝、`X-H3-Action` 403 门禁、CLI `--shutdown` 非交互确认拦截、保活回退与 `UptimeTracker` 持久化逻辑已通过 `unittest` 验证。
- [x] 数据源切换先递增 `requestSeq` 并中止在途请求、single-flight 完成后轮询（无 `setInterval`）、趋势图 `MAX_CHART_BARS = 200` 渲染上限与详情弹窗关闭按 `data-detail-file` 恢复焦点已实现并通过静态结构回归测试。

### 待决策 / 待实测验证项
- [ ] **开机时长首次回填口径确认**：确认是否保留无 `uptime_seconds` 时的 `idle_seconds`（上限 2h）回填回退，以及是否需为 `server.py` 增加独立后台心跳线程。
- [ ] 无头多浏览器（Chromium / Firefox / WebKit）跨视口（1440 / 980 / 600 / 390 / 320px）、真实系统字号缩放、粗指针 `44px` 触控、键盘漫游与读屏软件实测（本机未接入浏览器自动化环境，不宣称已完成读屏与跨浏览器认证）。

### 已知限制与未验证边界
- **被动追踪边界**：开机时长仅在浏览器页面打开且触发 `/api/snapshot`（或写操作）期间追踪，服务端无独立后台心跳线程；关闭页面、设为「手动」刷新、标签页后台节流或休眠导致两次采样间隔超过 `300s` 时均截断不计入。
- **首次会话回填近似性**：首次观测到新 `session_key` 且上游未提供 `uptime_seconds` 时，服务端当前会以 `idle_seconds`（上限 2h）回填起始时间；`idle_seconds` 仅代表距上次活跃的空闲时长，不等同于真实累计开机时长。
- **多端共享账本不支持并发写入**：Google Drive 同步目录下的 `uptime_ledger.json` 仅按本地 `mtime` 变化全量重载（重载会丢弃内存中尚未每 `30s` 刷盘的增量），写回采用后写者覆盖（last-writer-wins），叠加云盘同步延迟可能丢失部分会话线段；不支持多台电脑同时运行监控服务共享同一账本。
- **离线验证边界**：外部权威仓库 `sage_design_paradigm@de3fec6` 原文未在本机离线环境下重新比对，Token 门禁以 `tests/test_dashboard.py` 内固化基线为准。
