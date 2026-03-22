# BIST Pro

**Kisisel kullanim ve egitim amacli teknik analiz araci**

> **YASAL UYARI:** Bu uygulama yatirim tavsiyesi vermez.
> Tamamen kisisel ve egitim amaclidir.

---

## Ozellikler

- 8 AI Sistem (SuperTrend+TMA, PRO Engine, Fusion, Master AI, A60-A120)
- 424+ BIST Hissesi (XU030 / XU050 / XU100 / 22 Sektor Endeksi)
- Gercek zamanli tarama, Walk-Forward, Monte Carlo, Optimizasyon
- Sosyal: uyelik, WebSocket sohbet, forum
- AI sohbet, OpenClaw Gateway, Groq Llama
- PWA: iOS/Android ana ekranina eklenebilir, tamamen lokal calistirabilir

---

## Kurulum

### Render.com

```bash
# Start Command:
uvicorn main:app --host 0.0.0.0 --port $PORT
# Environment Variables:
# JWT_SECRET=gizli-anahtar
# GROQ_API_KEY=gsk_xxx
```

### Lokal (Windows/Mac)

```bash
cd bist_render
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
./start.sh
# http://localhost:8000
```

---

## Cevre Degiskenleri

| Degisken | Zorunlu | Aciklama |
|----------|---------|----------|
| JWT_SECRET | Evet | JWT imzalama anahtari |
| GROQ_API_KEY | Hayir | Ucretsiz AI |
| HF_API_KEY | Hayir | HuggingFace |
| ANTHROPIC_KEY | Hayir | Claude API |
| DB_PATH | Hayir | SQLite yolu |

---

## YASAL SORUMLULUK REDDI BEYANI

Bu uygulama ("BIST Pro") yalnizca **kisisel kullanim, egitim ve
bilgilendirme amacli** hazirlanmistir.

Bu uygulama icerisinde yer alan hicbir sinyal, analiz, grafik,
gosterge, backtest sonucu, yapay zeka ciktisi veya diger herhangi
bir bilgi; **6362 sayili Sermaye Piyasasi Kanunu** ve ilgili mevzuat
kapsaminda yatirim tavsiyesi, portfoy yonetimi veya yatirim
danismanligi hizmeti **teskil etmez** ve Sermaye Piyasasi Kurulu (SPK)
tarafindan lisansli yatirim danismanligi kapsaminda
**degerlendirilmez**.

Borsa ve sermaye piyasalarinda islem yapmak onemli finansal riskler
icerir. **Yatirimlarinizin tamamini veya bir kismini
kaybedebilirsiniz.** Gecmis performans gelecekteki sonuclarin
garantisi degildir.

Alim-satim kararlari tamamen kullanicinin kendisine aittir.
Uygulama gelistiricisi hicbir sekilde yatirim sonuclarindan
sorumlu tutulamaz.

Gosterilen fiyat ve veriler ucuncu parti kaynaklardan alinmakta
olup gercek zamanli olmayabilir. Resmi islem kararlari icin
brokerinizin sistemini kullanin.

**Bu uygulamayi kullanarak yukaridaki kosullari kabul etmis
sayilirsiniz.**

---

### English Disclaimer

This application ("BIST Pro") is developed solely for personal use,
educational and informational purposes.

No signal, analysis, backtest result, AI output or any information
within this application constitutes investment advice, portfolio
management or investment advisory services under Capital Markets Law
No. 6362. It is not licensed by the Capital Markets Board of Turkey.

Past performance is not a guarantee of future results.
Trading decisions are entirely the user's responsibility.

**By using this application, you accept all of the above terms.**

---

## Lisans

MIT License - Kisisel ve egitim amacli kullanim serbesttir.
Ticari kullanim icin izin gereklidir.
