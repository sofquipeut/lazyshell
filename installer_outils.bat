@echo off
cd /d "%~dp0"
title Installation des outils - Terminal accessible

echo ============================================
echo   Installation des outils de developpement
echo ============================================
echo.
echo Ce script installe, s'ils sont absents :
echo   - Git pour Windows   (gestion de versions)
echo   - GitHub CLI         (creation du depot GitHub)
echo   - Visual Studio Code (editeur, optionnel)
echo   - Claude Code        (agent de developpement)
echo.
echo Il utilise winget, le gestionnaire de paquets integre a Windows.
echo Aucun assistant d'installation a parcourir.
echo.
pause
echo.

winget --version >nul 2>&1
if errorlevel 1 goto pas_de_winget

echo --------------------------------------------
echo  1 sur 4 : Git pour Windows
echo --------------------------------------------
git --version >nul 2>&1
if not errorlevel 1 (
    echo Deja installe, on passe.
) else (
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
)
echo.

echo --------------------------------------------
echo  2 sur 4 : GitHub CLI
echo --------------------------------------------
gh --version >nul 2>&1
if not errorlevel 1 (
    echo Deja installe, on passe.
) else (
    winget install --id GitHub.cli -e --accept-package-agreements --accept-source-agreements
)
echo.

echo --------------------------------------------
echo  3 sur 4 : Visual Studio Code
echo --------------------------------------------
code --version >nul 2>&1
if not errorlevel 1 (
    echo Deja installe, on passe.
) else (
    winget install --id Microsoft.VisualStudioCode -e --accept-package-agreements --accept-source-agreements
)
echo.

echo --------------------------------------------
echo  4 sur 4 : Claude Code
echo --------------------------------------------
claude --version >nul 2>&1
if not errorlevel 1 (
    echo Deja installe, on passe.
) else (
    echo Telechargement du script officiel depuis claude.ai...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
)
echo.

echo ============================================
echo   Installation terminee
echo ============================================
echo.
echo IMPORTANT : fermez cette fenetre et ouvrez-en une nouvelle
echo avant de continuer. Les nouveaux programmes ne sont visibles
echo qu'apres redemarrage de l'invite de commandes.
echo.
echo Etape suivante : lancez le fichier configurer_git.bat
echo.
pause
exit /b 0

:pas_de_winget
echo ERREUR : winget est introuvable sur ce systeme.
echo.
echo winget est fourni avec Windows 10 version 1809 et suivantes,
echo via l'application "Installateur d'application" du Microsoft Store.
echo Mettez-la a jour, puis relancez ce script.
echo.
pause
exit /b 1
