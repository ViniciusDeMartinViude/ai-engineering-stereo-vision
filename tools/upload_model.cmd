@echo off
rem Upload a trained YOLO model to the camera server's model library (no Python needed).
rem   upload_model.cmd best.pt -Name belt-v4 -Description "Nov 24"
rem   upload_model.cmd -List
rem Options and settings: see upload_model.ps1 (same folder).
rem -ExecutionPolicy Bypass applies to this one script run only; nothing on the PC is changed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0upload_model.ps1" %*
exit /b %ERRORLEVEL%
