@echo off
REM ============================================================
REM  Build chessIQ into a standalone Windows app (no Python
REM  needed to RUN it). Produces dist\chessIQ\ with chessIQ.exe
REM  and a bundled Stockfish. Zip that folder and share it.
REM
REM  Run this once on your machine:  build.bat
REM ============================================================
setlocal

echo [1/4] Installing build + runtime dependencies...
python -m pip install -r requirements.txt pyinstaller || goto :error

echo [2/4] Ensuring Stockfish is present (stockfish\stockfish.exe)...
if not exist "stockfish\stockfish.exe" (
    if exist "C:\stockfish\stockfish.exe" (
        mkdir stockfish 2>nul
        copy /Y "C:\stockfish\stockfish.exe" "stockfish\stockfish.exe" >nul
        echo     copied from C:\stockfish\stockfish.exe
    ) else (
        echo.
        echo   ERROR: stockfish\stockfish.exe not found.
        echo   Download Stockfish from https://stockfishchess.org/download/
        echo   and place the .exe at:  stockfish\stockfish.exe
        echo.
        goto :error
    )
)

echo [3/4] Building with PyInstaller...
pyinstaller --noconfirm --clean --name chessIQ --onedir --console ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "schema.sql;." ^
    --add-data "sample.pgn;." ^
    desktop.py || goto :error

echo [4/4] Bundling Stockfish next to the exe...
xcopy /E /I /Y "stockfish" "dist\chessIQ\stockfish" >nul || goto :error

echo.
echo ============================================================
echo  Done!  Share the folder:  dist\chessIQ
echo  (zip it; friends unzip and double-click chessIQ.exe)
echo ============================================================
goto :eof

:error
echo.
echo  Build FAILED. See the messages above.
exit /b 1
