@echo off
rem Doppio clic per installare: aggira la ExecutionPolicy solo per questo script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
