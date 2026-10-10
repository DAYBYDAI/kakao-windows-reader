@echo off
"%~dp0..\tool\.venv\Scripts\python.exe" "%~dp0..\tool\entrypoint.py" %*
exit /b %errorlevel%
