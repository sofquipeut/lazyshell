@echo off
cd /d "%~dp0"
title Publication sur GitHub

echo ============================================
echo   Publication sur GitHub
echo ============================================
echo.

if not exist ".git\" goto pas_de_depot
if not exist ".gitignore" goto pas_de_gitignore

echo --- Garde-fou : verification des exclusions ---
git status --short | find /i "venv/" >nul
if not errorlevel 1 goto venv_expose
git status --short | find /i "installation.log" >nul
if not errorlevel 1 goto journal_expose
echo OK : ni venv ni journaux ne seraient envoyes.
echo.

echo --- Fichiers concernes ---
git status --short
echo.

set /p REPONSE="Enregistrer et publier ces fichiers ? (o/N) : "
if /i not "%REPONSE%"=="o" goto annule

echo.
git add .
git commit -m "Palier 0 complet : application, scripts d'outillage, notice"
if errorlevel 1 goto rien_a_faire

echo.
echo Envoi vers GitHub...
git push
if errorlevel 1 goto echec_push

echo.
echo ============================================
echo   Publie
echo ============================================
echo.
git log --oneline -1
echo.
pause
exit /b 0

:venv_expose
echo.
echo ARRET : le dossier venv apparait dans les fichiers a envoyer.
echo Le fichier .gitignore n'est pas pris en compte.
echo.
echo Verifiez qu'il s'appelle bien .gitignore, avec le point initial
echo et sans extension .txt ajoutee par le navigateur.
echo.
pause
exit /b 1

:journal_expose
echo.
echo ARRET : installation.log apparait dans les fichiers a envoyer.
echo Votre .gitignore est une ancienne version. Remplacez-le.
echo.
pause
exit /b 1

:pas_de_gitignore
echo ARRET : aucun fichier .gitignore dans ce dossier.
echo Sans lui, tout le dossier venv partirait sur GitHub.
echo.
echo Verifiez que le fichier n'a pas ete renomme en gitignore.txt
echo au telechargement.
echo.
pause
exit /b 1

:pas_de_depot
echo ARRET : pas de depot git dans ce dossier.
echo Lancez d'abord configurer_git.bat
echo.
pause
exit /b 1

:rien_a_faire
echo.
echo Rien de nouveau a enregistrer.
echo.
pause
exit /b 0

:echec_push
echo.
echo L'envoi a echoue. Message ci-dessus.
echo Si git parle d'une branche amont manquante, tapez :
echo    git push -u origin principal
echo.
pause
exit /b 1

:annule
echo.
echo Annule, rien n'a ete modifie.
echo.
pause
exit /b 0
