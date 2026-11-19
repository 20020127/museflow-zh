@echo off
chcp 65001 >nul
title 启动 26Flow 中文版 Chrome
set "CHROME_BIN=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_BIN%" set "CHROME_BIN=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

echo 正在启动 Chrome 并自动加载 26Flow 中文版扩展...
start "" "%CHROME_BIN%" --load-extension="%~dp0"
exit
