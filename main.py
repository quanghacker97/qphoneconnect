"""
QPhoneConnect - ứng dụng desktop.

Mở cửa sổ riêng (pywebview: WebView2 trên Windows) hiển thị giao diện điều khiển,
server chạy nền để điện thoại kết nối qua WiFi.

    python main.py            # chạy cửa sổ app
    python main.py --browser  # chạy bằng trình duyệt (khi dev / không có pywebview)
"""
from __future__ import annotations

import os
import sys
import time
import traceback
import webbrowser
from pathlib import Path

import server as core

_MUTEX = None  # giữ handle để mutex sống suốt vòng đời app


def message_box(text: str, title: str = core.APP_NAME, error: bool = False) -> None:
    if core.IS_WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10 if error else 0x40)
    else:
        print(f"{title}: {text}", file=sys.stderr if sys.stderr else None)


def already_running() -> bool:
    """Chỉ cho chạy 1 cửa sổ QPhoneConnect trên Windows."""
    global _MUTEX
    if not core.IS_WIN:
        return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    _MUTEX = kernel32.CreateMutexW(None, False, "Local\\QPhoneConnect_SingleInstance")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def focus_existing_window() -> None:
    if core.IS_WIN:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, core.APP_NAME)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return
    message_box("QPhoneConnect đang chạy rồi.")


class Api:
    """Các hàm JavaScript gọi được qua window.pywebview.api.* (chỉ có trong cửa sổ app)."""

    def __init__(self, app: core.QPhoneServer):
        # thuộc tính bắt đầu bằng "_" để pywebview không đưa ra JS
        self._app = app
        self._window = None

    def choose_folder(self):
        import webview
        dialog = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if dialog is None:
            dialog = webview.FOLDER_DIALOG
        result = self._window.create_file_dialog(dialog, directory=str(self._app.store.folder))
        if not result:
            return None
        folder = result[0] if isinstance(result, (list, tuple)) else result
        self._app.set_folder(Path(folder))
        return str(folder)

    def version(self):
        return core.VERSION


def has_webview2() -> bool:
    """Kiểm tra Microsoft Edge WebView2 Runtime (có sẵn trên Win 11 và đa số Win 10)."""
    if not core.IS_WIN:
        return True
    import winreg
    guid = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
    ]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as k:
                ver, _ = winreg.QueryValueEx(k, "pv")
                if ver and ver != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def run_browser_mode(app: core.QPhoneServer, url: str) -> None:
    webbrowser.open(url)
    if sys.stdout is None and core.IS_WIN:  # bản .exe không có console
        message_box("QPhoneConnect đang chạy trong trình duyệt.\n"
                    "Máy chưa có Microsoft Edge WebView2 nên không mở được cửa sổ riêng.\n"
                    "Cài tại: https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
                    "Bấm OK để TẮT QPhoneConnect.")
        return
    print(f"{core.APP_NAME} {core.VERSION} - mở {url}  (Ctrl+C để tắt)")
    print("Điện thoại:", app.url_for(core.get_lan_ip()))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    log_path = core.setup_logging()
    if already_running():
        focus_existing_window()
        return

    app = core.QPhoneServer()
    try:
        app.start()
    except OSError as e:
        message_box(f"Không khởi động được server:\n{e}", error=True)
        return
    url = f"http://127.0.0.1:{app.port}/"

    webview = None
    if "--browser" not in sys.argv and has_webview2():
        try:
            import webview  # type: ignore
        except ImportError:
            webview = None

    try:
        if webview is None:
            run_browser_mode(app, url)
        else:
            api = Api(app)
            window = webview.create_window(
                core.APP_NAME, url, js_api=api,
                width=1120, height=800, min_size=(420, 560),
                background_color="#0f172a", text_select=True,
            )
            api._window = window
            icon = core.BASE_DIR / "assets" / "icon.png"
            try:
                webview.start(icon=str(icon) if icon.exists() else None)
            except TypeError:  # pywebview cũ không có tham số icon
                webview.start()
    except Exception:
        core.log.error("Lỗi: %s", traceback.format_exc())
        message_box(f"QPhoneConnect gặp lỗi. Chi tiết trong:\n{log_path}", error=True)
    finally:
        app.stop()
        os._exit(0)  # đóng luôn các kết nối điện thoại đang mở


if __name__ == "__main__":
    main()
