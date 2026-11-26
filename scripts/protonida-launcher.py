#!/usr/bin/python3
"""Protonida GTK4 GUI Launcher — Linux + Wine/vkd3d-proton Xbox Series X|S Runner."""

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

CONFIG_DIR = Path.home() / ".config" / "protonida"
CONFIG_FILE = CONFIG_DIR / "launcher.json"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VICTORY_EXE = REPO_ROOT / "target" / "x86_64-pc-windows-gnullvm" / "release" / "victory.exe"
DEFAULT_WINESERVER = Path.home() / ".local" / "share" / "xcl-linux" / "wineserver-runtime" / "server" / "wineserver"
DEFAULT_WINEPREFIX = Path.home() / ".local" / "share" / "xcl-linux" / "prefix"
DEFAULT_XCL_LINUX_HOME = Path.home() / ".local" / "share" / "xcl-linux"
DEFAULT_GAME_EXE = Path("/mnt/NVMe/GTAXBOX/GTA6.Xbox.v1.0.2.1-CYBERLEEK/game_scarlett_master.exe")
DEFAULT_SKIP_PIPELINES = "7115e46a79c78f98,59b1ba494f18c894,b8ca32a6899b87fb,659fe9cae59dbd88"

LOG_LEVELS = [
    ("warn", "warn (Recommended — lowest CPU overhead)"),
    ("info", "info (FPS & frame-time telemetry)"),
]

RESOLUTIONS = [
    ("1280x720", "1280x720 (720p — Recommended 6 GB)"),
    ("1920x1080", "1920x1080 (1080p)"),
    ("2560x1440", "2560x1440 (1440p)"),
    ("3840x2160", "3840x2160 (4K)"),
]

CONSOLES = [
    ("series-s", "Xbox Series S (Lockhart — Recommended)"),
    ("series-x", "Xbox Series X (Anaconda)"),
]

RTX_MODES = [
    ("rtx_low", "On — Hardware RTGI + Reflections (Low RT Shadows)"),
    ("rtx_full", "On — Full Hardware RTX (RT Dynamic Shadows)"),
    ("off", "Off — Max FPS Screen-Space SH GI"),
]


def default_config() -> dict:
    return {
        "game_exe": str(DEFAULT_GAME_EXE) if DEFAULT_GAME_EXE.exists() else "",
        "victory_exe": str(DEFAULT_VICTORY_EXE),
        "wine_bin": shutil.which("wine") or "/usr/bin/wine",
        "wineprefix": str(DEFAULT_WINEPREFIX),
        "wineserver": str(DEFAULT_WINESERVER) if DEFAULT_WINESERVER.exists() else "",
        "resolution": "1280x720",
        "console": "series-s",
        "rtx_mode": "rtx_low",
        "log_level": "warn",
        "mangohud": False,
        # Advanced
        "xcl_linux_home": str(DEFAULT_XCL_LINUX_HOME),
        "gpu_filter": "NVIDIA",
        "cache_dir": "",
        "upscaler": "6",
        "port_constants": True,
        "port_descriptors": True,
        "port_guard": True,
        "port_journal": True,
        "port_descriptors_every": "32",
        "port_descriptors_check": "2",
        "late_wait": "3",
        "late_wait_lists": "1024",
        "async_submit": True,
        "show_after": False,
        "texture_no_wait": False,
        "guard_rest": "50000",
        "guard_hot": "1000",
        "texture_budget_mb": "768",
        "card_room_for_targets_mb": "2304",
        "card_buffers_min_kb": "64",
        "card_buffers_mb": "1024",
        "inplace_mb": "4096",
        "pipeline_workers": "2",
        "pipeline_wait_ms": "0",
        "ambient_shgi": "2.0",
        "ambient_air": "1.5",
        "skip_pipelines": DEFAULT_SKIP_PIPELINES,
        "extra_env": "",
    }


def load_config() -> dict:
    cfg = default_config()
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def linux_to_wine_path(path_str: str) -> str:
    p = path_str.strip()
    if not p:
        return ""
    if re.match(r"^[A-Za-z]:\\", p):
        return p
    return "Z:" + p.replace("/", "\\")


def update_settings_xml(wineprefix: str, rtx_mode: str) -> None:
    """Sync rtDynamicShadows & ssroSampleCount in settings.xml if present."""
    prefix = Path(wineprefix)
    if not prefix.exists():
        return
    matches = list(prefix.glob("drive_c/users/*/AppData/Local/victory/data/*/storage/settings/settings.xml"))
    if not matches:
        return
    xml_path = matches[0]
    try:
        text = xml_path.read_text(encoding="utf-8")
        rt_dyn = "true" if rtx_mode == "rtx_full" else "false"
        ssro = "2" if rtx_mode == "rtx_full" else "1"
        text = re.sub(
            r'(<rtDynamicShadows\s+value=")[^"]*("\s*/>)',
            rf"\g<1>{rt_dyn}\2",
            text,
        )
        text = re.sub(
            r'(<ssroSampleCount\s+value=")[^"]*("\s*/>)',
            rf"\g<1>{ssro}\2",
            text,
        )
        xml_path.write_text(text, encoding="utf-8")
    except Exception:
        pass


class ProtonidaWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Protonida Launcher")
        self.set_default_size(640, 620)
        self.cfg = load_config()
        self.proc: subprocess.Popen | None = None

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(scroll)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(18)
        root.set_margin_end(18)
        scroll.set_child(root)

        # Header
        header = Gtk.Label()
        header.set_markup(
            "<span size='x-large' weight='bold'>Protonida</span>\n"
            "<span size='small' alpha='75%'>Linux + Wine / vkd3d-proton Xbox Series X|S Launcher</span>"
        )
        header.set_xalign(0.0)
        root.append(header)

        # Main Settings Grid
        main_frame = Gtk.Frame(label="Main Settings")
        main_grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        main_grid.set_margin_top(10)
        main_grid.set_margin_bottom(12)
        main_grid.set_margin_start(12)
        main_grid.set_margin_end(12)
        main_frame.set_child(main_grid)
        root.append(main_frame)

        row = 0
        # Game executable
        self.game_entry = Gtk.Entry()
        self.game_entry.set_hexpand(True)
        self.game_entry.set_placeholder_text("/path/to/game_scarlett_master.exe")
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._on_browse_game)
        game_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        game_box.append(self.game_entry)
        game_box.append(browse_btn)
        self._add_row(main_grid, row, "Game Executable", game_box)
        row += 1

        # Resolution
        self.res_combo = Gtk.DropDown.new_from_strings([label for _, label in RESOLUTIONS])
        self._add_row(main_grid, row, "Resolution", self.res_combo)
        row += 1

        # Console profile (Series S Lockhart vs Series X Anaconda)
        self.console_combo = Gtk.DropDown.new_from_strings([label for _, label in CONSOLES])
        self._add_row(main_grid, row, "Console Profile", self.console_combo)
        row += 1

        # Ray Tracing (RTX)
        self.rtx_combo = Gtk.DropDown.new_from_strings([label for _, label in RTX_MODES])
        self._add_row(main_grid, row, "Ray Tracing (RTX)", self.rtx_combo)
        row += 1

        # MangoHud
        self.mangohud_check = Gtk.CheckButton(label="Enable MangoHud FPS / Frametime Overlay")
        self.mangohud_check.set_sensitive(shutil.which("mangohud") is not None)
        if shutil.which("mangohud") is None:
            self.mangohud_check.set_tooltip_text("mangohud not found in PATH")
        self._add_row(main_grid, row, "Overlay", self.mangohud_check)
        row += 1

        # Log level
        self.log_combo = Gtk.DropDown.new_from_strings([label for _, label in LOG_LEVELS])
        self._add_row(main_grid, row, "Log Level", self.log_combo)

        # Collapsible Advanced Section
        expander = Gtk.Expander(label="Advanced (VICTORY_* / Wine / VRAM / Guards)")
        expander.set_expanded(False)
        adv_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        adv_grid.set_margin_top(10)
        adv_grid.set_margin_bottom(10)
        adv_grid.set_margin_start(12)
        adv_grid.set_margin_end(12)
        expander.set_child(adv_grid)
        root.append(expander)

        arow = 0
        # Renderer Ports checkboxes
        ports_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.port1_chk = Gtk.CheckButton(label="Port 1 (Constants)")
        self.port2_chk = Gtk.CheckButton(label="Port 2 (Descriptors)")
        self.port6_chk = Gtk.CheckButton(label="Port 6 (Guard)")
        self.port7_chk = Gtk.CheckButton(label="Port 7 (Journal)")
        for w in (self.port1_chk, self.port2_chk, self.port6_chk, self.port7_chk):
            ports_box.append(w)
        self._add_row(adv_grid, arow, "Renderer Ports", ports_box)
        arow += 1

        self.async_chk = Gtk.CheckButton(label="VICTORY_ASYNC_SUBMIT=1 (requires LATE_WAIT=3)")
        self._add_row(adv_grid, arow, "Async Submit", self.async_chk)
        arow += 1

        pop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.show_after_chk = Gtk.CheckButton(label="VICTORY_SHOW_AFTER (may pop placeholders)")
        self.tex_nowait_chk = Gtk.CheckButton(label="VICTORY_TEXTURE_NO_WAIT (may pop textures)")
        pop_box.append(self.show_after_chk)
        pop_box.append(self.tex_nowait_chk)
        self._add_row(adv_grid, arow, "Streaming Trade-offs", pop_box)
        arow += 1

        self.adv_entries: dict[str, Gtk.Entry] = {}
        adv_fields = [
            ("upscaler", "VICTORY_UPSCALER", "6"),
            ("late_wait", "VICTORY_LATE_WAIT", "3"),
            ("late_wait_lists", "VICTORY_LATE_WAIT_LISTS", "1024"),
            ("guard_rest", "VICTORY_GUARD_REST (µs)", "50000"),
            ("guard_hot", "VICTORY_GUARD_HOT (ms)", "1000"),
            ("port_descriptors_every", "PORT_DESCRIPTORS_EVERY", "32"),
            ("port_descriptors_check", "PORT_DESCRIPTORS_CHECK", "2"),
            ("texture_budget_mb", "TEXTURE_BUDGET_MB", "768"),
            ("card_room_for_targets_mb", "CARD_ROOM_TARGETS_MB", "2304"),
            ("card_buffers_min_kb", "CARD_BUFFERS_MIN_KB", "64"),
            ("card_buffers_mb", "CARD_BUFFERS_MB", "1024"),
            ("inplace_mb", "VICTORY_INPLACE_MB", "4096"),
            ("pipeline_workers", "PIPELINE_WORKERS", "2"),
            ("pipeline_wait_ms", "PIPELINE_WAIT_MS", "0"),
            ("ambient_shgi", "AMBIENT (when RTX Off)", "2.0"),
            ("ambient_air", "VICTORY_AMBIENT_AIR", "1.5"),
            ("skip_pipelines", "VICTORY_SKIP_PIPELINES", DEFAULT_SKIP_PIPELINES),
            ("victory_exe", "victory.exe Path", str(DEFAULT_VICTORY_EXE)),
            ("wine_bin", "Wine Binary", "/usr/bin/wine"),
            ("wineprefix", "WINEPREFIX", str(DEFAULT_WINEPREFIX)),
            ("wineserver", "Custom WINESERVER", str(DEFAULT_WINESERVER)),
            ("xcl_linux_home", "XCL_LINUX_HOME (llvm-mingw/dxc/cache)", str(DEFAULT_XCL_LINUX_HOME)),
            ("gpu_filter", "GPU Filter (NVIDIA / AMD / Intel)", "NVIDIA"),
            ("cache_dir", "VICTORY_CACHE_DIR (optional)", ""),
        ]
        for key, label, placeholder in adv_fields:
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_placeholder_text(placeholder)
            self.adv_entries[key] = entry
            self._add_row(adv_grid, arow, label, entry)
            arow += 1

        self.extra_env_view = Gtk.TextView()
        self.extra_env_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.extra_env_view.set_size_request(-1, 64)
        extra_frame = Gtk.Frame()
        extra_frame.set_child(self.extra_env_view)
        self._add_row(adv_grid, arow, "Extra Env (KEY=VAL)", extra_frame)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_margin_top(6)

        self.launch_btn = Gtk.Button(label="Launch Game")
        self.launch_btn.add_css_class("suggested-action")
        self.launch_btn.connect("clicked", self._on_launch)

        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", self._on_stop)

        save_btn = Gtk.Button(label="Save Config")
        save_btn.connect("clicked", self._on_save)

        reset_btn = Gtk.Button(label="Reset Defaults")
        reset_btn.connect("clicked", self._on_reset)

        btn_box.append(self.launch_btn)
        btn_box.append(self.stop_btn)
        btn_box.append(save_btn)
        btn_box.append(reset_btn)
        root.append(btn_box)

        # Status & Log Viewer
        self.status_label = Gtk.Label(label="Ready.")
        self.status_label.set_xalign(0.0)
        root.append(self.status_label)

        log_expander = Gtk.Expander(label="Live Output Log")
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(150)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        log_scroll.set_child(self.log_view)
        log_expander.set_child(log_scroll)
        root.append(log_expander)

        self._apply_config_to_ui(self.cfg)

    def _add_row(self, grid: Gtk.Grid, row: int, label_text: str, widget: Gtk.Widget) -> None:
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0.0)
        grid.attach(lbl, 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)

    def _apply_config_to_ui(self, cfg: dict) -> None:
        self.game_entry.set_text(cfg.get("game_exe", ""))

        res_ids = [r[0] for r in RESOLUTIONS]
        self.res_combo.set_selected(res_ids.index(cfg["resolution"]) if cfg.get("resolution") in res_ids else 0)

        con_ids = [c[0] for c in CONSOLES]
        self.console_combo.set_selected(con_ids.index(cfg["console"]) if cfg.get("console") in con_ids else 0)

        rtx_ids = [m[0] for m in RTX_MODES]
        self.rtx_combo.set_selected(rtx_ids.index(cfg["rtx_mode"]) if cfg.get("rtx_mode") in rtx_ids else 0)

        log_ids = [l[0] for l in LOG_LEVELS]
        self.log_combo.set_selected(log_ids.index(cfg["log_level"]) if cfg.get("log_level") in log_ids else 0)

        self.mangohud_check.set_active(bool(cfg.get("mangohud", False)))
        self.port1_chk.set_active(bool(cfg.get("port_constants", True)))
        self.port2_chk.set_active(bool(cfg.get("port_descriptors", True)))
        self.port6_chk.set_active(bool(cfg.get("port_guard", True)))
        self.port7_chk.set_active(bool(cfg.get("port_journal", True)))
        self.async_chk.set_active(bool(cfg.get("async_submit", True)))
        self.show_after_chk.set_active(bool(cfg.get("show_after", False)))
        self.tex_nowait_chk.set_active(bool(cfg.get("texture_no_wait", False)))

        for key, entry in self.adv_entries.items():
            entry.set_text(str(cfg.get(key, "")))
        self.extra_env_view.get_buffer().set_text(cfg.get("extra_env", ""))

    def _collect_config_from_ui(self) -> dict:
        buf = self.extra_env_view.get_buffer()
        extra_env = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        cfg = {
            "game_exe": self.game_entry.get_text().strip(),
            "resolution": RESOLUTIONS[self.res_combo.get_selected()][0],
            "console": CONSOLES[self.console_combo.get_selected()][0],
            "rtx_mode": RTX_MODES[self.rtx_combo.get_selected()][0],
            "log_level": LOG_LEVELS[self.log_combo.get_selected()][0],
            "mangohud": self.mangohud_check.get_active(),
            "port_constants": self.port1_chk.get_active(),
            "port_descriptors": self.port2_chk.get_active(),
            "port_guard": self.port6_chk.get_active(),
            "port_journal": self.port7_chk.get_active(),
            "async_submit": self.async_chk.get_active(),
            "show_after": self.show_after_chk.get_active(),
            "texture_no_wait": self.tex_nowait_chk.get_active(),
            "extra_env": extra_env,
        }
        for key, entry in self.adv_entries.items():
            cfg[key] = entry.get_text().strip()
        return cfg

    def _on_browse_game(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Select game_scarlett_master.exe")
        dialog.open(self, None, self._on_browse_done)

    def _on_browse_done(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.open_finish(result)
            if gfile and gfile.get_path():
                self.game_entry.set_text(gfile.get_path())
        except Exception:
            pass

    def _on_save(self, _btn: Gtk.Button) -> None:
        self.cfg = self._collect_config_from_ui()
        save_config(self.cfg)
        self.status_label.set_text(f"Saved config to {CONFIG_FILE}")

    def _on_reset(self, _btn: Gtk.Button) -> None:
        self.cfg = default_config()
        self._apply_config_to_ui(self.cfg)
        save_config(self.cfg)
        self.status_label.set_text("Reset to Protonida defaults.")

    def _append_log(self, line: str) -> bool:
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), line)
        return False

    def _on_launch(self, _btn: Gtk.Button) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self.cfg = self._collect_config_from_ui()
        save_config(self.cfg)

        game_exe = self.cfg["game_exe"]
        victory_exe = self.cfg["victory_exe"]
        wine_bin = self.cfg["wine_bin"]
        if not game_exe or not Path(game_exe).exists():
            self.status_label.set_text("Error: Game executable not found.")
            return
        if not victory_exe or not Path(victory_exe).exists():
            self.status_label.set_text(f"Error: victory.exe not found at {victory_exe}")
            return

        # Update settings.xml for RTX toggle
        update_settings_xml(self.cfg["wineprefix"], self.cfg["rtx_mode"])

        env = os.environ.copy()
        xcl_home = Path(self.cfg.get("xcl_linux_home") or DEFAULT_XCL_LINUX_HOME)
        env["XCL_LINUX_HOME"] = str(xcl_home)
        env["WINEARCH"] = "win64"
        env["WINEDEBUG"] = env.get("WINEDEBUG", "-all")
        env["WINEPREFIX"] = self.cfg["wineprefix"]
        env["WINEDLLOVERRIDES"] = "d3d12,d3d12core,d3d11,dxgi,d3dcompiler_47=n;mscoree,mshtml="
        env["__GL_SHADER_DISK_CACHE_SIZE"] = "4294967296"

        # Prepend llvm-mingw runtime DLLs (libunwind.dll / libc++.dll) to WINEPATH
        mingw_bin = xcl_home / "llvm-mingw" / "x86_64-w64-mingw32" / "bin"
        if mingw_bin.exists():
            mingw_wine = linux_to_wine_path(str(mingw_bin))
            existing_wp = env.get("WINEPATH", "")
            env["WINEPATH"] = f"{mingw_wine};{existing_wp}" if existing_wp else mingw_wine

        dxc_bin = xcl_home / "dxc" / "bin" / "x64"
        if dxc_bin.exists():
            env["XCL_DXC_DIR"] = linux_to_wine_path(str(dxc_bin))

        vkd3d_cache = xcl_home / "vkd3d-cache"
        vkd3d_cache.mkdir(parents=True, exist_ok=True)
        env.setdefault("VKD3D_SHADER_CACHE_PATH", str(vkd3d_cache))
        env.setdefault("DXVK_STATE_CACHE_PATH", str(vkd3d_cache))

        env.setdefault("XCL_SAFE_BARRIER", "0")
        gpu_filter = self.cfg.get("gpu_filter", "NVIDIA").strip()
        if gpu_filter:
            env["VKD3D_FILTER_DEVICE_NAME"] = gpu_filter
            env["DXVK_FILTER_DEVICE_NAME"] = gpu_filter
            if gpu_filter.upper() == "NVIDIA":
                env["__NV_PRIME_RENDER_OFFLOAD"] = "1"
                env["__VK_LAYER_NV_optimus"] = "NVIDIA_only"

        if self.cfg.get("cache_dir"):
            env["VICTORY_CACHE_DIR"] = linux_to_wine_path(self.cfg["cache_dir"])
        env["VICTORY_LOG"] = self.cfg.get("log_level", "warn")

        wineserver = self.cfg.get("wineserver", "")
        if wineserver and Path(wineserver).exists():
            env["WINESERVER"] = wineserver
            try:
                chk = subprocess.run(["pgrep", "-f", wineserver], stdout=subprocess.DEVNULL)
                if chk.returncode != 0:
                    subprocess.Popen([wineserver, "-p"], env=env)
            except Exception:
                pass

        game_dir = str(Path(game_exe).parent)
        env["VICTORY_GAME_DIR"] = linux_to_wine_path(game_dir)
        env["VICTORY_OUTPUT"] = self.cfg["resolution"]
        env["VICTORY_UPSCALER"] = self.cfg["upscaler"]
        env["VICTORY_PORT_CONSTANTS"] = "1" if self.cfg["port_constants"] else "0"
        env["VICTORY_PORT_DESCRIPTORS"] = "1" if self.cfg["port_descriptors"] else "0"
        env["VICTORY_PORT_DESCRIPTORS_EVERY"] = self.cfg["port_descriptors_every"]
        env["VICTORY_PORT_DESCRIPTORS_CHECK"] = self.cfg["port_descriptors_check"]
        env["VICTORY_PORT_GUARD"] = "1" if self.cfg["port_guard"] else "0"
        env["VICTORY_PORT_JOURNAL"] = "1" if self.cfg["port_journal"] else "0"
        env["VICTORY_LATE_WAIT"] = self.cfg["late_wait"]
        env["VICTORY_LATE_WAIT_LISTS"] = self.cfg["late_wait_lists"]
        env["VICTORY_ASYNC_SUBMIT"] = "1" if self.cfg["async_submit"] else "0"
        env["VICTORY_INPLACE"] = "1"
        env["VICTORY_INPLACE_MB"] = self.cfg["inplace_mb"]
        env["VICTORY_PLANES"] = "1"
        env["VICTORY_TEXTURE_BUDGET_MB"] = self.cfg["texture_budget_mb"]
        env["VICTORY_CARD_ROOM_FOR_TARGETS_MB"] = self.cfg["card_room_for_targets_mb"]
        env["VICTORY_CARD_BUFFERS_MIN_KB"] = self.cfg["card_buffers_min_kb"]
        env["VICTORY_CARD_BUFFERS_MB"] = self.cfg["card_buffers_mb"]
        env["VICTORY_LEVELS_ON_DEMAND"] = "0"
        env["VICTORY_PIPELINE_WORKERS"] = self.cfg["pipeline_workers"]
        env["VICTORY_PIPELINE_WAIT_MS"] = self.cfg["pipeline_wait_ms"]
        env["VICTORY_RELAY"] = "2"
        env["VICTORY_RELAY_COPY"] = "1"
        env["VICTORY_WIDE_BACK"] = "1"
        env["VICTORY_CARD_REHASH_RUNS"] = "1"
        env["VICTORY_JOIN_COPIES"] = "1"
        env["VICTORY_INDIRECT_WINDOW"] = "0"
        env["VICTORY_INDIRECT_BATCH"] = "2"
        env["VICTORY_CARD_STEPS"] = "2"
        env["VICTORY_MAP_THREADS"] = "1"
        env["VICTORY_CARD_AWAKE"] = "1"
        env["VICTORY_SHOW_AFTER"] = "1" if self.cfg.get("show_after") else "0"
        env["VICTORY_LAND_SKIP"] = "1"
        env["VICTORY_GUARD_REST"] = self.cfg["guard_rest"]
        env["VICTORY_GUARD_HOT"] = self.cfg["guard_hot"]
        env["VICTORY_TEXTURE_NO_WAIT"] = "1" if self.cfg.get("texture_no_wait") else "0"
        env["VICTORY_THIN"] = "1"
        env["VICTORY_JOIN_PARTS"] = "8"
        env["VICTORY_UPLOAD_AHEAD"] = "1"
        env["VICTORY_AMBIENT_AIR"] = self.cfg["ambient_air"]
        if self.cfg["skip_pipelines"]:
            env["VICTORY_SKIP_PIPELINES"] = self.cfg["skip_pipelines"]

        if self.cfg["rtx_mode"] == "off":
            env["VICTORY_BVH_BUILDER"] = "0"
            env["VICTORY_SKIP_RT"] = "1"
            env["VICTORY_AMBIENT"] = self.cfg["ambient_shgi"] or "2.0"
        else:
            env["VICTORY_BVH_BUILDER"] = "1"
            env["VICTORY_SKIP_RT"] = "0"
            env["VICTORY_AMBIENT"] = "off"

        for raw_line in self.cfg.get("extra_env", "").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

        cmd = []
        if self.cfg["mangohud"] and shutil.which("mangohud"):
            cmd.append("mangohud")
        cmd.extend(
            [
                wine_bin,
                victory_exe,
                "run",
                "--console",
                self.cfg["console"],
                "--execute",
                linux_to_wine_path(game_exe),
            ]
        )

        self.log_view.get_buffer().set_text(f"$ {shlex.join(cmd)}\n")
        self.status_label.set_text("Running…")
        self.launch_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)

        def runner() -> None:
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    cwd=str(REPO_ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                )
                assert self.proc.stdout is not None
                for line in self.proc.stdout:
                    GLib.idle_add(self._append_log, line)
                code = self.proc.wait()
                GLib.idle_add(self._on_finished, code)
            except Exception as exc:
                GLib.idle_add(self._append_log, f"\nLauncher error: {exc}\n")
                GLib.idle_add(self._on_finished, -1)

        threading.Thread(target=runner, daemon=True).start()

    def _on_stop(self, _btn: Gtk.Button) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.status_label.set_text("Stopping…")

    def _on_finished(self, code: int) -> bool:
        self.proc = None
        self.launch_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        self.status_label.set_text(f"Exited with code {code}.")
        return False


class ProtonidaApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.deynes.ProtonidaLauncher")

    def do_activate(self):
        win = ProtonidaWindow(self)
        win.present()


def main() -> None:
    app = ProtonidaApp()
    app.run(None)


if __name__ == "__main__":
    main()
