import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "data/study.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    topic TEXT,
    difficulty TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    options TEXT NOT NULL,  -- JSON array of 4 strings
    answer_index INTEGER NOT NULL,
    explanation TEXT NOT NULL,
    chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    answers TEXT NOT NULL,  -- JSON array of chosen indexes
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS flashcards (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    chosen INTEGER,
    correct INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    flashcard_id INTEGER NOT NULL REFERENCES flashcards(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL,
    -- shortcut: the server's local date; correct while the app runs on the student's own machine.
    reviewed_on TEXT NOT NULL DEFAULT (date('now', 'localtime'))
);
"""

# Column changes to existing tables. Append only; PRAGMA user_version records how many have run.
MIGRATIONS = [
    "ALTER TABLE questions ADD COLUMN topic TEXT",
    # spaced-repetition state per flashcard; due_date NULL = new card, due now
    "ALTER TABLE flashcards ADD COLUMN ease REAL NOT NULL DEFAULT 2.5",
    "ALTER TABLE flashcards ADD COLUMN interval_days INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE flashcards ADD COLUMN reps INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE flashcards ADD COLUMN due_date TEXT",
]


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    # FastAPI may open the connection and run the endpoint on different threads; each request owns its connection.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    if conn.execute("PRAGMA user_version").fetchone()[0] < len(MIGRATIONS):
        migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    # BEGIN IMMEDIATE takes the write lock before reading the version, so two first requests arriving together
    # cannot both apply the same ALTER. SQLite DDL is transactional: all pending migrations commit or none do.
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for sql in MIGRATIONS[version:]:
            conn.execute(sql)
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
