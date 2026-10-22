@echo off
rem setup-external.cmd -- set up the external coders on this machine (3.3 T2). Double-click it: it asks for
rem a backend URL, its key and model ids, tests every model and saves the config only when all of them
rem answer (tools\pa_ext.py setup), keeping the window open at the end. Run install.cmd once per machine first.
setlocal
rem a double-click runs this file as `cmd /c "<path>\setup-external.cmd"` (see install.cmd)
echo %cmdcmdline% | find /i "%~nx0" >nul 2>nul && set "PA3_DOUBLECLICK=1"

set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=%%~C"
    )
)
if not defined PY goto :no_python

%PY% "%~dp0tools\pa_ext.py" setup
exit /b %ERRORLEVEL%

:no_python
echo Python 3.12 or newer was not found. Run install.cmd first: it installs Python when needed.
if defined PA3_DOUBLECLICK pause
exit /b 1
