import sys
import os
import json
import hashlib
import time
import asyncio
import threading
import socket
import requests
from cryptography.fernet import Fernet
import websockets
from zeroconf import Zeroconf, ServiceBrowser

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QInputDialog, QMessageBox, QStyle
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QTimer, Signal, QObject, Qt

CONFIG_FILE = "kip_client_config.json"

class SyncSignals(QObject):
    remote_update = Signal(str, str)

class KipClient(QSystemTrayIcon):
    def __init__(self):
        # FIX: Correct way to access standard icons in PySide6
        standard_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        super().__init__(standard_icon)
        
        self.setToolTip("Kip Syncing...")
        self.signals = SyncSignals()
        self.signals.remote_update.connect(self.apply_remote_update)
        
        self.config = self.load_config()
        self.server_ip = None
        self.last_local_hash = ""
        self.paused = False
        self.ws_loop = None # To store the background event loop
        self.ws_conn = None # To store the active websocket
        self.cipher = None

        # Setup Tray Menu
        self.menu = QMenu()
        self.pause_action = QAction("Pause Sync", self)
        self.pause_action.setCheckable(True)
        self.pause_action.triggered.connect(self.toggle_pause)
        self.menu.addAction(self.pause_action)
        self.menu.addSeparator()
        self.menu.addAction("Exit", self.quit_app)
        self.setContextMenu(self.menu)

        # Timer to check local clipboard (runs on Main Thread)
        self.clipboard_timer = QTimer()
        self.clipboard_timer.timeout.connect(self.check_local_clipboard)
        self.clipboard_timer.start(1000) 

        # Start background discovery
        threading.Thread(target=self.discovery_worker, daemon=True).start()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        return None

    def toggle_pause(self):
        self.paused = self.pause_action.isChecked()
        print(f"Sync paused: {self.paused}")

    def quit_app(self):
        QApplication.quit()

    # --- DISCOVERY & PAIRING ---
    def discovery_worker(self):
        print("Searching for Kip Hub...")
        zc = Zeroconf()
        
        class KipListener:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info:
                    outer.server_ip = socket.inet_ntoa(info.addresses[0])
                    print(f"Found Hub at {outer.server_ip}")
            def update_service(self, zc, type_, name): pass
            def remove_service(self, zc, type_, name): pass

        outer = self
        browser = ServiceBrowser(zc, "_kip._tcp.local.", KipListener())
        
        while not self.server_ip:
            time.sleep(1)
        
        if not self.config:
            QTimer.singleShot(0, self.request_pairing_gui)
        else:
            self.start_sync_engine()

    def request_pairing_gui(self):
        pin, ok = QInputDialog.getText(None, "Kip Pairing", 
                                       f"Hub found at {self.server_ip}\nEnter Pairing PIN:")
        if ok and pin:
            try:
                r = requests.get(f"http://{self.server_ip}:8000/pair/{pin}", timeout=5)
                data = r.json()
                if "api_key" in data:
                    self.config = data
                    with open(CONFIG_FILE, "w") as f:
                        json.dump(self.config, f)
                    self.start_sync_engine()
                else:
                    QMessageBox.critical(None, "Error", "Invalid PIN")
            except Exception as e:
                print(f"Pairing failed: {e}")

    # --- SYNC ENGINE ---
    def start_sync_engine(self):
        self.cipher = Fernet(self.config["enc_key"].encode())
        threading.Thread(target=self.run_async_loop, daemon=True).start()

    def run_async_loop(self):
        self.ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.ws_loop)
        self.ws_loop.run_until_complete(self.ws_handler())

    async def ws_handler(self):
        uri = f"ws://{self.server_ip}:8000/ws/{self.config['api_key']}"
        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    self.ws_conn = websocket
                    print("Connected to Hub!")
                    while True:
                        msg = await websocket.recv()
                        data = json.loads(msg)
                        if not self.paused:
                            try:
                                decrypted = self.cipher.decrypt(data['data'].encode()).decode()
                                # Signal the main thread to update clipboard
                                self.signals.remote_update.emit(data['type'], decrypted)
                            except:
                                print("Decryption failed (Key mismatch?)")
            except Exception as e:
                self.ws_conn = None
                await asyncio.sleep(5)

    # --- CLIPBOARD OPERATIONS ---
    def check_local_clipboard(self):
        if self.paused or not self.ws_conn:
            return

        cb = QApplication.clipboard()
        text = cb.text()
        if not text or len(text) > 1000000: # Ignore empty or massive (1MB+) payloads
            return

        h = hashlib.sha256(text.encode()).hexdigest()
        if h != self.last_local_hash:
            self.last_local_hash = h
            print("Uploading new clipboard...")
            
            encrypted = self.cipher.encrypt(text.encode()).decode()
            payload = json.dumps({
                "type": "text",
                "data": encrypted,
                "ts": time.time(),
                "hash": h
            })
            # Send through the background loop
            asyncio.run_coroutine_threadsafe(self.ws_conn.send(payload), self.ws_loop)

    def apply_remote_update(self, c_type, data):
        # Update our hash first so we don't re-upload what we just downloaded
        self.last_local_hash = hashlib.sha256(data.encode()).hexdigest()
        cb = QApplication.clipboard()
        cb.setText(data)
        print("Clipboard synced from remote.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    # Check if we are already running
    client = KipClient()
    client.show()
    
    sys.exit(app.exec())