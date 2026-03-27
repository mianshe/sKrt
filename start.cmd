@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
  echo.
  echo [start.cmd] 启动失败，错误见上方。按任意键关闭...
  pause >nul
  exit /b 1
)
