@echo off
cd /d "%~dp0"
title Terminal accessible

if not exist "venv\Scripts\python.exe" goto pas_installe

"venv\Scripts\python.exe" terminal_accessible.py %*
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
echo Ouvrez le fichier terminal.log pour en connaitre la cause.
echo.
pause
exit /b 1
