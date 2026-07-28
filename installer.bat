@echo off
cd /d "%~dp0"
title Installation detaillee

set JOURNAL=%~dp0installation.log
if exist "%JOURNAL%" del "%JOURNAL%"

echo ============================================
echo   Installation detaillee
echo ============================================
echo.
echo Tout est enregistre dans installation.log,
echo qui s'ouvrira dans le Bloc-notes a la fin.
echo.
echo N'executez PAS ce script en administrateur :
echo c'est inutile et cela peut brouiller les chemins.
echo.
pause
echo.

call :note "===== ENVIRONNEMENT ====="
call :executer py -0
call :executer py --version
call :executer where py
call :executer where python

set PYEXE=
py -3.13 --version >nul 2>&1
if not errorlevel 1 set PYEXE=py -3.13
if not "%PYEXE%"=="" goto choisi
py -3.12 --version >nul 2>&1
if not errorlevel 1 set PYEXE=py -3.12
if not "%PYEXE%"=="" goto choisi
py -3.11 --version >nul 2>&1
if not errorlevel 1 set PYEXE=py -3.11
if not "%PYEXE%"=="" goto choisi
set PYEXE=py

:choisi
call :note "Interpreteur retenu : %PYEXE%"
echo Interpreteur retenu : %PYEXE%
echo.

if exist "venv\Scripts\python.exe" goto venv_ok
echo Etape 1 sur 3 : creation de l'environnement virtuel...
call :note "===== CREATION DU VENV ====="
%PYEXE% -m venv venv >> "%JOURNAL%" 2>&1
if errorlevel 1 goto echec

:venv_ok
call :note "===== VERSION DU VENV ====="
call :executer "venv\Scripts\python.exe" --version
"venv\Scripts\python.exe" --version
echo.

echo Etape 2 sur 3 : mise a jour de pip...
call :note "===== PIP ====="
"venv\Scripts\python.exe" -m pip install --upgrade pip >> "%JOURNAL%" 2>&1

echo Etape 3 sur 3 : dependances du projet (requirements.txt,
echo le plus long, 2 a 4 minutes, wxPython en particulier)...
call :note "===== DEPENDANCES (requirements.txt) ====="
"venv\Scripts\python.exe" -m pip install -r requirements.txt >> "%JOURNAL%" 2>&1
if errorlevel 1 (
    call :note "ECHEC sur les dependances"
    goto echec
)

call :note "===== LISTE FINALE ====="
call :executer "venv\Scripts\python.exe" -m pip list

echo.
echo ============================================
echo   Installation reussie
echo ============================================
echo.
"venv\Scripts\python.exe" --version
echo.
echo Vous pouvez lancer lancer.bat
echo.
notepad "%JOURNAL%"
pause
exit /b 0

:echec
echo.
echo ============================================
echo   Echec
echo ============================================
echo.
echo Le Bloc-notes va s'ouvrir sur installation.log.
echo Cherchez la derniere section marquee ECHEC,
echo ou allez directement a la fin du fichier.
echo.
notepad "%JOURNAL%"
pause
exit /b 1

:note
echo.>> "%JOURNAL%"
echo %~1>> "%JOURNAL%"
exit /b 0

:executer
echo.>> "%JOURNAL%"
echo ^> %*>> "%JOURNAL%"
%* >> "%JOURNAL%" 2>&1
exit /b 0
