import requests
import json
from datetime import datetime, timedelta
import tkinter as tk
import os
from bs4 import BeautifulSoup
import time
import re
import webbrowser

# ====================== FILES ======================
SAVE_FILE = "elite_systems.json"
PREV_STRENGTH_FILE = "previous_strengths.json"
LAST_DATA_FILE = "last_system_data.json"
WEEKLY_BASELINE_FILE = "weekly_baseline.json"

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
    try:
        return float(s.strip("%"))
    except:
        return float("inf")

def make_control_bar(strength, delta):
    blocks = 10
    filled = 0 if strength == float("inf") else round(strength / 10)
    filled = max(0, min(filled, blocks))
    bar = "■" * filled + "□" * (blocks - filled)
    sign = "+" if delta > 0 else ""
    return f"{bar} {sign}{delta:.1f}%"

def last_thursday_3am(now=None):
    if not now:
        now = datetime.now()
    days_since_thursday = (now.weekday() - 3) % 7
    thursday = now - timedelta(days=days_since_thursday)
    return thursday.replace(hour=3, minute=0, second=0, microsecond=0)

# ====================== DATA FETCH ======================
def fetch_edsm_data(system):
    try:
        r = requests.get(
            "https://www.edsm.net/api-v1/system",
            params={"systemName": system, "showInformation": 1},
            timeout=15
        )
        fr = requests.get(
            "https://www.edsm.net/api-system-v1/factions",
            params={"systemName": system},
            timeout=15
        )
        return r.json(), fr.json()
    except:
        return None, None

def scrape_inara(system):
    try:
        url = f"https://inara.cz/elite/starsystem/?search={system.replace(' ', '+')}"
        r = requests.get(url, headers={"User-Agent": "EDMonitor"}, timeout=20)
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text(" ")

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

        return state, strength, updated
    except:
        return "Unknown", "Unknown", "Unknown"

# ====================== REPORT ======================
def build_report(system, edsm, factions, state, strength, delta, updated):
    lines = [
        f"=== {system} ===",
        f"Power State: {state}",
        f"Control Strength: {strength}",
        f"Weekly Change: {delta:+.1f}%",
        f"Info Updated: {updated}",
        ""
    ]

    if edsm and "information" in edsm:
        i = edsm["information"]
        lines += [
            f"Controlling Faction: {i.get('faction','None')}",
            f"Allegiance: {i.get('allegiance','None')}",
            f"Government: {i.get('government','None')}",
            f"Security: {i.get('security','None')}",
            f"Population: {i.get('population','Unknown')}",
            "",
            "Factions:"
        ]

    if factions and "factions" in factions:
        for f in sorted(factions["factions"], key=lambda x: x["influence"], reverse=True):
            lines.append(f"{f['name']} - {f['influence']*100:.1f}%")

    return "\n".join(lines)

# ====================== APP ======================
class EliteMonitor:
    def __init__(self, root):
        self.root = root
        root.title("Elite Dangerous Powerplay Monitor")
        root.configure(bg=ED_BG)
        root.geometry("1500x1000")

        self.systems = self.load(SAVE_FILE, [])
        self.prev_strengths = self.load(PREV_STRENGTH_FILE, {})
        self.data = self.load(LAST_DATA_FILE, {})
        self.weekly_baseline = self.load(WEEKLY_BASELINE_FILE, {"timestamp": "", "strengths": {}})

        self.ui()
        self.update_weekly_baseline()
        self.refresh_display()
        self.auto_refresh()
        self.countdown()

    # ================= UI =================
    def ui(self):
        main = tk.Frame(self.root, bg=ED_BG)
        main.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        top_frame = tk.Frame(main, bg=ED_BG)
        top_frame.pack(fill=tk.X)
        tk.Label(top_frame, text="Strength Trend (Week):", fg=ED_ORANGE, bg=ED_BG, font=("Courier", 12, "bold")).pack(side=tk.LEFT, padx=(0,20))
        tk.Label(top_frame, text="PP Systems Monitored:", fg=ED_ORANGE, bg=ED_BG, font=("Courier", 12, "bold")).pack(side=tk.LEFT)
        tk.Label(top_frame, text="v1.3", fg=ED_ORANGE, bg=ED_BG, font=("Courier", 12, "bold")).pack(side=tk.RIGHT)

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
            text="Enter Exact System Name: click Add, to remove click on system click Remove",
            fg=ED_ORANGE, bg=ED_BG, font=("Courier", 10)
        ).pack(anchor="w", pady=(5,0))

        self.entry = tk.Entry(left, bg="#111", fg=ED_ORANGE,
                              insertbackground=ED_ORANGE,
                              font=("Courier", 12))
        self.entry.pack(fill=tk.X, pady=5)

        btns = tk.Frame(left, bg=ED_BG)
        btns.pack(pady=5)
        tk.Button(btns, text="Add", command=self.add, bg=ED_ORANGE, fg="black", width=10).grid(row=0, column=0, padx=5)
        tk.Button(btns, text="Remove", command=self.remove, bg="#333", fg=ED_ORANGE, width=10).grid(row=0, column=1, padx=5)
        tk.Button(left, text="Refresh Now", command=self.refresh, bg=ED_ORANGE, fg="black", font=("Courier", 14, "bold")).pack(pady=10)

        right = tk.Frame(body, bg=ED_BG, width=720)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(20, 0))
        right.pack_propagate(False)

        self.text = tk.Text(
            right, bg=ED_BG, fg=ED_ORANGE,
            font=("Courier", 14), wrap="word",
            state="disabled"
        )
        self.text.pack(fill=tk.BOTH, expand=True)

        bottom_frame = tk.Frame(main, bg=ED_BG)
        bottom_frame.pack(fill=tk.X, pady=10)
        self.status = tk.Label(bottom_frame, text="Ready", fg=ED_ORANGE, bg=ED_BG, font=("Courier", 12), anchor="w")
        self.status.pack(side=tk.LEFT)
        tk.Button(bottom_frame, text="Donate via PayPal", command=self.donate, bg=ED_BLUE, fg="white", width=20).pack(side=tk.RIGHT)

    # ================= DATA =================
    def load(self, f, default):
        if os.path.exists(f):
            return json.load(open(f))
        return default

    def save(self, f, data):
        json.dump(data, open(f, "w"), indent=2)

    def donate(self):
        webbrowser.open("Https://www.paypal.com/ncp/payment/9UKRVTWBH93V6")

    # ================= WEEKLY BASELINE =================
    def update_weekly_baseline(self):
        last_weekly = self.weekly_baseline.get("timestamp", "")
        now = datetime.now()
        last_thu = last_thursday_3am(now)
        update_needed = False

        if last_weekly:
            try:
                last_weekly_dt = datetime.fromisoformat(last_weekly)
                if last_weekly_dt < last_thu:
                    update_needed = True
            except:
                update_needed = True
        else:
            update_needed = True

        if update_needed:
            strengths = {}
            for s in self.systems:
                strengths[s] = self.prev_strengths.get(s, "Unknown")
            self.weekly_baseline = {
                "timestamp": last_thu.isoformat(),
                "strengths": strengths
            }
            self.save(WEEKLY_BASELINE_FILE, self.weekly_baseline)

    # ================= SYSTEM FUNCTIONS =================
    def add(self):
        s = self.entry.get().strip()
        if s and s not in self.systems:
            self.systems.append(s)
            self.save(SAVE_FILE, self.systems)
        self.entry.delete(0, tk.END)
        self.refresh_display()

    def remove(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        s = self.sorted_systems[sel[0]]
        self.systems.remove(s)
        self.data.pop(s, None)
        self.prev_strengths.pop(s, None)
        self.save(SAVE_FILE, self.systems)
        self.save(LAST_DATA_FILE, self.data)
        self.save(PREV_STRENGTH_FILE, self.prev_strengths)
        self.refresh_display()
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.config(state="disabled")

    # ================= REFRESH =================
    def refresh(self):
        for i, s in enumerate(self.systems):
            self.status.config(text=f"Updating {s} ({i+1}/{len(self.systems)})")
            self.root.update()

            edsm, factions = fetch_edsm_data(s)
            state, strength_s, updated = scrape_inara(s)
            strength = parse_strength(strength_s)

            prev = parse_strength(self.prev_strengths.get(s, strength))
            weekly_strength = parse_strength(self.weekly_baseline["strengths"].get(s, strength))
            delta = round(strength - weekly_strength, 2) if strength != float("inf") else 0.0

            self.prev_strengths[s] = strength_s
            self.save(PREV_STRENGTH_FILE, self.prev_strengths)

            self.data[s] = {
                "state": state,
                "strength": strength,
                "delta": delta,
                "report": build_report(s, edsm, factions, state, strength_s, delta, updated)
            }

            time.sleep(1.2)

        self.save(LAST_DATA_FILE, self.data)
        self.status.config(text="✓ Update complete")
        self.refresh_display()

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
            bar = make_control_bar(d.get("strength", float("inf")), d.get("delta", 0))
            self.listbox.insert(tk.END, f"\t\t\t{bar}    {s}")  # 3 tabs + 4 spaces for more separation

            idx = self.listbox.size() - 1
            delta = d.get("delta", 0)
            color = ED_BLUE if delta > 0 else ED_YELLOW if delta < 0 else ED_ORANGE
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

    # ================= AUTO =================
    def auto_refresh(self):
        now = datetime.now()
        if now.minute == 20 and now.second < 5:
            self.refresh()
        self.root.after(60000, self.auto_refresh)

    def countdown(self):
        now = datetime.now()
        nxt = now.replace(minute=20, second=0, microsecond=0)
        if now.minute >= 20:
            nxt += timedelta(hours=1)
        d = nxt - now
        m, s = divmod(d.seconds, 60)
        if "Updating" not in self.status.cget("text"):
            self.status.config(text=f"Ready | Next auto-refresh in {m:02d}:{s:02d}")
        self.root.after(1000, self.countdown)

# ====================== RUN ======================
if __name__ == "__main__":
    root = tk.Tk()
    root.attributes("-topmost", True)  # <-- add this line
    app = EliteMonitor(root)
    root.mainloop()

