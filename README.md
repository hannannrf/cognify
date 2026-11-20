# Cognify

**Use it now:** https://hannannrf.github.io/cognify/ (runs fully in your browser; paste a free Gemini key in the sidebar)

**Project showcase:** https://hannannrf.github.io/cognify/demo.html

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
| Hosted copy | `frontend/local.js` on GitHub Pages | Same API in the browser: IndexedDB, pdf.js, transformers.js, Gemini with your own key |

**Two ways to run, same UI:** with the Python server, or as a static site. When `index.html` finds no server it loads `local.js`, which serves the same `/api` routes inside the tab. Your notes never leave your browser except the passages sent to Gemini with each question.

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

## Shared AI key for the hosted app (optional, still $0)

Without this, every visitor of the hosted app pastes their own free Gemini key. With it, AI works for everyone right away through a small Cloudflare Worker (`worker/`) that keeps your key secret and allows each visitor 20 requests a day (`DAILY_LIMIT` in `worker/wrangler.toml`). A visitor's own key still takes priority and has no cap.

1. Create a free Cloudflare account, and a Gemini key in a **separate Google project with billing never enabled**, so a leaked or drained key cannot cost money.
2. Deploy the Worker, then store the key as a secret (it is prompted for, never written to a file):

   ```powershell
   cd worker
   npx wrangler@4 login
   npx wrangler@4 deploy
   npx wrangler@4 secret put GEMINI_API_KEY
   ```

3. Put the printed `https://cognify.<your-subdomain>.workers.dev` URL into `AI_PROXY` in `frontend/local.js` and push.

Only origins listed in `ALLOWED_ORIGINS` get browser access. The Worker stores a day and a count per visitor, never the IP itself.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

Covers chunking, PDF extraction, malformed uploads, the full upload-then-chat flow with real embeddings, and the LLM-outage path.

The browser port's shared logic (chunking, scheduling, streaks, mastery, output validation) has its own check:

```powershell
node --test tests/local.test.mjs
```

## Roadmap

- [x] Vertical slice: course, PDF upload, chunking, local embeddings, retrieval, grounded answer, citations
- [x] Quizzes and flashcards: one LLM call per set in JSON mode, strict validation, stored; scoring is deterministic with explanations and page citations
- [x] Topic mastery and weak-topic detection: topics tagged in the same quiz call, Laplace-smoothed accuracy per topic in SQL, deterministic "practice next" recommendation
- [x] Spaced repetition: daily review queue (SM-2 variant), Again/Hard/Good/Easy with keyboard shortcuts, study streak
- [ ] Study planner (deterministic, no LLM calls)
- [ ] Auth, per-user isolation, document deletion
- [ ] Hybrid search with SQLite FTS5

AI answers can be wrong. Always check the cited pages.
