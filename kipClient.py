import sys, os, json, hashlib, time, asyncio, threading, requests
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QInputDialog, QMessageBox
from PySide6.QtGui import QIcon
from cryptography.fernet import Fernet
import websockets
import socket
from zeroconf import Zeroconf, ServiceBrowser

CONFIG_FILE = "kip_client_config.json"

class KipClient(QSystemTrayIcon):
    def __init__(self):
        super().__init__(QIcon.fromTheme("edit-copy"))
        self.config = self.load_config()
        self.server_ip = None
        self.last_hash = ""
        self.paused = False
        
        menu = QMenu()
        menu.addAction("Pause", self.toggle_pause)
        menu.addAction("Exit", QApplication.quit)
        self.setContextMenu(menu)

        threading.Thread(target=self.discover_and_run, daemon=True).start()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        return None

    def discover_and_run(self):
        # 1. Find Hub
        print("Searching for Kip Hub...")
        zc = Zeroconf()
        class Listener:
            def add_service(self, z, t, name):
                info = z.get_service_info(t, name)
                outer.server_ip = socket.inet_ntoa(info.addresses[0])
        outer = self
        browser = ServiceBrowser(zc, "_kip._tcp.local.", Listener())
        
        while not self.server_ip: time.sleep(1)
        
        # 2. Check Pairing
        if not self.config:
            self.request_pairing()
            
        self.cipher = Fernet(self.config["enc_key"].encode())
        asyncio.run(self.ws_loop())

    def request_pairing(self):
        # This runs in a thread, so we need to trigger the GUI carefully
        pin, ok = QInputDialog.getText(None, "Kip Pairing", f"Hub found at {self.server_ip}\nEnter Pairing PIN:")
        if ok and pin:
            resp = requests.get(f"http://{self.server_ip}:8000/pair/{pin}").json()
            if "api_key" in resp:
                self.config = resp
                with open(CONFIG_FILE, "w") as f: json.dump(self.config, f)
            else:
                QMessageBox.critical(None, "Error", "Invalid PIN")
                sys.exit()

    def toggle_pause(self): self.paused = not self.paused

    async def ws_loop(self):
        url = f"ws://{self.server_ip}:8000/ws/{self.config['api_key']}"
        while True:
            try:
                async with websockets.connect(url) as ws:
                    print("Connected and Syncing!")
                    # (Insert the same watch_local/watch_remote logic from previous code here)
                    # ... simplified for brevity ...
            except:
                await asyncio.sleep(5) # Auto-reconnect

if __name__ == "__main__":
    app = QApplication(sys.argv)
    client = KipClient()
    client.show()
    sys.exit(app.exec())