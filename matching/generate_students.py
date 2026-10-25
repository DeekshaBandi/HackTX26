"""
Generates ~1,000 fictional US college students with Gemma and saves them to students.json.

Setup (once):
    python -m pip install google-genai
    Add to .env:
        GEMINI_API_KEY=your_key
        GEMMA_MODEL=gemma-4-26b-a4b-it

Run:
    python generate_students.py

Safe to stop (Ctrl+C) and rerun: it picks up where it left off.
"""
import json
import math
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")

TOTAL = 1000
BATCH = 20      # students per request
WORKERS = 5     # requests running at the same time
OUT = "students.json"

random.seed(42)

SCHOOLS = [
    "UT Austin", "UT Dallas", "Texas A&M", "Rice University", "University of Houston",
    "UCLA", "UC Berkeley", "San Diego State", "Stanford", "USC",
    "University of Washington", "Oregon State", "Arizona State", "University of Colorado Boulder",
    "University of Utah", "University of Michigan", "Ohio State", "Purdue",
    "University of Illinois Urbana-Champaign", "University of Wisconsin-Madison",
    "University of Minnesota", "Northwestern", "NYU", "Columbia", "Rutgers",
    "Penn State", "Boston University", "Northeastern", "MIT", "Georgia Tech",
    "University of Florida", "Florida State", "UNC Chapel Hill", "Duke",
    "Virginia Tech", "University of Maryland", "Howard University", "Spelman College",
    "Morehouse College", "University of Georgia", "Vanderbilt", "Tulane",
    "LSU", "University of Kansas", "University of Iowa", "Indiana University",
    "University of Pittsburgh", "Temple University", "CUNY Hunter College", "University of Hawaii",
]

YEARS = ["freshman", "sophomore", "junior", "senior", "grad student"]

THEMES = [
    "switching majors and worried about telling family",
    "first-generation college student feeling like they don't belong",
    "transfer student who hasn't made friends yet",
    "international student who is homesick",
    "going through a breakup",
    "roommate conflict and dreading going home to the dorm",
    "burned out from classes and work",
    "imposter syndrome in a hard major",
    "rejected from every internship they applied to",
    "long-distance relationship that is struggling",
    "commuter student who never feels part of campus life",
    "anxious about graduating and what comes next",
    "failing a class for the first time",
    "stressed about money and working too many hours",
    "former athlete who stopped playing and lost their identity",
    "friend group drifted apart",
    "only person from their hometown at this school",
    "parents going through a divorce",
    "returning after taking a semester off",
    "studying for the MCAT or LSAT and feeling isolated",
    "grad student who feels alone in the lab",
    "moved off campus and lost touch with everyone",
    "spends most nights talking to a chatbot instead of people",
    "shy and finds it hard to start conversations",
    "lost a grandparent recently and far from family",
]

PROMPT = """You are generating realistic but FICTIONAL US college student profiles to test a peer-support matching app.

Write {n} different students who are all dealing with this kind of situation: "{theme}".
Make them varied: different majors, different specifics, different points in the situation
(some just started, some in the middle, some mostly through it), and different wording.

For each student give:
- name: a realistic first name only, from diverse backgrounds
- major
- situation: one short, casual phrase in the student's own words, with no names or schools (example: "left pre-med, haven't told my parents")
- stage: where they are in it right now, a short phrase (example: "just decided", "a few weeks in", "mostly past it")
- interests: 2 or 3 hobbies, comma separated

Keep it everyday and non-graphic. No crises, self-harm, or medical details.
Return ONLY a JSON array of objects with keys: name, major, situation, stage, interests. No other text."""

KEYS = ["name", "major", "situation", "stage", "interests"]


def parse(text):
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError("no JSON array in response")
    return json.loads(match.group(0))


def fetch(theme):
    start = time.time()
    resp = client.models.generate_content(
        model=MODEL,
        contents=PROMPT.format(n=BATCH, theme=theme),
    )
    return theme, parse(resp.text), time.time() - start


def save(students):
    with open(OUT, "w") as f:
        json.dump(students, f, indent=2)


students = json.load(open(OUT)) if os.path.exists(OUT) else []
print(f"Starting with {len(students)} students, model = {MODEL}")

round_num = 0
while len(students) < TOTAL:
    batches_left = math.ceil((TOTAL - len(students)) / BATCH)
    n = min(WORKERS, batches_left)
    themes = [THEMES[(round_num * WORKERS + i) % len(THEMES)] for i in range(n)]
    round_num += 1
    print(f"Asking Gemma for {n} batches at once (each can take up to a minute)...")

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(fetch, t) for t in themes]
        for fut in as_completed(futures):
            try:
                theme, batch, secs = fut.result()
            except Exception as e:
                msg = str(e)
                if any(code in msg for code in ("404", "400", "401", "403")):
                    print(f"Stopping: {msg}")
                    raise SystemExit(1)
                print(f"  one batch failed, will retry next round: {msg[:150]}")
                continue

            added = 0
            for s in batch:
                if len(students) >= TOTAL:
                    break
                if not isinstance(s, dict) or not all(s.get(k) for k in KEYS):
                    continue
                clean = {k: str(s[k]).strip() for k in KEYS}
                clean["student_id"] = f"syn_{len(students):04d}"
                clean["school"] = random.choice(SCHOOLS)
                clean["year"] = random.choice(YEARS)
                clean["theme"] = theme
                students.append(clean)
                added += 1

            save(students)
            print(f"  +{added} in {secs:.0f}s  ({len(students)}/{TOTAL})  {theme}")

    time.sleep(3)  # small pause between rounds for the rate limit

print(f"Done. Saved {len(students)} students to {OUT}")