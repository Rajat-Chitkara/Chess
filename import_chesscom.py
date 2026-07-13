"""
import_chesscom.py — pull your games straight from the chess.com public API.

chess.com exposes a free, no-auth, read-only "Published-Data" API:
  - archives list:  https://api.chess.com/pub/player/{user}/games/archives
  - one month:      https://api.chess.com/pub/player/{user}/games/{YYYY}/{MM}
Every game in a month includes a full `pgn` field, so we grab the most recent
games and hand the PGN text to the same importer used for manual uploads
(import_manual.parse_and_store_pgn_text) — which already handles chess.com
headers, de-duplication, profile tagging, opponent name, and time control.

Note: chess.com rejects requests that don't send a User-Agent header, so we
always set one.
"""

import requests

from import_manual import parse_and_store_pgn_text
from db import get_conn

ARCHIVES_URL = "https://api.chess.com/pub/player/{user}/games/archives"
HEADERS = {
    "User-Agent": "chessIQ/1.0 (personal chess trainer; "
                  "+https://github.com/Rajat-Chitkara/Chess)"
}


def fetch_pgns(username: str, max_games: int = 30, timeout: int = 30) -> str:
    """Return a concatenated PGN blob of the user's most recent `max_games` games."""
    user = username.strip().lower()
    resp = requests.get(ARCHIVES_URL.format(user=user), headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        raise ValueError(f"chess.com user '{username}' not found")
    resp.raise_for_status()
    archives = resp.json().get("archives", [])  # oldest -> newest month URLs

    pgns = []
    for archive_url in reversed(archives):          # start from the newest month
        m = requests.get(archive_url, headers=HEADERS, timeout=timeout)
        m.raise_for_status()
        games = m.json().get("games", [])           # oldest -> newest within a month
        for g in reversed(games):                   # newest game first
            pgn = g.get("pgn")
            if pgn:
                pgns.append(pgn)
            if len(pgns) >= max_games:
                break
        if len(pgns) >= max_games:
            break

    return "\n\n".join(pgns)


def import_chesscom(username: str, max_games: int = 30, conn=None, profile=None):
    """Fetch recent games from chess.com and store them. Returns
    (inserted, skipped, failed). Games are tagged with `profile` (defaults to
    `username`) and matched to it by the username in the PGN headers."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        blob = fetch_pgns(username, max_games)
        if not blob.strip():
            return 0, 0, 0
        return parse_and_store_pgn_text(blob, username, "chesscom", conn=conn, profile=profile)
    finally:
        if own_conn:
            conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python import_chesscom.py <chesscom_username> [max_games]")
        sys.exit(1)
    u = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    ins, skip, fail = import_chesscom(u, n)
    print(f"Imported {ins} new game(s), skipped {skip}, {fail} failed/not-yours.")
    print("Next: python main.py analyse")
