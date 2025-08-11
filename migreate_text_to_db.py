import sqlite3
from pathlib import Path
import json
from datetime import datetime, timezone

TXT = Path("processed_ids.txt")
DB = Path("data.db")  # or whatever DB_FILE you set in .env

if not TXT.exists():
    print("No processed_ids.txt found")
    raise SystemExit(0)

conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS processed_activities (
    activity_id TEXT PRIMARY KEY,
    uploaded_at TEXT,
    gpx_id TEXT,
    status TEXT,
    metadata TEXT
)
""")
with TXT.open("r", encoding="utf-8") as f:
    for line in f:
        aid = line.strip()
        if not aid:
            continue
        cur.execute("INSERT OR IGNORE INTO processed_activities (activity_id, uploaded_at, status) VALUES (?, ?, ?)",
                    (aid, datetime.now(timezone.utc).isoformat(), "migrated"))
conn.commit()
conn.close()
print("Migration done")
