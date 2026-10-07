@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python "%~dp0autostart.py" status
echo.
echo 1. 开启登录后自启
echo 2. 关闭登录后自启
echo 3. 只查看状态
echo 4. 退出
choice /c 1234 /n /m "请选择 [1-4]："
if errorlevel 4 exit /b 0
if errorlevel 3 goto status
if errorlevel 2 goto disable
goto enable
:enable
python "%~dp0autostart.py" enable
goto done
:disable
python "%~dp0autostart.py" disable
goto done
:status
python "%~dp0autostart.py" status
:done
pause
