"""
main.py — CLI entrypoint for chessIQ. Run subcommands as you build up your
data over time. Typical weekly loop:

    python3 main.py import <your_lichess_username>
    python3 main.py analyse
    python3 main.py score
    python3 main.py puzzles-own          # turn your own blunders into puzzles
    python3 main.py puzzles-lichess      # (one-time) python3 -c "from puzzles_lichess import download_puzzle_db; download_puzzle_db()"
                                          # then this pulls targeted puzzles for weak areas
    python3 main.py session              # see today's practice queue
    python3 main.py report               # full skill breakdown
"""

import argparse
import sys

from db import init_db
from import_lichess import fetch_games, parse_and_store
from import_manual import import_path
from analyse_game import analyse_all_pending
from scoring import compute_scores, print_report
from puzzles_own_games import generate_from_blunders
from puzzles_lichess import import_puzzles_for_weak_areas
from spaced_repetition import today_session


def cmd_init(args):
    init_db()


def cmd_import(args):
    print(f"Fetching games for {args.username}...")
    pgn_blob = fetch_games(args.username, args.max, args.token)
    inserted, skipped = parse_and_store(pgn_blob, args.username)
    print(f"Imported {inserted} new game(s), skipped {skipped} already-imported.")


def cmd_import_manual(args):
    import_path(args.username, args.path, args.source_label)


def cmd_analyse(args):
    analyse_all_pending(depth=args.depth, limit=args.limit)


def cmd_score(args):
    compute_scores()
    print_report()


def cmd_puzzles_own(args):
    generate_from_blunders(min_severity=args.min_severity)


def cmd_puzzles_lichess(args):
    import_puzzles_for_weak_areas(target_rating=args.rating)


def cmd_session(args):
    session = today_session(review_slots=args.review, fresh_slots=args.fresh)
    print(f"\n=== Today's session: {session['total']} puzzles ===\n")
    if session["review"]:
        print(f"-- Review ({len(session['review'])}) --")
        for p in session["review"]:
            print(f"  [{p['skill_area']}/{p['skill_subarea']}] rating~{p['rating']}  fen={p['fen'][:40]}...")
    if session["fresh"]:
        print(f"\n-- New ({len(session['fresh'])}) --")
        for p in session["fresh"]:
            print(f"  [{p['skill_area']}/{p['skill_subarea']}] rating~{p['rating']}  fen={p['fen'][:40]}...")
    if session["total"] == 0:
        print("No puzzles queued yet. Run: puzzles-own and/or puzzles-lichess first.")


def cmd_report(args):
    print_report()


def build_parser():
    p = argparse.ArgumentParser(description="chessIQ — personal chess improvement backend")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create the database").set_defaults(func=cmd_init)

    p_import = sub.add_parser("import", help="Import games from Lichess")
    p_import.add_argument("username")
    p_import.add_argument("--max", type=int, default=100)
    p_import.add_argument("--token", default=None)
    p_import.set_defaults(func=cmd_import)

    p_import_manual = sub.add_parser("import-manual", help="Import chess.com (or any) PGN file(s) you've downloaded")
    p_import_manual.add_argument("username", help="Your username exactly as it appears in the PGN headers")
    p_import_manual.add_argument("path", help="A .pgn file, or a folder of .pgn files")
    p_import_manual.add_argument("--source-label", default="chesscom")
    p_import_manual.set_defaults(func=cmd_import_manual)

    p_analyse = sub.add_parser("analyse", help="Run Stockfish analysis on pending games")
    p_analyse.add_argument("--depth", type=int, default=18)
    p_analyse.add_argument("--limit", type=int, default=None)
    p_analyse.set_defaults(func=cmd_analyse)

    sub.add_parser("score", help="Recompute skill scores and print report").set_defaults(func=cmd_score)

    p_pown = sub.add_parser("puzzles-own", help="Generate puzzles from your own blunders")
    p_pown.add_argument("--min-severity", choices=["mistake", "blunder"], default="mistake")
    p_pown.set_defaults(func=cmd_puzzles_own)

    p_plichess = sub.add_parser("puzzles-lichess", help="Pull targeted puzzles from Lichess DB for weak areas")
    p_plichess.add_argument("--rating", type=int, required=True, help="Your approx puzzle rating")
    p_plichess.set_defaults(func=cmd_puzzles_lichess)

    p_session = sub.add_parser("session", help="Show today's practice queue")
    p_session.add_argument("--review", type=int, default=6)
    p_session.add_argument("--fresh", type=int, default=6)
    p_session.set_defaults(func=cmd_session)

    sub.add_parser("report", help="Print current skill score report").set_defaults(func=cmd_report)

    return p


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
