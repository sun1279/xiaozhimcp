import os
import sys
import asyncio
import websockets

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

async def bridge():
    endpoint = os.environ.get("MCP_ENDPOINT")
    if not endpoint:
        for filename in ["新建文本文档.txt", ".env"]:
            if os.path.exists(filename):
                try:
                    with open(filename, "r", encoding="utf-8") as f:
                        import re
                        m = re.search(r'wss://[^\s"\']+', f.read())
                        if m:
                            endpoint = m.group(0)
                            break
                except Exception:
                    pass

    if not endpoint:
        print("[ERROR] 未检测到环境变量 MCP_ENDPOINT！")
        print("请在运行前先执行: $env:MCP_ENDPOINT=\"wss://api.xiaozhi.me/mcp/?token=你的TOKEN\"")
        sys.exit(1)

    cmd = sys.argv[1:]
    if not cmd:
        print("[ERROR] 请指定要拉起的 MCP 脚本，例如: python mcp_pipe.py python .\\xzmcp.py")
        sys.exit(1)

    print(f"[mcp-pipe] 正在拉起本地 MCP 服务: {' '.join(cmd)}")
    
    # 拉起本地 MCP 子进程
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr
    )

    print(f"[mcp-pipe] 正在连接小智 WSS 接入点: {endpoint[:35]}...")
    try:
        async with websockets.connect(endpoint) as ws:
            print("[SUCCESS] [mcp-pipe] WSS 连接成功！MCP 工具已注册到小智云端。")

            # 云端 WSS 命令 -> 发送给本地 MCP 进程的 Stdin
            async def ws_to_stdin():
                try:
                    async for message in ws:
                        if process.stdin:
                            data = message.encode('utf-8') if isinstance(message, str) else message
                            process.stdin.write(data + b'\n')
                            await process.stdin.drain()
                except Exception as e:
                    print(f"[mcp-pipe] WS -> Stdin 异常: {e}")

            # 本地 MCP 进程输出 Stdout -> 发送给云端 WSS
            async def stdout_to_ws():
                try:
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        msg = line.decode('utf-8').strip()
                        if msg:
                            await ws.send(msg)
                except Exception as e:
                    print(f"[mcp-pipe] Stdout -> WS 异常: {e}")

            await asyncio.gather(ws_to_stdin(), stdout_to_ws())

    except Exception as e:
        print(f"[ERROR] [mcp-pipe] 连接小智 WSS 端点失败: {e}")
    finally:
        if process.returncode is None:
            process.terminate()

if __name__ == "__main__":
    if sys.platform == 'win32' and sys.version_info < (3, 16):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass
    asyncio.run(bridge())