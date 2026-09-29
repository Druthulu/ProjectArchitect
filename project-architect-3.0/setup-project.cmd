@echo off
rem setup-project.cmd -- set Project Architect up in one repository (3.11 T7). Double-click it and pick
rem the folder, or drop a folder onto it: it runs pa_install.py --project <folder> in this window and,
rem when double-clicked, keeps the window open at the end. Run install.cmd once per machine first.
setlocal
rem a double-click runs this file as `cmd /c "<path>\setup-project.cmd"` (see install.cmd)
echo %cmdcmdline% | find /i "%~nx0" >nul 2>nul && set "PA3_DOUBLECLICK=1"

set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=%%~C"
    )
)
if not defined PY goto :no_python

set "FOLDER=%~1"
if defined FOLDER goto :run
set "PICK=%TEMP%\pa3-setup-%RANDOM%.txt"
powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = 'Choose the repository to set up with Project Architect'; $d.ShowNewFolderButton = $false; if ($d.ShowDialog() -eq 'OK') { [IO.File]::WriteAllText('%PICK%', $d.SelectedPath) }"
if exist "%PICK%" (
    set /p FOLDER=<"%PICK%"
    del "%PICK%" >nul 2>nul
)
if not defined FOLDER goto :no_folder

:run
echo.
echo   Project Architect 3.0 -- setting up "%FOLDER%"
echo.
%PY% "%~dp0pa_install.py" --project "%FOLDER%"
exit /b %ERRORLEVEL%

:no_python
echo Python 3.12 or newer was not found. Run install.cmd first: it installs Python when needed.
if defined PA3_DOUBLECLICK pause
exit /b 1

:no_folder
echo No folder was chosen; nothing was changed.
if defined PA3_DOUBLECLICK pause
exit /b 1
