# Elite-Dangerous-Power-Play-Monitor   https://drive.google.com/drive/folders/105aa1y6HImFj31ZvD36WhrI6ABu_ebNb
Requires you to run the EDMC. This tool monitors the current power play score of the systems you enter. Its companion the EDPPM Route Planner will help you keep your info current. It sets up the most optimal route to jump through your systems with EDMC running to push new data. 

EDPPM v1.3 – Elite Dangerous PowerPlay Monitor

EDPPM (Elite Dangerous PowerPlay Monitor) is a desktop monitoring tool designed to track PowerPlay control strength, state changes, and faction data for selected star systems in Elite Dangerous.

Version 1.3 represents the last stable release that uses manual Inara scraping as the authoritative source for PowerPlay control strength and state, combined with EDSM for system and faction metadata.

🚀 Features

📊 PowerPlay Control Strength Tracking

Visual control bars (0–100%)

Weekly delta calculation

Positive (blue) / negative (yellow) trend coloring

🏴 Power State Monitoring

Expansion

Exploited

Fortified

Stronghold

Contested

Uncontrolled

🧭 System List Management

Add / remove systems manually

Persistent storage between runs

Sorted by state priority and strength

🕒 Update Timestamp Awareness

Displays last known update time

Weekly baseline resets automatically every Thursday @ 03:00

🧑‍🤝‍🧑 Faction Data (EDSM)

Controlling faction

Allegiance, government, security

Population

Influence breakdown per system

🔄 Manual & Automatic Refresh

Manual “Refresh Now” button

Hourly auto-refresh at :20

🖥️ Elite-themed UI

Black/orange ED-style color scheme

Large readable Courier font

Always-on-top window option

📦 Data Sources (v1.3)
Source	Used For
Inara.cz	PowerPlay State, Control Strength, Update Time
EDSM.net	System metadata & faction data
Local JSON Files	Persistence, deltas, weekly baseline

⚠️ Important:
Inara scraping is manual and rate-limited. Excessive refreshes may result in temporary IP blocking.

📌 Usage Notes

PowerPlay data only updates when Refresh Now is clicked

Weekly deltas reset automatically after weekly PowerPlay tick

Systems are sorted by strategic priority

Blue bars indicate positive weekly movement

⚠️ Limitations (v1.3)

Inara scraping is fragile and subject to site layout changes

No EDDN live-stream integration in this version

No route planning or TSP optimization

No background PowerPlay data ingestion

These are addressed in later experimental versions.

📜 License

No License – All Rights Reserved

This code is proprietary.
You may not copy, redistribute, modify, or reuse this code without explicit permission from the author.

🧠 Author Notes

EDPPM v1.3 is considered the baseline reference implementation for PowerPlay monitoring.
Future versions may integrate EDSM + EDDN pipelines, with Inara used strictly for manual baseline verification.
