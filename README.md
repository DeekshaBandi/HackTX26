# Aide: AI phone operator for AIEDH

A student calls the Twilio number. Aide, an AI operator, answers, has a short conversation to understand what they're going through, and fills in a structured profile as it goes. The live conversation and profile show up at http://localhost:8000, and each call is saved as JSON for matching the caller with another student.

```
caller ─▶ Twilio ──POST /twilio/voice──▶ server.py ──TwiML: <Connect><Stream>, <Hangup/>
       ◀─▶ Twilio ◀─WS /twilio-audio (8 kHz mu-law, both ways)─▶ server.py
                caller audio ──16 kHz PCM──▶ Muse Voice Transcribe ──turns──▶ call_session.py
                call_session.py ──turn goal──▶ Muse Spark (ai_operator/) ──"say" text──▶ ElevenLabs Flash
                ElevenLabs ──mu-law audio──▶ server.py ──media + marks──▶ Twilio ──▶ caller
browser ◀──WS /dashboard (transcripts, operator lines, live record)── server.py
records/{call_id}.json ◀── written when the call ends
server.py ──/call/start, /call/turn, /call/record (matching_client.py)──▶ matching/api.py :8001
```

## Running everything (phone server + matching + judge site)
Three processes. The matching API needs `matching/.env` and `matching/rsa_key.p8` (from Deeksha, never committed) and a phone hotspot, since venue Wi-Fi blocks the database port.
```bash
.venv/bin/python server.py                                    # phone server, :8000 (ngrok points here)
cd matching && .venv/bin/python -m uvicorn api:app --host 0.0.0.0 --port 8001   # matching API, :8001
npm install && npm run dev                                    # judge site, http://localhost:5173
```
For the matching API's packages, once: `cd matching && python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn scikit-learn snowflake-connector-python python-dotenv sentence-transformers "psycopg[binary]" pgvector google-genai twilio`.

On the judge site, the top left is the Snowflake embedding map from the matching API (the caller's dot moves as their profile fills in), and the bottom left is the live transcript from the phone server. The phone app is at http://localhost:8001/app/ and the API docs at http://localhost:8001/docs. Each call is reported to the matching API as `ph_` + the caller number's last 10 digits; set `MATCHING_API_URL=` (empty) in `.env` to turn that off.

## Files
| File | What it does |
|---|---|
| `server.py` | FastAPI app: Twilio webhook and media stream, audio conversion, dashboard broadcast, call records, returning callers. On startup it builds the fixed-line audio cache and points the Twilio number's voice webhook at `PUBLIC_URL/twilio/voice`. |
| `call_session.py` | One call's timing and audio: turn-taking, fillers, slow-model handling, barge-in, silence reminders, crisis lines, hangup, latency log. |
| `ai_operator/` | The conversation itself, with no audio code (so `chat_test.py` runs the same logic). `conversation.py` per-call turn logic, `planner.py` phases and turn goals, `prompts.py` system prompt and fixed lines, `brain.py` the Spark reply engine, `safety.py` crisis tripwire and yes/no readers, `state.py` the call record. |
| `tts.py` | ElevenLabs Flash over one warm multi-context WebSocket per call; fixed-line cache in `tts_cache/`. |
| `matching_client.py` | Reports each call to the matching API in the background: `/call/start` on connect, `/call/turn` whenever the profile changes (newest only), `/call/record` at the end (without the transcript). Never holds up the call. |
| `muse_client.py` | `MuseStream`: one Muse realtime session (handshake, audio sender, turn events, endStream, close codes). |
| `dashboard.html` | Dashboard (served at `/` by server.py): "You:" / "Operator:" lines, profile panel, Download JSON. Reconnects automatically. |
| `chat_test.py` | Talk to Aide by typing (no phone, no audio). `--selftest` runs offline checks. `--case all` runs the scripted cases (both real test calls plus talkative, guarded, and failed-exam callers) against the real model and prints turn counts, moves, repeated openings, and checks. |
| `call_test.py` | `--selftest`: call_session.py against fake Twilio/Muse/TTS/model. `--live`: real Spark + ElevenLabs with a simulated caller. |
| `server_test.py` | `--selftest`: the dashboard token rules, against the real app with no outside calls. |
| `probe_spark.py` | Measures Spark (or any OpenAI-compatible server with `--endpoint`) time to first token. |
| `test_muse_mic.py` | Sends your laptop mic straight to Muse, as a sanity check for the key and the Muse API. |

## Setup (once)
```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in the values, including DASHBOARD_TOKEN
```
ngrok must forward `PUBLIC_URL` to `http://localhost:8000`.

## Run
```bash
.venv/bin/python chat_test.py --selftest && .venv/bin/python tts.py --selftest \
  && .venv/bin/python call_test.py --selftest && .venv/bin/python server_test.py --selftest
.venv/bin/python server.py          # open the "Dashboard:" link it prints, then call the number
```
Logs also go to `logs/aide.log` (rotating, gitignored), so a call can be checked afterwards. Set `LOG_LEVEL=DEBUG` to also log every partial transcript and the TwiML.

## How a call goes
1. Aide plays a cached opening (it's an AI, not a therapist or emergency line) and asks for a first name. A returning caller (same number, saved name) gets "Welcome back" and skips the identity questions.
2. Phases, decided in code: IDENTITY (name, school, then year and major in one question) → CONTEXT ("What made you call tonight?") → DEEPEN (at most 4 questions) → REFLECT (summary back, confirmed) → TIMING (now / later) → EMAIL (asked once, cached line, spelled back and confirmed) → CLOSING. If the caller opens up during IDENTITY, Aide goes straight to the conversation and asks any missing name / school / year / major just before the email.
   - REFLECT comes as soon as Aide knows what they're going through (with at least one theme) and what they want from another student (`wants` or `partner_traits`), both at confidence ≥ 0.6, or when DEEPEN's 4 questions are used up. If AI chatbot use is still unknown by then, one turn goes to "What do you usually end up talking to the chatbot about?" first. How long and interests are filled only when the caller mentions them.
   - In CONTEXT/DEEPEN, code picks a *move* each turn (`planner.choose_move`) from the record and a quick read of the caller's latest line: FOLLOW_THREAD (rich answer: pick up their words), OFFER_CHOICES (vague answer: 2-3 options; never twice in a row), CHATBOT_DOORWAY ("What was the last thing you talked to a chatbot about?", at most once), THEME_PROBE (a follow-up from the theme bank in `prompts.py`), ASK_WANTS, LET_THEM_TALK (long emotional answer that trails off), ASK_AI_USE, WRAP (go to REFLECT). No move repeats back to back except FOLLOW_THREAD. Each goal lets the model wrap up early if the latest line already covers what's missing; it reports that in the JSON's `move`.
   - Aide sees its last 3 replies, so it doesn't open the same way twice, and at most one reply in three opens with "thanks" / "got it". If the first sentence of a reply describes what Aide is about to say ("I'll ask what...") instead of saying it, the engine drops it before anything is spoken and asks again.
3. Each caller turn: one Spark call returns JSON with the spoken line (`say`) first, streamed straight into TTS, then what it learned. The record only changes through validated merges.
4. Crisis: a keyword tripwire or the model's "concern" makes the next question a calm, direct safety check. A yes (anything short of a clear no), or the model judging it urgent, switches to CRISIS: fixed lines only (call or text nine eight eight, or nine one one in immediate danger), no model calls, no call transfer. Crisis lines can't be interrupted. After 30 s of silence Aide repeats 988 and ends the call.
5. Silence: 12 s after Aide stops talking, "Take your time"; 12 s later, "... Are you still there?" (a "yeah" to it counts as an answer); 15 s later, the closing and hangup.

## Timing
All measured from Muse's `speechEnd` for the caller's turn:
- **0.7 s** with no reply text yet: a short cached filler ("Mm, okay."), never the same one twice in a row.
- **2.5 s**: a second identical Spark request in parallel (hedge); whichever speaks first wins.
- **6 s**: both cancelled, "Sorry, you cut out for a second" plays, and any late result is dropped.

The Spark call starts on `speechEnd` with the latest partial and is kept if the final transcript says the same thing. Short or trailing turns ("um so") wait until 0.7 s after speech end for the caller to continue. Muse sends `speechEnd` about 0.3-0.7 s after the caller actually goes quiet.

Barge-in: when the caller really talks over Aide, the audio stops. During the opening that takes 3 words, not counting so/um/uh/like/and/okay (background chatter like "So one day" doesn't cut it); elsewhere 2 words that aren't all backchannel, or one real word with ~500 ms of speech. Shorter speech over Aide is answered once Aide's line ends (or ignored, during the opening). If the opening is cut before its question, Aide re-introduces itself and asks the name; a question only counts as asked once the caller heard it. When the audio stops (`clear`), and only the part they heard goes in the transcript, from the marks Twilio echoes back. The interrupted turn's Spark call still finishes, and its analysis merges in turn order: it fills fields, but never overwrites one a newer turn already set.

Backchannels ("yeah", "mm-hmm") said over Aide never stop the audio. One counts as the caller's answer only if Aide's line ends in a yes/no question (like the email spell-back or "did I get that right?") and the caller said it during that final question; otherwise it's ignored and Aide waits for the real answer.

## What a healthy call looks like in the logs
```
Webhook hit: incoming call from +1...           # Twilio reached us (if missing: ngrok / webhook)
Twilio stream connected / stream started        # <Stream> WebSocket works
TTS socket connected in N ms
Muse handshake accepted, session=...            # key + handshake OK
Muse speechStart / speechEnd / FINAL turn=N: ...
turn N latency: first sound 701 ms (filler_3), reply audio 1327 ms, first say 1185 ms, ..., move=FOLLOW_THREAD
turn N: HEDGE ... / TIMEOUT ...                 # warnings when Spark is slow
barge-in on uN (...) after 40% of it played
Aide is ending the call
Twilio media WebSocket closed by Aide (call ending)
latency over N turns: first sound median ..., reply audio median ...; hedges 0, timeouts 0, barge-ins 1
Record saved: records/CA....json
Muse session closed: session=... code=1000      # 1008 = pacing/bad request, 1011 = server, 1013 = rate limit
```

## Dashboard access
Set `DASHBOARD_TOKEN` in `.env`. The dashboard page, its WebSocket, and record downloads then need `?token=<DASHBOARD_TOKEN>`; the server prints the full links (local and `PUBLIC_URL`) at startup, and the page passes the token on to its WebSocket and Download JSON links. `/twilio/voice` and `/twilio-audio` stay open for Twilio. Without a token the server logs a loud warning, and anyone with the ngrok URL can read every call.

## Dashboard events (`ws://localhost:8000/dashboard?token=...`)
These shapes are a contract that teammates build on.
```json
{"type": "call_started",    "call_id": "CA...", "from_number": "+1...", "ts": 1730000000.0}
{"type": "muse_transcript", "call_id": "CA...", "turn_id": 0, "text": "...", "final": false, "ts": ...}
{"type": "operator_text",   "call_id": "CA...", "turn_id": "a3", "text": "...", "final": false, "ts": ...}
{"type": "call_record",     "call_id": "CA...", "record": {...}, "ts": ...}
{"type": "call_ended",      "call_id": "CA...", "ts": ...}
```
On connect, a page first gets a snapshot of every call seen since the server started, then the latest `operator_text` for each operator line and the latest `call_record` for each call:
```json
{"type": "snapshot", "ts": ..., "calls": [{"call_id", "from_number", "started_ts", "ended_ts", "active",
                                            "turns": [{"turn_id", "text", "final", "ts"}]}]}
```
- `muse_transcript`: `turn_id` comes from Muse and is unique within a call. Partials are cumulative, so each one replaces the previous one; the `final: true` event replaces the partials.
- `operator_text`: Aide's line as it streams (`final: false`, cumulative), then once with `final: true` and the text the caller actually heard (shorter after a barge-in; empty if none of it played). `turn_id` is `"a1"`, `"a2"`, ... per call.
- `call_record`: the record below without `transcript`, plus the current `phase`.

## Call record (`GET /calls/{call_id}/record.json?token=...`, saved to `records/{call_id}.json`)
```json
{"call_id": "CA...", "caller_phone": "+1...", "started_at": "2026-10-24T23:10:05-05:00", "ended_at": "...",
 "phase_reached": "TIMING",
 "profile": {"name", "school", "year", "major", "email", "situation", "themes": [...], "stage", "wants",
             "partner_traits": ["high achiever", "good listener", ...], "ai_use", "interests": [...], "timing"},
 "confidence": {"situation": 0.8, "themes": 0.7, "stage": 0.9, "wants": 0.6},
 "summary": "Two to four sentences, no name/school/email.",
 "crisis": {"level": "none|concern|urgent", "reason": ""},
 "ready_to_match": true,
 "transcript": [{"speaker": "operator|caller", "text": "...", "t": "..."}]}
```
`records/` holds personal data and is gitignored.
