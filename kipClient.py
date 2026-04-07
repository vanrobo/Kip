import sys, os, json, hashlib, time, asyncio, threading, socket, requests, subprocess
from cryptography.fernet import Fernet
import websockets
from zeroconf import Zeroconf, ServiceBrowser

CONFIG_FILE = "kip_client_config.json"

# --- FAILSAFE LINUX CLIPBOARD FUNCTIONS ---
def get_clipboard():
    try:
        # Try Wayland first (RPi 5 default)
        return subprocess.check_output(['wl-paste', '-n'], text=True, stderr=subprocess.DEVNULL)
    except:
        try:
            # Fallback to X11
            return subprocess.check_output(['xclip', '-selection', 'clipboard', '-o'], text=True, stderr=subprocess.DEVNULL)
        except:
            return ""

def set_clipboard(text):
    try:
        # Try Wayland
        process = subprocess.Popen(['wl-copy'], stdin=subprocess.PIPE, text=True)
        process.communicate(input=text)
    except:
        try:
            # Fallback to X11
            process = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE, text=True)
            process.communicate(input=text)
        except:
            print("[!] Failed to set clipboard. Is wl-clipboard installed?")

class KipClient:
    def __init__(self):
        self.config = self.load_config()
        self.server_ip = None
        self.last_local_hash = ""
        self.ws_conn = None
        self.loop = None
        self.cipher = None

        # Start background discovery
        threading.Thread(target=self.discovery_worker, daemon=True).start()

        # START THE CLIPBOARD MONITOR (Pure Python Loop)
        threading.Thread(target=self.monitor_loop, daemon=True).start()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f: return json.load(f)
        return None

    def discovery_worker(self):
        print("[?] Searching for Kip Hub...")
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
            pin = input("Enter the 6-digit PIN from the Hub: ")
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
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self.ws_handler(), self.loop)

    async def ws_handler(self):
        uri = f"ws://{self.server_ip}:8000/ws/{self.config['api_key']}"
        while True:
            try:
                async with websockets.connect(uri) as ws:
                    self.ws_conn = ws
                    print("[✔] Connected to Hub! Monitoring RPi Clipboard...")
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        raw_data = self.cipher.decrypt(data['data'].encode()).decode()
                        print(f"\n[↓] Received from Hub: '{raw_data[:30]}...'")
                        self.last_local_hash = hashlib.sha256(raw_data.encode()).hexdigest()
                        set_clipboard(raw_data)
            except Exception as e:
                self.ws_conn = None
                print(f"[!] Connection lost. Retrying in 5s...")
                await asyncio.sleep(5)

    def monitor_loop(self):
        while True:
            if self.ws_conn:
                text = get_clipboard()
                if text:
                    h = hashlib.sha256(text.encode()).hexdigest()
                    if h != self.last_local_hash:
                        self.last_local_hash = h
                        print(f"\n[↑] RPi Copy Detected: '{text[:30]}...'")
                        
                        encrypted = self.cipher.encrypt(text.encode()).decode()
                        payload = json.dumps({"type": "text", "data": encrypted, "ts": time.time(), "hash": h})
                        asyncio.run_coroutine_threadsafe(self.ws_conn.send(payload), self.loop)
            time.sleep(1)

if __name__ == "__main__":
    client = KipClient()
    # Keep the main thread alive
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting...")