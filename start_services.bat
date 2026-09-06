@echo off
chcp 65001 >nul
echo 正在启动音乐 HTTP / YouTube 流媒体服务 (端口 8111)...
start "Xiaozhi Music & YouTube Stream Server" python yt_stream_server.py 8111

echo 正在启动小智 MCP 桥接服务...
start "Xiaozhi MCP Bridge" python mcp_pipe.py python xzmcp.py

echo.
echo ==============================================
echo [OK] 服务启动完成！
echo 1. 音乐与流媒体服务: http://192.168.50.220:8111/
echo 2. 小智 MCP 桥接服务已连上云端
echo ==============================================
pause
