import sys, time, hashlib, json, base64, asyncio, threading
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QClipboard, QImage, QPixmap
from PySide6.QtCore import QBuffer, QIODevice
from cryptography.fernet import Fernet
import websockets
import socket
from zeroconf import Zeroconf, ServiceBrowser

# Configuration
API_KEY = "your-very-secure-key"
# Generate this once using Fernet.generate_key() and share across devices
ENCRYPTION_KEY = b'your-32-byte-base64-key-here=' 
cipher = Fernet(ENCRYPTION_KEY)

class ClipboardClient(QSystemTrayIcon):
    def __init__(self):
        super().__init__(QIcon.fromTheme("edit-copy"))
        self.setToolTip("Secure Clipboard Sync")
        self.paused = False
        self.last_hash = ""
        self.server_url = None
        
        # UI
        menu = QMenu()
        self.pause_action = menu.addAction("Pause Sync", self.toggle_pause)
        menu.addSeparator()
        menu.addAction("Exit", self.quit_app)
        self.setContextMenu(menu)
        
        # Start Discovery and WS thread
        threading.Thread(target=self.discovery_worker, daemon=True).start()
        threading.Thread(target=self.asyncio_bridge, daemon=True).start()

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_action.setText("Resume Sync" if self.paused else "Pause Sync")

    def encrypt_data(self, data_str):
        return cipher.encrypt(data_str.encode()).decode()

    def decrypt_data(self, encrypted_str):
        return cipher.decrypt(encrypted_str.encode()).decode()

    def get_clipboard_content(self):
        cb = QApplication.clipboard()
        mime = cb.mimeData()
        
        if mime.hasImage():
            image = cb.image()
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            raw_data = base64.b64encode(buffer.data().data()).decode()
            return "image", raw_data
        elif mime.hasHtml():
            return "html", mime.html()
        else:
            return "text", cb.text()

    async def ws_handler(self):
        backoff = 1
        while True:
            if not self.server_url:
                await asyncio.sleep(2)
                continue
                
            try:
                async with websockets.connect(f"{self.server_url}/{API_KEY}") as ws:
                    backoff = 1
                    print("Connected to Hub")
                    
                    async def watch_local():
                        while True:
                            if not self.paused:
                                c_type, content = self.get_clipboard_content()
                                c_hash = hashlib.sha256(content.encode()).hexdigest()
                                
                                if c_hash != self.last_hash:
                                    self.last_hash = c_hash
                                    payload = {
                                        "type": c_type,
                                        "data": self.encrypt_data(content),
                                        "ts": time.time(),
                                        "hash": c_hash
                                    }
                                    await ws.send(json.dumps(payload))
                            await asyncio.sleep(1.5)

                    async def watch_remote():
                        while True:
                            msg = json.loads(await ws.recv())
                            if not self.paused and msg['hash'] != self.last_hash:
                                self.last_hash = msg['hash']
                                decrypted = self.decrypt_data(msg['data'])
                                self.update_local_clipboard(msg['type'], decrypted)

                    await asyncio.gather(watch_local(), watch_remote())
            except Exception as e:
                print(f"WS Error: {e}. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def update_local_clipboard(self, c_type, data):
        cb = QApplication.clipboard()
        if c_type == "image":
            img_data = base64.b64decode(data)
            qimg = QImage.fromData(img_data)
            cb.setImage(qimg)
        elif c_type == "html":
            from PySide6.QtCore import QMimeData
            mime = QMimeData()
            mime.setHtml(data)
            cb.setMimeData(mime)
        else:
            cb.setText(data)

    def discovery_worker(self):
        class Listener:
            def add_service(self, z, type_, name):
                if "SecureClipboardHub" in name:
                    info = z.get_service_info(type_, name)
                    addr = socket.inet_ntoa(info.addresses[0])
                    outer.server_url = f"ws://{addr}:8000/ws"
        
        outer = self
        zc = Zeroconf()
        browser = ServiceBrowser(zc, "_clipboard_sync._tcp.local.", Listener())
        while True: time.sleep(1)

    def asyncio_bridge(self):
        asyncio.run(self.ws_handler())

    def quit_app(self):
        QApplication.quit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    client = ClipboardClient()
    client.show()
    sys.exit(app.exec())