"""Quiz and flashcard generation: one LLM call per set, then strict validation of the model's JSON."""

import json

DIFFICULTY_HINTS = {
    "easy": "recall of definitions and key facts",
    "medium": "applying concepts to short examples",
    "hard": "comparing concepts, edge cases, and why something fails",
}


def _numbered(sources: list[dict]) -> str:
    return "\n\n".join(f"[{i}] {s['text']}" for i, s in enumerate(sources, 1))


def quiz_prompt(sources: list[dict], difficulty: str, count: int) -> str:
    return (
        f"Write {count} multiple-choice questions for a university student at {difficulty} difficulty "
        f"({DIFFICULTY_HINTS[difficulty]}), using ONLY the numbered sources below.\n"
        "Return a JSON array. Each item has: question (string), options (array of exactly 4 strings), "
        "answer (index 0-3 of the correct option), explanation (1-2 sentences on why it is correct), "
        "source (number of the source used).\n"
        "Make wrong options plausible. Never ask about file names or page numbers.\n\n"
        f"Sources:\n{_numbered(sources)}"
    )


def flashcard_prompt(sources: list[dict], count: int) -> str:
    return (
        f"Write {count} study flashcards using ONLY the numbered sources below.\n"
        "Return a JSON array. Each item has: front (a short question or term), "
        "back (a concise answer under 40 words), source (number of the source used).\n\n"
        f"Sources:\n{_numbered(sources)}"
    )


def _items(raw: str) -> list:
    """Parse the model's JSON; tolerate code fences and a wrapping object like {"questions": [...]}."""
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return data if isinstance(data, list) else []


def _source(item: dict, sources: list[dict]):
    s = item.get("source")
    return sources[s - 1]["chunk_id"] if isinstance(s, int) and 1 <= s <= len(sources) else None


def parse_questions(raw: str, sources: list[dict]) -> list[dict]:
    """Keep only well-formed questions; the model's output is untrusted input."""
    out = []
    for q in _items(raw):
        if not isinstance(q, dict):
            continue
        opts = q.get("options")
        ans = q.get("answer")
        ok = (
            isinstance(q.get("question"), str) and q["question"].strip()
            and isinstance(opts, list) and len(opts) == 4 and all(isinstance(o, str) and o.strip() for o in opts)
            and len({o.strip() for o in opts}) == 4
            and isinstance(ans, int) and not isinstance(ans, bool) and 0 <= ans <= 3
        )
        if ok:
            out.append({
                "prompt": q["question"].strip(),
                "options": [o.strip() for o in opts],
                "answer_index": ans,
                "explanation": str(q.get("explanation") or "").strip(),
                "chunk_id": _source(q, sources),
            })
    return out


def parse_flashcards(raw: str, sources: list[dict]) -> list[dict]:
    out = []
    for c in _items(raw):
        if isinstance(c, dict) and isinstance(c.get("front"), str) and isinstance(c.get("back"), str):
            if c["front"].strip() and c["back"].strip():
                out.append({"front": c["front"].strip(), "back": c["back"].strip(), "chunk_id": _source(c, sources)})
    return out
