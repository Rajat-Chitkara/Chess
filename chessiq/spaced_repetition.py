"""
spaced_repetition.py — SM-2-style scheduling for puzzle review, plus the
"what should I practice today" selector that mixes:
  1. Puzzles due for review (spaced repetition queue)
  2. Fresh puzzles targeting your current weakest subareas
  3. Puzzles generated from your own recent blunders

This is what a `today_session()` call in main.py hits to build your
practice queue.
"""

from datetime import datetime, timedelta
from db import get_conn

DEFAULT_EASE = 2.5
MIN_EASE = 1.3


def record_attempt(puzzle_id: int, solved: bool, time_taken_sec: float = None, conn=None):
    """Logs an attempt and schedules the next review using a simplified SM-2:
      - solved: ease goes up slightly, interval multiplies by ease
      - failed: ease drops, interval resets to 1 day (see it again tomorrow)
    """
    own_conn = conn is None
    conn = conn or get_conn()

    last = conn.execute("""
        SELECT ease_factor, interval_days FROM puzzle_attempts
        WHERE puzzle_id = ? ORDER BY attempted_at DESC LIMIT 1
    """, (puzzle_id,)).fetchone()

    ease = last["ease_factor"] if last else DEFAULT_EASE
    interval = last["interval_days"] if last else 1

    if solved:
        ease = min(ease + 0.1, 3.0)
        interval = max(1, round(interval * ease))
    else:
        ease = max(MIN_EASE, ease - 0.2)
        interval = 1  # see it again tomorrow if you missed it

    next_review = (datetime.now() + timedelta(days=interval)).date().isoformat()

    conn.execute("""
        INSERT INTO puzzle_attempts (puzzle_id, solved, time_taken_sec,
                                      ease_factor, interval_days, next_review_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (puzzle_id, int(solved), time_taken_sec, ease, interval, next_review))

    conn.commit()
    if own_conn:
        conn.close()
    return {"ease": ease, "interval_days": interval, "next_review_at": next_review}


def due_for_review(conn=None, limit: int = 10, profile=None):
    """Puzzles whose most recent attempt's next_review_at has passed, limited to
    the given profile's puzzles."""
    own_conn = conn is None
    conn = conn or get_conn()

    today = datetime.now().date().isoformat()
    params = [today]
    profile_clause = ""
    if profile:
        profile_clause = " AND p.profile = ?"
        params.append(profile)
    params.append(limit)

    rows = conn.execute(f"""
        SELECT p.*, latest.next_review_at, latest.solved as last_solved
        FROM puzzles p
        JOIN (
            SELECT puzzle_id, next_review_at, solved,
                   ROW_NUMBER() OVER (PARTITION BY puzzle_id ORDER BY attempted_at DESC) as rn
            FROM puzzle_attempts
        ) latest ON latest.puzzle_id = p.id AND latest.rn = 1
        WHERE latest.next_review_at <= ?{profile_clause}
        ORDER BY latest.next_review_at ASC
        LIMIT ?
    """, params).fetchall()

    if own_conn:
        conn.close()
    return rows


def fresh_puzzles(limit: int = 10, conn=None, profile=None):
    """Puzzles never attempted before, for the given profile, prioritising the
    areas you're weakest in.

    Ordering, weakest first:
      1. your PUZZLE accuracy in that sub-area (from past attempts) — so themes
         you keep missing on puzzles resurface before ones you've mastered;
      2. then your GAME skill score for that area — so with no puzzle history
         yet, your in-game weaknesses still lead.

    Both the accuracy and the skill-score joins are scoped to the same profile
    so nothing bleeds across profiles.
    """
    own_conn = conn is None
    conn = conn or get_conn()

    params = [profile, profile]   # for the two scoped joins
    profile_clause = ""
    if profile:
        profile_clause = " AND p.profile = ?"
        params.append(profile)
    params.append(limit)

    rows = conn.execute(f"""
        SELECT p.*
        FROM puzzles p
        LEFT JOIN puzzle_attempts pa ON pa.puzzle_id = p.id
        LEFT JOIN skill_scores ss
               ON ss.profile = ? AND ss.area = p.skill_area AND ss.subarea = p.skill_subarea
        LEFT JOIN (
            SELECT pz.skill_area, pz.skill_subarea,
                   AVG(a.solved) AS acc
            FROM puzzle_attempts a
            JOIN puzzles pz ON pz.id = a.puzzle_id
            WHERE pz.profile = ?
            GROUP BY pz.skill_area, pz.skill_subarea
        ) pstat
               ON pstat.skill_area = p.skill_area AND pstat.skill_subarea = p.skill_subarea
        WHERE pa.id IS NULL{profile_clause}
        ORDER BY COALESCE(pstat.acc, 0.5) ASC, COALESCE(ss.score, 50) ASC
        LIMIT ?
    """, params).fetchall()

    if own_conn:
        conn.close()
    return rows


def today_session(review_slots: int = 6, fresh_slots: int = 6, conn=None, profile=None):
    """Builds today's practice queue for a profile: due reviews first (spaced
    repetition always takes priority — that's the whole point of SR), then fill
    the rest with fresh puzzles targeting weak areas."""
    own_conn = conn is None
    conn = conn or get_conn()

    review = due_for_review(conn=conn, limit=review_slots, profile=profile)
    fresh = fresh_puzzles(limit=fresh_slots, conn=conn, profile=profile)

    if own_conn:
        conn.close()

    return {
        "review": [dict(r) for r in review],
        "fresh": [dict(f) for f in fresh],
        "total": len(review) + len(fresh),
    }


if __name__ == "__main__":
    session = today_session()
    print(f"Today's session: {session['total']} puzzles "
          f"({len(session['review'])} review, {len(session['fresh'])} new)")
