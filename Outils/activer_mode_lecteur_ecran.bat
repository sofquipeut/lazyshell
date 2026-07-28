@echo off
cd /d "%~dp0"
title Mode lecteur d'ecran - Claude Code

echo ============================================
echo   Activer le mode lecteur d'ecran
echo ============================================
echo.
echo Ajoute "axScreenReader" a votre fichier de reglages
echo Claude Code, sans toucher au reste. Une sauvegarde
echo est ecrite avant toute modification.
echo.
echo Le mode s'appliquera alors partout, sans avoir a
echo passer l'option a chaque lancement.
echo.
pause
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0activer_mode_lecteur_ecran.ps1"

echo.
echo ============================================
echo.
echo Verifiez en lancant claude : la premiere ligne
echo doit annoncer Screen Reader Mode: on via settings
echo.
pause
