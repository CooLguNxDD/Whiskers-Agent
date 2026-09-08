@echo off
REM Forward all arguments to the unified setup CLI, preferring the local venv.
set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  "%VENV_PY%" "%~dp0script\setup.py" %*
) else (
  python "%~dp0script\setup.py" %*
)
