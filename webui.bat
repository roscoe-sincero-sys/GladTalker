@echo off

IF NOT EXIST venv (
python -m venv venv
) ELSE (
echo venv folder already exists, skipping creation...
)
call .\venv\Scripts\activate.bat

set PYTHON="venv\Scripts\python.exe"
echo venv %PYTHON%

%PYTHON% launcher.py

if errorlevel 1 (
    echo.
    echo Launch unsuccessful. Exiting.
    pause
)