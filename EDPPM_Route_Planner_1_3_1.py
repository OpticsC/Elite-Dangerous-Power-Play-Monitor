import os
import json
import time
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List

import requests
import psutil

import tkinter as tk

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

import webbrowser

# ====================== DISPLAY VERSION ======================
VERSION_DISPLAY = "1.3.1"  # keep this

# ====================== FILES ======================
SYSTEMS_FILE = "elite_systems.json"
LAST_DATA_FILE = "last_system_data.json"
COORDS_CACHE_FILE = "system_coords_cache.json"

# ====================== COLORS ======================
ED_BG = "#000000"
ED_DARK = "#111111"
ED_ORANGE = "#FF6600"
ED_GREY = "#888888"
ED_WHITE = "#FFFFFF"
ED_YELLOW = "#FFFF00"

# ====================== NETWORK SAFETY ======================
MIN_SECONDS_BETWEEN_REFRESHES = 15
INARA_MIN_SECONDS_BETWEEN_CALLS = 1.5
EDSM_MIN_SECONDS_BETWEEN_CALLS = 0.25
INARA_MIN_RECHECK_HOURS = 6.0

REQUEST_TIMEOUT = 20
MAX_TSP_POINTS = 80

# ====================== WINDOW ======================
TOPMOST_ENFORCE_EVERY_MS = 2000


# ====================== HELPERS ======================
def safe_load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def is_edmarket_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == "edmarketconnector.exe":
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def get_system_names(systems_data: Any) -> List[str]:
    if isinstance(systems_data, list):
        return [str(x) for x in systems_data]
    if isinstance(systems_data, dict):
        return [str(k) for k in systems_data.keys()]
    return []


def try_get_local_coords(system_name: str, systems_data: Any) -> Optional[Tuple[float, float, float]]:
    if not isinstance(systems_data, dict):
        return None
    entry = systems_data.get(system_name)
    if not isinstance(entry, dict):
        return None

    c = entry.get("coords")
    if isinstance(c, dict) and all(k in c for k in ("x", "y", "z")):
        try:
            return float(c["x"]), float(c["y"]), float(c["z"])
        except Exception:
            return None

    if all(k in entry for k in ("x", "y", "z")):
        try:
            return float(entry["x"]), float(entry["y"]), float(entry["z"])
        except Exception:
            return None

    return None


def parse_inara_timestamp(ts: str) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    s = re.sub(r"\s*(am|pm)\b", lambda m: m.group(1).upper(), s, flags=re.IGNORECASE)
    try:
        return datetime.strptime(s, "%d %b %Y, %I:%M%p")
    except Exception:
        return None


def format_inara_timestamp(dt: datetime) -> str:
    s = dt.strftime("%d %b %Y, %I:%M%p")
    s = s.replace(", 0", ", ").lower()
    return s


class RateLimiter:
    def __init__(self, min_interval_seconds: float):
        self.min_interval = float(min_interval_seconds)
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            wait_for = (self._last_call + self.min_interval) - now
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_call = time.monotonic()


def fetch_edsm_coords(session: requests.Session, limiter: RateLimiter, system_name: str) -> Optional[Tuple[float, float, float]]:
    limiter.wait()
    try:
        r = session.get(
            "https://www.edsm.net/api-v1/system",
            params={"systemName": system_name, "showCoordinates": 1},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 429:
            time.sleep(5)
            return None
        r.raise_for_status()
        data = r.json()
        coords = data.get("coords")
        if isinstance(coords, dict) and all(k in coords for k in ("x", "y", "z")):
            return float(coords["x"]), float(coords["y"]), float(coords["z"])
    except Exception:
        return None
    return None


def fetch_inara_info_updated(session: requests.Session, limiter: RateLimiter, system_name: str) -> Optional[str]:
    limiter.wait()
    try:
        r = session.get(
            "https://inara.cz/elite/starsystem/",
            params={"search": system_name},
            headers={"User-Agent": f"EDPPM-RoutePlanner/{VERSION_DISPLAY}"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 429:
            time.sleep(10)
            return None
        if r.status_code != 200:
            return None

        text = r.text
        date_pattern = r"(\d{1,2}\s[A-Za-z]{3}\s\d{4},\s\d{1,2}:\d{2}\s*(?:am|pm))"
        matches = re.findall(date_pattern, text, flags=re.IGNORECASE)
        if not matches:
            return None

        best_dt = None
        for m in matches:
            dt = parse_inara_timestamp(m)
            if dt and (best_dt is None or dt > best_dt):
                best_dt = dt

        if best_dt:
            return format_inara_timestamp(best_dt)
    except Exception:
        return None
    return None


# ====================== ROUTE OPTIMIZATION ======================
def route_distance(route: List[str], coords_dict: Dict[str, Tuple[float, float, float]]) -> float:
    dist = 0.0
    for i in range(len(route) - 1):
        p1 = np.array(coords_dict[route[i]])
        p2 = np.array(coords_dict[route[i + 1]])
        dist += float(np.linalg.norm(p1 - p2))
    return dist


def two_opt(route: List[str], coords_dict: Dict[str, Tuple[float, float, float]]) -> List[str]:
    best = route[:]
    best_dist = route_distance(best, coords_dict)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best) - 2):
            for j in range(i + 1, len(best)):
                if j - i == 1:
                    continue
                new_route = best[:i] + best[i:j][::-1] + best[j:]
                new_dist = route_distance(new_route, coords_dict)
                if new_dist < best_dist:
                    best = new_route
                    best_dist = new_dist
                    improved = True
    return best


def nearest_neighbor_tsp(coords_dict: Dict[str, Tuple[float, float, float]], systems: List[str]) -> List[str]:
    if len(systems) <= 1:
        return systems[:]

    unvisited = set(systems)
    current = systems[0]
    route = [current]
    unvisited.remove(current)

    while unvisited:
        curp = np.array(coords_dict[current])
        nearest = min(unvisited, key=lambda s: float(np.linalg.norm(curp - np.array(coords_dict[s]))))
        route.append(nearest)
        unvisited.remove(nearest)
        current = nearest

    if len(route) <= MAX_TSP_POINTS:
        return two_opt(route, coords_dict)
    return route


# ====================== APP ======================
class RoutePlannerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"Elite Dangerous Route Planner v{VERSION_DISPLAY}")
        self.root.configure(bg=ED_BG)
        self.root.geometry("1400x900")

        try:
            self.root.iconbitmap("edppm.ico")
        except Exception:
            pass

        # Data (loaded local-first; refreshed again on each Refresh)
        self.systems_data = safe_load_json(SYSTEMS_FILE, default={})
        self.system_names = get_system_names(self.systems_data)
        self.last_data: Dict[str, Dict[str, Any]] = safe_load_json(LAST_DATA_FILE, default={})
        self.coords_cache: Dict[str, Any] = safe_load_json(COORDS_CACHE_FILE, default={})

        # State
        self.refresh_in_progress = False
        self.last_refresh_started_at = 0.0
        self.auto_refresh_enabled = False
        self.next_auto_refresh_at: Optional[datetime] = None

        self.always_on_top = True
        self._closing = False

        # Startup overlay (only show on first startup refresh)
        self._startup_refresh_pending = True

        # ---------- UI ----------
        top_frame = tk.Frame(root, bg=ED_BG)
        top_frame.pack(fill=tk.X, padx=10, pady=10)

        left_block = tk.Frame(top_frame, bg=ED_BG)
        left_block.pack(side=tk.LEFT, anchor="w")

        tk.Label(
            left_block,
            text=f"EDPPM Route Planner v{VERSION_DISPLAY}",
            fg=ED_ORANGE,
            bg=ED_BG,
            font=("Courier", 16, "bold"),
        ).pack(anchor="w")

        tk.Button(
            left_block,
            text="Donate via PayPal",
            bg="#003087",
            fg="white",
            font=("Courier", 10, "bold"),
            relief="flat",
            cursor="hand2",
            command=lambda: webbrowser.open("https://www.paypal.com/ncp/payment/9UKRVTWBH93V6"),
        ).pack(anchor="w", pady=(6, 0))

        controls = tk.Frame(top_frame, bg=ED_BG)
        controls.pack(side=tk.RIGHT)

        # OnTop toggle (color indicates state)
        self.ontop_btn = tk.Button(
            controls,
            text="OnTop",
            bg=ED_GREY,  # ON look
            fg="black",
            activebackground=ED_GREY,
            activeforeground="black",
            font=("Courier", 12, "bold"),
            relief="flat",
            cursor="hand2",
            command=self.toggle_topmost,
        )
        self.ontop_btn.pack(side=tk.RIGHT, padx=(0, 10))

        tk.Label(controls, text="Outdated Threshold (hours):", fg=ED_WHITE, bg=ED_BG, font=("Courier", 12)).pack(side=tk.LEFT)
        self.threshold_entry = tk.Entry(controls, width=6, bg=ED_DARK, fg=ED_ORANGE, insertbackground=ED_ORANGE, font=("Courier", 12))
        self.threshold_entry.insert(0, "24")
        self.threshold_entry.pack(side=tk.LEFT, padx=(5, 12))

        tk.Label(controls, text="Auto Refresh (min):", fg=ED_WHITE, bg=ED_BG, font=("Courier", 12)).pack(side=tk.LEFT)
        self.auto_interval_entry = tk.Entry(controls, width=6, bg=ED_DARK, fg=ED_ORANGE, insertbackground=ED_ORANGE, font=("Courier", 12))
        self.auto_interval_entry.insert(0, "15")
        self.auto_interval_entry.pack(side=tk.LEFT, padx=(5, 8))

        self.auto_btn = tk.Button(
            controls,
            text="Auto",
            bg=ED_DARK,
            fg=ED_GREY,
            activebackground=ED_DARK,
            activeforeground=ED_GREY,
            font=("Courier", 12, "bold"),
            relief="flat",
            cursor="hand2",
            command=self.toggle_auto_refresh,
        )
        self.auto_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.refresh_btn = tk.Button(
            controls,
            text="Refresh Route",
            bg=ED_ORANGE,
            fg="black",
            activebackground=ED_ORANGE,
            activeforeground="black",
            font=("Courier", 12, "bold"),
            relief="flat",
            cursor="hand2",
            command=self.refresh_route,
        )
        self.refresh_btn.pack(side=tk.LEFT)

        # Main content
        main_frame = tk.Frame(root, bg=ED_BG)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.plot_frame = tk.Frame(main_frame, bg=ED_BG)
        self.plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.fig = plt.Figure(figsize=(8, 8), facecolor="black")
        self.ax = self.fig.add_subplot(111, projection="3d")
        self.ax.set_facecolor("black")
        self.canvas = FigureCanvasTkAgg(self.fig, self.plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        route_frame = tk.Frame(main_frame, bg=ED_BG)
        route_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(20, 0))
        tk.Label(route_frame, text="Monitored Systems", fg=ED_ORANGE, bg=ED_BG, font=("Courier", 14, "bold")).pack(anchor="w")

        self.list_canvas = tk.Canvas(route_frame, bg=ED_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(route_frame, orient="vertical", command=self.list_canvas.yview)
        self.scrollable_frame = tk.Frame(self.list_canvas, bg=ED_BG)

        self.scrollable_frame.bind("<Configure>", lambda e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.list_canvas.configure(yscrollcommand=scrollbar.set)

        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.list_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # Bottom status bar (BOTTOM-LEFT, ALWAYS VISIBLE)
        bottom_frame = tk.Frame(root, bg=ED_BG)
        bottom_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        status_left = tk.Frame(bottom_frame, bg=ED_BG)
        status_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_main = tk.Label(
            status_left,
            text="Status: Starting…",
            fg=ED_ORANGE,
            bg=ED_BG,
            font=("Courier", 12, "bold"),
            anchor="w",
        )
        self.status_main.pack(side=tk.TOP, anchor="w")

        self.status_detail = tk.Label(
            status_left,
            text="",
            fg=ED_WHITE,
            bg=ED_BG,
            font=("Courier", 11),
            anchor="w",
        )
        self.status_detail.pack(side=tk.TOP, anchor="w")

        self.auto_status_label = tk.Label(bottom_frame, text="", fg=ED_GREY, bg=ED_BG, font=("Courier", 12), anchor="e")
        self.auto_status_label.pack(side=tk.RIGHT)

        # --- STARTUP OVERLAY: big orange "Refreshing..." ---
        self.startup_overlay = tk.Label(
            self.root,
            text="REFRESHING…",
            fg=ED_ORANGE,
            bg=ED_BG,
            font=("Courier", 44, "bold"),
        )
        self.startup_overlay.place(relx=0.5, rely=0.5, anchor="center")
        self.startup_overlay.lift()

        # Close handling
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Loops
        self.root.after(0, self._apply_topmost_initial)
        self.root.after(TOPMOST_ENFORCE_EVERY_MS, self._enforce_topmost)
        self.root.after(250, self._auto_tick)

        # initial refresh
        self.refresh_route()

    # ---------- STARTUP OVERLAY ----------
    def _hide_startup_overlay(self):
        if self.startup_overlay is not None:
            try:
                self.startup_overlay.place_forget()
            except Exception:
                pass
            self.startup_overlay = None

    # ---------- STATUS (UI THREAD SAFE) ----------
    def post_status(self, main: str, detail: str = ""):
        if self._closing:
            return

        def _apply():
            if self._closing:
                return
            self.status_main.config(text=f"Status: {main}")
            self.status_detail.config(text=detail)
            self.root.update_idletasks()

        self.root.after(0, _apply)

    # ---------- Always-on-top ----------
    def _apply_topmost_initial(self):
        self.set_topmost(self.always_on_top)

    def set_topmost(self, enabled: bool):
        self.always_on_top = bool(enabled)
        try:
            self.root.attributes("-topmost", self.always_on_top)
        except Exception:
            pass

        if self.always_on_top:
            try:
                self.root.lift()
            except Exception:
                pass
            self.ontop_btn.config(bg=ED_GREY, fg="black", activebackground=ED_GREY, activeforeground="black")
        else:
            self.ontop_btn.config(bg=ED_DARK, fg=ED_GREY, activebackground=ED_DARK, activeforeground=ED_GREY)

    def toggle_topmost(self):
        self.set_topmost(not self.always_on_top)

    def _enforce_topmost(self):
        if self._closing:
            return
        if self.always_on_top:
            try:
                if str(self.root.state()).lower() != "iconic":
                    self.root.attributes("-topmost", True)
            except Exception:
                pass
        self.root.after(TOPMOST_ENFORCE_EVERY_MS, self._enforce_topmost)

    def on_close(self):
        self._closing = True
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------- UI events ----------
    def _on_mousewheel(self, event):
        try:
            self.list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    # ---------- Auto refresh ----------
    def toggle_auto_refresh(self):
        self.auto_refresh_enabled = not self.auto_refresh_enabled
        if self.auto_refresh_enabled:
            self.auto_btn.config(bg=ED_GREY, fg="black", activebackground=ED_GREY, activeforeground="black")
            self._schedule_next_auto()
        else:
            self.auto_btn.config(bg=ED_DARK, fg=ED_GREY, activebackground=ED_DARK, activeforeground=ED_GREY)
            self.next_auto_refresh_at = None
            self.auto_status_label.config(text="")

    def _schedule_next_auto(self):
        try:
            mins = float(self.auto_interval_entry.get())
            if mins < 1:
                mins = 1
        except Exception:
            mins = 15
        self.next_auto_refresh_at = datetime.now() + timedelta(minutes=mins)

    def _auto_tick(self):
        if self.auto_refresh_enabled and self.next_auto_refresh_at:
            remaining = (self.next_auto_refresh_at - datetime.now()).total_seconds()
            if remaining <= 0:
                self._schedule_next_auto()
                self.refresh_route()
            else:
                mm = int(remaining) // 60
                ss = int(remaining) % 60
                self.auto_status_label.config(text=f"Next auto refresh in {mm:02d}:{ss:02d}")
        self.root.after(1000, self._auto_tick)

    # ---------- Refresh ----------
    def refresh_route(self):
        if self.refresh_in_progress:
            return
        if (time.time() - self.last_refresh_started_at) < MIN_SECONDS_BETWEEN_REFRESHES:
            self.post_status("Waiting", f"Refresh cooldown ({MIN_SECONDS_BETWEEN_REFRESHES}s) not finished yet…")
            return

        self.refresh_in_progress = True
        self.last_refresh_started_at = time.time()
        self.refresh_btn.config(state="disabled", text="Refreshing…")

        # On first startup, keep the big overlay visible.
        # On later refreshes, we *do not* show it again.
        if not self._startup_refresh_pending:
            # ensure it's hidden if user refreshed after startup
            self._hide_startup_overlay()

        self.post_status("Starting refresh", "Loading local files and caches…")
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        # Local-first reloads
        self.post_status("Loading local systems list", f"Reading {SYSTEMS_FILE}…")
        self.systems_data = safe_load_json(SYSTEMS_FILE, default={})
        self.system_names = get_system_names(self.systems_data)

        self.post_status("Loading caches", f"Reading {LAST_DATA_FILE} and {COORDS_CACHE_FILE}…")
        self.last_data = safe_load_json(LAST_DATA_FILE, default={})
        self.coords_cache = safe_load_json(COORDS_CACHE_FILE, default={})

        if not self.system_names:
            self.root.after(0, self._handle_no_systems)
            return

        try:
            threshold_hours = float(self.threshold_entry.get())
        except Exception:
            threshold_hours = 24.0
        cutoff = datetime.now() - timedelta(hours=threshold_hours)

        self.post_status("Checking EDMarketConnector", "Looking for EDMarketConnector.exe…")
        edmarket_ok = is_edmarket_running()

        session = requests.Session()
        inara_limiter = RateLimiter(INARA_MIN_SECONDS_BETWEEN_CALLS)
        edsm_limiter = RateLimiter(EDSM_MIN_SECONDS_BETWEEN_CALLS)

        coords: Dict[str, Tuple[float, float, float]] = {}
        outdated: List[str] = []
        current: List[str] = []
        unknown: List[str] = []

        total = len(self.system_names)
        inara_checked = 0
        inara_skipped = 0

        for idx, sys_name in enumerate(self.system_names, start=1):
            self.post_status("Processing systems", f"{idx}/{total}: {sys_name}")

            # Coords local-first: cache -> local -> EDSM
            c = self._get_system_coords_local_first(sys_name, session, edsm_limiter)
            if c:
                coords[sys_name] = c

            # Update time local-first: cache -> maybe Inara
            info_updated_dt = None
            rec = self.last_data.get(sys_name, {}) if isinstance(self.last_data, dict) else {}
            info_str = rec.get("Info Updated") if isinstance(rec, dict) else None
            if isinstance(info_str, str):
                info_updated_dt = parse_inara_timestamp(info_str)

            needs_inara = False
            reason = ""
            if info_updated_dt is None:
                needs_inara = True
                reason = "no cached update time"
            elif info_updated_dt < cutoff:
                last_checked = parse_iso(str(rec.get("last_checked_inara", ""))) if isinstance(rec, dict) else None
                if (last_checked is None) or ((datetime.now() - last_checked).total_seconds() >= INARA_MIN_RECHECK_HOURS * 3600):
                    needs_inara = True
                    reason = "cached time is old"
                else:
                    inara_skipped += 1

            if needs_inara:
                self.post_status("Checking Inara", f"{idx}/{total}: {sys_name} ({reason})")
                new_str = fetch_inara_info_updated(session, inara_limiter, sys_name)
                inara_checked += 1
                self.last_data.setdefault(sys_name, {})["last_checked_inara"] = now_iso()
                if new_str:
                    self.last_data.setdefault(sys_name, {})["Info Updated"] = new_str
                    info_updated_dt = parse_inara_timestamp(new_str)

            if info_updated_dt is None:
                unknown.append(sys_name)
            elif info_updated_dt < cutoff:
                outdated.append(sys_name)
            else:
                current.append(sys_name)

        self.post_status("Saving caches", "Writing updated cache files to disk…")
        try:
            atomic_write_json(LAST_DATA_FILE, self.last_data)
            atomic_write_json(COORDS_CACHE_FILE, self.coords_cache)
        except Exception:
            pass

        self.post_status("Building route", "Optimizing route order…")
        attention = outdated + unknown
        attention_with_coords = [s for s in attention if s in coords]
        if len(attention_with_coords) >= 2:
            route = nearest_neighbor_tsp(coords, attention_with_coords)
        else:
            route = attention_with_coords[:]

        result = {
            "coords": coords,
            "route": route,
            "outdated": outdated,
            "unknown": unknown,
            "current": current,
            "edmarket_ok": edmarket_ok,
            "threshold_hours": threshold_hours,
            "inara_checked": inara_checked,
            "inara_skipped": inara_skipped,
            "refreshed_iso": now_iso(),
        }
        self.root.after(0, lambda: self._apply_refresh_result(result))

    def _handle_no_systems(self):
        self._clear_ui()
        # Startup overlay must go away even if we have no systems
        self._hide_startup_overlay()
        self._startup_refresh_pending = False

        self.post_status("No systems monitored", f"Add systems to {SYSTEMS_FILE} and refresh.")
        self.refresh_in_progress = False
        self.refresh_btn.config(state="normal", text="Refresh Route")

    def _get_system_coords_local_first(
        self,
        system_name: str,
        session: requests.Session,
        edsm_limiter: RateLimiter,
    ) -> Optional[Tuple[float, float, float]]:
        cached = self.coords_cache.get(system_name)
        if isinstance(cached, dict) and all(k in cached for k in ("x", "y", "z")):
            try:
                return float(cached["x"]), float(cached["y"]), float(cached["z"])
            except Exception:
                pass
        if isinstance(cached, (list, tuple)) and len(cached) == 3:
            try:
                return float(cached[0]), float(cached[1]), float(cached[2])
            except Exception:
                pass

        local = try_get_local_coords(system_name, self.systems_data)
        if local:
            self.coords_cache[system_name] = {"x": local[0], "y": local[1], "z": local[2], "source": "local", "fetched_at": now_iso()}
            return local

        coords = fetch_edsm_coords(session, edsm_limiter, system_name)
        if coords:
            self.coords_cache[system_name] = {"x": coords[0], "y": coords[1], "z": coords[2], "source": "edsm", "fetched_at": now_iso()}
            return coords

        return None

    # ---------- UI ----------
    def _clear_ui(self):
        self.ax.cla()
        self.canvas.draw()
        for w in self.scrollable_frame.winfo_children():
            w.destroy()

    def _apply_refresh_result(self, result: Dict[str, Any]):
        self._clear_ui()

        coords: Dict[str, Tuple[float, float, float]] = result["coords"]
        route: List[str] = result["route"]
        outdated: List[str] = result["outdated"]
        unknown: List[str] = result["unknown"]
        current: List[str] = result["current"]
        edmarket_ok: bool = result["edmarket_ok"]

        self.post_status("Updating display", "Drawing route and rebuilding the list…")

        self.ax.set_facecolor("black")
        self.ax.set_title("Route (Outdated + Unknown, coords-required)", color=ED_WHITE, fontsize=12)

        if route:
            xs = [coords[s][0] for s in route]
            ys = [coords[s][1] for s in route]
            zs = [coords[s][2] for s in route]
            self.ax.plot(xs, ys, zs, "o-", color=ED_ORANGE, linewidth=2.5, markersize=6)
            for i, s in enumerate(route):
                self.ax.text(xs[i], ys[i], zs[i], f" {i+1}", color=ED_WHITE, fontsize=11)
        else:
            self.ax.text(0, 0, 0, "No outdated/unknown systems with coords!", color=ED_WHITE, fontsize=14, ha="center")

        self.canvas.draw()

        route_index = {s: i + 1 for i, s in enumerate(route)}
        current_sorted = sorted(current)
        attention = sorted(set(outdated + unknown))
        attention_no_coords = [s for s in attention if s not in route_index]
        display_order = route + attention_no_coords + [s for s in current_sorted if s not in attention]

        for sys_name in display_order:
            row = tk.Frame(self.scrollable_frame, bg=ED_BG)
            row.pack(fill=tk.X, pady=2)

            is_outdated = sys_name in outdated
            is_unknown = sys_name in unknown
            is_in_route = sys_name in route_index
            has_coords = sys_name in coords

            if is_unknown:
                fg = ED_YELLOW
            elif is_outdated:
                fg = ED_ORANGE
            else:
                fg = ED_GREY

            prefix = "   "
            if is_in_route:
                prefix = f"{route_index[sys_name]:2d}. "
            elif (is_outdated or is_unknown) and not has_coords:
                prefix = " !! "

            label = f"{prefix}{sys_name}"
            if (is_outdated or is_unknown) and not has_coords:
                label += "  (no coords)"
            if is_unknown:
                label += "  (unknown update)"

            tk.Label(row, text=label, fg=fg, bg=ED_BG, font=("Courier", 12), anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

            if is_outdated or is_unknown:
                btn = tk.Button(row, text="Copy", bg=ED_ORANGE, fg="black", font=("Courier", 10), relief="flat", cursor="hand2")
                btn.pack(side=tk.RIGHT)
                btn.config(command=lambda b=btn, s=sys_name: self.copy_to_clipboard(b, s))

        edmc = "running" if edmarket_ok else "NOT running (some data may be stale)"
        self.post_status(
            "Done",
            f"{len(outdated)} outdated, {len(unknown)} unknown, {len(current)} current. "
            f"EDMC is {edmc}. Inara checked {result['inara_checked']} (skipped {result['inara_skipped']}). "
            f"Last refresh: {result['refreshed_iso']}.",
        )

        # IMPORTANT: remove big startup overlay once ready (first refresh only)
        if self._startup_refresh_pending:
            self._hide_startup_overlay()
            self._startup_refresh_pending = False

        self.refresh_in_progress = False
        self.refresh_btn.config(state="normal", text="Refresh Route")

        if self.always_on_top:
            self.root.after(50, lambda: self.set_topmost(True))

    def copy_to_clipboard(self, btn: tk.Button, system_name: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(system_name)
        self.root.update()
        btn.config(bg=ED_GREY, text="Copied", fg=ED_WHITE, state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    app = RoutePlannerApp(root)
    root.mainloop()
