"""
Resets two demo phones so the live merge can be rehearsed again.

Matching never pairs two students who were already matched, so after one merge the same
two phones would never find each other again. This deletes their matches (and the chats
those opened), clears any crisis flag, and marks every other real caller offline, since a
call that crashed can leave a stale "online" row that would take the partner's place.

Run (between rehearsals, with the phone server's calls hung up):
    python reset_demo.py +15125550100 +15125550101
"""
import sys

from match import _db


def sid(phone):
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"ph_{digits[-10:]}"


if len(sys.argv) < 3:
    sys.exit(__doc__)

ids = [sid(p) for p in sys.argv[1:]]
db = _db()
matches = [r[0] for r in db.execute(
    "SELECT match_id FROM matches WHERE student_a = ANY(%(ids)s) OR student_b = ANY(%(ids)s)",
    {"ids": ids}).fetchall()]
chats = [r[0] for r in db.execute(
    "SELECT chat_id FROM chats WHERE match_id = ANY(%s)", (matches,)).fetchall()]
db.execute("DELETE FROM chat_messages WHERE chat_id = ANY(%s)", (chats,))
db.execute("DELETE FROM chats WHERE chat_id = ANY(%s)", (chats,))
db.execute("DELETE FROM matches WHERE match_id = ANY(%s)", (matches,))
db.execute("UPDATE students SET safety_flag = FALSE, crisis = NULL WHERE student_id = ANY(%s)", (ids,))
offline = db.execute(
    "UPDATE students SET online = FALSE WHERE online AND NOT is_synthetic RETURNING student_id").fetchall()
print(f"{', '.join(ids)}: removed {len(matches)} matches and {len(chats)} chats; "
      f"marked {len(offline)} real callers offline")
