@echo off
cd /d "%~dp0"
title LazyShell

if not exist "venv\Scripts\python.exe" goto pas_installe

"venv\Scripts\python.exe" lazyshell.py %*
if errorlevel 1 goto erreur
exit /b 0

:pas_installe
echo ERREUR : l'environnement n'est pas installe.
echo Lancez d'abord le fichier installer.bat
echo.
pause
exit /b 1

:erreur
echo.
echo Le programme s'est arrete sur une erreur.
echo Ouvrez le fichier lazyshell.log pour en connaitre la cause.
echo.
pause
exit /b 1
