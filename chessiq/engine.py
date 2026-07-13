"""
engine.py — thin wrapper around Stockfish via python-chess's UCI interface.

Handles:
  - starting/stopping the engine process
  - analysing a single FEN to a fixed depth
  - converting Stockfish's Score objects into a plain centipawn int
    (mate scores are clamped to a large constant so comparisons still work)

Change STOCKFISH_PATH if your install lives somewhere else.
Find yours with: `which stockfish` (mac/linux) or check Program Files (windows).
"""

import os
import shutil
import chess
import chess.engine
from pathlib import Path

ANALYSIS_DEPTH = 18                        # 16-20 is a good personal-use tradeoff
MATE_SCORE = 100_000                       # cp value used to represent mate-in-N

# Common install locations checked as a last resort, across platforms.
_COMMON_STOCKFISH_PATHS = [
    "/usr/games/stockfish",                          # Debian/Ubuntu apt
    "/usr/local/bin/stockfish",                       # Linux/macOS manual
    "/opt/homebrew/bin/stockfish",                    # macOS Apple-silicon Homebrew
    "/usr/local/opt/stockfish/bin/stockfish",         # macOS Intel Homebrew
    r"C:\Program Files\Stockfish\stockfish.exe",      # Windows manual install
    r"C:\Program Files\stockfish\stockfish.exe",
    r"C:\stockfish\stockfish.exe",
]


def resolve_stockfish_path() -> str | None:
    """Find the Stockfish binary without hardcoding one path.

    Order: STOCKFISH_PATH env var -> PATH lookup (stockfish / stockfish.exe)
    -> a short list of common install locations. Returns None if not found so
    callers can raise a helpful error.
    """
    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).exists():
        return env

    for name in ("stockfish", "stockfish.exe"):
        found = shutil.which(name)
        if found:
            return found

    for candidate in _COMMON_STOCKFISH_PATHS:
        if Path(candidate).exists():
            return candidate

    return None


# Resolved once at import; set the STOCKFISH_PATH env var to override.
STOCKFISH_PATH = resolve_stockfish_path()


class Analyser:
    """Wraps a single long-lived Stockfish process. Use as a context manager
    so it always gets shut down cleanly, e.g.:

        with Analyser() as an:
            for fen in fens:
                info = an.analyse_fen(fen)
    """

    def __init__(self, path: str = None, depth: int = ANALYSIS_DEPTH):
        path = path or STOCKFISH_PATH or resolve_stockfish_path()
        if not path or not Path(path).exists():
            raise FileNotFoundError(
                "Stockfish binary not found. Install it and either add it to your "
                "PATH or set the STOCKFISH_PATH environment variable to its full path.\n"
                "  Windows: download from https://stockfishchess.org/download/ and set "
                'STOCKFISH_PATH to e.g. C:\\Program Files\\Stockfish\\stockfish.exe\n'
                "  macOS:   brew install stockfish\n"
                "  Linux:   apt install stockfish"
            )
        self.path = path
        self.depth = depth
        self.engine = None

    def __enter__(self):
        self.engine = chess.engine.SimpleEngine.popen_uci(self.path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.engine:
            self.engine.quit()

    def analyse_fen(self, fen: str, depth: int = None):
        """Returns dict: {cp: int, best_move_uci: str, best_move_san: str, mate: int|None}
        cp is always from WHITE's point of view (standard convention), so it's
        directly comparable across positions regardless of side to move.
        """
        board = chess.Board(fen)
        info = self.engine.analyse(board, chess.engine.Limit(depth=depth or self.depth))

        score = info["score"].white()  # PovScore -> white's perspective
        mate = score.mate()
        if mate is not None:
            cp = MATE_SCORE if mate > 0 else -MATE_SCORE
        else:
            cp = score.score()

        best_move = info.get("pv", [None])[0]
        best_move_uci = best_move.uci() if best_move else None
        best_move_san = board.san(best_move) if best_move else None

        return {
            "cp": cp,
            "mate": mate,
            "best_move_uci": best_move_uci,
            "best_move_san": best_move_san,
        }


def quick_test():
    """Sanity check: analyse the starting position."""
    with Analyser() as an:
        result = an.analyse_fen(chess.STARTING_FEN, depth=12)
        print("Starting position:", result)
        # Should show a small positive cp (white's slight edge) and a reasonable opening move


if __name__ == "__main__":
    quick_test()
