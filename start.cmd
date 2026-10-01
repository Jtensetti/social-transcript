@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Kor setup.cmd forst.
  exit /b 1
)
.venv\Scripts\python.exe app.py
