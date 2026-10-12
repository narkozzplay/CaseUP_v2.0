@echo off
setlocal
chcp 65001 >nul

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

for %%I in ("%PROJECT_ROOT%") do set "PROJECT_PARENT=%%~dpI"
if "%PROJECT_PARENT:~-1%"=="\" set "PROJECT_PARENT=%PROJECT_PARENT:~0,-1%"

set "BUILD_DIR=%PROJECT_PARENT%\builds"

if not exist "%BUILD_DIR%" (
  mkdir "%BUILD_DIR%"
  if errorlevel 1 (
    echo [ERROR] Failed to create build directory: %BUILD_DIR%
    exit /b 1
  )
)

pushd "%PROJECT_ROOT%" >nul

if not exist "%PROJECT_ROOT%\VERSION.txt" (
  echo [ERROR] VERSION.txt not found: %PROJECT_ROOT%\VERSION.txt
  popd >nul
  exit /b 1
)

if exist "%PROJECT_ROOT%\.caseup_ignore.txt" (
  set "IGNORE_FILE=%PROJECT_ROOT%\.caseup_ignore.txt"
) else if exist "%PROJECT_ROOT%\.caseup-archive-ignore" (
  set "IGNORE_FILE=%PROJECT_ROOT%\.caseup-archive-ignore"
) else (
  echo [ERROR] Ignore file not found.
  echo [ERROR] Checked:
  echo   %PROJECT_ROOT%\.caseup_ignore.txt
  echo   %PROJECT_ROOT%\.caseup-archive-ignore
  popd >nul
  exit /b 1
)

py -3 "%PROJECT_ROOT%\tools\make_caseup_archive.py" --root "%PROJECT_ROOT%" --version-file "%PROJECT_ROOT%\VERSION.txt" --ignore-file "%IGNORE_FILE%" --output-dir "%BUILD_DIR%"
set "EXIT_CODE=%ERRORLEVEL%"

popd >nul

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] Archive build failed.
  exit /b %EXIT_CODE%
)

echo.
echo [OK] Build directory        : %BUILD_DIR%
echo [OK] Archive build completed.
exit /b 0