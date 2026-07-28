@echo off
cd /d "%~dp0"
title Claude Code - LazyShell

rem Lance Claude Code dans le dossier du projet.
rem Fonctionne meme si le PATH ne contient pas claude.exe.
rem
rem Le mode lecteur d'ecran n'est PAS passe en option ici : il vient du
rem fichier %USERPROFILE%\.claude\settings.json (voir le script
rem outils\activer_mode_lecteur_ecran.bat). La premiere ligne de session
rem doit annoncer : Screen Reader Mode: on via settings
rem
rem Si elle annonce autre chose, ou rien, relancez ce fichier avec
rem l'argument secours :   claude_ici.bat secours

set CLAUDE_EXE=
set OPTIONS=

if /i "%~1"=="secours" (
    set OPTIONS=--ax-screen-reader
    shift
)

for /f "delims=" %%c in ('where claude 2^>nul') do set CLAUDE_EXE=%%c
if defined CLAUDE_EXE goto lancer

if exist "%USERPROFILE%\.local\bin\claude.exe" set CLAUDE_EXE=%USERPROFILE%\.local\bin\claude.exe
if defined CLAUDE_EXE goto lancer

echo Claude Code est introuvable.
echo.
echo Lancez diagnostic.bat pour savoir pourquoi, ou installez-le avec :
echo    winget install Anthropic.ClaudeCode
echo.
pause
exit /b 1

:lancer
echo Lancement depuis : %CLAUDE_EXE%
echo Dossier de travail : %CD%
echo.
"%CLAUDE_EXE%" %OPTIONS% %*
