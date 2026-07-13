# chessIQ — personal chess improvement website (v1)

A local system that imports your Lichess games, runs them through Stockfish,
classifies every mistake by skill area (openings / tactics / positional /
endgame), scores you 0–100 in each area, and builds a daily puzzle practice
queue targeting your actual weaknesses — using your own blunders plus
targeted puzzles pulled from the Lichess puzzle database.

It ships with both a **web app** (an interactive board you practice on in the
browser) and the original **CLI**. Everything runs locally. No cloud, no
accounts beyond Lichess itself.

## Run the website

```bash
pip install -r requirements.txt
python main.py init        # one-time: create the database
python app.py              # then open http://127.0.0.1:5000/
```

The web app has four pages: **Dashboard** (your skill scores), **Practice**
(solve puzzles on a clickable board with spaced repetition), **Games** (what
you've imported), and **Import** (pull from Lichess or upload a PGN, then run
analysis / scoring / puzzle generation with a click). The board and all move
validation are pure `python-chess` on the server — no external JS, works fully
offline. Import and practice work without Stockfish; the **Analyse** step needs
it (see below).

Try it immediately with the bundled `sample.pgn`: on the Import page, enter
username `testuser` and upload `sample.pgn`.

## Status

This was built and tested in a sandboxed dev environment with **no internet
access to lichess.org**, so every module that doesn't need the network has
been run end-to-end against real test games (`engine.py`, `classifier.py`,
`analyse_game.py`, `scoring.py`, `spaced_repetition.py`,
`puzzles_own_games.py` — all confirmed working). The two modules that need
Lichess (`import_lichess.py`, `puzzles_lichess.py`) are written and ready
but need to be run on your own machine where you have internet access.

## Setup (on your machine)

```bash
# 1. Install Stockfish
brew install stockfish          # macOS
# or: apt install stockfish     # Ubuntu/Debian
# or download from https://stockfishchess.org/download/

# 2. chessIQ auto-detects Stockfish on your PATH and in common install
#    locations. If yours lives somewhere unusual, point it there explicitly:
#      macOS/Linux:  export STOCKFISH_PATH=/full/path/to/stockfish
#      Windows:      set STOCKFISH_PATH=C:\Program Files\Stockfish\stockfish.exe

# 3. Install Python deps
pip install -r requirements.txt

# 4. Initialize the database
python3 main.py init
```

## Weekly usage loop

**Option A — Lichess (automatic, needs internet + username):**
```bash
python3 main.py import your_lichess_username --max 50
```

**Option B — chess.com or any manual PGN (tested, works offline once you have the file):**

1. Go to `https://www.chess.com/games/archive/your_username`
2. Pick a month, click Download — saves a `.pgn` file with all games from that month
   (or open a single game and use its "Download" option for just that game)
3. Import it:
```bash
python3 main.py import-manual your_chesscom_username path/to/downloaded.pgn
# or point at a whole folder of monthly exports:
python3 main.py import-manual your_chesscom_username path/to/pgn_folder/
```
   The username must match exactly how it appears in the PGN's White/Black
   headers — if it doesn't match either side, that game is skipped with a
   warning so you can check the spelling. Re-running on the same file is
   safe; already-imported games are skipped automatically.

Then, regardless of which import method you used:

```bash
# Run Stockfish analysis (the slow step — a few min per game at depth 18)
python3 main.py analyse

# Recompute your skill scores
python3 main.py score

# Turn your own blunders into puzzles
python3 main.py puzzles-own

# One-time: download the Lichess puzzle DB (~250MB, few minutes)
python3 -c "from puzzles_lichess import download_puzzle_db; download_puzzle_db()"

# Pull puzzles targeting your current weak areas (run anytime after that)
python3 main.py puzzles-lichess --rating 1350   # use your approx rating

# See what to practice today
python3 main.py session

# Full skill report anytime
python3 main.py report
```

## How the scoring works

Every move you make gets a Stockfish eval before and after. The drop in
evaluation (centipawn loss, CPL) tells us how bad the move was:

- CPL ≥ 150 → blunder
- CPL ≥ 50 → mistake
- CPL ≥ 20 → inaccuracy
- else → fine

Each move is also tagged to a skill area using board-feature heuristics
(`classifier.py`, "Option B" — no LLM calls, pure `python-chess` logic):

- **Move 1–10ish, near-full material** → opening
- **Low material / no queens** → endgame
- Otherwise: forcing moves (checks, captures, promotions) → **tactics**;
  quiet moves → **positional**

Within tactics/positional, sub-tags are detected: forks, pins, outposts,
open files, king safety, pawn structure, etc.

Your score per area is `100 * e^(-avg_cpl / 100)` — a smooth curve where
flawless play scores ~100 and it decays gracefully as average CPL rises.
Tune `K` in `scoring.py` if scores feel too harsh or too lenient once you
have real data.

## Files

| File | Role |
|---|---|
| `schema.sql` / `db.py` | SQLite schema + connection helper |
| `engine.py` | Stockfish wrapper (analyse a FEN, get eval + best move) |
| `classifier.py` | Tags a position/move to phase + skill area + subarea |
| `analyse_game.py` | Full per-game pipeline: PGN → Stockfish → classified moves in DB |
| `scoring.py` | Aggregates moves into 0–100 skill scores, area + subarea |
| `import_lichess.py` | Pulls your games from the Lichess API |
| `import_manual.py` | Imports chess.com (or any) PGN files you've downloaded manually |
| `puzzles_lichess.py` | Downloads Lichess puzzle DB, pulls puzzles matching your weak areas |
| `puzzles_own_games.py` | Turns your own blunders into puzzles at the exact FEN |
| `spaced_repetition.py` | SM-2-style review scheduling + daily session builder |
| `main.py` | CLI wiring all of the above together |

## What's next (v2 ideas, not built yet)

- LLM explanation layer (Option C): feed each blunder's FEN + best move
  to Claude for a plain-English "why this was wrong" note
- A simple local web UI with an actual clickable board (chessboard.js +
  chess.js) instead of the CLI — the mockup from earlier in this
  conversation shows roughly the shape of it
- Opening repertoire tracking: build a personal book of "lines you should
  know" and flag the first move you deviated from theory
- Adjustable area weights for the overall composite score
