@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 邮件助手 一键安装

rem Prefer the py launcher: "python" may be the Microsoft Store placeholder.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if defined PY goto run
where python >nul 2>nul && set "PY=python"
if defined PY goto run
goto nopython

:run
%PY% -m mailer.setup_env
if errorlevel 9009 goto nopython
if errorlevel 1 goto failed
echo.
set /p START=现在启动邮件助手吗？[Y/n] 
if /i "%START%"=="n" goto end
start "" "%~dp0邮件助手.pyw"
goto end

:nopython
echo.
echo 没有找到 Python。
echo 请先从 https://www.python.org/downloads/windows/ 下载安装 Python 3.11 或以上版本，
echo 安装时务必勾选 "Add python.exe to PATH"，装好后再双击本文件。
echo.
pause
goto end

:failed
echo.
echo 安装没有完成，请把上面的提示截图发给维护人。
pause

:end
