# chessIQ — Architecture

chessIQ is a **local, profile-scoped chess-improvement app**: it imports your games, runs
them through Stockfish, scores your skills, and turns your mistakes (plus targeted Lichess
puzzles) into a spaced-repetition practice queue. It runs two entrypoints — a **Flask web
app** (`app.py`) and a **CLI** (`main.py`) — over the same domain modules and a single
SQLite database. All chess logic lives server-side in `python-chess`; the browser board is a
dependency-free renderer (no CDN, no chess.js), so everything works fully offline.

The three diagrams below give a component view, the data pipeline, and the database schema.

## 1 — Component / layer view

Browser frontend talks to the Flask app; the Flask app and the CLI share the same domain
logic, which persists through `db.py` to SQLite and reaches out to external services
(Stockfish, Lichess, PGN files).

```mermaid
flowchart TB
    subgraph Client["Browser — dependency-free frontend"]
        Pages["Jinja pages<br/>dashboard · practice · games · game_detail · import"]
        AppJS["app.js<br/>practice / import / game-review controllers"]
        Board["board.js<br/>click-to-move board (renders FEN, no chess.js)"]
        Prof["Profile switcher (base.html)"]
    end

    subgraph Server["Flask app — app.py"]
        Routes["Page routes<br/>/ · /practice · /games · /game/&lt;id&gt; · /import"]
        API["JSON API<br/>/api/session · /api/puzzle/* · /api/import/* · /api/analyse · /api/scores · /api/puzzle-stats"]
        ProfCtx["Profile session<br/>current_profile() + context processor"]
        BG["Background analysis thread + status polling"]
    end

    CLI["main.py — CLI entrypoint (same domain modules)"]

    subgraph Domain["Domain logic"]
        Import["import_lichess.py<br/>import_manual.py"]
        Analyse["analyse_game.py"]
        Engine["engine.py (Stockfish UCI wrapper)"]
        Classify["classifier.py (phase / skill-area heuristics)"]
        Score["scoring.py"]
        PuzGen["puzzles_own_games.py<br/>puzzles_lichess.py"]
        SR["spaced_repetition.py (SM-2)"]
        PS["puzzle_service.py (normalize + validate)"]
    end

    subgraph Data["Persistence"]
        DB["db.py — connection + migration"]
        SQLite[("SQLite — chessiq.db")]
    end

    subgraph Ext["External"]
        SF["Stockfish binary"]
        LiAPI["Lichess API"]
        LiDB["Lichess puzzle DB (CSV)"]
        PGN["chess.com / PGN files"]
    end

    Pages --> Routes
    AppJS --> API
    AppJS -. drives .- Board
    Prof --> ProfCtx
    Routes --> ProfCtx
    API --> ProfCtx
    API --> Import
    API --> BG
    API --> Score
    API --> PuzGen
    API --> PS
    CLI --> Import
    CLI --> Analyse
    CLI --> Score
    CLI --> PuzGen
    CLI --> SR
    BG --> Analyse
    Analyse --> Engine
    Analyse --> Classify
    Engine --> SF
    Import --> LiAPI
    Import --> PGN
    PuzGen --> LiDB
    PS --> SR
    Import --> DB
    Analyse --> DB
    Score --> DB
    PuzGen --> DB
    SR --> DB
    PS --> DB
    DB --> SQLite
```

## 2 — Data pipeline

The weekly loop: import → analyse → score → generate puzzles → practice. Each stage writes a
table the next stage reads; puzzle accuracy feeds back into what surfaces next.

```mermaid
flowchart LR
    A["Games in<br/>Lichess API · PGN upload"] -->|import_*.py<br/>tagged with profile| B[("games")]
    B -->|analyse_game.py<br/>Stockfish eval per ply| C[("moves<br/>cpl · severity · skill_area")]
    C -->|scoring.py<br/>100·e^-cpl/K, per profile| D[("skill_scores")]
    C -->|puzzles_own_games.py<br/>your blunders| E[("puzzles")]
    D -->|puzzles_lichess.py<br/>weak-area targeting| E
    E -->|spaced_repetition.py<br/>SM-2 + weak-area order| F["Today's session"]
    F -->|puzzle_service.py<br/>server-side move validation| G[("puzzle_attempts")]
    G -.->|accuracy feeds selection| E
```

## 3 — Database (profile-scoped)

Seven tables. A **profile** (your username) owns games, puzzles, and scores; moves cascade
from games and attempts cascade from puzzles.

```mermaid
erDiagram
    profiles ||--o{ games : owns
    profiles ||--o{ puzzles : owns
    profiles ||--o{ skill_scores : owns
    games ||--o{ moves : "has plies (cascade)"
    puzzles ||--o{ puzzle_attempts : "reviewed via (cascade)"

    profiles {
        text name PK
    }
    games {
        int id PK
        text profile FK
        text source_id
        text your_color
        int analysed
    }
    moves {
        int id PK
        int game_id FK
        int is_your_move
        int cpl
        text severity
        text skill_area
        text best_move_uci
    }
    skill_scores {
        text profile PK
        text area PK
        text subarea PK
        real score
    }
    puzzles {
        int id PK
        text profile FK
        text source
        text source_id
        text solution_uci
        text skill_area
    }
    puzzle_attempts {
        int id PK
        int puzzle_id FK
        int solved
        text next_review_at
    }
```

## Notes

- **Server owns the chess logic.** Move legality and puzzle-solution checking run in
  `python-chess` on the server (`puzzle_service.py`); `static/js/board.js` only draws the
  board from a FEN and reports clicked squares — no CDN, no chess.js, no piece images.
- **Everything is profile-scoped.** Games, scores, puzzles, and the practice queue all filter
  by the selected profile. `db.py`'s idempotent migration backfills legacy rows and rebuilds
  the `puzzles` table with a `(profile, source, source_id)` uniqueness key, so the same game
  can be tracked independently under two profiles.
- **Two entrypoints, one core.** `app.py` (web) and `main.py` (CLI) are thin shells over the
  same domain modules, so the analysis/scoring/puzzle logic is shared.
- **`skill_score_history`** (not drawn above) is the 7th table — append-only, capturing
  score snapshots per profile for future trend charts.
