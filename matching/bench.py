"""
Measures match search speed two ways, for the judge site:
  - database time: how long Tiger itself takes (EXPLAIN ANALYZE)
  - round trip:    database time + network from this laptop to Tiger

    from bench import search_speed
    search_speed()  -> {"db_ms": 3.1, "round_trip_ms": 180.4, "students": 1000, "uses_index": True}

Run:
    python bench.py
"""
import json
import statistics
import time

from match import SEARCH_SQL, _db

RUNS = 20


def search_speed(runs=RUNS):
    db = _db()
    ids = [r[0] for r in db.execute(
        "SELECT student_id FROM students WHERE is_synthetic ORDER BY random() LIMIT %s", (runs,)
    ).fetchall()]
    count = db.execute("SELECT count(*) FROM students WHERE embedding IS NOT NULL").fetchone()[0]

    db_times, trip_times, uses_index = [], [], False
    for sid in ids:
        params = {"me": sid, "k": 5}

        plan = db.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + SEARCH_SQL, params).fetchone()[0]
        plan = plan if isinstance(plan, list) else json.loads(plan)
        db_times.append(plan[0]["Execution Time"])
        uses_index = uses_index or "hnsw" in json.dumps(plan)

        t0 = time.perf_counter()
        db.execute(SEARCH_SQL, params).fetchall()
        trip_times.append((time.perf_counter() - t0) * 1000)

    return {
        "students": count,
        "db_ms": round(statistics.median(db_times), 2),
        "round_trip_ms": round(statistics.median(trip_times), 1),
        "uses_index": uses_index,
    }


if __name__ == "__main__":
    r = search_speed()
    print(f"Searched {r['students']:,} students ({RUNS} runs, median):")
    print(f"   inside Tiger:  {r['db_ms']} ms")
    print(f"   round trip:    {r['round_trip_ms']} ms  (includes network from this laptop)")
    print(f"   HNSW index used: {r['uses_index']}")