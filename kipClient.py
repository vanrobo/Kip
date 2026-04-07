import sys, os, json, hashlib, time, asyncio, threading, socket, requests
from cryptography.fernet import Fernet
import websockets
from zeroconf import Zeroconf, ServiceBrowser

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QInputDialog, QStyle
from PySide6.QtCore import QTimer, Signal, QObject, Qt

CONFIG_FILE = "kip_client_config.json"

class SyncSignals(QObject):
    remote_update = Signal(str, str)

class KipClient(QSystemTrayIcon):
    def __init__(self):
        # Setup tray icon
        standard_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        super().__init__(standard_icon)
        
        self.signals = SyncSignals()
        self.signals.remote_update.connect(self.apply_remote_update)
        
        self.config = self.load_config()
        self.server_ip = None
        self.last_local_hash = ""
        self.ws_conn = None
        self.loop = None # Reference to the background loop
        self.cipher = None

        # Start background discovery
        threading.Thread(target=self.discovery_worker, daemon=True).start()

        # START THE CLIPBOARD MONITOR
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_local_clipboard)
        self.timer.start(1000) # Every 1 second
        print("[!] Local Clipboard Monitor started (1s interval)")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f: return json.load(f)
        return None

    def discovery_worker(self):
        print("[?] Searching for Hub...")
        zc = Zeroconf()
        class Listener:
            def add_service(self, z, t, name):
                info = z.get_service_info(t, name)
                if info:
                    outer.server_ip = socket.inet_ntoa(info.addresses[0])
                    print(f"[+] Found Hub at {outer.server_ip}")
            def update_service(self, z, t, n): pass
            def remove_service(self, z, t, n): pass
        outer = self
        ServiceBrowser(zc, "_kip._tcp.local.", Listener())
        
        while not self.server_ip: time.sleep(0.5)
        
        if not self.config:
            print("\n!!! PAIRING REQUIRED !!!")
            pin = input("Enter the 6-digit PIN from the Hub window: ")
            self.submit_pairing(pin)
        else:
            self.start_sync_engine()

    def submit_pairing(self, pin):
        try:
            r = requests.get(f"http://{self.server_ip}:8000/pair/{pin}", timeout=5).json()
            if "api_key" in r:
                self.config = r
                with open(CONFIG_FILE, "w") as f: json.dump(self.config, f)
                print("[✔] Pairing Successful!")
                self.start_sync_engine()
            else: print("[✘] Invalid PIN")
        except Exception as e: print(f"[✘] Pair error: {e}")

    def start_sync_engine(self):
        self.cipher = Fernet(self.config["enc_key"].encode())
        # Start the async loop in a thread and keep a reference to it
        threading.Thread(target=self.run_async_setup, daemon=True).start()

    def run_async_setup(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.ws_handler())

    async def ws_handler(self):
        uri = f"ws://{self.server_ip}:8000/ws/{self.config['api_key']}"
        while True:
            try:
                async with websockets.connect(uri) as ws:
                    self.ws_conn = ws
                    print("[✔] WebSocket Connected! Kip is LIVE.")
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        raw_data = self.cipher.decrypt(data['data'].encode()).decode()
                        self.signals.remote_update.emit(data['type'], raw_data)
            except Exception as e:
                self.ws_conn = None
                print(f"[!] Connection lost: {e}. Retrying...")
                await asyncio.sleep(5)

    def check_local_clipboard(self):
        # DEBUG: Print a dot every time the timer fires so we know it's not frozen
        # print(".", end="", flush=True) 
        
        if not self.ws_conn: return
        
        cb = QApplication.clipboard()
        text = cb.text()
        
        if not text: return
        
        h = hashlib.sha256(text.encode()).hexdigest()
        if h != self.last_local_hash:
            self.last_local_hash = h
            print(f"\n[↑] Local Copy Detected: '{text[:30]}...'")
            
            # Encrypt
            encrypted = self.cipher.encrypt(text.encode()).decode()
            payload = json.dumps({"type": "text", "data": encrypted, "ts": time.time(), "hash": h})
            
            # Send to the background loop
            if self.loop:
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self.ws_conn.send(payload))
                )

    def apply_remote_update(self, c_type, data):
        print(f"\n[↓] Remote Update Received: '{data[:30]}...'")
        self.last_local_hash = hashlib.sha256(data.encode()).hexdigest()
        QApplication.clipboard().setText(data)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    client = KipClient()
    client.show()
    sys.exit(app.exec())