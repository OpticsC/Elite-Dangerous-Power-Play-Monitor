import requests
import json
from datetime import datetime, timedelta
import tkinter as tk
import os
from bs4 import BeautifulSoup
from bs4 import FeatureNotFound
import time
import re
import webbrowser
import sys
from pathlib import Path

# ====================== BASE PATH (JSONs next to EXE) ======================
def get_portable_base_dir() -> Path:
    # PyInstaller exe: this is the folder containing the .exe (not the temp _MEI folder)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Normal .py run
    return Path(__file__).resolve().parent

BASE_DIR = get_portable_base_dir()

def p(name: str) -> str:
    return str(BASE_DIR / name)

# ====================== FILES (stored next to EXE) ======================
SAVE_FILE = p("elite_systems.json")
PREV_STRENGTH_FILE = p("previous_strengths.json")
LAST_DATA_FILE = p("last_system_data.json")
WEEKLY_BASELINE_FILE = p("weekly_baseline.json")      # v1.3.1 legacy baseline (kept)
BASELINE_HISTORY_FILE = p("baseline_history.json")    # stores multiple Thu 03:00 snapshots (for 2W)
SETTINGS_FILE = p("settings.json")                    # auto update ON/OFF

# ====================== LIMITS / SAFETY (polite defaults) ======================
MANUAL_REFRESH_COOLDOWN_SEC = 120          # prevent spam clicking "Refresh Now"
INARA_MIN_SECONDS_BETWEEN_REQUESTS = 10    # min delay between Inara requests
INARA_MAX_REQUESTS_PER_HOUR = 60           # cap Inara page fetches per hour
INARA_BLOCK_COOLDOWN_SEC = 3600            # if rate-limited, pause Inara for 1 hour
EDSM_MIN_SECONDS_BETWEEN_REQUESTS = 1.0    # light throttle

# ====================== COLORS ======================
ED_BG = "#000000"
ED_ORANGE = "#FF6600"
ED_WHITE = "#FFFFFF"
ED_YELLOW = "#FFFF00"
ED_BLUE = "#00A2FF"

# ====================== STATE PRIORITY ======================
STATE_PRIORITY = {
    "Unknown": 0,
    "Expansion": 1,
    "Exploited": 2,
    "Fortified": 3,
    "Stronghold": 4,
    "Contested": 5,
    "Uncontrolled": 6
}

def get_state_priority(state):
    return STATE_PRIORITY.get(state, 99)

# ====================== HELPERS ======================
def parse_strength(s):
    """Accepts '52.3%', 'Unknown', None, float/int. Returns float percent, Unknown -> inf."""
    if isinstance(s, (int, float)):
        return float(s)
    if s is None:
        return float("inf")
    try:
        t = str(s).strip()
        if t == "" or t.lower() == "unknown":
            return float("inf")
        return float(t.strip("%"))
    except Exception:
        return float("inf")

def fmt_delta(d):
    if d is None:
        return "n/a"
    sign = "+" if d > 0 else ""
    return f"{sign}{d:.1f}%"

def make_control_bar(strength, d1, d2):
    blocks = 10
    filled = 0 if strength == float("inf") else round(strength / 10)
    filled = max(0, min(filled, blocks))
    bar = "■" * filled + "□" * (blocks - filled)
    return f"{bar}  1W:{fmt_delta(d1)}  2W:{fmt_delta(d2)}"

def last_thursday_3am(now=None):
    if not now:
        now = datetime.now()
    days_since_thursday = (now.weekday() - 3) % 7  # Thu=3
    thursday = now - timedelta(days=days_since_thursday)
    return thursday.replace(hour=3, minute=0, second=0, microsecond=0)

def iso_seconds(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")

# ====================== JSON SAFE IO ======================
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json_atomic(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass

# ====================== NETWORK ======================
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "EDMonitor/1.3.1+"})

# ====================== DATA FETCH ======================
def fetch_edsm_data(system):
    """Returns (edsm_system_json, edsm_factions_json) or (None, None)."""
    try:
        r = SESSION.get(
            "https://www.edsm.net/api-v1/system",
            params={"systemName": system, "showInformation": 1},
            timeout=15
        )
        fr = SESSION.get(
            "https://www.edsm.net/api-system-v1/factions",
            params={"systemName": system},
            timeout=15
        )
        edsm = r.json() if r.status_code == 200 else None
        facs = fr.json() if fr.status_code == 200 else None
        return edsm, facs
    except Exception:
        return None, None

def scrape_inara(system):
    """
    Scrape PowerPlay-ish state/strength from Inara.
    Returns: (state, strength_str, updated_str, status)
      status in {"ok","rate_limited","error"}
    """
    try:
        url = f"https://inara.cz/elite/starsystem/?search={system.replace(' ', '+')}"
        r = SESSION.get(url, timeout=20)

        if r.status_code == 429:
            return "Unknown", "Unknown", "Unknown", "rate_limited"

        low = (r.text or "").lower()
        if "too many requests" in low or "too much requests" in low:
            return "Unknown", "Unknown", "Unknown", "rate_limited"

        if r.status_code != 200:
            return "Unknown", "Unknown", "Unknown", "error"

        try:
            soup = BeautifulSoup(r.text, "lxml")
        except FeatureNotFound:
            soup = BeautifulSoup(r.text, "html.parser")

        text = soup.get_text(" ", strip=True)

        m = re.search(
            r"(Exploited|Fortified|Stronghold|Contested|Expansion|Uncontrolled)\s+(\d+(?:\.\d+)?%)",
            text
        )
        state = m.group(1) if m else "Unknown"
        strength = m.group(2) if m else "Unknown"

        updated = "Unknown"
        dm = re.search(r"(\d{1,2}\s[A-Za-z]{3}\s\d{4},\s\d{1,2}:\d{2}(?:am|pm))", text)
        if dm:
            updated = dm.group(1)

        return state, strength, updated, "ok"
    except Exception:
        return "Unknown", "Unknown", "Unknown", "error"

# ====================== REPORT ======================
def build_report(system, edsm, factions, state, strength_s, d1, d2, updated, inara_note=""):
    lines = [
        f"=== {system} ===",
        f"Power State: {state}",
        f"Control Strength: {strength_s}",
        f"Weekly Change (1W): {fmt_delta(d1)}",
        f"Weekly Change (2W): {fmt_delta(d2)}",
        f"Info Updated: {updated}",
    ]
    if inara_note:
        lines.append(f"Inara: {inara_note}")
    lines.append("")

    if edsm and isinstance(edsm, dict) and "information" in edsm:
        i = edsm.get("information") or {}
        lines += [
            f"Controlling Faction: {i.get('faction','None')}",
            f"Allegiance: {i.get('allegiance','None')}",
            f"Government: {i.get('government','None')}",
            f"Security: {i.get('security','None')}",
            f"Population: {i.get('population','Unknown')}",
            "",
            "Factions:"
        ]

    if factions and isinstance(factions, dict) and "factions" in factions:
        fac_list = factions.get("factions") or []
        fac_list = sorted(fac_list, key=lambda x: x.get("influence", 0), reverse=True)
        for f in fac_list:
            lines.append(f"{f.get('name','Unknown')} - {float(f.get('influence',0))*100:.1f}%")

    return "\n".join(lines)

# ====================== APP ======================
class EliteMonitor:
    def __init__(self, root):
        self.root = root
        root.title("Elite Dangerous Powerplay Monitor")
        root.configure(bg=ED_BG)
        root.geometry("1500x1000")

        # Data
        self.systems = load_json(SAVE_FILE, [])
        self.prev_strengths = load_json(PREV_STRENGTH_FILE, {})
        self.data = load_json(LAST_DATA_FILE, {})

        # Legacy baseline (v1.3.1)
        self.weekly_baseline = load_json(WEEKLY_BASELINE_FILE, {"timestamp": "", "strengths": {}})

        # New: baseline history (multiple Thu 03:00 snapshots)
        self.baseline_history = load_json(BASELINE_HISTORY_FILE, [])
        self.seed_baseline_history_from_legacy_if_needed()

        # Settings
        self.settings = load_json(SETTINGS_FILE, {"auto_enabled": True})
        self.auto_enabled = bool(self.settings.get("auto_enabled", True))

        # Run state
        self.sorted_systems = []
        self.refresh_running = False
        self.last_manual_refresh_at = 0.0
        self.last_auto_refresh_key = None

        # Inara politeness tracking
        self.inara_request_times = []     # epoch seconds within last hour
        self.last_inara_request_at = 0.0
        self.inara_blocked_until = 0.0

        # EDSM throttle
        self.last_edsm_request_at = 0.0

        self.ui()

        # Ensure we have a baseline snapshot for last Thu 03:00 (anchor)
        self.ensure_snapshot_for_last_thu_3am()

        self.refresh_display()
        self.auto_refresh_tick()
        self.countdown()

        self.set_status(f"Ready | Auto:{'ON' if self.auto_enabled else 'OFF'} | JSONs: {BASE_DIR}")

    # ================= UI =================
    def ui(self):
        main = tk.Frame(self.root, bg=ED_BG)
        main.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        top_frame = tk.Frame(main, bg=ED_BG)
        top_frame.pack(fill=tk.X)
        tk.Label(top_frame, text="Strength Trend (Thu 03:00) 1W & 2W:", fg=ED_ORANGE, bg=ED_BG,
                 font=("Courier", 12, "bold")).pack(side=tk.LEFT, padx=(0, 20))
        tk.Label(top_frame, text="PP Systems Monitored:", fg=ED_ORANGE, bg=ED_BG,
                 font=("Courier", 12, "bold")).pack(side=tk.LEFT)
        tk.Label(top_frame, text="v1.3.1", fg=ED_ORANGE, bg=ED_BG,
                 font=("Courier", 12, "bold")).pack(side=tk.RIGHT)

        body = tk.Frame(main, bg=ED_BG)
        body.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(body, bg=ED_BG, width=720)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 20))
        left.pack_propagate(False)

        self.listbox = tk.Listbox(
            left, bg="#111", fg=ED_ORANGE,
            font=("Courier", 12),
            selectbackground=ED_ORANGE,
            selectforeground=ED_WHITE
        )
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.select)

        tk.Label(
            left,
            text="Enter Exact System Name: click Add; to remove select system then Remove",
            fg=ED_ORANGE, bg=ED_BG, font=("Courier", 10)
        ).pack(anchor="w", pady=(5, 0))

        self.entry = tk.Entry(left, bg="#111", fg=ED_ORANGE, insertbackground=ED_ORANGE, font=("Courier", 12))
        self.entry.pack(fill=tk.X, pady=5)

        btns = tk.Frame(left, bg=ED_BG)
        btns.pack(pady=5)
        tk.Button(btns, text="Add", command=self.add, bg=ED_ORANGE, fg="black", width=10)\
            .grid(row=0, column=0, padx=5)
        tk.Button(btns, text="Remove", command=self.remove, bg="#333", fg=ED_ORANGE, width=10)\
            .grid(row=0, column=1, padx=5)

        self.refresh_btn = tk.Button(
            left, text="Refresh Now", command=self.refresh_now_clicked,
            bg=ED_ORANGE, fg="black", font=("Courier", 14, "bold")
        )
        self.refresh_btn.pack(pady=(10, 6))

        self.auto_btn = tk.Button(
            left, text=self.auto_button_text(), command=self.toggle_auto,
            bg="#333", fg=ED_ORANGE, font=("Courier", 12, "bold")
        )
        self.auto_btn.pack(pady=(0, 10))

        right = tk.Frame(body, bg=ED_BG, width=720)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(20, 0))
        right.pack_propagate(False)

        self.text = tk.Text(
            right, bg=ED_BG, fg=ED_ORANGE,
            font=("Courier", 14), wrap="word", state="disabled"
        )
        self.text.pack(fill=tk.BOTH, expand=True)

        bottom_frame = tk.Frame(main, bg=ED_BG)
        bottom_frame.pack(fill=tk.X, pady=10)
        self.status = tk.Label(bottom_frame, text="Ready", fg=ED_ORANGE, bg=ED_BG,
                               font=("Courier", 12), anchor="w")
        self.status.pack(side=tk.LEFT)
        tk.Button(bottom_frame, text="Donate via PayPal", command=self.donate,
                  bg=ED_BLUE, fg="white", width=20).pack(side=tk.RIGHT)

    def donate(self):
        webbrowser.open("https://www.paypal.com/ncp/payment/9UKRVTWBH93V6")

    def set_status(self, msg):
        self.status.config(text=msg)
        self.root.update()

    def auto_button_text(self):
        return f"Auto Update: {'ON' if self.auto_enabled else 'OFF'}"

    # ================= BASELINES (Thu 03:00 anchored) =================
    def seed_baseline_history_from_legacy_if_needed(self):
        if self.baseline_history:
            return
        ts = self.weekly_baseline.get("timestamp", "")
        strengths = self.weekly_baseline.get("strengths", {})
        if ts and isinstance(strengths, dict):
            self.baseline_history = [{"timestamp": ts, "strengths": strengths}]
            save_json_atomic(BASELINE_HISTORY_FILE, self.baseline_history)

    def best_known_strength_str(self, system: str) -> str:
        if system in self.prev_strengths:
            return self.prev_strengths.get(system, "Unknown")
        d = self.data.get(system, {})
        if isinstance(d, dict):
            if "strength_s" in d:
                return d.get("strength_s", "Unknown")
            if "strength_str" in d:
                return d.get("strength_str", "Unknown")
        return "Unknown"

    def ensure_snapshot_for_last_thu_3am(self):
        now = datetime.now()
        t1 = iso_seconds(last_thursday_3am(now))
        existing = {snap.get("timestamp") for snap in self.baseline_history if isinstance(snap, dict)}
        if t1 in existing:
            return

        strengths = {s: self.best_known_strength_str(s) for s in self.systems}
        self.baseline_history.append({"timestamp": t1, "strengths": strengths})
        self.baseline_history = sorted(self.baseline_history, key=lambda x: x.get("timestamp", ""))[-12:]
        save_json_atomic(BASELINE_HISTORY_FILE, self.baseline_history)

        self.weekly_baseline = {"timestamp": t1, "strengths": strengths}
        save_json_atomic(WEEKLY_BASELINE_FILE, self.weekly_baseline)

    def get_baselines_for_system(self, system: str, now=None):
        if now is None:
            now = datetime.now()
        t1 = iso_seconds(last_thursday_3am(now))
        t2 = iso_seconds(last_thursday_3am(now) - timedelta(days=14))

        snap_map = {snap.get("timestamp"): (snap.get("strengths") or {}) for snap in self.baseline_history}
        b1 = snap_map.get(t1, {}).get(system, None)
        b2 = snap_map.get(t2, {}).get(system, None)
        return b1, b2

    # ================= SYSTEM FUNCTIONS =================
    def add(self):
        s = self.entry.get().strip()
        if s and s not in self.systems:
            self.systems.append(s)
            save_json_atomic(SAVE_FILE, self.systems)
            for snap in self.baseline_history:
                snap.setdefault("strengths", {}).setdefault(s, "Unknown")
            save_json_atomic(BASELINE_HISTORY_FILE, self.baseline_history)
        self.entry.delete(0, tk.END)
        self.refresh_display()

    def remove(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        s = self.sorted_systems[sel[0]]

        if s in self.systems:
            self.systems.remove(s)

        self.data.pop(s, None)
        self.prev_strengths.pop(s, None)

        for snap in self.baseline_history:
            try:
                (snap.get("strengths") or {}).pop(s, None)
            except Exception:
                pass

        save_json_atomic(SAVE_FILE, self.systems)
        save_json_atomic(LAST_DATA_FILE, self.data)
        save_json_atomic(PREV_STRENGTH_FILE, self.prev_strengths)
        save_json_atomic(BASELINE_HISTORY_FILE, self.baseline_history)

        self.refresh_display()
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.config(state="disabled")

    # ================= RATE LIMIT HELPERS =================
    def prune_inara_hour_window(self):
        cutoff = time.time() - 3600
        self.inara_request_times = [t for t in self.inara_request_times if t >= cutoff]

    def can_hit_inara(self):
        now = time.time()

        if now < self.inara_blocked_until:
            wait = int(self.inara_blocked_until - now)
            return False, f"Inara cooldown ({wait}s left)"

        self.prune_inara_hour_window()
        if len(self.inara_request_times) >= INARA_MAX_REQUESTS_PER_HOUR:
            return False, f"Inara hourly cap reached ({INARA_MAX_REQUESTS_PER_HOUR}/hr)"

        since_last = now - self.last_inara_request_at
        if since_last < INARA_MIN_SECONDS_BETWEEN_REQUESTS:
            sleep_for = INARA_MIN_SECONDS_BETWEEN_REQUESTS - since_last
            self.set_status(f"Polite delay (Inara): {sleep_for:.1f}s")
            time.sleep(sleep_for)

        return True, ""

    def edsm_throttle(self):
        since_last = time.time() - self.last_edsm_request_at
        if since_last < EDSM_MIN_SECONDS_BETWEEN_REQUESTS:
            time.sleep(EDSM_MIN_SECONDS_BETWEEN_REQUESTS - since_last)

    # ================= REFRESH =================
    def refresh_now_clicked(self):
        if self.refresh_running:
            self.set_status("Refresh already running…")
            return

        now = time.time()
        if now - self.last_manual_refresh_at < MANUAL_REFRESH_COOLDOWN_SEC:
            wait = int(MANUAL_REFRESH_COOLDOWN_SEC - (now - self.last_manual_refresh_at))
            self.set_status(f"Please wait {wait}s before refreshing again (cooldown).")
            return

        self.last_manual_refresh_at = now
        self.refresh()

    def refresh(self):
        self.refresh_running = True
        self.refresh_btn.config(state="disabled")
        try:
            self.ensure_snapshot_for_last_thu_3am()

            for i, s in enumerate(self.systems):
                self.set_status(f"Updating {s} ({i+1}/{len(self.systems)})")

                self.edsm_throttle()
                edsm, factions = fetch_edsm_data(s)
                self.last_edsm_request_at = time.time()

                inara_note = ""
                ok, why = self.can_hit_inara()
                if ok:
                    state, strength_s, updated, status = scrape_inara(s)
                    self.last_inara_request_at = time.time()
                    self.inara_request_times.append(self.last_inara_request_at)

                    if status == "rate_limited":
                        self.inara_blocked_until = time.time() + INARA_BLOCK_COOLDOWN_SEC
                        inara_note = "Rate limited — pausing Inara ~1 hour. Using cached."
                        cached = self.data.get(s, {})
                        state = cached.get("state", "Unknown")
                        strength_s = cached.get("strength_s", cached.get("strength_str", "Unknown"))
                        updated = cached.get("updated", "Unknown")
                    elif status != "ok":
                        inara_note = "Fetch error — using whatever was available."
                else:
                    cached = self.data.get(s, {})
                    state = cached.get("state", "Unknown")
                    strength_s = cached.get("strength_s", cached.get("strength_str", "Unknown"))
                    updated = cached.get("updated", "Unknown")
                    inara_note = f"Skipped ({why}) — using cached."

                strength = parse_strength(strength_s)

                b1_str, b2_str = self.get_baselines_for_system(s)
                b1 = parse_strength(b1_str) if b1_str is not None else float("inf")
                b2 = parse_strength(b2_str) if b2_str is not None else float("inf")

                d1 = None if (strength == float("inf") or b1 == float("inf")) else round(strength - b1, 2)
                d2 = None if (strength == float("inf") or b2 == float("inf")) else round(strength - b2, 2)

                self.prev_strengths[s] = strength_s
                report = build_report(s, edsm, factions, state, strength_s, d1, d2, updated, inara_note=inara_note)

                self.data[s] = {
                    "state": state,
                    "strength": strength,
                    "strength_s": strength_s,
                    "delta_1w": d1,
                    "delta_2w": d2,
                    "updated": updated,
                    "report": report,
                    "last_refresh": iso_seconds(datetime.now())
                }

                time.sleep(0.25)

            save_json_atomic(PREV_STRENGTH_FILE, self.prev_strengths)
            save_json_atomic(LAST_DATA_FILE, self.data)

            self.refresh_display()
            self.set_status(f"✓ Update complete @ {datetime.now().strftime('%H:%M:%S')}")
        finally:
            self.refresh_running = False
            self.refresh_btn.config(state="normal")

    # ================= DISPLAY =================
    def refresh_display(self):
        self.listbox.delete(0, tk.END)

        self.sorted_systems = sorted(
            self.systems,
            key=lambda s: (
                get_state_priority(self.data.get(s, {}).get("state", "Unknown")),
                self.data.get(s, {}).get("strength", float("inf"))
            )
        )

        for s in self.sorted_systems:
            d = self.data.get(s, {})
            bar = make_control_bar(d.get("strength", float("inf")), d.get("delta_1w", None), d.get("delta_2w", None))
            self.listbox.insert(tk.END, f"\t\t{bar}    {s}")

            idx = self.listbox.size() - 1
            d1 = d.get("delta_1w", None)
            if d1 is None:
                color = ED_ORANGE
            else:
                color = ED_BLUE if d1 > 0 else ED_YELLOW if d1 < 0 else ED_ORANGE
            self.listbox.itemconfig(idx, fg=color)

    def select(self, _):
        sel = self.listbox.curselection()
        if not sel:
            return
        s = self.sorted_systems[sel[0]]
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, self.data.get(s, {}).get("report", ""))
        self.text.config(state="disabled")

    # ================= AUTO REFRESH + TOGGLE =================
    def toggle_auto(self):
        self.auto_enabled = not self.auto_enabled
        self.settings["auto_enabled"] = self.auto_enabled
        save_json_atomic(SETTINGS_FILE, self.settings)
        self.auto_btn.config(text=self.auto_button_text())
        self.set_status(f"Auto Update turned {'ON' if self.auto_enabled else 'OFF'}.")

    def auto_refresh_tick(self):
        now = datetime.now()
        key = now.strftime("%Y-%m-%d %H")

        if self.auto_enabled and (not self.refresh_running) and now.minute == 20 and self.last_auto_refresh_key != key:
            self.last_auto_refresh_key = key
            if time.time() - self.last_manual_refresh_at >= 15:
                self.refresh()

        self.root.after(1000, self.auto_refresh_tick)

    def countdown(self):
        now = datetime.now()
        nxt = now.replace(minute=20, second=0, microsecond=0)
        if now.minute >= 20:
            nxt += timedelta(hours=1)
        d = nxt - now
        m, s = divmod(d.seconds, 60)

        if not self.refresh_running:
            auto_txt = "ON" if self.auto_enabled else "OFF"
            self.status.config(text=f"Ready | Auto:{auto_txt} | Next auto in {m:02d}:{s:02d} | JSONs: {BASE_DIR}")

        self.root.after(1000, self.countdown)

# ====================== RUN ======================
if __name__ == "__main__":
    root = tk.Tk()
    root.attributes("-topmost", True)
    app = EliteMonitor(root)
    root.mainloop()
