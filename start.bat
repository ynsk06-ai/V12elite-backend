@echo off
chcp 65001 >nul
title BIST Katilim v1 - Lokal

echo ╔══════════════════════════════════╗
echo ║   BIST Katilim v1 - Lokal        ║
echo ╚══════════════════════════════════╝
echo.

:: Python kontrol
python --version >nul 2>&1
if errorlevel 1 (
    echo HATA: Python bulunamadi!
    echo python.org/downloads adresinden indirin
    echo Microsoft Store'dan da kurabilirsiniz: "Python 3.11"
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Python: %PYVER%

:: Sanal ortam
if not exist ".venv" (
    echo Sanal ortam olusturuluyor...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
echo Sanal ortam aktif

:: Bagimliliklari yukle
pip install -r requirements.txt -q
echo Bagimlilıklar hazir

:: .env kontrol
if not exist ".env" (
    copy .env.example .env
    echo.
    echo ONEMLI: .env dosyasi olusturuldu.
    echo GROQ_API_KEY eklemek icin .env dosyasini Not Defteri ile acin.
    echo.
)

:: Lokal IP bul
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1" ^| head -1') do (
    set LOCAL_IP=%%a
    set LOCAL_IP=!LOCAL_IP:~1!
)

if not defined PORT set PORT=8000

echo.
echo Uygulama baslatiliyor...
echo.
echo Adresler:
echo   Bilgisayar: http://localhost:%PORT%
echo   Telefon (ayni WiFi icin lokal IP'nizi kontrol edin)
echo.
echo Durdurmak icin: Ctrl+C
echo.

uvicorn main:app --host 0.0.0.0 --port %PORT% --reload

pause
