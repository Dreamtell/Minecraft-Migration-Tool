@echo off
chcp 65001 >nul
rem ============================================================
rem  One-click build for the Minecraft migration tool.
rem
rem  Why --onedir and --paths _qt:
rem    * PySide6 lives in the repo's _qt folder (not site-packages),
rem      so PyInstaller cannot see it unless we pass --paths.
rem      Without Qt the splash falls back to the in-process Tk one,
rem      which freezes for up to ~245ms while the UI is built.
rem    * --onefile re-extracts the WHOLE archive every time the app
rem      re-launches itself (the splash child does exactly that).
rem      With Qt inside that takes seconds, the parent gives up after
rem      1.2s and kills it -> no Qt splash AND a wasted extraction.
rem
rem  Paths are all absolute (%~dp0) because --specpath changes how
rem  relative --add-data / --icon are resolved.
rem
rem  NOTE: this file must stay UTF-8 (no BOM) and CRLF, otherwise
rem  the Chinese folder name below gets mangled by cmd.
rem
rem  Usage: 打包_命令行.bat                 (pause at the end)
rem         打包_命令行.bat nopause         (no pause, for scripts)
rem         打包_命令行.bat keyonly         (only write/refresh the built-in key)
rem         打包_命令行.bat keyonly nopause
rem
rem  Built-in CurseForge API key (see utils/secrets.py):
rem    The key is NEVER in this repository. It is injected at build time
rem    from the env var MCTOOL_CF_KEY by 生成内置Key.py into a gitignored
rem    module (Minecraft迁移工具\utils\_build_seed.py), stored as an
rem    obfuscated BLOB (XOR + base64) so that `strings` on the built exe
rem    does not reveal it. That is NOT encryption - see utils/secrets.py.
rem    The Git URL a reviewer clicks therefore stays clean, while the
rem    released exe ships with a working default.
rem    Set it for one build:
rem        set MCTOOL_CF_KEY=your-key-here
rem        打包_命令行.bat
rem    Not set -> the build has no built-in key; users can still paste
rem    their own key in Settings -> Migration & Categories.
rem ============================================================
set "ROOT=%~dp0"
set "PROJ=%ROOT%Minecraft迁移工具"
set "SEED_FILE=%PROJ%\utils\_build_seed.py"

rem ---- arguments (nopause may be %1 or %2 now that keyonly exists) ----
set "NOPAUSE="
set "KEYONLY="
if /i "%1"=="nopause" set "NOPAUSE=1"
if /i "%2"=="nopause" set "NOPAUSE=1"
if /i "%1"=="keyonly" set "KEYONLY=1"

rem ---- inject (or clear) the built-in CurseForge key ----
rem The helper writes an obfuscated BLOB from MCTOOL_CF_KEY, or deletes a stale
rem seed file when the variable is not set (so a previous build can never leak
rem into this one). It also self-checks that encode/decode round-trips.
py -3.12 "%ROOT%生成内置Key.py"
if errorlevel 1 echo [key] WARNING: key injection failed -- continuing anyway

if defined KEYONLY (
  echo [key] keyonly: stopping before PyInstaller.
  if not defined NOPAUSE pause
  exit /b 0
)

cd /d "%PROJ%"
py -3.12 -m PyInstaller --noconfirm --onedir --windowed --uac-admin ^
  --icon "%PROJ%\1.ico" ^
  --add-data "%PROJ%\1.ico;." ^
  --additional-hooks-dir "%PROJ%" ^
  --hidden-import tkinterdnd2 ^
  --exclude-module numpy ^
  --exclude-module cv2 ^
  --paths "%ROOT%_qt" ^
  --distpath "%ROOT%dist" ^
  --workpath "%ROOT%build" ^
  --specpath "%ROOT%build" ^
  app.py

rem The key is already inside the bundle by now; do not leave it in the
rem source tree (and .gitignore is the second line of defence).
if exist "%BUILTIN_FILE%" del /q "%BUILTIN_FILE%"

echo.
if not defined NOPAUSE pause
