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

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QInputDialog, QMessageBox
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QTimer, Signal, QObject, Qt

CONFIG_FILE = "kip_client_config.json"

# This class handles communication between the background thread and the UI
class SyncSignals(QObject):
    remote_update = Signal(str, str) # type, data

class KipClient(QSystemTrayIcon):
    def __init__(self):
        # Use a standard system icon so it's visible on Windows
        super().__init__(QApplication.style().standardIcon(QApplication.style().SP_ComputerIcon))
        
        self.setToolTip("Kip Syncing...")
        self.signals = SyncSignals()
        self.signals.remote_update.connect(self.apply_remote_update)
        
        self.config = self.load_config()
        self.server_ip = None
        self.last_local_hash = ""
        self.paused = False
        self.ws = None
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
        self.clipboard_timer.start(1500) # Check every 1.5 seconds

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
        print("Searching for Kip Hub on network...")
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
        
        # Once IP found, check pairing
        if not self.config:
            # We must use a Timer to trigger the GUI dialog from the main thread
            QTimer.singleShot(0, self.request_pairing_gui)
        else:
            self.start_sync_engine()

    def request_pairing_gui(self):
        pin, ok = QInputDialog.getText(None, "Kip Pairing", 
                                       f"Hub found at {self.server_ip}\nEnter Pairing PIN shown on Hub:")
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
                    sys.exit()
            except Exception as e:
                QMessageBox.critical(None, "Error", f"Connection failed: {e}")
                sys.exit()

    # --- SYNC ENGINE ---
    def start_sync_engine(self):
        self.cipher = Fernet(self.config["enc_key"].encode())
        threading.Thread(target=lambda: asyncio.run(self.ws_handler()), daemon=True).start()

    async def ws_handler(self):
        uri = f"ws://{self.server_ip}:8000/ws/{self.config['api_key']}"
        backoff = 2
        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    self.ws = websocket
                    backoff = 2
                    print("Sync Engine Connected!")
                    while True:
                        msg = await websocket.recv()
                        data = json.loads(msg)
                        if not self.paused:
                            # Decrypt and send to main thread to update clipboard
                            decrypted = self.cipher.decrypt(data['data'].encode()).decode()
                            self.signals.remote_update.emit(data['type'], decrypted)
            except Exception as e:
                print(f"Connection lost ({e}). Retrying in {backoff}s...")
                self.ws = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # --- CLIPBOARD OPERATIONS (Main Thread Safe) ---
    def check_local_clipboard(self):
        if self.paused or not self.ws:
            return

        cb = QApplication.clipboard()
        text = cb.text()
        if not text: return

        # Simple hash to see if it changed
        current_hash = hashlib.sha256(text.encode()).hexdigest()
        if current_hash != self.last_local_hash:
            self.last_local_hash = current_hash
            print("Local change detected. Uploading...")
            
            # Encrypt and send
            encrypted = self.cipher.encrypt(text.encode()).decode()
            payload = {
                "type": "text",
                "data": encrypted,
                "ts": time.time(),
                "hash": current_hash
            }
            # Run the async send in the background loop
            asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(payload)), asyncio.get_event_loop())

    def apply_remote_update(self, c_type, data):
        # This runs on Main Thread via Signal
        self.last_local_hash = hashlib.sha256(data.encode()).hexdigest()
        cb = QApplication.clipboard()
        cb.setText(data)
        print("Clipboard updated from remote.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    client = KipClient()
    client.show()
    
    sys.exit(app.exec())