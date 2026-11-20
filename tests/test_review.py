import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from backend.app import db
from backend.app.services import llm, review
from tests.test_assessment import _course_with_lecture

AGAIN, HARD, GOOD, EASY = range(4)


def test_schedule_grows_intervals_on_success():
    s = review.schedule(GOOD, 2.5, 0, 0)
    assert (s["interval_days"], s["reps"]) == (1, 1)
    s = review.schedule(GOOD, s["ease"], s["interval_days"], s["reps"])
    assert s["interval_days"] == 3
    s = review.schedule(GOOD, s["ease"], s["interval_days"], s["reps"])
    assert s["interval_days"] == 8  # round(3 * 2.5)
    assert review.schedule(EASY, 2.5, 0, 0)["interval_days"] == 4
    assert review.schedule(EASY, 2.5, 0, 0)["ease"] == 2.65


def test_schedule_again_and_hard():
    s = review.schedule(AGAIN, 2.5, 20, 5)
    assert s == {"ease": 2.3, "interval_days": 0, "reps": 0}  # relearn today
    assert review.schedule(AGAIN, 1.35, 3, 2)["ease"] == review.MIN_EASE  # ease floor
    assert review.schedule(HARD, 2.5, 10, 3)["interval_days"] == 12
    assert review.schedule(HARD, 2.5, 0, 0)["interval_days"] == 1
    assert review.preview(2.5, 0, 0) == [0, 1, 1, 4]


def test_streak():
    today = date(2026, 11, 20)
    assert review.streak(set(), today) == {"current": 0, "studied_today": False}
    assert review.streak({"2026-11-20", "2026-11-19", "2026-11-18"}, today) == {"current": 3, "studied_today": True}
    # not studied yet today: yesterday's streak is still alive
    assert review.streak({"2026-11-19", "2026-11-18", "2026-11-16"}, today) == {"current": 2, "studied_today": False}
    assert review.streak({"2026-11-17"}, today)["current"] == 0  # missed a day


def test_review_flow_and_streak(client, monkeypatch):
    cards = [{"front": f"Term {i}", "back": f"Meaning {i}", "source": 1} for i in range(3)]
    monkeypatch.setattr(llm, "generate", lambda p, json_mode=False: json.dumps(cards))
    cid = _course_with_lecture(client)
    client.post("/api/flashcards/generate", json={"course_id": cid, "count": 3})
    assert client.get("/api/streak").json() == {"current": 0, "studied_today": False}

    queue = client.get(f"/api/courses/{cid}/review").json()["cards"]
    assert len(queue) == 3 and queue[0]["preview"] == [0, 1, 1, 4]
    assert "ease" not in queue[0]

    good = client.post(f"/api/flashcards/{queue[0]['id']}/review", json={"rating": GOOD}).json()
    assert good["interval_days"] == 1 and good["due_date"] > date.today().isoformat()
    again = client.post(f"/api/flashcards/{queue[1]['id']}/review", json={"rating": AGAIN}).json()
    assert again["interval_days"] == 0 and again["due_date"] == date.today().isoformat()

    left = [c["id"] for c in client.get(f"/api/courses/{cid}/review").json()["cards"]]
    assert queue[0]["id"] not in left and queue[1]["id"] in left and len(left) == 2
    assert client.get("/api/streak").json() == {"current": 1, "studied_today": True}

    assert client.post(f"/api/flashcards/{queue[2]['id']}/review", json={"rating": 4}).status_code == 422
    assert client.post("/api/flashcards/999/review", json={"rating": 1}).status_code == 404


def test_migration_gives_old_cards_a_fresh_schedule(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)  # database from before spaced repetition, at migration version 1
    old.executescript("""
        CREATE TABLE flashcards (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, front TEXT NOT NULL,
            back TEXT NOT NULL, chunk_id INTEGER, created_at TEXT);
        CREATE TABLE questions (id INTEGER PRIMARY KEY, quiz_id INTEGER, position INTEGER, prompt TEXT, options TEXT,
            answer_index INTEGER, explanation TEXT, chunk_id INTEGER, topic TEXT);
        INSERT INTO flashcards VALUES (1, 1, 'front', 'back', NULL, NULL);
        PRAGMA user_version = 1;
    """)
    old.commit(); old.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    # The page's first requests arrive together; every one must survive the migration racing the others.
    with ThreadPoolExecutor(8) as pool:
        conns = list(pool.map(lambda _: db.connect(), range(8)))
    card = dict(conns[0].execute("SELECT front, ease, interval_days, reps, due_date FROM flashcards").fetchone())
    assert card == {"front": "front", "ease": 2.5, "interval_days": 0, "reps": 0, "due_date": None}
    assert conns[0].execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    for c in conns:
        c.close()
