"""
Numbers for the judge site (Nathan can import these, or call them through an API later).

    from metrics import time_to_human_daily, calls_by_theme, compression_stats, forecast_board

Run directly to print everything:
    python metrics.py
"""

from common import tiger_db
from forecast import plan


def _db():
    return tiger_db()


def time_to_human_daily(days=14):
    """How long the AI talks before handing off to a human, per day. Shorter is better."""
    try:
        rows = _db().execute(
            """
            SELECT time_bucket('1 day', bucket) AS day,
                   sum(handoffs) AS handoffs,
                   approx_percentile(0.5, rollup(pct)) AS p50,
                   approx_percentile(0.9, rollup(pct)) AS p90
            FROM time_to_human_hourly
            WHERE bucket > now() - make_interval(days => %s)
            GROUP BY day ORDER BY day
            """, (days,)).fetchall()
        return [{"day": d.date().isoformat(), "handoffs": int(h),
                 "p50_seconds": round(p50), "p90_seconds": round(p90)} for d, h, p50, p90 in rows]
    except Exception:
        # No toolkit: fall back to a weighted average
        rows = _db().execute(
            """
            SELECT time_bucket('1 day', bucket) AS day, sum(handoffs),
                   sum(avg_seconds * handoffs) / NULLIF(sum(handoffs), 0)
            FROM time_to_human_hourly
            WHERE bucket > now() - make_interval(days => %s)
            GROUP BY day ORDER BY day
            """, (days,)).fetchall()
        return [{"day": d.date().isoformat(), "handoffs": int(h), "avg_seconds": round(a)}
                for d, h, a in rows]


def calls_by_theme(hours=24 * 7):
    rows = _db().execute(
        """
        SELECT theme, sum(calls) FROM calls_by_theme_hourly
        WHERE bucket > now() - make_interval(hours => %s)
        GROUP BY theme ORDER BY 2 DESC
        """, (hours,)).fetchall()
    return [{"theme": t, "calls": int(c)} for t, c in rows]


def compression_stats():
    for fn in ("hypertable_columnstore_stats", "hypertable_compression_stats"):
        try:
            before, after = _db().execute(
                f"SELECT before_compression_total_bytes, after_compression_total_bytes FROM {fn}('call_events')"
            ).fetchone()
            if before:
                return {"before_mb": round(before / 1e6, 2), "after_mb": round(after / 1e6, 2),
                        "saved_percent": round(100 * (1 - after / before), 1)}
        except Exception:
            continue
    return None


def forecast_board():
    """Current hold-or-go decision for every theme."""
    themes = [r[0] for r in _db().execute(
        "SELECT DISTINCT theme FROM calls_by_theme_hourly ORDER BY 1").fetchall()]
    return [plan(t, has_live_match=False) for t in themes]


if __name__ == "__main__":
    print("Time to human (last 14 days):")
    for r in time_to_human_daily():
        print("  ", r)

    print("\nBusiest themes (last 7 days):")
    for r in calls_by_theme()[:8]:
        print(f"   {r['calls']:6,}  {r['theme']}")

    print("\nCompression:", compression_stats())

    print("\nRight now, a caller in each theme would get:")
    for p in forecast_board():
        print(f"   {p['decision']:10} {p['probability']:5.0%}  ~{p['calls_per_hour']:5.1f}/hr  {p['theme']}")