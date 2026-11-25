#!/usr/bin/env python3
"""Automated conformance and backend integration tests for MiniMax-H3 Web Dashboard."""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from h3_cli_monitor import _clear_shutdown_marker, fetch_snapshot, render_human  # noqa: E402
from server import create_handler  # noqa: E402


class MarkupInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.refs: list[str] = []
        self.label_refs: list[str] = []
        self.buttons: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        for attr in ("href", "src"):
            if values.get(attr):
                self.refs.append(values[attr])
        for attr in ("aria-labelledby", "aria-describedby"):
            if values.get(attr):
                self.label_refs.extend(values[attr].split())
        if tag == "button":
            self.buttons.append(values)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


class SageConformanceTests(unittest.TestCase):
    def test_html_semantics_and_local_refs(self) -> None:
        html_path = ROOT / "web" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        inspector = MarkupInspector()
        inspector.feed(html)

        self.assertEqual(len(inspector.ids), len(set(inspector.ids)), "Duplicate HTML IDs found")
        missing_aria_refs = set(inspector.label_refs) - set(inspector.ids)
        self.assertFalse(missing_aria_refs, f"Broken ARIA references: {missing_aria_refs}")
        self.assertTrue(all(b.get("type") == "button" for b in inspector.buttons), "Every <button> must specify type='button'")

        for ref in inspector.refs:
            parsed = urlsplit(ref)
            self.assertFalse(parsed.scheme or parsed.netloc, f"Remote asset reference disallowed: {ref}")
            target = (html_path.parent / unquote(parsed.path)).resolve()
            self.assertTrue(target.is_relative_to(ROOT / "web"), f"Asset escapes web/: {ref}")
            self.assertTrue(target.is_file(), f"Missing local asset: {ref}")

        self.assertIn('<meta name="text-scale" content="scale">', html)
        self.assertIn('<meta name="color-scheme" content="light dark">', html)
        self.assertIn('role="toolbar"', html)
        self.assertIn('focusgroup="toolbar wrap"', html)
        self.assertIn('scope="col"', html)
        self.assertIn("aria-sort=", html)
        self.assertIn("keepalive-now", inspector.ids)
        self.assertIn("shutdown-now", inspector.ids)
        self.assertIn("shutdown-dialog", inspector.ids)
        self.assertIn("uptime-card", inspector.ids)
        self.assertIn("uptime-range-group", inspector.ids)
        self.assertIn("uptime-distribution", inspector.ids)
        self.assertIn("uptime-table-body", inspector.ids)
        self.assertIn('focusgroup="tablist nomemory"', html)
        self.assertIn("instance-history-trigger", inspector.ids)
        self.assertIn("instance-history-menu", inspector.ids)
        tabs_start = html.index('id="instance-tabs"')
        tabs_end = html.index("</div>", tabs_start)
        self.assertGreater(html.index('id="instance-history-trigger"'), tabs_end)

    def test_css_sage_rules(self) -> None:
        for css_path in (ROOT / "web" / "styles").glob("*.css"):
            css = css_path.read_text(encoding="utf-8")
            self.assertIn("@layer base, theme, components, utilities;", css, css_path.name)
            self.assertIsNone(re.search(r"font-size\s*:\s*[\d.]+px", css), f"Hardcoded px font-size in {css_path.name}")
            self.assertIsNone(re.search(r"^\s*\*\s*\{", css, re.M), f"Global * reset in {css_path.name}")
            for ref in re.findall(r"url\([\"']?([^\"')]+)", css):
                target = (css_path.parent / ref).resolve()
                self.assertTrue(target.is_relative_to(ROOT / "web"), f"CSS asset escapes web/: {ref}")
                self.assertTrue(target.is_file(), f"Missing CSS asset: {ref}")

        ash_css = (ROOT / "web" / "styles" / "ash-marble.css").read_text(encoding="utf-8")
        self.assertNotIn("overflow: hidden", ash_css)
        opacity_match = re.search(r"ash-marble-card::before\s*\{[^}]*opacity:\s*([\d.]+)", ash_css, re.S)
        self.assertIsNotNone(opacity_match)
        self.assertLessEqual(float(opacity_match.group(1)), 0.12)

    def test_js_no_unsafe_dom_apis(self) -> None:
        for js_path in (ROOT / "web").glob("*.js"):
            js = js_path.read_text(encoding="utf-8")
            for forbidden in (".innerHTML", "insertAdjacentHTML", "eval(", "document.write("):
                self.assertNotIn(forbidden, js, f"Unsafe API {forbidden} in {js_path.name}")

    def test_js_polling_race_focus_and_chart_cap_invariants(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        # Single-flight post-completion polling (no setInterval)
        self.assertNotIn("setInterval(", app_js)
        self.assertIn("scheduleNextPoll()", app_js)
        self.assertIn("if (isPoll && (appState.inFlightSnapshot || appState.isKeepingAlive))", app_js)
        # Demo switch must increment requestSeq and abort in-flight request BEFORE entering demo branch
        load_fn_start = app_js.index("async function loadSnapshot(")
        load_fn_end = app_js.index("function updatePollTimer()", load_fn_start)
        load_body = app_js[load_fn_start:load_fn_end]
        self.assertLess(load_body.index("++appState.requestSeq"), load_body.index('appState.sourceMode === "demo"'))
        self.assertLess(load_body.index("appState.abortController.abort()"), load_body.index('appState.sourceMode === "demo"'))
        # Chart bar rendering cap
        self.assertIn("const MAX_CHART_BARS = 200;", app_js)
        # Focus restoration after polling DOM rebuild
        self.assertIn("appState.lastFocusedTrigger.isConnected", app_js)
        self.assertIn("[data-detail-file]", app_js)
        # Delegated tooltips, instance-tab keyboard & history popover
        self.assertIn("setupDelegatedTooltips", app_js)
        self.assertIn("setupInstanceHistoryPopover", app_js)
        self.assertIn("setupInstanceTabsKeyboard", app_js)

    def test_ash_marble_and_sage_token_contrast_calibration(self) -> None:
        def normalize(val: str) -> str:
            return re.sub(r"(?<![\w.])0(?=\.\d)", "", re.sub(r"\s+", "", val).lower())

        def css_values(css: str, selector: str) -> dict[str, str]:
            match = re.search(r"(?:^|\n)\s*" + re.escape(selector) + r"\s*\{([^{}]*)\}", css)
            self.assertIsNotNone(match, f"Missing CSS selector: {selector}")
            return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", match.group(1)))

        def luminance(color: str) -> float:
            comps = [int(color[i : i + 2], 16) / 255.0 for i in (1, 3, 5)]
            lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in comps]
            return sum(x * w for x, w in zip(lin, (0.2126, 0.7152, 0.0722)))

        def contrast(c1: str, c2: str) -> float:
            high, low = sorted([luminance(c1), luminance(c2)], reverse=True)
            return (high + 0.05) / (low + 0.05)

        def blend(fg: str, bg: str, alpha: float) -> str:
            return "#" + "".join(
                f"{math.floor(int(fg[i : i + 2], 16) * alpha + int(bg[i : i + 2], 16) * (1 - alpha)):02x}"
                for i in (1, 3, 5)
            )

        base_css = (ROOT / "web" / "styles" / "base.css").read_text(encoding="utf-8")
        ash_css = (ROOT / "web" / "styles" / "ash-marble.css").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

        # AUDIT-0002 / AUDIT-0003 layout & markup requirements
        self.assertIn("padding-block-start: 0.5rem;", base_css)
        self.assertIn(":is(.chart-scroll, .table-scroll):dir(rtl)", base_css)
        self.assertIn(".instance-tab-name", base_css)
        self.assertIn('.instance-tab[aria-selected="true"]::after', base_css)
        self.assertIn('.instance-tab[data-liveness="offline"]', base_css)
        self.assertIn(".instance-history-menu", base_css)
        self.assertIn(".sr-only", base_css)
        self.assertIn("<noscript>", html)
        self.assertIn('id="mode-table-foot"', html)
        self.assertIn('id="uptime-table-foot"', html)
        self.assertIn('href="styles/fonts.css"', html)

        fonts_css = (ROOT / "web" / "styles" / "fonts.css").read_text(encoding="utf-8")
        for family in ("'Space Grotesk'", "'Space Mono'", "'Noto Sans SC'"):
            self.assertIn(f"font-family: {family};", fonts_css)

        ofl_path = ROOT / "web" / "assets" / "fonts" / "OFL.txt"
        self.assertTrue(ofl_path.is_file(), "OFL-1.1 license text must be bundled with self-hosted fonts")
        ofl_text = ofl_path.read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", ofl_text)
        for attribution in ("Space Grotesk", "Space Mono", "Noto Sans SC"):
            self.assertIn(attribution, ofl_text)

        # Verify critical solid danger button and its hover state maintain >= 4.5:1 contrast with white text
        root_vals = css_values(base_css, ":root")
        self.assertGreaterEqual(contrast(root_vals["critical"], "#ffffff"), 4.5, "critical on #ffffff")
        self.assertIn("critical-hover", root_vals, "Missing explicit --critical-hover token")
        self.assertGreaterEqual(contrast(root_vals["critical-hover"], "#ffffff"), 4.5, "critical-hover on #ffffff")
        hover_match = re.search(
            r"\.danger-solid-button:hover[^{]*\{([^}]*)\}",
            base_css,
            re.S,
        )
        self.assertIsNotNone(hover_match)
        self.assertNotIn("opacity:", hover_match.group(1), "danger-solid-button:hover must not lower opacity")
        self.assertIn("var(--critical-hover)", hover_match.group(1))

        # Verify Sage v1.1.0 base light and dark tokens
        sage_expected = {
            ":root": {
                "page": "#c3ccb6", "surface-1": "#d8dfcf", "surface-2": "#b8c2ab",
                "ink-1": "#142d26", "ink-2": "#2f3b33", "ink-3": "#434f46",
                "grid": "#adb8a2", "axis": "#8c977f",
                "hairline": "rgba(20, 28, 22, .16)", "wash": "rgba(20, 28, 22, .07)", "focus": "#2a78d6",
                "s1": "#2a78d6", "s2": "#eb6834", "s3": "#169a8a", "s4": "#eda100", "s5": "#e87ba4", "other": "#858f7e",
                "seq": ["#b8c2ab", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
                "heat": ["#b8c2ab", "#8daac3", "#7195b4", "#5680a5", "#3b6b96", "#2e5a82", "#224a6e", "#18395a"],
            },
            ':root[data-resolved-theme="dark"]': {
                "page": "#0d0d0d", "surface-1": "#1a1a19", "surface-2": "#262624",
                "ink-1": "#ffffff", "ink-2": "#c3c2b7", "ink-3": "#898781",
                "grid": "#2c2c2a", "axis": "#383835",
                "hairline": "rgba(255, 255, 255, .10)", "wash": "rgba(255, 255, 255, .06)", "focus": "#3987e5",
                "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#c98500", "s5": "#d55181", "other": "#5d5c57",
                "seq": ["#262624", "#0d366b", "#104281", "#184f95", "#1c5cab", "#2a78d6", "#5598e7", "#9ec5f4"],
                "heat": ["#262624", "#293b4b", "#3e5366", "#536c80", "#68849b", "#7e9db5", "#94b6cf", "#abcfe9"],
            },
        }
        for sel, expected in sage_expected.items():
            vals = css_values(base_css, sel)
            for k, v in expected.items():
                if k in ("seq", "heat"):
                    for idx, hex_val in enumerate(v):
                        self.assertEqual(normalize(vals[f"{k}-{idx}"]), normalize(hex_val), f"{sel}.{k}-{idx}")
                else:
                    self.assertEqual(normalize(vals[k]), normalize(v), f"{sel}.{k}")

        # Verify Ash Marble SVG texture structure and compute conservative overlapping alpha bound
        svg_path = ROOT / "web" / "assets" / "ash-marble.svg"
        texture = ET.parse(svg_path).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        self.assertEqual(list(texture.iter(f"{ns}script")), [])
        self.assertEqual([c.tag for c in texture], [f"{ns}defs", f"{ns}g"])
        self.assertTrue(
            all(not any(k == "style" or k.lower().startswith("on") or k.endswith("href") for k in n.attrib) for n in texture.iter())
        )
        paths = list(texture.iter(f"{ns}path"))
        self.assertEqual(len(paths), 7)
        self.assertTrue(all("opacity" in p.attrib and "stroke" not in p.attrib for p in paths))
        group = texture.find(f"{ns}g")
        self.assertIsNotNone(group)
        self.assertEqual(group.attrib.get("stroke"), "#71808A")
        filters = list(texture.iter(f"{ns}filter"))
        self.assertEqual(len(filters), 1)
        self.assertEqual([n.tag for n in filters[0]], [f"{ns}feTurbulence", f"{ns}feDisplacementMap"])
        max_alpha = 1.0 - math.prod(1.0 - float(p.attrib["opacity"]) for p in paths)

        # Verify Ash Marble pale & gray tokens (explicit + no-JS fallback) and contrast invariants
        ash_tones = {
            "pale": {
                "page": "#F1F2F3", "surface-1": "#F9FAFB", "surface-2": "#E4E9EC",
                "ink-1": "#313A41", "ink-2": "#55636C", "ink-3": "#646F76",
                "grid": "#E0E6E9", "axis": "#6E7C84", "hairline": "#D9DFE2",
                "wash": "rgba(49, 58, 65, .055)", "focus": "#2A78D6",
                "accent-hero": "#345F78", "accent-secondary": "#376C67",
                "s1": "#3B6B87", "s2": "#4B7975", "s3": "#5C6380", "s4": "#7F6B58", "s5": "#7B5D6B", "other": "#78868F",
                "data-prior-period": "#D5DFE4", "data-prior-border": "#687780", "data-current-period": "#3B6B87",
                "seq": ["#E4E9EC", "#D2E0E8", "#B3CBD9", "#8FB0C4", "#6892AC", "#3B6B87", "#2D536A", "#1F3B4D"],
                "heat": ["#E4E9EC", "#C8D9D7", "#A5C1BE", "#80A7A3", "#5D8B86", "#3E6E69", "#2E5450", "#1F3A38"],
            },
            "gray": {
                "page": "#E1E4E5", "surface-1": "#F0F1F1", "surface-2": "#DCE1E2",
                "ink-1": "#30383E", "ink-2": "#505F68", "ink-3": "#5A656D",
                "grid": "#D5DBDD", "axis": "#66747C", "hairline": "#CBD1D3",
                "wash": "rgba(48, 56, 62, .06)", "focus": "#2A78D6",
                "accent-hero": "#345F78", "accent-secondary": "#376C67",
                "s1": "#35627C", "s2": "#436E6A", "s3": "#535A75", "s4": "#73604E", "s5": "#705361", "other": "#6B7982",
                "data-prior-period": "#C7D2D7", "data-prior-border": "#5D6B74", "data-current-period": "#35627C",
                "seq": ["#DCE1E2", "#C6D6DF", "#A6C0CF", "#82A4B8", "#5D869F", "#35627C", "#284B60", "#1B3545"],
                "heat": ["#DCE1E2", "#BDD0CE", "#9AB8B4", "#769E99", "#54827D", "#376560", "#284C48", "#1A3331"],
            },
        }
        for base_sel in (
            ':root[data-palette="ash-marble"][data-resolved-theme="light"]',
            ':root[data-palette="ash-marble"]:not([data-resolved-theme])',
        ):
            pale_vals = css_values(ash_css, base_sel)
            gray_vals = pale_vals | css_values(ash_css, base_sel + '[data-ash-tone="gray"]')
            for tone_name, vals in (("pale", pale_vals), ("gray", gray_vals)):
                exp = ash_tones[tone_name]
                for k, v in exp.items():
                    if k in ("seq", "heat"):
                        for idx, hex_val in enumerate(v):
                            self.assertEqual(normalize(vals[f"{k}-{idx}"]), normalize(hex_val), f"{tone_name}.{k}-{idx}")
                    else:
                        self.assertEqual(normalize(vals[k]), normalize(v), f"{tone_name}.{k}")
                for s in ("s1", "s2", "s3", "s4", "s5", "other"):
                    self.assertEqual(vals[f"data-{s}"], f"var(--{s})")

        for tone_name, colors in ash_tones.items():
            darkest = blend("#71808A", colors["surface-1"], max_alpha * 0.12)
            for ink in ("ink-1", "ink-2", "ink-3"):
                for bg_name, bg in (("page", colors["page"]), ("surface-1", colors["surface-1"]), ("textured", darkest)):
                    self.assertGreaterEqual(contrast(colors[ink], bg), 4.5, f"{tone_name}.{ink} on {bg_name}")
            for ink in ("ink-1", "ink-2"):
                self.assertGreaterEqual(contrast(colors[ink], colors["surface-2"]), 4.5, f"{tone_name}.{ink} on surface-2")
            self.assertGreaterEqual(contrast(colors["accent-hero"], colors["page"]), 4.5, f"{tone_name} hero contrast")
            self.assertGreaterEqual(contrast(colors["surface-2"], colors["surface-1"]), 1.15, f"{tone_name} track/card contrast")
            for series in ("s1", "s2", "s3", "s4", "s5", "other"):
                for bg_name, bg in (("surface-1", colors["surface-1"]), ("textured", darkest), ("surface-2", colors["surface-2"])):
                    self.assertGreaterEqual(contrast(colors[series], bg), 3.0, f"{tone_name}.{series} on {bg_name}")
            for ramp in ("seq", "heat"):
                steps = colors[ramp]
                self.assertEqual(normalize(steps[0]), normalize(colors["surface-2"]))
                lums = [luminance(c) for c in steps]
                self.assertTrue(all(lums[i] > lums[i + 1] for i in range(len(lums) - 1)), f"{tone_name}.{ramp} not decreasing")
            self.assertGreaterEqual(contrast(colors["seq"][5], colors["surface-1"]), 3.0)
            self.assertGreaterEqual(contrast(colors["seq"][5], colors["seq"][1]), 3.0)


class BackendAndApiTests(unittest.TestCase):
    def test_missing_endpoint_is_honestly_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = fetch_snapshot(Path(tmp) / "active_endpoint.json")
            self.assertFalse(snap["online"])
            self.assertFalse(snap["ep_exists"])
            self.assertIn("未找到", snap["error"])
            human = render_human(snap)
            self.assertIn("离线", human)

    def test_live_mock_endpoint_and_server_security(self) -> None:
        seen_auth: list[str | None] = []
        metrics_enabled = {"value": True}
        keepalive_route_enabled = {"value": True}
        colab_online = {"value": True}
        offline_http_code = {"value": 502}
        idle_seconds = {"value": 90}
        secret = "super-secret-token-xyz"

        class MockColabHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    if not colab_online["value"]:
                        self.send_error(offline_http_code["value"])
                        return
                    payload = {
                        "active_jobs": 1,
                        "idle_seconds": idle_seconds["value"],
                        "idle_timeout_minutes": 30,
                        "comfy_alive": True,
                        "dit_tier": "int8_convrot",
                        "gdrive_mode": "mount_sync",
                    }
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if self.path == "/v1/metrics":
                    seen_auth.append(self.headers.get("Authorization"))
                    if not metrics_enabled["value"]:
                        self.send_error(404)
                        return
                    payload = {
                        "gpu": {
                            "available": True,
                            "name": "NVIDIA L4",
                            "util_gpu_pct": 72,
                            "vram_used_mib": 16000,
                            "vram_total_mib": 23034,
                            "vram_pct": 69.5,
                            "temp_c": 65,
                            "power_w": 58.2,
                        },
                        "ram": {"used_gib": 18.2, "total_gib": 53.0, "pct": 34.3},
                        "disk": {"used_gib": 50.0, "total_gib": 112.6, "free_gib": 62.6},
                        "jobs": {"running": 1, "queued": 0, "succeeded": 5, "failed": 0},
                    }
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if self.path == "/v1/jobs/__keepalive__":
                    auth = self.headers.get("Authorization")
                    seen_auth.append(auth)
                    if auth != f"Bearer {secret}":
                        body = json.dumps({"detail": "Invalid API Key"}).encode("utf-8")
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    idle_seconds["value"] = 0
                    body = json.dumps({"detail": "Job not found"}).encode("utf-8")
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                if length > 0:
                    self.rfile.read(length)
                if self.path == "/v1/keepalive":
                    auth = self.headers.get("Authorization")
                    seen_auth.append(auth)
                    if auth != f"Bearer {secret}":
                        body = json.dumps({"detail": "Invalid API Key"}).encode("utf-8")
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if not keepalive_route_enabled["value"]:
                        self.send_error(404)
                        return
                    idle_seconds["value"] = 0
                    body = json.dumps({"status": "ok", "idle_seconds": 0}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/v1/shutdown":
                    auth = self.headers.get("Authorization")
                    seen_auth.append(auth)
                    if auth != f"Bearer {secret}":
                        body = json.dumps({"detail": "Invalid API Key"}).encode("utf-8")
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    colab_online["value"] = False
                    body = json.dumps(
                        {"status": "shutting_down", "message": "Colab L4 实例将在 3 秒后安全释放"}
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

        mock_colab = HTTPServer(("127.0.0.1", 0), MockColabHandler)
        colab_thread = threading.Thread(target=mock_colab.serve_forever, daemon=True)
        colab_thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                out_dir = tmp_dir / "outputs"
                out_dir.mkdir()
                (out_dir / "h3_edit_20261115_010000_0001.json").write_text(
                    json.dumps(
                        {
                            "mode": "edit",
                            "dit_tier": "int8_convrot",
                            "prompt": "Test courtyard",
                            "width": 2048,
                            "height": 2048,
                            "seed": 42,
                            "num_refs": 1,
                            "elapsed_sec": 44.5,
                            "created_at": "2026-11-15T01:00:00",
                        }
                    ),
                    encoding="utf-8",
                )
                (out_dir / "h3_edit_20261115_010000_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")

                ep_file = tmp_dir / "active_endpoint.json"
                ep_file.write_text(
                    json.dumps(
                        {
                            "base_url": f"http://127.0.0.1:{mock_colab.server_port}",
                            "api_key": secret,
                            "updated_at": "2026-11-15T00:50:00",
                            "dit_tier": "int8_convrot",
                        }
                    ),
                    encoding="utf-8",
                )

                web_server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(ep_file))
                web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
                web_thread.start()

                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    base = f"http://127.0.0.1:{web_server.server_port}"
                    with opener.open(f"{base}/api/snapshot?limit=6", timeout=5) as resp:
                        raw = resp.read().decode("utf-8")
                    self.assertNotIn(secret, raw, "api_key must NEVER be leaked to frontend")
                    snap = json.loads(raw)
                    self.assertTrue(snap["online"])
                    self.assertTrue(snap["has_api_key"])
                    self.assertEqual(snap["metrics"]["gpu"]["name"], "NVIDIA L4")
                    self.assertEqual(len(snap["recent_outputs"]), 1)
                    self.assertTrue(snap["recent_outputs"][0]["has_image"])
                    self.assertEqual(seen_auth[-1], f"Bearer {secret}")

                    # Verify 404 metrics graceful degradation
                    metrics_enabled["value"] = False
                    with opener.open(f"{base}/api/snapshot?limit=6", timeout=5) as resp:
                        snap_404 = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(snap_404["online"])
                    self.assertIsNone(snap_404["metrics"])
                    self.assertIn("尚未加载 /v1/metrics", snap_404["metrics_note"])

                    # Verify keepalive CSRF guard (missing X-H3-Action returns 403)
                    req_no_hdr = urllib.request.Request(f"{base}/api/keepalive?limit=6", data=b"", method="POST")
                    with self.assertRaises(urllib.error.HTTPError) as ctx_csrf:
                        opener.open(req_no_hdr, timeout=5)
                    self.assertEqual(ctx_csrf.exception.code, 403)

                    # Verify keepalive via direct /v1/keepalive route
                    req_ka = urllib.request.Request(
                        f"{base}/api/keepalive?limit=6",
                        data=b"",
                        headers={"X-H3-Action": "keepalive"},
                        method="POST",
                    )
                    with opener.open(req_ka, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        ka_raw = resp.read().decode("utf-8")
                    self.assertNotIn(secret, ka_raw, "api_key must NEVER be leaked by /api/keepalive")
                    ka_res = json.loads(ka_raw)
                    self.assertTrue(ka_res["ok"])
                    self.assertEqual(ka_res["via"], "keepalive_endpoint")
                    self.assertEqual(ka_res["snapshot"]["health"]["idle_seconds"], 0)

                    # Verify keepalive fallback for older Colab runtime without /v1/keepalive
                    idle_seconds["value"] = 120
                    keepalive_route_enabled["value"] = False
                    req_ka_fb = urllib.request.Request(
                        f"{base}/api/keepalive?limit=6",
                        data=b"",
                        headers={"X-H3-Action": "keepalive"},
                        method="POST",
                    )
                    with opener.open(req_ka_fb, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        fb_res = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(fb_res["ok"])
                    self.assertEqual(fb_res["via"], "jobs_auth_fallback")
                    self.assertEqual(fb_res["snapshot"]["health"]["idle_seconds"], 0)

                    # Verify image serving and traversal blocking
                    with opener.open(f"{base}/api/output-image?file=h3_edit_20261115_010000_0001.png", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        self.assertEqual(resp.read()[:4], b"\x89PNG")

                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        opener.open(f"{base}/api/output-image?file=../active_endpoint.json", timeout=5)
                    self.assertEqual(ctx.exception.code, 400)

                    # Verify shutdown CSRF guard (mismatched X-H3-Action returns 403)
                    req_sd_wrong_hdr = urllib.request.Request(
                        f"{base}/api/shutdown?limit=6",
                        data=b"",
                        headers={"X-H3-Action": "keepalive"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx_sd_csrf:
                        opener.open(req_sd_wrong_hdr, timeout=5)
                    self.assertEqual(ctx_sd_csrf.exception.code, 403)

                    # Verify shutdown proxy and credential isolation
                    req_sd = urllib.request.Request(
                        f"{base}/api/shutdown?limit=6",
                        data=b"",
                        headers={"X-H3-Action": "shutdown"},
                        method="POST",
                    )
                    with opener.open(req_sd, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        sd_raw = resp.read().decode("utf-8")
                    self.assertNotIn(secret, sd_raw, "api_key must NEVER be leaked by /api/shutdown")
                    sd_res = json.loads(sd_raw)
                    self.assertTrue(sd_res["ok"])
                    self.assertEqual(sd_res["status"], "shutting_down")
                    self.assertFalse(sd_res["snapshot"]["online"])
                    self.assertEqual(sd_res["snapshot"]["offline_reason"], "shutdown_confirmed")
                    self.assertIn("已关机", render_human(sd_res["snapshot"]))

                    # Verify HTTP 530 without local marker classifies as closed tunnel shutdown
                    _clear_shutdown_marker(f"http://127.0.0.1:{mock_colab.server_port}")
                    offline_http_code["value"] = 530
                    snap_530 = fetch_snapshot(ep_file, output_limit=6)
                    self.assertFalse(snap_530["online"])
                    self.assertEqual(snap_530["offline_reason"], "tunnel_closed")
                    self.assertIn("已关机", render_human(snap_530))
                finally:
                    _clear_shutdown_marker(f"http://127.0.0.1:{mock_colab.server_port}")
                    web_server.shutdown()
                    web_server.server_close()
        finally:
            mock_colab.shutdown()
            mock_colab.server_close()

    def test_uptime_tracking_gpu_and_cpu_only_persistence(self) -> None:
        secret = "uptime-secret-token-abc"
        state = {
            "online": True,
            "metrics_enabled": True,
            "gpu": {"available": True, "name": "NVIDIA L4", "util_gpu_pct": 55, "vram_used_mib": 12000, "vram_total_mib": 23034, "vram_pct": 52.1, "temp_c": 61, "power_w": 52.0},
            "idle_seconds": 90,
        }

        class MockRuntimeHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    if not state["online"]:
                        self.send_error(530)
                        return
                    body = json.dumps(
                        {
                            "active_jobs": 0,
                            "idle_seconds": state["idle_seconds"],
                            "idle_timeout_minutes": 30,
                            "comfy_alive": True,
                            "dit_tier": "int8_convrot",
                            "gdrive_mode": "mount_sync",
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if self.path == "/v1/metrics":
                    if not state["metrics_enabled"]:
                        self.send_error(404)
                        return
                    body = json.dumps(
                        {
                            "gpu": state["gpu"],
                            "ram": {"used_gib": 12.0, "total_gib": 53.0, "pct": 22.6},
                            "disk": {"used_gib": 40.0, "total_gib": 112.6, "free_gib": 72.6},
                            "jobs": {"running": 0, "queued": 0, "succeeded": 1, "failed": 0},
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                if length > 0:
                    self.rfile.read(length)
                if self.path == "/v1/shutdown":
                    state["online"] = False
                    body = json.dumps({"status": "shutting_down", "message": "实例正在释放"}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

        mock_colab = HTTPServer(("127.0.0.1", 0), MockRuntimeHandler)
        colab_thread = threading.Thread(target=mock_colab.serve_forever, daemon=True)
        colab_thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                ep_file = tmp_dir / "active_endpoint.json"
                base_ep_url = f"http://127.0.0.1:{mock_colab.server_port}"
                ep_file.write_text(
                    json.dumps(
                        {
                            "base_url": base_ep_url,
                            "api_key": secret,
                            "updated_at": "2026-11-17T01:00:00",
                            "dit_tier": "int8_convrot",
                        }
                    ),
                    encoding="utf-8",
                )

                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                web_server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(ep_file))
                web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
                web_thread.start()

                try:
                    base = f"http://127.0.0.1:{web_server.server_port}"
                    with opener.open(f"{base}/api/snapshot?limit=6", timeout=5) as resp:
                        raw = resp.read().decode("utf-8")
                    self.assertNotIn(secret, raw)
                    snap1 = json.loads(raw)
                    up1 = snap1["uptime_summary"]
                    self.assertTrue(up1["current_online"])
                    self.assertEqual(up1["current_hardware"], "NVIDIA L4")
                    self.assertEqual(up1["current_category"], "gpu")

                    for win_key in ("all", "today", "7d", "current"):
                        win = up1["windows"][win_key]
                        by_name = {item["hardware_key"]: item for item in win["items"]}
                        self.assertIn("NVIDIA L4", by_name)
                        self.assertIn("CPU Only", by_name)
                        self.assertTrue(by_name["NVIDIA L4"]["is_current"])
                        self.assertFalse(by_name["CPU Only"]["is_current"])
                        self.assertEqual(by_name["CPU Only"]["seconds"], 0.0)
                        if win_key != "today":
                            self.assertGreaterEqual(by_name["NVIDIA L4"]["seconds"], 90.0)

                    # Transient /v1/metrics 404 on the same session must preserve known GPU identity
                    state["metrics_enabled"] = False
                    with opener.open(f"{base}/api/snapshot?limit=6", timeout=5) as resp:
                        snap_404 = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(snap_404["uptime_summary"]["current_hardware"], "NVIDIA L4")
                    self.assertEqual(snap_404["uptime_summary"]["current_category"], "gpu")

                    # Switch to a new CPU Only machine session
                    state["metrics_enabled"] = True
                    state["gpu"] = {"available": False}
                    state["idle_seconds"] = 60
                    ep_file.write_text(
                        json.dumps(
                            {
                                "base_url": base_ep_url,
                                "api_key": secret,
                                "updated_at": "2026-11-17T02:00:00",
                                "dit_tier": "cpu_only",
                            }
                        ),
                        encoding="utf-8",
                    )
                    with opener.open(f"{base}/api/snapshot?limit=6", timeout=5) as resp:
                        snap_cpu = json.loads(resp.read().decode("utf-8"))
                    up_cpu = snap_cpu["uptime_summary"]
                    self.assertEqual(up_cpu["current_hardware"], "CPU Only")
                    self.assertEqual(up_cpu["current_category"], "cpu_only")

                    all_items = {item["hardware_key"]: item for item in up_cpu["windows"]["all"]["items"]}
                    self.assertGreaterEqual(all_items["NVIDIA L4"]["seconds"], 90.0)
                    self.assertGreaterEqual(all_items["CPU Only"]["seconds"], 60.0)
                    self.assertGreaterEqual(up_cpu["windows"]["all"]["total_seconds"], 150.0)

                    cur_items = {item["hardware_key"]: item for item in up_cpu["windows"]["current"]["items"]}
                    self.assertGreaterEqual(cur_items["CPU Only"]["seconds"], 60.0)
                    self.assertEqual(cur_items["NVIDIA L4"]["seconds"], 0.0)
                finally:
                    web_server.shutdown()
                    web_server.server_close()

                # Verify uptime_ledger.json persistence across server restarts and no secret leakage
                ledger_path = tmp_dir / "uptime_ledger.json"
                self.assertTrue(ledger_path.is_file())
                ledger_raw = ledger_path.read_text(encoding="utf-8")
                self.assertNotIn(secret, ledger_raw)

                web_server2 = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(ep_file))
                web_thread2 = threading.Thread(target=web_server2.serve_forever, daemon=True)
                web_thread2.start()
                try:
                    base2 = f"http://127.0.0.1:{web_server2.server_port}"
                    with opener.open(f"{base2}/api/snapshot?limit=6", timeout=5) as resp:
                        snap_reloaded = json.loads(resp.read().decode("utf-8"))
                    reloaded_all = {
                        item["hardware_key"]: item for item in snap_reloaded["uptime_summary"]["windows"]["all"]["items"]
                    }
                    self.assertGreaterEqual(reloaded_all["NVIDIA L4"]["seconds"], 90.0)
                    self.assertGreaterEqual(reloaded_all["CPU Only"]["seconds"], 60.0)

                    # Shutdown must clear current session while preserving historical totals
                    req_sd = urllib.request.Request(
                        f"{base2}/api/shutdown?limit=6",
                        data=b"",
                        headers={"X-H3-Action": "shutdown"},
                        method="POST",
                    )
                    with opener.open(req_sd, timeout=5) as resp:
                        sd_res = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(sd_res["ok"])
                    up_sd = sd_res["snapshot"]["uptime_summary"]
                    self.assertFalse(up_sd["current_online"])
                    self.assertEqual(up_sd["windows"]["current"]["total_seconds"], 0.0)
                    self.assertGreaterEqual(up_sd["windows"]["all"]["total_seconds"], 150.0)
                finally:
                    _clear_shutdown_marker(base_ep_url)
                    web_server2.shutdown()
                    web_server2.server_close()
        finally:
            mock_colab.shutdown()
            mock_colab.server_close()

    def test_quiet_server_suppresses_client_disconnect_noise(self) -> None:
        import contextlib
        import io
        from server import QuietThreadingHTTPServer

        with tempfile.TemporaryDirectory() as tmp:
            ep_file = Path(tmp) / "active_endpoint.json"
            srv = QuietThreadingHTTPServer(("127.0.0.1", 0), create_handler(ep_file))
            try:
                stderr_buf = io.StringIO()
                with contextlib.redirect_stderr(stderr_buf):
                    try:
                        raise ConnectionAbortedError(10053, "client aborted")
                    except ConnectionAbortedError:
                        srv.handle_error(None, ("127.0.0.1", 56970))
                self.assertEqual(stderr_buf.getvalue(), "")

                stderr_buf2 = io.StringIO()
                with contextlib.redirect_stderr(stderr_buf2):
                    try:
                        raise RuntimeError("boom")
                    except RuntimeError:
                        srv.handle_error(None, ("127.0.0.1", 56970))
                self.assertIn("[服务端提示]", stderr_buf2.getvalue())
                self.assertNotIn("Traceback", stderr_buf2.getvalue())
            finally:
                srv.server_close()

    def test_host_origin_rebinding_guard_and_remote_bind_refusal(self) -> None:
        import contextlib
        import http.client
        import io
        import subprocess
        from server import run_server

        with tempfile.TemporaryDirectory() as tmp:
            ep_file = Path(tmp) / "active_endpoint.json"
            # Non-loopback bind without allow_remote must exit with code 2
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                with self.assertRaises(SystemExit) as cm:
                    run_server(ep_path=ep_file, host="0.0.0.0", port=0, open_browser=False, allow_remote=False)
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("--allow-remote", stderr_buf.getvalue())

            # DNS rebinding Host & cross-origin Origin must be rejected with 403
            srv = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(ep_file, allow_remote=False))
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
                conn.request("GET", "/api/snapshot", headers={"Host": f"evil.example:{srv.server_port}"})
                resp = conn.getresponse()
                resp.read()
                self.assertEqual(resp.status, 403)
                conn.close()

                conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
                conn.request(
                    "POST",
                    "/api/shutdown",
                    body=b"",
                    headers={
                        "Host": f"127.0.0.1:{srv.server_port}",
                        "Origin": "http://evil.example",
                        "X-H3-Action": "shutdown",
                    },
                )
                resp = conn.getresponse()
                resp.read()
                self.assertEqual(resp.status, 403)
                conn.close()
            finally:
                srv.shutdown()
                srv.server_close()

            # Non-interactive CLI --shutdown must refuse without --yes
            proc = subprocess.run(
                [sys.executable, str(ROOT / "h3_cli_monitor.py"), "--ep", str(ep_file), "--shutdown"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("--yes", proc.stderr)


if __name__ == "__main__":
    unittest.main()
