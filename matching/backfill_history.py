"""
Creates 2 weeks of SYNTHETIC call history so the forecast and charts have data.
Every row is tagged detail = {"synthetic": true}. Say so in the demo.

Pattern: national-scale volume, busiest late at night (10pm-1am Central),
quietest early morning, a bump on Sunday nights. Some themes are much more
common than others. AI talk-time before handoff slowly improves over the 2 weeks.

Run (after tiger_analytics_setup.py):
    python backfill_history.py

Tip: retention deletes raw events older than 24h about once a day. The hourly
totals survive, but rerun this the morning of judging if you want the raw
history (and the compression numbers) to be there too.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from common import tiger_connect

DAYS = 14
PEAK_CALLS_PER_HOUR = 300   # nationwide, all themes, at the busiest hour
HANDOFF_RATE = 0.75         # share of calls that end with a human connection
CENTRAL = ZoneInfo("America/Chicago")

# How busy each local hour is, relative to the peak (index = hour 0-23)
HOUR_SHAPE = [0.9, 0.7, 0.5, 0.3, 0.15, 0.1, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35,
              0.4, 0.4, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.95, 1.0, 1.0]
# Monday..Sunday
DAY_SHAPE = [1.0, 1.0, 1.0, 0.95, 0.8, 0.75, 1.15]

rng = np.random.default_rng(7)

themes = sorted({s["theme"] for s in json.load(open("students.json"))})
weights = rng.uniform(0.3, 2.0, len(themes))
weights = weights / weights.mean()          # average theme weight = 1
per_theme_peak = PEAK_CALLS_PER_HOUR / len(themes)

now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
start = now - timedelta(days=DAYS)
detail = json.dumps({"synthetic": True})

rows = []
hour = start
while hour < now:
    local = hour.astimezone(CENTRAL)
    shape = HOUR_SHAPE[local.hour] * DAY_SHAPE[local.weekday()]
    progress = (hour - start) / (now - start)          # 0 -> 1 over the 2 weeks
    median_talk = 240 - 120 * progress                 # 4 min -> 2 min

    for theme, w in zip(themes, weights):
        for _ in range(rng.poisson(per_theme_peak * w * shape)):
            t = hour + timedelta(seconds=float(rng.uniform(0, 3600)))
            call_id = f"hist_{uuid.uuid4().hex[:10]}"
            if rng.random() < HANDOFF_RATE:
                talk = float(rng.lognormal(np.log(median_talk), 0.4))
                method = rng.choice(["group_text", "call_merge", "in_app"], p=[0.6, 0.15, 0.25])
                rows.append((t + timedelta(seconds=talk), call_id, None, method, theme, detail, talk))
                end = t + timedelta(seconds=talk + 30)
            else:
                end = t + timedelta(seconds=float(rng.uniform(60, 400)))
            rows.append((end, call_id, None, "call_ended", theme, detail, None))
    hour += timedelta(hours=1)

print(f"Generated {len(rows):,} synthetic events over {DAYS} days.")

conn = tiger_connect()
print("Removing old synthetic history...")
conn.execute("DELETE FROM call_events WHERE detail->>'synthetic' = 'true'")

print("Uploading (bulk COPY)...")
with conn.cursor() as cur:
    with cur.copy(
        "COPY call_events (time, call_id, student_id, event, theme, detail, value) FROM STDIN"
    ) as copy:
        for r in rows:
            copy.write_row(r)

print("Refreshing the hourly aggregates...")
for view in ("calls_by_theme_hourly", "time_to_human_hourly"):
    conn.execute(f"CALL refresh_continuous_aggregate('{view}', %s, %s)", (start - timedelta(hours=1), now))

print("Compressing older chunks so you can see the savings now...")
conn.execute(
    "SELECT compress_chunk(c, if_not_compressed => TRUE) "
    "FROM show_chunks('call_events', older_than => INTERVAL '2 hours') c"
)
print("Done. Run `python metrics.py` to see the results.")