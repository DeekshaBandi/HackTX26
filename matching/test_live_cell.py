"""
Pretends to be Person 2: sends a caller's profile one field at a time,
the way it will arrive during a real call, and prints the best match after each step.

Run:
    python test_live_call.py
"""
from match import _db, end_call, start_call, update_profile

CALL_ID = "test_call_001"

# The order Person 2's AI might learn things in
steps = [
    {"name": "Jordan"},
    {"school": "UT Dallas"},
    {"year": "sophomore", "major": "computer science"},
    {"situation": "my friends all transferred and I eat lunch alone every day"},
    {"stage": "it's been like two months"},
    {"interests": "basketball, video games, cooking"},
]

sid = start_call(CALL_ID, phone="+10000000000")
print(f"Call started, caller id = {sid}\n")

for fields in steps:
    result = update_profile(sid, fields, call_id=CALL_ID)
    print(f"Got {fields}")
    if not result["best"]:
        print("   (no situation yet, waiting to match)\n")
        continue
    b = result["best"]
    print(f"   re-embedded: {result['embedded']}  (embedder: {result['embedder']}"
          + (f", took {result['embed_ms']} ms)" if result['embed_ms'] else ")"))
    print(f"   best match: {b['score']:.3f}  {b['name']} ({b['school']}): {b['situation']} [{b['stage']}]")
    print(f"   live match on a call right now: {result['live_match'] is not None}")
    p = result["plan"]
    print(f"   match search took: {result['query_ms']} ms")
    print(f"   plan: {p['decision']} (chance someone similar calls in 90s: {p['probability']:.0%})")
    print(f"   AI says: \"{p['say']}\"\n")

end_call(sid, call_id=CALL_ID)

events = _db().execute(
    "SELECT time, event, detail FROM call_events WHERE call_id = %s ORDER BY time", (CALL_ID,)
).fetchall()
print("Events logged to Tiger:")
for t, event, detail in events:
    print(f"   {t:%H:%M:%S}  {event}  {detail}")

# Clean up the test caller so it doesn't show up in real matches
_db().execute("DELETE FROM students WHERE student_id = %s", (sid,))
_db().execute("DELETE FROM call_events WHERE call_id = %s", (CALL_ID,))
print("\nCleaned up test caller.")