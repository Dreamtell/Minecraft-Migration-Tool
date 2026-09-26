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
rem  Usage: 打包_命令行.bat            (pause at the end)
rem         打包_命令行.bat nopause    (no pause, for scripts)
rem ============================================================
set "ROOT=%~dp0"
set "PROJ=%ROOT%Minecraft迁移工具"

cd /d "%PROJ%"
py -3.12 -m PyInstaller --noconfirm --onedir --windowed --uac-admin ^
  --icon "%PROJ%\1.ico" ^
  --add-data "%PROJ%\1.ico;." ^
  --additional-hooks-dir "%PROJ%" ^
  --hidden-import tkinterdnd2 ^
  --paths "%ROOT%_qt" ^
  --distpath "%ROOT%dist" ^
  --workpath "%ROOT%build" ^
  --specpath "%ROOT%build" ^
  app.py

echo.
if /i not "%1"=="nopause" pause
