"""
Delivery: connect two matched students, post the intro, then the AI leaves.

    from deliver import deliver
    deliver(student_a, student_b, score, call_id="CA123", method="in_app")

Methods:
    in_app      (default) create a chat in Tiger, AI posts the intro, then leaves the chat.
                Person 4's student site shows it (through api.py).
    sms         same in-app chat, plus a text to each person with the intro.
                Needs a Twilio number that's allowed to text (A2P 10DLC / toll-free verified).
    call_merge  both people are on calls right now: each hears the intro, then both calls
                are moved into one private conference room. Replacing each call's
                instructions cuts off the AI's audio stream, so the AI is gone.
                call_id / other_call_id must be Twilio Call SIDs (they start with "CA").
                If room is given, the phone server (merge.py) already merged the calls,
                so this only records the match and opens the chat.

intro_text: Person 2's AI writes the real intro. If none is passed, a simple template is used.

Twilio settings in .env (only needed for sms / call_merge):
    TWILIO_ACCOUNT_SID=AC...
    TWILIO_AUTH_TOKEN=...
    TWILIO_FROM_NUMBER=+1...
    STUDENT_SITE_URL=https://...      (optional, link added to texts)
"""
import os
import uuid
from xml.sax.saxutils import escape

from match import _db, record_match

AI_ID = "aiedh"


def _student(sid):
    cols = ["student_id", "name", "school", "major", "year", "situation", "stage", "interests", "phone"]
    row = _db().execute(f"SELECT {', '.join(cols)} FROM students WHERE student_id = %s", (sid,)).fetchone()
    if not row:
        raise ValueError(f"No student {sid}")
    return dict(zip(cols, row))


def _shared_interests(a, b):
    def split(s):
        return {x.strip().lower() for x in (s or "").split(",") if x.strip()}
    return sorted(split(a["interests"]) & split(b["interests"]))


def default_intro(a, b):
    """Fallback intro if Person 2 doesn't send one. Plain and short on purpose."""
    name_a, name_b = a["name"] or "there", b["name"] or "there"
    lines = [f"Hi {name_a} and {name_b}! I'm AIEDH, the line you both called."]
    if a["situation"] and b["situation"]:
        lines.append(f"{name_a} said: \"{a['situation']}\". {name_b} said: \"{b['situation']}\".")
        lines.append("You're going through something really similar, so I thought you should meet.")
    shared = _shared_interests(a, b)
    if shared:
        lines.append(f"You're both into {', '.join(shared)}, too.")
    lines.append("I'm stepping out now so you two can talk. Take care of each other.")
    return " ".join(lines)


def _post(chat_id, sender, body):
    _db().execute(
        "INSERT INTO chat_messages (chat_id, sender_id, body) VALUES (%s, %s, %s)",
        (chat_id, sender, body),
    )


def open_chat(match_id, a_id, b_id, intro):
    """Creates the chat, AI posts the intro, then the AI leaves."""
    chat_id = f"chat_{uuid.uuid4().hex[:10]}"
    _db().execute(
        "INSERT INTO chats (chat_id, match_id, student_a, student_b) VALUES (%s, %s, %s, %s)",
        (chat_id, match_id, a_id, b_id),
    )
    _post(chat_id, AI_ID, intro)
    _post(chat_id, "system", "AIEDH left the chat.")
    _db().execute("UPDATE chats SET status = 'ai_left' WHERE chat_id = %s", (chat_id,))
    return chat_id


def _twilio():
    from twilio.rest import Client
    return Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])


def send_sms(people, intro):
    """Texts the intro to each person who has a phone number. Returns the Twilio message SIDs."""
    link = os.environ.get("STUDENT_SITE_URL")
    body = intro + (f"\n\nChat here: {link}" if link else "")
    client, sent = _twilio(), []
    for p in people:
        if p.get("phone"):
            msg = client.messages.create(to=p["phone"], from_=os.environ["TWILIO_FROM_NUMBER"], body=body)
            sent.append(msg.sid)
    return sent


def merge_calls(call_sid_a, call_sid_b, intro):
    """Moves both live calls into one private conference room. The AI's stream ends here."""
    room = f"aiedh_{uuid.uuid4().hex[:8]}"
    twiml = (
        "<Response>"
        f"<Say>{escape(intro)}</Say>"
        f"<Dial><Conference startConferenceOnEnter=\"true\" endConferenceOnExit=\"true\">{room}</Conference></Dial>"
        "</Response>"
    )
    client = _twilio()
    for sid in (call_sid_a, call_sid_b):
        client.calls(sid).update(twiml=twiml)
    return room


def deliver(student_a, student_b, score, call_id=None, method="in_app",
            intro_text=None, other_call_id=None, room=None):
    """
    Connect two students. Always creates the in-app chat (so there's a record and a
    place to keep talking), then adds SMS or a call merge on top if asked.
    Falls back to in-app only if SMS or the merge fails.
    """
    a, b = _student(student_a), _student(student_b)
    intro = intro_text or default_intro(a, b)

    result = {"method": method, "intro": intro, "chat_id": None, "sms_sids": [], "room": None, "error": None}
    delivered = method
    try:
        if method == "sms":
            result["sms_sids"] = send_sms([a, b], intro)
        elif method == "call_merge" and room:
            result["room"] = room
        elif method == "call_merge":
            if not (call_id and other_call_id):
                raise ValueError("call_merge needs both Twilio Call SIDs (call_id and other_call_id)")
            result["room"] = merge_calls(call_id, other_call_id, intro)
    except Exception as e:
        result["error"] = f"{method} failed, used in-app chat instead: {str(e).splitlines()[0]}"
        delivered = "in_app"

    match_id = record_match(student_a, student_b, score, delivered, intro_text=intro, call_id=call_id)
    result["match_id"] = match_id
    result["chat_id"] = open_chat(match_id, student_a, student_b, intro)
    result["method"] = delivered
    return result


# ---------- demo: fake students reply in the chat (Gemini) ----------

DEMO_REPLY_PROMPT = """You are {name}, a {year} {major} student at {school}. You're texting another college
student you were just connected with on AIEDH, a peer support line. You're both going through
something similar. What you're going through: "{situation}" ({stage}). Your interests: {interests}.

Reply to their latest message like a real college student texting: casual, warm, 1 to 3 short
sentences, lowercase is fine, no emojis overload, no advice lists, no therapy talk. Ask a question
back sometimes. Never say you're an AI. Keep it light and everyday, nothing graphic.

Chat so far:
{history}

Your reply:"""


def demo_reply(chat_id, partner_id):
    """
    The 1,000 fake students can't really answer, so in the demo Gemini replies as them.
    Only ever used for synthetic students (the app labels them "demo student").
    """
    import os
    from google import genai

    p = _student(partner_id)
    rows = _db().execute(
        "SELECT sender_id, body FROM chat_messages WHERE chat_id = %s AND sender_id <> 'system' "
        "ORDER BY id DESC LIMIT 12", (chat_id,)).fetchall()[::-1]
    names = {AI_ID: "AIEDH", partner_id: p["name"] or "me"}
    history = "\n".join(f"{names.get(s, 'them')}: {b}" for s, b in rows)

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
    resp = client.models.generate_content(model=model, contents=DEMO_REPLY_PROMPT.format(
        name=p["name"], year=p["year"], major=p["major"], school=p["school"],
        situation=p["situation"], stage=p["stage"], interests=p["interests"], history=history))
    text = (resp.text or "").strip().strip('"')
    if text:
        _post(chat_id, partner_id, text)