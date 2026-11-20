import json

from backend.app.services import assessment, llm
from tests.test_slice import LECTURE

SOURCES = [{"chunk_id": 11, "text": "a"}, {"chunk_id": 12, "text": "b"}]
GOOD_Q = {"question": "What do virtual functions enable?", "options": ["Polymorphism", "Encapsulation", "Inheritance", "Recursion"],
          "answer": 0, "explanation": "They dispatch to the derived implementation.", "source": 2}


def test_parse_questions_drops_malformed_items():
    raw = "```json\n" + json.dumps({"questions": [
        GOOD_Q,
        {**GOOD_Q, "options": ["only", "three", "options"]},
        {**GOOD_Q, "answer": 4},
        {**GOOD_Q, "answer": True},  # bool is an int in Python; reject it
        {**GOOD_Q, "options": ["same", "same", "x", "y"]},
        "not a dict",
    ]}) + "\n```"
    qs = assessment.parse_questions(raw, SOURCES)
    assert len(qs) == 1
    assert qs[0]["answer_index"] == 0 and qs[0]["chunk_id"] == 12
    assert assessment.parse_questions("not json", SOURCES) == []
    assert assessment.parse_questions(json.dumps([{**GOOD_Q, "source": 9}]), SOURCES)[0]["chunk_id"] is None


def test_parse_flashcards():
    raw = json.dumps([{"front": "Polymorphism", "back": "One interface, many implementations.", "source": 1}, {"front": "", "back": "x"}])
    assert assessment.parse_flashcards(raw, SOURCES) == [
        {"front": "Polymorphism", "back": "One interface, many implementations.", "chunk_id": 11}
    ]


def _course_with_lecture(client):
    cid = client.post("/api/courses", json={"name": "OOP"}).json()["id"]
    client.post(f"/api/courses/{cid}/documents", files={"file": ("oop.pdf", LECTURE, "application/pdf")})
    return cid


def test_quiz_hides_answers_then_scores_submission(client, monkeypatch):
    second = {**GOOD_Q, "question": "What hides internal state?", "options": ["Encapsulation", "Polymorphism", "Loops", "Arrays"], "source": 1}
    calls = []
    monkeypatch.setattr(llm, "generate", lambda p, json_mode=False: calls.append(json_mode) or json.dumps([GOOD_Q, second]))
    cid = _course_with_lecture(client)

    quiz = client.post("/api/quizzes/generate", json={"course_id": cid, "difficulty": "easy", "count": 2})
    assert quiz.status_code == 201, quiz.text
    quiz = quiz.json()
    assert calls == [True] and len(quiz["questions"]) == 2
    assert "answer_index" not in quiz["questions"][0] and "explanation" not in quiz["questions"][0]

    r = client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": [0, None]}).json()
    assert (r["score"], r["total"]) == (1, 2)
    assert r["results"][1] == {"correct": False, "answer_index": 0, "explanation": GOOD_Q["explanation"], "source": r["results"][1]["source"]}
    assert r["results"][0]["source"]["filename"] == "oop.pdf"
    assert client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": [0]}).status_code == 400
    assert client.post("/api/quizzes/999/submit", json={"answers": []}).status_code == 404


def test_quiz_errors_are_clear(client, monkeypatch):
    cid = client.post("/api/courses", json={"name": "Empty"}).json()["id"]
    assert client.post("/api/quizzes/generate", json={"course_id": cid}).status_code == 400  # no documents

    cid = _course_with_lecture(client)
    monkeypatch.setattr(llm, "generate", lambda p, json_mode=False: "[]")
    assert client.post("/api/quizzes/generate", json={"course_id": cid}).status_code == 502

    def down(p, json_mode=False):
        raise llm.LLMError("quota reached")

    monkeypatch.setattr(llm, "generate", down)
    r = client.post("/api/quizzes/generate", json={"course_id": cid})
    assert r.status_code == 503 and "quota" in r.json()["detail"]


def test_flashcards_are_stored_and_listed(client, monkeypatch):
    cards = [{"front": "Polymorphism", "back": "One interface, many implementations.", "source": 1}]
    monkeypatch.setattr(llm, "generate", lambda p, json_mode=False: json.dumps(cards))
    cid = _course_with_lecture(client)

    made = client.post("/api/flashcards/generate", json={"course_id": cid, "topic": "polymorphism", "count": 3})
    assert made.status_code == 201, made.text
    listed = client.get(f"/api/courses/{cid}/flashcards").json()
    assert listed == made.json()
    assert listed[0]["front"] == "Polymorphism" and listed[0]["filename"] == "oop.pdf"
