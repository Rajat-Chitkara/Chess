"""
db.py — SQLite connection helper for chessIQ.

Usage:
    from db import get_conn, init_db
    init_db()                     # run once, creates chessiq.db from schema.sql
    conn = get_conn()
    conn.execute(...)

init_db() also runs a small idempotent migration so databases created before
profiles existed get upgraded in place (profiles table, per-profile columns,
and data backfill).
"""

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "chessiq.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

# The profiles chessIQ ships with. A profile name is the player's username.
DEFAULT_PROFILES = ["Stock-Fish24", "rohitkparida", "ShawttyBad"]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait up to 5s instead of erroring if another connection (e.g. the web
    # app's background analysis thread) holds a write lock.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _has_column(conn, table, col) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _table_exists(conn, table) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _backfill_game_profiles(conn):
    """Assign each existing game to a profile by reading the player name on the
    side you played (your_color) from the stored PGN."""
    import io
    import chess.pgn
    for r in conn.execute("SELECT id, your_color, pgn FROM games WHERE profile IS NULL").fetchall():
        owner = None
        try:
            g = chess.pgn.read_game(io.StringIO(r["pgn"]))
            owner = g.headers.get("White") if r["your_color"] == "white" else g.headers.get("Black")
        except Exception:
            pass
        owner = (owner or DEFAULT_PROFILES[0]).strip()
        conn.execute("INSERT OR IGNORE INTO profiles(name) VALUES (?)", (owner,))
        conn.execute("UPDATE games SET profile = ? WHERE id = ?", (owner, r["id"]))


def _backfill_opponent_names(conn):
    """Fill opponent_name for existing games from the stored PGN (the side you
    did NOT play)."""
    import io
    import chess.pgn
    for r in conn.execute("SELECT id, your_color, pgn FROM games WHERE opponent_name IS NULL").fetchall():
        opp = None
        try:
            g = chess.pgn.read_game(io.StringIO(r["pgn"]))
            if g is not None:
                opp = g.headers.get("Black") if r["your_color"] == "white" else g.headers.get("White")
        except Exception:
            pass
        conn.execute("UPDATE games SET opponent_name = ? WHERE id = ?", (opp, r["id"]))


def _rebuild_puzzles_with_profile(conn):
    """Rebuild the puzzles table to add `profile` and widen the uniqueness key to
    (profile, source, source_id), preserving ids so puzzle_attempts FKs stay valid."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("""
        CREATE TABLE puzzles_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile TEXT, source TEXT NOT NULL, source_id TEXT,
            fen TEXT NOT NULL, solution_uci TEXT NOT NULL, rating INTEGER,
            themes_json TEXT, skill_area TEXT, skill_subarea TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(profile, source, source_id)
        )
    """)
    conn.execute("""
        INSERT INTO puzzles_new (id, profile, source, source_id, fen, solution_uci,
                                 rating, themes_json, skill_area, skill_subarea, added_at)
        SELECT id, NULL, source, source_id, fen, solution_uci,
               rating, themes_json, skill_area, skill_subarea, added_at FROM puzzles
    """)
    conn.execute("DROP TABLE puzzles")
    conn.execute("ALTER TABLE puzzles_new RENAME TO puzzles")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_puzzles_profile ON puzzles(profile)")
    conn.execute("PRAGMA foreign_keys = ON")


def _backfill_puzzle_profiles(conn):
    """own_game puzzles (source_id 'gameid:ply') inherit their game's profile;
    anything else defaults to the first profile."""
    for r in conn.execute("SELECT id, source, source_id FROM puzzles WHERE profile IS NULL").fetchall():
        prof = None
        if r["source"] == "own_game" and r["source_id"] and ":" in r["source_id"]:
            gid = r["source_id"].split(":")[0]
            row = conn.execute("SELECT profile FROM games WHERE id = ?", (gid,)).fetchone()
            prof = row["profile"] if row else None
        conn.execute("UPDATE puzzles SET profile = ? WHERE id = ?",
                     (prof or DEFAULT_PROFILES[0], r["id"]))


def migrate(conn):
    """Idempotent upgrade to the profile-aware schema."""
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles (
        name TEXT PRIMARY KEY, created_at TEXT DEFAULT (datetime('now')))""")
    for p in DEFAULT_PROFILES:
        conn.execute("INSERT OR IGNORE INTO profiles(name) VALUES (?)", (p,))

    if _table_exists(conn, "games") and not _has_column(conn, "games", "profile"):
        conn.execute("ALTER TABLE games ADD COLUMN profile TEXT")
        _backfill_game_profiles(conn)

    if _table_exists(conn, "games") and not _has_column(conn, "games", "opponent_name"):
        conn.execute("ALTER TABLE games ADD COLUMN opponent_name TEXT")
        _backfill_opponent_names(conn)

    if _table_exists(conn, "puzzles") and not _has_column(conn, "puzzles", "profile"):
        _rebuild_puzzles_with_profile(conn)
        _backfill_puzzle_profiles(conn)

    # skill_scores gains profile in its primary key -> rebuild (scores are
    # recomputable from moves, so we just drop and let scoring repopulate).
    if _table_exists(conn, "skill_scores") and not _has_column(conn, "skill_scores", "profile"):
        conn.execute("DROP TABLE skill_scores")
        conn.execute("""CREATE TABLE skill_scores (
            profile TEXT NOT NULL, area TEXT NOT NULL, subarea TEXT NOT NULL,
            score REAL NOT NULL, sample_size INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (profile, area, subarea))""")

    if _table_exists(conn, "skill_score_history") and not _has_column(conn, "skill_score_history", "profile"):
        conn.execute("ALTER TABLE skill_score_history ADD COLUMN profile TEXT")

    conn.commit()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    migrate(conn)
    conn.close()
    print(f"Database ready at {DB_PATH}")


if __name__ == "__main__":
    init_db()
