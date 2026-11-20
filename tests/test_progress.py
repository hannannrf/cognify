import json
import sqlite3

from backend.app import db
from backend.app.services import assessment, llm, progress
from tests.test_assessment import GOOD_Q, _course_with_lecture


def test_summarize_finds_weak_topics_and_recommends():
    s = progress.summarize([
        {"topic": "Polymorphism", "answered": 4, "correct": 1},  # 2/6 = 33%: weak, easy
        {"topic": "Inheritance", "answered": 3, "correct": 1},   # 2/5 = 40%: weak, medium
        {"topic": "Encapsulation", "answered": 5, "correct": 5},  # 6/7 = 86%
    ])
    assert [t["topic"] for t in s["topics"]] == ["Polymorphism", "Inheritance", "Encapsulation"]
    assert s["weak_topics"] == ["Polymorphism", "Inheritance"]
    assert s["recommendation"] == {
        "text": "Review Polymorphism, then take a quiz on it at easy difficulty.", "topic": "Polymorphism", "difficulty": "easy",
    }


def test_summarize_edge_cases():
    assert progress.summarize([])["recommendation"]["difficulty"] == "easy"
    one_miss = progress.summarize([{"topic": "Loops", "answered": 1, "correct": 0}])
    assert one_miss["weak_topics"] == []  # a single answer is not a trend
    assert one_miss["recommendation"]["topic"] == "Loops"
    strong = progress.summarize([{"topic": "Loops", "answered": 4, "correct": 4}])
    assert strong["recommendation"]["difficulty"] == "hard"


def test_normalize_topic_merges_spelling():
    known = ["Polymorphism"]
    assert assessment.normalize_topic("  polymorphism ", known, "General") == "Polymorphism"
    assert assessment.normalize_topic("", known, "General") == "General"
    assert assessment.normalize_topic(None, known, "OOP") == "OOP"
    assert len(assessment.normalize_topic("x" * 100, known, "General")) == 40


def test_migration_keeps_existing_data(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)  # a database created before the topic column existed
    old.executescript("""
        CREATE TABLE questions (id INTEGER PRIMARY KEY, quiz_id INTEGER NOT NULL, position INTEGER NOT NULL,
            prompt TEXT NOT NULL, options TEXT NOT NULL, answer_index INTEGER NOT NULL, explanation TEXT NOT NULL, chunk_id INTEGER);
        INSERT INTO questions VALUES (1, 1, 0, 'old question', '[]', 0, 'why', NULL);
    """)
    old.commit(); old.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    conn = db.connect()
    row = conn.execute("SELECT prompt, topic FROM questions").fetchone()
    assert (row["prompt"], row["topic"]) == ("old question", None)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    conn.close()
    db.connect().close()  # second start must not re-run the migration


def test_progress_after_quizzes(client, monkeypatch):
    poly = {**GOOD_Q, "topic": "Polymorphism"}
    enc = {**GOOD_Q, "question": "What hides state?", "options": ["Encapsulation", "Loops", "Arrays", "Sorting"], "topic": "encapsulation"}
    prompts = []
    monkeypatch.setattr(llm, "generate", lambda p, json_mode=False: prompts.append(p) or json.dumps([poly, enc]))
    cid = _course_with_lecture(client)
    assert client.get(f"/api/courses/{cid}/progress").json()["quizzes_taken"] == 0

    for answers in ([1, 0], [2, 0]):  # polymorphism wrong twice, encapsulation right twice
        quiz = client.post("/api/quizzes/generate", json={"course_id": cid}).json()
        r = client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers}).json()
        assert r["results"][0]["topic"] == "Polymorphism"

    assert "Reuse one of these existing topic names" in prompts[1] and "Polymorphism" in prompts[1]
    p = client.get(f"/api/courses/{cid}/progress").json()
    assert (p["quizzes_taken"], p["accuracy"]) == (2, 0.5)
    by_topic = {t["topic"]: t for t in p["topics"]}
    assert set(by_topic) == {"Polymorphism", "encapsulation"}
    assert (by_topic["Polymorphism"]["answered"], by_topic["Polymorphism"]["correct"]) == (2, 0)
    assert p["weak_topics"] == ["Polymorphism"]
    assert p["recommendation"]["topic"] == "Polymorphism"
    assert len(p["history"]) == 2
