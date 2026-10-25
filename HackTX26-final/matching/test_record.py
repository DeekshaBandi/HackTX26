"""
Runs your two real test calls (phone numbers replaced, transcripts removed) through matching,
exactly as the end-of-call record arrives, and prints who each caller would be matched with.

Run:
    python test_record.py
    python test_record.py path/to/another_call.json
"""
import json
import sys

from common import profile_text
from match import _db, ingest_call

files = sys.argv[1:] or ["samples/call_stressed_cs.json", "samples/call_maya_lonely.json"]

for path in files:
    rec = json.load(open(path))
    prof = dict(rec["profile"], summary=rec.get("summary"))
    print(f"=== {path}")
    print(f"situation: {rec['profile'].get('situation')}")
    print(f"text we embed: {profile_text(prof)}")

    r = ingest_call(rec)
    print(f"crisis: {r.get('crisis')}   embedder: {r.get('embedder')}   plan: {r['plan']['decision'] if r.get('plan') else None}")
    for m in r["matches"][:3]:
        print(f"   {m['score']:.3f}  {m['name']} ({m['school']}): {m['situation']}")
    print()

    # Clean up so test callers don't show up in real matches
    _db().execute("DELETE FROM call_events WHERE call_id = %s", (rec["call_id"],))
    _db().execute("DELETE FROM students WHERE student_id = %s", (r["student_id"],))

print("Cleaned up test data.")