import sys
import os
import json
import secrets
import socket
import threading
import subprocess
import ctypes
from datetime import datetime

# Networking & Backend
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from zeroconf import ServiceInfo, Zeroconf
from cryptography.fernet import Fernet

# GUI
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QIcon

# --- CONFIGURATION & SECURITY ---
CONFIG_FILE = "kip_hub_config.json"

def load_or_create_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    
    config = {
        "api_key": secrets.token_urlsafe(32),
        "enc_key": Fernet.generate_key().decode(),
        "pairing_pin": str(secrets.randbelow(899999) + 100000)
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)
    return config

CONF = load_or_create_config()

# --- WINDOWS ADMIN & FIREWALL AUTOMATION ---
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    # Relaunch the script with admin privileges
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)

def setup_firewall():
    if os.name == 'nt': # Only if on Windows
        rule_name = "KipSync_Hub_Port8000"
        # Check if the rule already exists to avoid spamming
        check = subprocess.run(f'netsh advfirewall firewall show rule name="{rule_name}"', 
                               capture_output=True, shell=True, text=True)
        if "no rules match" in check.stdout.lower() or check.returncode != 0:
            subprocess.run(
                f'netsh advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport=8000', 
                shell=True, capture_output=True
            )

# --- BACKEND LOGIC (FASTAPI) ---
app = FastAPI()
current_clipboard = {"type": "text", "data": "", "ts": 0, "hash": ""}
active_clients = []

@app.get("/pair/{pin}")
async def pair_device(pin: str):
    if pin == CONF["pairing_pin"]:
        return {
            "api_key": CONF["api_key"],
            "enc_key": CONF["enc_key"]
        }
    return {"error": "Invalid PIN"}, 401

@app.websocket("/ws/{api_key}")
async def websocket_endpoint(websocket: WebSocket, api_key: str):
    if api_key != CONF["api_key"]:
        await websocket.close(code=1008)
        return
    
    await websocket.accept()
    active_clients.append(websocket)
    
    try:
        # Send the latest clipboard state immediately on connect
        if current_clipboard["data"]:
            await websocket.send_json({"type": "sync", "payload": current_clipboard})
        
        while True:
            data = await websocket.receive_json()
            # Timestamp check to prevent race conditions
            if data.get('ts', 0) > current_clipboard['ts']:
                current_clipboard.update(data)
                # Broadcast to everyone else
                for client in active_clients:
                    if client != websocket:
                        try:
                            await client.send_json(data)
                        except:
                            pass
    except WebSocketDisconnect:
        active_clients.remove(websocket)

# --- THE UI (PYSIDE6) ---
class KipHubUI(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Kip Hub")
        self.setFixedSize(350, 250)
        self.setWindowFlags(Qt.WindowStaysOnTopHint) # Keep it visible for pairing
        
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(30, 30, 30, 30)

        title = QLabel("Kip Hub Active")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2ecc71;")
        
        desc = QLabel("Enter this PIN on your other devices to link them:")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #7f8c8d; font-size: 13px;")

        self.pin_display = QLabel(CONF["pairing_pin"])
        self.pin_display.setAlignment(Qt.AlignCenter)
        self.pin_display.setStyleSheet("""
            font-size: 42px; 
            font-family: 'Courier New'; 
            font-weight: bold; 
            background-color: #f4f4f4; 
            border: 2px dashed #bdc3c7; 
            border-radius: 10px; 
            padding: 10px; 
            color: #2c3e50;
        """)

        status = QLabel("Searching for devices...")
        status.setAlignment(Qt.AlignCenter)
        status.setStyleSheet("font-size: 11px; color: #95a5a6;")

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(self.pin_display)
        layout.addStretch()
        layout.addWidget(status)
        self.setLayout(layout)

# --- NETWORK DISCOVERY (ZEROCONF) ---
def start_discovery():
    # Find local LAN IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except:
        ip = '127.0.0.1'
    finally:
        s.close()

    desc = {'version': '1.0'}
    info = ServiceInfo(
        "_kip._tcp.local.",
        "KipHub._kip._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=8000,
        properties=desc,
    )
    zc = Zeroconf()
    zc.register_service(info)
    return zc

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # 1. Handle Admin Rights on Windows
    if os.name == 'nt' and not is_admin():
        print("Requesting Admin rights to configure firewall...")
        run_as_admin()
        sys.exit()

    # 2. Setup Firewall (Only runs if Admin)
    setup_firewall()

    # 3. Start Zeroconf Discovery
    discovery = start_discovery()

    # 4. Start Backend Server in Thread
    def run_server():
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 5. Launch GUI
    qt_app = QApplication(sys.argv)
    window = KipHubUI()
    window.show()
    
    try:
        sys.exit(qt_app.exec())
    finally:
        discovery.unregister_all_services()