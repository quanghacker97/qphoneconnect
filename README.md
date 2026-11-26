# 📲 QPhoneConnect

Phần mềm Windows để **truyền file và văn bản hai chiều giữa máy tính và điện thoại qua WiFi**.
Máy tính cài QPhoneConnect, còn điện thoại **không cần cài gì**: chỉ cần quét mã QR là mở được trên trình duyệt (Android lẫn iPhone).

## Dành cho người dùng
1. Chạy `QPhoneConnect-Setup-1.0.0.exe` và bấm Next cho đến khi cài xong.
2. Mở QPhoneConnect từ icon ngoài Desktop.
3. Điện thoại bắt WiFi **cùng mạng** với máy tính, mở Camera và quét mã QR hiện trên cửa sổ.
4. Gửi file hoặc văn bản qua lại. File nhận được nằm trong `Documents\QPhoneConnect` (đổi được trong app).

> Nếu Windows hiện "Windows protected your PC": bấm **More info → Run anyway**. Cảnh báo này xuất hiện vì phần mềm chưa được ký số, không phải virus.

## Tính năng
- Ứng dụng có **cửa sổ riêng**, không phải mở trình duyệt. Mỗi máy chỉ chạy một cửa sổ; mở lần hai thì cửa sổ cũ được đưa lên.
- Gửi file hai chiều, nhiều file cùng lúc, có thanh tiến trình kèm tốc độ. File vài GB cũng không tốn RAM.
- Gửi văn bản và link qua lại, có nút sao chép.
- Danh sách cập nhật realtime. Máy tính thấy được **điện thoại nào đang kết nối**.
- Trên PC: kéo thả file, Ctrl+V để dán ảnh, mở file, hiện file trong thư mục, đổi thư mục lưu.
- Trên điện thoại: tải về, xem trước ảnh/video/nhạc, tua được video.
- Bảo mật: muốn kết nối phải quét đúng mã QR (có token). Điện thoại tự nhớ nên lần sau không phải quét lại.
- Bộ cài tự mở tường lửa Windows cho app, tạo icon Desktop và Start Menu, có trình gỡ cài đặt, có tùy chọn tự chạy cùng Windows.

## Build ra file cài đặt

### Cách 1: build trên máy Windows của bạn
1. Cài **Python 3.12** từ python.org (nhớ tick *Add python.exe to PATH*).
2. Cài **Inno Setup 6**: `winget install JRSoftware.InnoSetup`
3. Bấm đúp `build.bat`.

Kết quả nằm trong thư mục `dist\`:
| File | Dùng để |
|---|---|
| `QPhoneConnect-Setup-1.0.0.exe` | **Gửi cho người khác**: bộ cài đầy đủ (~15–25 MB) |
| `QPhoneConnect-1.0.0-Portable.zip` | Bản giải nén là chạy, không cần cài |

### Cách 2: để GitHub tự build (không cần cài gì trên máy)
1. Đẩy thư mục này lên một repo GitHub.
2. Vào tab **Actions → Build Windows → Run workflow**.
3. Khoảng 3–5 phút sau, tải file trong mục **Artifacts**.

Nếu muốn tạo bản phát hành có link tải cố định: `git tag v1.0.0 && git push --tags`. Bộ cài sẽ được đính vào mục **Releases** của repo.

### Đổi phiên bản
Sửa `VERSION` trong `server.py`. Tên bộ cài sẽ tự đổi theo.

## Chạy khi đang phát triển
```bash
pip install -r requirements.txt
python main.py            # cửa sổ app
python main.py --browser  # mở bằng trình duyệt (dễ debug bằng F12)
```

## Cấu trúc
```
main.py                 app desktop: cửa sổ pywebview, chỉ cho 1 instance, hộp thoại chọn thư mục
server.py               lõi: HTTP server, token, upload/download, realtime (SSE), danh sách thiết bị
static/index.html       giao diện dùng chung cho PC và điện thoại (tự nhận biết)
assets/icon.ico|png     icon
installer/*.iss         script bộ cài Inno Setup
build.bat               build 1 lần bấm
.github/workflows/      GitHub Actions build tự động
```
Cấu hình và log nằm trong `%APPDATA%\QPhoneConnect\` (`config.json` và `qphoneconnect.log`).

## Mã nguồn mở đang dùng
- **pywebview** (BSD): hiện giao diện trong cửa sổ native, dùng Edge WebView2 có sẵn trên Windows 10/11.
- **qrcode** (BSD): vẽ mã QR.
- **PyInstaller** (GPL, có ngoại lệ cho phép phát hành bản đóng gói): đóng gói thành file .exe.
- **Inno Setup**: tạo bộ cài.

## Ghi chú
- Máy nào chưa có **WebView2** (một số máy Win 10 cũ) thì app tự chuyển sang mở bằng trình duyệt và hiện link để cài WebView2.
- Rule tường lửa mở cho mọi loại mạng, vì nhiều WiFi gia đình bị Windows xếp vào loại "Public". Người lạ không vào được vì cần token trong mã QR. Muốn đổi token, sửa hoặc xóa dòng `token` trong `config.json`.

## Hướng phát triển
- Thu nhỏ xuống khay hệ thống (dùng `pystray`).
- Tự dò thiết bị qua mDNS (dùng `zeroconf`).
- Tương thích giao thức **LocalSend** để dùng app LocalSend có sẵn trên điện thoại.
- Ký số file .exe để không còn cảnh báo SmartScreen.
