@echo off
cd /d "%~dp0"
title Compilation - LazyShell

if not exist "venv\Scripts\python.exe" goto pas_installe

set NOM_DLL=
if exist "nvdaControllerClient64.dll" set NOM_DLL=nvdaControllerClient64.dll
if exist "nvdaControllerClient.dll"   set NOM_DLL=nvdaControllerClient.dll

echo ============================================
echo   Production de l'executable
echo ============================================
echo.
if "%NOM_DLL%"=="" (
    echo ATTENTION : aucun client controleur NVDA trouve dans ce dossier.
    echo L'executable fonctionnera mais sans annonce automatique.
    echo.
)
echo Comptez 1 a 3 minutes. Patientez.
echo.

rem PyInstaller supprime entierement dist\LazyShell avant de le
rem reconstruire : sans cette sauvegarde, chaque recompilation effacerait
rem les profils SSH, la cle d'hote memorisee, les commandes enregistrees
rem et les reglages de l'utilisateur qui vivent dans ce meme dossier.
set SAUVEGARDE=%TEMP%\lazyshell_sauvegarde_compilation
if exist "%SAUVEGARDE%" rmdir /s /q "%SAUVEGARDE%"
if exist "dist\LazyShell" (
    mkdir "%SAUVEGARDE%" >nul
    for %%F in (settings.json ssh_profiles.json known_hosts commands.json) do (
        if exist "dist\LazyShell\%%F" copy /y "dist\LazyShell\%%F" "%SAUVEGARDE%\%%F" >nul
    )
)

"venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --windowed ^
  --clean ^
  --name "LazyShell" ^
  lazyshell.py

if errorlevel 1 goto echec

rem Mode dossier (--onedir, implicite en l'absence de --onefile depuis
rem PyInstaller 6) : l'exe et les fichiers de donnees (settings.json,
rem ssh_profiles.json, known_hosts, commands.json, lazyshell.log) vivent
rem directement a cote les uns des autres dans dist\LazyShell, les
rem bibliotheques Python dans le sous-dossier _internal. La DLL doit
rem etre a cote de l'exe, jamais dans _internal : l'appli ne cherche que
rem dans le dossier de l'exe (voir Voix._candidats).
if not "%NOM_DLL%"=="" copy /y "%NOM_DLL%" "dist\LazyShell\%NOM_DLL%" >nul

if exist "%SAUVEGARDE%" (
    for %%F in (settings.json ssh_profiles.json known_hosts commands.json) do (
        if exist "%SAUVEGARDE%\%%F" copy /y "%SAUVEGARDE%\%%F" "dist\LazyShell\%%F" >nul
    )
    rmdir /s /q "%SAUVEGARDE%"
)

echo.
echo ============================================
echo   Compilation terminee
echo ============================================
echo.
echo Le dossier dist\LazyShell contient l'application complete.
echo C'est ce dossier entier qu'il faut distribuer ou copier ailleurs,
echo pas seulement LazyShell.exe : il a besoin du sous-dossier _internal
echo a cote de lui pour fonctionner.
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
