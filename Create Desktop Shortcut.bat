@echo off
REM ============================================================
REM  Creates a "chessIQ" shortcut on your Desktop that launches
REM  chessIQ.exe from this folder. Run it once after unzipping.
REM  Works wherever this folder lives (the path is resolved now).
REM ============================================================
setlocal
set "APPDIR=%~dp0"
set "TARGET=%APPDIR%chessIQ.exe"

if not exist "%TARGET%" (
    echo ERROR: chessIQ.exe was not found next to this script.
    echo Keep this file inside the chessIQ folder and try again.
    pause
    exit /b 1
)

REM Resolve the REAL Desktop folder (handles OneDrive-redirected desktops).
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP=%%D"
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT=%DESKTOP%\chessIQ.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SHORTCUT); $s.TargetPath=$env:TARGET; $s.WorkingDirectory=$env:APPDIR; $s.IconLocation=$env:TARGET + ',0'; $s.Description='chessIQ - personal chess trainer'; $s.Save()"

if exist "%SHORTCUT%" (
    echo.
    echo  Done! A "chessIQ" shortcut is now on your Desktop.
    echo  Double-click it any time to start chessIQ.
) else (
    echo.
    echo  Could not create the shortcut. You can still run chessIQ.exe directly.
)
echo.
pause
