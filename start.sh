#!/bin/bash
# BIST Katilim - Lokal Baslangic Scripti (Mac/Linux)
set -e

echo "╔══════════════════════════════════╗"
echo "║   BIST Katilim v1 - Lokal        ║"
echo "╚══════════════════════════════════╝"

# Python kontrolu
if ! command -v python3 &>/dev/null; then
    echo "HATA: Python3 bulunamadi!"
    echo "Mac: brew install python3"
    echo "Linux: sudo apt install python3 python3-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PYTHON_VERSION"

# Sanal ortam
if [ ! -d ".venv" ]; then
    echo "Sanal ortam olusturuluyor..."
    python3 -m venv .venv
fi

source .venv/bin/activate
echo "Sanal ortam aktif"

# Bagimliliklari yukle
pip install -r requirements.txt -q
echo "Bagimlilıklar hazir"

# .env kontrol
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "ONEMLI: .env dosyasi olusturuldu."
    echo "GROQ_API_KEY eklemek icin: nano .env"
    echo ""
fi

# Lokal IP
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || ifconfig | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | head -1)
PORT=${PORT:-8000}

echo ""
echo "Uygulama baslatiliyor..."
echo ""
echo "Adresler:"
echo "  Bilgisayar: http://localhost:$PORT"
echo "  Telefon (ayni WiFi): http://$LOCAL_IP:$PORT"
echo ""
echo "Durdurmak icin: Ctrl+C"
echo ""

uvicorn main:app --host 0.0.0.0 --port $PORT --reload
