@echo off
rem install.cmd -- Windows bootstrap: find (or install) a Python 3.12+
rem interpreter, clone or pull the package source, then hand off every argument
rem to pa_install.py --root.  No other logic lives here; see pa_install.py --help.
rem
rem   install.cmd [--dry-run] [--yes] [--config-dir DIR] [--source URL] ...
setlocal
goto :main

:find_interpreter
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=%%~C"
    )
)
goto :eof

:parse_config_dir
set "CONFIG_DIR="
set "SOURCE="
set "SKIP_NEXT="
:parse_loop
if "%~1"=="" goto :parse_done
if defined SKIP_NEXT (
    set "SKIP_NEXT="
    shift
    goto :parse_loop
)
if "%~1"=="--config-dir" (
    set "CONFIG_DIR=%~2"
    set "SKIP_NEXT=1"
    shift
    shift
    goto :parse_loop
)
if "%~1"=="--source" (
    set "SOURCE=%~2"
    set "SKIP_NEXT=1"
    shift
    shift
    goto :parse_loop
)
shift
goto :parse_loop
:parse_done
if not defined CONFIG_DIR (
    if defined CLAUDE_CONFIG_DIR (
        set "CONFIG_DIR=%CLAUDE_CONFIG_DIR%"
    ) else (
        set "CONFIG_DIR=%USERPROFILE%\.claude"
    )
)
goto :eof

:main
rem 3.11 T6: a double-click runs this file as `cmd /c "<path>\install.cmd"`; the installer then keeps
rem the window open at the end, unless a shell shares the console (a PowerShell or bash run also uses cmd /c)
echo %cmdcmdline% | find /i "%~nx0" >nul 2>nul && set "PA3_DOUBLECLICK=1"
echo.
echo   Project Architect 3.0 -- installing for this machine.
echo   This sets PA3 up once for your Claude config; your repositories are not touched yet.
echo.
set "PY="
call :find_interpreter

if not defined PY (
    echo No Python 3.12+ interpreter found on PATH; installing via winget...
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    call :find_interpreter
)

if not defined PY (
    echo Could not find or install a Python 3.12+ interpreter.
    if defined PA3_DOUBLECLICK pause
    exit /b 1
)

echo Using interpreter: %PY%

call :parse_config_dir %*

rem Clone or pull the package source when git is available
where git >nul 2>nul
if errorlevel 1 (
    echo git not found on PATH; running the local installer without a clone.
    %PY% "%~dp0pa_install.py" --root --no-clone %*
    exit /b %ERRORLEVEL%
)

set "CLONE=%CONFIG_DIR%\pa3-src"
if not defined SOURCE (
    %PY% -c "import sys,os;sys.path.insert(0,os.path.join(r'%~dp0'));from pa import config;print(config.DEFAULTS['install']['source'])" 2>nul > "%TEMP%\pa3src.txt"
    set /p SOURCE=<"%TEMP%\pa3src.txt"
    del "%TEMP%\pa3src.txt" 2>nul
    if not defined SOURCE set "SOURCE=https://github.com/Druthulu/ProjectArchitect.git"
)

if exist "%CLONE%\.git" (
    git -C "%CLONE%" fetch --quiet >nul 2>nul
    git -C "%CLONE%" pull --ff-only >nul 2>nul
) else (
    git clone -c core.autocrlf=false "%SOURCE%" "%CLONE%" >nul 2>nul
)

if exist "%CLONE%\project-architect-3.0\pa_install.py" (
    %PY% "%CLONE%\project-architect-3.0\pa_install.py" --root %*
) else (
    %PY% "%~dp0pa_install.py" --root --no-clone %*
)
exit /b %ERRORLEVEL%
