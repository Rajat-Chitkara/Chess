"""
desktop.py — entry point for the standalone chessIQ app.

Runs the Flask app on a local production server (waitress) inside one long-lived
process and opens the browser automatically. This is what PyInstaller packages
into chessIQ.exe. Because it's a normal always-running process, Stockfish and the
background analysis thread work exactly like they do when you run it from source.

Run from source:  python desktop.py
Frozen:           double-click chessIQ.exe
"""

import socket
import threading
import webbrowser

from waitress import serve

from app import app  # importing runs init_db() + one-time score compute

HOST = "127.0.0.1"


def _free_port(preferred=5000):
    """Use 5000 if available, otherwise let the OS pick a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, preferred))
            return preferred
        except OSError:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                s2.bind((HOST, 0))
                return s2.getsockname()[1]


def main():
    port = _free_port(5000)
    url = f"http://{HOST}:{port}/"
    # open the browser shortly after the server starts accepting connections
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print("=" * 52)
    print(f"  chessIQ is running at {url}")
    print("  Keep this window open while you use the app.")
    print("  Close this window to quit.")
    print("=" * 52)
    serve(app, host=HOST, port=port, threads=8)


if __name__ == "__main__":
    main()
