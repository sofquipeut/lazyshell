@echo off
cd /d "%~dp0"
title Reparation du PATH

echo ============================================
echo   Ajout de Claude Code au PATH
echo ============================================
echo.
echo Ce script modifie uniquement votre PATH utilisateur.
echo Aucun droit administrateur n'est necessaire.
echo Une sauvegarde est ecrite avant toute modification.
echo.
pause
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0reparer_path.ps1"

echo.
echo ============================================
echo.
echo Fermez cette fenetre et ouvrez-en une NOUVELLE,
echo puis tapez :  claude --version
echo.
echo Si la commande reste inconnue, fermez puis rouvrez
echo votre session Windows : certaines applications ne
echo relisent le PATH qu'a l'ouverture de session.
echo.
pause
