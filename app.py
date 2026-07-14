"""
app.py — the chessIQ web app. A small local Flask server that turns the
existing CLI backend into a browsable "chess learning website".

Run it:
    python app.py
    # then open http://127.0.0.1:5000/

It reuses the existing modules unchanged: imports, Stockfish analysis,
scoring, puzzle generation and spaced repetition. All chess logic stays in
python-chess on the server; the frontend is a dependency-free click-to-move
board (static/js/board.js).
"""

import io
import threading
import traceback

from flask import Flask, render_template, request, jsonify, session, redirect, url_for

from paths import resource_path
from db import get_conn, init_db, DEFAULT_PROFILES
from analyse_game import analyse_game_by_id
from engine import Analyser, resolve_stockfish_path
from scoring import compute_scores
from puzzles_own_games import generate_from_blunders
import puzzle_service
import game_review

# Bundle-aware folders so templates/static resolve both from source and when
# frozen into a standalone app (PyInstaller).
app = Flask(__name__,
            template_folder=resource_path("templates"),
            static_folder=resource_path("static"))
app.secret_key = "chessiq-local-dev-key"   # only used to sign the local session cookie

# Make sure the DB/tables exist (and are migrated) before the first request.
init_db()
# Repopulate per-profile skill scores once at startup (harmless if already current).
try:
    compute_scores()
except Exception:
    traceback.print_exc()


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def list_profiles():
    conn = get_conn()
    # rowid == insertion order, so the first-seeded profile (Stock-Fish24) stays
    # the default even though the three share a created_at timestamp.
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM profiles ORDER BY rowid").fetchall()]
    conn.close()
    return names


def current_profile():
    """The profile selected in the session, or the first available one."""
    names = list_profiles()
    chosen = session.get("profile")
    if chosen in names:
        return chosen
    return names[0] if names else DEFAULT_PROFILES[0]


@app.context_processor
def inject_profiles():
    return {"profiles": list_profiles(), "current_profile": current_profile()}


@app.route("/profile/select", methods=["POST"])
def profile_select():
    name = (request.form.get("profile") or "").strip()
    if name:
        session["profile"] = name
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/profile/create", methods=["POST"])
def api_profile_create():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO profiles(name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    session["profile"] = name
    return jsonify({"ok": True, "name": name})


# ---------------------------------------------------------------------------
# Shared read helpers
# ---------------------------------------------------------------------------

def _fetch_scores(conn, profile):
    areas = conn.execute("""
        SELECT area, score, sample_size FROM skill_scores
        WHERE profile = ? AND subarea = '_overall' ORDER BY score ASC
    """, (profile,)).fetchall()
    weak = conn.execute("""
        SELECT area, subarea, score, sample_size FROM skill_scores
        WHERE profile = ? AND subarea != '_overall' AND sample_size >= 2
        ORDER BY score ASC LIMIT 8
    """, (profile,)).fetchall()
    return [dict(a) for a in areas], [dict(w) for w in weak]


def _fetch_games(conn, profile):
    return [dict(r) for r in conn.execute("""
        SELECT id, source, played_at, time_control, your_color, your_rating,
               opponent_rating, opponent_name, result, opening_name, analysed
        FROM games WHERE profile = ?
        ORDER BY COALESCE(played_at, imported_at) DESC, id DESC
    """, (profile,)).fetchall()]


def _counts(conn, profile):
    g = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(analysed),0) a FROM games WHERE profile = ?",
                     (profile,)).fetchone()
    p = conn.execute("SELECT COUNT(*) c FROM puzzles WHERE profile = ?", (profile,)).fetchone()
    return {"games": g["c"], "analysed": g["a"], "puzzles": p["c"]}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    profile = current_profile()
    conn = get_conn()
    areas, weak = _fetch_scores(conn, profile)
    counts = _counts(conn, profile)
    pstats = puzzle_service.puzzle_stats(conn=conn, profile=profile)
    conn.close()
    return render_template("dashboard.html", areas=areas, weak=weak, counts=counts,
                           pstats=pstats, active="dashboard")


@app.route("/practice")
def practice():
    return render_template("practice.html", active="practice")


@app.route("/games")
def games():
    conn = get_conn()
    rows = _fetch_games(conn, current_profile())
    conn.close()
    return render_template("games.html", games=rows, active="games")


def _players_from_pgn(pgn, your_color):
    """Extract the two players (name + rating) and orient them so `bottom` is the
    side you played and `top` is the opponent."""
    white = {"name": "White", "rating": None}
    black = {"name": "Black", "rating": None}
    try:
        import io
        import chess.pgn
        g = chess.pgn.read_game(io.StringIO(pgn or ""))
        if g is not None:
            h = g.headers
            white = {"name": h.get("White") or "White", "rating": h.get("WhiteElo") or None}
            black = {"name": h.get("Black") or "Black", "rating": h.get("BlackElo") or None}
    except Exception:
        pass
    if your_color == "black":
        return {"bottom": {**black, "color": "black"}, "top": {**white, "color": "white"}}
    return {"bottom": {**white, "color": "white"}, "top": {**black, "color": "black"}}


@app.route("/game/<int:game_id>")
def game_detail(game_id):
    conn = get_conn()
    game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if game is None:
        conn.close()
        return render_template("game_detail.html", game=None, active="games"), 404
    moves = conn.execute("""
        SELECT ply, move_number, side, is_your_move, move_san, move_uci,
               best_move_san, best_move_uci, cpl, severity, phase,
               skill_area, skill_subarea, fen_before, fen_after,
               eval_before_cp, eval_after_cp
        FROM moves WHERE game_id = ? ORDER BY ply
    """, (game_id,)).fetchall()
    conn.close()

    game = dict(game)
    moves = [dict(m) for m in moves]
    ERR = ("inaccuracy", "mistake", "blunder")
    summary = {sev: sum(1 for m in moves if m["is_your_move"] and m["severity"] == sev)
               for sev in ("blunder", "mistake", "inaccuracy")}
    # 1-based ply positions of your mistakes, for the "jump to mistake" controls
    mistake_plies = [i for i, m in enumerate(moves, start=1)
                     if m["is_your_move"] and m["severity"] in ERR]
    start_fen = (moves[0]["fen_before"] if moves
                 else "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")

    # Per-move review labels + coach commentary (chess.com-style, heuristic).
    game_review.annotate(moves)
    # Counts of YOUR moves by quality (best/book/good/inaccuracy/mistake/blunder).
    qsummary = game_review.summarize_quality(moves)
    # 1-based ply positions of YOUR moves in each quality, so clicking a summary
    # pill can cycle through that category's moves.
    quality_plies = {k: [] for k in ("best", "book", "good", "inaccuracy", "mistake", "blunder")}
    for i, m in enumerate(moves, start=1):
        if m["is_your_move"] and m.get("quality") in quality_plies:
            quality_plies[m["quality"]].append(i)

    # Group half-moves into full-move rows (num, white, black) for the move table.
    by_num = {}
    for m in moves:
        row = by_num.setdefault(m["move_number"], {"num": m["move_number"], "white": None, "black": None})
        row[m["side"]] = m
    move_rows = [by_num[k] for k in sorted(by_num)]

    # White-POV eval at each ply (index 0 = start position), for the eval bar + graph.
    start_eval = moves[0]["eval_before_cp"] if moves else 0
    evals = [start_eval] + [m["eval_after_cp"] for m in moves]

    # Player names/ratings pulled from the stored PGN headers.
    players = _players_from_pgn(game.get("pgn", ""), game.get("your_color"))

    return render_template("game_detail.html", game=game, moves=moves, move_rows=move_rows,
                           summary=summary, qsummary=qsummary, quality_plies=quality_plies,
                           start_fen=start_fen, mistake_plies=mistake_plies, evals=evals,
                           players=players, orientation=game["your_color"] or "white", active="games")


@app.route("/import")
def import_page():
    stockfish = resolve_stockfish_path()
    return render_template("import.html", active="import", stockfish=stockfish)


# ---------------------------------------------------------------------------
# JSON API — read
# ---------------------------------------------------------------------------

@app.route("/api/scores")
def api_scores():
    conn = get_conn()
    areas, weak = _fetch_scores(conn, current_profile())
    conn.close()
    return jsonify({"areas": areas, "weak": weak})


@app.route("/api/puzzle-stats")
def api_puzzle_stats():
    return jsonify(puzzle_service.puzzle_stats(profile=current_profile()))


@app.route("/api/session")
def api_session():
    review = int(request.args.get("review", 6))
    fresh = int(request.args.get("fresh", 6))
    return jsonify(puzzle_service.get_session(review_slots=review, fresh_slots=fresh,
                                              profile=current_profile()))


# ---------------------------------------------------------------------------
# JSON API — puzzle solving
# ---------------------------------------------------------------------------

@app.route("/api/puzzle/<int:puzzle_id>/move", methods=["POST"])
def api_puzzle_move(puzzle_id):
    data = request.get_json(force=True, silent=True) or {}
    move = data.get("move")
    ply_index = data.get("ply_index", 0)
    if not move:
        return jsonify({"ok": False, "error": "missing move"}), 400
    return jsonify(puzzle_service.evaluate_move(puzzle_id, int(ply_index), move))


@app.route("/api/puzzle/<int:puzzle_id>/hint")
def api_puzzle_hint(puzzle_id):
    ply_index = int(request.args.get("ply_index", 0))
    return jsonify(puzzle_service.get_hint(puzzle_id, ply_index))


@app.route("/api/puzzle/<int:puzzle_id>/result", methods=["POST"])
def api_puzzle_result(puzzle_id):
    data = request.get_json(force=True, silent=True) or {}
    solved = bool(data.get("solved"))
    tsec = data.get("time_taken_sec")
    schedule = puzzle_service.submit_result(puzzle_id, solved, time_taken_sec=tsec)
    return jsonify({"ok": True, "schedule": schedule})


# ---------------------------------------------------------------------------
# JSON API — import
# ---------------------------------------------------------------------------

@app.route("/api/import/lichess", methods=["POST"])
def api_import_lichess():
    data = request.get_json(force=True, silent=True) or {}
    profile = current_profile()
    # The profile name is the username; allow an explicit override if the Lichess
    # handle differs from the profile name.
    username = (data.get("username") or profile).strip()
    max_games = int(data.get("max", 50))
    token = data.get("token") or None
    try:
        from import_lichess import fetch_games, parse_and_store
        pgn_blob = fetch_games(username, max_games, token)
        inserted, skipped = parse_and_store(pgn_blob, username, profile=profile)
        return jsonify({"ok": True, "inserted": inserted, "skipped": skipped, "profile": profile})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 502


@app.route("/api/import/chesscom", methods=["POST"])
def api_import_chesscom():
    data = request.get_json(force=True, silent=True) or {}
    profile = current_profile()
    # The chess.com handle to fetch; defaults to the profile name.
    username = (data.get("username") or profile).strip()
    max_games = int(data.get("max", 30))
    try:
        from import_chesscom import import_chesscom
        inserted, skipped, failed = import_chesscom(username, max_games, profile=profile)
        return jsonify({"ok": True, "inserted": inserted, "skipped": skipped,
                        "failed": failed, "profile": profile})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 502


@app.route("/api/import/pgn", methods=["POST"])
def api_import_pgn():
    # Games are imported under the currently selected profile — the profile name
    # is matched against the PGN's White/Black headers to pick your side.
    profile = current_profile()
    source_label = (request.form.get("source_label") or "chesscom").strip()

    # Bulk: accept many PGN files at once. Each file may itself contain many
    # games (a monthly chess.com export), which parse_and_store_pgn_text handles.
    files = request.files.getlist("pgn")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({"ok": False, "error": "no PGN file uploaded"}), 400

    from import_manual import parse_and_store_pgn_text
    conn = get_conn()
    total_ins, total_skip, total_fail = 0, 0, 0
    per_file = []
    try:
        for f in files:
            text = f.read().decode("utf-8", errors="replace")
            try:
                ins, skip, fail = parse_and_store_pgn_text(text, profile, source_label,
                                                           conn=conn, profile=profile)
            except Exception as e:
                per_file.append({"name": f.filename, "error": f"{type(e).__name__}: {e}"})
                continue
            total_ins += ins; total_skip += skip; total_fail += fail
            per_file.append({"name": f.filename, "inserted": ins, "skipped": skip, "failed": fail})
    finally:
        conn.close()

    return jsonify({"ok": True, "profile": profile, "files": len(files), "inserted": total_ins,
                    "skipped": total_skip, "failed": total_fail, "per_file": per_file})


# ---------------------------------------------------------------------------
# JSON API — analysis (background thread + polling)
# ---------------------------------------------------------------------------

_analysis = {"running": False, "total": 0, "done": 0, "current": None,
             "error": None, "finished": False}
_analysis_lock = threading.Lock()


def _run_analysis(depth, limit):
    conn = get_conn()
    try:
        query = "SELECT id FROM games WHERE analysed = 0 ORDER BY id"
        if limit:
            query += f" LIMIT {int(limit)}"
        pending = [r["id"] for r in conn.execute(query).fetchall()]

        with _analysis_lock:
            _analysis.update(total=len(pending), done=0, current=None,
                             error=None, finished=False, running=True)

        if pending:
            with Analyser(depth=depth) as an:
                for gid in pending:
                    with _analysis_lock:
                        _analysis["current"] = gid
                    try:
                        analyse_game_by_id(gid, an, conn=conn)
                    except Exception as e:
                        print(f"  Game {gid} failed: {e}")
                    with _analysis_lock:
                        _analysis["done"] += 1
            # Recompute scores once analysis is done so the dashboard updates.
            compute_scores(conn=conn)
    except Exception as e:
        traceback.print_exc()
        with _analysis_lock:
            _analysis["error"] = f"{type(e).__name__}: {e}"
    finally:
        conn.close()
        with _analysis_lock:
            _analysis.update(running=False, finished=True, current=None)


@app.route("/api/analyse", methods=["POST"])
def api_analyse():
    data = request.get_json(force=True, silent=True) or {}
    depth = int(data.get("depth", 18))
    limit = data.get("limit")

    if resolve_stockfish_path() is None:
        return jsonify({"ok": False, "error": "Stockfish not found. Install it or set "
                        "the STOCKFISH_PATH environment variable."}), 400

    with _analysis_lock:
        if _analysis["running"]:
            return jsonify({"ok": False, "error": "analysis already running"}), 409
        _analysis.update(running=True, finished=False, error=None, total=0, done=0)

    threading.Thread(target=_run_analysis, args=(depth, limit), daemon=True).start()
    return jsonify({"ok": True, "started": True})


@app.route("/api/analyse/status")
def api_analyse_status():
    with _analysis_lock:
        return jsonify(dict(_analysis))


# ---------------------------------------------------------------------------
# JSON API — scoring + puzzle generation
# ---------------------------------------------------------------------------

@app.route("/api/score", methods=["POST"])
def api_score():
    profile = current_profile()
    result = compute_scores()
    areas = {area: v[0] for (prof, area), v in result["areas"].items() if prof == profile}
    return jsonify({"ok": True, "profile": profile, "areas": areas})


@app.route("/api/puzzles/generate", methods=["POST"])
def api_generate_puzzles():
    data = request.get_json(force=True, silent=True) or {}
    profile = current_profile()
    min_severity = data.get("min_severity", "mistake")
    own = generate_from_blunders(min_severity=min_severity, profile=profile)

    lichess_added = None
    rating = data.get("rating")
    if rating:
        try:
            from puzzles_lichess import import_puzzles_for_weak_areas
            import_puzzles_for_weak_areas(target_rating=int(rating), profile=profile)
            lichess_added = "requested"
        except FileNotFoundError:
            lichess_added = "puzzle DB not downloaded (own-game puzzles still generated)"
        except Exception as e:
            lichess_added = f"error: {e}"

    return jsonify({"ok": True, "own_generated": own, "lichess": lichess_added})


if __name__ == "__main__":
    print("chessIQ web app starting on http://127.0.0.1:5000/")
    if resolve_stockfish_path() is None:
        print("  (note: Stockfish not detected — import & practice work, but "
              "'Analyse' needs it. Set STOCKFISH_PATH to enable.)")
    app.run(host="127.0.0.1", port=5000, debug=True)
