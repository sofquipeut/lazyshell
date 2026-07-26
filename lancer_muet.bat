@echo off
cd /d "%~dp0"
title Terminal accessible - mode muet
echo Lancement en mode muet : aucune annonce ne sera envoyee a NVDA.
echo.
call "%~dp0lancer.bat" --muet
