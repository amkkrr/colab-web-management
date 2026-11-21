/* MiniMax-H3 Web Dashboard client logic.
   Safe DOM construction via createElement and textContent only. */
(() => {
  "use strict";

  const display = window.__H3_DISPLAY__;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const forcedColors = window.matchMedia("(forced-colors: active)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const labels = {
    sage: "Sage",
    "ash-marble": "Ash Marble",
    auto: "自动",
    light: "浅色",
    dark: "深色",
    pale: "苍白",
    gray: "苍灰",
    marble: "石纹",
    flat: "纯色"
  };

  const integerFmt = new Intl.NumberFormat("en-US");
  const MODE_COLORS = {
    edit: "s1",
    t2i: "s2",
    i2i: "s3",
    upscale: "s4",
    other: "other"
  };

  // Explicit demo sample (never disguised as live data)
  const DEMO_SNAPSHOT = {
    online: true,
    is_demo: true,
    fetched_at: "2026-11-15T07:55:00+00:00",
    ep_path: "demo://active_endpoint.json",
    ep_exists: true,
    base_url: "https://demo-colab-l4.trycloudflare.com",
    has_api_key: true,
    endpoint_updated_at: "2026-11-15T04:28:08",
    health: {
      active_jobs: 1,
      idle_seconds: 148,
      idle_timeout_minutes: 30,
      comfy_alive: true,
      dit_tier: "int8_convrot",
      gdrive_mode: "mount_sync"
    },
    metrics: {
      gpu: {
        available: true,
        name: "NVIDIA L4",
        util_gpu_pct: 84,
        vram_used_mib: 18940,
        vram_total_mib: 23034,
        vram_pct: 82.2,
        temp_c: 68,
        power_w: 64.5
      },
      ram: {
        used_gib: 19.4,
        total_gib: 53.0,
        pct: 36.6
      },
      disk: {
        used_gib: 64.2,
        total_gib: 112.6,
        free_gib: 48.4
      },
      jobs: {
        running: 1,
        queued: 2,
        succeeded: 28,
        failed: 1
      }
    },
    recent_outputs: [
      {
        file: "h3_edit_20261115_075218_1154.png",
        mode: "edit",
        size: "4096x4096",
        width: 4096,
        height: 4096,
        mp: 16.78,
        refs: 2,
        elapsed_sec: 118.71,
        dit_tier: "int8_convrot",
        seed: 20261154,
        created_at: "07:54:16",
        created_at_full: "2026-11-15T07:54:16",
        prompt: "[演示样本] Isometric courtyard diorama with Song-dynasty Qinglv Shanshui mineral-pigment palette.",
        local_path: "/content/colab_workspace/outputs/h3_edit_20261115_075218_1154.png",
        drive_url: "/content/drive/MyDrive/colab_workspace/outputs/h3_edit_20261115_075218_1154.png",
        has_image: false
      },
      {
        file: "h3_edit_20261115_074102_8821.png",
        mode: "edit",
        size: "2048x2048",
        width: 2048,
        height: 2048,
        mp: 4.19,
        refs: 1,
        elapsed_sec: 46.32,
        dit_tier: "int8_convrot",
        seed: 882104,
        created_at: "07:41:48",
        created_at_full: "2026-11-15T07:41:48",
        prompt: "[演示样本] Architectural elevation refinement preserving linework and warm limestone tone.",
        local_path: "/content/colab_workspace/outputs/h3_edit_20261115_074102_8821.png",
        drive_url: "/content/drive/MyDrive/colab_workspace/outputs/h3_edit_20261115_074102_8821.png",
        has_image: false
      },
      {
        file: "h3_t2i_20261115_072811_5512.png",
        mode: "t2i",
        size: "2048x1536",
        width: 2048,
        height: 1536,
        mp: 3.15,
        refs: 0,
        elapsed_sec: 34.15,
        dit_tier: "int8_convrot",
        seed: 551299,
        created_at: "07:28:45",
        created_at_full: "2026-11-15T07:28:45",
        prompt: "[演示样本] Minimalist product studio study on pale mineral stone plinth.",
        local_path: "/content/colab_workspace/outputs/h3_t2i_20261115_072811_5512.png",
        drive_url: "/content/drive/MyDrive/colab_workspace/outputs/h3_t2i_20261115_072811_5512.png",
        has_image: false
      },
      {
        file: "h3_t2i_20261115_071409_3210.png",
        mode: "t2i",
        size: "1536x1536",
        width: 1536,
        height: 1536,
        mp: 2.36,
        refs: 0,
        elapsed_sec: 26.8,
        dit_tier: "int8_convrot",
        seed: 321011,
        created_at: "07:14:36",
        created_at_full: "2026-11-15T07:14:36",
        prompt: "[演示样本] Botanical study in soft natural overcast daylight.",
        local_path: "/content/colab_workspace/outputs/h3_t2i_20261115_071409_3210.png",
        drive_url: "/content/drive/MyDrive/colab_workspace/outputs/h3_t2i_20261115_071409_3210.png",
        has_image: false
      },
      {
        file: "h3_i2i_20261115_065922_1904.png",
        mode: "i2i",
        size: "2048x2048",
        width: 2048,
        height: 2048,
        mp: 4.19,
        refs: 1,
        elapsed_sec: 41.9,
        dit_tier: "int8_convrot",
        seed: 190477,
        created_at: "07:00:04",
        created_at_full: "2026-11-15T07:00:04",
        prompt: "[演示样本] Style transfer pass toward muted ash marble material palette.",
        local_path: "/content/colab_workspace/outputs/h3_i2i_20261115_065922_1904.png",
        drive_url: "/content/drive/MyDrive/colab_workspace/outputs/h3_i2i_20261115_065922_1904.png",
        has_image: false
      },
      {
        file: "h3_edit_20261115_064015_7743.png",
        mode: "edit",
        size: "4096x4096",
        width: 4096,
        height: 4096,
        mp: 16.78,
        refs: 2,
        elapsed_sec: 112.4,
        dit_tier: "int8_convrot",
        seed: 774302,
        created_at: "06:42:07",
        created_at_full: "2026-11-15T06:42:07",
        prompt: "[演示样本] High-resolution 4K composite check with two reference plates.",
        local_path: "/content/colab_workspace/outputs/h3_edit_20261115_064015_7743.png",
        drive_url: "/content/drive/MyDrive/colab_workspace/outputs/h3_edit_20261115_064015_7743.png",
        has_image: false
      }
    ],
    total_output_files: 6,
    uptime_summary: {
      ledger_file: "demo://uptime_ledger.json",
      updated_at: "2026-11-15T07:55:00+00:00",
      today_start_at: "2026-11-15T00:00:00+08:00",
      current_online: true,
      current_hardware: "NVIDIA L4",
      current_category: "gpu",
      current_session_seconds: 3420.0,
      total_sessions: 14,
      compute_units_summary: {
        account_balance: 903.33,
        live_rate_hourly: 4.82,
        active_assignments: 1,
        estimated_remaining_hours: 187.4,
        usage_updated_at: "2026-11-15T07:55:00+00:00",
        all_units: 51.56,
        today_units: 7.11,
        seven_days_units: 45.66,
        current_session_units: 4.58
      },
      windows: {
        all: {
          total_seconds: 33240.0,
          gpu_seconds: 29700.0,
          cpu_only_seconds: 3540.0,
          unknown_seconds: 0.0,
          total_compute_units: 51.56,
          gpu_compute_units: 50.91,
          cpu_only_compute_units: 0.65,
          session_count: 14,
          items: [
            {
              hardware_key: "NVIDIA L4",
              category: "gpu",
              seconds: 18420.0,
              share_pct: 55.4,
              compute_units: 24.66,
              compute_units_share_pct: 47.8,
              rate_cu_per_hour: 4.82,
              rate_source: "live_ccu",
              session_count: 6,
              is_current: true,
              last_seen_at: "2026-11-15T07:55:00+00:00"
            },
            {
              hardware_key: "NVIDIA A100-SXM4-40GB",
              category: "gpu",
              seconds: 7380.0,
              share_pct: 22.2,
              compute_units: 24.13,
              compute_units_share_pct: 46.8,
              rate_cu_per_hour: 11.77,
              rate_source: "estimate",
              session_count: 2,
              is_current: false,
              last_seen_at: "2026-11-14T16:12:00+00:00"
            },
            {
              hardware_key: "Tesla T4",
              category: "gpu",
              seconds: 3900.0,
              share_pct: 11.7,
              compute_units: 2.12,
              compute_units_share_pct: 4.1,
              rate_cu_per_hour: 1.96,
              rate_source: "estimate",
              session_count: 3,
              is_current: false,
              last_seen_at: "2026-11-13T11:40:00+00:00"
            },
            {
              hardware_key: "CPU Only (High-RAM)",
              category: "cpu_only",
              seconds: 1800.0,
              share_pct: 5.4,
              compute_units: 0.60,
              compute_units_share_pct: 1.2,
              rate_cu_per_hour: 1.20,
              rate_source: "estimate",
              session_count: 1,
              is_current: false,
              last_seen_at: "2026-11-13T18:10:00+00:00"
            },
            {
              hardware_key: "CPU Only",
              category: "cpu_only",
              seconds: 1740.0,
              share_pct: 5.2,
              compute_units: 0.05,
              compute_units_share_pct: 0.1,
              rate_cu_per_hour: 0.10,
              rate_source: "estimate",
              session_count: 2,
              is_current: false,
              last_seen_at: "2026-11-12T09:20:00+00:00"
            }
          ]
        },
        today: {
          total_seconds: 6560.0,
          gpu_seconds: 5280.0,
          cpu_only_seconds: 1280.0,
          unknown_seconds: 0.0,
          total_compute_units: 7.11,
          gpu_compute_units: 7.07,
          cpu_only_compute_units: 0.04,
          session_count: 3,
          items: [
            {
              hardware_key: "NVIDIA L4",
              category: "gpu",
              seconds: 5280.0,
              share_pct: 80.5,
              compute_units: 7.07,
              compute_units_share_pct: 99.4,
              rate_cu_per_hour: 4.82,
              rate_source: "live_ccu",
              session_count: 2,
              is_current: true,
              last_seen_at: "2026-11-15T07:55:00+00:00"
            },
            {
              hardware_key: "CPU Only",
              category: "cpu_only",
              seconds: 1280.0,
              share_pct: 19.5,
              compute_units: 0.04,
              compute_units_share_pct: 0.6,
              rate_cu_per_hour: 0.10,
              rate_source: "estimate",
              session_count: 1,
              is_current: false,
              last_seen_at: "2026-11-12T09:20:00+00:00"
            },
            {
              hardware_key: "CPU Only (High-RAM)",
              category: "cpu_only",
              seconds: 0.0,
              share_pct: 0.0,
              compute_units: 0.0,
              compute_units_share_pct: 0.0,
              rate_cu_per_hour: 1.20,
              rate_source: "estimate",
              session_count: 0,
              is_current: false,
              last_seen_at: "2026-11-13T18:10:00+00:00"
            },
            {
              hardware_key: "NVIDIA A100-SXM4-40GB",
              category: "gpu",
              seconds: 0.0,
              share_pct: 0.0,
              compute_units: 0.0,
              compute_units_share_pct: 0.0,
              rate_cu_per_hour: 11.77,
              rate_source: "estimate",
              session_count: 0,
              is_current: false,
              last_seen_at: "2026-11-14T16:12:00+00:00"
            },
            {
              hardware_key: "Tesla T4",
              category: "gpu",
              seconds: 0.0,
              share_pct: 0.0,
              compute_units: 0.0,
              compute_units_share_pct: 0.0,
              rate_cu_per_hour: 1.96,
              rate_source: "estimate",
              session_count: 0,
              is_current: false,
              last_seen_at: "2026-11-13T11:40:00+00:00"
            }
          ]
        },
        "7d": {
          total_seconds: 27240.0,
          gpu_seconds: 24120.0,
          cpu_only_seconds: 3120.0,
          unknown_seconds: 0.0,
          total_compute_units: 45.66,
          gpu_compute_units: 45.02,
          cpu_only_compute_units: 0.64,
          session_count: 11,
          items: [
            {
              hardware_key: "NVIDIA L4",
              category: "gpu",
              seconds: 14820.0,
              share_pct: 54.4,
              compute_units: 19.84,
              compute_units_share_pct: 43.5,
              rate_cu_per_hour: 4.82,
              rate_source: "live_ccu",
              session_count: 5,
              is_current: true,
              last_seen_at: "2026-11-15T07:55:00+00:00"
            },
            {
              hardware_key: "NVIDIA A100-SXM4-40GB",
              category: "gpu",
              seconds: 7380.0,
              share_pct: 27.1,
              compute_units: 24.13,
              compute_units_share_pct: 52.8,
              rate_cu_per_hour: 11.77,
              rate_source: "estimate",
              session_count: 2,
              is_current: false,
              last_seen_at: "2026-11-14T16:12:00+00:00"
            },
            {
              hardware_key: "Tesla T4",
              category: "gpu",
              seconds: 1920.0,
              share_pct: 7.0,
              compute_units: 1.05,
              compute_units_share_pct: 2.3,
              rate_cu_per_hour: 1.96,
              rate_source: "estimate",
              session_count: 2,
              is_current: false,
              last_seen_at: "2026-11-13T11:40:00+00:00"
            },
            {
              hardware_key: "CPU Only (High-RAM)",
              category: "cpu_only",
              seconds: 1800.0,
              share_pct: 6.6,
              compute_units: 0.60,
              compute_units_share_pct: 1.3,
              rate_cu_per_hour: 1.20,
              rate_source: "estimate",
              session_count: 1,
              is_current: false,
              last_seen_at: "2026-11-13T18:10:00+00:00"
            },
            {
              hardware_key: "CPU Only",
              category: "cpu_only",
              seconds: 1320.0,
              share_pct: 4.8,
              compute_units: 0.04,
              compute_units_share_pct: 0.1,
              rate_cu_per_hour: 0.10,
              rate_source: "estimate",
              session_count: 1,
              is_current: false,
              last_seen_at: "2026-11-12T09:20:00+00:00"
            }
          ]
        },
        current: {
          total_seconds: 3420.0,
          gpu_seconds: 3420.0,
          cpu_only_seconds: 0.0,
          unknown_seconds: 0.0,
          total_compute_units: 4.58,
          gpu_compute_units: 4.58,
          cpu_only_compute_units: 0.0,
          session_count: 1,
          items: [
            {
              hardware_key: "NVIDIA L4",
              category: "gpu",
              seconds: 3420.0,
              share_pct: 100.0,
              compute_units: 4.58,
              compute_units_share_pct: 100.0,
              rate_cu_per_hour: 4.82,
              rate_source: "live_ccu",
              session_count: 1,
              is_current: true,
              last_seen_at: "2026-11-15T07:55:00+00:00"
            },
            {
              hardware_key: "CPU Only",
              category: "cpu_only",
              seconds: 0.0,
              share_pct: 0.0,
              compute_units: 0.0,
              compute_units_share_pct: 0.0,
              rate_cu_per_hour: 0.10,
              rate_source: "estimate",
              session_count: 0,
              is_current: false,
              last_seen_at: "2026-11-12T09:20:00+00:00"
            },
            {
              hardware_key: "CPU Only (High-RAM)",
              category: "cpu_only",
              seconds: 0.0,
              share_pct: 0.0,
              compute_units: 0.0,
              compute_units_share_pct: 0.0,
              rate_cu_per_hour: 1.20,
              rate_source: "estimate",
              session_count: 0,
              is_current: false,
              last_seen_at: "2026-11-13T18:10:00+00:00"
            },
            {
              hardware_key: "NVIDIA A100-SXM4-40GB",
              category: "gpu",
              seconds: 0.0,
              share_pct: 0.0,
              compute_units: 0.0,
              compute_units_share_pct: 0.0,
              rate_cu_per_hour: 11.77,
              rate_source: "estimate",
              session_count: 0,
              is_current: false,
              last_seen_at: "2026-11-14T16:12:00+00:00"
            },
            {
              hardware_key: "Tesla T4",
              category: "gpu",
              seconds: 0.0,
              share_pct: 0.0,
              compute_units: 0.0,
              compute_units_share_pct: 0.0,
              rate_cu_per_hour: 1.96,
              rate_source: "estimate",
              session_count: 0,
              is_current: false,
              last_seen_at: "2026-11-13T11:40:00+00:00"
            }
          ]
        }
      }
    }
  };

  const appState = {
    sourceMode: "live", // "live" | "demo"
    pollSeconds: 10,
    outputLimit: 12,
    uptimeRange: "all", // "all" | "today" | "7d" | "current"
    uptimeMetric: "time", // "time" | "units"
    modeFilter: "all",
    searchQuery: "",
    sortKey: "created_at_full",
    sortDir: "desc",
    snapshot: null,
    requestSeq: 0,
    abortController: null,
    inFlightSnapshot: false,
    pollTimer: null,
    lastFocusedTrigger: null,
    lastFocusedTarget: null,
    noticeTimer: null,
    isComposing: false,
    isKeepingAlive: false,
    isShuttingDown: false,
    shutdownTimers: [],
    lastShutdownTrigger: null,
    demoIdleSeconds: DEMO_SNAPSHOT.health.idle_seconds,
    demoOnline: true,
    // v2.0 Control Plane state
    v2Instances: [],
    selectedInstanceId: null,
    currentV2Instance: null,
    v2Commands: [],
    inspectorTab: "logs", // "logs" | "processes"
    logSource: "agent",
    logFilter: "",
    lastLogPayload: null,
    lastProcesses: [],
    pendingConfirmAction: null,
    lastConfirmTrigger: null,
    demoV2State: null,
    // Plan A Notebook Launcher & Injector state
    notebooks: [],
    notebooksSearch: "",
    notebooksIncludeBackups: false,
    notebooksLoaded: false,
    notebooksDriveEnabled: false,
    notebooksDriveError: null,
    pendingInjectTarget: null,
    lastInjectTrigger: null,
    pendingBindTarget: null,
    lastBindTrigger: null,
    archiveUserToggled: false,
    // Headless CLI + Pure OAuth2 Provisioner state
    provisionerStatus: null,
    provisionerActiveJobId: null,
    provisionerJobTimer: null,
    provisionerBooting: false
  };

  function createDemoV2State() {
    const nowIso = new Date().toISOString();
    return {
      selectedId: "demo_inst_l4_01",
      instances: [
        {
          schema_version: "2.0",
          instance_id: "demo_inst_l4_01",
          is_legacy: false,
          project: "image_studio_demo",
          label: "Image Studio · L4 在线网关",
          run_mode: "online_gateway",
          liveness_state: "ONLINE_LIVE",
          active_channel: "tunnel",
          has_api_key: true,
          started_at: "2026-11-21T02:15:00Z",
          heartbeat_at: nowIso,
          capabilities: [
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
          tunnel: {
            enabled: true,
            healthy: true,
            base_url: "https://demo-colab-l4.trycloudflare.com",
            agent_prefix: "/agent/v1",
            updated_at: "2026-11-21T02:16:12Z"
          },
          lifecycle: {
            state: "running",
            idle_seconds: 142,
            idle_timeout_minutes: 60,
            keepalive_lease_until: null,
            deadline_shutdown_at: null,
            auto_shutdown_after_batch: true,
            drain_requested: false
          },
          hardware: {
            gpu: {
              available: true,
              name: "NVIDIA L4",
              util_gpu_pct: 78,
              vram_used_mib: 16420,
              vram_total_mib: 23034,
              vram_pct: 71.3,
              temp_c: 63,
              power_draw_w: 64.2
            },
            ram: { used_gib: 18.4, total_gib: 53.0, pct: 34.7 },
            cpu: { cores_logical: 12, util_pct: 42.5, load_avg_1m: 3.12 },
            disk: { used_gib: 52.4, free_gib: 60.2, total_gib: 112.6, pct: 46.5 },
            drive: { mode: "oauth_silent", healthy: true, latency_ms: 145 }
          },
          services: {
            comfyui: { status: "running", pid: 1842, port: 8188, uptime_sec: 1810 },
            cloudflared: { status: "running", pid: 1995, uptime_sec: 1758 },
            gateway: { status: "running", port: 8000, uptime_sec: 1760 }
          },
          workload: {
            kind: "job_queue",
            paused: false,
            running: 1,
            queued: 4,
            succeeded: 19,
            failed: 1,
            progress: { stage: "在线/脱机队列生成", current_item: "task_020_courtyard_4k", done: 19, total: 24, eta_sec: 215 }
          }
        },
        {
          schema_version: "2.0",
          instance_id: "demo_inst_t4_02",
          is_legacy: false,
          project: "audio_batch_demo",
          label: "Audio Batch · 脱机批跑",
          run_mode: "offline_batch",
          liveness_state: "ONLINE_DRIVE_ONLY",
          active_channel: "drive",
          has_api_key: true,
          started_at: "2026-11-21T01:40:00Z",
          heartbeat_at: nowIso,
          capabilities: [
            "lifecycle.keepalive",
            "lifecycle.set_keepalive_lease",
            "lifecycle.set_idle_policy",
            "lifecycle.drain_shutdown",
            "lifecycle.cancel_drain",
            "lifecycle.shutdown_now",
            "network.rebuild_tunnel",
            "ops.kill_process",
            "ops.cleanup_disk",
            "workload.pause_queue",
            "workload.resume_queue",
            "storage.flush_and_verify",
            "logs.snapshot_to_drive",
            "read.logs"
          ],
          tunnel: { enabled: false, healthy: false, base_url: "", agent_prefix: "/agent/v1", updated_at: null },
          lifecycle: {
            state: "running",
            idle_seconds: 18,
            idle_timeout_minutes: 90,
            keepalive_lease_until: null,
            deadline_shutdown_at: null,
            auto_shutdown_after_batch: true,
            drain_requested: false
          },
          hardware: {
            gpu: {
              available: true,
              name: "Tesla T4",
              util_gpu_pct: 64,
              vram_used_mib: 6120,
              vram_total_mib: 15360,
              vram_pct: 39.8,
              temp_c: 58,
              power_draw_w: 51.0
            },
            ram: { used_gib: 8.6, total_gib: 12.7, pct: 67.7 },
            cpu: { cores_logical: 8, util_pct: 68.4, load_avg_1m: 5.42 },
            disk: { used_gib: 34.1, free_gib: 44.1, total_gib: 78.2, pct: 43.6 },
            drive: { mode: "fuse", healthy: true, latency_ms: 420 }
          },
          services: {
            cloudflared: { status: "stopped", pid: null }
          },
          workload: {
            kind: "batch_loop",
            paused: false,
            running: 1,
            queued: 21,
            succeeded: 14,
            failed: 0,
            progress: { stage: "章节语音合成 (Kokoro)", current_item: "Chapter_15.wav", done: 14, total: 36, eta_sec: 1420 }
          }
        }
      ],
      commands: [],
      logs: {
        agent: [
          "[2026-11-21T02:45:00Z] Agent initialized: instance_id=demo_inst_l4_01, mode=online_gateway",
          "[2026-11-21T02:45:30Z] Heartbeat published via oauth_silent (145ms), secrets redacted: [REDACTED]",
          "[2026-11-21T02:46:00Z] Action [tunnel] lifecycle.keepalive -> succeeded"
        ],
        comfyui: [
          "[ComfyUI] Loaded MiniMax-H3 int8_convrot weights in 14.2s",
          "[ComfyUI] Prompt executed in 41.82 seconds (2048x2048)"
        ],
        cloudflared: [
          "INF Registered tunnel connection connIndex=0 location=hkg02",
          "INF Tunnel healthy: https://demo-colab-l4.trycloudflare.com"
        ],
        gateway: [
          "INFO:     127.0.0.1 - \"GET /agent/v1/metrics HTTP/1.1\" 200 OK",
          "INFO:     Authorization: Bearer [REDACTED]"
        ]
      },
      processes: [
        { pid: 1, ppid: 0, create_time: 100, name: "tini", cmdline: "/sbin/tini -- /datalab/run", rss_mib: 4.2, killable: false, protected_reason: "PID 1 系统根进程" },
        { pid: 142, ppid: 1, create_time: 105, name: "python3", cmdline: "python3 -m ipykernel_launcher", rss_mib: 412.5, killable: false, protected_reason: "受保护系统进程 (ipykernel_launcher)" },
        { pid: 1842, ppid: 142, create_time: 120, name: "python3", cmdline: "python3 /content/ComfyUI/main.py --listen 127.0.0.1 --port 8188", rss_mib: 6840.0, killable: true, protected_reason: null },
        { pid: 1995, ppid: 142, create_time: 130, name: "cloudflared", cmdline: "/usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8000", rss_mib: 42.8, killable: true, protected_reason: null }
      ],
      notebooks: [
        {
          path: "gdrive://demo_sample_gateway_id",
          source: "drive_api",
          file_id: "demo_sample_gateway_id",
          name: "sample_gateway_colab.ipynb",
          stem: "sample_gateway_colab",
          display_path: "Colab Notebooks/sample_gateway_colab.ipynb",
          is_backup: false,
          size_kib: 74.8,
          modified_at: nowIso,
          injected: true,
          injected_version: "2.0",
          detected_mode: "oauth_silent",
          has_fastapi: true,
          total_cells: 11,
          colab_url: "https://colab.research.google.com/drive/demo_sample_gateway"
        },
        {
          path: "demo://Colab Notebooks/sample_audio_batch_colab.ipynb",
          source: "local",
          file_id: null,
          name: "sample_audio_batch_colab.ipynb",
          stem: "sample_audio_batch_colab",
          display_path: "Colab Notebooks/sample_audio_batch_colab.ipynb",
          is_backup: false,
          size_kib: 42.1,
          modified_at: nowIso,
          injected: false,
          injected_version: null,
          detected_mode: "fuse_mounted",
          has_fastapi: false,
          total_cells: 6,
          colab_url: ""
        }
      ],
      provisioner: {
        ok: true,
        cli_available: true,
        cli_auth_configured: true,
        drive_oauth_configured: true,
        drive_monitor_path: "_colab_monitor",
        drive_oauth_health: { ok: true, monitor_folder_path: "_colab_monitor" },
        usage: {
          available: true,
          balance: 903.33,
          rate_hourly: 4.82,
          assignments_count: 1,
          estimated_remaining_hours: 187.4,
          fetched_at: nowIso
        },
        busy: false,
        active_job: null,
        recent_jobs: [],
        sessions: [
          {
            name: "demo-l4-studio",
            endpoint: "m-s-demo-l4-01",
            hardware: "L4",
            shape: "High-RAM",
            variant: "GPU",
            status: "ACTIVE",
            instance_id: "demo_inst_l4_01",
            drive_mode: "oauth_silent"
          }
        ]
      }
    };
  }

  function clearShutdownTimers() {
    for (const tid of appState.shutdownTimers) {
      window.clearTimeout(tid);
    }
    appState.shutdownTimers = [];
  }

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }

  function formatNum(val, digits = 1) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "—";
    return Number(val).toFixed(digits);
  }

  function formatLocalTime(iso) {
    if (!iso) return "—";
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return String(iso);
    return parsed.toLocaleString("zh-CN", {
      hour12: false,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
  }

  function getSeriesToken(mode) {
    if (Object.hasOwn(MODE_COLORS, mode)) return MODE_COLORS[mode];
    // Deterministic hash into s1..s5 for unseen modes (independent of rank)
    let hash = 0;
    const str = String(mode || "");
    for (let i = 0; i < str.length; i++) {
      hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
    }
    return "s" + ((hash % 5) + 1);
  }

  function showNotice(message) {
    const notice = document.getElementById("notice");
    notice.textContent = message;
    notice.hidden = false;
    if (appState.noticeTimer) window.clearTimeout(appState.noticeTimer);
    appState.noticeTimer = window.setTimeout(() => {
      notice.hidden = true;
    }, 3500);
  }

  // --- Display / Theme toolbar ---
  function syncDisplayControls() {
    display.applyRootAttributes();
    const resolved = display.resolveMode();
    const activeAsh = display.state.palette === "ash-marble" && resolved === "light" && !forcedColors.matches;

    for (const dim of ["palette", "tone", "material"]) {
      for (const btn of document.querySelectorAll("[data-" + dim + "-choice]")) {
        btn.disabled = false;
        const val = btn.dataset[dim + "Choice"];
        btn.setAttribute("aria-pressed", String(val === display.state[dim]));
        const inactive = dim !== "palette" && !activeAsh;
        btn.setAttribute("aria-disabled", String(inactive));
      }
    }

    const modeBtn = document.getElementById("theme-mode");
    modeBtn.disabled = false;
    modeBtn.setAttribute("aria-disabled", "false");
    const modes = display.choices.mode;
    const next = modes[(modes.indexOf(display.state.mode) + 1) % modes.length];
    modeBtn.textContent = "主题：" + labels[display.state.mode];
    modeBtn.setAttribute(
      "aria-label",
      "主题模式：" + labels[display.state.mode] + "，当前" + labels[resolved] + "；切换为" + labels[next]
    );

    const detail = forcedColors.matches
      ? "强制颜色模式，浅色调和石纹停用"
      : activeAsh
        ? labels[display.state.tone] + " · " + labels[display.state.material]
        : resolved === "dark"
          ? "沿用 Sage 深色，石纹禁用"
          : "原 Sage 浅色，石纹禁用";

    document.getElementById("display-status").textContent =
      labels[display.state.palette] +
      " · " +
      labels[display.state.mode] +
      "（" +
      labels[resolved] +
      "） · " +
      detail +
      (display.storageAvailable ? "" : " · 存储不可用，仅当前页面生效");

    syncAllSegIndicators(false);
  }

  function selectDisplay(dimension, value) {
    if (!display.choices[dimension].includes(value)) return;
    display.state[dimension] = value;
    try {
      if (dimension === "mode" && value === "auto") {
        window.localStorage.removeItem(display.prefix + dimension);
      } else {
        window.localStorage.setItem(display.prefix + dimension, value);
      }
    } catch {
      display.setStorageUnavailable();
    }
    syncDisplayControls();
  }

  // --- Segmented control sliding indicator (Section 7 progressive enhancement) ---
  const segObservers = [];
  let segRafPending = false;

  function updateSegIndicator(group, immediate) {
    let indicator = group.querySelector(".seg-indicator");
    if (!indicator) {
      indicator = el("span", undefined, "seg-indicator");
      indicator.setAttribute("aria-hidden", "true");
      group.prepend(indicator);
    }
    const buttons = [...group.querySelectorAll("button")];
    const active = buttons.find((b) => b.getAttribute("aria-pressed") === "true");
    // Check single-row feasibility and non-zero layout; if wrapped or hidden, fall back to static styling
    const singleRow = buttons.length > 0 && buttons.every((b) => b.offsetTop === buttons[0].offsetTop);
    if (!active || active.offsetWidth === 0 || !singleRow || forcedColors.matches) {
      group.classList.remove("has-indicator");
      indicator.style.opacity = "0";
      return;
    }

    group.classList.add("has-indicator");
    if (immediate || reducedMotion.matches) {
      indicator.classList.add("is-init");
    }
    indicator.style.inlineSize = active.offsetWidth + "px";
    indicator.style.blockSize = active.offsetHeight + "px";
    indicator.style.transform = `translate3d(${active.offsetLeft}px, ${active.offsetTop}px, 0)`;
    indicator.style.opacity = "1";

    if (immediate) {
      void indicator.offsetWidth;
      indicator.classList.remove("is-init");
    }
  }

  function syncAllSegIndicators(immediate) {
    for (const group of document.querySelectorAll(".control-set[data-seg]")) {
      updateSegIndicator(group, immediate);
    }
  }

  function scheduleSegSync() {
    if (segRafPending) return;
    segRafPending = true;
    window.requestAnimationFrame(() => {
      segRafPending = false;
      syncAllSegIndicators(false);
    });
  }

  function setupToolbarFallback(bar) {
    if ("focusGroup" in HTMLElement.prototype) return;
    const getItems = () => [...bar.querySelectorAll("button")];
    const initial = getItems();
    initial.forEach((b, i) => {
      b.tabIndex = i === 0 ? 0 : -1;
    });
    bar.addEventListener("focusin", ({ target }) => {
      const items = getItems();
      if (items.includes(target)) {
        items.forEach((b) => {
          b.tabIndex = b === target ? 0 : -1;
        });
      }
    });
    bar.addEventListener("keydown", (event) => {
      const items = getItems();
      const index = items.indexOf(document.activeElement);
      if (index < 0) return;
      const vertical = bar.getAttribute("aria-orientation") === "vertical";
      const rtl = getComputedStyle(bar).direction === "rtl";
      const steps = vertical
        ? { ArrowDown: 1, ArrowUp: -1 }
        : { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 };
      let nextIndex = null;
      if (Object.hasOwn(steps, event.key)) {
        nextIndex = (index + steps[event.key] + items.length) % items.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = items.length - 1;
      }
      if (nextIndex !== null) {
        event.preventDefault();
        items[nextIndex].focus();
      }
    });
  }

  function setupScrollHintsFallback() {
    if (typeof CSS !== "undefined" && CSS.supports("animation-timeline", "scroll()")) return;
    if (typeof IntersectionObserver === "undefined") return;
    for (const scroller of document.querySelectorAll(".chart-scroll, .table-scroll")) {
      const content = scroller.firstElementChild;
      if (!content) continue;
      scroller.style.display = "flex";
      content.style.flex = "1 0 auto";
      const start = document.createElement("div");
      const end = document.createElement("div");
      start.setAttribute("aria-hidden", "true");
      end.setAttribute("aria-hidden", "true");
      start.style.cssText = "flex:0 0 1px;inline-size:1px;block-size:1px;margin-inline-end:-1px;pointer-events:none";
      end.style.cssText = "flex:0 0 1px;inline-size:1px;block-size:1px;margin-inline-start:-1px;pointer-events:none";
      scroller.prepend(start);
      scroller.append(end);
      const observer = new IntersectionObserver(
        (entries) => {
          for (const { target, isIntersecting } of entries) {
            scroller.classList.toggle(target === start ? "more-start" : "more-end", !isIntersecting);
          }
        },
        { root: scroller }
      );
      observer.observe(start);
      observer.observe(end);
    }
  }

  // --- Tooltip & Modal Dialog helpers ---
  function showTooltip(anchor, title, meta) {
    const tip = document.getElementById("chart-tooltip");
    const titleEl = document.getElementById("tooltip-title");
    const metaEl = document.getElementById("tooltip-meta");
    if (!tip || !titleEl || !metaEl) return;
    titleEl.textContent = title || "";
    if (meta) {
      metaEl.textContent = meta;
      metaEl.hidden = false;
    } else {
      metaEl.textContent = "";
      metaEl.hidden = true;
    }
    tip.hidden = false;

    const rect = anchor.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    const inset = 8;
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(inset, Math.min(left, window.innerWidth - tipRect.width - inset));
    let top = rect.top - tipRect.height - 8;
    if (top < inset) {
      top = Math.min(window.innerHeight - tipRect.height - inset, rect.bottom + 8);
    }
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }

  function hideTooltip() {
    const tip = document.getElementById("chart-tooltip");
    if (tip) tip.hidden = true;
  }

  function attachTooltipTarget(node) {
    if (!node || node.dataset.tooltipBound === "true") return;
    node.dataset.tooltipBound = "true";
    const show = () => {
      const txt = node.dataset.tooltip;
      if (txt) showTooltip(node, txt, "");
    };
    node.addEventListener("mouseenter", show);
    node.addEventListener("mouseleave", hideTooltip);
    node.addEventListener("focus", show);
    node.addEventListener("blur", hideTooltip);
  }

  let supportsDisplayTransitionCache;
  function canTransitionDisplay() {
    if (supportsDisplayTransitionCache !== undefined) return supportsDisplayTransitionCache;
    if (!window.CSS || typeof window.CSS.supports !== "function" || !window.CSS.supports("transition-behavior", "allow-discrete") || !document.body) {
      supportsDisplayTransitionCache = false;
      return false;
    }
    const probe = document.createElement("div");
    probe.style.cssText = "transition: display 1s allow-discrete !important; display: block;";
    document.body.appendChild(probe);
    void window.getComputedStyle(probe).display;
    probe.style.display = "none";
    supportsDisplayTransitionCache = window.getComputedStyle(probe).display === "block";
    probe.remove();
    return supportsDisplayTransitionCache;
  }

  function supportsTopLayerExit() {
    return Boolean(window.CSS && typeof window.CSS.supports === "function" && window.CSS.supports("overlay", "auto") && canTransitionDisplay());
  }

  function openModalDialog(dialog) {
    if (!dialog) return;
    dialog.removeAttribute("data-closing");
    if (!dialog.open) dialog.showModal();
  }

  async function closeTopLayerDialog(dialog) {
    if (!dialog || !dialog.open) return;
    if (!reducedMotion.matches && !supportsTopLayerExit() && !dialog.hasAttribute("data-closing") && typeof dialog.getAnimations === "function") {
      dialog.setAttribute("data-closing", "");
      const animations = dialog.getAnimations({ subtree: true });
      if (animations.length > 0) {
        await Promise.race([
          Promise.allSettled(animations.map((a) => a.finished)),
          new Promise((resolve) => window.setTimeout(resolve, 400))
        ]);
      }
      if (!dialog.hasAttribute("data-closing")) return;
      dialog.removeAttribute("data-closing");
    }
    if (dialog.open) dialog.close();
  }

  function enhanceModalDialog(dialog, closeFn) {
    if (!dialog) return;
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeFn();
    });
    if (!("closedBy" in HTMLDialogElement.prototype)) {
      const outside = (e) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      dialog.addEventListener("pointerdown", (e) => {
        pressedOutside = outside(e);
      });
      dialog.addEventListener("pointerup", (e) => {
        if (pressedOutside && outside(e)) closeFn();
        pressedOutside = false;
      });
    }
  }

  // --- Render functions ---
  function setKpiValue(id, valueText, unitText) {
    const node = document.getElementById(id);
    node.textContent = valueText;
    if (unitText && valueText !== "—") {
      node.append(el("span", " " + unitText, "units"));
    }
  }

  function isShutdownOffline(snap) {
    return Boolean(
      snap &&
      !snap.online &&
      (snap.offline_reason === "shutdown_confirmed" || snap.offline_reason === "tunnel_closed")
    );
  }

  function renderHeaderAndBanner(snap) {
    const dot = document.getElementById("gateway-dot");
    const summary = document.getElementById("gateway-summary");
    const tunnel = document.getElementById("tunnel-url");
    const updated = document.getElementById("last-updated");
    const scope = document.getElementById("data-scope");
    const banner = document.getElementById("status-banner");
    const bannerTitle = document.getElementById("banner-title");
    const bannerMsg = document.getElementById("banner-message");
    const retryBtn = document.getElementById("banner-retry");
    const demoBtn = document.getElementById("banner-demo");

    const curV2 = appState.currentV2Instance;
    tunnel.textContent = snap.base_url || (isDriveOnlyInstance(curV2) ? "无 HTTPS 隧道（Drive 带外心跳）" : "未配置隧道地址");
    updated.textContent = "采集于 " + formatLocalTime(snap.fetched_at);

    const isDemo = Boolean(snap.is_demo);
    const prefix = isDemo ? "[演示样本 · 非真实采集] " : "";
    const wasShuttingDown = appState.isShuttingDown;

    if (snap.online && snap.health) {
      if (appState.isShuttingDown || snap.shutdown_pending) {
        dot.dataset.state = "shutting-down";
        summary.textContent = prefix + "正在关机释放…";
      } else {
        const active = Number(snap.health.active_jobs || 0);
        dot.dataset.state = active > 0 ? "busy" : "idle";
        summary.textContent = prefix + (active > 0 ? `推理运行中 (${active})` : "在线空闲待命");
      }
    } else {
      if (wasShuttingDown) {
        appState.isShuttingDown = false;
        clearShutdownTimers();
        showNotice("Colab 实例已确认关机并释放算力。");
      }
      if (isShutdownOffline(snap)) {
        dot.dataset.state = "shutdown";
        summary.textContent = prefix + "实例已关机（已释放）";
      } else {
        dot.dataset.state = "offline";
        summary.textContent = prefix + "实例离线";
      }
    }

    const epInfo = snap.ep_path ? snap.ep_path.split("/").slice(-2).join("/") : "active_endpoint.json";
    const epUpdated = snap.endpoint_updated_at ? ` · 端点更新时间 ${snap.endpoint_updated_at}` : "";
    const authText = snap.has_api_key ? " · 只读鉴权已就绪" : " · 未提供 api_key";
    scope.textContent =
      prefix +
      `配置来源：${epInfo}${epUpdated}${authText} · 本地归档共 ${snap.total_output_files ?? 0} 条 · 显示时间按浏览器本地时区解析。`;

    if (isDemo) {
      banner.hidden = false;
      banner.dataset.severity = "warning";
      bannerTitle.textContent = "当前处于演示样本模式";
      bannerMsg.textContent = "以下 GPU、队列与出图记录均为内置静态样本，仅用于界面预览，不代表真实 Colab 实例。";
      retryBtn.textContent = "切回实时端点";
      demoBtn.hidden = true;
    } else if (!snap.online) {
      banner.hidden = false;
      if (isShutdownOffline(snap)) {
        banner.dataset.severity = "neutral";
        bannerTitle.textContent = "实例已关机";
        bannerMsg.textContent = snap.error || "Colab L4 实例已释放并停止计费。如需继续生图，请在 Colab 重新运行 Step 1–6。";
        retryBtn.textContent = "重新检测";
      } else {
        banner.dataset.severity = "critical";
        bannerTitle.textContent = "网关离线";
        bannerMsg.textContent = snap.error || "无法连接 /health；本地归档记录（如有）仍可只读浏览。";
        retryBtn.textContent = "重试连接";
      }
      demoBtn.hidden = false;
    } else if (!snap.metrics && snap.metrics_note) {
      banner.hidden = false;
      banner.dataset.severity = "warning";
      bannerTitle.textContent = "部分指标缺失";
      bannerMsg.textContent = snap.metrics_note;
      retryBtn.textContent = "刷新检测";
      demoBtn.hidden = false;
    } else {
      banner.hidden = true;
    }
  }

  function resolveCpuMetrics(snap) {
    if (!snap || !snap.online) return null;
    const rawCpu = snap.metrics?.cpu || appState.currentV2Instance?.hardware?.cpu || null;
    return rawCpu && (rawCpu.cores_logical || rawCpu.util_pct !== undefined) ? rawCpu : null;
  }

  function renderKpis(snap) {
    const h = snap.online ? snap.health : null;
    const m = snap.online ? snap.metrics : null;

    if (h) {
      const idleS = Number(h.idle_seconds ?? 0);
      const timeoutM = Number(h.idle_timeout_minutes ?? 0);
      if (timeoutM > 0) {
        const remM = Math.max(0, timeoutM - idleS / 60);
        setKpiValue("kpi-idle-rem", remM.toFixed(1), "分钟");
        document.getElementById("kpi-idle-help").textContent =
          (appState.isShuttingDown || snap.shutdown_pending)
            ? "已发送 /v1/shutdown 关机指令，正在等待实例同步落盘并释放…"
            : `已空闲 ${idleS}s (${(idleS / 60).toFixed(1)}m) / 上限 ${timeoutM}m · 活跃任务 ${h.active_jobs ?? 0} · 轮询不重置计时`;
      } else {
        setKpiValue("kpi-idle-rem", "—");
        document.getElementById("kpi-idle-help").textContent =
          (appState.isShuttingDown || snap.shutdown_pending)
            ? "已发送 /v1/shutdown 关机指令，正在等待实例同步落盘并释放…"
            : `活跃任务 ${h.active_jobs ?? 0} · 未提供 idle_timeout_minutes`;
      }
    } else {
      setKpiValue("kpi-idle-rem", "—");
      document.getElementById("kpi-idle-help").textContent = isShutdownOffline(snap)
        ? "实例已关机并释放算力，当前不消耗计算单元"
        : (snap.online ? "缺少 /health 数据" : "实例离线，无法获取空闲计时");
    }

    const gpuLabelEl = document.getElementById("kpi-gpu-label");
    const gpu = m && m.gpu ? m.gpu : null;
    const cpu = resolveCpuMetrics(snap);
    if (gpu && gpu.available) {
      if (gpuLabelEl) gpuLabelEl.textContent = "GPU 核心利用率";
      setKpiValue("kpi-gpu-util", formatNum(gpu.util_gpu_pct, 0), "%");
      document.getElementById("kpi-gpu-help").textContent =
        `${gpu.name || "GPU"} · 温度 ${gpu.temp_c ?? "—"}°C · 功耗 ${gpu.power_w ?? "—"}W`;

      setKpiValue("kpi-vram-pct", formatNum(gpu.vram_pct, 1), "%");
      document.getElementById("kpi-vram-help").textContent =
        `${integerFmt.format(gpu.vram_used_mib ?? 0)} / ${integerFmt.format(gpu.vram_total_mib ?? 0)} MiB`;
    } else if (snap.online && cpu) {
      if (gpuLabelEl) gpuLabelEl.textContent = "CPU 利用率";
      setKpiValue("kpi-gpu-util", formatNum(cpu.util_pct, 1), "%");
      document.getElementById("kpi-gpu-help").textContent =
        `${cpu.cores_logical ?? "—"} vCPU · 1m 负载 ${formatNum(cpu.load_avg_1m, 2)} · CPU Only 实例`;
      setKpiValue("kpi-vram-pct", "—");
      document.getElementById("kpi-vram-help").textContent = "CPU Only 实例（无独立 GPU）";
    } else {
      if (gpuLabelEl) gpuLabelEl.textContent = "GPU 核心利用率";
      setKpiValue("kpi-gpu-util", "—");
      document.getElementById("kpi-gpu-help").textContent = snap.online
        ? "等待或未启用 /v1/metrics"
        : (isShutdownOffline(snap) ? "实例已关机" : "实例离线");
      setKpiValue("kpi-vram-pct", "—");
      document.getElementById("kpi-vram-help").textContent = "显存数据不可用";
    }

    const ram = m && m.ram ? m.ram : null;
    const disk = m && m.disk ? m.disk : null;
    if (ram) {
      setKpiValue("kpi-ram-pct", formatNum(ram.pct, 1), "%");
      const diskPart = disk ? ` · 磁盘剩余 ${disk.free_gib ?? "—"}/${disk.total_gib ?? "—"} GiB` : "";
      document.getElementById("kpi-ram-help").textContent =
        `RAM ${ram.used_gib ?? "—"}/${ram.total_gib ?? "—"} GiB${diskPart}`;
    } else {
      setKpiValue("kpi-ram-pct", "—");
      document.getElementById("kpi-ram-help").textContent = "内存与磁盘数据不可用";
    }

    const jobs = m && m.jobs ? m.jobs : null;
    if (jobs) {
      setKpiValue("kpi-jobs-done", integerFmt.format(jobs.succeeded ?? 0), "次");
      document.getElementById("kpi-jobs-help").textContent =
        `运行中 ${jobs.running ?? 0} · 排队 ${jobs.queued ?? 0} · 失败 ${jobs.failed ?? 0}`;
    } else {
      setKpiValue("kpi-jobs-done", "—");
      document.getElementById("kpi-jobs-help").textContent = "任务队列统计不可用";
    }
  }

  function renderResourcesCard(snap) {
    const metersHost = document.getElementById("resource-meters");
    const kvHost = document.getElementById("gateway-kv");
    const tableBody = document.getElementById("resources-table-body");

    const h = snap.online ? snap.health : null;
    const m = snap.online ? snap.metrics : null;
    const gpu = m && m.gpu && m.gpu.available ? m.gpu : null;
    const cpu = resolveCpuMetrics(snap);
    const ram = m && m.ram ? m.ram : null;
    const disk = m && m.disk ? m.disk : null;

    const diskPct =
      disk && Number(disk.total_gib) > 0
        ? Math.min(100, Math.max(0, (Number(disk.used_gib) / Number(disk.total_gib)) * 100))
        : null;

    const idlePct =
      h && Number(h.idle_timeout_minutes) > 0
        ? Math.min(100, Math.max(0, (Number(h.idle_seconds || 0) / (Number(h.idle_timeout_minutes) * 60)) * 100))
        : null;

    const meterItems = [
      {
        name: "GPU 核心利用率",
        pct: gpu ? Number(gpu.util_gpu_pct) : null,
        text: gpu ? `${formatNum(gpu.util_gpu_pct, 0)}%` : "—"
      },
      {
        name: "GPU 显存 (VRAM)",
        pct: gpu ? Number(gpu.vram_pct) : null,
        text: gpu
          ? `${integerFmt.format(gpu.vram_used_mib)} / ${integerFmt.format(gpu.vram_total_mib)} MiB (${formatNum(gpu.vram_pct, 1)}%)`
          : "—"
      },
      {
        name: "CPU 利用率",
        pct: cpu ? Number(cpu.util_pct) : null,
        text: cpu
          ? `${formatNum(cpu.util_pct, 1)}% (${cpu.cores_logical ?? "—"} vCPU · 负载 ${formatNum(cpu.load_avg_1m, 2)})`
          : "—"
      },
      {
        name: "系统内存 (RAM)",
        pct: ram ? Number(ram.pct) : null,
        text: ram ? `${ram.used_gib} / ${ram.total_gib} GiB (${formatNum(ram.pct, 1)}%)` : "—"
      },
      {
        name: "磁盘空间 (Disk)",
        pct: diskPct,
        text: disk ? `${disk.used_gib} / ${disk.total_gib} GiB (剩余 ${disk.free_gib} GiB)` : "—"
      },
      {
        name: "空闲释放计时占比",
        pct: idlePct,
        text: h && h.idle_timeout_minutes
          ? `${h.idle_seconds ?? 0}s / ${h.idle_timeout_minutes * 60}s (${formatNum(idlePct, 1)}%)`
          : "—"
      }
    ];

    const meterNodes = meterItems.map((item) => {
      const row = el("div", undefined, "meter-row");
      const head = el("div", undefined, "meter-head");
      head.append(el("span", item.name, "meter-name"), el("span", item.text, "meter-val"));
      const track = el("div", undefined, "meter-track");
      track.setAttribute("aria-hidden", "true");
      const fill = el("div", undefined, "meter-fill");
      const clamped = item.pct !== null && !Number.isNaN(item.pct) ? Math.min(100, Math.max(0, item.pct)) : 0;
      fill.style.setProperty("--pct", clamped + "%");
      if (clamped >= 99.5) fill.classList.add("is-full");
      track.append(fill);
      row.append(head, track);
      return row;
    });
    metersHost.replaceChildren(...meterNodes);

    const kvData = [
      ["ComfyUI 存活", h ? String(h.comfy_alive) : (isShutdownOffline(snap) ? "已关机" : "—")],
      ["量化档位 (dit_tier)", h?.dit_tier ?? snap.endpoint_meta?.dit_tier ?? "—"],
      ["云盘模式 (gdrive_mode)", h?.gdrive_mode ?? "—"],
      ["CPU 规格 / 负载", cpu ? `${cpu.cores_logical ?? "—"} vCPU · 1m 负载 ${formatNum(cpu.load_avg_1m, 2)}` : "—"],
      ["GPU 型号", gpu?.name ?? "—"],
      ["GPU 温度 / 功耗", gpu ? `${gpu.temp_c ?? "—"}°C / ${gpu.power_w ?? "—"}W` : "—"],
      ["默认尺寸", snap.endpoint_meta?.default_size ?? "—"]
    ];

    const kvNodes = kvData.map(([k, v]) => {
      const wrap = el("div", undefined, "kv-item");
      wrap.append(el("dt", k), el("dd", v));
      return wrap;
    });
    kvHost.replaceChildren(...kvNodes);

    const exactRows = [
      ["active_jobs", "/health", h ? String(h.active_jobs ?? "—") : "—"],
      ["idle_seconds", "/health", h ? `${h.idle_seconds ?? "—"} s` : "—"],
      ["idle_timeout_minutes", "/health", h ? `${h.idle_timeout_minutes ?? "—"} min` : "—"],
      ["comfy_alive", "/health", h ? String(h.comfy_alive) : "—"],
      ["gpu.util_gpu_pct", "/v1/metrics", gpu ? `${gpu.util_gpu_pct}%` : "—"],
      ["gpu.vram", "/v1/metrics", gpu ? `${gpu.vram_used_mib} / ${gpu.vram_total_mib} MiB` : "—"],
      ["gpu.temp_c / power_w", "/v1/metrics", gpu ? `${gpu.temp_c}°C / ${gpu.power_w}W` : "—"],
      ["cpu", "/v2/hardware", cpu ? `${formatNum(cpu.util_pct, 1)}% (${cpu.cores_logical ?? "—"} vCPU, load1m ${formatNum(cpu.load_avg_1m, 2)})` : "—"],
      ["ram", "/v1/metrics", ram ? `${ram.used_gib} / ${ram.total_gib} GiB (${ram.pct}%)` : "—"],
      ["disk", "/v1/metrics", disk ? `${disk.used_gib} / ${disk.total_gib} GiB (free ${disk.free_gib} GiB)` : "—"]
    ];

    const trNodes = exactRows.map(([k, src, val]) => {
      const tr = el("tr");
      const th = el("th", k, "mono");
      th.scope = "row";
      tr.append(th, el("td", src, "mono"), el("td", val, "numeric"));
      return tr;
    });
    tableBody.replaceChildren(...trNodes);
  }

  function renderCompositionCard(snap) {
    const jobs = snap.online && snap.metrics ? snap.metrics.jobs : null;
    const capBar = document.getElementById("jobs-capacity-bar");
    const jobsLegend = document.getElementById("jobs-legend");
    const jobsTotalLabel = document.getElementById("jobs-total-label");

    if (jobs) {
      const buckets = [
        { id: "succeeded", label: "已完成", count: Number(jobs.succeeded || 0), color: "var(--s3)" },
        { id: "running", label: "运行中", count: Number(jobs.running || 0), color: "var(--s1)" },
        { id: "queued", label: "排队中", count: Number(jobs.queued || 0), color: "var(--s4)" },
        { id: "failed", label: "失败", count: Number(jobs.failed || 0), color: "var(--s5)" }
      ];
      const totalJobs = buckets.reduce((acc, b) => acc + b.count, 0);
      jobsTotalLabel.textContent = `总计 ${integerFmt.format(totalJobs)} 次`;

      const segNodes = [];
      for (const b of buckets) {
        if (b.count <= 0 || totalJobs <= 0) continue;
        const seg = el("div", undefined, "capacity-seg");
        seg.style.flexGrow = String(b.count);
        seg.style.setProperty("--seg-color", b.color);
        segNodes.push(seg);
      }
      capBar.replaceChildren(...segNodes);

      const legendNodes = buckets.map((b) => {
        const item = el("span", undefined, "legend-item");
        const sw = el("i", undefined, "swatch");
        sw.style.setProperty("--swatch-color", b.color);
        item.append(sw, document.createTextNode(`${b.label} ${integerFmt.format(b.count)}`));
        return item;
      });
      jobsLegend.replaceChildren(...legendNodes);
    } else {
      jobsTotalLabel.textContent = "—";
      capBar.replaceChildren();
      jobsLegend.replaceChildren(el("span", "等待 /v1/metrics 任务队列统计", "chart-caption"));
    }

    // Output mode distribution from recent_outputs
    const outputs = snap.recent_outputs || [];
    const distHost = document.getElementById("mode-distribution");
    const modeTableBody = document.getElementById("mode-table-body");
    const modeTableFoot = document.getElementById("mode-table-foot");
    const subtitle = document.getElementById("composition-subtitle");

    if (!outputs.length) {
      subtitle.textContent = "当前无本地归档样本";
      distHost.replaceChildren(el("div", "暂无 outputs/h3_*.json 记录", "chart-caption"));
      modeTableBody.replaceChildren();
      modeTableFoot?.replaceChildren();
      return;
    }

    const totalOutputs = outputs.length;
    subtitle.textContent = `分母 = 当前读取 ${totalOutputs} 条归档`;

    const grouped = new Map();
    for (const r of outputs) {
      const mode = r.mode || "未知";
      if (!grouped.has(mode)) {
        grouped.set(mode, { mode, count: 0, elapsedSum: 0, elapsedN: 0 });
      }
      const entry = grouped.get(mode);
      entry.count += 1;
      if (typeof r.elapsed_sec === "number" && !Number.isNaN(r.elapsed_sec)) {
        entry.elapsedSum += r.elapsed_sec;
        entry.elapsedN += 1;
      }
    }

    const sortedModes = [...grouped.values()].sort((a, b) => b.count - a.count);
    // Keep top 5 explicit modes; fold tail into "其他" if > 5
    let displayModes = sortedModes;
    if (sortedModes.length > 5) {
      const head = sortedModes.slice(0, 4);
      const tail = sortedModes.slice(4);
      const other = tail.reduce(
        (acc, x) => ({
          mode: "其他",
          count: acc.count + x.count,
          elapsedSum: acc.elapsedSum + x.elapsedSum,
          elapsedN: acc.elapsedN + x.elapsedN
        }),
        { mode: "其他", count: 0, elapsedSum: 0, elapsedN: 0 }
      );
      displayModes = [...head, other];
    }

    const distNodes = displayModes.map((m) => {
      const share = (m.count / totalOutputs) * 100;
      const avg = m.elapsedN > 0 ? (m.elapsedSum / m.elapsedN).toFixed(1) + "s" : "—";
      const wrap = el("div");
      const head = el("div", undefined, "dist-head");
      const lbl = el("span", undefined, "dist-label");
      const sw = el("i", undefined, "swatch");
      const token = m.mode === "其他" ? "other" : getSeriesToken(m.mode);
      sw.style.setProperty("--swatch-color", `var(--${token})`);
      lbl.append(sw, document.createTextNode(m.mode));
      head.append(lbl, el("span", `${m.count} 条 (${share.toFixed(1)}%) · 均值 ${avg}`, "dist-value"));

      const track = el("div", undefined, "track");
      track.setAttribute("aria-hidden", "true");
      const fill = el("div", undefined, "fill");
      fill.style.setProperty("--value", share.toFixed(2) + "%");
      fill.style.setProperty("--series", `var(--${token})`);
      track.append(fill);
      wrap.append(head, track);
      return wrap;
    });
    distHost.replaceChildren(...distNodes);

    const rowNodes = sortedModes.map((m) => {
      const share = (m.count / totalOutputs) * 100;
      const avg = m.elapsedN > 0 ? (m.elapsedSum / m.elapsedN).toFixed(2) : "—";
      const tr = el("tr");
      const th = el("th", m.mode);
      th.scope = "row";
      tr.append(
        th,
        el("td", integerFmt.format(m.count), "numeric"),
        el("td", share.toFixed(1) + "%", "numeric"),
        el("td", avg, "numeric")
      );
      return tr;
    });
    modeTableBody.replaceChildren(...rowNodes);

    if (modeTableFoot) {
      const totalElapsedSum = sortedModes.reduce((acc, m) => acc + m.elapsedSum, 0);
      const totalElapsedN = sortedModes.reduce((acc, m) => acc + m.elapsedN, 0);
      const totalAvg = totalElapsedN > 0 ? (totalElapsedSum / totalElapsedN).toFixed(2) : "—";
      const footTr = el("tr");
      const footTh = el("th", "合计");
      footTh.scope = "row";
      footTr.append(
        footTh,
        el("td", integerFmt.format(totalOutputs), "numeric"),
        el("td", "100.0%", "numeric"),
        el("td", totalAvg, "numeric")
      );
      modeTableFoot.replaceChildren(footTr);
    }
  }

  const UPTIME_RANGE_META = {
    all: { kvLabel: "全部累计开机", desc: "全部累计" },
    today: { kvLabel: "今日累计开机", desc: "今日" },
    "7d": { kvLabel: "近 7 天累计开机", desc: "近 7 天" },
    current: { kvLabel: "当前会话开机", desc: "当前会话" }
  };

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "—";
    const total = Math.max(0, Math.round(Number(seconds)));
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    if (mins < 60) {
      return `${mins}m ${String(secs).padStart(2, "0")}s`;
    }
    const hours = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hours}h ${String(remMins).padStart(2, "0")}m ${String(secs).padStart(2, "0")}s`;
  }

  function getHardwareToken(hardwareKey, category) {
    if (category === "cpu_only") {
      return String(hardwareKey || "").toLowerCase().includes("high-ram") ? "s5" : "s3";
    }
    if (category === "unknown" || hardwareKey === "其他 GPU") return "other";
    const key = String(hardwareKey || "").toUpperCase();
    if (key.includes("L4")) return "s1";
    if (key.includes("A100")) return "s2";
    if (key.includes("T4")) return "s4";
    if (key.includes("V100") || key.includes("H100") || key.includes("RTX") || key.includes("G4")) return "s5";
    const pool = ["s1", "s2", "s4", "s5"];
    let hash = 0;
    for (let i = 0; i < key.length; i++) {
      hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
    }
    return pool[hash % pool.length];
  }

  function getCategoryLabel(category) {
    if (category === "gpu") return "GPU";
    if (category === "cpu_only") return "CPU Only";
    return "待识别";
  }

  function createUptimeBadge(text, isOnline = false) {
    const badge = el("span", undefined, "uptime-badge");
    if (isOnline) {
      badge.dataset.state = "online";
      const dot = el("i", undefined, "uptime-badge-dot");
      dot.setAttribute("aria-hidden", "true");
      badge.append(dot);
    }
    badge.append(document.createTextNode(text));
    return badge;
  }

  function renderUptimeCard(snap) {
    const subtitle = document.getElementById("uptime-subtitle");
    const summaryHost = document.getElementById("uptime-summary-kv");
    const distTitle = document.getElementById("uptime-dist-title");
    const totalLabel = document.getElementById("uptime-total-label");
    const capBar = document.getElementById("uptime-capacity-bar");
    const legendHost = document.getElementById("uptime-legend");
    const distHost = document.getElementById("uptime-distribution");
    const caption = document.getElementById("uptime-caption");
    const tableBody = document.getElementById("uptime-table-body");
    const tableFoot = document.getElementById("uptime-table-foot");

    const range = Object.hasOwn(UPTIME_RANGE_META, appState.uptimeRange) ? appState.uptimeRange : "all";
    const metric = appState.uptimeMetric === "units" ? "units" : "time";
    const meta = UPTIME_RANGE_META[range];
    for (const btn of document.querySelectorAll("[data-uptime-range]")) {
      btn.setAttribute("aria-pressed", String(btn.dataset.uptimeRange === range));
    }
    for (const btn of document.querySelectorAll("[data-uptime-metric]")) {
      btn.setAttribute("aria-pressed", String(btn.dataset.uptimeMetric === metric));
    }
    if (distTitle) {
      distTitle.textContent = metric === "units"
        ? "各硬件积分消耗构成（GPU 型号与标准 / 高内存 CPU Only）"
        : "各硬件开机时长构成（GPU 型号与标准 / 高内存 CPU Only）";
    }

    const summary = snap?.uptime_summary;
    const win = summary?.windows?.[range];
    if (!summary || !win) {
      subtitle.textContent = "暂无开机时间与积分账本数据";
      const emptyKvs = [
        [meta.kvLabel, "—"],
        ["所选周期积分消耗", "—"],
        ["GPU 机型合计", "—"],
        ["CPU Only 合计", "—"],
        ["账号剩余积分 / 实时费率", "—"],
        ["当前实例 / 会话", "—"]
      ].map(([k, v]) => {
        const wrap = el("div", undefined, "kv-item");
        wrap.append(el("dt", k), el("dd", v));
        return wrap;
      });
      summaryHost.replaceChildren(...emptyKvs);
      totalLabel.textContent = "—";
      capBar.replaceChildren();
      legendHost.replaceChildren(el("span", "等待服务端返回 uptime_summary", "chart-caption"));
      distHost.replaceChildren(el("div", "暂无可展示的分 GPU / CPU Only 开机与积分记录", "chart-caption"));
      tableBody.replaceChildren();
      tableFoot?.replaceChildren();
      caption.textContent = "分母为所选统计周期内的总在线时长或总积分；无 GPU 实例按内存区分 CPU Only（标准 ≈0.10 CU/h）与 CPU Only (High-RAM ≈1.20 CU/h)，离线空窗期不计入。";
      return;
    }

    const totalSec = Number(win.total_seconds || 0);
    const gpuSec = Number(win.gpu_seconds || 0);
    const cpuSec = Number(win.cpu_only_seconds || 0);
    const gpuPct = totalSec > 0 ? ((gpuSec / totalSec) * 100).toFixed(1) : "0.0";
    const cpuPct = totalSec > 0 ? ((cpuSec / totalSec) * 100).toFixed(1) : "0.0";
    const totalCu = Number(win.total_compute_units || 0);
    const gpuCu = Number(win.gpu_compute_units || 0);
    const cpuCu = Number(win.cpu_only_compute_units || 0);
    const sessCnt = Number(win.session_count || 0);
    const curHwText = summary.current_online
      ? `${summary.current_hardware || "待识别硬件"} (在线)`
      : (isShutdownOffline(snap) ? "当前已关机" : "当前离线");

    const ccu = summary.compute_units_summary || {};
    const accBal = typeof ccu.account_balance === "number" ? ccu.account_balance : null;
    const liveRate = typeof ccu.live_rate_hourly === "number" ? ccu.live_rate_hourly : null;
    const remHours = typeof ccu.estimated_remaining_hours === "number" ? ccu.estimated_remaining_hours : null;
    const accText = accBal !== null
      ? `${accBal.toFixed(2)} CU · ${(liveRate ?? 0).toFixed(2)}/hr${remHours ? ` · 约 ${remHours}h` : ""}`
      : "未接入 CLI 余额";

    const ledgerPrefix = snap.is_demo
      ? "[演示样本] "
      : `账本 ${summary.ledger_file || "uptime_ledger.json"} · `;
    subtitle.textContent =
      `${ledgerPrefix}${meta.desc} · 累计 ${integerFmt.format(sessCnt)} 次会话 · 更新于 ${formatLocalTime(summary.updated_at)}`;

    const kvData = [
      [meta.kvLabel, formatDuration(totalSec)],
      ["所选周期积分消耗", `${totalCu.toFixed(2)} CU (GPU ${gpuCu.toFixed(2)} · CPU ${cpuCu.toFixed(2)})`],
      ["GPU 机型合计", `${formatDuration(gpuSec)} (${gpuPct}%) · ${gpuCu.toFixed(2)} CU`],
      ["CPU Only 合计", `${formatDuration(cpuSec)} (${cpuPct}%) · ${cpuCu.toFixed(2)} CU`],
      ["账号剩余积分 / 实时费率", accText],
      ["当前实例 / 会话", `${curHwText} · ${integerFmt.format(sessCnt)} 次会话`]
    ];
    const kvNodes = kvData.map(([k, v]) => {
      const wrap = el("div", undefined, "kv-item");
      wrap.append(el("dt", k), el("dd", v));
      return wrap;
    });
    summaryHost.replaceChildren(...kvNodes);

    totalLabel.textContent = `总计 ${formatDuration(totalSec)}（${integerFmt.format(Math.round(totalSec))}s） · ${totalCu.toFixed(2)} CU`;

    const rawItems = Array.isArray(win.items) ? win.items : [];
    const items = rawItems.map((it) => {
      const cu = Number(it.compute_units || 0);
      const cuShare = it.compute_units_share_pct !== undefined
        ? Number(it.compute_units_share_pct || 0)
        : (totalCu > 0 ? Number(((cu / totalCu) * 100).toFixed(1)) : 0);
      return {
        ...it,
        compute_units: cu,
        compute_units_share_pct: cuShare,
        rate_cu_per_hour: Number(it.rate_cu_per_hour || 0),
        rate_source: it.rate_source || "estimate"
      };
    });

    const metricTotal = metric === "units" ? totalCu : totalSec;
    const metricVal = (it) => (metric === "units" ? it.compute_units : Number(it.seconds || 0));
    const activeItems = items.filter((item) => metricVal(item) > 0);
    if (metricTotal > 0 && activeItems.length > 0) {
      const segNodes = activeItems.map((item) => {
        const seg = el("div", undefined, "capacity-seg");
        seg.style.flexGrow = String(Math.max(0.01, metricVal(item)));
        seg.style.setProperty("--seg-color", `var(--${getHardwareToken(item.hardware_key, item.category)})`);
        return seg;
      });
      capBar.replaceChildren(...segNodes);

      const legendNodes = activeItems.map((item) => {
        const legendItem = el("span", undefined, "legend-item");
        const sw = el("i", undefined, "swatch");
        sw.style.setProperty("--swatch-color", `var(--${getHardwareToken(item.hardware_key, item.category)})`);
        const labelVal = metric === "units"
          ? `${item.hardware_key} ${item.compute_units.toFixed(2)} CU`
          : `${item.hardware_key} ${formatDuration(item.seconds)} (${item.compute_units.toFixed(2)} CU)`;
        legendItem.append(sw, document.createTextNode(labelVal));
        return legendItem;
      });
      legendHost.replaceChildren(...legendNodes);
    } else {
      capBar.replaceChildren();
      legendHost.replaceChildren(
        el("span", "当前统计周期暂无在线时长与积分消耗记录（GPU 与 CPU Only 均记为 0）", "chart-caption")
      );
    }

    // Keep CPU Only as a permanent first-class bar; fold >5 non-CPU entries into "其他 GPU"
    const cpuOnlyItems = items.filter((item) => item.category === "cpu_only");
    const nonCpuItems = items.filter((item) => item.category !== "cpu_only");
    let displayNonCpu = nonCpuItems;
    if (nonCpuItems.length > 5) {
      const head = nonCpuItems.slice(0, 4);
      const tail = nonCpuItems.slice(4);
      const otherSec = tail.reduce((acc, x) => acc + Number(x.seconds || 0), 0);
      const otherCu = tail.reduce((acc, x) => acc + Number(x.compute_units || 0), 0);
      const otherSess = tail.reduce((acc, x) => acc + Number(x.session_count || 0), 0);
      displayNonCpu = [
        ...head,
        {
          hardware_key: "其他 GPU",
          category: "gpu",
          seconds: otherSec,
          share_pct: totalSec > 0 ? Number(((otherSec / totalSec) * 100).toFixed(1)) : 0,
          compute_units: Number(otherCu.toFixed(2)),
          compute_units_share_pct: totalCu > 0 ? Number(((otherCu / totalCu) * 100).toFixed(1)) : 0,
          rate_cu_per_hour: otherSec > 0 ? Number(((otherCu * 3600) / otherSec).toFixed(2)) : 0,
          rate_source: "estimate",
          session_count: otherSess,
          is_current: tail.some((x) => Boolean(x.is_current)),
          last_seen_at: null
        }
      ];
    }

    const displayItems = [...displayNonCpu, ...cpuOnlyItems].sort((a, b) => {
      const diff = metricVal(b) - metricVal(a);
      if (diff !== 0) return diff;
      if (Boolean(b.is_current) !== Boolean(a.is_current)) return a.is_current ? -1 : 1;
      const rank = (cat) => (cat === "gpu" ? 0 : cat === "cpu_only" ? 1 : 2);
      return rank(a.category) - rank(b.category);
    });

    if (!displayItems.length) {
      distHost.replaceChildren(el("div", "暂无硬件运行时与积分记录", "chart-caption"));
    } else {
      const distNodes = displayItems.map((item) => {
        const sec = Number(item.seconds || 0);
        const cu = item.compute_units;
        const share = totalSec > 0 ? (sec / totalSec) * 100 : 0;
        const cuShare = totalCu > 0 ? (cu / totalCu) * 100 : 0;
        const barPct = metric === "units" ? cuShare : share;
        const token = getHardwareToken(item.hardware_key, item.category);

        const wrap = el("div");
        const head = el("div", undefined, "dist-head");
        const lbl = el("span", undefined, "dist-label");
        const sw = el("i", undefined, "swatch");
        sw.style.setProperty("--swatch-color", `var(--${token})`);
        lbl.append(sw, document.createTextNode(item.hardware_key));
        if (item.is_current) {
          lbl.append(createUptimeBadge("当前在线", true));
        }

        const valText = metric === "units"
          ? `${cu.toFixed(2)} CU (${Number(item.compute_units_share_pct ?? cuShare).toFixed(1)}%) · ${formatDuration(sec)} · ${item.rate_cu_per_hour.toFixed(2)}/h`
          : `${formatDuration(sec)} (${Number(item.share_pct ?? share).toFixed(1)}%) · ${cu.toFixed(2)} CU · ${integerFmt.format(item.session_count ?? 0)} 次`;
        head.append(lbl, el("span", valText, "dist-value"));

        const track = el("div", undefined, "track");
        track.setAttribute("aria-hidden", "true");
        const fill = el("div", undefined, "fill");
        fill.style.setProperty("--value", barPct.toFixed(2) + "%");
        fill.style.setProperty("--series", `var(--${token})`);
        if (metricVal(item) <= 0) {
          fill.style.minInlineSize = "0";
        }
        track.append(fill);
        wrap.append(head, track);
        return wrap;
      });
      distHost.replaceChildren(...distNodes);
    }

    const trNodes = items.map((item) => {
      const tr = el("tr");
      const th = el("th");
      th.scope = "row";
      const sw = el("i", undefined, "swatch");
      sw.style.setProperty("--swatch-color", `var(--${getHardwareToken(item.hardware_key, item.category)})`);
      sw.style.display = "inline-block";
      sw.style.marginInlineEnd = "6px";
      th.append(sw, document.createTextNode(item.hardware_key));
      if (item.is_current) {
        const badge = createUptimeBadge("当前在线", true);
        badge.style.marginInlineStart = "6px";
        th.append(badge);
      }

      const rateTag = item.rate_source === "live_ccu" ? "实时" : "估算";
      const rateText = `${item.rate_cu_per_hour.toFixed(2)} (${rateTag})`;

      tr.append(
        th,
        el("td", getCategoryLabel(item.category)),
        el("td", integerFmt.format(item.session_count ?? 0), "numeric"),
        el("td", formatDuration(item.seconds), "numeric"),
        el("td", `${Number(item.seconds ?? 0).toFixed(1)}s`, "numeric mono"),
        el("td", `${Number(item.share_pct ?? 0).toFixed(1)}%`, "numeric"),
        el("td", rateText, "numeric mono"),
        el("td", `${item.compute_units.toFixed(2)} CU`, "numeric mono"),
        el("td", `${item.compute_units_share_pct.toFixed(1)}%`, "numeric"),
        el("td", item.last_seen_at ? formatLocalTime(item.last_seen_at) : "—", "mono")
      );
      return tr;
    });
    tableBody.replaceChildren(...trNodes);

    if (tableFoot) {
      const footTr = el("tr");
      const footTh = el("th", "合计");
      footTh.scope = "row";
      const avgRate = totalSec > 0 ? `${((totalCu * 3600) / totalSec).toFixed(2)}` : "—";
      footTr.append(
        footTh,
        el("td", "—"),
        el("td", integerFmt.format(sessCnt), "numeric"),
        el("td", formatDuration(totalSec), "numeric"),
        el("td", `${totalSec.toFixed(1)}s`, "numeric mono"),
        el("td", totalSec > 0 ? "100.0%" : "0.0%", "numeric"),
        el("td", avgRate, "numeric mono"),
        el("td", `${totalCu.toFixed(2)} CU`, "numeric mono"),
        el("td", totalCu > 0 ? "100.0%" : "0.0%", "numeric"),
        el("td", "—", "mono")
      );
      tableFoot.replaceChildren(footTr);
    }

    if (metric === "units") {
      caption.textContent = range === "current"
        ? "分母为当前在线会话积分消耗；费率优先取单个在线会话下 colab usage 实时速率，其余按机型参考费率估算。"
        : `分母为「${meta.desc}」统计周期内的总积分消耗 (CU)；费率优先取实时 CCU 校准，其余按机型参考费率估算。`;
    } else {
      caption.textContent = range === "current"
        ? "分母为当前在线会话时长；若实例离线或已关机则显示 0s。无 GPU 实例归入 CPU Only。"
        : `分母为「${meta.desc}」统计周期内的总在线时长；无 GPU 实例归入 CPU Only，离线空窗期不计入。`;
    }
  }

  const MAX_CHART_BARS = 200;

  function renderElapsedChart(snap) {
    const yaxis = document.getElementById("elapsed-yaxis");
    const barsHost = document.getElementById("elapsed-bars");
    const xaxis = document.getElementById("elapsed-xaxis");
    const caption = document.getElementById("elapsed-caption");
    const visual = document.getElementById("trend-visual");

    // Chronological order (oldest -> newest), capped at latest MAX_CHART_BARS to bound DOM size
    const allRecent = snap.recent_outputs || [];
    const truncated = allRecent.length > MAX_CHART_BARS;
    const items = [...allRecent.slice(0, MAX_CHART_BARS)].reverse();
    const validElapsed = items
      .map((x) => (typeof x.elapsed_sec === "number" ? x.elapsed_sec : null))
      .filter((x) => x !== null);

    if (!items.length || !validElapsed.length) {
      yaxis.replaceChildren();
      barsHost.replaceChildren(el("div", "暂无包含 GPU 耗时的归档记录", "empty-state"));
      xaxis.replaceChildren();
      caption.textContent = "零基线，暂无可绘制的 elapsed_sec 样本。";
      return;
    }

    const rawMax = Math.max(...validElapsed);
    const step = rawMax <= 50 ? 10 : rawMax <= 120 ? 25 : 40;
    const ceiling = Math.max(step * 5, Math.ceil(rawMax / step) * step);
    // Align 5 grid lines (0%, 20%, 40%, 60%, 80%, 100%)
    const ticks = [0, 20, 40, 60, 80, 100].map((pos) => {
      const val = Math.round(ceiling * (1 - pos / 100));
      const span = el("span", `${val}s`);
      span.style.setProperty("--tick-y", pos + "%");
      return span;
    });
    yaxis.replaceChildren(...ticks);

    visual.style.minInlineSize = Math.max(24, items.length * 2.25) + "rem";
    xaxis.style.setProperty("--cols", String(items.length));

    const barNodes = [];
    const xNodes = [];

    for (const item of items) {
      const col = el("div", undefined, "bar-col");
      const btn = el("button", undefined, "bar-btn");
      btn.type = "button";
      btn.dataset.detailSource = "chart";
      btn.dataset.detailFile = item.file;
      const val = typeof item.elapsed_sec === "number" ? item.elapsed_sec : null;
      const label = `${item.file} (${item.mode}), ${item.size}, 耗时 ${val !== null ? val.toFixed(2) + " 秒" : "缺失"}`;
      btn.setAttribute("aria-label", label);

      if (val !== null) {
        const fill = el("span", undefined, "bar-fill");
        const pct = Math.max(1.5, Math.min(100, (val / ceiling) * 100));
        fill.style.setProperty("--h", pct.toFixed(2) + "%");
        fill.style.setProperty("--bar-color", `var(--${getSeriesToken(item.mode)})`);
        btn.append(fill);
      }

      const tipMeta = `${item.mode} · ${item.size} (${item.mp}MP) · refs=${item.refs} · 耗时 ${val !== null ? val.toFixed(2) + "s" : "—"}`;
      btn.addEventListener("mouseenter", () => showTooltip(btn, item.file, tipMeta));
      btn.addEventListener("mouseleave", hideTooltip);
      btn.addEventListener("focus", () => showTooltip(btn, item.file, tipMeta));
      btn.addEventListener("blur", hideTooltip);
      btn.addEventListener("click", () => openDetailDialog(item, btn, "chart"));

      col.append(btn);
      barNodes.push(col);
      const xLabel =
        item.created_at_full && item.created_at_full.includes("T")
          ? item.created_at_full.slice(11, 19)
          : item.created_at || item.file.slice(-10, -4);
      xNodes.push(el("span", xLabel));
    }

    barsHost.replaceChildren(...barNodes);
    xaxis.replaceChildren(...xNodes);

    const avg = validElapsed.reduce((a, b) => a + b, 0) / validElapsed.length;
    const capNote = truncated ? `（仅绘制最近 ${MAX_CHART_BARS} / 已加载 ${allRecent.length} 条）` : "";
    caption.textContent =
      `零基线，纵轴上限 ${ceiling}s；共 ${items.length} 条样本${capNote}（有效耗时 ${validElapsed.length} 条），平均耗时 ${avg.toFixed(2)}s，最大耗时 ${rawMax.toFixed(2)}s。`;
  }

  function syncModeFilterButtons(outputs) {
    const group = document.getElementById("mode-filter-group");
    const modes = [...new Set(outputs.map((r) => r.mode).filter(Boolean))].sort();
    if (appState.modeFilter !== "all" && !modes.includes(appState.modeFilter)) {
      appState.modeFilter = "all";
    }
    const buttons = [
      { value: "all", text: "全部模式" },
      ...modes.map((m) => ({ value: m, text: m }))
    ];
    const nodes = buttons.map(({ value, text }) => {
      const btn = el("button", text);
      btn.type = "button";
      btn.dataset.modeFilter = value;
      btn.setAttribute("aria-pressed", String(appState.modeFilter === value));
      btn.addEventListener("click", () => {
        appState.modeFilter = value;
        syncModeFilterButtons(appState.snapshot?.recent_outputs || []);
        renderOutputsTable();
      });
      return btn;
    });
    group.replaceChildren(...nodes);
    updateSegIndicator(group, true);
  }

  function updateSortHeaders() {
    const map = {
      file: "th-file",
      mode: "th-mode",
      mp: "th-mp",
      refs: "th-refs",
      elapsed_sec: "th-elapsed",
      created_at_full: "th-created"
    };
    for (const [key, thId] of Object.entries(map)) {
      const th = document.getElementById(thId);
      if (!th) continue;
      if (key === appState.sortKey) {
        th.setAttribute("aria-sort", appState.sortDir === "asc" ? "ascending" : "descending");
      } else {
        th.setAttribute("aria-sort", "none");
      }
    }
  }

  function renderOutputsTable() {
    const tbody = document.getElementById("outputs-table-body");
    const empty = document.getElementById("outputs-empty");
    const countLabel = document.getElementById("outputs-count-label");
    const all = appState.snapshot?.recent_outputs || [];

    const query = appState.searchQuery.trim().toLowerCase();
    const filtered = all.filter((r) => {
      if (appState.modeFilter !== "all" && r.mode !== appState.modeFilter) return false;
      if (!query) return true;
      const hay = `${r.file} ${r.mode} ${r.dit_tier || ""} ${r.prompt || ""}`.toLowerCase();
      return hay.includes(query);
    });

    const sorted = [...filtered].sort((a, b) => {
      const k = appState.sortKey;
      const av = a[k] ?? "";
      const bv = b[k] ?? "";
      let cmp = 0;
      if (typeof av === "number" && typeof bv === "number") {
        cmp = av - bv;
      } else {
        cmp = String(av).localeCompare(String(bv), "zh-CN");
      }
      return appState.sortDir === "asc" ? cmp : -cmp;
    });

    updateSortHeaders();
    const totalArchive = appState.snapshot?.total_output_files ?? all.length;
    countLabel.textContent = `当前显示 ${sorted.length} / 已加载 ${all.length} 条（云盘总计 ${totalArchive} 条）`;

    const archiveSummary = document.getElementById("archive-section-summary");
    const archiveDisclosure = document.getElementById("archive-cards-disclosure");
    if (archiveSummary) {
      archiveSummary.textContent = all.length > 0
        ? `云盘出图归档与耗时分析（${all.length} 条已加载 / 云盘总计 ${totalArchive} 条）`
        : "云盘出图归档与耗时分析（当前无归档样本，点击展开）";
    }
    if (archiveDisclosure && !appState.archiveUserToggled) {
      archiveDisclosure.open = all.length > 0;
    }

    const tableScroll = tbody.closest(".table-scroll");
    if (!sorted.length) {
      tbody.replaceChildren();
      if (tableScroll) tableScroll.hidden = true;
      empty.hidden = false;
      return;
    }

    if (tableScroll) tableScroll.hidden = false;
    empty.hidden = true;
    const rows = sorted.map((r) => {
      const tr = el("tr");
      const th = el("th", r.file, "mono");
      th.scope = "row";

      const modeTd = el("td");
      const sw = el("i", undefined, "swatch");
      sw.style.setProperty("--swatch-color", `var(--${getSeriesToken(r.mode)})`);
      sw.style.display = "inline-block";
      sw.style.marginInlineEnd = "6px";
      modeTd.append(sw, document.createTextNode(r.mode));

      const sizeText = r.mp ? `${r.size} (${r.mp}MP)` : r.size;
      const elapsedText = typeof r.elapsed_sec === "number" ? `${r.elapsed_sec.toFixed(2)}s` : "—";
      const timeText = r.created_at_full ? r.created_at_full.replace("T", " ").slice(0, 19) : (r.created_at || "—");

      const actionTd = el("td");
      const detailBtn = el("button", "详情", "ghost-button");
      detailBtn.type = "button";
      detailBtn.dataset.detailSource = "table";
      detailBtn.dataset.detailFile = r.file;
      detailBtn.setAttribute("aria-label", `查看 ${r.file} 详情`);
      detailBtn.addEventListener("click", () => openDetailDialog(r, detailBtn, "table"));
      actionTd.append(detailBtn);

      tr.append(
        th,
        modeTd,
        el("td", sizeText, "numeric"),
        el("td", String(r.refs ?? 0), "numeric"),
        el("td", elapsedText, "numeric"),
        el("td", r.dit_tier || "—", "mono"),
        el("td", timeText, "mono"),
        actionTd
      );
      return tr;
    });

    tbody.replaceChildren(...rows);
  }

  // --- Detail modal dialog (native <dialog> with focus restoration) ---
  function openDetailDialog(item, triggerEl, sourceKind = "") {
    appState.lastFocusedTrigger = triggerEl || document.activeElement;
    appState.lastFocusedTarget = item && item.file
      ? { file: item.file, source: sourceKind || triggerEl?.dataset?.detailSource || "" }
      : null;
    const dialog = document.getElementById("detail-dialog");
    document.getElementById("dialog-title").textContent = item.file;

    const img = document.getElementById("dialog-image");
    if (item.has_image && !appState.snapshot?.is_demo) {
      img.src = "/api/output-image?file=" + encodeURIComponent(item.file);
      img.alt = item.file + " 缩略预览";
      img.hidden = false;
    } else {
      img.removeAttribute("src");
      img.alt = "";
      img.hidden = true;
    }

    const kvItems = [
      ["生成模式", item.mode || "—"],
      ["分辨率", item.mp ? `${item.size} (${item.mp} MP)` : (item.size || "—")],
      ["参考图数", String(item.refs ?? 0)],
      ["GPU 耗时", typeof item.elapsed_sec === "number" ? `${item.elapsed_sec.toFixed(2)} s` : "—"],
      ["量化档位", item.dit_tier || "—"],
      ["Seed", item.seed !== undefined && item.seed !== null ? String(item.seed) : "—"],
      ["创建时间", item.created_at_full || item.created_at || "—"]
    ];
    const kvNodes = kvItems.map(([k, v]) => {
      const wrap = el("div");
      wrap.append(el("dt", k), el("dd", v));
      return wrap;
    });
    document.getElementById("dialog-kv").replaceChildren(...kvNodes);

    document.getElementById("dialog-prompt").textContent = item.prompt || "（无 Prompt 记录）";
    const paths = [item.local_path, item.drive_url].filter(Boolean).join(" | ");
    document.getElementById("dialog-paths").textContent = paths || "无附加路径信息";

    openModalDialog(dialog);
  }

  function closeDetailDialog() {
    closeTopLayerDialog(document.getElementById("detail-dialog"));
  }

  function syncActionButtons(snap) {
    const keepaliveBtn = document.getElementById("keepalive-now");
    const shutdownBtn = document.getElementById("shutdown-now");
    const isDemo = appState.sourceMode === "demo";
    const ready = isDemo ? appState.demoOnline : Boolean(snap && snap.online && snap.has_api_key);

    if (keepaliveBtn) {
      if (appState.isKeepingAlive) {
        keepaliveBtn.textContent = "保活中…";
        keepaliveBtn.setAttribute("aria-disabled", "true");
        keepaliveBtn.setAttribute("aria-busy", "true");
      } else {
        keepaliveBtn.textContent = "手动保活";
        keepaliveBtn.removeAttribute("aria-busy");
        keepaliveBtn.setAttribute("aria-disabled", String(!ready || appState.isShuttingDown));
      }
    }

    if (shutdownBtn) {
      if (appState.isShuttingDown || snap?.shutdown_pending) {
        shutdownBtn.textContent = "释放中…";
        shutdownBtn.setAttribute("aria-disabled", "true");
        shutdownBtn.setAttribute("aria-busy", "true");
      } else if ((isDemo && !appState.demoOnline) || (!isDemo && isShutdownOffline(snap))) {
        shutdownBtn.textContent = "已关机";
        shutdownBtn.removeAttribute("aria-busy");
        shutdownBtn.setAttribute("aria-disabled", "true");
      } else {
        shutdownBtn.textContent = "一键关机";
        shutdownBtn.removeAttribute("aria-busy");
        shutdownBtn.setAttribute("aria-disabled", String(!ready || appState.isKeepingAlive));
      }
    }
  }

  // --- v2.0 Control Plane UI helpers & rendering ---
  function hasCap(inst, cap) {
    return Boolean(inst && Array.isArray(inst.capabilities) && inst.capabilities.includes(cap));
  }

  function isV2Online(inst) {
    const st = inst?.liveness_state || "OFFLINE";
    return st === "ONLINE_LIVE" || st === "ONLINE_DRIVE_ONLY" || st === "DRAINING";
  }

  function livenessLabel(state) {
    const map = {
      ONLINE_LIVE: "在线（实时隧道）",
      ONLINE_DRIVE_ONLY: "在线（云盘信箱）",
      DRAINING: "排空停机等待中",
      DEGRADED_STALE: "心跳延迟",
      SHUTTING_DOWN: "释放中",
      OFFLINE: "已离线"
    };
    return map[state] || state || "未知";
  }

  function livenessDotState(state) {
    if (state === "ONLINE_LIVE" || state === "ONLINE_DRIVE_ONLY") return "busy";
    if (state === "DRAINING" || state === "DEGRADED_STALE") return "idle";
    if (state === "SHUTTING_DOWN") return "shutting-down";
    return "offline";
  }

  function isTunnelDegraded(inst) {
    return Boolean(
      inst &&
      !inst.is_legacy &&
      inst.tunnel?.enabled &&
      !inst.tunnel?.healthy &&
      (inst.liveness_state === "ONLINE_DRIVE_ONLY" || inst.liveness_state === "DEGRADED_STALE")
    );
  }

  function isDriveOnlyInstance(inst) {
    return Boolean(
      inst &&
      !inst.is_legacy &&
      (inst.liveness_state === "ONLINE_DRIVE_ONLY" ||
        inst.active_channel === "drive" ||
        (!inst.tunnel?.enabled && isV2Online(inst)))
    );
  }

  function renderInstanceBar() {
    const tabsHost = document.getElementById("instance-tabs");
    const badge = document.getElementById("active-channel-badge");
    const fleetBtn = document.getElementById("fleet-drain-all");
    const callout = document.getElementById("instance-mode-callout");
    const calloutPill = document.getElementById("instance-mode-pill");
    const calloutText = document.getElementById("instance-mode-text");
    const rebuildBannerBtn = document.getElementById("banner-rebuild-tunnel");
    if (!tabsHost) return;

    const list = appState.v2Instances || [];
    const cur = appState.currentV2Instance;

    if (!list.length) {
      tabsHost.replaceChildren(el("span", "未发现活跃实例（兼容单端点模式）", "chart-caption"));
      if (badge) {
        badge.textContent = "通道：Legacy / 离线";
        delete badge.dataset.channel;
        delete badge.dataset.tooltip;
      }
      if (fleetBtn) fleetBtn.hidden = true;
      if (callout) callout.hidden = true;
      if (rebuildBannerBtn) rebuildBannerBtn.hidden = true;
      return;
    }

    const tabNodes = list.map((inst) => {
      const btn = el("button", undefined, "instance-tab");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      const selected = cur && inst.instance_id === cur.instance_id;
      btn.setAttribute("aria-selected", String(Boolean(selected)));

      const dot = el("span", undefined, "status-dot");
      dot.dataset.state = livenessDotState(inst.liveness_state);
      dot.setAttribute("aria-hidden", "true");

      const hwLabel = inst.hardware?.gpu?.available
        ? (inst.hardware.gpu.name || "GPU")
        : (Number(inst.hardware?.ram?.total_gib || 0) >= 24 ? "CPU High-RAM" : "CPU");
      const prog = inst.workload?.progress;
      const progText = prog && prog.total > 0 ? ` · ${Math.round((prog.done / prog.total) * 100)}%` : "";
      const meta = el("span", `${hwLabel}${progText}`, "instance-tab-meta");

      let chPill = null;
      if (!inst.is_legacy && !inst.tunnel?.enabled) {
        chPill = el("span", "无隧道", "instance-tab-channel");
        chPill.dataset.channel = "drive";
        chPill.dataset.tooltip = "无 HTTPS 隧道：经 Google Drive 心跳同步监控与带外指令";
      } else if (isTunnelDegraded(inst)) {
        chPill = el("span", "隧道降级", "instance-tab-channel");
        chPill.dataset.channel = "degraded";
        chPill.dataset.tooltip = "HTTPS 隧道不可达，已降级为 Google Drive 带外信箱通道";
      } else if (inst.active_channel === "tunnel") {
        chPill = el("span", "HTTPS", "instance-tab-channel");
        chPill.dataset.channel = "tunnel";
      }

      btn.append(dot, document.createTextNode(inst.label || inst.instance_id), meta);
      if (chPill) btn.append(chPill);
      btn.addEventListener("click", () => {
        if (appState.selectedInstanceId === inst.instance_id) return;
        appState.selectedInstanceId = inst.instance_id;
        if (appState.sourceMode === "demo" && appState.demoV2State) {
          appState.demoV2State.selectedId = inst.instance_id;
        } else {
          appState.lastLogPayload = null;
          appState.lastProcesses = [];
        }
        loadSnapshot();
      });
      return btn;
    });
    tabsHost.replaceChildren(...tabNodes);

    if (badge) {
      const chMap = { tunnel: "通道 A · HTTPS 实时隧道", drive: "通道 B · Google Drive 信箱", none: "无可用控制通道" };
      const stText = cur ? livenessLabel(cur.liveness_state) : "未知";
      const chText = cur ? (chMap[cur.active_channel] || cur.active_channel || "—") : "—";
      if (cur && !cur.is_legacy && !cur.tunnel?.enabled && isV2Online(cur)) {
        badge.textContent = "在线（无 HTTPS 隧道 · Drive 带外）";
        badge.dataset.channel = "drive";
        badge.dataset.tooltip = "未开启 HTTPS 公网隧道，监控与控制指令均走 Google Drive 带外信箱（15–30s 刷新）";
      } else if (isTunnelDegraded(cur)) {
        badge.textContent = `${stText} · 通道 B · Google Drive 信箱`;
        badge.dataset.channel = "degraded";
        badge.dataset.tooltip = "HTTPS 实时隧道不可达，已自动降级为 Google Drive 带外通道";
      } else {
        badge.textContent = `${stText} · ${chText}`;
        badge.dataset.channel = cur?.active_channel || "none";
        delete badge.dataset.tooltip;
      }
    }

    const onlineCount = list.filter((i) => isV2Online(i)).length;
    if (fleetBtn) {
      fleetBtn.hidden = onlineCount < 2;
      fleetBtn.textContent = `全部排空关机 (${onlineCount})`;
    }

    const degraded = isTunnelDegraded(cur);
    const showCallout = Boolean(
      cur &&
      !cur.is_legacy &&
      (isV2Online(cur) || cur.liveness_state === "DEGRADED_STALE") &&
      (isDriveOnlyInstance(cur) || degraded)
    );
    if (showCallout && callout && calloutPill && calloutText) {
      callout.hidden = false;
      if (degraded) {
        callout.dataset.mode = "degraded";
        calloutPill.textContent = "隧道断流 · 已降级 Drive 信箱";
        calloutText.textContent =
          "原 HTTPS 实时隧道当前不可达，已自动切换至 Google Drive 带外心跳与指令信箱（约 15–60s 生效）；实时日志流与在线进程列表暂不可用。";
      } else {
        callout.dataset.mode = "drive-only";
        calloutPill.textContent = "无 HTTPS 隧道 · Drive 带外模式";
        calloutText.textContent =
          "当前实例未开启 HTTPS 公网隧道（offline_batch），硬件指标与阶段进度每 15–30s 通过 Google Drive 心跳同步，保活/关机指令经云盘信箱下发（约 15–30s 生效）；不支持实时日志流与在线进程列表，可用「日志快照转存」导出日志。";
      }
      if (rebuildBannerBtn) rebuildBannerBtn.hidden = !hasCap(cur, "network.rebuild_tunnel");
    } else {
      if (callout) callout.hidden = true;
      if (rebuildBannerBtn) rebuildBannerBtn.hidden = true;
    }
  }

  function renderCommandBar() {
    const bar = document.getElementById("command-bar");
    const listHost = document.getElementById("command-list");
    const summary = document.getElementById("command-bar-summary");
    if (!bar || !listHost) return;
    const cmds = (appState.v2Commands || []).slice(0, 5);
    if (!cmds.length) {
      bar.hidden = true;
      listHost.replaceChildren();
      return;
    }
    bar.hidden = false;
    const pendingCnt = cmds.filter((c) => ["queued", "accepted", "running"].includes(c.status)).length;
    if (summary) {
      summary.textContent = pendingCnt > 0 ? `有 ${pendingCnt} 条指令等待实例响应` : `最近 ${cmds.length} 条管控回执`;
    }
    const statusLabels = {
      queued: "已写入云盘信箱 → 等待响应",
      accepted: "已受理 → 执行中",
      running: "执行中",
      succeeded: "已完成",
      failed: "失败",
      timed_out: "超时未回执",
      rejected_expired: "已过期拒绝",
      rejected_unauthorized: "鉴权/验签拒绝",
      rejected_invalid: "参数/策略拒绝"
    };
    const nodes = cmds.map((c) => {
      const row = el("div", undefined, "command-item");
      const left = el("span", `${c.action} — ${c.message || ""}`);
      const pill = el("span", `${c.channel || "drive"} · ${statusLabels[c.status] || c.status}`, "command-pill mono");
      row.append(left, pill);
      return row;
    });
    listHost.replaceChildren(...nodes);
  }

  function renderControlOpsCard() {
    const inst = appState.currentV2Instance;
    const policyStatus = document.getElementById("lifecycle-policy-status");
    const svcSummary = document.getElementById("services-summary-label");
    const svcKv = document.getElementById("services-status-kv");
    const drainBtn = document.getElementById("drain-toggle");
    if (!policyStatus || !svcKv) return;

    const online = isV2Online(inst);
    const lc = inst?.lifecycle || {};
    const drainOn = Boolean(lc.drain_requested);

    if (drainBtn) {
      const canDrain = Boolean(online && (drainOn ? hasCap(inst, "lifecycle.cancel_drain") : hasCap(inst, "lifecycle.drain_shutdown")));
      drainBtn.setAttribute("aria-pressed", String(drainOn));
      drainBtn.setAttribute("aria-disabled", String(!canDrain));
      drainBtn.textContent = drainOn ? "取消排空" : "跑完即停";
    }

    if (!inst) {
      policyStatus.textContent = "当前策略：暂无在线实例";
      if (svcSummary) svcSummary.textContent = "—";
      svcKv.replaceChildren();
    } else {
      const tm = lc.idle_timeout_minutes;
      const tmText = tm === 0 ? "禁用自动释放" : `${tm ?? "—"} 分钟`;
      const leaseText = lc.keepalive_lease_until ? `租约至 ${formatLocalTime(lc.keepalive_lease_until)}` : "未开启租约";
      const drainText = drainOn ? "排空停机中" : "正常接单";
      policyStatus.textContent = `当前策略：空闲阈值 ${tmText} · ${leaseText} · ${drainText}${inst.is_legacy ? "（Legacy 实例仅支持保活/关机）" : ""}`;

      const svcs = inst.services || {};
      const drive = inst.hardware?.drive || {};
      const noTunnel = !inst.is_legacy && !inst.tunnel?.enabled;
      const kvData = [
        ["ComfyUI", svcs.comfyui ? `${svcs.comfyui.status}${svcs.comfyui.pid ? " (PID " + svcs.comfyui.pid + ")" : ""}` : "未注册"],
        ["Cloudflared", svcs.cloudflared ? svcs.cloudflared.status : (noTunnel ? "未启用（无隧道模式）" : (inst.tunnel?.healthy ? "running" : "stopped"))],
        ["Gateway", svcs.gateway ? svcs.gateway.status : (noTunnel ? "无公网网关 (offline_batch)" : (inst.tunnel?.enabled ? "online" : "offline_batch"))],
        ["Google Drive", drive.mode ? `${drive.mode} (${drive.healthy ? "正常" : "异常"})` : "—"]
      ];
      svcKv.replaceChildren(
        ...kvData.map(([k, v]) => {
          const wrap = el("div", undefined, "kv-item");
          wrap.append(el("dt", k), el("dd", v));
          return wrap;
        })
      );
      if (svcSummary) {
        svcSummary.textContent = inst.is_legacy ? "Legacy 模式" : `${Object.keys(svcs).length} 项服务`;
      }
    }

    // Enable/disable policy & service buttons by capabilities
    const canLease = Boolean(online && hasCap(inst, "lifecycle.set_keepalive_lease"));
    for (const btn of document.querySelectorAll("[data-lease-min]")) {
      btn.setAttribute("aria-disabled", String(!canLease));
    }
    const canIdle = Boolean(online && hasCap(inst, "lifecycle.set_idle_policy"));
    for (const btn of document.querySelectorAll("[data-idle-min]")) {
      btn.setAttribute("aria-disabled", String(!canIdle));
    }
    const capByOps = {
      "comfy-interrupt": "ops.service_control",
      "comfy-free-vram": "ops.service_control",
      "comfy-restart": "ops.service_control",
      "rebuild-tunnel": "network.rebuild_tunnel",
      "flush-verify": "storage.flush_and_verify",
      "cleanup-dry-run": "ops.cleanup_disk"
    };
    for (const btn of document.querySelectorAll("[data-ops-action]")) {
      const reqCap = capByOps[btn.dataset.opsAction];
      const ok = Boolean(online && reqCap && hasCap(inst, reqCap));
      btn.setAttribute("aria-disabled", String(!ok));
    }
  }

  function renderWorkloadOpsCard() {
    const inst = appState.currentV2Instance;
    const kvHost = document.getElementById("workload-progress-kv");
    const stageLbl = document.getElementById("workload-stage-label");
    const pctLbl = document.getElementById("workload-progress-pct");
    const fill = document.getElementById("workload-progress-fill");
    const curItem = document.getElementById("workload-current-item");
    if (!kvHost) return;

    const wl = inst?.workload || {};
    const prog = wl.progress || {};
    const done = Number(prog.done || 0);
    const total = Number(prog.total || 0);
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

    const kvData = [
      ["调度状态", wl.paused ? "已暂停下发" : (isV2Online(inst) ? "运行中" : "离线")],
      ["运行 / 排队", `${wl.running ?? "—"} / ${wl.queued ?? "—"}`],
      ["成功 / 失败", `${wl.succeeded ?? "—"} / ${wl.failed ?? "—"}`],
      ["预计剩余 (ETA)", typeof prog.eta_sec === "number" && prog.eta_sec > 0 ? formatDuration(prog.eta_sec) : "—"]
    ];
    kvHost.replaceChildren(
      ...kvData.map(([k, v]) => {
        const wrap = el("div", undefined, "kv-item");
        wrap.append(el("dt", k), el("dd", v));
        return wrap;
      })
    );
    if (stageLbl) stageLbl.textContent = `当前阶段：${prog.stage || "—"}`;
    if (pctLbl) pctLbl.textContent = total > 0 ? `${done} / ${total} (${pct}%)` : "—";
    if (fill) {
      fill.style.setProperty("--pct", `${pct}%`);
      fill.classList.toggle("is-full", pct >= 99.5);
    }
    if (curItem) curItem.textContent = `当前处理项：${prog.current_item || "—"}`;

    const online = isV2Online(inst);
    const btnStates = [
      ["workload-pause-btn", online && !wl.paused && hasCap(inst, "workload.pause_queue")],
      ["workload-resume-btn", online && Boolean(wl.paused) && hasCap(inst, "workload.resume_queue")],
      ["workload-reload-btn", online && hasCap(inst, "workload.reload_drive_queue")],
      ["workload-retry-failed-btn", online && hasCap(inst, "workload.queue_mutate")],
      ["workload-clear-pending-btn", online && hasCap(inst, "workload.queue_mutate")]
    ];
    for (const [id, enabled] of btnStates) {
      const b = document.getElementById(id);
      if (b) b.setAttribute("aria-disabled", String(!enabled));
    }
  }

  function renderInspectorLogs() {
    const box = document.getElementById("log-viewer-box");
    if (!box) return;
    const payload = appState.lastLogPayload;
    if (!payload) {
      box.textContent = isDriveOnlyInstance(appState.currentV2Instance)
        ? "当前实例为无 HTTPS 隧道（Drive 带外）模式，不支持实时日志流；可点击「日志快照转存」写入云盘后，再点击「拉取日志」读取最新云盘快照。"
        : "点击「拉取日志」或切换至「演示样本」查看白名单日志（自动脱敏密钥）。";
      return;
    }
    const q = (appState.logFilter || "").trim().toLowerCase();
    const lines = (payload.lines || []).filter((ln) => !q || String(ln).toLowerCase().includes(q));
    if (!lines.length) {
      box.textContent = payload.note || (q ? "无匹配该关键词的日志行。" : "暂无该白名单源的日志内容。");
      return;
    }
    const header = payload.truncated ? "[提示：已按 512 KiB / 2000 行上限截断，密钥已自动脱敏]\n" : "";
    box.textContent = header + lines.join("\n");
  }

  function renderInspectorProcesses() {
    const tbody = document.getElementById("processes-table-body");
    if (!tbody) return;
    const procs = appState.lastProcesses || [];
    if (!procs.length) {
      const tr = el("tr");
      const td = el(
        "td",
        isDriveOnlyInstance(appState.currentV2Instance)
          ? "当前实例为无 HTTPS 隧道（Drive 带外）模式，暂不支持在线进程列表（仅 HTTPS 实时隧道模式支持）。"
          : "暂无进程数据，请点击「刷新进程列表」（需 ONLINE_LIVE 隧道模式）。"
      );
      td.colSpan = 5;
      tr.append(td);
      tbody.replaceChildren(tr);
      return;
    }
    const rows = procs.map((p) => {
      const tr = el("tr");
      const actTd = el("td");
      if (p.killable && isV2Online(appState.currentV2Instance) && hasCap(appState.currentV2Instance, "ops.kill_process")) {
        const killBtn = el("button", "终止 (TERM)", "ghost-button danger-button");
        killBtn.type = "button";
        killBtn.addEventListener("click", () => {
          openActionConfirmDialog({
            title: `确认终止受管进程 PID ${p.pid}`,
            desc: `将向受管业务进程 ${p.name} (PID ${p.pid}) 发送 SIGTERM 信号。`,
            warning: "若该进程正在执行渲染或隧道转发，终止后将立即中断当前任务。",
            kv: [["PID", String(p.pid)], ["进程名", p.name], ["命令行", p.cmdline || "—"]],
            trigger: killBtn,
            onConfirm: () => dispatchV2Action("ops.kill_process", { pid: p.pid, create_time: p.create_time, signal: "TERM" })
          });
        });
        actTd.append(killBtn);
      } else {
        actTd.append(el("span", p.protected_reason || "系统保护", "proc-protected-badge"));
      }
      tr.append(
        el("td", String(p.pid), "numeric mono"),
        el("td", p.name || "—", "mono"),
        el("td", p.cmdline || "—", "mono"),
        el("td", formatNum(p.rss_mib, 1), "numeric"),
        actTd
      );
      return tr;
    });
    tbody.replaceChildren(...rows);
  }

  function openActionConfirmDialog(cfg) {
    const dlg = document.getElementById("action-confirm-dialog");
    if (!dlg) return;
    document.getElementById("action-confirm-title").textContent = cfg.title || "确认执行远程管控动作";
    document.getElementById("action-confirm-desc").textContent = cfg.desc || "确认执行该远程管控操作？";
    const warn = document.getElementById("action-confirm-warning");
    if (cfg.warning) {
      warn.textContent = cfg.warning;
      warn.hidden = false;
    } else {
      warn.textContent = "";
      warn.hidden = true;
    }
    const kvHost = document.getElementById("action-confirm-kv");
    kvHost.replaceChildren(
      ...(cfg.kv || []).map(([k, v]) => {
        const wrap = el("div");
        wrap.append(el("dt", k), el("dd", v));
        return wrap;
      })
    );
    appState.pendingConfirmAction = cfg.onConfirm || null;
    appState.lastConfirmTrigger = cfg.trigger || document.activeElement;
    openModalDialog(dlg);
  }

  function closeActionConfirmDialog() {
    closeTopLayerDialog(document.getElementById("action-confirm-dialog"));
  }

  async function fetchInspectorLogs() {
    const src = appState.logSource || "agent";
    if (appState.sourceMode === "demo") {
      if (!appState.demoV2State) appState.demoV2State = createDemoV2State();
      appState.lastLogPayload = { source: src, lines: appState.demoV2State.logs[src] || [], truncated: false, redacted: true };
      renderInspectorLogs();
      return;
    }
    const inst = appState.currentV2Instance;
    if (!inst) return;
    try {
      const resp = await fetch(`/api/v2/instances/${encodeURIComponent(inst.instance_id)}/logs?source=${encodeURIComponent(src)}&tail=200`, { cache: "no-store" });
      const data = await resp.json().catch(() => null);
      if (data) {
        appState.lastLogPayload = data;
        renderInspectorLogs();
      }
    } catch (err) {
      showNotice("拉取日志失败：" + (err?.message || err));
    }
  }

  async function fetchInspectorProcesses() {
    if (appState.sourceMode === "demo") {
      if (!appState.demoV2State) appState.demoV2State = createDemoV2State();
      appState.lastProcesses = appState.demoV2State.processes || [];
      renderInspectorProcesses();
      return;
    }
    const inst = appState.currentV2Instance;
    if (!inst) return;
    try {
      const resp = await fetch(`/api/v2/instances/${encodeURIComponent(inst.instance_id)}/processes?limit=20`, { cache: "no-store" });
      const data = await resp.json().catch(() => null);
      if (data && Array.isArray(data.processes)) {
        appState.lastProcesses = data.processes;
        renderInspectorProcesses();
      }
    } catch (err) {
      showNotice("拉取进程列表失败：" + (err?.message || err));
    }
  }

  async function dispatchV2Action(action, params = {}) {
    const inst = appState.currentV2Instance;
    if (!inst) {
      showNotice("当前无选中的在线实例。");
      return;
    }

    if (appState.sourceMode === "demo") {
      if (!appState.demoV2State) appState.demoV2State = createDemoV2State();
      const target = appState.demoV2State.instances.find((i) => i.instance_id === inst.instance_id);
      if (!target) return;
      const isDrive = target.active_channel === "drive";
      const cmdId = `cmd_demo_${Date.now()}`;
      const rec = {
        cmd_id: cmdId,
        instance_id: target.instance_id,
        action,
        channel: isDrive ? "drive" : "tunnel",
        status: isDrive ? "queued" : "succeeded",
        message: isDrive ? "[演示样本] 已写入云盘信箱，模拟 1.2s 后收到回执…" : `[演示样本] ${action} 已通过实时隧道生效。`
      };
      appState.demoV2State.commands.unshift(rec);
      appState.demoV2State.commands = appState.demoV2State.commands.slice(0, 10);

      const applyDemoEffect = () => {
        if (action === "lifecycle.keepalive") target.lifecycle.idle_seconds = 0;
        else if (action === "lifecycle.set_keepalive_lease") {
          target.lifecycle.keepalive_lease_until = params.duration_minutes > 0 ? new Date(Date.now() + params.duration_minutes * 60000).toISOString() : null;
        } else if (action === "lifecycle.set_idle_policy") {
          target.lifecycle.idle_timeout_minutes = params.idle_timeout_minutes;
        } else if (action === "lifecycle.drain_shutdown") {
          target.lifecycle.drain_requested = true;
          target.liveness_state = "DRAINING";
        } else if (action === "lifecycle.cancel_drain") {
          target.lifecycle.drain_requested = false;
          target.liveness_state = target.tunnel.enabled ? "ONLINE_LIVE" : "ONLINE_DRIVE_ONLY";
        } else if (action === "workload.pause_queue") {
          target.workload.paused = true;
        } else if (action === "workload.resume_queue") {
          target.workload.paused = false;
        } else if (action === "network.rebuild_tunnel") {
          target.tunnel.enabled = true;
          target.tunnel.healthy = true;
          target.tunnel.base_url = "https://demo-rebuilt-tunnel.trycloudflare.com";
          target.active_channel = "tunnel";
          target.liveness_state = "ONLINE_LIVE";
        } else if (action === "ops.kill_process") {
          appState.demoV2State.processes = appState.demoV2State.processes.filter((p) => p.pid !== params.pid);
        } else if (action === "ops.cleanup_disk" && params.dry_run) {
          rec.message = "[演示样本 Dry-Run] 预计可释放 1,420.5 MiB（18 个缓存文件，未触碰 Drive/outputs）。";
        }
        rec.status = "succeeded";
        if (isDrive) rec.message = `[演示样本] 云盘信箱回执成功：${action} 已生效。`;
        loadSnapshot();
        showNotice(rec.message);
      };

      if (isDrive) {
        appState.v2Commands = [...appState.demoV2State.commands];
        renderCommandBar();
        showNotice(rec.message);
        window.setTimeout(applyDemoEffect, 1200);
      } else {
        applyDemoEffect();
      }
      return;
    }

    try {
      const resp = await fetch(`/api/v2/instances/${encodeURIComponent(inst.instance_id)}/actions?limit=${appState.outputLimit}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Colab-Action": action
        },
        body: JSON.stringify({ action, params }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok) {
        showNotice(`[${data.channel || "v2"}] ${data.message || `${action} 已受理`}`);
        loadSnapshot();
      } else {
        showNotice(`操作失败 (${action})：` + (data?.message || data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice(`操作异常 (${action})：` + (err?.message || err));
    }
  }

  async function dispatchFleetDrainAll() {
    if (appState.sourceMode === "demo") {
      if (!appState.demoV2State) appState.demoV2State = createDemoV2State();
      for (const inst of appState.demoV2State.instances) {
        if (isV2Online(inst)) {
          inst.lifecycle.drain_requested = true;
          inst.liveness_state = "DRAINING";
        }
      }
      loadSnapshot();
      showNotice("[演示样本] 已向全部在线实例下发排空关机（跑完即停）。");
      return;
    }
    try {
      const resp = await fetch("/api/v2/fleet/actions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Colab-Action": "fleet.drain_all"
        },
        body: JSON.stringify({ action: "fleet.drain_all", params: { sync_drive: true, include_queued: false } }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data) {
        const total = (data.results || []).length;
        const okCnt = (data.results || []).filter((r) => r.ok).length;
        showNotice(`批量排空关机已下发：成功/入队 ${okCnt} / ${total} 台实例。`);
        loadSnapshot();
      } else {
        showNotice("批量排空失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice("批量排空异常：" + (err?.message || err));
    }
  }

  // --- Plan A: Notebook Launcher & Safe Idempotent Injector ---
  function notebookModeLabel(mode) {
    if (mode === "oauth_silent") return "OAuth 静默直连";
    if (mode === "fuse_mounted") return "FUSE 云盘挂载";
    return "独立挂载引导";
  }

  async function fetchNotebooks() {
    if (appState.sourceMode === "demo") {
      if (!appState.demoV2State) appState.demoV2State = createDemoV2State();
      appState.notebooks = appState.demoV2State.notebooks || [];
      appState.notebooksDriveEnabled = true;
      appState.notebooksDriveError = null;
      appState.notebooksLoaded = true;
      renderNotebooksCard();
      return;
    }
    try {
      const inc = appState.notebooksIncludeBackups ? "1" : "0";
      const resp = await fetch(`/api/v2/notebooks?include_backups=${inc}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      appState.notebooks = Array.isArray(data.notebooks) ? data.notebooks : [];
      appState.notebooksDriveEnabled = Boolean(data.drive_scan_enabled);
      appState.notebooksDriveError = data.drive_error || null;
      appState.notebooksLoaded = true;
      renderNotebooksCard();
    } catch (err) {
      showNotice("扫描云盘笔记本失败：" + (err?.message || err));
    }
  }

  function renderNotebooksCard() {
    const tbody = document.getElementById("notebooks-table-body");
    const empty = document.getElementById("notebooks-empty");
    const countLbl = document.getElementById("notebooks-count-label");
    const bakBtn = document.getElementById("notebooks-toggle-backups");
    if (!tbody) return;

    if (bakBtn) {
      bakBtn.setAttribute("aria-pressed", String(Boolean(appState.notebooksIncludeBackups)));
      bakBtn.textContent = appState.notebooksIncludeBackups ? "隐藏备份/副本" : "显示备份/副本";
    }

    const q = (appState.notebooksSearch || "").trim().toLowerCase();
    const all = appState.notebooks || [];
    const filtered = all.filter((nb) => {
      if (!q) return true;
      return `${nb.name} ${nb.display_path} ${nb.detected_mode} ${nb.source || ""}`.toLowerCase().includes(q);
    });

    const injectedCnt = all.filter((n) => n.injected).length;
    const driveHint = appState.notebooksDriveError
      ? " · Drive API 扫描失败"
      : appState.notebooksDriveEnabled
        ? " · Drive API 已接通"
        : "";
    if (countLbl) {
      countLbl.textContent = `显示 ${filtered.length} / 共 ${all.length} 个笔记本（已接入探针 ${injectedCnt} 个）${driveHint}`;
    }

    const disc = document.getElementById("notebooks-disclosure");
    const discText = document.getElementById("notebooks-disclosure-text");
    if (discText) {
      if (!appState.notebooksLoaded) {
        discText.textContent = disc?.open ? "收起笔记本列表（正在扫描…）" : "展开笔记本列表（正在扫描…）";
      } else {
        const prefix = disc?.open ? "收起笔记本列表" : "展开笔记本列表";
        discText.textContent = `${prefix} · 共 ${all.length} 个（已接入 ${injectedCnt} 个）${driveHint}`;
      }
    }

    const tableScroll = tbody.closest(".table-scroll");
    if (!filtered.length) {
      tbody.replaceChildren();
      if (tableScroll) tableScroll.hidden = true;
      if (empty) {
        empty.hidden = false;
        if (all.length > 0 && q) {
          empty.textContent = "未检索到匹配的 .ipynb 文件。";
        } else if (appState.notebooksDriveError) {
          empty.textContent = `OAuth2 Drive 扫描异常：${appState.notebooksDriveError}`;
        } else {
          empty.textContent = appState.notebooksDriveEnabled
            ? "未在 Google Drive 云盘与本地目录中发现 .ipynb 文件。"
            : "当前未配置 Google Drive 扫描或本地目录无 .ipynb 文件。";
        }
      }
      return;
    }
    if (tableScroll) tableScroll.hidden = false;
    if (empty) empty.hidden = true;

    const rows = filtered.map((nb) => {
      const tr = el("tr");
      const isDriveApi = nb.source === "drive_api" || String(nb.path || "").startsWith("gdrive://");

      const nameTh = el("th", undefined, "mono");
      nameTh.scope = "row";
      const srcBadge = el("span", isDriveApi ? "Drive API" : "本地", "nb-source-badge");
      srcBadge.dataset.source = isDriveApi ? "drive_api" : "local";
      nameTh.append(
        document.createTextNode(nb.name),
        srcBadge,
        el("span", `${nb.display_path} · ${nb.total_cells} 单元 · ${nb.size_kib} KiB`, "nb-path-sub")
      );

      const modeTd = el("td", notebookModeLabel(nb.detected_mode));

      const statusTd = el("td");
      const badge = el("span", nb.injected ? `已接入 v${nb.injected_version || "2.0"}` : "未接入", "nb-badge");
      badge.dataset.injected = String(Boolean(nb.injected));
      statusTd.append(badge);

      const urlTd = el("td");
      const urlText = isDriveApi
        ? "Drive 直达链接"
        : (nb.colab_url ? "已绑定 Colab 链接" : "未绑定（将打开 Drive 搜索）");
      urlTd.append(el("span", urlText, "chart-caption"));

      const opsTd = el("td");
      const opsWrap = el("div", undefined, "ops-btn-group");

      const launchBtn = el("button", nb.colab_url ? "一键唤起" : "Drive 定位", "ghost-button keepalive-button");
      launchBtn.type = "button";
      launchBtn.addEventListener("click", () => launchNotebook(nb));

      const bindBtn = el("button", nb.colab_url ? "改链接" : "绑定链接", "ghost-button");
      bindBtn.type = "button";
      bindBtn.addEventListener("click", () => openBindUrlDialog(nb, bindBtn));

      const injectBtn = el("button", nb.injected ? "升级注入" : "预览注入", "ghost-button");
      injectBtn.type = "button";
      injectBtn.addEventListener("click", () => openInjectPreviewDialog(nb, injectBtn));

      opsWrap.append(launchBtn, bindBtn, injectBtn);

      if (nb.injected) {
        const removeBtn = el("button", "移除注入", "ghost-button danger-button");
        removeBtn.type = "button";
        removeBtn.addEventListener("click", () => {
          openActionConfirmDialog({
            title: `确认移除 ${nb.name} 的探针注入`,
            desc: isDriveApi
              ? "将先在服务端本地备份原 .ipynb，再通过 Google Drive v3 API 原地移除带 colab_monitor_injected 标记的单元（保持链接 ID 不变）。"
              : "将先在纯本地目录备份当前 .ipynb，再仅删除带 colab_monitor_injected 标记的单元，原业务单元保持完全不变。",
            kv: [["笔记本", nb.display_path], ["当前单元数", String(nb.total_cells)]],
            trigger: removeBtn,
            onConfirm: () => applyRemoveInjection(nb)
          });
        });
        opsWrap.append(removeBtn);
      }

      opsTd.append(opsWrap);
      tr.append(nameTh, modeTd, statusTd, urlTd, opsTd);
      return tr;
    });
    tbody.replaceChildren(...rows);
  }

  async function launchNotebook(nb) {
    if (appState.sourceMode === "demo") {
      showNotice(
        nb.colab_url
          ? `[演示样本] 已模拟打开 ${nb.colab_url}（真实模式下打开后按 ⌘+F9 全部运行即可上线）。`
          : `[演示样本] 尚未绑定直达链接，真实模式将打开 Google Drive 搜索 ${nb.name}。`
      );
      return;
    }
    const isLoopback = ["127.0.0.1", "localhost", "::1", "[::1]"].includes(window.location.hostname);
    try {
      const resp = await fetch("/api/v2/notebooks/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Colab-Action": "notebooks.launch" },
        body: JSON.stringify({ action: "notebooks.launch", path: nb.path, open_browser: isLoopback }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok) {
        if (!isLoopback && data.launched_url) {
          try {
            const parsed = new URL(data.launched_url);
            if (parsed.protocol === "https:" && (parsed.hostname === "colab.research.google.com" || parsed.hostname === "drive.google.com")) {
              window.open(parsed.toString(), "_blank", "noopener");
            }
          } catch {
            // ignore invalid url
          }
        }
        showNotice(data.message || "已在浏览器中打开笔记本。");
      } else {
        showNotice("唤起失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice("唤起异常：" + (err?.message || err));
    }
  }

  // --- Headless Colab Provisioner (CLI + Pure OAuth2 Drive API) ---
  function clearProvisionerJobTimer() {
    if (appState.provisionerJobTimer) {
      window.clearTimeout(appState.provisionerJobTimer);
      appState.provisionerJobTimer = null;
    }
  }

  function renderProvisionerJob(job) {
    const box = document.getElementById("provisioner-job-status");
    const titleEl = document.getElementById("provisioner-job-title");
    const logEl = document.getElementById("provisioner-job-log");
    if (!box || !titleEl || !logEl) return;
    if (!job) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const stepLabels = {
      queued: "排队中",
      allocating_vm: "正在向 Colab 分配算力",
      uploading_agent: "正在上传 colab_agent.py",
      bootstrapping_oauth_agent: "正在注入纯 OAuth2 凭据并启动探针",
      completed: "开机成功",
      failed: "开机失败"
    };
    const stateLabel = stepLabels[job.step] || job.status || "执行中";
    titleEl.textContent = `${job.session_name || "Colab VM"} (${job.runtime_spec || "cpu"}) · ${stateLabel}`;
    const lines = Array.isArray(job.steps_log) ? [...job.steps_log] : [];
    if (job.error) lines.push(`错误：${job.error}`);
    if (job.instance_id) lines.push(`已接入实例：${job.instance_id}`);
    logEl.textContent = lines.join("\n") || "等待执行输出…";
  }

  function renderProvisioner() {
    const st = appState.provisionerStatus;
    const ccuBadge = document.getElementById("provisioner-ccu-badge");
    const cliBadge = document.getElementById("provisioner-cli-badge");
    const driveBadge = document.getElementById("provisioner-drive-badge");
    const bootBtn = document.getElementById("provisioner-boot-btn");
    const tbody = document.getElementById("provisioner-sessions-body");
    const empty = document.getElementById("provisioner-sessions-empty");

    if (ccuBadge) {
      const usage = st?.usage;
      if (!st) {
        ccuBadge.textContent = "积分余额：检测中";
        ccuBadge.dataset.injected = "false";
      } else if (usage && typeof usage.balance === "number") {
        const rate = Number(usage.rate_hourly || 0);
        const rem = typeof usage.estimated_remaining_hours === "number" && rate > 0
          ? ` (约 ${usage.estimated_remaining_hours}h)`
          : "";
        ccuBadge.textContent = `积分余额：${usage.balance.toFixed(2)} CU · ${rate.toFixed(2)}/hr${rem}`;
        ccuBadge.dataset.injected = "true";
      } else {
        ccuBadge.textContent = "积分余额：不可用";
        ccuBadge.dataset.injected = "false";
      }
    }

    if (cliBadge) {
      if (!st) {
        cliBadge.textContent = "CLI：检测中";
        cliBadge.dataset.injected = "false";
      } else if (st.cli_available && st.cli_auth_configured) {
        cliBadge.textContent = "CLI：已就绪";
        cliBadge.dataset.injected = "true";
      } else if (st.cli_available) {
        cliBadge.textContent = "CLI：待授权";
        cliBadge.dataset.injected = "false";
      } else {
        cliBadge.textContent = "CLI：未安装";
        cliBadge.dataset.injected = "false";
      }
    }

    if (driveBadge) {
      if (!st) {
        driveBadge.textContent = "OAuth2 Drive：检测中";
        driveBadge.dataset.injected = "false";
      } else if (st.drive_oauth_configured) {
        const healthy = !st.drive_oauth_health || st.drive_oauth_health.ok !== false;
        driveBadge.textContent = healthy ? `OAuth2 Drive：已就绪 (${st.drive_monitor_path || "_colab_monitor"})` : "OAuth2 Drive：异常";
        driveBadge.dataset.injected = String(healthy);
        if (!healthy && st.drive_oauth_health?.error) {
          driveBadge.dataset.tooltip = st.drive_oauth_health.error;
        } else {
          delete driveBadge.dataset.tooltip;
        }
      } else {
        driveBadge.textContent = "OAuth2 Drive：未配置";
        driveBadge.dataset.injected = "false";
      }
    }

    const isBusy = Boolean(appState.provisionerBooting || st?.busy);
    if (bootBtn) {
      const disabled = isBusy || (appState.sourceMode === "live" && (!st || !st.cli_available));
      bootBtn.disabled = disabled;
      bootBtn.setAttribute("aria-disabled", String(disabled));
      bootBtn.textContent = isBusy ? "开机中…" : "一键开机";
    }

    if (!tbody) return;
    const tableScroll = tbody.closest(".table-scroll");
    const sessions = Array.isArray(st?.sessions) ? st.sessions : [];
    if (!sessions.length) {
      tbody.replaceChildren();
      if (tableScroll) tableScroll.hidden = true;
      if (empty) {
        empty.hidden = false;
        empty.textContent = st?.sessions_error ? `获取会话列表失败：${st.sessions_error}` : "当前无运行中的 Colab CLI 会话。";
      }
      return;
    }
    if (tableScroll) tableScroll.hidden = false;
    if (empty) empty.hidden = true;

    const rows = sessions.map((sess) => {
      const tr = el("tr");
      const nameTh = el("th", sess.name, "mono");
      nameTh.scope = "row";

      const epTd = el("td", sess.endpoint || "—", "mono");
      const hwTd = el("td", `${sess.hardware || "CPU"} · ${sess.shape || "Standard"} (${sess.variant || "DEFAULT"})`);

      const instTd = el("td");
      if (sess.instance_id) {
        const badge = el("span", `${sess.instance_id}`, "nb-badge");
        badge.dataset.injected = "true";
        instTd.append(badge);
      } else {
        instTd.append(el("span", sess.is_orphan ? "孤儿会话 (未关联)" : "未注入探针", "chart-caption"));
      }

      const opsTd = el("td");
      const wrap = el("div", undefined, "ops-btn-group");

      const urlBtn = el("button", "浏览器打开", "ghost-button");
      urlBtn.type = "button";
      urlBtn.addEventListener("click", () => openProvisionerSessionUrl(sess.name));
      wrap.append(urlBtn);

      if (sess.instance_id && (appState.v2Instances || []).some((i) => i.instance_id === sess.instance_id)) {
        const focusBtn = el("button", "查看监控", "ghost-button keepalive-button");
        focusBtn.type = "button";
        focusBtn.addEventListener("click", () => {
          appState.selectedInstanceId = sess.instance_id;
          loadSnapshot();
          showNotice(`已切换至实例 ${sess.instance_id}`);
        });
        wrap.append(focusBtn);
      }

      if (!sess.is_orphan) {
        const stopBtn = el("button", "停止 VM", "ghost-button danger-button");
        stopBtn.type = "button";
        stopBtn.addEventListener("click", () => {
          openActionConfirmDialog({
            title: `确认释放 Colab 会话 ${sess.name}`,
            desc: "将调用 colab stop 立即终止并回收该远端 VM，未保存至 Google Drive 的 /content 本地数据将丢失。",
            kv: [
              ["会话名", sess.name],
              ["Endpoint", sess.endpoint || "—"],
              ["硬件规格", `${sess.hardware || "CPU"} · ${sess.shape || "Standard"}`]
            ],
            trigger: stopBtn,
            onConfirm: () => stopProvisionerSession(sess.name)
          });
        });
        wrap.append(stopBtn);
      }

      opsTd.append(wrap);
      tr.append(nameTh, epTd, hwTd, instTd, opsTd);
      return tr;
    });
    tbody.replaceChildren(...rows);
  }

  async function loadProvisionerStatus(options = {}) {
    const refresh = Boolean(options && options.refresh);
    if (appState.sourceMode === "demo") {
      if (!appState.demoV2State) appState.demoV2State = createDemoV2State();
      appState.provisionerStatus = appState.demoV2State.provisioner;
      renderProvisioner();
      return;
    }
    try {
      const resp = await fetch(`/api/v2/provisioner/status?refresh=${refresh ? "1" : "0"}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      appState.provisionerStatus = data;
      if (data.active_job && (data.active_job.status === "queued" || data.active_job.status === "running")) {
        appState.provisionerBooting = true;
        appState.provisionerActiveJobId = data.active_job.job_id;
        renderProvisionerJob(data.active_job);
        if (!appState.provisionerJobTimer) {
          pollProvisionerJob(data.active_job.job_id);
        }
      } else if (!appState.provisionerBooting && Array.isArray(data.recent_jobs) && data.recent_jobs.length && appState.provisionerActiveJobId) {
        const match = data.recent_jobs.find((j) => j.job_id === appState.provisionerActiveJobId);
        if (match) renderProvisionerJob(match);
      }
      renderProvisioner();
    } catch (err) {
      showNotice("读取无头开机状态失败：" + (err?.message || err));
    }
  }

  function scheduleProvisionerJobPoll(jobId, delayMs = 2000) {
    clearProvisionerJobTimer();
    appState.provisionerJobTimer = window.setTimeout(() => {
      pollProvisionerJob(jobId);
    }, delayMs);
  }

  async function pollProvisionerJob(jobId) {
    clearProvisionerJobTimer();
    if (appState.sourceMode !== "live" || !jobId) return;
    try {
      const resp = await fetch(`/api/v2/provisioner/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const job = await resp.json();
      renderProvisionerJob(job);
      if (job.status === "queued" || job.status === "running") {
        appState.provisionerBooting = true;
        renderProvisioner();
        scheduleProvisionerJobPoll(jobId, 2000);
        return;
      }
      appState.provisionerBooting = false;
      await loadProvisionerStatus({ refresh: true });
      if (job.status === "succeeded") {
        if (job.instance_id) appState.selectedInstanceId = job.instance_id;
        showNotice(job.instance_id ? `一键开机完成，实例 ${job.instance_id} 已上线。` : `会话 ${job.session_name} 开机完成。`);
        loadSnapshot();
      } else {
        showNotice("一键开机失败：" + (job.error || "未知错误"));
      }
    } catch (err) {
      appState.provisionerBooting = false;
      renderProvisioner();
      showNotice("查询开机进度异常：" + (err?.message || err));
    }
  }

  async function startProvisionerBoot() {
    const rtRaw = document.getElementById("provisioner-runtime")?.value || "cpu";
    const sessionInput = (document.getElementById("provisioner-session")?.value || "").trim();
    const idleVal = Number(document.getElementById("provisioner-idle")?.value ?? 60);
    let highMem = Boolean(document.getElementById("provisioner-high-mem")?.checked);
    const attachAgent = Boolean(document.getElementById("provisioner-attach-agent")?.checked);

    let runtimeType = "cpu";
    let gpu = "";
    let tpu = "";
    if (rtRaw === "cpu:high-mem") {
      runtimeType = "cpu";
      highMem = true;
    } else if (rtRaw.startsWith("gpu:")) {
      runtimeType = "gpu";
      gpu = rtRaw.slice(4);
    } else if (rtRaw.startsWith("tpu:")) {
      runtimeType = "tpu";
      tpu = rtRaw.slice(4);
    }

    if (appState.sourceMode === "demo") {
      if (!appState.demoV2State) appState.demoV2State = createDemoV2State();
      const sname = sessionInput || `demo-${gpu || tpu || "cpu"}-01`;
      const demoJob = {
        job_id: "demo_boot_1",
        status: "succeeded",
        step: "completed",
        session_name: sname,
        runtime_spec: rtRaw + (highMem ? "+high-mem" : ""),
        instance_id: "demo_inst_l4_01",
        steps_log: [
          `[演示样本] 正在向 Colab 分配算力 (${sname})…`,
          "[演示样本] 已上传 colab_agent.py 并注入纯 OAuth2 凭据",
          "[演示样本] 探针已上线 (oauth_silent)"
        ]
      };
      renderProvisionerJob(demoJob);
      showNotice(`[演示样本] 已模拟无头开机 ${sname}。`);
      return;
    }

    appState.provisionerBooting = true;
    renderProvisioner();
    try {
      const resp = await fetch("/api/v2/provisioner/boot", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Colab-Action": "provisioner.boot" },
        body: JSON.stringify({
          action: "provisioner.boot",
          runtime_type: runtimeType,
          gpu,
          tpu,
          high_mem: highMem,
          session_name: sessionInput,
          idle_timeout_minutes: Number.isFinite(idleVal) ? idleVal : 60,
          attach_agent: attachAgent
        }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if ((resp.status === 202 || resp.ok) && data && data.job_id) {
        appState.provisionerActiveJobId = data.job_id;
        renderProvisionerJob(data);
        showNotice(`已提交开机任务：${data.session_name}`);
        scheduleProvisionerJobPoll(data.job_id, 1200);
      } else {
        appState.provisionerBooting = false;
        renderProvisioner();
        showNotice("发起开机失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      appState.provisionerBooting = false;
      renderProvisioner();
      showNotice("发起开机异常：" + (err?.message || err));
    }
  }

  async function openProvisionerSessionUrl(sessionName) {
    if (appState.sourceMode === "demo") {
      showNotice(`[演示样本] 将在新标签页打开会话 ${sessionName} 的 Colab Web UI。`);
      return;
    }
    try {
      const resp = await fetch("/api/v2/provisioner/url", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Colab-Action": "provisioner.url" },
        body: JSON.stringify({ action: "provisioner.url", session_name: sessionName }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok && data.url) {
        const parsed = new URL(data.url);
        if (parsed.protocol === "https:" && parsed.hostname === "colab.research.google.com") {
          window.open(parsed.toString(), "_blank", "noopener");
          showNotice(`已在新标签页打开 ${sessionName}。`);
          return;
        }
      }
      showNotice("获取 Colab 链接失败：" + (data?.error || `HTTP ${resp.status}`));
    } catch (err) {
      showNotice("获取 Colab 链接异常：" + (err?.message || err));
    }
  }

  async function stopProvisionerSession(sessionName) {
    if (appState.sourceMode === "demo") {
      if (appState.demoV2State?.provisioner) {
        appState.demoV2State.provisioner.sessions = appState.demoV2State.provisioner.sessions.filter(
          (s) => s.name !== sessionName
        );
        renderProvisioner();
      }
      showNotice(`[演示样本] 已释放会话 ${sessionName}。`);
      return;
    }
    try {
      const resp = await fetch("/api/v2/provisioner/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Colab-Action": "provisioner.stop" },
        body: JSON.stringify({ action: "provisioner.stop", session_name: sessionName }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok) {
        showNotice(data.message || `已释放会话 ${sessionName}。`);
        await loadProvisionerStatus({ refresh: true });
        loadSnapshot();
      } else {
        showNotice("停止会话失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice("停止会话异常：" + (err?.message || err));
    }
  }

  async function openInjectPreviewDialog(nb, triggerEl) {
    let preview = null;
    if (appState.sourceMode === "demo") {
      preview = {
        action: nb.injected ? "update" : "insert",
        mode: nb.detected_mode,
        insert_index: 1,
        cells_before: nb.total_cells,
        cells_after: nb.injected ? nb.total_cells : nb.total_cells + 1,
        cell_source:
          `# @title 📡 Colab Monitor v2.0 探针（演示预览 · ${nb.detected_mode}）\n` +
          `# 将加载 _colab_monitor/colab_agent.py 并启动后台心跳与云盘指令监听\n` +
          `COLAB_AGENT.start_background_workers()\n`
      };
    } else {
      try {
        const resp = await fetch("/api/v2/notebooks/inject", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Colab-Action": "notebooks.inject" },
          body: JSON.stringify({ action: "notebooks.inject", path: nb.path, dry_run: true }),
          cache: "no-store"
        });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || !data.ok) {
          showNotice("生成注入预览失败：" + (data?.error || `HTTP ${resp.status}`));
          return;
        }
        preview = data;
      } catch (err) {
        showNotice("生成注入预览异常：" + (err?.message || err));
        return;
      }
    }

    const dlg = document.getElementById("inject-preview-dialog");
    if (!dlg) return;
    const isDriveApi = nb.source === "drive_api" || String(nb.path || "").startsWith("gdrive://");
    document.getElementById("inject-preview-title").textContent =
      preview.action === "update" ? `升级探针注入：${nb.name}` : `预览探针注入：${nb.name}`;
    const descEl = document.getElementById("inject-preview-desc");
    if (descEl) {
      descEl.textContent = isDriveApi
        ? "确认后将先在服务端本地备份原 .ipynb，再同步最新 colab_agent.py 至云端 _colab_monitor/，并通过 Drive v3 API 原地写入独立标记单元（不改变链接 ID，不改动原业务单元）。"
        : "确认后将先在纯本地目录备份原 .ipynb，再同步最新 colab_agent.py 至云盘 _colab_monitor/，并以独立标记单元幂等写入（不改动任何原业务单元）。";
    }
    const kvItems = [
      ["目标文件", nb.display_path],
      ["检测云盘模式", notebookModeLabel(preview.mode)],
      ["操作类型", preview.action === "update" ? "原地升级已有标记单元（幂等）" : `在第 ${preview.insert_index + 1} 个单元位置插入独立单元`],
      ["单元数变化", `${preview.cells_before} → ${preview.cells_after}`]
    ];
    document.getElementById("inject-preview-kv").replaceChildren(
      ...kvItems.map(([k, v]) => {
        const w = el("div");
        w.append(el("dt", k), el("dd", v));
        return w;
      })
    );
    document.getElementById("inject-preview-code").textContent = preview.cell_source || "";
    appState.pendingInjectTarget = nb;
    appState.lastInjectTrigger = triggerEl || document.activeElement;
    openModalDialog(dlg);
  }

  function closeInjectPreviewDialog() {
    closeTopLayerDialog(document.getElementById("inject-preview-dialog"));
  }

  async function applyNotebookInjection() {
    const nb = appState.pendingInjectTarget;
    closeInjectPreviewDialog();
    if (!nb) return;

    if (appState.sourceMode === "demo") {
      nb.injected = true;
      nb.injected_version = "2.0";
      renderNotebooksCard();
      showNotice(`[演示样本] 已模拟写入探针单元到 ${nb.name}（未修改真实磁盘文件）。`);
      return;
    }
    try {
      const resp = await fetch("/api/v2/notebooks/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Colab-Action": "notebooks.inject" },
        body: JSON.stringify({ action: "notebooks.inject", path: nb.path, dry_run: false }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok) {
        showNotice(`注入完成：${nb.name}（原文件已备份至本地，colab_agent.py 已同步至云盘 _colab_monitor/）。`);
        fetchNotebooks();
      } else {
        showNotice("写入注入失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice("写入注入异常：" + (err?.message || err));
    }
  }

  async function applyRemoveInjection(nb) {
    if (appState.sourceMode === "demo") {
      nb.injected = false;
      nb.injected_version = null;
      renderNotebooksCard();
      showNotice(`[演示样本] 已模拟移除 ${nb.name} 的探针单元。`);
      return;
    }
    try {
      const resp = await fetch("/api/v2/notebooks/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Colab-Action": "notebooks.remove" },
        body: JSON.stringify({ action: "notebooks.remove", path: nb.path, dry_run: false }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok) {
        showNotice(`已移除 ${data.removed_count} 个注入单元，原文件已备份至本地。`);
        fetchNotebooks();
      } else {
        showNotice("移除注入失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice("移除注入异常：" + (err?.message || err));
    }
  }

  function openBindUrlDialog(nb, triggerEl) {
    const dlg = document.getElementById("bind-url-dialog");
    if (!dlg) return;
    document.getElementById("bind-url-kv").replaceChildren(
      (() => {
        const w = el("div");
        w.append(el("dt", "目标笔记本"), el("dd", nb.display_path));
        return w;
      })()
    );
    const input = document.getElementById("bind-url-input");
    if (input) input.value = nb.colab_url || "";
    appState.pendingBindTarget = nb;
    appState.lastBindTrigger = triggerEl || document.activeElement;
    openModalDialog(dlg);
  }

  function closeBindUrlDialog() {
    closeTopLayerDialog(document.getElementById("bind-url-dialog"));
  }

  async function saveBindUrl(urlValue) {
    const nb = appState.pendingBindTarget;
    closeBindUrlDialog();
    if (!nb) return;

    if (appState.sourceMode === "demo") {
      nb.colab_url = (urlValue || "").trim();
      renderNotebooksCard();
      showNotice(nb.colab_url ? "[演示样本] 已保存 Colab 直达链接。" : "[演示样本] 已清除绑定链接。");
      return;
    }
    try {
      const resp = await fetch("/api/v2/notebooks/bind-url", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Colab-Action": "notebooks.bind_url" },
        body: JSON.stringify({ action: "notebooks.bind_url", path: nb.path, colab_url: urlValue || "" }),
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok) {
        showNotice(data.colab_url ? `已绑定 ${nb.name} 的 Colab 直达链接。` : `已清除 ${nb.name} 的绑定链接。`);
        fetchNotebooks();
      } else {
        showNotice("保存链接失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice("保存链接异常：" + (err?.message || err));
    }
  }

  function renderAll(snap) {
    appState.snapshot = snap;
    renderInstanceBar();
    renderCommandBar();
    renderHeaderAndBanner(snap);
    renderKpis(snap);
    renderControlOpsCard();
    renderWorkloadOpsCard();
    renderResourcesCard(snap);
    renderCompositionCard(snap);
    renderUptimeCard(snap);
    renderElapsedChart(snap);
    syncModeFilterButtons(snap.recent_outputs || []);
    renderOutputsTable();
    syncActionButtons(snap);
    if (!appState.lastLogPayload) renderInspectorLogs();
    if (!(appState.lastProcesses || []).length) renderInspectorProcesses();
    if (appState.provisionerStatus && !document.getElementById("provisioner-sessions-body")?.contains(document.activeElement)) {
      renderProvisioner();
    }
    scheduleSegSync();
  }

  function clearPollTimer() {
    if (appState.pollTimer) {
      window.clearTimeout(appState.pollTimer);
      appState.pollTimer = null;
    }
  }

  function scheduleNextPoll() {
    clearPollTimer();
    if (appState.pollSeconds > 0 && appState.sourceMode === "live" && !appState.inFlightSnapshot) {
      appState.pollTimer = window.setTimeout(() => {
        loadSnapshot({ isPoll: true });
      }, appState.pollSeconds * 1000);
    }
  }

  // --- Data fetching with stale-response protection & single-flight polling ---
  async function loadSnapshot(options = {}) {
    const isPoll = Boolean(options && options.isPoll);
    if (isPoll && (appState.inFlightSnapshot || appState.isKeepingAlive)) {
      return;
    }

    clearPollTimer();
    hideTooltip();

    const seq = ++appState.requestSeq;
    if (appState.abortController) {
      appState.abortController.abort();
      appState.abortController = null;
    }
    const shell = document.getElementById("dashboard-shell");

    if (appState.sourceMode === "demo") {
      appState.inFlightSnapshot = false;
      shell.classList.remove("is-loading");
      if (!appState.demoV2State) {
        appState.demoV2State = createDemoV2State();
      }
      const dState = appState.demoV2State;
      appState.v2Instances = dState.instances;
      const curDemo = dState.instances.find((i) => i.instance_id === dState.selectedId) || dState.instances[0];
      appState.selectedInstanceId = curDemo.instance_id;
      appState.currentV2Instance = curDemo;
      appState.v2Commands = dState.commands.filter((c) => c.instance_id === curDemo.instance_id);
      appState.lastLogPayload = { source: appState.logSource, lines: dState.logs[appState.logSource] || [], truncated: false, redacted: true };
      appState.lastProcesses = dState.processes || [];

      const limit = appState.outputLimit;
      const cloned = structuredClone(DEMO_SNAPSHOT);
      cloned.base_url = curDemo.tunnel?.base_url || "(云盘带外信箱模式 · 无公网隧道)";
      if (appState.demoOnline && curDemo.liveness_state !== "OFFLINE") {
        cloned.health.idle_seconds = curDemo.lifecycle.idle_seconds;
        cloned.health.idle_timeout_minutes = curDemo.lifecycle.idle_timeout_minutes;
        cloned.health.active_jobs = curDemo.workload.running;
        cloned.metrics.gpu = { ...curDemo.hardware.gpu, power_w: curDemo.hardware.gpu.power_draw_w };
        cloned.metrics.cpu = curDemo.hardware.cpu || null;
        cloned.metrics.ram = curDemo.hardware.ram;
        cloned.metrics.disk = curDemo.hardware.disk;
        cloned.metrics.jobs = {
          running: curDemo.workload.running,
          queued: curDemo.workload.queued,
          succeeded: curDemo.workload.succeeded,
          failed: curDemo.workload.failed
        };
      } else {
        cloned.online = false;
        cloned.offline_reason = "shutdown_confirmed";
        cloned.health = null;
        cloned.metrics = null;
        cloned.error = "[演示样本] 已模拟执行远程关机，实例已释放下线。";
        if (cloned.uptime_summary) {
          cloned.uptime_summary.current_online = false;
          cloned.uptime_summary.current_hardware = null;
          cloned.uptime_summary.current_category = null;
          cloned.uptime_summary.current_session_seconds = 0;
          if (cloned.uptime_summary.compute_units_summary) {
            cloned.uptime_summary.compute_units_summary.live_rate_hourly = 0;
            cloned.uptime_summary.compute_units_summary.active_assignments = 0;
            cloned.uptime_summary.compute_units_summary.estimated_remaining_hours = null;
            cloned.uptime_summary.compute_units_summary.current_session_units = 0;
          }
          for (const win of Object.values(cloned.uptime_summary.windows || {})) {
            for (const item of win.items || []) {
              item.is_current = false;
            }
          }
          const curWin = cloned.uptime_summary.windows?.current;
          if (curWin) {
            curWin.total_seconds = 0;
            curWin.gpu_seconds = 0;
            curWin.cpu_only_seconds = 0;
            curWin.unknown_seconds = 0;
            curWin.total_compute_units = 0;
            curWin.gpu_compute_units = 0;
            curWin.cpu_only_compute_units = 0;
            curWin.session_count = 0;
            for (const item of curWin.items || []) {
              item.seconds = 0;
              item.share_pct = 0;
              item.compute_units = 0;
              item.compute_units_share_pct = 0;
              item.session_count = 0;
              item.is_current = false;
            }
          }
        }
      }
      if (limit > 0) {
        cloned.recent_outputs = cloned.recent_outputs.slice(0, limit);
      }
      renderAll(cloned);
      renderInspectorLogs();
      renderInspectorProcesses();
      return;
    }

    appState.inFlightSnapshot = true;
    appState.abortController = new AbortController();
    shell.classList.add("is-loading");

    try {
      const signal = appState.abortController.signal;
      let v2UptimeSummary = null;
      const v2Resp = await fetch(`/api/v2/instances?limit=${appState.outputLimit}`, { cache: "no-store", signal });
      if (v2Resp.ok) {
        const v2List = await v2Resp.json();
        if (seq !== appState.requestSeq) return;
        v2UptimeSummary = v2List.uptime_summary || null;
        const instances = Array.isArray(v2List.instances) ? v2List.instances : [];
        appState.v2Instances = instances;
        let targetId = appState.selectedInstanceId;
        if (!targetId || !instances.some((i) => i.instance_id === targetId)) {
          targetId = v2List.default_instance_id || (instances[0] ? instances[0].instance_id : null);
          appState.selectedInstanceId = targetId;
        }
        if (targetId) {
          const snapResp = await fetch(
            `/api/v2/instances/${encodeURIComponent(targetId)}/snapshot?limit=${appState.outputLimit}`,
            { cache: "no-store", signal }
          );
          if (snapResp.ok) {
            const snapData = await snapResp.json();
            if (seq !== appState.requestSeq) return;
            appState.currentV2Instance = snapData.instance || null;
            appState.v2Commands = Array.isArray(snapData.commands) ? snapData.commands : [];
            if (snapData.v1_snapshot) {
              renderAll(snapData.v1_snapshot);
              return;
            }
          }
        }
      }
      // Fallback to v1 /api/snapshot
      const resp = await fetch(`/api/snapshot?limit=${appState.outputLimit}`, {
        cache: "no-store",
        signal
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (seq !== appState.requestSeq) return;
      if (
        !data.online &&
        v2UptimeSummary &&
        ((v2UptimeSummary.total_sessions || 0) > 0 ||
          v2UptimeSummary.compute_units_summary?.account_balance !== null ||
          (data.uptime_summary?.total_sessions || 0) === 0)
      ) {
        data.uptime_summary = v2UptimeSummary;
      }
      appState.v2Instances = [];
      appState.currentV2Instance = null;
      appState.v2Commands = [];
      renderAll(data);
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (seq !== appState.requestSeq) return;
      renderAll({
        online: false,
        fetched_at: new Date().toISOString(),
        ep_path: "本地服务端",
        base_url: "",
        has_api_key: false,
        error: `无法访问本地监控 API (/api/snapshot): ${err.message || err}`,
        health: null,
        metrics: null,
        recent_outputs: appState.snapshot?.recent_outputs || [],
        total_output_files: appState.snapshot?.total_output_files || 0,
        uptime_summary: appState.snapshot?.uptime_summary || null
      });
    } finally {
      if (seq === appState.requestSeq) {
        appState.inFlightSnapshot = false;
        appState.abortController = null;
        shell.classList.remove("is-loading");
        scheduleNextPoll();
      }
    }
  }

  function updatePollTimer() {
    clearPollTimer();
    scheduleNextPoll();
  }

  async function triggerKeepalive() {
    if (appState.isKeepingAlive || appState.isShuttingDown) return;
    if (appState.sourceMode === "demo") {
      if (!appState.demoOnline) {
        showNotice("[演示样本] 当前模拟实例已离线，切回实时端点或重新切入演示样本可重置。");
        return;
      }
      appState.demoIdleSeconds = 0;
      await loadSnapshot();
      showNotice("[演示样本] 已模拟手动保活，空闲计时已重置为 0s（未连接真实 Colab 实例）。");
      return;
    }
    if (!appState.snapshot || !appState.snapshot.online) {
      showNotice("当前 Colab 实例离线，无法发送保活指令。");
      return;
    }
    if (!appState.snapshot.has_api_key) {
      showNotice("active_endpoint.json 缺少 api_key，无法鉴权保活。");
      return;
    }

    appState.isKeepingAlive = true;
    clearPollTimer();
    syncActionButtons(appState.snapshot);

    const seq = ++appState.requestSeq;
    if (appState.abortController) {
      appState.abortController.abort();
      appState.abortController = null;
    }
    appState.inFlightSnapshot = false;
    document.getElementById("dashboard-shell")?.classList.remove("is-loading");

    try {
      const resp = await fetch(`/api/keepalive?limit=${appState.outputLimit}`, {
        method: "POST",
        headers: { "X-H3-Action": "keepalive" },
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (data && data.snapshot && seq === appState.requestSeq) {
        renderAll(data.snapshot);
      }
      if (resp.ok && data && data.ok) {
        const timeoutM = Number(data.idle_timeout_minutes ?? 0);
        const idleS = Number(data.idle_seconds ?? 0);
        const modeNote = data.via === "jobs_auth_fallback" ? "（兼容模式）" : "";
        if (timeoutM > 0) {
          showNotice(`手动保活成功${modeNote}：空闲计时已重置，距自动释放剩余 ${Math.max(0, timeoutM - idleS / 60).toFixed(1)} 分钟。`);
        } else {
          showNotice(`手动保活成功${modeNote}：网关活跃时间已更新。`);
        }
      } else {
        showNotice("保活失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      showNotice("保活请求异常：" + (err?.message || err));
    } finally {
      appState.isKeepingAlive = false;
      syncActionButtons(appState.snapshot);
      scheduleNextPoll();
    }
  }

  function openShutdownDialog() {
    if (appState.isShuttingDown) {
      showNotice("关机指令已发送，正在等待 Colab 实例释放…");
      return;
    }
    if (appState.sourceMode === "demo") {
      if (!appState.demoOnline) {
        showNotice("[演示样本] 当前模拟实例已处于离线状态。");
        return;
      }
    } else {
      if (!appState.snapshot || !appState.snapshot.online) {
        showNotice("当前 Colab 实例已处于离线状态。");
        return;
      }
      if (!appState.snapshot.has_api_key) {
        showNotice("active_endpoint.json 缺少 api_key，无法鉴权关机。");
        return;
      }
    }

    const snap = appState.snapshot;
    const isDemo = appState.sourceMode === "demo";
    const hwName = snap?.metrics?.gpu?.available ? (snap.metrics.gpu.name || "GPU") : "当前计算";
    document.getElementById("shutdown-dialog-desc").textContent = isDemo
      ? "[演示样本] 确认后仅在本地模拟实例下线状态，不会连接真实 Colab 实例。"
      : `即将向当前 Colab 网关发送远程释放指令（POST /v1/shutdown），实例将在约 3–8 秒内执行 os.sync() 并释放 ${hwName} 算力。`;

    const active = Number(snap?.health?.active_jobs || 0);
    const queued = Number(snap?.metrics?.jobs?.queued || 0);
    const warning = document.getElementById("shutdown-dialog-warning");
    if (active > 0 || queued > 0) {
      warning.textContent = `注意：当前仍有 ${active} 个运行中任务、${queued} 个排队任务，释放实例将立即中断未完成的推理！`;
      warning.hidden = false;
    } else {
      warning.textContent = "";
      warning.hidden = true;
    }

    const kvItems = [
      ["目标端点", snap?.base_url || "—"],
      ["活跃 / 排队任务", `${active} / ${queued}`]
    ];
    const kvNodes = kvItems.map(([k, v]) => {
      const wrap = el("div");
      wrap.append(el("dt", k), el("dd", v));
      return wrap;
    });
    document.getElementById("shutdown-dialog-kv").replaceChildren(...kvNodes);

    appState.lastShutdownTrigger = document.getElementById("shutdown-now");
    openModalDialog(document.getElementById("shutdown-dialog"));
  }

  function closeShutdownDialog() {
    closeTopLayerDialog(document.getElementById("shutdown-dialog"));
  }

  async function confirmShutdown() {
    closeShutdownDialog();
    if (appState.isShuttingDown) return;

    if (appState.sourceMode === "demo") {
      appState.demoOnline = false;
      await loadSnapshot();
      showNotice("[演示样本] 已模拟远程关机，实例现已显示为离线状态。");
      return;
    }

    appState.isShuttingDown = true;
    clearPollTimer();
    clearShutdownTimers();
    syncActionButtons(appState.snapshot);

    const seq = ++appState.requestSeq;
    if (appState.abortController) {
      appState.abortController.abort();
      appState.abortController = null;
    }
    appState.inFlightSnapshot = false;
    document.getElementById("dashboard-shell")?.classList.remove("is-loading");

    try {
      const resp = await fetch(`/api/shutdown?limit=${appState.outputLimit}`, {
        method: "POST",
        headers: { "X-H3-Action": "shutdown" },
        cache: "no-store"
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data && data.ok) {
        if (data.snapshot && seq === appState.requestSeq) {
          renderAll(data.snapshot);
        }
        showNotice("关机指令已发送：" + (data.message || "Colab 实例将在数秒内安全释放。"));
        if (appState.isShuttingDown) {
          const delays = [2500, 5000, 7500, 10000, 13000, 16000, 20000, 25000, 30000];
          for (const delay of delays) {
            appState.shutdownTimers.push(
              window.setTimeout(() => {
                if (appState.sourceMode === "live" && appState.isShuttingDown) {
                  loadSnapshot({ isPoll: true });
                }
              }, delay)
            );
          }
          appState.shutdownTimers.push(
            window.setTimeout(() => {
              if (appState.isShuttingDown) {
                appState.isShuttingDown = false;
                syncActionButtons(appState.snapshot);
                if (appState.snapshot && appState.snapshot.online) {
                  showNotice("实例在 30 秒后仍未断开隧道；若曾在 Colab 手动中断过 Step 6 守护循环，请在 Colab 运行 Step 7 释放。");
                }
              }
            }, 32000)
          );
        }
      } else {
        appState.isShuttingDown = false;
        if (data && data.snapshot && seq === appState.requestSeq) {
          renderAll(data.snapshot);
        } else {
          syncActionButtons(appState.snapshot);
        }
        showNotice("关机失败：" + (data?.error || `HTTP ${resp.status}`));
      }
    } catch (err) {
      appState.isShuttingDown = false;
      syncActionButtons(appState.snapshot);
      showNotice("关机请求异常：" + (err?.message || err));
    } finally {
      scheduleNextPoll();
    }
  }

  function setSourceMode(mode) {
    appState.sourceMode = mode;
    appState.isShuttingDown = false;
    appState.provisionerBooting = false;
    clearShutdownTimers();
    clearProvisionerJobTimer();
    renderProvisionerJob(null);
    if (mode === "demo") {
      appState.demoOnline = true;
      appState.demoIdleSeconds = DEMO_SNAPSHOT.health.idle_seconds;
      appState.demoV2State = createDemoV2State();
      appState.notebooks = appState.demoV2State.notebooks;
      appState.provisionerStatus = appState.demoV2State.provisioner;
      renderNotebooksCard();
      renderProvisioner();
    } else {
      appState.selectedInstanceId = null;
      appState.lastLogPayload = null;
      appState.lastProcesses = [];
      appState.provisionerStatus = null;
      fetchNotebooks();
      loadProvisionerStatus({ refresh: false });
    }
    for (const btn of document.querySelectorAll("[data-source-choice]")) {
      btn.setAttribute("aria-pressed", String(btn.dataset.sourceChoice === mode));
    }
    updatePollTimer();
    syncAllSegIndicators(false);
    loadSnapshot();
  }

  // --- Bindings ---
  document.addEventListener("DOMContentLoaded", () => {
    syncDisplayControls();

    const toolbar = document.querySelector('[role="toolbar"]');
    if (toolbar) setupToolbarFallback(toolbar);
    setupScrollHintsFallback();

    // Display settings listeners
    systemDark.addEventListener("change", () => {
      if (display.state.mode === "auto") syncDisplayControls();
    });
    forcedColors.addEventListener("change", syncDisplayControls);
    window.addEventListener("storage", (event) => {
      if (!event.key || event.key.startsWith(display.prefix)) {
        display.loadStoredState();
        syncDisplayControls();
      }
    });

    for (const dim of ["palette", "tone", "material"]) {
      for (const btn of document.querySelectorAll("[data-" + dim + "-choice]")) {
        btn.addEventListener("click", () => {
          if (!btn.disabled && btn.getAttribute("aria-disabled") !== "true") {
            selectDisplay(dim, btn.dataset[dim + "Choice"]);
          }
        });
      }
    }

    document.getElementById("theme-mode").addEventListener("click", () => {
      const modeBtn = document.getElementById("theme-mode");
      if (!modeBtn.disabled && modeBtn.getAttribute("aria-disabled") !== "true") {
        const modes = display.choices.mode;
        const next = modes[(modes.indexOf(display.state.mode) + 1) % modes.length];
        selectDisplay("mode", next);
      }
    });

    // Source mode buttons
    for (const btn of document.querySelectorAll("[data-source-choice]")) {
      btn.addEventListener("click", () => setSourceMode(btn.dataset.sourceChoice));
    }

    // Poll interval buttons
    for (const btn of document.querySelectorAll("[data-interval-choice]")) {
      btn.addEventListener("click", () => {
        appState.pollSeconds = Number(btn.dataset.intervalChoice || 0);
        for (const b of document.querySelectorAll("[data-interval-choice]")) {
          b.setAttribute("aria-pressed", String(b === btn));
        }
        updatePollTimer();
        syncAllSegIndicators(false);
      });
    }

    // Output limit buttons
    for (const btn of document.querySelectorAll("[data-limit-choice]")) {
      btn.addEventListener("click", () => {
        appState.outputLimit = Number(btn.dataset.limitChoice || 12);
        for (const b of document.querySelectorAll("[data-limit-choice]")) {
          b.setAttribute("aria-pressed", String(b === btn));
        }
        syncAllSegIndicators(false);
        loadSnapshot();
      });
    }

    // Uptime range & metric switchers inside the uptime card
    for (const btn of document.querySelectorAll("[data-uptime-range]")) {
      btn.addEventListener("click", () => {
        appState.uptimeRange = btn.dataset.uptimeRange || "all";
        for (const b of document.querySelectorAll("[data-uptime-range]")) {
          b.setAttribute("aria-pressed", String(b === btn));
        }
        renderUptimeCard(appState.snapshot);
        syncAllSegIndicators(false);
      });
    }
    for (const btn of document.querySelectorAll("[data-uptime-metric]")) {
      btn.addEventListener("click", () => {
        appState.uptimeMetric = btn.dataset.uptimeMetric === "units" ? "units" : "time";
        for (const b of document.querySelectorAll("[data-uptime-metric]")) {
          b.setAttribute("aria-pressed", String(b === btn));
        }
        renderUptimeCard(appState.snapshot);
        syncAllSegIndicators(false);
      });
    }

    document.getElementById("refresh-now").addEventListener("click", () => loadSnapshot());
    document.getElementById("keepalive-now").addEventListener("click", triggerKeepalive);
    document.getElementById("drain-toggle")?.addEventListener("click", (ev) => {
      const btn = ev.currentTarget;
      if (btn.getAttribute("aria-disabled") === "true") return;
      const inst = appState.currentV2Instance;
      if (!inst) return;
      if (inst.lifecycle?.drain_requested) {
        dispatchV2Action("lifecycle.cancel_drain", {});
      } else {
        openActionConfirmDialog({
          title: "确认开启排空关机（跑完即停）",
          desc: `实例 ${inst.label || inst.instance_id} 将拒绝新任务，待当前运行中任务完成并同步云盘后自动释放算力。`,
          kv: [
            ["目标实例", inst.label || inst.instance_id],
            ["运行中 / 排队", `${inst.workload?.running ?? 0} / ${inst.workload?.queued ?? 0}`]
          ],
          trigger: btn,
          onConfirm: () => dispatchV2Action("lifecycle.drain_shutdown", { sync_drive: true, include_queued: false, timeout_minutes: 120 })
        });
      }
    });
    document.getElementById("shutdown-now").addEventListener("click", openShutdownDialog);
    document.getElementById("banner-rebuild-tunnel")?.addEventListener("click", () => {
      dispatchV2Action("network.rebuild_tunnel", {});
    });
    document.getElementById("fleet-drain-all")?.addEventListener("click", (ev) => {
      const onlineList = (appState.v2Instances || []).filter((i) => isV2Online(i));
      openActionConfirmDialog({
        title: `确认对全部 ${onlineList.length} 台在线实例执行排空关机`,
        desc: "所有在线 Colab 实例将在完成当前在途任务并同步云盘后自动释放算力。",
        warning: "如某实例处于云盘信箱模式，指令将自动走云盘带外通道下发。",
        kv: onlineList.map((i) => [i.label || i.instance_id, `运行中 ${i.workload?.running ?? 0} · 通道 ${i.active_channel}`]),
        trigger: ev.currentTarget,
        onConfirm: () => dispatchFleetDrainAll()
      });
    });

    // Lease & idle policy buttons
    for (const btn of document.querySelectorAll("[data-lease-min]")) {
      btn.addEventListener("click", () => {
        if (btn.getAttribute("aria-disabled") === "true") return;
        dispatchV2Action("lifecycle.set_keepalive_lease", { duration_minutes: Number(btn.dataset.leaseMin || 0) });
      });
    }
    for (const btn of document.querySelectorAll("[data-idle-min]")) {
      btn.addEventListener("click", () => {
        if (btn.getAttribute("aria-disabled") === "true") return;
        const autoSd = Boolean(appState.currentV2Instance?.lifecycle?.auto_shutdown_after_batch);
        dispatchV2Action("lifecycle.set_idle_policy", {
          idle_timeout_minutes: Number(btn.dataset.idleMin || 0),
          auto_shutdown_after_batch: autoSd
        });
      });
    }

    // Service & storage quick ops
    for (const btn of document.querySelectorAll("[data-ops-action]")) {
      btn.addEventListener("click", () => {
        if (btn.getAttribute("aria-disabled") === "true") return;
        const act = btn.dataset.opsAction;
        if (act === "comfy-interrupt") dispatchV2Action("ops.service_control", { service: "comfyui", op: "interrupt" });
        else if (act === "comfy-free-vram") dispatchV2Action("ops.service_control", { service: "comfyui", op: "free_vram" });
        else if (act === "comfy-restart") {
          openActionConfirmDialog({
            title: "确认重启 ComfyUI 子服务",
            desc: "将安全重启 127.0.0.1:8188 后台进程，正在进行的生图会被中断。",
            trigger: btn,
            onConfirm: () => dispatchV2Action("ops.service_control", { service: "comfyui", op: "restart" })
          });
        } else if (act === "rebuild-tunnel") dispatchV2Action("network.rebuild_tunnel", {});
        else if (act === "flush-verify") dispatchV2Action("storage.flush_and_verify", {});
        else if (act === "cleanup-dry-run") {
          dispatchV2Action("ops.cleanup_disk", { targets: ["comfy_temp", "pip_cache", "hf_incomplete", "agent_scratch"], dry_run: true });
        }
      });
    }

    // Workload queue buttons
    document.getElementById("workload-pause-btn")?.addEventListener("click", (e) => {
      if (e.currentTarget.getAttribute("aria-disabled") !== "true") dispatchV2Action("workload.pause_queue", {});
    });
    document.getElementById("workload-resume-btn")?.addEventListener("click", (e) => {
      if (e.currentTarget.getAttribute("aria-disabled") !== "true") dispatchV2Action("workload.resume_queue", {});
    });
    document.getElementById("workload-reload-btn")?.addEventListener("click", (e) => {
      if (e.currentTarget.getAttribute("aria-disabled") !== "true") {
        dispatchV2Action("workload.reload_drive_queue", { queue_file: "offline_batch_queue.json" });
      }
    });
    document.getElementById("workload-retry-failed-btn")?.addEventListener("click", (e) => {
      if (e.currentTarget.getAttribute("aria-disabled") !== "true") {
        dispatchV2Action("workload.queue_mutate", { op: "retry_failed", job_id: null });
      }
    });
    document.getElementById("workload-clear-pending-btn")?.addEventListener("click", (e) => {
      if (e.currentTarget.getAttribute("aria-disabled") === "true") return;
      openActionConfirmDialog({
        title: "确认清空所有排队任务",
        desc: "将移除全部尚未开始的排队项（已在运行中的任务不受影响）。",
        trigger: e.currentTarget,
        onConfirm: () => dispatchV2Action("workload.queue_mutate", { op: "clear_pending", job_id: null })
      });
    });

    // Inspector tabs, logs & processes
    for (const btn of document.querySelectorAll("[data-inspector-tab]")) {
      btn.addEventListener("click", () => {
        const tab = btn.dataset.inspectorTab || "logs";
        appState.inspectorTab = tab;
        for (const b of document.querySelectorAll("[data-inspector-tab]")) {
          b.setAttribute("aria-pressed", String(b === btn));
        }
        document.getElementById("inspector-logs-panel").hidden = tab !== "logs";
        document.getElementById("inspector-processes-panel").hidden = tab !== "processes";
        syncAllSegIndicators(false);
        if (tab === "logs") fetchInspectorLogs();
        else fetchInspectorProcesses();
      });
    }
    for (const btn of document.querySelectorAll("[data-log-source]")) {
      btn.addEventListener("click", () => {
        appState.logSource = btn.dataset.logSource || "agent";
        for (const b of document.querySelectorAll("[data-log-source]")) {
          b.setAttribute("aria-pressed", String(b === btn));
        }
        syncAllSegIndicators(false);
        fetchInspectorLogs();
      });
    }
    document.getElementById("log-filter-input")?.addEventListener("input", (e) => {
      appState.logFilter = e.currentTarget.value || "";
      renderInspectorLogs();
    });
    document.getElementById("logs-refresh-btn")?.addEventListener("click", fetchInspectorLogs);
    document.getElementById("logs-snapshot-btn")?.addEventListener("click", () => {
      dispatchV2Action("logs.snapshot_to_drive", { sources: ["comfyui", "cloudflared", "gateway", "agent"] });
    });
    document.getElementById("processes-refresh-btn")?.addEventListener("click", fetchInspectorProcesses);

    // Generic action confirmation dialog
    const actDlg = document.getElementById("action-confirm-dialog");
    if (actDlg) {
      enhanceModalDialog(actDlg, closeActionConfirmDialog);
      document.getElementById("action-confirm-close")?.addEventListener("click", closeActionConfirmDialog);
      document.getElementById("action-confirm-cancel")?.addEventListener("click", closeActionConfirmDialog);
      document.getElementById("action-confirm-ok")?.addEventListener("click", () => {
        const fn = appState.pendingConfirmAction;
        appState.pendingConfirmAction = null;
        closeActionConfirmDialog();
        if (typeof fn === "function") fn();
      });
      actDlg.addEventListener("close", () => {
        if (appState.lastConfirmTrigger && appState.lastConfirmTrigger.isConnected && typeof appState.lastConfirmTrigger.focus === "function") {
          appState.lastConfirmTrigger.focus();
        }
        appState.lastConfirmTrigger = null;
        appState.pendingConfirmAction = null;
      });
    }
    document.getElementById("banner-retry").addEventListener("click", () => {
      if (appState.sourceMode === "demo") setSourceMode("live");
      else loadSnapshot();
    });
    document.getElementById("banner-demo").addEventListener("click", () => setSourceMode("demo"));

    // Search input (IME composition safe)
    const searchInput = document.getElementById("output-search");
    searchInput.addEventListener("compositionstart", () => {
      appState.isComposing = true;
    });
    searchInput.addEventListener("compositionend", () => {
      appState.isComposing = false;
      appState.searchQuery = searchInput.value;
      renderOutputsTable();
    });
    searchInput.addEventListener("input", () => {
      if (appState.isComposing) return;
      appState.searchQuery = searchInput.value;
      renderOutputsTable();
    });

    // Sort buttons
    for (const btn of document.querySelectorAll("[data-sort-key]")) {
      btn.addEventListener("click", () => {
        const key = btn.dataset.sortKey;
        if (appState.sortKey === key) {
          appState.sortDir = appState.sortDir === "asc" ? "desc" : "asc";
        } else {
          appState.sortKey = key;
          appState.sortDir = key === "file" || key === "mode" ? "asc" : "desc";
        }
        renderOutputsTable();
      });
    }

    // Dialog close & focus return
    const dialog = document.getElementById("detail-dialog");
    enhanceModalDialog(dialog, closeDetailDialog);
    document.getElementById("dialog-close").addEventListener("click", closeDetailDialog);
    dialog.addEventListener("close", () => {
      let targetEl = null;
      if (
        appState.lastFocusedTrigger &&
        appState.lastFocusedTrigger.isConnected &&
        typeof appState.lastFocusedTrigger.focus === "function"
      ) {
        targetEl = appState.lastFocusedTrigger;
      } else if (appState.lastFocusedTarget && appState.lastFocusedTarget.file) {
        const wantedFile = appState.lastFocusedTarget.file;
        const wantedSource = appState.lastFocusedTarget.source;
        const candidates = [...document.querySelectorAll("[data-detail-file]")];
        targetEl =
          candidates.find(
            (elNode) =>
              elNode.dataset.detailFile === wantedFile &&
              (!wantedSource || elNode.dataset.detailSource === wantedSource)
          ) ||
          candidates.find((elNode) => elNode.dataset.detailFile === wantedFile) ||
          document.getElementById("output-search");
      }
      if (targetEl && typeof targetEl.focus === "function") {
        targetEl.focus();
      }
      appState.lastFocusedTrigger = null;
      appState.lastFocusedTarget = null;
    });

    const shutdownDialog = document.getElementById("shutdown-dialog");
    enhanceModalDialog(shutdownDialog, closeShutdownDialog);
    document.getElementById("shutdown-dialog-close").addEventListener("click", closeShutdownDialog);
    document.getElementById("shutdown-cancel").addEventListener("click", closeShutdownDialog);
    document.getElementById("shutdown-confirm").addEventListener("click", confirmShutdown);
    shutdownDialog.addEventListener("close", () => {
      if (appState.lastShutdownTrigger && typeof appState.lastShutdownTrigger.focus === "function") {
        appState.lastShutdownTrigger.focus();
      }
      appState.lastShutdownTrigger = null;
    });

    // Clipboard copy (only report success after Promise resolves)
    document.getElementById("dialog-copy-prompt").addEventListener("click", async () => {
      const promptText = document.getElementById("dialog-prompt").textContent || "";
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
        showNotice("当前环境不支持自动剪贴板，请在上方 Prompt 框内手动全选复制。");
        return;
      }
      try {
        await navigator.clipboard.writeText(promptText);
        showNotice("已复制 Prompt 到剪贴板。");
      } catch {
        showNotice("复制失败，请在上方 Prompt 框内手动全选复制。");
      }
    });

    // Escape closes tooltip when open
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hideTooltip();
    });

    // Segmented indicator measurement observers
    if (typeof ResizeObserver !== "undefined") {
      for (const group of document.querySelectorAll(".control-set[data-seg]")) {
        const ro = new ResizeObserver(scheduleSegSync);
        ro.observe(group);
        segObservers.push(ro);
      }
    }
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(scheduleSegSync).catch(() => {});
    }

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && appState.sourceMode === "live" && appState.pollSeconds > 0) {
        loadSnapshot({ isPoll: true });
      }
    });

    // Notebook Launcher & Injector bindings
    document.getElementById("notebooks-disclosure")?.addEventListener("toggle", () => renderNotebooksCard());
    document.getElementById("notebooks-refresh-btn")?.addEventListener("click", fetchNotebooks);
    document.getElementById("notebooks-toggle-backups")?.addEventListener("click", () => {
      appState.notebooksIncludeBackups = !appState.notebooksIncludeBackups;
      fetchNotebooks();
    });
    document.getElementById("notebooks-search")?.addEventListener("input", (ev) => {
      appState.notebooksSearch = ev.currentTarget.value || "";
      renderNotebooksCard();
    });

    const injectDlg = document.getElementById("inject-preview-dialog");
    if (injectDlg) {
      enhanceModalDialog(injectDlg, closeInjectPreviewDialog);
      document.getElementById("inject-preview-close")?.addEventListener("click", closeInjectPreviewDialog);
      document.getElementById("inject-preview-cancel")?.addEventListener("click", closeInjectPreviewDialog);
      document.getElementById("inject-preview-apply")?.addEventListener("click", applyNotebookInjection);
      injectDlg.addEventListener("close", () => {
        if (appState.lastInjectTrigger && appState.lastInjectTrigger.isConnected && typeof appState.lastInjectTrigger.focus === "function") {
          appState.lastInjectTrigger.focus();
        }
        appState.lastInjectTrigger = null;
        appState.pendingInjectTarget = null;
      });
    }

    const bindDlg = document.getElementById("bind-url-dialog");
    if (bindDlg) {
      enhanceModalDialog(bindDlg, closeBindUrlDialog);
      document.getElementById("bind-url-close")?.addEventListener("click", closeBindUrlDialog);
      document.getElementById("bind-url-cancel")?.addEventListener("click", closeBindUrlDialog);
      document.getElementById("bind-url-clear")?.addEventListener("click", () => saveBindUrl(""));
      document.getElementById("bind-url-save")?.addEventListener("click", () => {
        const val = document.getElementById("bind-url-input")?.value || "";
        saveBindUrl(val);
      });
      document.getElementById("bind-url-input")?.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          saveBindUrl(ev.currentTarget.value || "");
        }
      });
      bindDlg.addEventListener("close", () => {
        if (appState.lastBindTrigger && appState.lastBindTrigger.isConnected && typeof appState.lastBindTrigger.focus === "function") {
          appState.lastBindTrigger.focus();
        }
        appState.lastBindTrigger = null;
        appState.pendingBindTarget = null;
      });
    }

    document.getElementById("archive-section-summary")?.addEventListener("click", () => {
      appState.archiveUserToggled = true;
    });

    // WebUI custom tooltips, display settings popover & custom runtime selector
    for (const node of document.querySelectorAll("[data-tooltip]")) {
      attachTooltipTarget(node);
    }
    attachTooltipTarget(document.getElementById("provisioner-drive-badge"));
    setupDisplaySettingsPopover();
    setupProvisionerRuntimeSelect();

    // Headless CLI + Pure OAuth2 Provisioner bindings
    document.getElementById("provisioner-refresh-btn")?.addEventListener("click", () => {
      loadProvisionerStatus({ refresh: true });
    });
    document.getElementById("provisioner-boot-btn")?.addEventListener("click", startProvisionerBoot);

    // Initial synchronous seg positioning without animation, then load live snapshot, provisioner & notebooks
    syncAllSegIndicators(true);
    loadSnapshot();
    loadProvisionerStatus({ refresh: false });
    fetchNotebooks();
  });

  function positionAnchoredPopoverFallback(trigger, panel, topVar, leftVar, alignEnd = false) {
    if (window.CSS && typeof window.CSS.supports === "function" && window.CSS.supports("position-area: block-end")) {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    const panelW = Math.max(rect.width, panel.offsetWidth || 320);
    const maxH = Math.min(384, window.innerHeight - 32);
    let top = rect.bottom + 6;
    if (top + maxH > window.innerHeight - 12 && rect.top > window.innerHeight - rect.bottom) {
      top = Math.max(12, rect.top - Math.min(maxH, panel.scrollHeight || 320) - 6);
    }
    const rawLeft = alignEnd ? rect.right - panelW : rect.left;
    const left = Math.max(12, Math.min(rawLeft, window.innerWidth - panelW - 12));
    panel.style.setProperty(topVar, `${Math.round(top)}px`);
    panel.style.setProperty(leftVar, `${Math.round(left)}px`);
  }

  function setupDisplaySettingsPopover() {
    const trigger = document.getElementById("display-settings-trigger");
    const panel = document.getElementById("display-popover");
    if (!trigger || !panel) return;

    const hasNativePopover = Object.hasOwn(HTMLElement.prototype, "popover");
    const isOpen = () => (hasNativePopover ? panel.matches(":popover-open") : panel.dataset.open === "true");

    const onOpen = () => {
      positionAnchoredPopoverFallback(trigger, panel, "--pop-top", "--pop-left", true);
      window.requestAnimationFrame(() => syncAllSegIndicators(true));
    };

    const closePanel = (restoreFocus = false) => {
      if (!isOpen()) return;
      if (hasNativePopover) panel.hidePopover();
      else {
        delete panel.dataset.open;
        trigger.setAttribute("aria-expanded", "false");
      }
      if (restoreFocus) trigger.focus();
    };

    if (hasNativePopover) {
      panel.addEventListener("toggle", (ev) => {
        const open = ev.newState === "open";
        trigger.setAttribute("aria-expanded", String(open));
        if (open) onOpen();
      });
    } else {
      document.addEventListener("pointerdown", (ev) => {
        if (isOpen() && !panel.contains(ev.target) && !trigger.contains(ev.target)) {
          closePanel(false);
        }
      });
    }

    panel.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        closePanel(true);
      }
    });

    trigger.addEventListener("click", () => {
      if (isOpen()) {
        closePanel(false);
      } else if (hasNativePopover) {
        panel.showPopover();
        onOpen();
      } else {
        panel.dataset.open = "true";
        trigger.setAttribute("aria-expanded", "true");
        onOpen();
      }
    });
  }

  function setupProvisionerRuntimeSelect() {
    const hiddenInput = document.getElementById("provisioner-runtime");
    const trigger = document.getElementById("provisioner-runtime-trigger");
    const valueEl = document.getElementById("provisioner-runtime-value");
    const rateEl = document.getElementById("provisioner-runtime-rate");
    const menu = document.getElementById("provisioner-runtime-menu");
    const highMemBox = document.getElementById("provisioner-high-mem");
    const highMemLabel = document.getElementById("provisioner-high-mem-label");
    if (!hiddenInput || !trigger || !valueEl || !rateEl || !menu) return;

    if (highMemLabel) attachTooltipTarget(highMemLabel);
    const options = [...menu.querySelectorAll(".webui-select-option")];
    const hasNativePopover = Object.hasOwn(HTMLElement.prototype, "popover");

    const positionMenu = () => positionAnchoredPopoverFallback(trigger, menu, "--dd-top", "--dd-left", false);
    const isMenuOpen = () => (hasNativePopover ? menu.matches(":popover-open") : menu.dataset.open === "true");

    const focusOptionByMode = (mode) => {
      if (!options.length) return;
      let target = options.find((o) => o.dataset.value === hiddenInput.value) || options[0];
      if (mode === "first") target = options[0];
      else if (mode === "last") target = options[options.length - 1];
      target.focus();
    };

    const openMenu = (focusMode = "selected") => {
      positionMenu();
      if (hasNativePopover) {
        if (!menu.matches(":popover-open")) menu.showPopover();
      } else {
        menu.dataset.open = "true";
        trigger.setAttribute("aria-expanded", "true");
      }
      positionMenu();
      window.requestAnimationFrame(() => focusOptionByMode(focusMode));
    };

    const closeMenu = (restoreFocus = false) => {
      if (!isMenuOpen()) return;
      if (hasNativePopover) {
        menu.hidePopover();
      } else {
        delete menu.dataset.open;
        trigger.setAttribute("aria-expanded", "false");
      }
      if (restoreFocus) trigger.focus();
    };

    const syncHighMemUi = () => {
      const opt = options.find((o) => o.dataset.value === hiddenInput.value);
      const baseRate = opt?.dataset.rate || "";
      if (hiddenInput.value === "gpu:T4" && highMemBox?.checked) {
        rateEl.textContent = "≈1.96+ CU/h · 高内存";
      } else {
        rateEl.textContent = baseRate;
      }
      if (highMemLabel) {
        const isPresetHighMem = hiddenInput.value !== "cpu" && hiddenInput.value !== "cpu:high-mem" && hiddenInput.value !== "gpu:T4";
        if (isPresetHighMem) {
          highMemLabel.dataset.tooltip = "L4 / A100 / H100 / TPU 默认已配备大内存；--high-mem 主要用于 CPU 与 T4";
        } else {
          delete highMemLabel.dataset.tooltip;
        }
      }
    };

    const selectValue = (val, syncHighMem = true) => {
      const opt = options.find((o) => o.dataset.value === val) || options[0];
      if (!opt) return;
      hiddenInput.value = opt.dataset.value;
      valueEl.textContent = opt.dataset.label || "";
      for (const item of options) {
        item.setAttribute("aria-selected", String(item === opt));
      }
      if (syncHighMem && highMemBox) {
        if (opt.dataset.value === "cpu:high-mem") highMemBox.checked = true;
        else if (opt.dataset.value === "cpu") highMemBox.checked = false;
      }
      syncHighMemUi();
    };

    if (hasNativePopover) {
      menu.addEventListener("toggle", (ev) => {
        const open = ev.newState === "open";
        trigger.setAttribute("aria-expanded", String(open));
        if (open) positionMenu();
      });
    } else {
      document.addEventListener("pointerdown", (ev) => {
        if (isMenuOpen() && !menu.contains(ev.target) && !trigger.contains(ev.target)) {
          closeMenu(false);
        }
      });
    }

    trigger.addEventListener("click", () => {
      if (isMenuOpen()) closeMenu(false);
      else openMenu("selected");
    });

    trigger.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        openMenu("selected");
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        openMenu("last");
      }
    });

    menu.addEventListener("keydown", (ev) => {
      const idx = options.indexOf(document.activeElement);
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        const next = options[(idx + 1 + options.length) % options.length];
        next?.focus();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        const prev = options[(idx - 1 + options.length) % options.length];
        prev?.focus();
      } else if (ev.key === "Home") {
        ev.preventDefault();
        options[0]?.focus();
      } else if (ev.key === "End") {
        ev.preventDefault();
        options[options.length - 1]?.focus();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        closeMenu(true);
      } else if (ev.key === "Tab") {
        closeMenu(false);
      }
    });

    for (const opt of options) {
      opt.addEventListener("click", () => {
        selectValue(opt.dataset.value, true);
        closeMenu(true);
      });
    }

    highMemBox?.addEventListener("change", () => {
      if (highMemBox.checked && hiddenInput.value === "cpu") {
        selectValue("cpu:high-mem", false);
      } else if (!highMemBox.checked && hiddenInput.value === "cpu:high-mem") {
        selectValue("cpu", false);
      } else {
        syncHighMemUi();
      }
    });

    selectValue(hiddenInput.value || "cpu", false);
  }
})();
