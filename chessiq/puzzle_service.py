"""
puzzle_service.py — bridges the DB puzzle rows and the web frontend.

Two responsibilities:

  1. NORMALIZE the two puzzle sources, which store solutions differently:
       - own_game: `fen` is the position to solve; `solution_uci` is the
         solver's move(s), solver moves first.
       - lichess:  by Lichess puzzle-DB convention, `fen` is the position
         BEFORE a setup move. The first move in `solution_uci` is played by
         the opponent to reach the puzzle position; the solver plays from the
         second move onward.
     After normalization every puzzle looks the same to the UI: a board FEN
     the solver is looking at, whose turn it is, and a flat solution line
     whose EVEN indices (0,2,4,...) are the solver's moves.

  2. VALIDATE solver moves server-side (one move at a time) so the full
     solution never has to be shipped to the browser. The board and legality
     are owned by python-chess, the single source of truth.
"""

import chess

from db import get_conn
from spaced_repetition import today_session, record_attempt


def normalize_puzzle(row) -> dict:
    """Turn a `puzzles` row (sqlite3.Row or dict) into a UI-ready puzzle.

    Returns keys: puzzle_id, source, board_fen, side_to_move ('white'|'black'),
    solution (list[str] of UCI, solver moves at even indices), rating,
    skill_area, skill_subarea.
    """
    row = dict(row)
    moves = (row["solution_uci"] or "").split()

    if row["source"] == "lichess" and len(moves) >= 1:
        # Apply the opponent's setup move to reach the position the solver sees.
        board = chess.Board(row["fen"])
        try:
            board.push_uci(moves[0])
            board_fen = board.fen()
            solution = moves[1:]
        except (ValueError, chess.IllegalMoveError):
            # Malformed puzzle — fall back to treating fen as the solve position.
            board_fen = row["fen"]
            solution = moves
    else:
        board_fen = row["fen"]
        solution = moves

    side_to_move = "white" if chess.Board(board_fen).turn == chess.WHITE else "black"

    return {
        "puzzle_id": row["id"],
        "source": row["source"],
        "board_fen": board_fen,
        "side_to_move": side_to_move,
        "solution": solution,
        "rating": row["rating"],
        "skill_area": row["skill_area"],
        "skill_subarea": row["skill_subarea"],
    }


def public_puzzle(norm: dict) -> dict:
    """The subset of a normalized puzzle safe to send to the browser — no
    solution, so the answer can't be read out of the page source."""
    return {
        "puzzle_id": norm["puzzle_id"],
        "source": norm["source"],
        "board_fen": norm["board_fen"],
        "side_to_move": norm["side_to_move"],
        "num_moves": len(norm["solution"]),
        "rating": norm["rating"],
        "skill_area": norm["skill_area"],
        "skill_subarea": norm["skill_subarea"],
    }


def get_session(review_slots: int = 6, fresh_slots: int = 6, conn=None, profile=None) -> dict:
    """Today's practice queue for a profile, normalized and stripped for the client."""
    own_conn = conn is None
    conn = conn or get_conn()

    session = today_session(review_slots=review_slots, fresh_slots=fresh_slots,
                            conn=conn, profile=profile)
    review = [public_puzzle(normalize_puzzle(p)) for p in session["review"]]
    fresh = [public_puzzle(normalize_puzzle(p)) for p in session["fresh"]]

    if own_conn:
        conn.close()

    return {"review": review, "fresh": fresh, "total": len(review) + len(fresh)}


def _load_normalized(puzzle_id: int, conn) -> dict | None:
    row = conn.execute("SELECT * FROM puzzles WHERE id = ?", (puzzle_id,)).fetchone()
    return normalize_puzzle(row) if row else None


def evaluate_move(puzzle_id: int, ply_index: int, move_uci: str, conn=None) -> dict:
    """Check one solver move against the solution line.

    ply_index is the index into the normalized `solution` the solver is
    answering (even numbers only — the solver's turns).

    Returns:
      {ok: True, correct: bool, done: bool,
       opponent_reply: uci|None, expected: uci, next_ply: int}
    or {ok: False, error: str} if the puzzle/index is invalid.
    """
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        norm = _load_normalized(puzzle_id, conn)
        if norm is None:
            return {"ok": False, "error": "puzzle not found"}

        solution = norm["solution"]
        if not (0 <= ply_index < len(solution)):
            return {"ok": False, "error": "ply_index out of range"}

        expected = solution[ply_index]
        correct = (move_uci == expected)

        if not correct:
            return {
                "ok": True, "correct": False, "done": True,
                "opponent_reply": None, "expected": expected, "next_ply": ply_index,
            }

        # Correct. Was that the solver's final move in the line?
        done = ply_index >= len(solution) - 1
        opponent_reply = None
        next_ply = ply_index
        if not done:
            # The move immediately after the solver's is the forced opponent reply.
            opponent_reply = solution[ply_index + 1] if ply_index + 1 < len(solution) else None
            next_ply = ply_index + 2
            # If the opponent's reply is the last move, the puzzle is complete
            # once it's played (no further solver move remains).
            if next_ply >= len(solution):
                done = True

        return {
            "ok": True, "correct": True, "done": done,
            "opponent_reply": opponent_reply, "expected": expected, "next_ply": next_ply,
        }
    finally:
        if own_conn:
            conn.close()


def puzzle_stats(conn=None, profile=None) -> dict:
    """A profile's puzzle-solving accuracy, per skill area and sub-area, computed
    from the attempt log. This is separate from the game-based skill scores: it
    tells you which *puzzle* themes you actually solve vs. miss, and it also
    drives which fresh puzzles surface next (see spaced_repetition.fresh_puzzles).

    Accuracy counts every attempt (a puzzle seen 3 times = 3 data points), so
    it reflects recall under spaced repetition, not just first exposure.
    """
    own_conn = conn is None
    conn = conn or get_conn()
    prof_clause = " AND p.profile = ?" if profile else ""
    prof_params = [profile] if profile else []
    try:
        areas = conn.execute(f"""
            SELECT p.skill_area AS area,
                   COUNT(*) AS attempts,
                   COALESCE(SUM(pa.solved), 0) AS solved
            FROM puzzle_attempts pa
            JOIN puzzles p ON p.id = pa.puzzle_id
            WHERE p.skill_area IS NOT NULL{prof_clause}
            GROUP BY p.skill_area
            ORDER BY (CAST(COALESCE(SUM(pa.solved),0) AS REAL) / COUNT(*)) ASC
        """, prof_params).fetchall()

        subareas = conn.execute(f"""
            SELECT p.skill_area AS area, p.skill_subarea AS subarea,
                   COUNT(*) AS attempts,
                   COALESCE(SUM(pa.solved), 0) AS solved
            FROM puzzle_attempts pa
            JOIN puzzles p ON p.id = pa.puzzle_id
            WHERE p.skill_area IS NOT NULL{prof_clause}
            GROUP BY p.skill_area, p.skill_subarea
            HAVING COUNT(*) >= 2
            ORDER BY (CAST(COALESCE(SUM(pa.solved),0) AS REAL) / COUNT(*)) ASC
        """, prof_params).fetchall()

        def shape(rows):
            out = []
            for r in rows:
                r = dict(r)
                r["accuracy"] = round(100.0 * r["solved"] / r["attempts"], 1) if r["attempts"] else 0.0
                out.append(r)
            return out

        return {"areas": shape(areas), "subareas": shape(subareas)}
    finally:
        if own_conn:
            conn.close()


def get_hint(puzzle_id: int, ply_index: int, conn=None) -> dict:
    """Reveal only the from-square of the current solver move — enough of a
    nudge to help learning without giving the whole answer away."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        norm = _load_normalized(puzzle_id, conn)
        if norm is None or not (0 <= ply_index < len(norm["solution"])):
            return {"ok": False, "error": "no hint available"}
        return {"ok": True, "from": norm["solution"][ply_index][:2]}
    finally:
        if own_conn:
            conn.close()


def submit_result(puzzle_id: int, solved: bool, time_taken_sec: float = None, conn=None) -> dict:
    """Record a finished puzzle attempt and return the spaced-repetition schedule."""
    return record_attempt(puzzle_id, solved, time_taken_sec=time_taken_sec, conn=conn)
