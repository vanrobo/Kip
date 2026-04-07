import sys, os, json, hashlib, time, asyncio, threading, socket, requests
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
        standard_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        super().__init__(standard_icon)
        
        self.signals = SyncSignals()
        self.signals.remote_update.connect(self.apply_remote_update)
        
        self.config = self.load_config()
        self.server_ip = None
        self.last_local_hash = ""
        self.paused = False
        self.ws_loop = None
        self.ws_conn = None
        self.cipher = None

        self.menu = QMenu()
        self.menu.addAction("Exit", QApplication.quit)
        self.setContextMenu(self.menu)

        self.clipboard_timer = QTimer()
        self.clipboard_timer.timeout.connect(self.check_local_clipboard)
        self.clipboard_timer.start(1000) 

        threading.Thread(target=self.discovery_worker, daemon=True).start()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        return None

    def discovery_worker(self):
        print("\n[!] Searching for Kip Hub...")
        zc = Zeroconf()
        class KipListener:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info:
                    outer.server_ip = socket.inet_ntoa(info.addresses[0])
                    print(f"[+] Found Hub at {outer.server_ip}")
            def update_service(self, zc, type_, name): pass
            def remove_service(self, zc, type_, name): pass

        outer = self
        browser = ServiceBrowser(zc, "_kip._tcp.local.", KipListener())
        
        while not self.server_ip:
            time.sleep(1)
        
        if not self.config:
            # TERMINAL FALLBACK for no-mouse users
            print(f"\n{'='*40}")
            print(f"PAIRS REQUIRED: Enter the 6-digit PIN from the Hub")
            print(f"{'='*40}")
            # We use QTimer to trigger the GUI, but we also listen to the terminal
            QTimer.singleShot(0, self.request_pairing_gui)
            
            # Use a simple input loop in this background thread
            pin = input("ENTER PIN HERE > ")
            self.submit_pairing(pin)
        else:
            self.start_sync_engine()

    def request_pairing_gui(self):
        # This creates a popup that should appear in Alt+Tab
        dialog = QInputDialog()
        dialog.setWindowTitle("Kip Pairing")
        dialog.setLabelText(f"Hub found at {self.server_ip}\nEnter Pairing PIN:")
        dialog.setWindowFlags(Qt.WindowStaysOnTopHint)
        if dialog.exec():
            self.submit_pairing(dialog.textValue())

    def submit_pairing(self, pin):
        if self.config: return # Already paired
        try:
            print(f"[*] Attempting to pair with PIN: {pin}...")
            r = requests.get(f"http://{self.server_ip}:8000/pair/{pin}", timeout=5)
            data = r.json()
            if "api_key" in data:
                self.config = data
                with open(CONFIG_FILE, "w") as f:
                    json.dump(self.config, f)
                print("[✔] Pairing Successful! Syncing started.")
                self.start_sync_engine()
            else:
                print("[✘] Error: Invalid PIN.")
        except Exception as e:
            print(f"[✘] Connection failed: {e}")

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
                    print("[✔] Connected to Hub WebSocket!")
                    while True:
                        msg = await websocket.recv()
                        data = json.loads(msg)
                        if not self.paused:
                            decrypted = self.cipher.decrypt(data['data'].encode()).decode()
                            self.signals.remote_update.emit(data['type'], decrypted)
            except Exception:
                self.ws_conn = None
                await asyncio.sleep(5)

    def check_local_clipboard(self):
        if not self.ws_conn or self.paused: return
        cb = QApplication.clipboard()
        text = cb.text()
        if not text: return
        h = hashlib.sha256(text.encode()).hexdigest()
        if h != self.last_local_hash:
            self.last_local_hash = h
            print(f"-> Uploading: {text[:20]}...")
            encrypted = self.cipher.encrypt(text.encode()).decode()
            payload = json.dumps({"type": "text", "data": encrypted, "ts": time.time(), "hash": h})
            asyncio.run_coroutine_threadsafe(self.ws_conn.send(payload), self.ws_loop)

    def apply_remote_update(self, c_type, data):
        self.last_local_hash = hashlib.sha256(data.encode()).hexdigest()
        QApplication.clipboard().setText(data)
        print(f"<- Received: {data[:20]}...")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    client = KipClient()
    client.show()
    sys.exit(app.exec())