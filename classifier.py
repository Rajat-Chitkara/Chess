"""
classifier.py — tags a position/move to a skill area using board-feature
heuristics (Option B: no LLM calls, pure python-chess computation).

Two jobs:
  1. classify_phase(board, move_number)   -> 'opening' | 'middlegame' | 'endgame'
  2. classify_move(board, best_move)      -> (move_type, skill_area, skill_subarea, tags)

The feature detectors here are heuristics, not perfect chess theory. They're
tuned to be directionally right often enough to produce a useful skill
breakdown over dozens of games, not to be a chess engine's positional
evaluator.
"""

import chess

# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------

def _material_count(board: chess.Board) -> int:
    """Total non-king material on the board, in points (P=1,N/B=3,R=5,Q=9)."""
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    total = 0
    for piece_type, val in values.items():
        total += len(board.pieces(piece_type, chess.WHITE)) * val
        total += len(board.pieces(piece_type, chess.BLACK)) * val
    return total

def _queens_on_board(board: chess.Board) -> int:
    return len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.QUEEN, chess.BLACK))

STARTING_MATERIAL = 78  # 8p+2n+2b+2r+q = 8+6+6+10+9=39 per side *2

def classify_phase(board: chess.Board, move_number: int) -> str:
    material = _material_count(board)
    queens = _queens_on_board(board)

    if move_number <= 10 and material >= STARTING_MATERIAL - 6:
        return "opening"

    # Endgame: material has dropped a lot, or queens are off with low material
    if material <= 24 or (queens == 0 and material <= 30):
        return "endgame"

    if move_number <= 12:
        return "opening"

    return "middlegame"


# ---------------------------------------------------------------------------
# Move / feature classification
# ---------------------------------------------------------------------------

def _is_forcing(board: chess.Board, move: chess.Move) -> bool:
    """A move is 'forcing' if it's a check, a capture, or a promotion —
    the hallmark of tactical (calculation-driven) play vs quiet positional play."""
    if board.is_capture(move):
        return True
    if move.promotion:
        return True
    board.push(move)
    is_check = board.is_check()
    board.pop()
    return is_check


def _detect_tactical_motifs(board: chess.Board, move: chess.Move) -> list[str]:
    """Cheap heuristics for common tactical patterns triggered by `move`.
    Not exhaustive — covers the motifs common enough to be worth scoring on.
    """
    tags = []
    board.push(move)

    # Fork: the moved piece now attacks 2+ enemy pieces of higher-or-equal value than a pawn
    moved_piece = board.piece_at(move.to_square)
    if moved_piece:
        attacked = [
            sq for sq in board.attacks(move.to_square)
            if board.piece_at(sq) and board.piece_at(sq).color != moved_piece.color
            and board.piece_at(sq).piece_type != chess.KING
        ]
        valuable_targets = [sq for sq in attacked if board.piece_at(sq).piece_type != chess.PAWN]
        if len(attacked) >= 2 and len(valuable_targets) >= 1:
            tags.append("fork")

    # Discovered attack: moving the piece reveals an attack from a different piece
    # (approximate: check if a friendly slider now attacks something it didn't before)
    if board.is_check():
        tags.append("check_delivered")

    board.pop()

    # Pin/skewer detection (post-move): any enemy piece pinned to king or queen
    board.push(move)
    for color in [chess.WHITE, chess.BLACK]:
        for sq in board.pieces(chess.KING, color):
            pass  # king pin handled by board.is_pinned below
    for sq, piece in board.piece_map().items():
        if piece.color != moved_piece.color if moved_piece else False:
            if board.is_pinned(piece.color, sq):
                tags.append("pin")
                break
    board.pop()

    if move.promotion:
        tags.append("promotion")

    if board.is_capture(move):
        captured = board.piece_at(move.to_square)
        if captured:
            tags.append("capture")

    return list(set(tags)) or ["calculation"]


def _detect_positional_motifs(board: chess.Board, move: chess.Move) -> list[str]:
    """Heuristics for quiet/strategic move themes."""
    tags = []
    piece = board.piece_at(move.from_square)
    if not piece:
        return ["plan"]

    to_rank = chess.square_rank(move.to_square)
    file_ = chess.square_file(move.to_square)

    # Outpost-ish: knight/bishop moving to central, advanced square not attackable by enemy pawns
    if piece.piece_type in (chess.KNIGHT, chess.BISHOP):
        advanced = (to_rank >= 4) if piece.color == chess.WHITE else (to_rank <= 3)
        central_file = 2 <= file_ <= 5
        if advanced and central_file:
            tags.append("outpost")

    # Rook to open/half-open file
    if piece.piece_type == chess.ROOK:
        board.push(move)
        own_pawns_on_file = any(
            chess.square_file(sq) == file_
            for sq in board.pieces(chess.PAWN, piece.color)
        )
        board.pop()
        if not own_pawns_on_file:
            tags.append("open_file")

    # Pawn break / structure change
    if piece.piece_type == chess.PAWN:
        tags.append("pawn_structure")

    # King safety (castling or king moves before move 15)
    if board.is_castling(move):
        tags.append("king_safety")

    if not tags:
        tags.append("plan")

    return tags


def classify_move(board: chess.Board, best_move: chess.Move, move_number: int) -> dict:
    """Main entry point. `board` is the position BEFORE the move was made.
    Classifies based on what the BEST move was (i.e. what the player missed),
    since that tells us what skill would have found it.
    """
    phase = classify_phase(board, move_number)
    forcing = _is_forcing(board, best_move)
    move_type = "tactical" if forcing else "positional"

    if move_type == "tactical":
        tags = _detect_tactical_motifs(board, best_move)
    else:
        tags = _detect_positional_motifs(board, best_move)

    # Map to top-level skill area
    if phase == "opening":
        area = "openings"
        subarea = tags[0] if tags else "theory"
    elif phase == "endgame":
        area = "endgame"
        # sub-classify endgame type by remaining material
        subarea = _endgame_subtype(board)
    else:
        # Tactical shots (forcing moves) always charge to Tactics, regardless
        # of game phase. Quiet/strategic moves charge to Positional play.
        # 'Middlegame' as a standalone area is intentionally not used here —
        # it would just double-count moves already captured by tactics/positional.
        # Middlegame-specific themes (plans, prophylaxis, piece activity) live
        # as SUBAREAS under 'positional' instead, so you still see them broken out.
        area = "tactics" if move_type == "tactical" else "positional"
        subarea = tags[0] if tags else move_type

    return {
        "phase": phase,
        "move_type": move_type,
        "skill_area": area,
        "skill_subarea": subarea,
        "tags": tags,
    }


def _endgame_subtype(board: chess.Board) -> str:
    """Classify endgame by which piece types remain, for subarea scoring
    like 'rook_endgame', 'pawn_endgame', 'queen_endgame'."""
    has = lambda pt: any(board.pieces(pt, c) for c in (chess.WHITE, chess.BLACK))
    if has(chess.QUEEN):
        return "queen_endgame"
    if has(chess.ROOK) and not has(chess.QUEEN):
        # rook + minor is common; still bucket as rook_endgame
        return "rook_endgame"
    if has(chess.BISHOP) or has(chess.KNIGHT):
        return "minor_piece_endgame"
    return "pawn_endgame"


if __name__ == "__main__":
    # quick smoke test
    board = chess.Board()
    best = chess.Move.from_uci("e2e4")
    print(classify_move(board, best, 1))

    # a tactical-looking midgame position: white knight can fork
    board2 = chess.Board("r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 4 5")
    best2 = chess.Move.from_uci("f3g5")
    print(classify_move(board2, best2, 5))
