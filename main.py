"""
QPhoneConnect - ứng dụng desktop.

Mở cửa sổ riêng (pywebview: WebView2 trên Windows) hiển thị giao diện điều khiển,
server chạy nền để điện thoại kết nối qua WiFi.

    python main.py            # chạy cửa sổ app
    python main.py --browser  # chạy bằng trình duyệt (khi dev / không có pywebview)
"""
from __future__ import annotations

import os
import platform
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


NO_WEBVIEW2_NOTE = ("Máy chưa có Microsoft Edge WebView2 nên không mở được cửa sổ riêng.\n"
                    "Cài tại: https://go.microsoft.com/fwlink/p/?LinkId=2124703")

# Giao diện phải tải xong trong khoảng này, quá hạn thì coi như cửa sổ app không lên được
WINDOW_TIMEOUT = 20  # giây


def run_browser_mode(app: core.QPhoneServer, url: str, note: str = NO_WEBVIEW2_NOTE) -> None:
    webbrowser.open(url)
    if sys.stdout is None and core.IS_WIN:  # bản .exe không có console
        message_box(f"QPhoneConnect đang chạy trong trình duyệt.\n{note}\n\n"
                    "Bấm OK để TẮT QPhoneConnect.")
        return
    print(f"{core.APP_NAME} {core.VERSION} - mở {url}  (Ctrl+C để tắt)")
    print("Điện thoại:", app.url_for(core.get_lan_ip()))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_window(webview, app: core.QPhoneServer, url: str) -> bool:
    """Mở cửa sổ app và chờ đến khi đóng.

    Trả về False nếu giao diện chưa từng tải được (WebView2 khởi tạo lỗi, cửa sổ trống...)
    để người gọi chuyển sang trình duyệt thay vì thoát im lặng.
    """
    api = Api(app)
    window = webview.create_window(
        core.APP_NAME, url, js_api=api,
        width=1120, height=800, min_size=(420, 560),
        background_color="#0f172a", text_select=True,
    )
    api._window = window
    t0 = time.monotonic()

    # Các mốc này cho biết app dừng ở bước nào nếu cửa sổ không lên (xem file log)
    def on_shown() -> None:
        core.log.info("Cửa sổ đã hiện (%.1fs)", time.monotonic() - t0)

    def on_loaded() -> None:
        core.log.info("Giao diện đã tải xong (%.1fs)", time.monotonic() - t0)

    window.events.shown += on_shown
    window.events.loaded += on_loaded

    def watchdog() -> None:  # chạy ở luồng riêng sau khi pywebview khởi tạo xong engine
        core.log.info("Đã khởi tạo engine, chờ giao diện tải (tối đa %ss)", WINDOW_TIMEOUT)
        if window.events.loaded.wait(WINDOW_TIMEOUT):
            return
        core.log.error("Giao diện chưa tải xong sau %ss - đóng cửa sổ để chuyển sang trình duyệt", WINDOW_TIMEOUT)
        try:
            window.destroy()
        except Exception:
            core.log.exception("Không đóng được cửa sổ bị lỗi")

    # Windows (WinForms) tạo icon bằng System.Drawing.Icon - chỉ nhận .ico. Đưa .png vào sẽ ném
    # ArgumentException ở luồng .NET, làm cả tiến trình chết ngay mà không để lại log Python nào.
    icon = core.BASE_DIR / "assets" / ("icon.ico" if core.IS_WIN else "icon.png")
    core.log.info("Gọi webview.start() - khởi tạo pythonnet/WebView2, trang: %s", url)
    try:
        webview.start(watchdog, icon=str(icon) if icon.exists() else None)
    except TypeError:  # pywebview cũ không có tham số icon
        webview.start(watchdog)

    loaded = window.events.loaded.is_set()
    core.log.info("Cửa sổ đã đóng (renderer=%s, đã tải giao diện=%s)", getattr(webview, "renderer", None), loaded)
    return loaded


def main() -> None:
    log_path = core.setup_logging()
    core.log.info("Khởi động %s %s | Python %s | %s | frozen=%s", core.APP_NAME, core.VERSION,
                  platform.python_version(), platform.platform(), bool(getattr(sys, "frozen", False)))
    app = None
    exit_code = 0
    try:
        if already_running():
            core.log.info("Đã có một cửa sổ QPhoneConnect đang chạy")
            focus_existing_window()
            return

        app = core.QPhoneServer()
        try:
            app.start()
        except OSError as e:
            core.log.error("Không khởi động được server: %s", e)
            message_box(f"Không khởi động được server:\n{e}", error=True)
            exit_code = 1
            return
        url = f"http://127.0.0.1:{app.port}/"

        webview = None
        note = NO_WEBVIEW2_NOTE
        if "--browser" in sys.argv:
            note = "Đang chạy theo tùy chọn --browser."
        elif not has_webview2():
            core.log.warning("Không tìm thấy Microsoft Edge WebView2 Runtime - chạy bằng trình duyệt")
        else:
            core.log.info("Nạp pywebview")
            try:
                import webview  # type: ignore
            except Exception:
                core.log.error("Không nạp được pywebview:\n%s", traceback.format_exc())
                note = f"Không nạp được thư viện cửa sổ (pywebview).\nChi tiết lỗi: {log_path}"

        if webview is None:
            run_browser_mode(app, url, note)
        elif not run_window(webview, app, url):
            core.log.error("Cửa sổ app không hiển thị được - chuyển sang trình duyệt")
            run_browser_mode(app, url, f"Không mở được cửa sổ riêng nên chạy bằng trình duyệt.\n"
                                       f"Chi tiết lỗi: {log_path}")
    except Exception:
        core.log.error("Lỗi: %s", traceback.format_exc())
        message_box(f"QPhoneConnect gặp lỗi. Chi tiết trong:\n{log_path}", error=True)
        exit_code = 1
    finally:
        if app is not None:
            app.stop()
        os._exit(exit_code)  # đóng luôn các kết nối điện thoại đang mở


if __name__ == "__main__":
    main()
