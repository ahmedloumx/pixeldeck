@echo off
rem Windows: double-click this file to start PixelDeck.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 pixeldeck.py %*) || (python pixeldeck.py %*)
