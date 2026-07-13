"""
puzzles_lichess.py — imports the Lichess open puzzle database and selects
puzzles targeted at your weakest skill areas.

The Lichess puzzle DB is a public CSV dump (puzzle id, FEN, moves, rating,
themes, ...), updated daily, ~3-4M puzzles, ~250MB compressed:
    https://database.lichess.org/lichess_db_puzzle.csv.zst

NOTE ON THIS SANDBOX: downloading that file needs internet access this
sandbox doesn't have. Run download_puzzle_db() on your own machine once
(takes a few minutes), then everything downstream (theme mapping, querying,
selection) runs entirely offline against the local file / SQLite.

--------------------------------------------------------------------------
Theme mapping: Lichess puzzle themes -> our skill_area/skill_subarea

Lichess puzzle themes (examples): fork, pin, skewer, discoveredAttack,
deflection, sacrifice, mateIn2, backRankMate, hangingPiece, endgame,
rookEndgame, pawnEndgame, queenEndgame, opening, middlegame, advantage,
crushing, master, ...

We map the subset that's directly useful for filling YOUR weak subareas.
"""

import csv
import io
import sqlite3
import subprocess
import zstandard
from pathlib import Path

from db import get_conn

PUZZLE_DB_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
LOCAL_ZST_PATH = Path(__file__).parent / "data" / "lichess_db_puzzle.csv.zst"
LOCAL_CSV_PATH = Path(__file__).parent / "data" / "lichess_db_puzzle.csv"

# Maps our (skill_area, skill_subarea) -> set of Lichess theme tags to search for.
# Used both directions: to import matching puzzles, and to backfill our own tags.
THEME_MAP = {
    ("tactics", "fork"):              {"fork"},
    ("tactics", "pin"):               {"pin"},
    ("tactics", "check_delivered"):   {"discoveredAttack", "doubleCheck"},
    ("tactics", "calculation"):       {"advancedPawn", "sacrifice", "deflection", "clearance"},
    ("tactics", "capture"):           {"hangingPiece", "trappedPiece"},
    ("positional", "outpost"):        {"middlegame"},  # lichess has no literal "outpost" tag; fall back to general middlegame
    ("positional", "open_file"):      {"middlegame"},
    ("positional", "pawn_structure"): {"pawnEndgame", "advancedPawn"},
    ("positional", "king_safety"):    {"kingsideAttack", "queensideAttack", "exposedKing"},
    ("positional", "plan"):           {"middlegame", "quietMove"},
    ("openings", "plan"):             {"opening"},
    ("openings", "theory"):           {"opening"},
    ("endgame", "rook_endgame"):      {"rookEndgame"},
    ("endgame", "pawn_endgame"):      {"pawnEndgame"},
    ("endgame", "queen_endgame"):     {"queenEndgame"},
    ("endgame", "minor_piece_endgame"): {"bishopEndgame", "knightEndgame"},
}


def download_puzzle_db():
    """One-time download + decompress of the Lichess puzzle CSV.
    Run this on your own machine (needs internet + ~1GB free disk)."""
    LOCAL_ZST_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not LOCAL_ZST_PATH.exists():
        print("Downloading Lichess puzzle DB (~250MB)...")
        subprocess.run(["curl", "-L", "-o", str(LOCAL_ZST_PATH), PUZZLE_DB_URL], check=True)

    if not LOCAL_CSV_PATH.exists():
        print("Decompressing...")
        dctx = zstandard.ZstdDecompressor()
        with open(LOCAL_ZST_PATH, "rb") as ifh, open(LOCAL_CSV_PATH, "wb") as ofh:
            dctx.copy_stream(ifh, ofh)

    print(f"Puzzle DB ready at {LOCAL_CSV_PATH}")


def _csv_reader():
    """Yields dict rows from the local puzzle CSV.
    Columns: PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,OpeningTags
    """
    if not LOCAL_CSV_PATH.exists():
        raise FileNotFoundError(
            "Puzzle CSV not found. Run download_puzzle_db() first (needs internet)."
        )
    with open(LOCAL_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, fieldnames=[
            "PuzzleId", "FEN", "Moves", "Rating", "RatingDeviation",
            "Popularity", "NbPlays", "Themes", "GameUrl", "OpeningTags",
        ])
        for row in reader:
            yield row


def import_puzzles_for_weak_areas(target_rating: int, rating_band: int = 150,
                                   per_subarea: int = 20, conn=None, profile=None):
    """Pulls a batch of puzzles from the local CSV matching each weak
    subarea's theme tags, filtered to roughly your rating, and inserts
    them into the `puzzles` table for the given profile.

    target_rating: your current puzzle rating (start around your game
                   Elo if you don't have a puzzle rating yet)
    profile:       which profile's weak areas to target and tag the puzzles with.
    """
    own_conn = conn is None
    conn = conn or get_conn()

    weak_params = []
    weak_profile_clause = ""
    if profile:
        weak_profile_clause = "profile = ? AND "
        weak_params.append(profile)
    weak = conn.execute(f"""
        SELECT area, subarea FROM skill_scores
        WHERE {weak_profile_clause}subarea != '_overall' AND sample_size >= 2
        ORDER BY score ASC LIMIT 5
    """, weak_params).fetchall()

    if not weak:
        print("No weak subareas found yet — analyse some games and run scoring.py first.")
        if own_conn:
            conn.close()
        return

    wanted_themes = set()
    for w in weak:
        wanted_themes |= THEME_MAP.get((w["area"], w["subarea"]), set())

    if not wanted_themes:
        print("No theme mapping found for current weak areas.")
        if own_conn:
            conn.close()
        return

    print(f"Searching puzzle DB for themes: {wanted_themes}")
    lo, hi = target_rating - rating_band, target_rating + rating_band
    found_per_theme = {t: 0 for t in wanted_themes}
    inserted = 0

    for row in _csv_reader():
        try:
            rating = int(row["Rating"])
        except (ValueError, TypeError):
            continue
        if not (lo <= rating <= hi):
            continue

        themes = set(row["Themes"].split())
        matched = themes & wanted_themes
        if not matched:
            continue

        theme = next(iter(matched))
        if found_per_theme.get(theme, 0) >= per_subarea:
            continue

        # find which of our (area,subarea) this theme maps back to
        area, subarea = None, None
        for (a, s), tset in THEME_MAP.items():
            if theme in tset:
                area, subarea = a, s
                break

        try:
            conn.execute("""
                INSERT INTO puzzles (profile, source, source_id, fen, solution_uci, rating,
                                      themes_json, skill_area, skill_subarea)
                VALUES (?, 'lichess', ?, ?, ?, ?, ?, ?, ?)
            """, (
                profile, row["PuzzleId"], row["FEN"], row["Moves"], rating,
                row["Themes"], area, subarea,
            ))
            inserted += 1
            found_per_theme[theme] = found_per_theme.get(theme, 0) + 1
        except Exception:
            continue  # already have this puzzle

        if all(v >= per_subarea for v in found_per_theme.values()):
            break

    conn.commit()
    if own_conn:
        conn.close()
    print(f"Inserted {inserted} targeted puzzles.")


if __name__ == "__main__":
    # Example flow (run on your own machine with internet access):
    #   1. download_puzzle_db()
    #   2. import_puzzles_for_weak_areas(target_rating=1350)
    print("This module is meant to be imported, or run download_puzzle_db() "
          "then import_puzzles_for_weak_areas() interactively / from main.py")
