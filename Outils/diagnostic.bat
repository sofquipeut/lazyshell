@echo off
cd /d "%~dp0"
title Diagnostic des outils

echo ============================================
echo   Diagnostic
echo ============================================
echo.

echo --- Claude Code sur le PATH ---
where claude 2>nul
if errorlevel 1 echo INTROUVABLE sur le PATH
echo.

echo --- Presence du binaire a l'emplacement officiel ---
if exist "%USERPROFILE%\.local\bin\claude.exe" (
    echo TROUVE : %USERPROFILE%\.local\bin\claude.exe
    echo Le programme est installe. C'est le PATH qui est en cause.
) else (
    echo ABSENT de %USERPROFILE%\.local\bin
    echo L'installation n'a pas abouti.
)
echo.

echo --- Le dossier est-il dans le PATH utilisateur ---
echo %PATH% | find /i "%USERPROFILE%\.local\bin" >nul
if errorlevel 1 (
    echo NON : le dossier .local\bin n'est pas dans le PATH de cette session.
) else (
    echo OUI : le dossier est bien dans le PATH.
)
echo.

echo --- Autres outils ---
for %%O in (git gh code python py winget) do call :verifier %%O
echo.

echo ============================================
echo   Que faire
echo ============================================
echo.
echo Si le binaire est TROUVE mais absent du PATH :
echo   lancez claude_ici.bat, il fonctionne sans le PATH.
echo.
echo Si le binaire est ABSENT, tapez dans cette fenetre :
echo   winget install Anthropic.ClaudeCode
echo puis rouvrez une fenetre.
echo.
pause
exit /b 0

:verifier
%1 --version >nul 2>&1
if errorlevel 1 (echo %1 : absent) else (echo %1 : present)
exit /b 0
