@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >/dev/null 2>/dev/null && ( py -3 "打包便携版.py" & goto done )
where python >/dev/null 2>/dev/null && ( python "打包便携版.py" & goto done )
echo.
echo 没有找到 Python。打包这台电脑需要先装 Python（从 https://www.python.org/downloads/windows/ ），
echo 安装时勾选 "Add python.exe to PATH"。同事用的便携版不需要装，这里是给你打包用的。
:done
echo.
pause
