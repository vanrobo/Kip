import hmac, hashlib, time, socket
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from zeroconf import ServiceInfo, Zeroconf
import uvicorn

app = FastAPI()
API_KEY = "your-very-secure-key" # Change this!
current_clipboard = {"type": "text", "data": "", "ts": 0, "hash": ""}

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict, sender: WebSocket):
        for connection in self.active_connections:
            if connection != sender:
                await connection.send_json(message)

manager = ConnectionManager()

@app.websocket("/ws/{api_key}")
async def websocket_endpoint(websocket: WebSocket, api_key: str):
    if api_key != API_KEY:
        await websocket.close(code=1008)
        return
    
    await manager.connect(websocket)
    try:
        # Send current state upon connection
        await websocket.send_json({"type": "sync", "payload": current_clipboard})
        while True:
            data = await websocket.receive_json()
            # Race condition check: only update if timestamp is newer
            if data['ts'] > current_clipboard['ts']:
                current_clipboard.update(data)
                await manager.broadcast(data, websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1)) # Connect to external to find local source IP
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

if __name__ == "__main__":
    ip = get_lan_ip()
    zc = Zeroconf()
    info = ServiceInfo(
        "_clipboard_sync._tcp.local.",
        "SecureClipboardHub._clipboard_sync._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=8000,
        properties={"ver": "1.0"}
    )
    zc.register_service(info)
    print(f"Server started on {ip}:8000")
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    finally:
        zc.unregister_all_services()