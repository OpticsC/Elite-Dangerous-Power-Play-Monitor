#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import requests
import tkinter as tk
from tkinter import ttk
import threading
import math
import os
import gzip
import io
import time
import re
from datetime import datetime, timezone
import webbrowser
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime

# ===================== VERSION =====================
APP_TITLE = "EDPPM State Finder"
APP_SUBTITLE = "State + Power + Ring Scanner (Local Dumps)"
APP_VERSION = "v1.3.1"

# ===================== Config ======================
EDSM_BASE = "https://www.edsm.net"
POPULATED_URL = "https://www.edsm.net/dump/systemsPopulated.json.gz"
POWERPLAY_URL = "https://www.edsm.net/dump/powerPlay.json.gz"
NIGHTLY_DUMPS = "https://www.edsm.net/en/nightly-dumps"

POP_JSON_FILE = "populated_local.json"
POWER_JSON_FILE = "powerplay_local.json"
CONFIG_FILE = "config.json"

DEFAULT_HOME = "Clayakarma"
DEFAULT_RADIUS = 30
PAYPAL_LINK = "https://www.paypal.com/ncp/payment/9UKRVTWBH93V6"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "EDPPM-State-Finder/1.3.1 (+local scan; respects EDSM dumps)"})

# ===================== Theme (BLACK) =====================
PRIMARY_ORANGE = "#FE8600"
HIGHLIGHT_ORANGE = "#FFAA33"
DARK_BG = "#000000"
PANEL_BG = "#0A0A0A"
PANEL_BG_2 = "#070707"
TEXT_COLOR = "#FE8600"
TEXT_DIM = "#9A5A12"
BORDER = "#1A1A1A"
FONT_NAME = "Arial"

# ===================== Data ========================
BGS_STATES = [
    "All (Any State)",
    "None", "Boom", "Bust", "Civil War", "War", "Election", "Outbreak", "Lockdown", "Expansion",
    "Retreat", "Investment", "Famine", "Civil Unrest", "Pirate Attack", "Blight", "Natural Disaster",
    "Infrastructure Failure", "Drought", "Terrorist Attack", "Public Holiday"
]

POWERS = [
    "All (Any / Uncontrolled)",
    "None (Uncontrolled)",
    "Yuri Grom", "Zachary Hudson", "Felicia Winters", "Aisling Duval",
    "Arissa Lavigny-Duval", "Denton Patreus", "Edmund Mahon", "Li Yong-Rui", "Pranav Antal",
    "Archon Delaine", "Zemina Torval"
]

RING_CHOICES = [
    "All (Any Rings)",     # no filter
    "None (No Rings)",     # must have zero rings
    "Icy",
    "Rocky",
    "Metal Rich",
    "Metallic"
]

# ===================== Helpers =====================
def load_json(file_name):
    if os.path.exists(file_name):
        try:
            with open(file_name, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_json(data, file_name):
    try:
        with open(file_name, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Save failed for {file_name}: {e}")

def distance_between(coord1, coord2):
    if not coord1 or not coord2:
        return float("inf")
    dx = coord1["x"] - coord2["x"]
    dy = coord1["y"] - coord2["y"]
    dz = coord1["z"] - coord2["z"]
    return math.sqrt(dx * dx + dy * dy + dz * dz)

def get_system_coords(system_name):
    # One small request for home coords only.
    url = f"{EDSM_BASE}/api-v1/systems"
    params = {"systemName": system_name, "showCoordinates": 1}
    try:
        resp = SESSION.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and "coords" in data[0]:
            return data[0]["coords"]
        return None
    except Exception as e:
        print(f"Error fetching coords for {system_name}: {e}")
        return None

def fmt_dt(dt):
    if not dt:
        return "None"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# ===================== Robust "Generated" timestamp =====================
_DATE_FMT = "%b %d, %Y, %I:%M:%S %p"  # e.g. Dec 25, 2025, 4:33:48 AM

def _try_parse_generated(s):
    """
    Parse EDSM nightly-dumps 'Generated:' string. Returns aware UTC dt or None.
    Page does not show timezone; we treat it as UTC for consistent comparisons.
    """
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, _DATE_FMT).replace(tzinfo=timezone.utc)
    except Exception:
        return None

def fetch_edsm_generated_time(url):
    """
    Robustly gets the dump's 'Generated' time.
    Strategy:
      1) Scrape nightly-dumps page by locating the dump URL and reading the next 'Generated:' line.
      2) Fallback: HTTP HEAD Last-Modified for the dump URL.
    Returns: (dt_utc or None, source_string)
    """
    filename = url.split("/")[-1]

    # --- 1) scrape nightly-dumps ---
    try:
        resp = SESSION.get(NIGHTLY_DUMPS, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        txt = soup.get_text("\n", strip=True)

        patterns = [
            re.escape(url),
            re.escape(filename),
        ]

        for pat in patterns:
            m = re.search(pat, txt, flags=re.IGNORECASE)
            if not m:
                continue
            window = txt[m.start(): m.start() + 800]
            gm = re.search(
                r"Generated:\s*([A-Za-z]{3}\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)",
                window
            )
            if gm:
                dt = _try_parse_generated(gm.group(1))
                if dt:
                    return dt, "nightly-dumps"

        # Structured scan
        lines = txt.splitlines()
        for i, line in enumerate(lines):
            if url.lower() in line.lower() or filename.lower() in line.lower():
                for j in range(i, min(i + 12, len(lines))):
                    if lines[j].strip().lower().startswith("generated:"):
                        cand = lines[j].split(":", 1)[-1].strip()
                        dt = _try_parse_generated(cand)
                        if dt:
                            return dt, "nightly-dumps"
    except Exception:
        pass

    # --- 2) fallback: HEAD Last-Modified ---
    try:
        h = SESSION.head(url, timeout=20, allow_redirects=True)
        if 200 <= h.status_code < 400:
            lm = h.headers.get("Last-Modified")
            if lm:
                dt = parsedate_to_datetime(lm)
                if dt:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc), "last-modified"
    except Exception:
        pass

    return None, "unknown"

# ===================== Meta timestamp (robust freshness) =====================
def meta_filename(json_file):
    return json_file + ".meta.json"

def load_local_generated(json_file):
    mf = meta_filename(json_file)
    if not os.path.exists(mf):
        return None
    try:
        with open(mf, "r", encoding="utf-8") as f:
            meta = json.load(f) or {}
        s = meta.get("edsm_generated_utc")
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def save_local_generated(json_file, edsm_dt_utc, source="nightly-dumps"):
    mf = meta_filename(json_file)
    try:
        payload = {
            "edsm_generated_utc": edsm_dt_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": source,
            "saved_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        with open(mf, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass

# ===================== Download ETA =====================
def format_bytes(n):
    if n is None:
        return "?"
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(n)
    for u in units:
        if size < step:
            return f"{size:.1f} {u}"
        size /= step
    return f"{size:.1f} EB"

def format_eta(seconds):
    if seconds is None or seconds == float("inf") or seconds < 0:
        return "??:??"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def download_with_progress(url, timeout, status_cb, stop_event=None, chunk_size=1024 * 512):
    resp = SESSION.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()

    total = resp.headers.get("Content-Length")
    total = int(total) if total and str(total).isdigit() else None

    start = time.time()
    last_ui = 0.0
    downloaded = 0
    chunks = []

    for chunk in resp.iter_content(chunk_size=chunk_size):
        if stop_event and stop_event.is_set():
            raise RuntimeError("Download stopped by user")

        if not chunk:
            continue

        chunks.append(chunk)
        downloaded += len(chunk)

        now = time.time()
        if now - last_ui >= 0.25:
            elapsed = max(now - start, 1e-6)
            speed = downloaded / elapsed
            if total:
                remaining = max(total - downloaded, 0)
                eta = remaining / max(speed, 1e-6)
                pct = (downloaded / total) * 100.0
                status_cb(
                    f"Downloading… {format_bytes(downloaded)} / {format_bytes(total)} "
                    f"({pct:.1f}%) — {format_bytes(speed)}/s — ETA {format_eta(eta)}"
                )
            else:
                status_cb(f"Downloading… {format_bytes(downloaded)} — {format_bytes(speed)}/s")
            last_ui = now

    return b"".join(chunks)

# ===================== Ring filter (LOCAL) =====================
def system_ring_matches_local(system, ring_choice):
    """
    ring_choice:
      - "All (Any Rings)" -> True for all systems (no filter)
      - "None (No Rings)" -> only systems with zero rings
      - "Icy" / "Rocky" / "Metal Rich" / "Metallic" -> any ring.type match
    """
    ring_choice = (ring_choice or "").strip()

    if ring_choice == "" or ring_choice == "All (Any Rings)":
        return True

    bodies = system.get("bodies") or []
    total_rings = 0
    target = ring_choice.lower()

    for body in bodies:
        rings = (body or {}).get("rings") or []
        if rings:
            total_rings += len(rings)

        for ring in rings:
            rtype = (ring.get("type") or "").strip().lower()
            rname = (ring.get("name") or "").strip().lower()

            # Primary match: type field
            if rtype == target:
                return True
            # Fallback: occasionally helpful
            if target and target in rname:
                return True

    if ring_choice == "None (No Rings)":
        return total_rings == 0

    return False

# ===================== Power cross-ref (LOCAL) =====================
def build_power_index(powerplay_data):
    idx = {}
    if not isinstance(powerplay_data, list):
        return idx
    for s in powerplay_data:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        power = s.get("power")
        if name:
            idx[name] = (power or "").strip()
    return idx

# ===================== App =========================
class EDPPMStateFinderApp:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_TITLE} {APP_VERSION}")
        root.configure(bg=DARK_BG)
        root.geometry("1100x740")
        root.minsize(980, 640)

        self.stop_event = threading.Event()
        self.results = []

        self._setup_style()
        self._build_layout()
        self._load_config_into_ui()
        self._update_local_data_status()

    # ---------- Style ----------
    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", font=(FONT_NAME, 10, "bold"))
        style.configure("TFrame", background=DARK_BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Panel2.TFrame", background=PANEL_BG_2)

        style.configure("TLabel", background=PANEL_BG, foreground=TEXT_COLOR)
        style.configure("Header.TLabel", background=DARK_BG, foreground=PRIMARY_ORANGE, font=(FONT_NAME, 18, "bold"))
        style.configure("SubHeader.TLabel", background=DARK_BG, foreground=TEXT_DIM, font=(FONT_NAME, 10, "bold"))
        style.configure("Section.TLabel", background=PANEL_BG, foreground=HIGHLIGHT_ORANGE, font=(FONT_NAME, 10, "bold"))

        style.configure("TEntry", fieldbackground="#111111", foreground=HIGHLIGHT_ORANGE, insertcolor=HIGHLIGHT_ORANGE)
        style.configure("TCombobox", fieldbackground="#111111", foreground=TEXT_COLOR, arrowcolor=TEXT_COLOR)
        style.map("TCombobox", fieldbackground=[("readonly", "#111111")])

        style.configure("Primary.TButton", background=PRIMARY_ORANGE, foreground="black", padding=10, font=(FONT_NAME, 11, "bold"))
        style.map("Primary.TButton", background=[("active", HIGHLIGHT_ORANGE)])

        style.configure("Danger.TButton", background="#DD4444", foreground="white", padding=10, font=(FONT_NAME, 11, "bold"))
        style.map("Danger.TButton", background=[("active", "#FF6666")])  # ✅ fixed

        style.configure("Thin.TButton", background="#111111", foreground=TEXT_COLOR, padding=6, font=(FONT_NAME, 9, "bold"))
        style.map("Thin.TButton", background=[("active", "#1A1A1A")])

        style.configure("Horizontal.TProgressbar", background=PRIMARY_ORANGE, troughcolor="#101010", thickness=16)

    # ---------- Layout ----------
    def _build_layout(self):
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=(10, 8))

        ttk.Label(header, text=APP_TITLE, style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text=f"{APP_SUBTITLE} — {APP_VERSION}", style="SubHeader.TLabel").pack(anchor="w")

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=12, pady=8)
        main.columnconfigure(0, weight=0)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        self.controls_panel = ttk.Frame(main, style="Panel.TFrame")
        self.controls_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        self.controls_panel.configure(width=360)

        self.results_panel = ttk.Frame(main, style="Panel2.TFrame")
        self.results_panel.grid(row=0, column=1, sticky="nsew")

        self._build_controls(self.controls_panel)
        self._build_results(self.results_panel)

        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=12, pady=(0, 10))

        self.progress = ttk.Progressbar(bottom, orient="horizontal", mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = tk.Label(bottom, textvariable=self.status_var, bg=DARK_BG, fg="yellow", font=(FONT_NAME, 10, "bold"))
        self.status_label.pack(anchor="w")

    def _build_controls(self, parent):
        ttk.Label(parent, text="SEARCH", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=8)
        parent.columnconfigure(1, weight=1)

        r = 1
        def add_row(label_text, widget):
            nonlocal r
            ttk.Label(parent, text=label_text).grid(row=r, column=0, sticky="w", padx=12, pady=(0, 6))
            widget.grid(row=r, column=1, sticky="ew", padx=12, pady=(0, 6))
            r += 1

        self.home_entry = ttk.Entry(parent)
        add_row("Home System", self.home_entry)

        self.radius_entry = ttk.Entry(parent)
        add_row("Radius (ly) — 0 = All", self.radius_entry)

        self.power_combo = ttk.Combobox(parent, values=POWERS, state="readonly")
        add_row("Power Filter", self.power_combo)

        self.state_combo = ttk.Combobox(parent, values=BGS_STATES, state="readonly")
        add_row("State", self.state_combo)

        self.ring_filter = ttk.Combobox(parent, values=RING_CHOICES, state="readonly")
        add_row("Ring Filter (Faction mode)", self.ring_filter)

        ttk.Label(parent, text="MODE", style="Section.TLabel").grid(row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(14, 8))
        r += 1

        self.mode_var = tk.StringVar(value="system")
        ttk.Radiobutton(parent, text="System Only (Fast) — powerPlay dump", variable=self.mode_var, value="system") \
            .grid(row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4))
        r += 1
        ttk.Radiobutton(parent, text="Faction Search (Slower) — populated dump", variable=self.mode_var, value="faction") \
            .grid(row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))
        r += 1

        btns = ttk.Frame(parent, style="Panel.TFrame")
        btns.grid(row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=(6, 8))
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        self.start_btn = ttk.Button(btns, text="START SCAN", style="Primary.TButton", command=self.start_scan)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.stop_btn = ttk.Button(btns, text="STOP", style="Danger.TButton", command=self.stop_scan)
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.stop_btn.state(["disabled"])
        r += 1

        utils = ttk.Frame(parent, style="Panel.TFrame")
        utils.grid(row=r, column=0, columnspan=2, sticky="ew", padx=12, pady=(6, 10))
        utils.columnconfigure(0, weight=1)
        utils.columnconfigure(1, weight=1)

        ttk.Button(utils, text="Donate via PayPal", style="Thin.TButton",
                   command=lambda: webbrowser.open(PAYPAL_LINK)) \
            .grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ttk.Button(utils, text="Open Data Folder", style="Thin.TButton",
                   command=self.open_data_folder) \
            .grid(row=0, column=1, sticky="ew", padx=(6, 0))
        r += 1

        ttk.Label(parent, text="DATA", style="Section.TLabel").grid(row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 6))
        r += 1

        self.data_info_var = tk.StringVar(value="Local Data: (checking...)")
        tk.Label(parent, textvariable=self.data_info_var, bg=PANEL_BG, fg=TEXT_DIM, font=(FONT_NAME, 9, "bold")) \
            .grid(row=r, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12))

    def _build_results(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        top = ttk.Frame(parent, style="Panel2.TFrame")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        top.columnconfigure(0, weight=1)

        tk.Label(top, text="RESULTS", bg=PANEL_BG_2, fg=HIGHLIGHT_ORANGE, font=(FONT_NAME, 11, "bold")) \
            .grid(row=0, column=0, sticky="w")
        self.count_var = tk.StringVar(value="0 found")
        tk.Label(top, textvariable=self.count_var, bg=PANEL_BG_2, fg=TEXT_DIM, font=(FONT_NAME, 10, "bold")) \
            .grid(row=0, column=1, sticky="e")

        container = tk.Frame(parent, bg=PANEL_BG_2, highlightbackground=BORDER, highlightthickness=1)
        container.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.result_canvas = tk.Canvas(container, bg=PANEL_BG_2, highlightthickness=0)
        self.result_canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.result_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.result_canvas.configure(yscrollcommand=scrollbar.set)

        self.result_inner = tk.Frame(self.result_canvas, bg=PANEL_BG_2)
        self.result_canvas.create_window((0, 0), window=self.result_inner, anchor="nw")
        self.result_inner.bind("<Configure>", lambda e: self.result_canvas.configure(scrollregion=self.result_canvas.bbox("all")))

        # Only bind wheel while mouse is over results area (prevents popup scroll from moving main list)
        def _bind_results_wheel(_event=None):
            self.root.bind_all("<MouseWheel>", self._on_mousewheel)
            self.root.bind_all("<Button-4>", self._on_mousewheel_linux)
            self.root.bind_all("<Button-5>", self._on_mousewheel_linux)

        def _unbind_results_wheel(_event=None):
            self.root.unbind_all("<MouseWheel>")
            self.root.unbind_all("<Button-4>")
            self.root.unbind_all("<Button-5>")

        self.result_canvas.bind("<Enter>", _bind_results_wheel)
        self.result_canvas.bind("<Leave>", _unbind_results_wheel)
        self.result_inner.bind("<Enter>", _bind_results_wheel)
        self.result_inner.bind("<Leave>", _unbind_results_wheel)

    def _on_mousewheel(self, event):
        try:
            self.result_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def _on_mousewheel_linux(self, event):
        try:
            if event.num == 4:
                self.result_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.result_canvas.yview_scroll(1, "units")
        except Exception:
            pass

    # ---------- UI helpers ----------
    def open_data_folder(self):
        try:
            folder = os.path.abspath(os.getcwd())
            os.startfile(folder)
        except Exception:
            pass

    def set_status(self, text):
        def _do():
            self.status_var.set(text)
            try:
                self.root.update_idletasks()
            except Exception:
                pass
        self.root.after(0, _do)

    def _update_local_data_status(self):
        pop_local_gen = load_local_generated(POP_JSON_FILE)
        power_local_gen = load_local_generated(POWER_JSON_FILE)

        pop_fallback = datetime.fromtimestamp(os.path.getmtime(POP_JSON_FILE), tz=timezone.utc) if os.path.exists(POP_JSON_FILE) else None
        power_fallback = datetime.fromtimestamp(os.path.getmtime(POWER_JSON_FILE), tz=timezone.utc) if os.path.exists(POWER_JSON_FILE) else None

        pop_dt = pop_local_gen or pop_fallback
        power_dt = power_local_gen or power_fallback

        self.data_info_var.set(f"Local JSON — Populated: {fmt_dt(pop_dt)} | PowerPlay: {fmt_dt(power_dt)}")

    def _load_config_into_ui(self):
        cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f) or {}
            except Exception:
                cfg = {}

        self.home_entry.insert(0, cfg.get("home_system", DEFAULT_HOME))
        self.radius_entry.insert(0, str(cfg.get("radius", DEFAULT_RADIUS)))
        self.power_combo.set(cfg.get("power", "All (Any / Uncontrolled)"))
        self.state_combo.set(cfg.get("state", "Boom"))
        self.ring_filter.set(cfg.get("ring_filter", "All (Any Rings)"))
        self.mode_var.set(cfg.get("mode", "system"))

        # Validate (handles upgrades)
        if self.state_combo.get() not in BGS_STATES:
            self.state_combo.set("Boom")
        if self.ring_filter.get() not in RING_CHOICES:
            self.ring_filter.set("All (Any Rings)")
        if self.power_combo.get() not in POWERS:
            self.power_combo.set("All (Any / Uncontrolled)")

    def _save_config_from_ui(self):
        cfg = {
            "home_system": self.home_entry.get().strip(),
            "radius": self.radius_entry.get().strip(),
            "power": self.power_combo.get(),
            "state": self.state_combo.get(),
            "ring_filter": self.ring_filter.get(),
            "mode": self.mode_var.get(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # ---------- Actions ----------
    def start_scan(self):
        self.results.clear()
        for w in self.result_inner.winfo_children():
            w.destroy()
        self.count_var.set("0 found")

        self.stop_event.clear()
        self._save_config_from_ui()

        self.start_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.progress.start(15)

        threading.Thread(target=self.scan_loop, daemon=True).start()

    def stop_scan(self):
        self.stop_event.set()
        self.set_status("Stop requested…")

    def cleanup(self):
        def _do():
            self.progress.stop()
            self.start_btn.state(["!disabled"])
            self.stop_btn.state(["disabled"])
            self._update_local_data_status()
        self.root.after(0, _do)

    # ---------- Dumps ----------
    def load_or_download_dump(self, url, json_file):
        # 1) Determine EDSM "Generated" time robustly
        self.set_status("Checking nightly-dumps freshness…")
        edsm_gen, src = fetch_edsm_generated_time(url)
        local_gen = load_local_generated(json_file)

        # Display BOTH and the source for debugging
        self.set_status(f"Age check — EDSM: {fmt_dt(edsm_gen)} ({src}) | Local: {fmt_dt(local_gen)}")

        # 2) Load local if possible
        data = load_json(json_file)

        # If local JSON unreadable => always download
        if not data:
            self.set_status("Local JSON missing/corrupt — downloading…")
        else:
            # If we cannot get EDSM time at all, we cannot safely decide to refresh
            if edsm_gen is None:
                self.set_status("Warning: Could not determine EDSM freshness (scrape/HEAD failed) — using local JSON")
                return data

            # If we have no local_gen stored, FORCE one refresh if EDSM time exists
            if local_gen is None:
                self.set_status("No local 'Generated' timestamp saved — forcing refresh to sync with EDSM…")
            else:
                if local_gen >= edsm_gen:
                    self.set_status("Using local JSON (up to date)")
                    return data
                self.set_status("Newer JSON on EDSM — downloading latest…")

        # 3) Download
        gz_bytes = download_with_progress(url, timeout=300, status_cb=self.set_status, stop_event=self.stop_event)

        self.set_status("Decompressing…")
        with gzip.open(io.BytesIO(gz_bytes)) as f:
            raw = f.read()

        self.set_status("Parsing JSON…")
        data = json.loads(raw.decode("utf-8"))

        save_json(data, json_file)

        # 4) Save EDSM timestamp to meta
        if edsm_gen:
            save_local_generated(json_file, edsm_gen, source=src)
        else:
            save_local_generated(json_file, datetime.now(timezone.utc), source="download-no-generated")

        self.set_status("Download complete — local JSON updated")
        return data

    # ---------- Scan ----------
    def scan_loop(self):
        try:
            home_system = self.home_entry.get().strip()
            try:
                radius = float(self.radius_entry.get().strip())
            except Exception:
                radius = 0.0

            mode = self.mode_var.get()

            state_choice = (self.state_combo.get() or "").strip()
            target_state = state_choice.lower()
            any_state = (state_choice == "All (Any State)")

            selected_power = self.power_combo.get()
            ring_choice = (self.ring_filter.get() or "").strip()

            # Load dumps
            if mode == "system":
                data = self.load_or_download_dump(POWERPLAY_URL, POWER_JSON_FILE)
                power_index = None
            else:
                data = self.load_or_download_dump(POPULATED_URL, POP_JSON_FILE)
                power_index = None
                if selected_power != "All (Any / Uncontrolled)":
                    powerplay = self.load_or_download_dump(POWERPLAY_URL, POWER_JSON_FILE)
                    power_index = build_power_index(powerplay)

            if not isinstance(data, list):
                self.set_status("Unexpected JSON format (not a list).")
                return

            # Home coords
            home_coords = None
            if home_system and radius > 0:
                self.set_status(f"Fetching coordinates for {home_system}…")
                home_coords = get_system_coords(home_system)
                if not home_coords:
                    self.set_status("Home system coords not found — searching all.")
                    radius = 0.0

            # Scan
            self.set_status("Scanning…")
            total = len(data)
            processed = 0
            found = []

            for sys in data:
                if self.stop_event.is_set():
                    break

                processed += 1
                if processed % 1500 == 0:
                    self.set_status(f"Scanning… {processed}/{total}")

                name = sys.get("name")
                if not name:
                    continue

                if mode == "system":
                    power = (sys.get("power") or "").strip()
                    state = (sys.get("state") or "").strip().lower()

                    # Power filter (system mode uses powerplay dump directly)
                    if selected_power == "None (Uncontrolled)":
                        if power:
                            continue
                    elif selected_power != "All (Any / Uncontrolled)":
                        if power != selected_power:
                            continue

                    # State filter
                    if (not any_state) and (state != target_state):
                        continue

                else:
                    # Cross reference powerplay (local) if filtering by power
                    if selected_power != "All (Any / Uncontrolled)":
                        sys_power = (power_index.get(name) or "").strip() if power_index else ""
                        if selected_power == "None (Uncontrolled)":
                            if sys_power:
                                continue
                        else:
                            if sys_power != selected_power:
                                continue

                    # Faction state filter
                    if not any_state:
                        factions = sys.get("factions") or []
                        if not any((((f or {}).get("state") or "").strip().lower()) == target_state for f in factions):
                            continue

                    # Ring filter (Faction mode only)
                    if not system_ring_matches_local(sys, ring_choice):
                        continue

                coords = sys.get("coords")
                dist = None
                if radius > 0 and home_coords and coords:
                    dist = distance_between(home_coords, coords)
                    if dist > radius:
                        continue

                found.append((sys, dist))

            found.sort(key=lambda x: x[1] if x[1] is not None else float("inf"))
            self.results = found

            self.set_status(f"Done — Found {len(self.results)} matching systems.")
            self.root.after(0, self.show_results)

        except Exception as e:
            self.set_status(f"Scan error: {e}")
        finally:
            self.cleanup()

    # ---------- Results ----------
    def show_results(self):
        for w in self.result_inner.winfo_children():
            w.destroy()

        self.count_var.set(f"{len(self.results)} found")

        for sys, dist in self.results:
            name = sys.get("name", "Unknown")
            distance_str = f"{dist:.2f} ly" if dist is not None else "N/A"

            row = tk.Frame(self.result_inner, bg=PANEL_BG_2, highlightbackground=BORDER, highlightthickness=1)
            row.pack(fill="x", padx=8, pady=6)

            left = tk.Frame(row, bg=PANEL_BG_2)
            left.pack(side="left", fill="both", expand=True, padx=10, pady=8)

            title = tk.Label(left, text=name, bg=PANEL_BG_2, fg=TEXT_COLOR, font=(FONT_NAME, 11, "bold"))
            title.pack(anchor="w")

            meta = tk.Label(left, text=f"Distance: {distance_str}", bg=PANEL_BG_2, fg=TEXT_DIM, font=(FONT_NAME, 9, "bold"))
            meta.pack(anchor="w", pady=(2, 0))

            title.bind("<Button-1>", lambda e, s=sys: self.show_system_details(s))
            meta.bind("<Button-1>", lambda e, s=sys: self.show_system_details(s))

            btns = tk.Frame(row, bg=PANEL_BG_2)
            btns.pack(side="right", padx=10, pady=8)

            tk.Button(btns, text="Details", bg="#111111", fg=TEXT_COLOR, relief="flat",
                      font=(FONT_NAME, 9, "bold"),
                      command=lambda s=sys: self.show_system_details(s)).pack(fill="x", pady=(0, 6))

            tk.Button(btns, text="Copy", bg=PRIMARY_ORANGE, fg="black", relief="flat",
                      font=(FONT_NAME, 9, "bold"),
                      command=lambda n=name: self.copy_system(n)).pack(fill="x")

    def copy_system(self, name):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(name)
            self.set_status(f"Copied: {name}")
        except Exception:
            self.set_status("Clipboard copy failed.")

    # ---------- Details popup ----------
    def show_system_details(self, system):
        win = tk.Toplevel(self.root)
        win.title(f"System: {system.get('name','Unknown')}")
        win.configure(bg="#000000")

        frame = tk.Frame(win, bg="#000000")
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        scroll = ttk.Scrollbar(frame, orient="vertical")
        scroll.pack(side="right", fill="y")

        text = tk.Text(
            frame, bg="#000000", fg=TEXT_COLOR, font=(FONT_NAME, 10),
            width=110, height=35, yscrollcommand=scroll.set
        )
        text.pack(side="left", fill="both", expand=True)
        scroll.config(command=text.yview)

        def insert_line(label, val):
            text.insert(tk.END, f"{label}: {val}\n")

        insert_line("System", system.get("name", "Unknown"))
        insert_line("ID", system.get("id", ""))
        insert_line("ID64", system.get("id64", ""))

        coords = system.get("coords")
        if coords:
            insert_line("Coordinates", f"x={coords.get('x')}, y={coords.get('y')}, z={coords.get('z')}")

        insert_line("Allegiance", system.get("allegiance", ""))
        insert_line("Government", system.get("government", ""))
        insert_line("State", system.get("state", ""))
        insert_line("Economy", system.get("economy", ""))
        insert_line("Security", system.get("security", ""))
        insert_line("Population", system.get("population", ""))

        controlling = system.get("controllingFaction")
        if controlling:
            text.insert(tk.END, "\nControlling Faction:\n")
            for k, v in controlling.items():
                text.insert(tk.END, f"  {k}: {v}\n")

        factions = system.get("factions", [])
        if factions:
            text.insert(tk.END, "\nFactions:\n")
            for f in factions:
                f = f or {}
                text.insert(tk.END, f"  - {f.get('name','Unknown')} | State: {f.get('state','')} | Influence: {f.get('influence','')}\n")

        text.insert(tk.END, "\nRings (local dump):\n")
        bodies = system.get("bodies") or []
        ring_count = 0
        for b in bodies:
            rings = (b or {}).get("rings") or []
            if not rings:
                continue
            text.insert(tk.END, f"  - {b.get('name','Unknown')}\n")
            for r in rings:
                text.insert(tk.END, f"      Ring: {r.get('name','')} | Type: {r.get('type','')}\n")
                ring_count += 1
                if ring_count >= 120:
                    text.insert(tk.END, "      (…truncated…)\n")
                    break
            if ring_count >= 120:
                break
        if ring_count == 0:
            text.insert(tk.END, "  (No rings found in local dump for this system)\n")

        date = system.get("date")
        if date:
            insert_line("\nSystem Data Last Updated", date)

        text.config(state=tk.DISABLED)

# ===================== Main ========================
if __name__ == "__main__":
    root = tk.Tk()
    app = EDPPMStateFinderApp(root)
    root.mainloop()
