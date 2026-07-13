-- chessIQ database schema
-- SQLite. Run once via db.py:init_db()

PRAGMA foreign_keys = ON;

-- One row per profile (a profile name == the player's username in the games)
CREATE TABLE IF NOT EXISTS profiles (
    name            TEXT PRIMARY KEY,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- One row per imported game
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile         TEXT,                   -- owning profile (== your username in this game)
    source          TEXT NOT NULL,          -- 'lichess' | 'chesscom' | 'manual'
    source_id       TEXT,                   -- external game id, unique per source
    pgn             TEXT NOT NULL,
    played_at       TEXT,                   -- ISO8601
    time_control    TEXT,                   -- 'bullet' | 'blitz' | 'rapid' | 'classical' | 'correspondence'
    your_color      TEXT,                   -- 'white' | 'black'
    your_rating     INTEGER,
    opponent_rating INTEGER,
    opponent_name   TEXT,                   -- opponent's username (the other side)
    result          TEXT,                   -- '1-0' | '0-1' | '1/2-1/2'
    opening_eco     TEXT,
    opening_name    TEXT,
    analysed        INTEGER DEFAULT 0,      -- 0/1 flag, set once stockfish pass is done
    imported_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(source, source_id)
);

-- One row per ply (half-move) in an analysed game
CREATE TABLE IF NOT EXISTS moves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply             INTEGER NOT NULL,       -- 1-indexed half-move number
    move_number     INTEGER NOT NULL,       -- full move number (1,1,2,2,3,3...)
    side            TEXT NOT NULL,          -- 'white' | 'black'
    is_your_move    INTEGER NOT NULL,       -- 0/1
    fen_before      TEXT NOT NULL,
    fen_after       TEXT NOT NULL,
    move_san        TEXT NOT NULL,
    move_uci        TEXT NOT NULL,
    best_move_uci   TEXT,
    best_move_san   TEXT,
    eval_before_cp  INTEGER,                -- centipawns, from side-to-move POV, mate scores encoded as +-100000
    eval_after_cp   INTEGER,
    cpl             INTEGER,                -- centipawn loss caused by this move (>=0), null if not applicable (e.g. only move)
    severity        TEXT,                   -- 'blunder' | 'mistake' | 'inaccuracy' | 'ok' | 'good'
    phase           TEXT,                   -- 'opening' | 'middlegame' | 'endgame'
    move_type       TEXT,                   -- 'tactical' | 'positional' | 'forced'
    skill_area      TEXT,                   -- primary area this error charges against
    skill_subarea   TEXT,                   -- specific tag, e.g. 'fork', 'weak_square', 'rook_endgame'
    tags_json       TEXT,                   -- json list of all detected feature tags for this position
    UNIQUE(game_id, ply)
);

-- Rolling skill scores, recomputed after each new analysed game
CREATE TABLE IF NOT EXISTS skill_scores (
    profile         TEXT NOT NULL,
    area            TEXT NOT NULL,
    subarea         TEXT NOT NULL,          -- '_overall' row per area holds the parent score
    score           REAL NOT NULL,
    sample_size     INTEGER NOT NULL DEFAULT 0,   -- number of moves/attempts backing this score
    last_updated    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (profile, area, subarea)
);

-- History of area scores over time, for trend charts
CREATE TABLE IF NOT EXISTS skill_score_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile         TEXT,
    area            TEXT NOT NULL,
    subarea         TEXT NOT NULL,
    score           REAL NOT NULL,
    recorded_at     TEXT DEFAULT (datetime('now'))
);

-- Puzzles, either pulled from Lichess puzzle DB or generated from your own blunders
CREATE TABLE IF NOT EXISTS puzzles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile         TEXT,                   -- owning profile
    source          TEXT NOT NULL,          -- 'lichess' | 'own_game'
    source_id       TEXT,                   -- lichess puzzle id, or game_id:ply for own-game puzzles
    fen             TEXT NOT NULL,
    solution_uci    TEXT NOT NULL,          -- space-separated UCI moves, full solution line
    rating          INTEGER,                -- lichess puzzle rating, or estimated
    themes_json     TEXT,                   -- json list of theme tags
    skill_area      TEXT,
    skill_subarea   TEXT,
    added_at        TEXT DEFAULT (datetime('now')),
    UNIQUE(profile, source, source_id)
);

-- Spaced-repetition attempt log
CREATE TABLE IF NOT EXISTS puzzle_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id       INTEGER NOT NULL REFERENCES puzzles(id) ON DELETE CASCADE,
    solved          INTEGER NOT NULL,       -- 0/1
    time_taken_sec  REAL,
    attempted_at    TEXT DEFAULT (datetime('now')),
    ease_factor     REAL DEFAULT 2.5,       -- SM-2 style ease factor at time of this attempt
    interval_days   REAL DEFAULT 1,         -- interval used to schedule the NEXT review
    next_review_at  TEXT                    -- ISO date, when this puzzle should resurface
);

CREATE INDEX IF NOT EXISTS idx_moves_game ON moves(game_id);
CREATE INDEX IF NOT EXISTS idx_moves_skillarea ON moves(skill_area, skill_subarea);
CREATE INDEX IF NOT EXISTS idx_moves_severity ON moves(severity);
CREATE INDEX IF NOT EXISTS idx_puzzle_attempts_puzzle ON puzzle_attempts(puzzle_id);
CREATE INDEX IF NOT EXISTS idx_puzzle_attempts_next_review ON puzzle_attempts(next_review_at);
