@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Please contact the project maintainer.
    pause
    exit /b 1
)

echo Starting the citation verification website...
echo Keep this window open while using the website.
echo Press Ctrl+C in this window to stop the website.
echo.

".venv\Scripts\python.exe" -m streamlit run app.py --server.headless false --browser.gatherUsageStats false

pause

