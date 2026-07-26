@echo off
cd /d "%~dp0"
title Compilation - Terminal accessible

if not exist "venv\Scripts\python.exe" goto pas_installe

set OPTION_DLL=
if exist "nvdaControllerClient64.dll" set OPTION_DLL=--add-binary "nvdaControllerClient64.dll;."
if exist "nvdaControllerClient.dll"   set OPTION_DLL=--add-binary "nvdaControllerClient.dll;."

echo ============================================
echo   Production de l'executable
echo ============================================
echo.
if "%OPTION_DLL%"=="" (
    echo ATTENTION : aucun client controleur NVDA trouve dans ce dossier.
    echo L'executable fonctionnera mais sans annonce automatique.
    echo.
)
echo Comptez 1 a 3 minutes. Patientez.
echo.

"venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --clean ^
  --name "TerminalAccessible" ^
  %OPTION_DLL% ^
  terminal_accessible.py

if errorlevel 1 goto echec

echo.
echo ============================================
echo   Compilation terminee
echo ============================================
echo.
echo L'executable se trouve dans le sous-dossier dist,
echo sous le nom TerminalAccessible.exe
echo.
echo Rappel : un executable non signe est frequemment mis en
echo quarantaine par Windows Defender. Si le fichier disparait,
echo ajoutez une exclusion pour ce dossier.
echo.
pause
exit /b 0

:pas_installe
echo ERREUR : l'environnement n'est pas installe.
echo Lancez d'abord le fichier installer.bat
echo.
pause
exit /b 1

:echec
echo.
echo La compilation a echoue. Voir les messages ci-dessus.
echo.
pause
exit /b 1
