/* app.js — frontend controllers for the practice and import pages. */
window.chessIQ = (function () {

  async function jpost(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  }

  // ---------------------------------------------------------------- Practice
  function initPractice() {
    const boardEl = document.getElementById("board");
    const fb = document.getElementById("feedback");
    const btnNext = document.getElementById("btn-next");
    const btnHint = document.getElementById("btn-hint");
    const btnSkip = document.getElementById("btn-skip");
    const btnRetry = document.getElementById("btn-retry");

    let queue = [];
    let idx = 0;
    let board = null;
    let current = null;   // current puzzle
    let plyIndex = 0;
    let startTime = 0;
    let finished = false;
    let resultRecorded = false;   // ensure the SR attempt is logged only once per puzzle

    function setFeedback(msg, cls) { fb.textContent = msg; fb.className = "feedback " + (cls || ""); }

    function loadSession() {
      fetch("/api/session").then(r => r.json()).then(s => {
        queue = [...s.review, ...s.fresh];
        idx = 0;
        if (queue.length === 0) {
          document.querySelector(".practice-wrap").style.display = "none";
          document.getElementById("empty-state").style.display = "block";
          return;
        }
        showPuzzle();
      });
    }

    function showPuzzle() {
      if (idx >= queue.length) { loadSession(); return; }  // refetch (schedules may have changed)
      current = queue[idx];
      plyIndex = 0;
      finished = false;
      resultRecorded = false;
      startTime = Date.now();
      btnNext.style.display = "none";
      btnRetry.style.display = "none";
      setFeedback("Find the best move.", "info");

      document.getElementById("pm-turn").textContent = current.side_to_move;
      // Theme is deliberately not shown while solving (it would hint the answer);
      // it's still tracked on the backend for categorisation and puzzle stats.
      document.getElementById("pm-rating").textContent = current.rating || "—";
      document.getElementById("pm-progress").textContent = (idx + 1) + " / " + queue.length;

      if (!board) {
        board = ChessBoard(boardEl, { fen: current.board_fen, orientation: current.side_to_move, onMove });
      } else {
        board.setPosition(current.board_fen, current.side_to_move);
      }
      board.setInteractive(true);
    }

    async function onMove(uci) {
      if (finished) return;
      board.setInteractive(false);
      const res = await jpost(`/api/puzzle/${current.puzzle_id}/move`, { move: uci, ply_index: plyIndex });
      if (!res.ok) { setFeedback("Error: " + res.error, "bad"); return; }

      if (!res.correct) {
        board.applyUci(uci);                 // show the wrong move that was tried
        finished = true;
        setFeedback("Not the move. Best was " + res.expected + ". Retry to try again.", "bad");
        recordResult(false);                 // first attempt counts for spaced repetition
        btnRetry.style.display = "";
        btnNext.style.display = "";
        return;
      }

      board.applyUci(uci);                   // confirmed solver move
      if (res.done && !res.opponent_reply) {
        solvedPuzzle();
        return;
      }
      if (res.opponent_reply) {
        setTimeout(() => {
          board.applyUci(res.opponent_reply);
          if (res.done) { solvedPuzzle(); }
          else { plyIndex = res.next_ply; board.setInteractive(true); setFeedback("Good. Keep going…", "good"); }
        }, 350);
      } else {
        solvedPuzzle();
      }
    }

    function solvedPuzzle() {
      finished = true;
      setFeedback("Solved! ✓", "good");
      recordResult(true);
      btnNext.style.display = "";
    }

    function recordResult(solved) {
      if (resultRecorded) return;            // log the first outcome only; retries are practice
      resultRecorded = true;
      const secs = (Date.now() - startTime) / 1000;
      jpost(`/api/puzzle/${current.puzzle_id}/result`, { solved, time_taken_sec: secs });
    }

    function retry() {
      // Replay the same puzzle from the start for practice. The first attempt
      // is already recorded, so this does NOT change the spaced-repetition schedule.
      plyIndex = 0;
      finished = false;
      board.setPosition(current.board_fen, current.side_to_move);
      board.setInteractive(true);
      btnRetry.style.display = "none";
      btnNext.style.display = "";
      setFeedback("Retry — find the best move.", "info");
    }

    btnNext.addEventListener("click", () => { idx++; showPuzzle(); });
    btnSkip.addEventListener("click", () => { idx++; showPuzzle(); });
    btnRetry.addEventListener("click", retry);
    btnHint.addEventListener("click", async () => {
      if (finished) return;
      const h = await fetch(`/api/puzzle/${current.puzzle_id}/hint?ply_index=${plyIndex}`).then(r => r.json());
      if (h.ok) { board.highlightHint(h.from); setFeedback("Hint: move the piece on " + h.from + ".", "info"); }
    });

    loadSession();
  }

  // ------------------------------------------------------------------ Import
  function initImport() {
    const msg = document.getElementById("import-msg");
    const pzMsg = document.getElementById("puzzle-msg");

    document.getElementById("btn-cc").addEventListener("click", async () => {
      msg.textContent = "Fetching from chess.com…"; msg.className = "feedback info";
      const res = await jpost("/api/import/chesscom", {
        username: document.getElementById("cc-user").value.trim() || null,
        max: parseInt(document.getElementById("cc-max").value || "30", 10),
      });
      if (res.ok) { msg.textContent = `Imported ${res.inserted} new game(s) into ${res.profile}, skipped ${res.skipped}, ${res.failed} not-yours/failed.`; msg.className = "feedback good"; }
      else { msg.textContent = "Failed: " + res.error; msg.className = "feedback bad"; }
    });

    document.getElementById("btn-li").addEventListener("click", async () => {
      msg.textContent = "Importing from Lichess…"; msg.className = "feedback info";
      const res = await jpost("/api/import/lichess", {
        username: document.getElementById("li-user").value.trim() || null,
        max: parseInt(document.getElementById("li-max").value || "30", 10),
        token: document.getElementById("li-token").value.trim() || null,
      });
      if (res.ok) { msg.textContent = `Imported ${res.inserted} new game(s) into ${res.profile}, skipped ${res.skipped}.`; msg.className = "feedback good"; }
      else { msg.textContent = "Failed: " + res.error; msg.className = "feedback bad"; }
    });

    document.getElementById("pgn-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const files = document.getElementById("pgn-file").files;
      if (files.length === 0) { msg.textContent = "Select at least one PGN file."; msg.className = "feedback bad"; return; }
      const fd = new FormData();
      for (const f of files) fd.append("pgn", f);
      msg.textContent = `Uploading ${files.length} file(s)…`; msg.className = "feedback info";
      const res = await fetch("/api/import/pgn", { method: "POST", body: fd }).then(r => r.json());
      if (res.ok) { msg.textContent = `Imported ${res.inserted} game(s) into ${res.profile} from ${res.files} file(s), skipped ${res.skipped}, ${res.failed} not-yours/failed.`; msg.className = "feedback good"; }
      else { msg.textContent = "Failed: " + res.error; msg.className = "feedback bad"; }
    });

    const btnAnalyse = document.getElementById("btn-analyse");
    if (btnAnalyse) btnAnalyse.addEventListener("click", async () => {
      const start = await jpost("/api/analyse", {});
      if (!start.ok) { alert(start.error); return; }
      document.getElementById("analyse-status").style.display = "block";
      pollAnalyse();
    });

    function pollAnalyse() {
      const bar = document.getElementById("analyse-bar");
      const txt = document.getElementById("analyse-text");
      fetch("/api/analyse/status").then(r => r.json()).then(s => {
        const pct = s.total ? Math.round((s.done / s.total) * 100) : (s.finished ? 100 : 0);
        bar.style.width = pct + "%";
        if (s.error) { txt.textContent = "Error: " + s.error; return; }
        txt.textContent = s.finished
          ? `Done — analysed ${s.done} game(s). Scores updated.`
          : `Analysing ${s.done}/${s.total} (game ${s.current || "…"})`;
        if (!s.finished) setTimeout(pollAnalyse, 1500);
      });
    }

    document.getElementById("btn-puzzles").addEventListener("click", async () => {
      pzMsg.textContent = "Generating puzzles…"; pzMsg.className = "feedback info";
      const rating = document.getElementById("pz-rating").value.trim();
      const res = await jpost("/api/puzzles/generate", { rating: rating ? parseInt(rating, 10) : null });
      if (res.ok) {
        let t = `Generated ${res.own_generated} puzzle(s) from your blunders.`;
        if (res.lichess) t += " Lichess: " + res.lichess + ".";
        pzMsg.textContent = t; pzMsg.className = "feedback good";
      } else { pzMsg.textContent = "Failed: " + res.error; pzMsg.className = "feedback bad"; }
    });
  }

  // ----------------------------------------------------- Game Review (chess.com-style)
  function initGameDetail() {
    const data = JSON.parse(document.getElementById("game-data").textContent);
    const moves = data.moves;                 // one entry per half-move (ply)
    const evals = data.evals || [0];          // white-POV cp at each ply, index 0 = start
    const board = ChessBoard("#board", { orientation: data.orientation, coords: true });
    board.setInteractive(false);

    const $ = id => document.getElementById(id);
    const moveEls = Array.from(document.querySelectorAll("#movetable .mt-move"));
    let cur = 0;              // 0 = start; k = after k-th half-move
    let explaining = false;
    let playTimer = null;

    function clampCp(cp) { return Math.max(-1500, Math.min(1500, cp)); }
    function winProb(cp) {  // logistic win-probability for white
      if (cp >= 99000) return 1; if (cp <= -99000) return 0;
      return 1 / (1 + Math.pow(10, -clampCp(cp) / 400));
    }
    function evalStr(cp) {
      if (cp === null || cp === undefined) return "0.0";
      if (Math.abs(cp) >= 99000) return cp > 0 ? "M" : "-M";
      return (cp / 100).toFixed(Math.abs(cp) >= 1000 ? 1 : 2);
    }

    // Build the eval sparkline once; a marker rectangle is moved on navigation.
    function buildGraph() {
      const w = 300, h = 66, n = Math.max(1, evals.length - 1);
      const pts = evals.map((cp, i) => [(i / n) * w, (1 - winProb(cp)) * h]);
      const line = pts.map(p => p.join(",")).join(" ");
      const area = `0,${h} ` + line + ` ${w},${h}`;
      $("eval-graph").innerHTML =
        `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="eg-svg">
           <polygon points="${area}" class="eg-area"/>
           <polyline points="${line}" class="eg-line"/>
           <rect id="eg-marker" x="0" y="0" width="1.5" height="${h}" class="eg-marker"/>
         </svg>`;
    }
    function moveMarker() {
      const n = Math.max(1, evals.length - 1);
      const mk = $("eg-marker");
      if (mk) mk.setAttribute("x", ((cur / n) * 300).toFixed(1));
    }

    function setEvalBar(cp) {
      // eval-white = white's win share; CSS anchors it to white's end of the bar
      // (bottom normally, top when the board is flipped for a black game).
      $("eval-white").style.height = (winProb(cp) * 100).toFixed(1) + "%";
      $("eval-num").textContent = evalStr(cp);
    }

    function render() {
      const m = cur === 0 ? null : moves[cur - 1];
      // board
      board.setPosition(m ? m.fen_after : data.start_fen, data.orientation);
      const marks = {};
      if (m && m.move_uci) {
        const cls = ({ "q-blun": "mark-bad", "q-mist": "mark-bad", "q-inacc": "mark-bad" })[m.q_cls] || "mark-last";
        marks[m.move_uci.slice(0, 2)] = cls; marks[m.move_uci.slice(2, 4)] = cls;
      }
      board.setMarks(marks);

      // Best move drawn as a green arrow (on mistakes, or whenever "Explain" is on).
      const arrows = [];
      if (m && (explaining || ["q-blun", "q-mist", "q-inacc"].includes(m.q_cls))
          && m.best_move_uci && m.best_move_uci !== m.move_uci) {
        arrows.push({ from: m.best_move_uci.slice(0, 2), to: m.best_move_uci.slice(2, 4), cls: "arrow-good" });
      }
      board.setArrows(arrows);

      // eval bar + graph marker
      setEvalBar(evals[cur] ?? 0);
      moveMarker();

      // coach bubble
      if (!m) {
        $("cb-glyph").textContent = ""; $("cb-glyph").className = "cb-glyph";
        $("cb-title").textContent = "Starting position";
        $("cb-eval").textContent = "";
        $("cb-text").textContent = "Step through the game to review each move.";
      } else {
        $("cb-glyph").textContent = m.q_glyph; $("cb-glyph").className = "cb-glyph " + m.q_cls;
        const num = m.move_number + (m.side === "white" ? "." : "…");
        $("cb-title").innerHTML = `<span class="${m.q_cls}">${num} ${m.move_san}</span> — ${m.q_label}`;
        $("cb-eval").textContent = m.eval_after_str;
        $("cb-eval").className = "cb-eval " + ((evals[cur] ?? 0) >= 0 ? "for-white" : "for-black");
        $("cb-text").textContent = m.q_comment;
      }
      renderExplain(m);

      // move table highlight
      moveEls.forEach(el => el.classList.toggle("active", parseInt(el.dataset.ply, 10) === cur));
      const act = moveEls.find(el => parseInt(el.dataset.ply, 10) === cur);
      if (act) act.scrollIntoView({ block: "nearest" });
    }

    function renderExplain(m) {
      const box = $("cb-explain");
      if (!explaining || !m) { box.style.display = "none"; return; }
      box.style.display = "block";
      const swing = (cur > 0) ? (evals[cur] - evals[cur - 1]) : 0;
      let html = `Engine best: <strong>${m.best_move_san || "—"}</strong>. `;
      html += `Eval ${evalStr(evals[cur - 1])} → ${evalStr(evals[cur])} (${(swing / 100).toFixed(2)}). `;
      if (m.skill_area) html += `Theme: ${m.skill_area}${m.skill_subarea ? " / " + m.skill_subarea : ""}.`;
      box.innerHTML = html;
    }

    function go(n) { stopPlay(); cur = Math.max(0, Math.min(moves.length, n)); render(); }
    function nextMistake() {
      const next = (data.mistakePlies || []).find(p => p > cur);
      if (next !== undefined) go(next);
    }
    function stopPlay() { if (playTimer) { clearInterval(playTimer); playTimer = null; $("nav-play").textContent = "▶"; } }
    function togglePlay() {
      if (playTimer) { stopPlay(); return; }
      $("nav-play").textContent = "⏸";
      playTimer = setInterval(() => {
        if (cur >= moves.length) { stopPlay(); return; }
        cur += 1; render();
      }, 900);
    }

    $("nav-first").addEventListener("click", () => go(0));
    $("nav-prev").addEventListener("click", () => go(cur - 1));
    $("nav-next").addEventListener("click", () => go(cur + 1));
    $("nav-last").addEventListener("click", () => go(moves.length));
    $("nav-play").addEventListener("click", togglePlay);
    $("btn-next2").addEventListener("click", () => go(cur + 1));
    $("nav-mistake").addEventListener("click", (e) => { e.preventDefault(); nextMistake(); });
    $("btn-explain").addEventListener("click", () => { explaining = !explaining; render(); });
    moveEls.forEach(el => el.addEventListener("click", () => go(parseInt(el.dataset.ply, 10))));

    document.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { go(cur - 1); e.preventDefault(); }
      else if (e.key === "ArrowRight") { go(cur + 1); e.preventDefault(); }
      else if (e.key === "Home") { go(0); e.preventDefault(); }
      else if (e.key === "End") { go(moves.length); e.preventDefault(); }
      else if (e.key === "m" || e.key === "M") { nextMistake(); e.preventDefault(); }
    });

    buildGraph();
    render();
  }

  return { initPractice, initImport, initGameDetail };
})();
