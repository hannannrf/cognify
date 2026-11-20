// In-browser backend for static hosting (GitHub Pages). Serves the same /api routes as backend/app/main.py, so
// index.html runs unchanged: data lives in this browser's IndexedDB, PDF text comes from pdf.js, embeddings from
// transformers.js (same bge-small model, run locally in the tab), and Gemini is called directly with the user's own key.
// Ports of the Python services are kept line-for-line where possible; tests/local.test.mjs checks the shared logic.

const PDFJS = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/";
const TRANSFORMERS = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.0.2";
const EMBED_MODEL = "Xenova/bge-small-en-v1.5";
const GEMINI_MODEL = "gemini-flash-lite-latest";
export const KEY_STORAGE = "cognify-gemini-key";
const NOT_FOUND_ANSWER = "I could not find this in your uploaded material.";
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const CHUNK_CHARS = 1000, OVERLAP_CHARS = 200;
const AGAIN = 0, HARD = 1, GOOD = 2, EASY = 3, MIN_EASE = 1.3;
const WEAK_BELOW = 0.6, MIN_ANSWERED = 2;
const DIFFICULTY_HINTS = {
  easy: "recall of definitions and key facts",
  medium: "applying concepts to short examples",
  hard: "comparing concepts, edge cases, and why something fails",
};

// ---------- pure logic (ported from backend/app/services) ----------

export function chunkPages(pages) {
  const chunks = [], step = CHUNK_CHARS - OVERLAP_CHARS;
  pages.forEach((text, i) => {
    for (let start = 0; start < Math.max(text.length - OVERLAP_CHARS, 1); start += step) {
      const piece = text.slice(start, start + CHUNK_CHARS).trim();
      if (piece) chunks.push([i + 1, piece]);
    }
  });
  return chunks;
}

const round2 = (x) => Math.round(x * 100) / 100;

export function schedule(rating, ease, interval_days, reps) {
  if (rating === AGAIN) return { ease: round2(Math.max(MIN_EASE, ease - 0.2)), interval_days: 0, reps: 0 };
  if (rating === HARD) return { ease: round2(Math.max(MIN_EASE, ease - 0.15)), interval_days: Math.max(1, Math.round(interval_days * 1.2)), reps: reps + 1 };
  const interval = reps === 0 ? (rating === GOOD ? 1 : 4)
    : reps === 1 ? (rating === GOOD ? 3 : 6)
    : Math.round(interval_days * ease * (rating === EASY ? 1.3 : 1));
  return { ease: round2(ease + (rating === EASY ? 0.15 : 0)), interval_days: Math.max(interval, interval_days + 1), reps: reps + 1 };
}

export const preview = (ease, interval_days, reps) => [0, 1, 2, 3].map((r) => schedule(r, ease, interval_days, reps).interval_days);

export function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function streak(studyDays, today) {
  const studied_today = studyDays.has(today);
  let day = studied_today ? today : addDays(today, -1), current = 0;
  while (studyDays.has(day)) { current++; day = addDays(day, -1); }
  return { current, studied_today };
}

export function summarize(topicRows) {
  const topics = topicRows
    .map((t) => ({ ...t, mastery: round2((t.correct + 1) / (t.answered + 2)) }))
    .sort((a, b) => a.mastery - b.mastery || b.answered - a.answered);
  const weak = topics.filter((t) => t.answered >= MIN_ANSWERED && t.mastery < WEAK_BELOW);
  const thin = topics.filter((t) => t.answered < MIN_ANSWERED);
  let rec;
  if (!topics.length) rec = { text: "Take your first quiz to start tracking your progress.", topic: "", difficulty: "easy" };
  else if (weak.length) {
    const t = weak[0], difficulty = t.mastery < 0.4 ? "easy" : "medium";
    rec = { text: `Review ${t.topic}, then take a quiz on it at ${difficulty} difficulty.`, topic: t.topic, difficulty };
  } else if (thin.length) rec = { text: `Practice ${thin[0].topic} a bit more to get a reliable score.`, topic: thin[0].topic, difficulty: "medium" };
  else rec = { text: "No weak topics. Challenge yourself with a hard quiz.", topic: "", difficulty: "hard" };
  return { topics, weak_topics: weak.map((t) => t.topic), recommendation: rec };
}

const numbered = (sources) => sources.map((s, i) => `[${i + 1}] ${s.text}`).join("\n\n");

export function quizPrompt(sources, difficulty, count, knownTopics = []) {
  const reuse = knownTopics.length ? ` Reuse one of these existing topic names when it fits: ${knownTopics.join(", ")}.` : "";
  return `Write ${count} multiple-choice questions for a university student at ${difficulty} difficulty ` +
    `(${DIFFICULTY_HINTS[difficulty]}), using ONLY the numbered sources below.\n` +
    "Return a JSON array. Each item has: question (string), options (array of exactly 4 strings), " +
    "answer (index 0-3 of the correct option), explanation (1-2 sentences on why it is correct), " +
    'source (number of the source used), topic (the concept tested, 1-3 words, e.g. "Polymorphism").' +
    `${reuse}\n` +
    "Make wrong options plausible. Never ask about file names or page numbers.\n\n" +
    `Sources:\n${numbered(sources)}`;
}

export const flashcardPrompt = (sources, count) =>
  `Write ${count} study flashcards using ONLY the numbered sources below.\n` +
  "Return a JSON array. Each item has: front (a short question or term), " +
  "back (a concise answer under 40 words), source (number of the source used).\n\n" +
  `Sources:\n${numbered(sources)}`;

export function buildPrompt(question, sources, strict) {
  const rule = strict
    ? `Use ONLY the sources. If they do not contain the answer, reply exactly: "${NOT_FOUND_ANSWER}"`
    : "Prefer the sources. If you add general knowledge that is not in the sources, say so clearly.";
  const nums = sources.map((s, i) => `[${i + 1}] (${s.filename}, page ${s.page})\n${s.text}`).join("\n\n");
  return "You are a patient study tutor. Answer the student's question for a beginner, " +
    `citing sources inline like [1] or [2].\n${rule}\n\nSources:\n${nums}\n\nQuestion: ${question}`;
}

export function normalizeTopic(raw, knownTopics, fallback) {
  const topic = typeof raw === "string" ? raw.split(/\s+/).filter(Boolean).join(" ").slice(0, 40) : "";
  if (!topic) return fallback;
  return knownTopics.find((k) => k.toLowerCase() === topic.toLowerCase()) ?? topic;
}

function items(raw) { // the model's JSON is untrusted: tolerate code fences and a wrapping object
  let text = raw.trim();
  for (const p of ["```json", "```"]) if (text.startsWith(p)) { text = text.slice(p.length); break; }
  if (text.endsWith("```")) text = text.slice(0, -3);
  let data;
  try { data = JSON.parse(text); } catch { return []; }
  if (data && typeof data === "object" && !Array.isArray(data)) data = Object.values(data).find(Array.isArray) ?? [];
  return Array.isArray(data) ? data : [];
}

function sourceOf(item, sources) {
  const s = item.source;
  const hit = Number.isInteger(s) && s >= 1 && s <= sources.length ? sources[s - 1] : null;
  return hit && { filename: hit.filename, page: hit.page };
}

const str = (v) => (typeof v === "string" ? v.trim() : "");

export function parseQuestions(raw, sources, knownTopics = [], fallbackTopic = "General") {
  const out = [], known = [...knownTopics];
  for (const q of items(raw)) {
    if (!q || typeof q !== "object") continue;
    const opts = q.options, ans = q.answer;
    const ok = str(q.question) && Array.isArray(opts) && opts.length === 4 && opts.every((o) => str(o))
      && new Set(opts.map((o) => o.trim())).size === 4 && Number.isInteger(ans) && ans >= 0 && ans <= 3;
    if (!ok) continue;
    const topic = normalizeTopic(q.topic, known, fallbackTopic);
    if (!known.includes(topic)) known.push(topic); // later questions in the same batch merge into it too
    out.push({ prompt: str(q.question), options: opts.map((o) => o.trim()), answer_index: ans,
      explanation: String(q.explanation ?? "").trim(), source: sourceOf(q, sources), topic });
  }
  return out;
}

export function parseFlashcards(raw, sources) {
  return items(raw)
    .filter((c) => c && str(c.front) && str(c.back))
    .map((c) => ({ front: str(c.front), back: str(c.back), source: sourceOf(c, sources) }));
}

// ---------- browser services ----------

export function hasKey() {
  try { return !!localStorage.getItem(KEY_STORAGE); } catch { return false; }
}

const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const nowLocal = () => new Date().toLocaleString("sv-SE"); // "YYYY-MM-DD HH:MM:SS" in local time
const today = () => nowLocal().slice(0, 10);

async function generate(prompt, jsonMode = false) {
  let key = "";
  try { key = (localStorage.getItem(KEY_STORAGE) || "").trim(); } catch {}
  if (!key) throw new Error("Add your free Gemini key in the sidebar (AI key) first.");
  const body = { contents: [{ parts: [{ text: prompt }] }] };
  if (jsonMode) body.generationConfig = { responseMimeType: "application/json" };
  for (let attempt = 0; ; attempt++) {
    let res;
    try {
      res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`, {
        method: "POST", headers: { "Content-Type": "application/json", "x-goog-api-key": key },
        body: JSON.stringify(body), signal: AbortSignal.timeout(30000),
      });
    } catch (e) {
      if (attempt < 2) { await sleep(2000 * 2 ** attempt); continue; }
      throw new Error(`Could not reach Gemini: ${e.message}`);
    }
    // 429 = free-tier rate limit, 5xx = provider hiccup: back off and retry; anything else is final.
    if ([429, 500, 503].includes(res.status) && attempt < 2) { await sleep(2000 * 2 ** attempt); continue; }
    if (res.status === 429) throw new Error("Free-tier rate limit or daily quota reached. Try again later.");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`Gemini returned HTTP ${res.status}: ${data.error?.message ?? ""}`);
    const parts = data.candidates?.[0]?.content?.parts;
    if (!parts) throw new Error("Unexpected Gemini response (possibly blocked).");
    return parts.map((p) => p.text ?? "").join("");
  }
}

function loadScript(src) {
  return new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = () => fail(new Error(`Could not load ${src}`));
    document.head.append(s);
  });
}

async function extractPages(bytes) {
  if (!window.pdfjsLib) {
    await loadScript(`${PDFJS}pdf.min.js`);
    pdfjsLib.GlobalWorkerOptions.workerSrc = `${PDFJS}pdf.worker.min.js`;
  }
  const pdf = await pdfjsLib.getDocument({ data: bytes }).promise;
  const pages = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const content = await (await pdf.getPage(i)).getTextContent();
    pages.push(content.items.map((it) => it.str).join(" ").replace(/\s+/g, " ").trim());
  }
  return pages;
}

let extractor;
async function embed(texts) {
  extractor ??= import(TRANSFORMERS).then((t) => t.pipeline("feature-extraction", EMBED_MODEL));
  const run = await extractor, out = [];
  for (let i = 0; i < texts.length; i += 16) { // small batches keep memory flat on big PDFs
    const t = await run(texts.slice(i, i + 16), { pooling: "cls", normalize: true });
    out.push(...t.tolist().map((v) => Float32Array.from(v)));
  }
  return out;
}

// ---------- storage: the whole state is one IndexedDB record ----------
// shortcut: every write saves the full state (vectors included); fine for one student's notes, split stores past ~50 MB.

let db, ready;
const empty = () => ({ seq: 0, courses: [], documents: [], chunks: [], quizzes: [], attempts: [], flashcards: [], days: [] });

function idb(mode, fn) {
  return new Promise((ok, fail) => {
    const open = indexedDB.open("cognify", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("state");
    open.onerror = () => fail(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction("state", mode);
      const req = fn(tx.objectStore("state"));
      tx.oncomplete = () => { open.result.close(); ok(req.result); };
      tx.onerror = () => fail(tx.error);
    };
  });
}
const load = () => (ready ??= idb("readonly", (s) => s.get("db")).then((v) => (db = v ?? empty())));
const save = () => idb("readwrite", (s) => s.put(db, "db"));
const nextId = () => ++db.seq;
const markStudied = () => db.days.includes(today()) || db.days.push(today());

// ---------- routes ----------

class HttpError extends Error {
  constructor(status, body) { super(body.detail ?? body.error); this.status = status; this.body = body; }
}
const fail = (status, detail) => { throw new HttpError(status, { detail }); };

function course(id) { return db.courses.find((c) => c.id === id) ?? fail(404, "Course not found"); }

function search(courseId, vec, k = 5) {
  return db.chunks.filter((c) => c.course_id === courseId)
    .map((c) => ({ c, score: c.vec.reduce((sum, x, i) => sum + x * vec[i], 0) }))
    .sort((a, b) => b.score - a.score).slice(0, k)
    .map(({ c, score }) => ({ filename: c.filename, page: c.page, text: c.text, score: round2(score) }));
}

function sample(courseId, k = 8) {
  const pool = db.chunks.filter((c) => c.course_id === courseId);
  for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
  return pool.slice(0, k).map((c) => ({ filename: c.filename, page: c.page, text: c.text }));
}

async function pickSources(courseId, topic, k = 8) {
  topic = topic.trim();
  const sources = topic ? search(courseId, (await embed([topic]))[0], k) : sample(courseId, k);
  return sources.length ? sources : fail(400, "Upload a document to this course first");
}

async function generateJson(prompt) {
  try { return await generate(prompt, true); } catch (e) { fail(503, e.message); }
}

const intIn = (v, lo, hi, name) => (Number.isInteger(v) && v >= lo && v <= hi ? v : fail(422, `${name} must be ${lo}-${hi}`));
const text = (v, max, name, min = 0) => {
  const s = typeof v === "string" ? v : "";
  return s.trim().length >= min && s.length <= max ? s : fail(422, `${name} must be ${min}-${max} characters`);
};

const chatCache = new Map(); // identical question + identical sources = no second free-tier call

const routes = [
  ["GET", /^\/api\/courses$/, () => db.courses.map(({ id, name, created_at }) => ({ id, name, created_at }))],

  ["POST", /^\/api\/courses$/, async (b) => {
    const name = text(b.name, 100, "name", 1).trim();
    const c = { id: nextId(), name, created_at: nowLocal() };
    db.courses.push(c); await save();
    return { id: c.id, name };
  }],

  ["GET", /^\/api\/courses\/(\d+)\/documents$/, (_, id) => {
    course(id);
    return db.documents.filter((d) => d.course_id === id);
  }],

  ["POST", /^\/api\/courses\/(\d+)\/documents$/, async (form, id) => {
    course(id);
    const file = form?.get?.("file");
    if (!(file instanceof Blob)) fail(400, "No file uploaded");
    if (file.size > MAX_UPLOAD_BYTES) fail(413, "File larger than 20 MB");
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (String.fromCharCode(...bytes.slice(0, 4)) !== "%PDF") fail(400, "Only PDF files are supported");
    let pages;
    try { pages = await extractPages(bytes); } catch (e) { fail(400, `Could not read PDF: ${e.message}`); }
    const chunks = chunkPages(pages);
    if (!chunks.length) fail(400, "No text found in PDF (scanned PDFs need OCR, not supported yet)");
    const vectors = await embed(chunks.map(([, t]) => t));
    const doc = { id: nextId(), course_id: id, filename: file.name || "upload.pdf", page_count: pages.length, uploaded_at: nowLocal() };
    db.documents.push(doc);
    chunks.forEach(([page, t], i) => db.chunks.push({ id: nextId(), course_id: id, document_id: doc.id, filename: doc.filename, page, text: t, vec: vectors[i] }));
    await save();
    return { id: doc.id, filename: doc.filename, page_count: pages.length, chunks: chunks.length };
  }],

  ["POST", /^\/api\/chat$/, async (b) => {
    course(b.course_id);
    const question = text(b.question, 2000, "question", 1);
    const sources = search(b.course_id, (await embed([question]))[0]);
    if (!sources.length) return { answer: NOT_FOUND_ANSWER, sources: [] }; // no LLM call: nothing to ground on
    const prompt = buildPrompt(question, sources, b.strict ?? true);
    try {
      if (!chatCache.has(prompt)) chatCache.set(prompt, await generate(prompt));
      return { answer: chatCache.get(prompt), sources };
    } catch (e) {
      throw new HttpError(503, { error: e.message, sources });
    }
  }],

  ["POST", /^\/api\/quizzes\/generate$/, async (b) => {
    course(b.course_id);
    const topic = text(b.topic ?? "", 200, "topic"), difficulty = b.difficulty ?? "medium", count = intIn(b.count ?? 5, 1, 10, "count");
    if (!(difficulty in DIFFICULTY_HINTS)) fail(422, "difficulty must be easy, medium or hard");
    const sources = await pickSources(b.course_id, topic);
    const known = [...new Set(db.quizzes.filter((z) => z.course_id === b.course_id).flatMap((z) => z.questions.map((q) => q.topic)))];
    const raw = await generateJson(quizPrompt(sources, difficulty, count, known));
    const fallback = topic.split(/\s+/).filter(Boolean).join(" ").slice(0, 40) || "General";
    const questions = parseQuestions(raw, sources, known, fallback).slice(0, count);
    if (!questions.length) fail(502, "The model returned no usable questions. Try again.");
    const quiz = { id: nextId(), course_id: b.course_id, topic: topic.trim() || null, difficulty, questions };
    db.quizzes.push(quiz); await save();
    return { id: quiz.id, questions: questions.map(({ prompt, options }) => ({ prompt, options })) };
  }],

  ["POST", /^\/api\/quizzes\/(\d+)\/submit$/, async (b, id) => {
    const quiz = db.quizzes.find((z) => z.id === id) ?? fail(404, "Quiz not found");
    if (!Array.isArray(b.answers) || b.answers.length !== quiz.questions.length) fail(400, `Expected ${quiz.questions.length} answers`);
    const results = quiz.questions.map((q, i) => ({
      correct: b.answers[i] === q.answer_index, answer_index: q.answer_index, explanation: q.explanation, topic: q.topic, source: q.source,
    }));
    const score = results.filter((r) => r.correct).length;
    db.attempts.push({ id: nextId(), quiz_id: id, course_id: quiz.course_id, score, total: results.length, submitted_at: nowLocal(),
      results: results.map(({ topic, correct }) => ({ topic, correct })) });
    markStudied(); await save();
    return { score, total: results.length, results };
  }],

  ["GET", /^\/api\/courses\/(\d+)\/progress$/, (_, id) => {
    course(id);
    const attempts = db.attempts.filter((a) => a.course_id === id), byTopic = new Map();
    for (const r of attempts.flatMap((a) => a.results)) {
      const t = byTopic.get(r.topic ?? "General") ?? { topic: r.topic ?? "General", answered: 0, correct: 0 };
      t.answered++; t.correct += r.correct ? 1 : 0; byTopic.set(t.topic, t);
    }
    const score = attempts.reduce((s, a) => s + a.score, 0), total = attempts.reduce((s, a) => s + a.total, 0);
    return {
      ...summarize([...byTopic.values()]),
      quizzes_taken: attempts.length,
      accuracy: total ? round2(score / total) : null,
      history: attempts.slice(-20).reverse().map((a) => {
        const z = db.quizzes.find((q) => q.id === a.quiz_id);
        return { id: a.id, score: a.score, total: a.total, submitted_at: a.submitted_at, topic: z?.topic ?? null, difficulty: z?.difficulty };
      }),
    };
  }],

  ["GET", /^\/api\/courses\/(\d+)\/flashcards$/, (_, id) => listCards(id)],

  ["POST", /^\/api\/flashcards\/generate$/, async (b) => {
    course(b.course_id);
    const count = intIn(b.count ?? 8, 1, 20, "count");
    const sources = await pickSources(b.course_id, text(b.topic ?? "", 200, "topic"));
    const cards = parseFlashcards(await generateJson(flashcardPrompt(sources, count)), sources).slice(0, count);
    if (!cards.length) fail(502, "The model returned no usable flashcards. Try again.");
    for (const c of cards) {
      db.flashcards.push({ id: nextId(), course_id: b.course_id, front: c.front, back: c.back, filename: c.source?.filename ?? null,
        page: c.source?.page ?? null, ease: 2.5, interval_days: 0, reps: 0, due_date: null });
    }
    await save();
    return listCards(b.course_id);
  }],

  ["GET", /^\/api\/courses\/(\d+)\/review$/, (_, id) => {
    course(id);
    const t = today();
    const due = db.flashcards.filter((c) => c.course_id === id && (c.due_date === null || c.due_date <= t))
      .sort((a, b) => (a.due_date === null) - (b.due_date === null) || (a.due_date ?? "").localeCompare(b.due_date ?? "") || a.id - b.id)
      .slice(0, 50);
    return { cards: due.map((c) => ({ ...cardView(c), preview: preview(c.ease, c.interval_days, c.reps) })) };
  }],

  ["POST", /^\/api\/flashcards\/(\d+)\/review$/, async (b, id) => {
    const rating = intIn(b.rating, 0, 3, "rating");
    const card = db.flashcards.find((c) => c.id === id) ?? fail(404, "Flashcard not found");
    Object.assign(card, schedule(rating, card.ease, card.interval_days, card.reps));
    card.due_date = addDays(today(), card.interval_days);
    markStudied(); await save();
    return { interval_days: card.interval_days, due_date: card.due_date, preview: preview(card.ease, card.interval_days, card.reps) };
  }],

  ["GET", /^\/api\/streak$/, () => streak(new Set(db.days), today())],
];

const cardView = ({ id, front, back, filename, page }) => ({ id, front, back, filename, page });
function listCards(id) {
  course(id);
  return db.flashcards.filter((c) => c.course_id === id).sort((a, b) => b.id - a.id).map(cardView);
}

const reply = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** Drop-in for fetch(path, options) on /api routes; returns a Response like the FastAPI server would. */
export async function handle(path, options = {}) {
  const method = options.method ?? "GET";
  try {
    await load();
    const body = typeof options.body === "string" ? JSON.parse(options.body) : options.body ?? {};
    for (const [m, re, fn] of routes) {
      const hit = m === method && path.match(re);
      if (hit) return reply(200, await fn(body, ...hit.slice(1).map(Number)));
    }
    return reply(404, { detail: "Not found" });
  } catch (e) {
    if (e instanceof HttpError) return reply(e.status, e.body);
    console.error(e);
    return reply(500, { detail: e.message });
  }
}
