@echo off
chcp 936 >nul
title Education Warning System
echo.
echo ========================================
echo   Education Warning System v1.0
echo   (FastAPI Backend + Vue 3 Frontend)
echo ========================================
echo.

REM ---------- Backend Service ----------
echo [1/2] Starting FastAPI backend (port 9090)...
start "FastAPI-Backend" cmd /k "chcp 936 >nul & cd /d %~dp0server & python main.py"

REM ---------- Wait 3 seconds for backend ----------
timeout /t 3 /nobreak >nul

REM ---------- Frontend Dev Server ----------
echo [2/2] Starting Vue 3 dev server (port 5173)...
cd /d %~dp0frontend
start "Vue3-Frontend" cmd /k "chcp 936 >nul & npm.cmd run dev"

REM ---------- Show Access URLs ----------
timeout /t 2 /nobreak >nul
echo.
echo ========================================
echo   Startup Complete!
echo   Frontend:    http://localhost:5173
echo   Backend API: http://localhost:9090
echo   Swagger:     http://localhost:9090/docs
echo ========================================
echo.
echo Press any key to close THIS window only.
echo The backend and frontend windows will keep running.
echo.
pause >nul
