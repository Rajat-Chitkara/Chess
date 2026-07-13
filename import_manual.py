"""
import_manual.py — imports games from PGN files you've manually exported
(e.g. from chess.com's "Download PGN" button on your game archive page,
or any single-game PGN you save yourself).

chess.com lets you export PGN two ways:
  1. Single game: open the game -> "..." menu -> Download -> saves a .pgn file
  2. Bulk: https://www.chess.com/games/archive/<your_username> -> select a
     month -> Download -> saves one .pgn file containing many games

Both work here — this script handles single-game and multi-game PGN files
the same way, since PGN format allows concatenating games in one file.

Usage:
    python3 import_manual.py your_chesscom_username path/to/file.pgn
    python3 import_manual.py your_chesscom_username path/to/pgn_folder/   # imports every .pgn in the folder
"""

import argparse
import hashlib
import io
import sqlite3
import sys
from pathlib import Path

import chess.pgn

from db import get_conn


def derive_source_id(headers, white, black, game) -> str:
    """Return a STABLE, per-game unique id for de-duplication.

    Prefer a real game id from the Link/Site URL (chess.com puts the numeric
    game id in Link; lichess puts an 8-char id in Site). Crucially, we reject a
    bare domain like 'Chess.com' — a dot means it's a hostname, not a game id —
    because otherwise every game in a file collides to the same id and all but
    the first get skipped as duplicates. When no usable id exists, hash the game
    content so re-imports still de-duplicate but distinct games never collide.
    """
    for key in ("Link", "Site"):
        raw = (headers.get(key, "") or "").strip()
        if not raw:
            continue
        tail = raw.rstrip("/").split("/")[-1]
        tail = tail.split("?")[0].split("#")[0]   # drop query/fragment, e.g. '?move=0'
        if tail and "." not in tail:               # a game id, not a bare hostname
            return tail

    moves_text = str(game.mainline_moves())
    basis = "|".join([white, black, headers.get("Date", ""), headers.get("UTCDate", ""),
                      headers.get("UTCTime", ""), headers.get("EndTime", ""), moves_text])
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def parse_and_store_pgn_text(pgn_text: str, username: str, source_label: str, conn=None, profile=None):
    """Same shape as import_lichess.parse_and_store, but source-agnostic —
    works for chess.com exports or any PGN with standard headers.

    The imported games are tagged with `profile` (defaults to `username`), so
    they show up under that profile in the UI.
    """
    own_conn = conn is None
    conn = conn or get_conn()
    profile = (profile or username).strip()
    conn.execute("INSERT OR IGNORE INTO profiles(name) VALUES (?)", (profile,))

    stream = io.StringIO(pgn_text)
    inserted = 0
    skipped = 0
    failed = 0

    while True:
        try:
            game = chess.pgn.read_game(stream)
        except Exception as e:
            print(f"  Warning: skipped a malformed game ({e})")
            failed += 1
            continue

        if game is None:
            break

        headers = game.headers
        white = headers.get("White", "")
        black = headers.get("Black", "")

        if white.lower() == username.lower():
            your_color = "white"
        elif black.lower() == username.lower():
            your_color = "black"
        else:
            print(f"  Warning: username '{username}' not found as White or Black "
                  f"(White='{white}', Black='{black}') — skipping this game. "
                  f"Check the --username spelling matches your chess.com handle exactly.")
            failed += 1
            continue

        source_id = derive_source_id(headers, white, black, game)

        played_at = headers.get("Date", headers.get("UTCDate", "")).replace(".", "-")
        time_control_raw = (headers.get("Event", "") + " " + headers.get("TimeControl", "")).lower()
        time_control = "unknown"
        # chess.com TimeControl is seconds-based (e.g. "600"), Event sometimes says "Live Chess"
        tc_seconds = headers.get("TimeControl", "").split("+")[0]
        try:
            secs = int(tc_seconds)
            if secs < 180:
                time_control = "bullet"
            elif secs < 600:
                time_control = "blitz"
            elif secs < 1800:
                time_control = "rapid"
            else:
                time_control = "classical"
        except ValueError:
            for tc in ("bullet", "blitz", "rapid", "classical", "daily", "correspondence"):
                if tc in time_control_raw:
                    time_control = tc
                    break

        your_rating = int(headers.get("WhiteElo" if your_color == "white" else "BlackElo", 0) or 0)
        opp_rating = int(headers.get("BlackElo" if your_color == "white" else "WhiteElo", 0) or 0)

        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        single_pgn = game.accept(exporter)

        try:
            opponent_name = black if your_color == "white" else white
            conn.execute("""
                INSERT INTO games (profile, source, source_id, pgn, played_at, time_control,
                                    your_color, your_rating, opponent_rating, opponent_name, result,
                                    opening_eco, opening_name, analysed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                profile, source_label, source_id, single_pgn, played_at.strip(), time_control,
                your_color, your_rating, opp_rating, opponent_name, headers.get("Result", ""),
                headers.get("ECO", ""), headers.get("Opening", headers.get("ECOUrl", "")),
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1  # UNIQUE(source, source_id) -> already imported

    conn.commit()
    if own_conn:
        conn.close()
    return inserted, skipped, failed


def import_path(username: str, path: str, source_label: str = "chesscom"):
    p = Path(path)
    if not p.exists():
        print(f"Path not found: {path}")
        sys.exit(1)

    pgn_files = [p] if p.is_file() else sorted(p.glob("*.pgn"))
    if not pgn_files:
        print(f"No .pgn files found at {path}")
        sys.exit(1)

    total_inserted, total_skipped, total_failed = 0, 0, 0
    conn = get_conn()

    for f in pgn_files:
        print(f"Reading {f.name}...")
        text = f.read_text(encoding="utf-8", errors="replace")
        ins, skip, fail = parse_and_store_pgn_text(text, username, source_label, conn=conn)
        total_inserted += ins
        total_skipped += skip
        total_failed += fail

    conn.close()
    print(f"\nDone: {total_inserted} imported, {total_skipped} already-imported (skipped), "
          f"{total_failed} failed/not-yours.")
    if total_inserted:
        print("Next: python3 main.py analyse")


def main():
    ap = argparse.ArgumentParser(description="Import chess.com (or any) exported PGN files")
    ap.add_argument("username", help="Your chess.com username, exactly as it appears in the PGN White/Black headers")
    ap.add_argument("path", help="A single .pgn file, or a folder containing .pgn files")
    ap.add_argument("--source-label", default="chesscom", help="Tag stored in the games.source column")
    args = ap.parse_args()

    import_path(args.username, args.path, args.source_label)


if __name__ == "__main__":
    main()
