# Cognify

Upload your lecture PDFs, ask questions, and get answers grounded in your own material, with page-level citations.

**Built to cost $0:** free-tier Gemini API (or a fully local Ollama model), local embeddings, SQLite, no paid services.

## How it works

```
PDF -> text per page -> overlapping chunks -> local embeddings (bge-small, ONNX) -> SQLite
Question -> embedding -> cosine search over the course's chunks -> top 5 -> prompt -> LLM -> answer + [n] citations
```

- **Strict source mode** (default on): the model must answer only from your material, or say it could not find the answer.
- **Rate limits are expected:** if the free tier says 429 or the provider is down, the app still shows the matching passages.
- **No wasted calls:** a question with no matching material never reaches the LLM, and repeated identical prompts are cached.

| Layer | Choice | Why |
| --- | --- | --- |
| API | FastAPI | Typed, auto docs at `/docs` |
| Storage | SQLite (stdlib `sqlite3`) | Zero setup |
| Embeddings | `fastembed` + `BAAI/bge-small-en-v1.5` | Runs locally on CPU, no quota |
| Vector search | numpy brute-force cosine | One student's notes fit easily; swap for FAISS past ~100k chunks |
| LLM | Gemini free tier, or Ollama | Both $0; swap with `LLM_PROVIDER` |
| Frontend | One HTML file | No build step |

## Run it

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/) (or plain pip).

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
copy .env.example .env   # then paste your free key from https://aistudio.google.com/apikey
.venv\Scripts\python -m uvicorn backend.app.main:app --env-file .env
```

Open http://localhost:8000 for the app, or http://localhost:8000/demo.html for the project showcase with its animated "agents building the page" intro. The first upload downloads the embedding model once (about 70 MB, into `data/models`).

**Fully offline / private:** install [Ollama](https://ollama.com), run `ollama pull llama3.2`, and set `LLM_PROVIDER=ollama` in `.env`. Your notes never leave your machine.

> Privacy note: free-tier API terms may allow the provider to use submitted content to improve its products. Check the current Google AI Studio terms before uploading private notes, or use Ollama.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

Covers chunking, PDF extraction, malformed uploads, the full upload-then-chat flow with real embeddings, and the LLM-outage path.

## Roadmap

- [x] Vertical slice: course, PDF upload, chunking, local embeddings, retrieval, grounded answer, citations
- [x] Quizzes and flashcards: one LLM call per set in JSON mode, strict validation, stored; scoring is deterministic with explanations and page citations
- [x] Topic mastery and weak-topic detection: topics tagged in the same quiz call, Laplace-smoothed accuracy per topic in SQL, deterministic "practice next" recommendation
- [ ] Study planner and spaced repetition (deterministic, no LLM calls)
- [ ] Auth, per-user isolation, document deletion
- [ ] Hybrid search with SQLite FTS5

AI answers can be wrong. Always check the cited pages.
