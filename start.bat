@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo [ERROR] Environment not installed. Run setup.bat first.
  pause
  exit /b 1
)

netstat -ano | findstr /C:":8765" | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
  echo [ERROR] Port 8765 is already in use by another process.
  echo         Close the old server window, or run in PowerShell:
  echo         Get-NetTCPConnection -LocalPort 8765 -State Listen ^| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  echo         Or start on another port:  set BK_PORT=8766  then run start.bat again
  pause
  exit /b 1
)

if not exist "web\dist\index.html" (
  echo Frontend not built yet, trying to build...
  pushd "%~dp0web"
  call npm install
  call npm run build
  popd
  if not exist "web\dist\index.html" (
    echo [ERROR] Frontend build failed. Run setup.bat to inspect the error.
    pause
    exit /b 1
  )
)

echo Starting server at http://127.0.0.1:8765
echo Close this window to stop the server.
"venv\Scripts\python.exe" -m server.main
pause