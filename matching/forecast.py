"""
Hold-or-go: after a match search, decide what to do with the caller.

    merge_now   someone similar is on a call right now -> merge the calls
    hold        nobody yet, but someone similar is fairly likely to call in the next 90s -> AI stays on
    group_text  unlikely anyone calls soon -> save the profile, connect them by text later

Uses the calls_by_theme_hourly continuous aggregate: how many calls this theme
usually gets at this hour on this day of the week.

    from forecast import plan
    p = plan(theme="going through a breakup", has_live_match=False)
    p["decision"], p["say"], p["probability"], p["expected_wait_seconds"]
"""
import math
from datetime import datetime, timezone

from common import tiger_db

HOLD_WINDOW_SECONDS = 90     # longest we keep a caller waiting on the line
HOLD_MIN_PROBABILITY = 0.25  # hold only if at least this likely someone calls in that window

SAY = {
    "merge_now": "There's someone on the line right now going through something really similar. "
                 "Want me to connect you two?",
    "hold": "People going through this usually call around now, so I'm going to stay on with you "
            "for a minute and see if someone joins.",
    "group_text": "Nobody going through this is on the line right now, so I'll text you and someone "
                  "who gets it as soon as we find them.",
}


def _db():
    return tiger_db()


def calls_per_hour(theme, at=None):
    """Typical calls for this theme at this hour + weekday (US Central), averaged per week of history."""
    at = at or datetime.now(timezone.utc)
    row = _db().execute(
        """
        WITH span AS (
            SELECT GREATEST(1, CEIL(EXTRACT(epoch FROM max(bucket) - min(bucket)) / 604800.0)) AS weeks
            FROM calls_by_theme_hourly
        ),
        t AS (SELECT %(at)s::timestamptz AT TIME ZONE 'America/Chicago' AS local_at)
        SELECT COALESCE(SUM(c.calls), 0)::float / (SELECT weeks FROM span)
        FROM calls_by_theme_hourly c, t
        WHERE c.theme = %(theme)s
          AND EXTRACT(dow  FROM c.bucket AT TIME ZONE 'America/Chicago') = EXTRACT(dow  FROM t.local_at)
          AND EXTRACT(hour FROM c.bucket AT TIME ZONE 'America/Chicago') = EXTRACT(hour FROM t.local_at)
        """,
        {"theme": theme, "at": at},
    ).fetchone()
    return float(row[0] or 0)


def plan(theme, has_live_match, at=None, demand_multiplier=1.0):
    """
    demand_multiplier: optional boost from Google Trends (SerpApi), e.g. 1.3 during finals week.
    at: pretend it's a different time (handy for the demo: "what would happen at 11pm?").
    """
    if has_live_match:
        return {"decision": "merge_now", "say": SAY["merge_now"], "probability": 1.0,
                "expected_wait_seconds": 0, "calls_per_hour": None, "theme": theme}

    rate = calls_per_hour(theme, at) * demand_multiplier if theme else 0.0
    per_second = rate / 3600
    # Chance at least one similar caller shows up during the hold window (Poisson arrivals)
    probability = 1 - math.exp(-per_second * HOLD_WINDOW_SECONDS)
    expected_wait = (1 / per_second) if per_second > 0 else None

    decision = "hold" if probability >= HOLD_MIN_PROBABILITY else "group_text"
    return {
        "decision": decision,
        "say": SAY[decision],
        "probability": round(probability, 3),
        "expected_wait_seconds": round(expected_wait) if expected_wait else None,
        "hold_seconds": HOLD_WINDOW_SECONDS if decision == "hold" else 0,
        "calls_per_hour": round(rate, 2),
        "theme": theme,
    }


if __name__ == "__main__":
    # Quick look: same caller at 6am vs 11pm (Central). Shows the forecast reacting to time of day.
    from zoneinfo import ZoneInfo
    central = datetime.now(ZoneInfo("America/Chicago"))
    six_am = central.replace(hour=6, minute=0)
    eleven_pm = central.replace(hour=23, minute=0)
    themes = [r[0] for r in _db().execute(
        "SELECT DISTINCT theme FROM calls_by_theme_hourly ORDER BY 1 LIMIT 8").fetchall()]
    for th in themes:
        early, late = plan(th, False, at=six_am), plan(th, False, at=eleven_pm)
        print(f"{th[:55]:55}  6am: {early['decision']:10} ({early['probability']:.0%})"
              f"   11pm: {late['decision']:10} ({late['probability']:.0%})")