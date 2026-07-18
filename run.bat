@echo off
echo Starting Suneo Island...
where python >nul 2>nul || (echo Python not found in PATH & pause & exit /b 1)
pip show waitress >nul 2>nul || (echo Installing production server ^(waitress^)... & pip install -q waitress requests flask)
echo Open http://localhost:5000 in your browser
python server.py
pause
