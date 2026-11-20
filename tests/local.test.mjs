// Checks the in-browser backend (frontend/local.js) against the same cases as the Python tests.
// Run: node --test tests/local.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import { addDays, chunkPages, parseFlashcards, parseQuestions, preview, schedule, streak, summarize } from "../frontend/local.js";

const [AGAIN, HARD, GOOD, EASY] = [0, 1, 2, 3];

test("chunks keep page numbers and overlap", () => {
  const chunks = chunkPages(["", "x".repeat(2500), "short"]);
  assert.deepEqual(chunks.map(([p]) => p), [2, 2, 2, 3]);
  assert.equal(chunks[0][1].length, 1000);
  assert.deepEqual(chunks.at(-1), [3, "short"]);
});

test("schedule matches the Python SM-2 variant", () => {
  let s = schedule(GOOD, 2.5, 0, 0);
  assert.deepEqual([s.interval_days, s.reps], [1, 1]);
  s = schedule(GOOD, s.ease, s.interval_days, s.reps);
  assert.equal(s.interval_days, 3);
  assert.equal(schedule(GOOD, s.ease, s.interval_days, s.reps).interval_days, 8);
  assert.equal(schedule(EASY, 2.5, 0, 0).interval_days, 4);
  assert.equal(schedule(EASY, 2.5, 0, 0).ease, 2.65);
  assert.deepEqual(schedule(AGAIN, 2.5, 20, 5), { ease: 2.3, interval_days: 0, reps: 0 });
  assert.equal(schedule(AGAIN, 1.35, 3, 2).ease, 1.3);
  assert.equal(schedule(HARD, 2.5, 10, 3).interval_days, 12);
  assert.deepEqual(preview(2.5, 0, 0), [0, 1, 1, 4]);
});

test("streak survives until the day after the last study day", () => {
  const today = "2026-11-20";
  assert.deepEqual(streak(new Set(), today), { current: 0, studied_today: false });
  assert.deepEqual(streak(new Set(["2026-11-20", "2026-11-19", "2026-11-18"]), today), { current: 3, studied_today: true });
  assert.deepEqual(streak(new Set(["2026-11-19", "2026-11-18", "2026-11-16"]), today), { current: 2, studied_today: false });
  assert.equal(streak(new Set(["2026-11-17"]), today).current, 0);
  assert.equal(addDays("2026-02-28", 1), "2026-03-01");
});

test("summarize recommends the weakest topic", () => {
  const p = summarize([{ topic: "Loops", answered: 4, correct: 4 }, { topic: "Pointers", answered: 4, correct: 0 }]);
  assert.deepEqual(p.weak_topics, ["Pointers"]);
  assert.deepEqual(p.recommendation, { text: "Review Pointers, then take a quiz on it at easy difficulty.", topic: "Pointers", difficulty: "easy" });
  assert.equal(summarize([]).recommendation.difficulty, "easy");
});

test("model output is validated", () => {
  const sources = [{ filename: "l.pdf", page: 2, text: "t" }];
  const good = { question: "Q?", options: ["a", "b", "c", "d"], answer: 1, explanation: "e", source: 1, topic: "polymorphism" };
  const raw = "```json\n" + JSON.stringify({ questions: [
    good,
    { ...good, topic: "Polymorphism " },
    { ...good, options: ["a", "a", "b", "c"] },
    { ...good, answer: true },
    { ...good, answer: 4 },
  ] }) + "\n```";
  const qs = parseQuestions(raw, sources, ["Polymorphism"]);
  assert.equal(qs.length, 2);
  assert.deepEqual(qs.map((q) => q.topic), ["Polymorphism", "Polymorphism"]);
  assert.deepEqual(qs[0].source, { filename: "l.pdf", page: 2 });
  assert.deepEqual(parseQuestions("not json", sources), []);
  const cards = parseFlashcards(JSON.stringify([{ front: "F", back: "B", source: 9 }, { front: " ", back: "B" }]), sources);
  assert.deepEqual(cards, [{ front: "F", back: "B", source: null }]);
});
