import sqlite3

import numpy as np


def sample(conn: sqlite3.Connection, course_id: int, k: int = 8) -> list[dict]:
    """Random chunks from a course, for quizzes/flashcards with no topic given."""
    rows = conn.execute(
        """SELECT c.id, c.text, c.page_number, d.filename
           FROM chunks c JOIN documents d ON d.id = c.document_id
           WHERE d.course_id = ? ORDER BY RANDOM() LIMIT ?""",
        (course_id, k),
    ).fetchall()
    return [{"chunk_id": r["id"], "filename": r["filename"], "page": r["page_number"], "text": r["text"]} for r in rows]


def search(conn: sqlite3.Connection, course_id: int, query_vector: np.ndarray, k: int = 5) -> list[dict]:
    """Top-k chunks of one course by cosine similarity (vectors are pre-normalized)."""
    # shortcut: brute-force scan of every chunk in the course; fine to ~100k chunks, move to FAISS/sqlite-vec past that.
    rows = conn.execute(
        """SELECT c.id, c.text, c.page_number, c.embedding, d.filename
           FROM chunks c JOIN documents d ON d.id = c.document_id
           WHERE d.course_id = ?""",
        (course_id,),
    ).fetchall()
    if not rows:
        return []
    matrix = np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    scores = matrix @ query_vector
    top = np.argsort(-scores)[:k]
    return [
        {
            "chunk_id": rows[i]["id"],
            "filename": rows[i]["filename"],
            "page": rows[i]["page_number"],
            "text": rows[i]["text"],
            "score": round(float(scores[i]), 4),
        }
        for i in top
    ]
