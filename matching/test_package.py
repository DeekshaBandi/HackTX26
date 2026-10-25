"""
Replays Tisha's call from Emraan's spec, one Muse Spark package per turn,
then checks that a crisis package blocks matching.

Run:
    python test_package.py
"""
from match import _db, apply_package, end_call, start_call

CALL_ID = "test_package_001"


def pkg(learned, summary=None, crisis="none", conf=None):
    base = {k: None for k in ["name", "school", "year", "major", "email", "situation",
                              "themes", "stage", "wants", "ai_use", "interests", "timing"]}
    base.update(learned)
    return {"say": "...", "ready_for_next_phase": False, "learned": base,
            "confidence": conf or {}, "summary": summary, "crisis": crisis}


turns = [
    pkg({"name": "Tisha"}),
    pkg({"school": "UT Austin", "year": "freshman", "major": "biology"}),
    pkg({"situation": "Moved from Houston and hasn't made close friends yet.",
         "themes": ["homesick", "loneliness"], "ai_use": "daily, venting about her day"},
        summary="A freshman biology student. Moved from Houston and hasn't made close friends yet; "
                "talks to ChatGPT every night."),
    pkg({"stage": "months"}),
    pkg({"wants": "someone who's been through it", "interests": ["baking", "anime"], "timing": "now"}),
]

sid = start_call(CALL_ID)
for i, t in enumerate(turns, 1):
    r = apply_package(sid, t, call_id=CALL_ID)
    learned = {k: v for k, v in t["learned"].items() if v}
    print(f"Turn {i}: {learned}")
    if r["best"]:
        b = r["best"]
        print(f"   best: {b['score']:.3f}  {b['name']} ({b['school']}): {b['situation']}"
              f"   | embedder: {r['embedder']} | plan: {r['plan']['decision']}")
    print()

row = _db().execute("SELECT name, tags, wants, timing, summary FROM students WHERE student_id = %s",
                    (sid,)).fetchone()
print("Saved record:", dict(zip(["name", "tags", "wants", "timing", "summary"], row)), "\n")

# A package with crisis = urgent should stop all matching for this caller
r = apply_package(sid, pkg({}, crisis="urgent"), call_id=CALL_ID)
print(f"After crisis=urgent: crisis={r['crisis']}, matches returned: {len(r['matches'])} (should be 0)")

end_call(sid, call_id=CALL_ID)
_db().execute("DELETE FROM call_events WHERE call_id = %s", (CALL_ID,))
_db().execute("DELETE FROM students WHERE student_id = %s", (sid,))
print("Cleaned up test data.")