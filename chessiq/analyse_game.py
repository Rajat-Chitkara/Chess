"""
analyse_game.py — runs the full per-game analysis pipeline:

  PGN -> walk every position -> Stockfish eval before/after each move
      -> compute centipawn loss -> classify_phase / classify_move
      -> write one row per ply into `moves`
      -> mark game as analysed

This is the most expensive step (one Stockfish call per ply), so it's built
to run as a batch job over all un-analysed games in the DB.

Severity thresholds (centipawn loss, from the mover's own POV):
    >= 150   blunder
    >= 50    mistake
    >= 20    inaccuracy
    else     ok / good
"""

import io
import json
import chess
import chess.pgn

from db import get_conn
from engine import Analyser, MATE_SCORE
from classifier import classify_phase, classify_move

BLUNDER_CP = 150
MISTAKE_CP = 50
INACCURACY_CP = 20


def severity_for_cpl(cpl: int) -> str:
    if cpl >= BLUNDER_CP:
        return "blunder"
    if cpl >= MISTAKE_CP:
        return "mistake"
    if cpl >= INACCURACY_CP:
        return "inaccuracy"
    if cpl <= 0:
        return "good"
    return "ok"


def analyse_pgn_string(pgn_text: str, your_color: str, analyser: Analyser):
    """Walks a single game's PGN and returns a list of move-row dicts ready
    for insertion. `your_color` is 'white' or 'black' — only used to flag
    is_your_move; both sides get fully analysed regardless (useful context
    for e.g. seeing what your opponent missed too, though scoring only
    uses is_your_move=1 rows).
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("Could not parse PGN")

    board = game.board()
    rows = []
    ply = 0
    move_number = 1

    # eval of the starting position, needed as "before" for move 1
    prev_eval = analyser.analyse_fen(board.fen())["cp"]

    for move in game.mainline_moves():
        ply += 1
        side = "white" if board.turn == chess.WHITE else "black"
        is_your_move = 1 if side == your_color else 0
        fen_before = board.fen()

        # best move BEFORE this move is played (what should have happened)
        pre_info = analyser.analyse_fen(fen_before)
        best_move_uci = pre_info["best_move_uci"]
        best_move_san = pre_info["best_move_san"]

        move_san = board.san(move)
        move_uci = move.uci()

        board.push(move)
        fen_after = board.fen()
        post_info = analyser.analyse_fen(fen_after)

        eval_before = prev_eval               # white-POV cp, before this move
        eval_after = post_info["cp"]           # white-POV cp, after this move
        prev_eval = eval_after

        # Convert to "loss from the mover's perspective" — a move is bad if
        # it makes the position worse FOR THE SIDE THAT JUST MOVED.
        if side == "white":
            cpl = max(0, eval_before - eval_after)
        else:
            # eval is always white-POV. Black loses ground when the eval RISES
            # (gets better for white), so Black's loss = eval_after - eval_before.
            cpl = max(0, eval_after - eval_before)

        # clamp absurd mate-adjacent swings so one blown mate doesn't dominate scoring
        cpl = min(cpl, 1000)

        severity = severity_for_cpl(cpl)

        # classify using the position BEFORE the move, and the best move that was missed
        board.pop()  # step back to fen_before for classification context
        best_move_obj = chess.Move.from_uci(best_move_uci) if best_move_uci else move
        cls = classify_move(board, best_move_obj, move_number)
        board.push(move)  # replay forward again

        rows.append({
            "ply": ply,
            "move_number": move_number,
            "side": side,
            "is_your_move": is_your_move,
            "fen_before": fen_before,
            "fen_after": fen_after,
            "move_san": move_san,
            "move_uci": move_uci,
            "best_move_uci": best_move_uci,
            "best_move_san": best_move_san,
            "eval_before_cp": eval_before,
            "eval_after_cp": eval_after,
            "cpl": cpl,
            "severity": severity,
            "phase": cls["phase"],
            "move_type": cls["move_type"],
            "skill_area": cls["skill_area"],
            "skill_subarea": cls["skill_subarea"],
            "tags_json": json.dumps(cls["tags"]),
        })

        if side == "black":
            move_number += 1

    return rows


def analyse_game_by_id(game_id: int, analyser: Analyser, conn=None):
    own_conn = conn is None
    conn = conn or get_conn()

    row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        raise ValueError(f"No game with id {game_id}")
    if row["analysed"]:
        print(f"Game {game_id} already analysed, skipping")
        if own_conn:
            conn.close()
        return

    move_rows = analyse_pgn_string(row["pgn"], row["your_color"], analyser)

    for mr in move_rows:
        conn.execute("""
            INSERT INTO moves (
                game_id, ply, move_number, side, is_your_move,
                fen_before, fen_after, move_san, move_uci,
                best_move_uci, best_move_san,
                eval_before_cp, eval_after_cp, cpl, severity,
                phase, move_type, skill_area, skill_subarea, tags_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            game_id, mr["ply"], mr["move_number"], mr["side"], mr["is_your_move"],
            mr["fen_before"], mr["fen_after"], mr["move_san"], mr["move_uci"],
            mr["best_move_uci"], mr["best_move_san"],
            mr["eval_before_cp"], mr["eval_after_cp"], mr["cpl"], mr["severity"],
            mr["phase"], mr["move_type"], mr["skill_area"], mr["skill_subarea"], mr["tags_json"],
        ))

    conn.execute("UPDATE games SET analysed = 1 WHERE id = ?", (game_id,))
    conn.commit()
    print(f"Game {game_id}: analysed {len(move_rows)} plies")

    if own_conn:
        conn.close()


def analyse_all_pending(depth: int = 18, limit: int = None):
    """Batch entrypoint: analyse every game where analysed = 0."""
    conn = get_conn()
    query = "SELECT id FROM games WHERE analysed = 0 ORDER BY id"
    if limit:
        query += f" LIMIT {limit}"
    pending = [r["id"] for r in conn.execute(query).fetchall()]

    if not pending:
        print("No pending games to analyse.")
        conn.close()
        return

    print(f"Analysing {len(pending)} game(s) at depth {depth}...")
    with Analyser(depth=depth) as an:
        for gid in pending:
            try:
                analyse_game_by_id(gid, an, conn=conn)
            except Exception as e:
                print(f"  Game {gid} failed: {e}")
    conn.close()


if __name__ == "__main__":
    analyse_all_pending()
