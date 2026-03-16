@echo off
title GolfGame Dashboard
cd /d C:\Users\hozak\GolfGame

echo ============================================
echo   GolfGame Dashboard - Starting...
echo ============================================
echo.

REM フロントエンドビルドが存在しない場合は自動ビルド
if not exist "frontend\dist\index.html" (
    echo [INFO] Building frontend for the first time...
    cd frontend
    call npm install
    call npm run build
    cd ..
    echo [OK] Frontend build complete.
    echo.
)

echo [INFO] Starting server at http://localhost:8000
echo [INFO] Press Ctrl+C to stop the server.
echo.

REM 2秒後にブラウザを自動オープン
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"

REM FastAPIサーバー起動（フォアグラウンドで実行）
uv run uvicorn api.server:app --host 127.0.0.1 --port 8000
