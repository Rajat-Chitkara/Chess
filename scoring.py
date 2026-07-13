"""
scoring.py — aggregates analysed moves into 0-100 skill scores per area/subarea.

Model:
    score = 100 * exp(-avg_cpl / K)

  This maps average centipawn loss to a 0-100 score with diminishing
  penalty at the high end (a 20 -> 40 avg CPL jump hurts less than a
  0 -> 20 jump), which matches how chess mistakes actually feel: going
  from perfect to slightly imprecise is a bigger relative change than
  going from sloppy to very sloppy.

  K=100 means: avg_cpl=0 -> 100, avg_cpl=70 -> ~50, avg_cpl=230 -> ~10.
  Tune K in TUNING below if scores feel too harsh/lenient once you have
  real games in the system.

  Only rows where is_your_move=1 count — we only score YOUR moves, not
  the opponent's.

Run after every analyse_game.py pass. Also writes a snapshot into
skill_score_history so you can chart trend over time.
"""

import math
from db import get_conn

K = 100  # decay constant, see docstring
MIN_SAMPLE_FOR_SCORE = 3  # below this many moves, area is 'insufficient data'

# Skill areas the classifier actually assigns. 'middlegame' is intentionally
# absent — middlegame themes are scored as subareas under tactics/positional
# (see classifier.classify_move), so it would only double-count moves here.
AREAS = ["openings", "tactics", "positional", "endgame"]


def cpl_to_score(avg_cpl: float) -> float:
    return round(100 * math.exp(-avg_cpl / K), 1)


def compute_scores(conn=None):
    """Recompute skill scores for every profile from its own games' moves.

    Scores are always grouped by games.profile so each profile's numbers are
    isolated. Only your moves (is_your_move = 1) count.
    """
    own_conn = conn is None
    conn = conn or get_conn()

    # subarea-level aggregation, per profile
    rows = conn.execute("""
        SELECT g.profile AS profile, m.skill_area, m.skill_subarea,
               AVG(m.cpl) AS avg_cpl, COUNT(*) AS n
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.is_your_move = 1 AND m.cpl IS NOT NULL AND g.profile IS NOT NULL
        GROUP BY g.profile, m.skill_area, m.skill_subarea
    """).fetchall()

    subarea_scores = {}
    for r in rows:
        score = cpl_to_score(r["avg_cpl"])
        subarea_scores[(r["profile"], r["skill_area"], r["skill_subarea"])] = (score, r["n"])
        conn.execute("""
            INSERT INTO skill_scores (profile, area, subarea, score, sample_size, last_updated)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(profile, area, subarea) DO UPDATE SET
                score = excluded.score,
                sample_size = excluded.sample_size,
                last_updated = excluded.last_updated
        """, (r["profile"], r["skill_area"], r["skill_subarea"], score, r["n"]))
        conn.execute("""
            INSERT INTO skill_score_history (profile, area, subarea, score)
            VALUES (?, ?, ?, ?)
        """, (r["profile"], r["skill_area"], r["skill_subarea"], score))

    # area-level aggregation (the '_overall' row per area, used for the dashboard/radar)
    area_rows = conn.execute("""
        SELECT g.profile AS profile, m.skill_area,
               AVG(m.cpl) AS avg_cpl, COUNT(*) AS n
        FROM moves m
        JOIN games g ON g.id = m.game_id
        WHERE m.is_your_move = 1 AND m.cpl IS NOT NULL AND g.profile IS NOT NULL
        GROUP BY g.profile, m.skill_area
    """).fetchall()

    area_scores = {}
    for r in area_rows:
        score = cpl_to_score(r["avg_cpl"])
        area_scores[(r["profile"], r["skill_area"])] = (score, r["n"])
        conn.execute("""
            INSERT INTO skill_scores (profile, area, subarea, score, sample_size, last_updated)
            VALUES (?, ?, '_overall', ?, ?, datetime('now'))
            ON CONFLICT(profile, area, subarea) DO UPDATE SET
                score = excluded.score,
                sample_size = excluded.sample_size,
                last_updated = excluded.last_updated
        """, (r["profile"], r["skill_area"], score, r["n"]))
        conn.execute("""
            INSERT INTO skill_score_history (profile, area, subarea, score)
            VALUES (?, ?, '_overall', ?)
        """, (r["profile"], r["skill_area"], score))

    conn.commit()
    if own_conn:
        conn.close()

    return {"areas": area_scores, "subareas": subarea_scores}


def print_report(profile=None, conn=None):
    """Print skill scores. With no profile, prints a report for each profile
    that has any scores."""
    own_conn = conn is None
    conn = conn or get_conn()

    if profile is None:
        profiles = [r["profile"] for r in conn.execute(
            "SELECT DISTINCT profile FROM skill_scores ORDER BY profile").fetchall()]
        if not profiles:
            print("No scores yet — analyse some games first (analyse_game.py).")
            if own_conn:
                conn.close()
            return
        for p in profiles:
            print_report(profile=p, conn=conn)
        if own_conn:
            conn.close()
        return

    print(f"\n=== Skill scores — {profile} ===")
    areas = conn.execute("""
        SELECT area, score, sample_size FROM skill_scores
        WHERE profile = ? AND subarea = '_overall' ORDER BY score ASC
    """, (profile,)).fetchall()

    if not areas:
        print("  No scores yet.")
        if own_conn:
            conn.close()
        return

    for a in areas:
        flag = " (low sample)" if a["sample_size"] < MIN_SAMPLE_FOR_SCORE else ""
        print(f"  {a['area']:14} {a['score']:5.1f} / 100   [{a['sample_size']} moves]{flag}")

    print("  -- Weakest sub-areas (top 5, min 2 samples) --")
    subs = conn.execute("""
        SELECT area, subarea, score, sample_size FROM skill_scores
        WHERE profile = ? AND subarea != '_overall' AND sample_size >= 2
        ORDER BY score ASC LIMIT 5
    """, (profile,)).fetchall()
    for s in subs:
        print(f"  {s['area']:12} / {s['subarea']:20} {s['score']:5.1f}   [{s['sample_size']} moves]")

    if own_conn:
        conn.close()


if __name__ == "__main__":
    compute_scores()
    print_report()
