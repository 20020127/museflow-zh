"""
26Flow / MuseFlow AI Agent Control Bridge
-----------------------------------------
Provides a WebSocket & HTTP REST bridge allowing AI agents and local scripts
to programmatically control 26Flow (load workflows, add/connect nodes, run, export)
without simulating mouse clicks or keyboard inputs.

Usage:
  python agent_bridge.py serve                 # Start the bridge server
  python agent_bridge.py status                # Get live canvas status
  python agent_bridge.py load <workflow.json>  # Inject workflow JSON into canvas
  python agent_bridge.py run                   # Trigger workflow execution
  python agent_bridge.py script <file_or_text> # Build nodes from scene script
  python agent_bridge.py stop                  # Stop running generation
"""

import sys
import os
import json
import asyncio
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.request
import urllib.error

try:
    import websockets
except ImportError:
    websockets = None

HOST = "127.0.0.1"
PORT = 26262

# Global connected 26Flow web clients
connected_clients = set()
pending_requests = {}
req_counter = 0

async def ws_handler(websocket):
    global req_counter
    connected_clients.add(websocket)
    print(f"[AgentBridge] 26Flow 画布已连接 (在线客户端数: {len(connected_clients)})")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                req_id = data.get("id")
                if req_id and req_id in pending_requests:
                    future = pending_requests.pop(req_id)
                    if not future.done():
                        future.set_result(data)
                elif data.get("type") == "STATUS":
                    pass
            except Exception as e:
                print(f"[AgentBridge] 处理消息异常: {e}")
    except Exception as e:
        pass
    finally:
        connected_clients.discard(websocket)
        print(f"[AgentBridge] 26Flow 画布断开连接 (剩余在线: {len(connected_clients)})")

async def send_command(action, params=None, timeout=10.0):
    global req_counter
    if not connected_clients:
        return {"ok": False, "error": "没有在线的 26Flow 画布。请在浏览器中打开 26Flow 扩展页面。"}
    
    req_counter += 1
    req_id = f"cmd_{req_counter}"
    payload = json.dumps({
        "id": req_id,
        "action": action,
        "params": params or {}
    })
    
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[req_id] = future
    
    # Broadcast to all connected 26Flow tabs
    for client in list(connected_clients):
        try:
            await client.send(payload)
        except Exception:
            connected_clients.discard(client)
            
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        pending_requests.pop(req_id, None)
        return {"ok": False, "error": f"指令 '{action}' 执行超时 (10秒)"}

class BridgeHTTPHandler(BaseHTTPRequestHandler):
    loop = None

    def log_message(self, format, *args):
        # Quiet standard logging
        pass

    def _send_json(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        if self.path == "/status" or self.path == "/api/status":
            future = asyncio.run_coroutine_threadsafe(send_command("get_status"), self.loop)
            try:
                res = future.result(timeout=5.0)
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
        elif self.path == "/workflow" or self.path == "/api/workflow":
            future = asyncio.run_coroutine_threadsafe(send_command("get_workflow"), self.loop)
            try:
                res = future.result(timeout=5.0)
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
        else:
            self._send_json(404, {"ok": False, "error": f"Not found: {self.path}"})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        params = {}
        if body:
            try:
                params = json.loads(body.decode("utf-8"))
            except Exception:
                pass

        action_map = {
            "/api/workflow/load": ("load_workflow", params),
            "/api/workflow/clear": ("clear_workflow", {}),
            "/api/workflow/run": ("run_workflow", params),
            "/api/script/build": ("build_from_script", params),
            "/api/node/add": ("add_node", params),
            "/api/node/remove": ("remove_node", params),
            "/api/node/connect": ("connect", params),
            "/api/node/run": ("run_node", params),
            "/api/stop": ("stop", {}),
            "/api/export": ("export_timeline", params)
        }

        path = self.path
        if path in action_map:
            action, p = action_map[path]
            future = asyncio.run_coroutine_threadsafe(send_command(action, p), self.loop)
            try:
                res = future.result(timeout=15.0)
                self._send_json(200, res)
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
        else:
            self._send_json(404, {"ok": False, "error": f"Unknown action endpoint: {path}"})

def run_http_server(loop):
    BridgeHTTPHandler.loop = loop
    server = HTTPServer((HOST, PORT), BridgeHTTPHandler)
    print(f"[AgentBridge] HTTP API 已就绪: http://{HOST}:{PORT}/api/...")
    server.serve_forever()

async def start_server():
    if not websockets:
        print("[AgentBridge] 错误: 未安装 websockets 库。请运行 `pip install websockets`。")
        sys.exit(1)

    loop = asyncio.get_running_loop()
    # Start HTTP server in a daemon thread sharing the event loop
    http_thread = threading.Thread(target=run_http_server, args=(loop,), daemon=True)
    http_thread.start()

    async with websockets.serve(ws_handler, HOST, PORT):
        print(f"[AgentBridge] WebSocket 接口已就绪: ws://{HOST}:{PORT}/ws")
        print("[AgentBridge] 等待 26Flow 扩展页面连接...")
        await asyncio.Future()  # run forever

# Client helper for CLI invocation
def client_request(method, endpoint, payload=None):
    url = f"http://{HOST}:{PORT}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"无法连接到 AgentBridge (请确认是否运行了 python agent_bridge.py serve): {e}"}

def main():
    parser = argparse.ArgumentParser(description="26Flow AI Agent 控制网桥")
    subparsers = parser.add_subparsers(dest="command")

    # serve command
    subparsers.add_parser("serve", help="启动网桥服务 (WebSocket + HTTP)")

    # status command
    subparsers.add_parser("status", help="获取画板当前状态")

    # run command
    subparsers.add_parser("run", help="触发运行当前工作流")

    # stop command
    subparsers.add_parser("stop", help="停止当前生成")

    # load command
    load_parser = subparsers.add_parser("load", help="加载工作流 JSON 文件")
    load_parser.add_argument("file", help="工作流 JSON 文件路径")

    # script command
    script_parser = subparsers.add_parser("script", help="从分镜脚本自动生成画板节点")
    script_parser.add_argument("script_input", help="分镜脚本文件路径或纯文本")

    args = parser.parse_args()

    if args.command == "serve" or args.command is None:
        try:
            asyncio.run(start_server())
        except KeyboardInterrupt:
            print("\n[AgentBridge] 服务已关闭。")
    elif args.command == "status":
        res = client_request("GET", "/api/status")
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "run":
        res = client_request("POST", "/api/workflow/run", {})
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "stop":
        res = client_request("POST", "/api/stop", {})
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "load":
        if not os.path.exists(args.file):
            print(f"错误: 找不到文件 {args.file}")
            sys.exit(1)
        with open(args.file, "r", encoding="utf-8") as f:
            workflow_data = json.load(f)
            if workflow_data.get("format") == "26flow-workflow" and "workflow" in workflow_data:
                workflow_data = workflow_data["workflow"]
        res = client_request("POST", "/api/workflow/load", {"workflow": workflow_data})
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "script":
        if os.path.exists(args.script_input):
            with open(args.script_input, "r", encoding="utf-8") as f:
                script_text = f.read()
        else:
            script_text = args.script_input
        res = client_request("POST", "/api/script/build", {"script": script_text})
        print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
