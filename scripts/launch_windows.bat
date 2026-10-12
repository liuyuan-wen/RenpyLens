@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."
set "RENPYLENS_SELECTED_PYTHON="

rem RENPYLENS_PYTHON accepts a full path to a specific interpreter.
if not defined RENPYLENS_PYTHON goto auto_detect
call :try_python "%RENPYLENS_PYTHON%"
goto python_selected

:auto_detect
for %%P in ("%CD%\.venv\Scripts\python.exe" "%CD%\venv\Scripts\python.exe" "%VIRTUAL_ENV%\Scripts\python.exe" "%CONDA_PREFIX%\python.exe") do call :try_python "%%~P"

rem Retain support for Conda installations outside the standard locations.
if defined CONDA_BAT for %%C in ("%CONDA_BAT%") do for %%R in ("%%~dpC..") do call :try_conda "%%~fR"
if defined CONDA_EXE for %%C in ("%CONDA_EXE%") do for %%R in ("%%~dpC." "%%~dpC..") do call :try_conda "%%~fR"
for %%C in (conda.exe conda.bat) do call :try_conda_command "%%~$PATH:C"
for %%R in ("%USERPROFILE%" "%LOCALAPPDATA%" "%ProgramData%" "%ProgramFiles%" "%SystemDrive%\") do (
    for %%D in (miniconda3 anaconda3 miniforge3) do call :try_conda "%%~R\%%D"
)
for %%P in (python.exe python3.exe) do call :try_python "%%~$PATH:P"
if defined RENPYLENS_SELECTED_PYTHON goto python_selected
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try_python "%%P"

:python_selected
if not defined RENPYLENS_SELECTED_PYTHON (
    echo [ERROR] Python 3.10+ was not found.
    echo Create .venv in this project, or set RENPYLENS_PYTHON to a Python executable.
    pause
    exit /b 1
)

rem Conda DLLs also need to be discoverable without an activated Prompt.
for %%P in ("%RENPYLENS_SELECTED_PYTHON%") do set "RENPYLENS_PYTHON_DIR=%%~dpP"
set "PATH=%RENPYLENS_PYTHON_DIR%;%RENPYLENS_PYTHON_DIR%Scripts;%RENPYLENS_PYTHON_DIR%Library\bin;%PATH%"
if "%RENPYLENS_ACTION%"=="build" goto build

if not exist "%RENPYLENS_PYTHON_DIR%pythonw.exe" goto run_console
rem Report import errors before handing off to Python's windowless launcher.
"%RENPYLENS_SELECTED_PYTHON%" -c "import sys; sys.path.insert(0, 'src'); import main"
if errorlevel 1 (
    echo [ERROR] RenpyLens could not start with "%RENPYLENS_SELECTED_PYTHON%".
    echo If dependencies are missing, install them with:
    echo "%RENPYLENS_SELECTED_PYTHON%" -m pip install -r requirements.txt
    pause
    exit /b 1
)
start "" /B "%RENPYLENS_PYTHON_DIR%pythonw.exe" "src\main.py" %*
exit /b %ERRORLEVEL%

:run_console
"%RENPYLENS_SELECTED_PYTHON%" "src\main.py" %*
set "RENPYLENS_EXIT_CODE=%ERRORLEVEL%"
if not "%RENPYLENS_EXIT_CODE%"=="0" pause
exit /b %RENPYLENS_EXIT_CODE%

:build
echo Python: "%RENPYLENS_SELECTED_PYTHON%"
"%RENPYLENS_SELECTED_PYTHON%" build.py %*
set "RENPYLENS_EXIT_CODE=%ERRORLEVEL%"
echo.
if "%RENPYLENS_EXIT_CODE%"=="0" (
    echo [OK] Build finished successfully.
) else (
    echo [ERROR] Build failed. Exit code: %RENPYLENS_EXIT_CODE%
)
pause
exit /b %RENPYLENS_EXIT_CODE%

:try_python
if defined RENPYLENS_SELECTED_PYTHON exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 exit /b 0
set "RENPYLENS_SELECTED_PYTHON=%~f1"
exit /b 0

:try_conda
call :try_python "%~1\envs\renpylens\python.exe"
call :try_python "%~1\python.exe"
exit /b 0

:try_conda_command
if "%~1"=="" exit /b 0
for %%R in ("%~dp1." "%~dp1..") do call :try_conda "%%~fR"
exit /b 0
