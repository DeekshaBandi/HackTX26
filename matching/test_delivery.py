"""
Full flow, start to finish: a caller shares their situation, gets matched,
gets connected by in-app chat, the AI posts the intro and leaves.

Run:
    python test_delivery.py
"""
from deliver import deliver
from match import _db, end_call, start_call, update_profile

CALL_ID = "test_delivery_001"

sid = start_call(CALL_ID, phone=None)
update_profile(sid, {"name": "Jordan", "school": "UT Dallas", "year": "sophomore",
                     "major": "computer science"}, call_id=CALL_ID)
result = update_profile(sid, {"situation": "my friends all transferred and I eat lunch alone every day",
                              "interests": "basketball, video games, cooking"}, call_id=CALL_ID)

best, p = result["best"], result["plan"]
print(f"Best match: {best['name']} ({best['school']}), score {best['score']:.3f}")
print(f"Plan: {p['decision']}\n")

out = deliver(sid, best["student_id"], best["score"], call_id=CALL_ID, method="in_app")
print(f"Delivered by: {out['method']}   chat: {out['chat_id']}")
if out["error"]:
    print("Note:", out["error"])

print("\nChat:")
for sender, body in _db().execute(
        "SELECT sender_id, body FROM chat_messages WHERE chat_id = %s ORDER BY id", (out["chat_id"],)).fetchall():
    print(f"   [{sender}] {body}")

end_call(sid, call_id=CALL_ID)

handoff = _db().execute(
    "SELECT event, value FROM call_events WHERE call_id = %s AND event = 'in_app'", (CALL_ID,)).fetchone()
print(f"\nTime to human logged: {handoff[1]:.1f} s" if handoff else "\n(no handoff logged)")

# Clean up everything this test created
_db().execute("DELETE FROM chat_messages WHERE chat_id = %s", (out["chat_id"],))
_db().execute("DELETE FROM chats WHERE chat_id = %s", (out["chat_id"],))
_db().execute("DELETE FROM matches WHERE match_id = %s", (out["match_id"],))
_db().execute("DELETE FROM call_events WHERE call_id = %s", (CALL_ID,))
_db().execute("DELETE FROM students WHERE student_id = %s", (sid,))
print("Cleaned up test data.")