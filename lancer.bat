@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" goto pas_installe

rem pythonw.exe (et non python.exe) : version sans console de Python,
rem lancee par start pour que ce script se termine aussitot. Avec
rem python.exe, la fenetre de console restait ouverte pendant toute la
rem duree de la session. Une erreur au demarrage n'est plus affichee
rem ici : elle apparait dans une boite de dialogue et dans lazyshell.log.
start "" "venv\Scripts\pythonw.exe" lazyshell.py %*
exit /b 0

:pas_installe
echo ERREUR : l'environnement n'est pas installe.
echo Lancez d'abord le fichier installer.bat
echo.
pause
exit /b 1
