@echo off
chcp 65001 >nul
title 教学预警系统

echo ========================================
echo  教学预警系统 — 一键启动
echo ========================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 关闭可能占用 5000 端口的旧进程（防止端口冲突）
echo [1/4] 检查端口占用...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo 发现旧进程 PID=%%a，正在关闭...
    taskkill /f /pid %%a >nul 2>&1
)
echo 端口检查完成

REM 选择是否安装依赖（首次部署/换机器时需要，个人测试可跳过）
echo.
echo 是否安装依赖？
echo   [Y] 安装/更新依赖（首次运行或给老师部署时选这个）
echo   [N] 跳过依赖（依赖已装好时选这个，如个人测试，直接回车=跳过）
set /p INSTALL_DEPS=请输入选择 (Y/N)：
if /i "%INSTALL_DEPS%"=="Y" (
    echo [2/4] 安装依赖（使用国内镜像源）...
    pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30 --retries 3
    if %errorlevel% neq 0 (
        echo [警告] 部分依赖安装失败，尝试继续...
    )
) else (
    echo [2/4] 已跳过依赖安装
)

REM 生成知识点库
echo [3/4] 生成知识点库...
python main.py build-knowledge
if %errorlevel% neq 0 (
    echo [警告] 知识点库生成失败
)

REM 启动 Web 服务
echo [4/4] 启动 Web 服务...
echo.
echo 浏览器自动打开中...
start http://localhost:5000
echo.
python web/app.py

pause
