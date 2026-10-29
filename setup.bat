@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo [1/4] Creating Python venv...
  python -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
)

echo [2/4] Installing Python dependencies...
call "venv\Scripts\activate.bat"
where uv >nul 2>nul
if %errorlevel%==0 (
  uv pip install -r requirements.txt
) else (
  python -m pip install -U pip
  pip install -r requirements.txt
)
if errorlevel 1 (
  echo [ERROR] Installing Python dependencies failed.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js not found. Install Node.js 18+ from https://nodejs.org
  echo         then run setup.bat again.
  pause
  exit /b 1
)

echo [3/4] Installing frontend dependencies...
pushd "%~dp0web"
call npm install
if errorlevel 1 (
  popd
  echo [ERROR] npm install failed.
  pause
  exit /b 1
)

echo [4/4] Building frontend...
call npm run build
if errorlevel 1 (
  popd
  echo [ERROR] Frontend build failed.
  pause
  exit /b 1
)
popd

echo.
echo Done. From now on just double-click start.bat.
pause