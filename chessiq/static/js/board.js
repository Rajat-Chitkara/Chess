/*
 * board.js — a tiny dependency-free chess board.
 *
 * Renders a position from a FEN, supports click-to-move, and can apply UCI
 * moves to the display. It does NOT judge legality or solutions — the Flask
 * server (python-chess) is the single source of truth. This just draws the
 * board and reports the from/to squares the user clicked.
 */
(function (global) {
  const FILES = ["a", "b", "c", "d", "e", "f", "g", "h"];
  const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };

  function parseFen(fen) {
    // Returns { pieces: {square: 'wP'...}, turn: 'w'|'b' }
    const [placement, turn] = fen.split(" ");
    const pieces = {};
    const rows = placement.split("/");
    for (let r = 0; r < 8; r++) {
      let file = 0;
      for (const ch of rows[r]) {
        if (/\d/.test(ch)) { file += parseInt(ch, 10); continue; }
        const color = ch === ch.toUpperCase() ? "w" : "b";
        const sq = FILES[file] + (8 - r);
        pieces[sq] = color + ch.toLowerCase();
        file++;
      }
    }
    return { pieces, turn };
  }

  function Board(selector, opts) {
    const el = typeof selector === "string" ? document.querySelector(selector) : selector;
    let pieces = {};
    let turn = "w";
    let orientation = opts.orientation === "black" ? "black" : "white";
    let interactive = true;
    let selected = null;
    let lastMove = null;
    let marks = {};   // square -> extra css class, e.g. {e2: "mark-good"}
    let arrows = [];  // [{from, to, cls}] drawn as an SVG overlay
    const coords = opts.coords !== false;   // show file/rank labels by default
    const onMove = opts.onMove || function () {};

    function squareList() {
      // top-to-bottom, left-to-right in display order
      const ranks = orientation === "white" ? [8, 7, 6, 5, 4, 3, 2, 1] : [1, 2, 3, 4, 5, 6, 7, 8];
      const files = orientation === "white" ? FILES : [...FILES].reverse();
      const list = [];
      for (const rank of ranks) for (const f of files) list.push(f + rank);
      return list;
    }

    function render() {
      el.innerHTML = "";
      const list = squareList();
      const posMap = {};   // square -> [x, y] centre in an 8x8 viewBox
      list.forEach((sq, i) => {
        const file = FILES.indexOf(sq[0]);
        const rank = parseInt(sq[1], 10);
        const r = Math.floor(i / 8), c = i % 8;
        posMap[sq] = [c + 0.5, r + 0.5];
        const div = document.createElement("div");
        div.className = "sq " + (((file + rank) % 2 === 0) ? "dark" : "light");
        if (sq === selected) div.classList.add("sel");
        if (lastMove && (sq === lastMove.slice(0, 2) || sq === lastMove.slice(2, 4))) div.classList.add("last");
        if (marks[sq]) div.classList.add(marks[sq]);
        div.dataset.sq = sq;
        const p = pieces[sq];
        if (p) {
          const span = document.createElement("span");
          span.className = "piece " + p[0];
          span.textContent = GLYPH[p[1]];
          div.appendChild(span);
        }
        if (coords && c === 0) {   // rank number on the left edge
          const rk = document.createElement("span");
          rk.className = "coord rank";
          rk.textContent = sq[1];
          div.appendChild(rk);
        }
        if (coords && r === 7) {   // file letter on the bottom edge
          const fl = document.createElement("span");
          fl.className = "coord file";
          fl.textContent = sq[0];
          div.appendChild(fl);
        }
        div.addEventListener("click", () => onSquareClick(sq));
        el.appendChild(div);
      });
      drawArrows(posMap);
    }

    function drawArrows(posMap) {
      if (!arrows.length) return;
      const NS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 8 8");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("class", "board-arrows");

      const defs = document.createElementNS(NS, "defs");
      const marker = document.createElementNS(NS, "marker");
      marker.setAttribute("id", "ar-head");
      marker.setAttribute("viewBox", "0 0 10 10");
      marker.setAttribute("refX", "8"); marker.setAttribute("refY", "5");
      marker.setAttribute("markerWidth", "3.5"); marker.setAttribute("markerHeight", "3.5");
      marker.setAttribute("orient", "auto");
      const mpath = document.createElementNS(NS, "path");
      mpath.setAttribute("d", "M0,1 L9,5 L0,9 z");
      mpath.setAttribute("fill", "rgba(74,168,82,0.9)");
      marker.appendChild(mpath); defs.appendChild(marker); svg.appendChild(defs);

      arrows.forEach(a => {
        const p1 = posMap[a.from], p2 = posMap[a.to];
        if (!p1 || !p2) return;
        let [x1, y1] = p1; let [x2, y2] = p2;
        const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;
        x1 += ux * 0.18; y1 += uy * 0.18;   // start just outside the source centre
        x2 -= ux * 0.45; y2 -= uy * 0.45;   // stop short so the head tip lands on the target
        const line = document.createElementNS(NS, "line");
        line.setAttribute("x1", x1); line.setAttribute("y1", y1);
        line.setAttribute("x2", x2); line.setAttribute("y2", y2);
        line.setAttribute("class", a.cls || "arrow-good");
        line.setAttribute("marker-end", "url(#ar-head)");
        svg.appendChild(line);
      });
      el.appendChild(svg);
    }

    function onSquareClick(sq) {
      if (!interactive) return;
      const moverColor = turn;
      const p = pieces[sq];
      if (selected === null) {
        if (p && p[0] === moverColor) { selected = sq; render(); }
        return;
      }
      if (sq === selected) { selected = null; render(); return; }
      if (p && p[0] === moverColor) { selected = sq; render(); return; }  // reselect own piece
      // attempt move selected -> sq
      let uci = selected + sq;
      const movingPiece = pieces[selected];
      const destRank = parseInt(sq[1], 10);
      if (movingPiece && movingPiece[1] === "p" && (destRank === 8 || destRank === 1)) {
        uci += "q";  // auto-queen; covers the large majority of puzzles
      }
      const from = selected;
      selected = null;
      render();
      onMove(uci, from, sq);
    }

    function applyUci(uci) {
      const from = uci.slice(0, 2), to = uci.slice(2, 4), promo = uci.slice(4, 5);
      const p = pieces[from];
      if (!p) return;
      delete pieces[from];
      // en passant: pawn moves diagonally to an empty square -> remove captured pawn
      if (p[1] === "p" && from[0] !== to[0] && !pieces[to]) {
        const capturedSq = to[0] + from[1];
        delete pieces[capturedSq];
      }
      pieces[to] = promo ? p[0] + promo : p;
      // castling: move the rook too
      if (p[1] === "k") {
        if (from === "e1" && to === "g1") { pieces["f1"] = pieces["h1"]; delete pieces["h1"]; }
        if (from === "e1" && to === "c1") { pieces["d1"] = pieces["a1"]; delete pieces["a1"]; }
        if (from === "e8" && to === "g8") { pieces["f8"] = pieces["h8"]; delete pieces["h8"]; }
        if (from === "e8" && to === "c8") { pieces["d8"] = pieces["a8"]; delete pieces["a8"]; }
      }
      turn = turn === "w" ? "b" : "w";
      lastMove = uci;
      render();
    }

    function setPosition(fen, orient) {
      const parsed = parseFen(fen);
      pieces = parsed.pieces;
      turn = parsed.turn;
      if (orient) orientation = orient === "black" ? "black" : "white";
      selected = null;
      lastMove = null;
      marks = {};
      arrows = [];
      render();
    }

    function setInteractive(v) { interactive = v; if (!v) { selected = null; render(); } }

    function highlightHint(square) {
      render();
      const cell = el.querySelector(`[data-sq="${square}"]`);
      if (cell) cell.classList.add("hint");
    }

    function setMarks(obj) { marks = obj || {}; render(); }
    function setArrows(list) { arrows = list || []; render(); }

    if (opts.fen) setPosition(opts.fen);
    else render();

    return { setPosition, applyUci, setInteractive, highlightHint, setMarks, setArrows,
             get turn() { return turn; } };
  }

  global.ChessBoard = Board;
})(window);
