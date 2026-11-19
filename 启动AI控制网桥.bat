@echo off
chcp 65001 >nul
title 26Flow AI Agent 控制网桥服务 (端口 26262)
echo ========================================================
echo        26Flow / MuseFlow AI Agent 本地控制网桥
echo ========================================================
echo 正在启动本地 WebSocket + HTTP REST 服务...
echo 接口地址: http://127.0.0.1:26262/api/...
echo WebSocket: ws://127.0.0.1:26263/ws
echo 保持此窗口开启，AI 即可直接免点击控制浏览器画板！
echo ========================================================
cd /d "%~dp0"
python agent_bridge.py serve
pause
