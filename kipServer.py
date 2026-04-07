import os, json, secrets, socket, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from cryptography.fernet import Fernet
from zeroconf import ServiceInfo, Zeroconf
import uvicorn

CONFIG_FILE = "kip_config.json"

def load_or_create_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    
    # Generate new credentials if they don't exist
    config = {
        "api_key": secrets.token_urlsafe(16),
        "enc_key": Fernet.generate_key().decode(),
        "pairing_pin": str(secrets.randbelow(899999) + 100000) # 6-digit PIN
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)
    return config

conf = load_or_create_config()
app = FastAPI()
current_clipboard = {"type": "text", "data": "", "ts": 0, "hash": ""}
clients = []

@app.get("/pair/{pin}")
async def pair_device(pin: str):
    if pin == conf["pairing_pin"]:
        return {"api_key": conf["api_key"], "enc_key": conf["enc_key"]}
    return {"error": "Invalid PIN"}, 401

@app.websocket("/ws/{api_key}")
async def websocket_endpoint(websocket: WebSocket, api_key: str):
    if api_key != conf["api_key"]:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    clients.append(websocket)
    try:
        await websocket.send_json({"type": "sync", "payload": current_clipboard})
        while True:
            data = await websocket.receive_json()
            if data['ts'] > current_clipboard['ts']:
                current_clipboard.update(data)
                for client in clients:
                    if client != websocket: await client.send_json(data)
    except WebSocketDisconnect:
        clients.remove(websocket)

if __name__ == "__main__":
    print(f"\n🚀 KIP HUB IS RUNNING")
    print(f"🔑 PAIRING PIN: {conf['pairing_pin']}")
    print(f"-----------------------------------\n")
    
    # Get LAN IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 1))
    ip = s.getsockname()[0]
    s.close()

    zc = Zeroconf()
    info = ServiceInfo("_kip._tcp.local.", "KipHub._kip._tcp.local.",
                       addresses=[socket.inet_aton(ip)], port=8000)
    zc.register_service(info)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")