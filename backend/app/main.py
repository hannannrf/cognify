import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app import db
from backend.app.services import assessment, documents, embeddings, llm, progress, retriever

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
NOT_FOUND_ANSWER = "I could not find this in your uploaded material."

log = logging.getLogger("study")
app = FastAPI(title="AI Study Partner")


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def require_course(conn, course_id: int):
    if not conn.execute("SELECT 1 FROM courses WHERE id = ?", (course_id,)).fetchone():
        raise HTTPException(404, "Course not found")


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ChatIn(BaseModel):
    course_id: int
    question: str = Field(min_length=1, max_length=2000)
    strict: bool = True


@app.get("/api/courses")
def list_courses(conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute("SELECT id, name, created_at FROM courses ORDER BY id")]


@app.post("/api/courses", status_code=201)
def create_course(body: CourseIn, conn=Depends(get_conn)):
    cur = conn.execute("INSERT INTO courses (name) VALUES (?)", (body.name.strip(),))
    conn.commit()
    return {"id": cur.lastrowid, "name": body.name.strip()}


@app.get("/api/courses/{course_id}/documents")
def list_documents(course_id: int, conn=Depends(get_conn)):
    require_course(conn, course_id)
    rows = conn.execute(
        "SELECT id, filename, page_count, uploaded_at FROM documents WHERE course_id = ? ORDER BY id", (course_id,)
    )
    return [dict(r) for r in rows]


@app.post("/api/courses/{course_id}/documents", status_code=201)
def upload_document(course_id: int, file: UploadFile, conn=Depends(get_conn)):
    require_course(conn, course_id)
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File larger than 20 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Only PDF files are supported")
    try:
        pages = documents.extract_pages(data)
    except documents.BadDocument as e:
        raise HTTPException(400, str(e))
    chunks = documents.chunk_pages(pages)
    if not chunks:
        raise HTTPException(400, "No text found in PDF (scanned PDFs need OCR, not supported yet)")

    # shortcut: embeds inside the request; a 300-page PDF blocks ~1 min. Move to a background task when that hurts.
    vectors = embeddings.embed([text for _, text in chunks])
    cur = conn.execute(
        "INSERT INTO documents (course_id, filename, page_count) VALUES (?, ?, ?)",
        (course_id, file.filename or "upload.pdf", len(pages)),
    )
    conn.executemany(
        "INSERT INTO chunks (document_id, chunk_index, page_number, text, embedding) VALUES (?, ?, ?, ?, ?)",
        [(cur.lastrowid, i, page, text, vec.tobytes()) for i, ((page, text), vec) in enumerate(zip(chunks, vectors))],
    )
    conn.commit()
    log.info("document %s ingested: %d pages, %d chunks", cur.lastrowid, len(pages), len(chunks))
    return {"id": cur.lastrowid, "filename": file.filename, "page_count": len(pages), "chunks": len(chunks)}


def build_prompt(question: str, sources: list[dict], strict: bool) -> str:
    rule = (
        f'Use ONLY the sources. If they do not contain the answer, reply exactly: "{NOT_FOUND_ANSWER}"'
        if strict
        else "Prefer the sources. If you add general knowledge that is not in the sources, say so clearly."
    )
    numbered = "\n\n".join(f"[{i}] ({s['filename']}, page {s['page']})\n{s['text']}" for i, s in enumerate(sources, 1))
    return (
        "You are a patient study tutor. Answer the student's question for a beginner, "
        f"citing sources inline like [1] or [2].\n{rule}\n\nSources:\n{numbered}\n\nQuestion: {question}"
    )


@lru_cache(maxsize=256)  # identical question + identical sources = no second free-tier call
def cached_generate(prompt: str) -> str:
    return llm.generate(prompt)


@app.post("/api/chat")
def chat(body: ChatIn, conn=Depends(get_conn)):
    require_course(conn, body.course_id)
    sources = retriever.search(conn, body.course_id, embeddings.embed([body.question])[0])
    if not sources:
        return {"answer": NOT_FOUND_ANSWER, "sources": []}  # no LLM call: nothing to ground on
    try:
        answer = cached_generate(build_prompt(body.question, sources, body.strict))
    except llm.LLMError as e:
        log.warning("llm failed: %s", e)
        return JSONResponse({"error": str(e), "sources": sources}, status_code=503)
    return {"answer": answer, "sources": sources}


# ---------- quizzes & flashcards: one LLM call per set, stored, never regenerated ----------

class QuizIn(BaseModel):
    course_id: int
    topic: str = Field("", max_length=200)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    count: int = Field(5, ge=1, le=10)


class FlashcardsIn(BaseModel):
    course_id: int
    topic: str = Field("", max_length=200)
    count: int = Field(8, ge=1, le=20)


class SubmitIn(BaseModel):
    answers: list[int | None]


SOURCE_JOIN = "LEFT JOIN chunks c ON c.id = x.chunk_id LEFT JOIN documents d ON d.id = c.document_id"


def pick_sources(conn, course_id: int, topic: str, k: int = 8) -> list[dict]:
    topic = topic.strip()
    sources = (
        retriever.search(conn, course_id, embeddings.embed([topic])[0], k) if topic else retriever.sample(conn, course_id, k)
    )
    if not sources:
        raise HTTPException(400, "Upload a document to this course first")
    return sources


def generate_json(prompt: str) -> str:
    try:
        return llm.generate(prompt, json_mode=True)
    except llm.LLMError as e:
        raise HTTPException(503, str(e))


@app.post("/api/quizzes/generate", status_code=201)
def generate_quiz(body: QuizIn, conn=Depends(get_conn)):
    require_course(conn, body.course_id)
    sources = pick_sources(conn, body.course_id, body.topic)
    known = [r[0] for r in conn.execute(
        """SELECT DISTINCT x.topic FROM questions x JOIN quizzes z ON z.id = x.quiz_id
           WHERE z.course_id = ? AND x.topic IS NOT NULL""", (body.course_id,))]
    raw = generate_json(assessment.quiz_prompt(sources, body.difficulty, body.count, known))
    fallback = " ".join(body.topic.split())[:40] or "General"
    questions = assessment.parse_questions(raw, sources, known, fallback)[: body.count]
    if not questions:
        raise HTTPException(502, "The model returned no usable questions. Try again.")
    quiz_id = conn.execute(
        "INSERT INTO quizzes (course_id, topic, difficulty) VALUES (?, ?, ?)",
        (body.course_id, body.topic.strip() or None, body.difficulty),
    ).lastrowid
    conn.executemany(
        """INSERT INTO questions (quiz_id, position, prompt, options, answer_index, explanation, chunk_id, topic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(quiz_id, i, q["prompt"], json.dumps(q["options"]), q["answer_index"], q["explanation"], q["chunk_id"], q["topic"])
         for i, q in enumerate(questions)],
    )
    conn.commit()
    # Answers stay on the server until submit.
    return {"id": quiz_id, "questions": [{"prompt": q["prompt"], "options": q["options"]} for q in questions]}


@app.post("/api/quizzes/{quiz_id}/submit")
def submit_quiz(quiz_id: int, body: SubmitIn, conn=Depends(get_conn)):
    rows = conn.execute(
        f"""SELECT x.id, x.answer_index, x.explanation, x.topic, d.filename, c.page_number
            FROM questions x {SOURCE_JOIN} WHERE x.quiz_id = ? ORDER BY x.position""",
        (quiz_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(404, "Quiz not found")
    if len(body.answers) != len(rows):
        raise HTTPException(400, f"Expected {len(rows)} answers")
    results = [
        {
            "correct": chosen == r["answer_index"],
            "answer_index": r["answer_index"],
            "explanation": r["explanation"],
            "topic": r["topic"],
            "source": {"filename": r["filename"], "page": r["page_number"]} if r["filename"] else None,
        }
        for chosen, r in zip(body.answers, rows)
    ]
    score = sum(r["correct"] for r in results)
    attempt_id = conn.execute(
        "INSERT INTO attempts (quiz_id, answers, score, total) VALUES (?, ?, ?, ?)",
        (quiz_id, json.dumps(body.answers), score, len(rows)),
    ).lastrowid
    conn.executemany(
        "INSERT INTO responses (attempt_id, question_id, chosen, correct) VALUES (?, ?, ?, ?)",
        [(attempt_id, r["id"], chosen, int(res["correct"])) for chosen, r, res in zip(body.answers, rows, results)],
    )
    conn.commit()
    return {"score": score, "total": len(rows), "results": results}


@app.get("/api/courses/{course_id}/progress")
def course_progress(course_id: int, conn=Depends(get_conn)):
    require_course(conn, course_id)
    topic_rows = conn.execute(
        """SELECT COALESCE(x.topic, 'General') AS topic, COUNT(*) AS answered, SUM(r.correct) AS correct
           FROM responses r
           JOIN questions x ON x.id = r.question_id
           JOIN quizzes z ON z.id = x.quiz_id
           WHERE z.course_id = ?
           GROUP BY COALESCE(x.topic, 'General')""",
        (course_id,),
    )
    history = conn.execute(
        """SELECT a.id, a.score, a.total, a.submitted_at, z.topic, z.difficulty
           FROM attempts a JOIN quizzes z ON z.id = a.quiz_id
           WHERE z.course_id = ? ORDER BY a.id DESC LIMIT 20""",
        (course_id,),
    )
    totals = conn.execute(
        """SELECT COUNT(*) AS n, SUM(a.score) AS score, SUM(a.total) AS total
           FROM attempts a JOIN quizzes z ON z.id = a.quiz_id WHERE z.course_id = ?""",
        (course_id,),
    ).fetchone()
    return {
        **progress.summarize([dict(r) for r in topic_rows]),
        "quizzes_taken": totals["n"],
        "accuracy": round(totals["score"] / totals["total"], 2) if totals["total"] else None,
        "history": [dict(r) for r in history],
    }


@app.get("/api/courses/{course_id}/flashcards")
def list_flashcards(course_id: int, conn=Depends(get_conn)):
    require_course(conn, course_id)
    rows = conn.execute(
        f"""SELECT x.id, x.front, x.back, d.filename, c.page_number AS page
            FROM flashcards x {SOURCE_JOIN} WHERE x.course_id = ? ORDER BY x.id DESC""",
        (course_id,),
    )
    return [dict(r) for r in rows]


@app.post("/api/flashcards/generate", status_code=201)
def generate_flashcards(body: FlashcardsIn, conn=Depends(get_conn)):
    require_course(conn, body.course_id)
    sources = pick_sources(conn, body.course_id, body.topic)
    cards = assessment.parse_flashcards(generate_json(assessment.flashcard_prompt(sources, body.count)), sources)[: body.count]
    if not cards:
        raise HTTPException(502, "The model returned no usable flashcards. Try again.")
    conn.executemany(
        "INSERT INTO flashcards (course_id, front, back, chunk_id) VALUES (?, ?, ?, ?)",
        [(body.course_id, c["front"], c["back"], c["chunk_id"]) for c in cards],
    )
    conn.commit()
    return list_flashcards(body.course_id, conn)


app.mount("/", StaticFiles(directory=Path(__file__).parents[2] / "frontend", html=True), name="frontend")
