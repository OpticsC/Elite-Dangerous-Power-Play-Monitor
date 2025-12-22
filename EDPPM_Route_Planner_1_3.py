import requests
import json
import os
import time
import re
import psutil
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import messagebox
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import webbrowser

# Files
SYSTEMS_FILE = "elite_systems.json"
LAST_DATA_FILE = "last_system_data.json"
COORDS_CACHE_FILE = "system_coords_cache.json"

# Colors
ED_BG = "#000000"
ED_ORANGE = "#FF6600"
ED_GREY = "#888888"
ED_WHITE = "#FFFFFF"

def is_edmarket_running():
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] == "EDMarketConnector.exe":
            return True
    return False

def load_json(file):
    if os.path.exists(file):
        with open(file, 'r') as f:
            return json.load(f)
    return {}

def save_json(file, data):
    with open(file, 'w') as f:
        json.dump(data, f, indent=2)

def get_system_coords(system_name, cache):
    if system_name in cache:
        return cache[system_name]
    try:
        url = f"https://www.edsm.net/api-v1/system?systemName={system_name}&showCoordinates=1"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if 'coords' in data:
                coords = (data['coords']['x'], data['coords']['y'], data['coords']['z'])
                cache[system_name] = coords
                return coords
    except:
        pass
    return None

def get_inara_info_update(system_name):
    try:
        url = f"https://inara.cz/elite/starsystem/?search={system_name.replace(' ','+')}"

        headers = {'User-Agent': 'EliteMonitorApp/1.3'}
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            return None
        text = r.text
        date_pattern = r'(\d{1,2}\s[A-Za-z]{3}\s\d{4},\s\d{1,2}:\d{2}(?:am|pm))'
        matches = re.findall(date_pattern, text)
        if matches:
            return matches[0]
    except:
        pass
    return None

# --- Fast approximate TSP using Nearest Neighbor + 2-opt ---
def nearest_neighbor_tsp(coords_dict, systems):
    if len(systems) <= 1:
        return systems[:]
    
    unvisited = set(systems)
    current = systems[0]
    route = [current]
    unvisited.remove(current)
    
    while unvisited:
        nearest = min(unvisited, key=lambda s: np.linalg.norm(np.array(coords_dict[current]) - np.array(coords_dict[s])))
        route.append(nearest)
        unvisited.remove(nearest)
        current = nearest
    
    return two_opt(route, coords_dict)

def two_opt(route, coords_dict):
    """Simple 2-opt optimization"""
    best = route[:]
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best)-2):
            for j in range(i+1, len(best)):
                if j-i == 1: continue
                new_route = best[:i] + best[i:j][::-1] + best[j:]
                if route_distance(new_route, coords_dict) < route_distance(best, coords_dict):
                    best = new_route
                    improved = True
        if not improved:
            break
    return best

def route_distance(route, coords_dict):
    dist = 0
    for i in range(len(route)-1):
        p1 = np.array(coords_dict[route[i]])
        p2 = np.array(coords_dict[route[i+1]])
        dist += np.linalg.norm(p1-p2)
    return dist

class RoutePlannerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Elite Dangerous Route Planner v1.3")
        self.root.configure(bg=ED_BG)
        self.root.geometry("1400x900")
        self.root.attributes('-topmost', True)
        
        try:
            self.root.iconbitmap("edppm.ico")
        except:
            pass
        
        self.systems = load_json(SYSTEMS_FILE)
        self.last_data = load_json(LAST_DATA_FILE)
        self.coords_cache = load_json(COORDS_CACHE_FILE)
        
        # --- Top frame ---
        top_frame = tk.Frame(root, bg=ED_BG)
        top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Label(top_frame, text="EDPPM Route Planner v1.3", fg=ED_ORANGE, bg=ED_BG, font=("Courier",16,"bold")).pack(side=tk.LEFT)
        
        # Threshold label and entry
        threshold_frame = tk.Frame(top_frame, bg=ED_BG)
        threshold_frame.pack(side=tk.RIGHT)
        tk.Label(threshold_frame, text="Update Threshold (hours):", fg=ED_WHITE, bg=ED_BG, font=("Courier",12)).pack(side=tk.LEFT)
        self.threshold_entry = tk.Entry(threshold_frame, width=5, bg="#111111", fg=ED_ORANGE, insertbackground=ED_ORANGE, font=("Courier",12))
        self.threshold_entry.insert(0, "24")
        self.threshold_entry.pack(side=tk.LEFT, padx=(5,10))
        
        # Refresh button
        self.refresh_btn = tk.Button(top_frame, text="Refresh Route", bg=ED_ORANGE, fg="black", font=("Courier",12,"bold"), command=self.refresh_route)
        self.refresh_btn.pack(side=tk.RIGHT)
        
        # --- Main frame ---
        main_frame = tk.Frame(root, bg=ED_BG)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Plot frame
        self.plot_frame = tk.Frame(main_frame, bg=ED_BG)
        self.plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.fig = plt.Figure(figsize=(8,8), facecolor='black')
        self.ax = self.fig.add_subplot(111, projection='3d')
        self.ax.set_facecolor('black')
        self.canvas = FigureCanvasTkAgg(self.fig, self.plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Scrollable system list
        route_frame = tk.Frame(main_frame, bg=ED_BG)
        route_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(20,0))
        tk.Label(route_frame, text="All Monitored Systems", fg=ED_ORANGE, bg=ED_BG, font=("Courier",14,"bold")).pack(anchor="w")
        
        canvas = tk.Canvas(route_frame, bg=ED_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(route_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = tk.Frame(canvas, bg=ED_BG)
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0,0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bottom frame with status + PayPal button
        bottom_frame = tk.Frame(root, bg=ED_BG)
        bottom_frame.pack(fill=tk.X, padx=10, pady=(0,10))
        self.status_label = tk.Label(bottom_frame, text="Ready", fg=ED_ORANGE, bg=ED_BG, font=("Courier",12), anchor="w")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.paypal_btn = tk.Button(bottom_frame, text="Donate via PayPal", bg="#003087", fg="white", font=("Courier",10,"bold"), command=self.open_paypal)
        self.paypal_btn.pack(side=tk.RIGHT)
        
        self.refresh_route()
    
    def open_paypal(self):
        webbrowser.open("https://www.paypal.com/ncp/payment/9UKRVTWBH93V6")
    
    def refresh_route(self):
        try:
            threshold_hours = float(self.threshold_entry.get())
        except:
            threshold_hours = 24
        threshold = timedelta(hours=threshold_hours)
        cutoff = datetime.now() - threshold
        
        self.ax.cla()
        self.ax.set_facecolor('black')
        
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        
        if not self.systems:
            self.status_label.config(text="No systems monitored")
            return
        
        edmarket_status = "running" if is_edmarket_running() else "NOT running"
        if edmarket_status == "NOT running":
            messagebox.showwarning("EDMarketConnector", "EDMarketConnector.exe is NOT running. Some data may be outdated!")
        
        outdated = []
        current = []
        coords = {}
        self.status_label.config(text="Checking system update times...")
        self.root.update()
        
        for sys_name in self.systems:
            info_updated = None
            if sys_name in self.last_data:
                info_updated_str = self.last_data[sys_name].get("Info Updated", None)
                if info_updated_str:
                    try:
                        info_updated = datetime.strptime(info_updated_str.replace("am","").replace("pm","").strip(), "%d %b %Y, %H:%M")
                    except:
                        info_updated = None
            
            if not info_updated or (datetime.now() - info_updated) > timedelta(hours=2):
                info_update_str = get_inara_info_update(sys_name)
                if info_update_str:
                    self.last_data.setdefault(sys_name, {})["Info Updated"] = info_update_str
                    try:
                        info_updated = datetime.strptime(info_update_str.replace("am","").replace("pm","").strip(), "%d %b %Y, %H:%M")
                    except:
                        info_updated = datetime.now()
            
            if info_updated and info_updated < cutoff:
                outdated.append(sys_name)
            else:
                current.append(sys_name)
            
            # Coordinates
            c = get_system_coords(sys_name, self.coords_cache)
            if c:
                coords[sys_name] = c
            time.sleep(0.05)
        
        save_json(LAST_DATA_FILE, self.last_data)
        save_json(COORDS_CACHE_FILE, self.coords_cache)
        
        # --- Approx TSP route ---
        route = nearest_neighbor_tsp(coords, outdated) if len(outdated) >= 2 else outdated
        
        if route:
            x = [coords[s][0] for s in route]
            y = [coords[s][1] for s in route]
            z = [coords[s][2] for s in route]
            self.ax.plot(x, y, z, 'o-', color=ED_ORANGE, linewidth=3, markersize=8)
            for i, s in enumerate(route):
                self.ax.text(x[i], y[i], z[i], f" {i+1}", color='white', fontsize=12)
        else:
            self.ax.text(0,0,0,"No Outdated\nSystems!", color='white', fontsize=20, ha='center')
        
        self.canvas.draw()
        
        # Display system list
        current.sort()
        display_order = route + current
        for i, sys_name in enumerate(display_order):
            row = tk.Frame(self.scrollable_frame, bg=ED_BG)
            row.pack(fill=tk.X, pady=2)
            is_current = sys_name in current
            fg_color = ED_GREY if is_current else ED_ORANGE
            label_text = f"{'   ' if is_current else f'{i+1:2d}. '}{sys_name}"
            lbl = tk.Label(row, text=label_text, fg=fg_color, bg=ED_BG, font=("Courier",12), anchor="w")
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if not is_current:
                btn = tk.Button(row, text="Copy", bg=ED_ORANGE, fg="black", font=("Courier",10))
                btn.pack(side=tk.RIGHT)
                btn.config(command=lambda b=btn,s=sys_name:self.copy_to_clipboard(b,s))
        
        self.status_label.config(text=f"{len(outdated)} outdated | {len(current)} current | EDMarketConnector {edmarket_status}")
    
    def copy_to_clipboard(self, btn, system_name):
        self.root.clipboard_clear()
        self.root.clipboard_append(system_name)
        self.root.update()
        btn.config(bg=ED_GREY, text="Copied", fg=ED_WHITE, state="disabled")

if __name__ == "__main__":
    root = tk.Tk()
    app = RoutePlannerApp(root)
    root.mainloop()
