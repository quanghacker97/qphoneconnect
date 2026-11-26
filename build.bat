@echo off
REM =====================================================================
REM  Build QPhoneConnect cho Windows
REM  Ket qua (thu muc dist):
REM    QPhoneConnect-Setup-x.y.z.exe   -> bo cai, gui file nay cho nguoi khac
REM    QPhoneConnect-x.y.z-Portable.zip -> ban chay ngay khong can cai
REM  Can: Python 3.11/3.12 (python.org), Inno Setup 6 (de tao bo cai)
REM =====================================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto :nopython

echo [1/4] Cai thu vien...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :err

for /f %%v in ('python -c "import server;print(server.VERSION)"') do set VERSION=%%v
echo Phien ban: %VERSION%

echo [2/4] Dong goi bang PyInstaller...
python -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name QPhoneConnect --icon assets\icon.ico ^
  --add-data "static;static" --add-data "assets;assets" ^
  main.py
if errorlevel 1 goto :err

echo [3/4] Tao ban Portable (.zip)...
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\QPhoneConnect' -DestinationPath 'dist\QPhoneConnect-%VERSION%-Portable.zip'"

echo [4/4] Tao bo cai bang Inno Setup...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" goto :noinno
"%ISCC%" /Q /DMyAppVersion=%VERSION% installer\QPhoneConnect.iss
if errorlevel 1 goto :err

echo.
echo ===== XONG =====
echo Bo cai : dist\QPhoneConnect-Setup-%VERSION%.exe
echo Portable: dist\QPhoneConnect-%VERSION%-Portable.zip
goto :done

:noinno
echo.
echo Chua cai Inno Setup 6 nen chua tao duoc bo cai .exe
echo Cai bang lenh:  winget install JRSoftware.InnoSetup   roi chay lai build.bat
echo Tam thoi co the gui ban Portable: dist\QPhoneConnect-%VERSION%-Portable.zip
goto :done

:nopython
echo Chua cai Python. Tai Python 3.12 tai https://www.python.org/downloads/
echo (nho tick "Add python.exe to PATH" khi cai)
goto :done

:err
echo.
echo *** BUILD LOI - xem thong bao phia tren ***
if not defined CI pause
exit /b 1

:done
if not defined CI pause
exit /b 0
