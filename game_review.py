"""
game_review.py — turns the analysed `moves` rows into a chess.com-style
"Game Review": a per-move quality label (Book / Best / Good / Inaccuracy /
Mistake / Blunder), a short plain-English comment, and a formatted evaluation.

Everything here is heuristic — derived from data we already store (severity,
whether you found the engine's top move, phase, and the eval swing). No engine
calls and no LLM, so it's instant and offline. It's the same information a
coach panel shows, just generated from rules instead of prose models.
"""

# Quality labels, best -> worst, each with a css class and a badge glyph.
QUALITY = {
    "best":       {"label": "Best move",  "cls": "q-best",  "glyph": "✓"},   # ✓
    "book":       {"label": "Book move",  "cls": "q-book",  "glyph": "\U0001F4D6"}, # 📖
    "good":       {"label": "Good",       "cls": "q-good",  "glyph": "●"},    # ●
    "inaccuracy": {"label": "Inaccuracy", "cls": "q-inacc", "glyph": "?!"},
    "mistake":    {"label": "Mistake",    "cls": "q-mist",  "glyph": "?"},
    "blunder":    {"label": "Blunder",    "cls": "q-blun",  "glyph": "??"},
}


def eval_str(cp) -> str:
    """White-POV centipawns -> a chess.com-style eval string (+0.24, -1.8, M)."""
    if cp is None:
        return ""
    if abs(cp) >= 99000:
        return "M" if cp > 0 else "-M"
    v = cp / 100.0
    return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"


def _quality_key(move: dict) -> str:
    sev = move.get("severity")
    if sev == "blunder":
        return "blunder"
    if sev == "mistake":
        return "mistake"
    if sev == "inaccuracy":
        return "inaccuracy"
    # Not a mistake — was it the engine's top choice?
    is_best = move.get("move_uci") and move["move_uci"] == move.get("best_move_uci")
    if move.get("phase") == "opening":
        # In the opening we don't have a theory book, so treat solid opening
        # moves as "book" — the same signal chess.com's book icon conveys.
        return "book"
    if is_best:
        return "best"
    return "good"


def _comment(move: dict, key: str) -> str:
    san = move.get("move_san") or "This move"
    best = move.get("best_move_san")
    if key == "book":
        return f"{san} is a book move — a solid, well-known opening choice."
    if key == "best":
        return f"{san} is the best move in the position."
    if key == "good":
        return f"{san} is a good move that keeps the position healthy."
    if key == "inaccuracy":
        tail = f" {best} was a touch more precise." if best else ""
        return f"{san} is a slight inaccuracy.{tail}"
    if key == "mistake":
        tail = f" {best} was stronger." if best else ""
        return f"{san} is a mistake — it gives away some of your advantage.{tail}"
    if key == "blunder":
        tail = f" {best} was much better." if best else ""
        return f"{san} is a blunder, losing significant ground.{tail}"
    return f"{san}."


def annotate(moves: list) -> list:
    """Enrich each move dict in place with review fields and return the list.

    Adds: quality (key), q_label, q_cls, q_glyph, q_comment, eval_after_str.
    """
    for m in moves:
        key = _quality_key(m)
        q = QUALITY[key]
        m["quality"] = key
        m["q_label"] = q["label"]
        m["q_cls"] = q["cls"]
        m["q_glyph"] = q["glyph"]
        m["q_comment"] = _comment(m, key)
        m["eval_after_str"] = eval_str(m.get("eval_after_cp"))
    return moves


def summarize_quality(moves: list) -> dict:
    """Count your moves by quality label, for the review header."""
    out = {k: 0 for k in QUALITY}
    for m in moves:
        if m.get("is_your_move") and m.get("quality") in out:
            out[m["quality"]] += 1
    return out
