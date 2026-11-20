"""Topic mastery and next-step recommendation. Pure math on stored quiz responses: no LLM calls."""

WEAK_BELOW = 0.6  # mastery under 60% marks a weak topic
MIN_ANSWERED = 2  # one lucky or unlucky answer is not a trend


def mastery(correct: int, answered: int) -> float:
    """Laplace-smoothed accuracy: starts at 50% and moves toward the real rate as answers accumulate.

    1 of 1 correct gives 67%, not 100%; 9 of 10 gives 83%.
    """
    return (correct + 1) / (answered + 2)


def summarize(topic_rows: list[dict]) -> dict:
    """topic_rows: [{"topic", "answered", "correct"}]. Returns topics sorted weakest first, plus a recommendation."""
    topics = sorted(
        ({**t, "mastery": round(mastery(t["correct"], t["answered"]), 2)} for t in topic_rows),
        key=lambda t: (t["mastery"], -t["answered"]),
    )
    weak = [t for t in topics if t["answered"] >= MIN_ANSWERED and t["mastery"] < WEAK_BELOW]
    thin = [t for t in topics if t["answered"] < MIN_ANSWERED]

    if not topics:
        rec = {"text": "Take your first quiz to start tracking your progress.", "topic": "", "difficulty": "easy"}
    elif weak:
        t = weak[0]
        difficulty = "easy" if t["mastery"] < 0.4 else "medium"
        rec = {"text": f"Review {t['topic']}, then take a quiz on it at {difficulty} difficulty.", "topic": t["topic"], "difficulty": difficulty}
    elif thin:
        t = thin[0]
        rec = {"text": f"Practice {t['topic']} a bit more to get a reliable score.", "topic": t["topic"], "difficulty": "medium"}
    else:
        rec = {"text": "No weak topics. Challenge yourself with a hard quiz.", "topic": "", "difficulty": "hard"}

    return {"topics": topics, "weak_topics": [t["topic"] for t in weak], "recommendation": rec}
