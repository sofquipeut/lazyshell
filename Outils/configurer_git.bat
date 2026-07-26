@echo off
cd /d "%~dp0"
title Configuration de Git

echo ============================================
echo   Configuration de Git et du depot
echo ============================================
echo.

git --version >nul 2>&1
if errorlevel 1 goto pas_de_git

set NOM=
for /f "delims=" %%n in ('git config --global user.name 2^>nul') do set NOM=%%n
if defined NOM goto nom_ok
echo Git a besoin de savoir qui signe les modifications.
echo.
set /p NOM="Votre nom (par exemple Sofian) : "
git config --global user.name "%NOM%"
:nom_ok

set MEL=
for /f "delims=" %%m in ('git config --global user.email 2^>nul') do set MEL=%%m
if defined MEL goto mel_ok
set /p MEL="Votre adresse de courriel GitHub : "
git config --global user.email "%MEL%"
:mel_ok

echo.
echo Identite configuree :
git config --global user.name
git config --global user.email
echo.

if exist ".git\" goto depot_existant

echo Creation du depot local...
git init -b principal
git add .
git commit -m "Palier 0 : coquille de l'interface, blocs, couche vocale"
echo.
echo Premier enregistrement effectue.
goto suite

:depot_existant
echo Le depot existe deja dans ce dossier.

:suite
echo.
echo ============================================
echo   Depot local pret
echo ============================================
echo.
echo Pour le publier sur GitHub, dans une nouvelle fenetre :
echo.
echo     gh auth login
echo.
echo Repondez GitHub.com, puis HTTPS, puis authentification par
echo navigateur. Une page s'ouvre, vous y collez le code affiche.
echo.
echo Ensuite, revenu dans ce dossier :
echo.
echo     gh repo create terminal-accessible --private --source=. --push
echo.
pause
exit /b 0

:pas_de_git
echo ERREUR : Git est introuvable.
echo Lancez d'abord installer_outils.bat, puis ROUVREZ une fenetre.
echo.
pause
exit /b 1
