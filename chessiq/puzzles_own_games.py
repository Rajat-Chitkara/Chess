"""
puzzles_own_games.py — turns your own blunders/mistakes into puzzles.
These are the highest-value puzzles in the system: the exact position
where you personally went wrong, re-presented as "find the best move."

A puzzle is generated for every move you made (is_your_move=1) with
severity 'blunder' or 'mistake' that isn't already in the puzzles table.
"""

from db import get_conn

# A blunder only makes a good puzzle if the position was still *contested* when
# you went wrong. If you were already lost, the engine's "best" move is just the
# least-bad flail (and can look like a blunder itself); if you were already
# winning big, any sensible move keeps the win. Both make confusing puzzles, so
# we only keep positions inside this eval band, measured from the mover's POV.
PUZZLE_MIN_EVAL = -150   # cp; below this you were already losing -> skip
PUZZLE_MAX_EVAL = 600    # cp; above this you were already winning decisively -> skip


def generate_from_blunders(min_severity: str = "mistake", conn=None, profile=None):
    """min_severity: 'mistake' includes mistakes+blunders, 'blunder' is blunders only.

    profile: limit to one profile's games (the web app always passes the current
    profile). None generates across all profiles, tagging each puzzle with its
    own game's profile.
    """
    own_conn = conn is None
    conn = conn or get_conn()

    severities = ["mistake", "blunder"] if min_severity == "mistake" else ["blunder"]
    placeholders = ",".join("?" * len(severities))
    params = list(severities)
    profile_clause = ""
    if profile:
        profile_clause = " AND g.profile = ?"
        params.append(profile)

    rows = conn.execute(f"""
        SELECT g.profile AS profile, m.game_id, m.ply, m.side, m.fen_before, m.best_move_uci,
               m.skill_area, m.skill_subarea, m.cpl, m.severity, m.eval_before_cp
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.is_your_move = 1 AND m.severity IN ({placeholders})
        AND m.best_move_uci IS NOT NULL{profile_clause}
    """, params).fetchall()

    inserted = 0
    skipped_decided = 0
    for r in rows:
        # Skip positions that were already decided (mover's POV eval out of band).
        ev = r["eval_before_cp"]
        if ev is not None:
            mover_eval = ev if r["side"] == "white" else -ev
            if mover_eval < PUZZLE_MIN_EVAL or mover_eval > PUZZLE_MAX_EVAL:
                skipped_decided += 1
                continue

        source_id = f"{r['game_id']}:{r['ply']}"
        # rough difficulty proxy: higher cpl the blunder was, the more "obvious"
        # the punishing tactic likely is, so we invert it a bit for a rating estimate
        est_rating = max(800, 1600 - r["cpl"])
        try:
            conn.execute("""
                INSERT INTO puzzles (profile, source, source_id, fen, solution_uci, rating,
                                      themes_json, skill_area, skill_subarea)
                VALUES (?, 'own_game', ?, ?, ?, ?, '[]', ?, ?)
            """, (
                r["profile"], source_id, r["fen_before"], r["best_move_uci"], est_rating,
                r["skill_area"], r["skill_subarea"],
            ))
            inserted += 1
        except Exception:
            continue  # already generated for this move

    conn.commit()
    if own_conn:
        conn.close()
    print(f"Generated {inserted} puzzle(s) from your own {min_severity}s "
          f"(skipped {skipped_decided} from already-decided positions).")
    return inserted


if __name__ == "__main__":
    generate_from_blunders()
