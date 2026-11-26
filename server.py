"""
QPhoneConnect - lõi server.

Mở một HTTP server trong mạng LAN. Điện thoại quét QR -> mở trình duyệt là gửi/nhận
file và văn bản hai chiều với máy tính. Chỉ dùng thư viện chuẩn Python
(gói `qrcode` để vẽ mã QR).
"""
from __future__ import annotations

import faulthandler
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from http import cookies
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

try:
    import qrcode  # type: ignore
    import qrcode.image.svg  # type: ignore
except Exception:  # pragma: no cover
    qrcode = None

APP_NAME = "QPhoneConnect"
VERSION = "1.0.0"
DEFAULT_PORT = 8765
CHUNK = 1024 * 1024  # 1 MB
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_DIR = BASE_DIR / "static"
IS_WIN = sys.platform.startswith("win")

log = logging.getLogger(APP_NAME)


# --------------------------------------------------------------------------- #
# Đường dẫn, cấu hình, log
# --------------------------------------------------------------------------- #
def app_data_dir() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_folder() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / APP_NAME


_crash_file = None  # giữ handle mở để faulthandler còn ghi được


def _thread_excepthook(args) -> None:
    if args.exc_type is SystemExit:
        return
    log.error("Lỗi không bắt được ở luồng %s", getattr(args.thread, "name", "?"),
              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def setup_logging() -> Path:
    """Ghi log vào %APPDATA%\\QPhoneConnect\\qphoneconnect.log.

    Bản .exe (--windowed) không có console: sys.stderr là None nên mọi lỗi in ra màn hình
    đều biến mất. Vì vậy cả lỗi của pywebview/WebView2 và crash cứng cũng được dẫn vào file.
    """
    global _crash_file
    folder = app_data_dir()
    path = folder / "qphoneconnect.log"
    log.setLevel(logging.INFO)
    if not log.handlers:
        fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(fh)
        if sys.stderr is not None:  # bản .exe không có console
            log.addHandler(logging.StreamHandler(sys.stderr))

        # pywebview báo lỗi (vd. "WebView2 initialization failed") qua logger "pywebview" rồi
        # bỏ qua, không ném exception -> nếu không ghi file thì không ai biết vì sao cửa sổ không lên.
        # Phải gắn trước `import webview`: khi đã có handler, pywebview không tự thêm StreamHandler nữa.
        wl = logging.getLogger("pywebview")
        wl.setLevel(logging.DEBUG)  # có cả "Using WinForms / Chromium" - cho biết engine nào được chọn
        wl.addHandler(fh)

        # Lỗi ở luồng phụ và crash cứng (pythonnet/WebView2 làm chết tiến trình, không có traceback Python)
        threading.excepthook = _thread_excepthook
        try:
            _crash_file = open(folder / "crash.log", "a", encoding="utf-8")
            faulthandler.enable(_crash_file)
        except Exception:  # pragma: no cover
            pass
    return path


class Config:
    def __init__(self):
        self.path = app_data_dir() / "config.json"
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except Exception:
                self.data = {}
        changed = False
        if not self.data.get("token"):
            self.data["token"] = secrets.token_urlsafe(9)
            changed = True
        if not self.data.get("folder"):
            self.data["folder"] = str(default_folder())
            changed = True
        self.data.setdefault("port", DEFAULT_PORT)
        if changed:
            self.save()

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")


# --------------------------------------------------------------------------- #
# Tiện ích
# --------------------------------------------------------------------------- #
def get_lan_ip() -> str:
    """IP của card mạng đang ra ngoài (không gửi gói tin thật)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def get_all_ips() -> list[str]:
    primary = get_lan_ip()
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    others = sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")) and ip != primary)
    return [primary] + others


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:200] or "file"


def unique_path(folder: Path, name: str) -> Path:
    p = folder / name
    stem, suffix, i = p.stem, p.suffix, 1
    while p.exists() or p.with_name(p.name + ".part").exists():
        p = folder / f"{stem} ({i}){suffix}"
        i += 1
    return p


def device_name(ua: str) -> str:
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    m = re.search(r"Android[^;)]*;\s*([^;)]+?)(?:\s+Build[^;)]*)?\)", ua)
    if m and m.group(1).strip() not in ("K", "wv", "Mobile"):
        return m.group(1).strip()[:30]
    if "Android" in ua:
        return "Android"
    return "Thiết bị"


def open_path(path: Path) -> None:
    if IS_WIN:
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_path(path: Path) -> None:
    """Mở thư mục chứa file và chọn sẵn file đó."""
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def hide_on_windows(path: Path) -> None:
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # HIDDEN
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Kho dữ liệu + sự kiện realtime
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self, folder: Path):
        self.lock = threading.RLock()
        self.subscribers: list[queue.Queue] = []
        self.devices: dict[int, dict] = {}
        self.items: list[dict] = []
        self.set_folder(folder, notify=False)

    # --- thư mục ---
    def set_folder(self, folder: Path, notify: bool = True):
        folder = Path(folder).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        meta = folder / ".qphoneconnect"
        meta.mkdir(exist_ok=True)
        hide_on_windows(meta)
        with self.lock:
            self.folder = folder
            self.db = meta / "items.json"
            self.items = self._load()
        if notify:
            self.publish({"type": "reset"})

    def _load(self) -> list[dict]:
        if not self.db.exists():
            return []
        try:
            items = json.loads(self.db.read_text("utf-8"))
            return [i for i in items if i["kind"] == "text" or (self.folder / i["file"]).exists()]
        except Exception:
            log.exception("Không đọc được items.json")
            return []

    def _save(self):
        tmp = self.db.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.db)

    # --- thao tác ---
    def add(self, item: dict) -> dict:
        item.setdefault("id", uuid.uuid4().hex[:12])
        item.setdefault("ts", time.time())
        with self.lock:
            self.items.append(item)
            self._save()
        self.publish({"type": "add", "item": item})
        return item

    def get(self, item_id: str) -> dict | None:
        with self.lock:
            return next((i for i in self.items if i["id"] == item_id), None)

    def file_path(self, item: dict) -> Path | None:
        fp = (self.folder / item["file"]).resolve()
        if self.folder.resolve() not in fp.parents or not fp.exists():
            return None
        return fp

    def remove(self, item_id: str) -> bool:
        with self.lock:
            item = next((i for i in self.items if i["id"] == item_id), None)
            if not item:
                return False
            self.items.remove(item)
            self._save()
            if item["kind"] == "file":
                try:
                    (self.folder / item["file"]).unlink(missing_ok=True)
                except OSError:
                    pass
        self.publish({"type": "remove", "id": item_id})
        return True

    def list(self) -> list[dict]:
        with self.lock:
            return list(self.items)

    # --- thiết bị đang kết nối ---
    def device_list(self) -> list[dict]:
        with self.lock:
            return list(self.devices.values())

    def _devices_changed(self):
        self.publish({"type": "devices", "list": self.device_list()})

    # --- realtime (SSE) ---
    def subscribe(self, device: dict | None) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self.lock:
            self.subscribers.append(q)
            if device:
                self.devices[id(q)] = device
        if device:
            log.info("Thiết bị kết nối: %s (%s)", device["name"], device["ip"])
            self._devices_changed()
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            dev = self.devices.pop(id(q), None)
        if dev:
            self._devices_changed()

    def publish(self, event: dict):
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{VERSION}"
    app: "QPhoneServer" = None  # type: ignore

    def log_message(self, fmt, *args):
        if args and "/api/events" in str(args[0]):
            return
        log.info("%s %s", self.client_address[0], fmt % args)

    # ---------- tiện ích ----------
    @property
    def store(self) -> Store:
        return self.app.store

    @property
    def is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _authorized(self) -> tuple[bool, bool]:
        """(hợp lệ, cần set cookie)"""
        token = self.app.config["token"]
        if self.is_local:
            return True, False
        if secrets.compare_digest(self._query().get("t", ""), token):
            return True, True
        if secrets.compare_digest(self.headers.get("X-Token", ""), token):
            return True, False
        c = cookies.SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            pass
        if "qpc_token" in c and secrets.compare_digest(c["qpc_token"].value, token):
            return True, False
        return False, False

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5 * 1024 * 1024:
            raise ValueError("Nội dung quá lớn")
        return json.loads(self.rfile.read(n) or b"{}")

    def _guard(self) -> bool:
        ok, self._set_cookie = self._authorized()
        if not ok:
            if urlparse(self.path).path.startswith("/api/"):
                self._json({"error": "Không có quyền. Hãy quét lại mã QR trên máy tính."}, 401)
            else:
                html = ("<meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        "<h2 style='font-family:sans-serif;padding:16px'>🔒 Hãy quét mã QR trong "
                        "phần mềm QPhoneConnect trên máy tính để kết nối.</h2>")
                self._send(401, html.encode(), "text/html; charset=utf-8")
        return ok

    def _local_only(self) -> bool:
        if not self.is_local:
            self._json({"error": "Chỉ dùng trên máy tính"}, 403)
        return self.is_local

    # ---------- định tuyến ----------
    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = (STATIC_DIR / "index.html").read_bytes()
            extra = {}
            if self._set_cookie:
                extra["Set-Cookie"] = (f"qpc_token={self.app.config['token']}; Path=/; "
                                       "Max-Age=31536000; SameSite=Lax; HttpOnly")
            return self._send(200, body, "text/html; charset=utf-8", extra)
        if path == "/api/info":
            info = {"app": APP_NAME, "version": VERSION, "is_pc": self.is_local, "qr": qrcode is not None}
            if self.is_local:
                ips = get_all_ips()
                info.update({
                    "url": self.app.url_for(ips[0]),
                    "other_urls": [self.app.url_for(ip) for ip in ips[1:]],
                    "folder": str(self.store.folder),
                    "devices": self.store.device_list(),
                    "host": socket.gethostname(),
                })
            return self._json(info)
        if path == "/api/items":
            return self._json(self.store.list())
        if path == "/api/events":
            return self._events()
        if path == "/api/qr.svg":
            return self._qr()
        if path.startswith("/api/file/"):
            return self._download(path.rsplit("/", 1)[-1])
        self._json({"error": "not found"}, 404)

    do_HEAD = do_GET

    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/upload":
                return self._upload()
            if path == "/api/text":
                data = self._read_json()
                text = str(data.get("text", "")).strip()
                if not text:
                    return self._json({"error": "Văn bản trống"}, 400)
                item = self.store.add({"kind": "text", "text": text[:100_000],
                                       "sender": str(data.get("sender", "?"))[:40]})
                return self._json(item, 201)
            # --- chỉ trên máy tính ---
            if path == "/api/open-folder":
                if self._local_only():
                    open_path(self.store.folder)
                    self._json({"ok": True})
                return
            if path.startswith(("/api/open/", "/api/reveal/")):
                if not self._local_only():
                    return
                item = self.store.get(path.rsplit("/", 1)[-1])
                fp = self.store.file_path(item) if item and item["kind"] == "file" else None
                if not fp:
                    return self._json({"error": "Không tìm thấy file"}, 404)
                (open_path if "/open/" in path else reveal_path)(fp)
                return self._json({"ok": True})
        except (ValueError, json.JSONDecodeError) as e:
            return self._json({"error": str(e)}, 400)
        except OSError as e:
            return self._json({"error": f"Lỗi hệ thống: {e}"}, 500)
        self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path.startswith("/api/items/"):
            ok = self.store.remove(path.rsplit("/", 1)[-1])
            return self._json({"ok": ok}, 200 if ok else 404)
        self._json({"error": "not found"}, 404)

    # ---------- xử lý ----------
    def _upload(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return self._json({"error": "Thiếu Content-Length"}, 411)
        remaining = int(length)
        name = safe_filename(unquote(self.headers.get("X-Filename", "file")))
        sender = unquote(self.headers.get("X-Sender", "?"))[:40]
        folder = self.store.folder
        with self.store.lock:
            dest = unique_path(folder, name)
            part = dest.with_name(dest.name + ".part")
            part.touch()  # giữ chỗ tên file khi nhiều file upload song song
        try:
            with open(part, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError("Mất kết nối khi đang tải lên")
                    f.write(chunk)
                    remaining -= len(chunk)
            part.replace(dest)
        except Exception as e:
            part.unlink(missing_ok=True)
            log.warning("Upload lỗi %s: %s", name, e)
            return self._json({"error": str(e)}, 500)
        item = self.store.add({
            "kind": "file", "name": dest.name, "file": dest.name,
            "size": dest.stat().st_size, "sender": sender,
            "mime": mimetypes.guess_type(dest.name)[0] or "application/octet-stream",
        })
        self._json(item, 201)

    def _download(self, item_id: str):
        item = self.store.get(item_id)
        fp = self.store.file_path(item) if item and item["kind"] == "file" else None
        if not fp:
            return self._json({"error": "Không tìm thấy file"}, 404)
        size = fp.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
        if m and size > 0:
            s, e = m.groups()
            if s:
                start, end = int(s), int(e) if e else size - 1
            elif e:
                start, end = max(0, size - int(e)), size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            code = 206
        disp = "inline" if self._query().get("inline") == "1" else "attachment"
        self.send_response(code)
        self.send_header("Content-Type", item.get("mime") or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{quote(item['name'])}")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(fp, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        device = None
        if not self.is_local:
            device = {"name": device_name(self.headers.get("User-Agent", "")), "ip": self.client_address[0]}
        q = self.store.subscribe(device)
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while not self.app.stopping:
                try:
                    ev = q.get(timeout=5)
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.store.unsubscribe(q)

    def _qr(self):
        if qrcode is None:
            return self._json({"error": "Thiếu gói qrcode"}, 404)
        url = self._query().get("url") or self.app.url_for(get_lan_ip())
        img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
        self._send(200, img.to_string(), "image/svg+xml")


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
class _Server(ThreadingHTTPServer):
    # Windows: SO_REUSEADDR cho phép nhiều tiến trình cùng bind một cổng đang có người dùng mà không
    # báo lỗi (khác Linux), nên vòng thử cổng trong start() không bao giờ nhận ra cổng bị trùng với
    # ứng dụng khác - request có thể rơi vào app kia. Trên Windows dùng SO_EXCLUSIVEADDRUSE thay thế.
    allow_reuse_address = not IS_WIN

    def server_bind(self):
        if IS_WIN and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class QPhoneServer:
    def __init__(self, host: str = "0.0.0.0"):
        self.host = host
        self.config = Config()
        self.store = Store(Path(self.config["folder"]))
        self.httpd: ThreadingHTTPServer | None = None
        self.port = 0
        self.stopping = False

    def url_for(self, ip: str) -> str:
        return f"http://{ip}:{self.port}/?t={self.config['token']}"

    def set_folder(self, folder: Path):
        self.store.set_folder(folder)
        self.config["folder"] = str(folder)
        log.info("Đổi thư mục lưu: %s", folder)

    @staticmethod
    def _probe(port: int) -> bool | None:
        """Gọi thử 127.0.0.1:cổng/api/info xem có đúng QPhoneConnect trả lời không.

        True = đúng; False = một chương trình khác đang trả lời cổng này; None = không kiểm tra được.
        """
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                conn.request("GET", "/api/info")
                data = json.loads(conn.getresponse().read().decode("utf-8", "replace"))
            finally:
                conn.close()
        except ValueError:  # không phải JSON -> app khác
            return False
        except (OSError, HTTPException) as e:
            log.warning("Không kiểm tra được cổng %s: %s", port, e)
            return None
        return isinstance(data, dict) and data.get("app") == APP_NAME

    def start(self) -> int:
        """Mở server trong luồng nền, trả về cổng đang dùng."""
        Handler.app = self
        want = int(self.config["port"])
        for p in range(want, want + 20):
            try:
                httpd = _Server((self.host, p), Handler)
            except OSError as e:
                log.info("Cổng %s đang bận (%s), thử cổng kế tiếp", p, e)
                continue
            httpd.daemon_threads = True
            self.httpd, self.port = httpd, p
            threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
            if self._probe(p) is False:
                log.warning("Cổng %s đang do chương trình khác trả lời, thử cổng kế tiếp", p)
                self.httpd, self.port = None, 0
                httpd.shutdown()
                httpd.server_close()
                continue
            log.info("%s %s chạy ở cổng %s, thư mục %s", APP_NAME, VERSION, p, self.store.folder)
            return p
        raise OSError(f"Không mở được cổng nào từ {want} đến {want + 19}")

    def stop(self):
        self.stopping = True
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
