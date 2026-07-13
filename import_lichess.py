"""
import_lichess.py — pulls your games from the Lichess API and stores them
in the `games` table, unanalysed (analyse_game.py handles the rest).

Requires: your Lichess username. A personal API token is optional but
recommended (higher rate limits, and required if you want to fetch games
from a private/friends-only account). Get one at:
    https://lichess.org/account/oauth/token
    (no scopes needed for reading public games)

Usage:
    python3 import_lichess.py <your_lichess_username> [--max 100] [--token YOUR_TOKEN]

NOTE ON THIS SANDBOX: this script needs outbound internet access to
lichess.org, which this development sandbox does not have. Run it on your
own machine where python-chess, requests and internet access are all
available — everything else in the project (engine.py, classifier.py,
scoring.py) has already been tested standalone in-sandbox.
"""

import argparse
import sqlite3
import requests
import chess.pgn
import io

from db import get_conn

LICHESS_EXPORT_URL = "https://lichess.org/api/games/user/{username}"


def fetch_games(username: str, max_games: int = 100, token: str = None):
    """Streams games as PGN text from the Lichess export endpoint.
    Lichess returns newline-delimited PGN blocks for this endpoint.
    """
    headers = {"Accept": "application/x-chess-pgn"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    params = {
        "max": max_games,
        "opening": "true",
        "clocks": "false",
        "evals": "false",     # we run our own Stockfish pass, don't need Lichess's
        "pgnInJson": "false",
    }

    resp = requests.get(
        LICHESS_EXPORT_URL.format(username=username),
        headers=headers, params=params, stream=True, timeout=60,
    )
    resp.raise_for_status()
    return resp.text  # concatenated PGN, games separated by blank lines


def parse_and_store(pgn_blob: str, username: str, conn=None, profile=None):
    own_conn = conn is None
    conn = conn or get_conn()
    profile = (profile or username).strip()
    conn.execute("INSERT OR IGNORE INTO profiles(name) VALUES (?)", (profile,))

    stream = io.StringIO(pgn_blob)
    inserted = 0
    skipped = 0

    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break

        headers = game.headers
        white = headers.get("White", "")
        black = headers.get("Black", "")
        your_color = "white" if white.lower() == username.lower() else "black"

        source_id = headers.get("Site", "").rstrip("/").split("/")[-1] or None
        played_at = headers.get("UTCDate", "") + " " + headers.get("UTCTime", "")
        time_control_raw = headers.get("Event", "")
        # Lichess Event header looks like "Rated blitz game" — extract the speed
        time_control = "unknown"
        for tc in ("bullet", "blitz", "rapid", "classical", "correspondence"):
            if tc in time_control_raw.lower():
                time_control = tc
                break

        your_rating = int(headers.get("WhiteElo" if your_color == "white" else "BlackElo", 0) or 0)
        opp_rating = int(headers.get("BlackElo" if your_color == "white" else "WhiteElo", 0) or 0)

        # re-serialize just this game's PGN text for storage
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        single_pgn = game.accept(exporter)

        try:
            opponent_name = black if your_color == "white" else white
            conn.execute("""
                INSERT INTO games (profile, source, source_id, pgn, played_at, time_control,
                                    your_color, your_rating, opponent_rating, opponent_name, result,
                                    opening_eco, opening_name, analysed)
                VALUES (?, 'lichess', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                profile, source_id, single_pgn, played_at.strip(), time_control,
                your_color, your_rating, opp_rating, opponent_name, headers.get("Result", ""),
                headers.get("ECO", ""), headers.get("Opening", ""),
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1  # UNIQUE constraint on (source, source_id) -> already imported

    conn.commit()
    if own_conn:
        conn.close()
    return inserted, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("username", help="Your Lichess username")
    ap.add_argument("--max", type=int, default=100, help="Max games to fetch")
    ap.add_argument("--token", default=None, help="Lichess personal API token (optional)")
    args = ap.parse_args()

    print(f"Fetching up to {args.max} games for {args.username}...")
    pgn_blob = fetch_games(args.username, args.max, args.token)
    inserted, skipped = parse_and_store(pgn_blob, args.username)
    print(f"Imported {inserted} new game(s), skipped {skipped} already-imported.")
    print("Next: python3 analyse_game.py")


if __name__ == "__main__":
    main()
