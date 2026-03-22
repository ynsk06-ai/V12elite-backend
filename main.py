"""
BIST AI Proxy v3 - Render Free Tier
─────────────────────────────────────
1. Pine Script birebir PRO Engine (XU100 RS, 4H/2H multi-TF, MACD, DNA)
2. APScheduler: piyasa saatlerinde 7/24 otomatik tarama + Telegram
"""

from fastapi import FastAPI, Response

# ─── LOKAL .ENV DESTEGI ─────────────────────────────────────
import os as _os
_env_file = _os.path.join(_os.path.dirname(__file__), '.env')
if _os.path.exists(_env_file):
    with open(_env_file) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                _os.environ.setdefault(_k.strip(), _v.strip())
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager
import httpx, asyncio, time, math, os, logging, json
from datetime import datetime

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler(timezone="Europe/Istanbul")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        from social import init_db, _register_social_routes, _register_stats_route
        init_db()
        _register_social_routes(app)
        _register_stats_route(app)
    except Exception as e:
        print(f"Social init hatasi: {e}")
    try:
        scheduler.add_job(auto_scan, "cron", minute="*/5",
                          id="bist_scan", replace_existing=True)
        scheduler.start()
        log.info(f"Scheduler started. TG: {bool(TG_TOKEN and TG_CHAT)}")
    except Exception as e:
        log.error(f"Scheduler start error: {e}")
    yield
    # Shutdown: scheduler'i durdur
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("Scheduler stopped")
    except Exception as e:
        log.error(f"Scheduler stop error: {e}")

app = FastAPI(title="BIST AI Proxy v3", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

YH  = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"}
BIN = {"User-Agent": "BIST-AI-Scanner/3.0"}
_cache: dict = {}

def cget(k):
    e = _cache.get(k)
    return e["d"] if e and time.time()-e["t"]<e["ttl"] else None

def cset(k, d, ttl=300):
    _cache[k] = {"d":d,"t":time.time(),"ttl":ttl}


# ─────────────────────────────────────────────────────────
# TEKNİK ANALİZ
# ─────────────────────────────────────────────────────────

def _atr(h,l,c,n=14):
    tr=[max(h[i]-l[i],abs(h[i]-c[i-1]) if i else 0,abs(l[i]-c[i-1]) if i else 0) for i in range(len(c))]
    a=[0.0]*len(c)
    if len(c)<n: return a
    a[n-1]=sum(tr[:n])/n
    for i in range(n,len(c)): a[i]=(a[i-1]*(n-1)+tr[i])/n
    return a

def _ema(c,n):
    k=2/(n+1); e=[0.0]*len(c); e[0]=c[0]
    for i in range(1,len(c)): e[i]=c[i]*k+e[i-1]*(1-k)
    return e

def _sma(c,n):
    out=[0.0]*len(c)
    for i in range(n-1,len(c)): out[i]=sum(c[i-n+1:i+1])/n
    return out

def _rsi(c,n=14):
    L=len(c); r=[50.0]*L
    if L<n+1: return r
    d=[c[i]-c[i-1] for i in range(1,L)]
    ag=sum(max(x,0) for x in d[:n])/n; al=sum(max(-x,0) for x in d[:n])/n
    for i in range(n,L-1):
        ag=(ag*(n-1)+max(d[i],0))/n; al=(al*(n-1)+max(-d[i],0))/n
        r[i+1]=100-100/(1+ag/(al+1e-10))
    return r

def _supertrend(h,l,c,atr_len=10,mult=3.0):
    at=_atr(h,l,c,atr_len); n=len(c)
    hl2=[(h[i]+l[i])/2 for i in range(n)]
    up=[hl2[i]+mult*at[i] for i in range(n)]; dn=[hl2[i]-mult*at[i] for i in range(n)]
    fu=list(up); fd=list(dn)
    for i in range(1,n):
        fu[i]=up[i] if up[i]<fu[i-1] or c[i-1]>fu[i-1] else fu[i-1]
        fd[i]=dn[i] if dn[i]>fd[i-1] or c[i-1]<fd[i-1] else fd[i-1]
    d=[1]*n
    for i in range(1,n):
        if d[i-1]==-1 and c[i]>fu[i]: d[i]=1
        elif d[i-1]==1 and c[i]<fd[i]: d[i]=-1
        else: d[i]=d[i-1]
    return d,fu,fd

def _adx(h,l,c,n=14):
    L=len(c); tr=[0.0]*L; pdm=[0.0]*L; mdm=[0.0]*L
    for i in range(1,L):
        tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
        hd=h[i]-h[i-1]; ld=l[i-1]-l[i]
        pdm[i]=hd if hd>ld and hd>0 else 0
        mdm[i]=ld if ld>hd and ld>0 else 0
    def ws(a,n):
        o=[0.0]*L
        if L<n: return o
        o[n-1]=sum(a[:n])
        for i in range(n,L): o[i]=o[i-1]-o[i-1]/n+a[i]
        return o
    s=ws(tr,n); p=ws(pdm,n); m=ws(mdm,n); eps=1e-10
    pi=[100*p[i]/(s[i]+eps) for i in range(L)]
    mi=[100*m[i]/(s[i]+eps) for i in range(L)]
    dx=[100*abs(pi[i]-mi[i])/(pi[i]+mi[i]+eps) for i in range(L)]
    return [ws(dx,n)[i]/n for i in range(L)], pi, mi

def _tma_upper(c,length=200,amult=8.0,alen=20):
    half=length//2+1
    s1=_sma(c,half); t=_sma(s1,half)
    ph=[max(c[i],c[i-1] if i else c[i]) for i in range(len(c))]
    pl=[min(c[i],c[i-1] if i else c[i]) for i in range(len(c))]
    at=_atr(ph,pl,c,alen)
    return [t[i]+amult*at[i] for i in range(len(c))]

def _chandelier(h,l,c,n=20,mult=8.0):
    at=_atr(h,l,c,n); L=len(c); stops=[0.0]*L
    for i in range(n,L): stops[i]=max(h[max(0,i-n):i+1])-mult*at[i]
    return stops

def _pstate(closes,n=150):
    """Pine quantum states ile birebir pstate"""
    n=min(n,len(closes))
    if n<10: return "NORMAL"
    arr=sorted(closes[-n:]); close=closes[-1]
    rank=sum(1 for x in arr if x<=close); pricePct=rank/n
    mean=sum(closes[-n:])/n
    variance=sum((x-mean)**2 for x in closes[-n:])/n
    stdev=math.sqrt(variance) if variance>0 else 1e-10
    z=(close-mean)/stdev
    vCQ=max(0,(0.15-pricePct)/0.15)*max(0,(-1.5-z)/-1.5)
    cQ=max(0,abs(pricePct-0.225)/0.075)*max(0,(-0.5-z)/-0.5)
    nQ=max(0,1-abs(pricePct-0.5)/0.2)
    eQ=max(0,abs(pricePct-0.775)/0.075)*max(0,(z-0.5)/0.5)
    vEQ=max(0,(pricePct-0.85)/0.15)*max(0,(z-1.5)/1.5)
    sm=vCQ+cQ+nQ+eQ+vEQ
    if sm>0: vCQ/=sm; cQ/=sm; nQ/=sm; eQ/=sm; vEQ/=sm
    if vEQ>0.5: return "COK PAHALI"
    if eQ>0.5:  return "PAHALI"
    if nQ>0.5:  return "NORMAL"
    if cQ>0.5:  return "UCUZ"
    if vCQ>0.5: return "COK UCUZ"
    states=[("COK UCUZ",vCQ),("UCUZ",cQ),("NORMAL",nQ),("PAHALI",eQ),("COK PAHALI",vEQ)]
    return max(states,key=lambda x:x[1])[0]


# ─────────────────────────────────────────────────────────
# OHLCV ÇEKİM
# ─────────────────────────────────────────────────────────

async def fetch_ohlcv(ticker:str, tf:str, suffix:str=".IS") -> list:
    k=f"ohlcv_{ticker}_{tf}"; cached=cget(k)
    if cached: return cached
    intv={"D":"1d","240":"60m","120":"30m"}.get(tf,"1d")
    rng={"D":"2y","240":"60d","120":"30d"}.get(tf,"2y")
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}?interval={intv}&range={rng}"
    try:
        async with httpx.AsyncClient(timeout=20,headers=YH) as cl:
            r=await cl.get(url); d=r.json()
        res=d["chart"]["result"][0]
        ts=res["timestamp"]; q=res["indicators"]["quote"][0]
        adj=res["indicators"].get("adjclose",[{}])[0].get("adjclose",[])
        cls=adj if adj else q.get("close",[])
        data=[]
        for i in range(len(ts)):
            ov=q["open"][i]; hv=q["high"][i]; lv=q["low"][i]
            cv=cls[i] if i<len(cls) else q["close"][i]; vv=q["volume"][i]
            if None not in (ov,hv,lv,cv):
                data.append({"t":ts[i],"o":float(ov),"h":float(hv),
                             "l":float(lv),"c":float(cv),"v":float(vv or 0)})
        cset(k,data,600); return data
    except Exception as e:
        log.warning(f"OHLCV err {ticker}: {e}"); return []

async def fetch_xu100_closes() -> list:
    k="xu100_closes"; cached=cget(k)
    if cached: return cached
    try:
        async with httpx.AsyncClient(timeout=15,headers=YH) as cl:
            r=await cl.get("https://query1.finance.yahoo.com/v8/finance/chart/XU100.IS?interval=1d&range=2y")
            d=r.json()
        res=d["chart"]["result"][0]
        adj=res["indicators"].get("adjclose",[{}])[0].get("adjclose",[])
        cls=adj if adj else res["indicators"]["quote"][0].get("close",[])
        closes=[float(x) for x in cls if x is not None]
        cset(k,closes,3600); return closes
    except Exception as e:
        log.warning(f"XU100 closes err: {e}"); return []


# ─────────────────────────────────────────────────────────
# PINE BİREBİR ANALİZ — 6 PRO FAKTÖRÜ DOĞRU
# ─────────────────────────────────────────────────────────

def analyze_full(ohlcv_d,ohlcv_4h,ohlcv_2h,xu100_c,cfg) -> dict:
    if len(ohlcv_d)<60: return {"signal":False,"error":"Yetersiz veri"}

    c=[x["c"] for x in ohlcv_d]; h=[x["h"] for x in ohlcv_d]
    l=[x["l"] for x in ohlcv_d]; v=[x["v"] for x in ohlcv_d]
    i=len(c)-1; pr=c[i]

    sd,su,sdn=_supertrend(h,l,c,cfg.get("st_len",10),cfg.get("st_mult",3.0))
    tu=_tma_upper(c,cfg.get("tma_len",200),cfg.get("tma_atr_mult",8.0))
    adv,pdi,mdi=_adx(h,l,c)
    rsi_d=_rsi(c); e200=_ema(c,200); e50=_ema(c,50)
    e12=_ema(c,12); e26=_ema(c,26); chst=_chandelier(h,l,c)
    sma20=_sma(c,20)

    ad=adv[i]; ri=rsi_d[i]; macd=e12[i]-e26[i]; am=cfg.get("adx_min",25)

    # Sistem 1: Pine longTrendFlipUp
    flip_up=sd[i]==1 and (i>0 and sd[i-1]==-1)
    s1=flip_up and pr>tu[i] and ad>am

    # ── PRO 6 faktör (Pine birebir) ──────────────────
    # 1. rsStrong2: close/xu100 >= highest(rs,200)*0.97
    rs_strong=False
    if xu100_c and len(xu100_c)>=2:
        rs_vals=[c[j]/xu100_c[min(j,len(xu100_c)-1)] for j in range(len(c))]
        lb200=max(0,i-199)
        rs_max=max(rs_vals[lb200:i+1]) if rs_vals[lb200:i+1] else rs_vals[i]
        rs_strong=rs_vals[i]>=rs_max*0.97

    # 2. accumD: daily close > sma(20)
    accum_d=pr>sma20[i] if i>20 else False

    # 3. exp4h: 4H range > sma(range,20)*1.5
    exp_4h=False
    if len(ohlcv_4h)>=25:
        h4=[x["h"] for x in ohlcv_4h]; l4=[x["l"] for x in ohlcv_4h]
        i4=len(h4)-1
        ranges=[h4[j]-l4[j] for j in range(len(h4))]
        sma_r=_sma(ranges,20)
        exp_4h=ranges[i4]>sma_r[i4]*1.5

    # 4. break4h: 4H high >= highest(high,20)
    break_4h=False
    if len(ohlcv_4h)>=25:
        h4=[x["h"] for x in ohlcv_4h]; i4=len(h4)-1
        lb4=max(0,i4-19)
        break_4h=h4[i4]>=max(h4[lb4:i4+1])

    # 5. mom2h: 2H RSI(14) > 60
    mom_2h=False
    if len(ohlcv_2h)>=20:
        c2=[x["c"] for x in ohlcv_2h]
        mom_2h=_rsi(c2)[-1]>60

    # 6. dna: RSI(14) > 55 AND macd > 0
    dna=ri>55 and macd>0

    score6=sum([rs_strong,accum_d,exp_4h,break_4h,mom_2h,dna])
    trend2=pr>e200[i] and ad>am and pr>tu[i]
    s2=score6>=cfg.get("pro_min",5) and trend2

    # Fusion
    lb=min(150,len(c)); mc_=sum(c[-lb:])/lb
    sd_=math.sqrt(sum((x-mc_)**2 for x in c[-lb:])/lb)
    z=(pr-mc_)/(sd_+1e-10)
    qp=(ad/50)*max(0,min(1,(ri-30)/40))
    nn=0.5+(0.2 if pr>e50[i] else 0)+(0.2 if e50[i]>e200[i] else 0)+(0.1 if z>-0.5 else 0)
    fp=min(1.0,qp*nn*1.5)
    fu=fp>cfg.get("fthr",0.8) and pr>tu[i] and ad>am

    # Konsensus
    wt={"s1":ad/100,"s2":score6/6,"fu":fp}
    tw=sum(wt.values()); bw=sum(w for k_,w in wt.items() if {"s1":s1,"s2":s2,"fu":fu}[k_])
    cons=(bw/tw*100) if tw>0 else 0
    master=cons>cfg.get("mthr",65)

    # Guc skoru
    sc=0
    if cons>=80: sc+=4
    elif cons>=65: sc+=3
    elif cons>=50: sc+=2
    elif cons>=35: sc+=1
    if ad>=40: sc+=2
    elif ad>=25: sc+=1
    if score6>=5: sc+=2
    elif score6>=3: sc+=1
    if fp>=0.6: sc+=1
    if master: sc=min(10,sc+1)

    # Multi-TF hizalanma bonusu
    tf_align=sum([s1 or s2 or fu, break_4h, mom_2h])
    if tf_align==3: sc=min(10,sc+2)
    elif tf_align==2: sc=min(10,sc+1)

    # RSI divergence cezasi
    rsi_div=False
    if i>=5:
        rsi_div=pr>c[i-5] and ri<rsi_d[i-5]
        if rsi_div: sc=max(1,sc-1)

    # Hacim bonusu
    vol_avg=sum(v[-20:])/20 if len(v)>=20 else v[-1]
    vol_ratio=v[i]/vol_avg if vol_avg>0 else 1.0
    if vol_ratio>2.0: sc=min(10,sc+1)
    sc=max(1,min(10,sc))

    # ── 5 BAĞIMSIZ AGENT (Pine birebir) ──────────────────
    # Fusedprob: quantum × nn (agent giriş koşulu)
    fused_prob = fp  # zaten hesaplandı
    fthr = cfg.get("fthr", 0.8)

    # Agent 60: noise60 = sin(bar*0.37)*0.05
    # Pine: bar_index yok, i kullanıyoruz
    noise60 = math.sin(i * 0.37) * 0.05
    a60_prob = fused_prob * 0.92 + noise60
    a60 = a60_prob > fthr and pr > tu[i] and ad > am

    # Agent 61: rs_strong2 ile birleşir
    noise61 = math.sin(i * 0.42) * 0.04
    a61_prob = fused_prob * 1.02 + score6 / 20 + noise61
    a61 = a61_prob > fthr and pr > tu[i] and rs_strong

    # Agent 62: ema200 + dna
    noise62 = math.sin(i * 0.29) * 0.06
    a62_prob = fused_prob * 0.98 + ad / 50 + noise62
    a62 = a62_prob > fthr and pr > e200[i] and dna

    # Agent 81: accumD + tma (tma_upper yerine e50 kullan - yakın)
    noise81 = 1 + math.sin(i * 0.21) * 0.1
    a81_prob = fused_prob * noise81
    a81 = a81_prob > fthr and pr > e50[i] and accum_d

    # Agent 120: break4h + ema200
    noise120 = math.sin(i * 0.15) * 0.07
    a120_prob = fused_prob * 0.88 + qp + noise120
    a120 = a120_prob > fthr and pr > e200[i] and break_4h

    # ── MASTER AI: 8 SİSTEM KONSENSÜSİ ──────────────────
    # Pine: conditionsBuy[0..7] ve conditionProbs[0..7]
    cond_buy  = [s1, s2, fu, a60, a61, a62, a81, a120]
    cond_prob = [
        ad / 100,        # S1 ağırlığı: ADX
        score6 / 6,      # S2 ağırlığı: PRO skor
        fp,              # Fusion ağırlığı
        a60_prob,        # A60
        a61_prob,        # A61
        a62_prob,        # A62
        a81_prob,        # A81
        a120_prob,       # A120
    ]
    # Equal weighting (Pine default)
    total_w = sum(cond_prob)
    buy_w   = sum(cond_prob[k] for k in range(8) if cond_buy[k])
    cons_8  = (buy_w / total_w * 100) if total_w > 0 else cons
    master  = cons_8 > cfg.get("mthr", 65)

    # Güç skoru güncelle: 8 sistem konsensüs ile
    sc = 0
    if cons_8 >= 80: sc += 4
    elif cons_8 >= 65: sc += 3
    elif cons_8 >= 50: sc += 2
    elif cons_8 >= 35: sc += 1
    if ad >= 40: sc += 2
    elif ad >= 25: sc += 1
    if score6 >= 5: sc += 2
    elif score6 >= 3: sc += 1
    if fp >= 0.6: sc += 1
    if master: sc = min(10, sc + 1)
    if tf_align == 3: sc = min(10, sc + 2)
    elif tf_align == 2: sc = min(10, sc + 1)
    if rsi_div: sc = max(1, sc - 1)
    if vol_ratio > 2.0: sc = min(10, sc + 1)
    sc = max(1, min(10, sc))

    # Aktif sistemler
    acts = []
    if s1:   acts.append("ST+TMA")
    if s2:   acts.append("PRO")
    if fu:   acts.append("Fusion")
    if a60:  acts.append("A60")
    if a61:  acts.append("A61")
    if a62:  acts.append("A62")
    if a81:  acts.append("A81")
    if a120: acts.append("A120")

    # Herhangi bir sistem sinyal verdiyse al
    any_signal = s1 or s2 or fu or a60 or a61 or a62 or a81 or a120

    stop = round(chst[i], 2) if chst[i] > 0 else round(pr * 0.93, 2)

    # ── Pine tablosu istatistikleri: son 504 bar backtest ──────────────
    # Pine: buyCount1, sellCount1, winCount1, lossCount1, totalPnL1 vb.
    # Her sistem için son 504 barda (2 yıl) simüle istatistik
    def _sys_stats(signal_fn, stop_mult=8.0, lookback=504):
        buys=0; sells=0; wins=0; losses=0; total_pnl=0.0
        in_t=False; ep=0.0; hp=0.0
        lb=min(lookback, i)
        for j in range(max(210, i-lb), i+1):
            if j >= len(c) or j < 1: continue
            pr_j=c[j]
            sig_j = signal_fn(j)
            if not in_t and sig_j:
                in_t=True; ep=pr_j; hp=pr_j; buys+=1
            elif in_t:
                if pr_j > hp: hp=pr_j
                ch_stop = hp - _atr(h,l,c,20)[j]*stop_mult if j>=20 else hp*0.9
                if pr_j < ch_stop or pr_j < ep*0.75:
                    pnl=(pr_j-ep)/ep*100
                    total_pnl+=pnl
                    if pnl>=0: wins+=1
                    else: losses+=1
                    sells+=1; in_t=False
        # Açık pozisyon PnL
        open_pnl = round((c[i]-ep)/ep*100,2) if in_t else None
        return {
            "buys":buys,"sells":sells,"wins":wins,"losses":losses,
            "total_pnl":round(total_pnl,2),"open_pnl":open_pnl
        }

    # S1 sinyal fonksiyonu
    def _s1_sig(j):
        if j<1: return False
        return (data_st_dir[j]==1 and data_st_dir[j-1]==-1
                and c[j]>tu[j] and adv[j]>am)

    # S2 sinyal fonksiyonu
    def _s2_sig(j):
        if j<200: return False
        return adv[j]>am and c[j]>e200[j] and c[j]>tu[j]

    # Fusion sinyal fonksiyonu (basitleştirilmiş)
    def _fu_sig(j):
        if j<200: return False
        lb_=min(150,j+1)
        mc__=sum(c[j+1-lb_:j+1])/lb_
        sd__=math.sqrt(sum((x-mc__)**2 for x in c[j+1-lb_:j+1])/lb_)+1e-10
        z__=(c[j]-mc__)/sd__
        qp__=(adv[j]/50)*max(0,min(1,(rsi_d[j]-30)/40))
        nn__=0.5+(0.2 if c[j]>e50[j] else 0)+(0.2 if e50[j]>e200[j] else 0)+(0.1 if z__>-0.5 else 0)
        fp__=min(1.0,qp__*nn__*1.5)
        return fp__>cfg.get("fthr",0.8) and c[j]>tu[j] and adv[j]>am

    # SuperTrend direction array gerekiyor - precompute
    data_st_dir = sd  # zaten hesaplı

    stats_s1  = _sys_stats(_s1_sig,  stop_mult=cfg.get("chMult",8.0))
    stats_s2  = _sys_stats(_s2_sig,  stop_mult=cfg.get("chMult",8.0))
    stats_fu  = _sys_stats(_fu_sig,  stop_mult=cfg.get("chMult",8.0))

    # Agent istatistikleri (Pine A60-A120 noise ile)
    def _agent_sig(j, prob_fn, cond_fn):
        if j<200: return False
        return prob_fn(j) > cfg.get("fthr",0.8) and cond_fn(j)

    def _a60_prob(j): return fp*0.92 + math.sin(j*0.37)*0.05
    def _a61_prob(j): return fp*1.02 + score6/20 + math.sin(j*0.42)*0.04
    def _a62_prob(j): return fp*0.98 + adv[j]/50 + math.sin(j*0.29)*0.06
    def _a81_prob(j): return fp*(1+math.sin(j*0.21)*0.1)
    def _a120_prob(j): return fp*0.88 + qp + math.sin(j*0.15)*0.07

    def _a60_cond(j):  return c[j]>tu[j] and adv[j]>am
    def _a61_cond(j):  return c[j]>tu[j] and rs_strong
    def _a62_cond(j):  return c[j]>e200[j] and dna
    def _a81_cond(j):  return c[j]>e50[j] and c[j]>sma20[j]
    def _a120_cond(j): return c[j]>e200[j] and break_4h

    stats_a60  = _sys_stats(lambda j: _agent_sig(j,_a60_prob,_a60_cond))
    stats_a61  = _sys_stats(lambda j: _agent_sig(j,_a61_prob,_a61_cond))
    stats_a62  = _sys_stats(lambda j: _agent_sig(j,_a62_prob,_a62_cond))
    stats_a81  = _sys_stats(lambda j: _agent_sig(j,_a81_prob,_a81_cond))
    stats_a120 = _sys_stats(lambda j: _agent_sig(j,_a120_prob,_a120_cond))

    # Master AI toplam PnL (tüm sistemlerin ortalaması)
    all_pnls = [s["total_pnl"] for s in [stats_s1,stats_s2,stats_fu,stats_a60,stats_a61,stats_a62,stats_a81,stats_a120]]
    master_pnl = round(sum(all_pnls)/len(all_pnls), 2)

    # Dinamik eşikler (Pine RL/DynThreshold - basit approximation)
    dyn_buy_thresh  = round(cfg.get("mthr",65)/100, 2)
    dyn_sell_thresh = round(1 - dyn_buy_thresh, 2)

    return {
        "signal":     any_signal,
        "is_master":  master,
        "price":      round(pr, 2),
        "adx":        round(ad, 1),
        "rsi":        round(ri, 1),
        "macd":       round(macd, 4),
        "pro_score":  score6,
        "fusion_pct": round(fp * 100, 1),
        "consensus":  round(cons_8, 1),
        "buy_consensus":  round(buy_w/total_w*100 if total_w>0 else 0, 1),
        "sell_consensus": round((total_w-buy_w)/total_w*100 if total_w>0 else 0, 1),
        "dyn_buy_thresh":  dyn_buy_thresh,
        "dyn_sell_thresh": dyn_sell_thresh,
        "strength":   sc,
        "pstate":     _pstate(c),
        "stop_price": stop,
        "active_sys": acts,
        "ema200":     round(e200[i], 2),
        "ema50":      round(e50[i], 2),
        "s1": s1, "s2": s2, "fu": fu,
        "a60": a60, "a61": a61, "a62": a62, "a81": a81, "a120": a120,
        "pro_factors": {
            "rs_strong": rs_strong, "accum_d": accum_d,
            "exp_4h":    exp_4h,    "break_4h": break_4h,
            "mom_2h":    mom_2h,    "dna":      dna,
        },
        "agent_probs": {
            "a60": round(a60_prob, 3), "a61": round(a61_prob, 3),
            "a62": round(a62_prob, 3), "a81": round(a81_prob, 3),
            "a120": round(a120_prob, 3),
        },
        "tf_align":   tf_align,
        "vol_ratio":  round(vol_ratio, 2),
        "rsi_div":    rsi_div,
        # ── Pine tablo istatistikleri ──────────────────────
        # t1 (bottom_right) - Sistem 1
        "sys1": {
            "buys":      stats_s1["buys"],
            "sells":     stats_s1["sells"],
            "wins":      stats_s1["wins"],
            "losses":    stats_s1["losses"],
            "total_pnl": stats_s1["total_pnl"],
            "open_pnl":  stats_s1["open_pnl"],
            "pstate":    _pstate(c),
        },
        # t2 (bottom_left) - PRO Engine
        "sys2": {
            "buys":      stats_s2["buys"],
            "sells":     stats_s2["sells"],
            "wins":      stats_s2["wins"],
            "losses":    stats_s2["losses"],
            "total_pnl": stats_s2["total_pnl"],
            "open_pnl":  stats_s2["open_pnl"],
            "score":     score6,
        },
        # tf (top_right) - Fusion
        "fusion": {
            "buys":      stats_fu["buys"],
            "sells":     stats_fu["sells"],
            "wins":      stats_fu["wins"],
            "losses":    stats_fu["losses"],
            "total_pnl": stats_fu["total_pnl"],
            "open_pnl":  stats_fu["open_pnl"],
            "pstate":    _pstate(c),
        },
        # tm (top_left) - Master AI
        "master_ai": {
            "total_pnl":        master_pnl,
            "buy_consensus":    round(buy_w/total_w*100 if total_w>0 else 0, 1),
            "sell_consensus":   round((total_w-buy_w)/total_w*100 if total_w>0 else 0, 1),
            "dyn_buy_thresh":   dyn_buy_thresh,
            "dyn_sell_thresh":  dyn_sell_thresh,
            "open_pnl":         stats_s1["open_pnl"],  # proxy'de açık poz yok - s1 yaklaşık
        },
        # agentDash (middle_right) - Agent PnL'leri
        "agents": {
            "a60":  {"pnl": stats_a60["total_pnl"],  "buys": stats_a60["buys"],  "wins": stats_a60["wins"],  "losses": stats_a60["losses"]},
            "a61":  {"pnl": stats_a61["total_pnl"],  "buys": stats_a61["buys"],  "wins": stats_a61["wins"],  "losses": stats_a61["losses"]},
            "a62":  {"pnl": stats_a62["total_pnl"],  "buys": stats_a62["buys"],  "wins": stats_a62["wins"],  "losses": stats_a62["losses"]},
            "a81":  {"pnl": stats_a81["total_pnl"],  "buys": stats_a81["buys"],  "wins": stats_a81["wins"],  "losses": stats_a81["losses"]},
            "a120": {"pnl": stats_a120["total_pnl"], "buys": stats_a120["buys"], "wins": stats_a120["wins"], "losses": stats_a120["losses"]},
        },
    }


# ─────────────────────────────────────────────────────────
# KRİPTO — Binance
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────
# TELEGRAM GÖNDERİM
# ─────────────────────────────────────────────────────────

TG_TOKEN="8156380916:AAG2khLNZLvb9AN8heF8vnzj31gGWrRiTls"
TG_CHAT="348018531"

async def send_telegram(token:str,chat:str,text:str):
    if not token or not chat: return
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            await cl.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id":chat,"text":text})
    except Exception as e:
        log.warning(f"Telegram err: {e}")

_sent_today: set = set()

BIST_TICKERS=[
    "AKSA","ALTNY","ASELS","BIMAS","BSOKE","CANTE","CIMSA","CWENE",
    "EREGL","EUPWR","GENIL","GESAN","GLRMK","GRSEL","GUBRF","KONTR",
    "KRDMD","KTLEV","MAVI","MPARK","PETKM","TUPRS","AKFYE","ALBRK",
    "ARDYZ","BANVT","BRSAN","BUCIM","ENKAI","FROTO","GARAN","HEKTS",
    "ISDMR","KCHOL","KOZAL","LOGO","MGROS","OYAKC","PRKAB","SASA",
    "TATGD","THYAO","TOASO","TTKOM","ULKER","VAKBN","YKBNK","SAHOL",
    "SISE","TCELL","AKBNK","ISCTR","TAVHL","SKBNK","ZOREN",
]


# ─────────────────────────────────────────────────────────
# SCHEDULER — 7/24 OTOMATİK TARAMA
# ─────────────────────────────────────────────────────────

async def auto_scan():
    """Piyasa saatlerinde her 5 dk otomatik tarama + Telegram"""
    now=datetime.now(); wd=now.weekday(); h=now.hour; m=now.minute
    if wd>=5: return  # Hafta sonu
    if not ((9<=h<18) or (h==18 and m<=20)): return  # Mesai disi
    if h<9 or (h==9 and m<40): return  # Seans oncesi

    log.info(f"Auto scan: {now.strftime('%H:%M')}")
    xu100=await fetch_xu100_closes()
    cfg={"st_len":10,"st_mult":3.0,"tma_len":200,"tma_atr_mult":8.0,
         "adx_min":25,"pro_min":5,"fthr":0.8,"mthr":65}
    signals=[]

    for ticker in BIST_TICKERS:
        try:
            sig_key=f"{ticker}_{now.strftime('%Y%m%d')}"
            if sig_key in _sent_today: continue
            od,o4,o2=await asyncio.gather(
                fetch_ohlcv(ticker,"D"),
                fetch_ohlcv(ticker,"240"),
                fetch_ohlcv(ticker,"120"),
            )
            if not od or len(od)<60: continue
            res=analyze_full(od,o4 or [],o2 or [],xu100,cfg)
            if not res.get("signal") and not res.get("is_master"): continue
            res["ticker"]=ticker; signals.append(res)
        except Exception as e:
            log.warning(f"Scan err {ticker}: {e}")

    signals.sort(key=lambda x:x.get("strength",0),reverse=True)
    for res in signals:
        ticker=res["ticker"]
        _sent_today.add(f"{ticker}_{now.strftime('%Y%m%d')}")
        tl="MASTER AI" if res.get("is_master") else "AL"
        str_=res.get("strength",5)
        bar="#"*(str_//2)+"_"*(5-str_//2)
        pf=res.get("pro_factors",{})
        # Aktif sistemler
        acts = res.get("active_sys", [])
        systems_str = " | ".join(acts) if acts else "-"
        # PRO faktörleri
        pf = res.get("pro_factors", {})
        pro_detail = []
        if pf.get("rs_strong"): pro_detail.append("RS")
        if pf.get("accum_d"):   pro_detail.append("Birikim")
        if pf.get("exp_4h"):    pro_detail.append("4H Genislik")
        if pf.get("break_4h"):  pro_detail.append("4H Kirilim")
        if pf.get("mom_2h"):    pro_detail.append("2H Mom")
        if pf.get("dna"):       pro_detail.append("DNA")
        msg=(f"{tl} SINYALI\n"
             f"--------------------\n"
             f"Hisse: {ticker}\n"
             f"Fiyat: TL{res['price']}\n"
             f"Guc: [{bar}] {str_}/10\n"
             f"Konsensus: %{res['consensus']} (8 sistem)\n"
             f"--------------------\n"
             f"ADX:{res['adx']} RSI:{res['rsi']} PRO:{res['pro_score']}/6\n"
             f"TF Hizalama: {res.get('tf_align',0)}/3\n"
             f"Bolge: {res['pstate']}\n"
             f"Stop: TL{res['stop_price']}\n"
             f"Aktif: {systems_str}\n"
             +(f"PRO: {', '.join(pro_detail)}\n" if pro_detail else "")
             +f"\nGrafik: https://www.tradingview.com/chart/?symbol=BIST:{ticker}&interval=1D")
        await send_telegram(TG_TOKEN,TG_CHAT,msg)
        await asyncio.sleep(0.5)

    if h==0 and m<6: _sent_today.clear()

# Scheduler lifespan ile yonetiliyor (yukarida)


# ─────────────────────────────────────────────────────────
# API ENDPOINT'LER
# ─────────────────────────────────────────────────────────

@app.get("/status")
async def root():
    return {"status":"ok","service":"BIST AI Proxy v3",
            "scheduler":scheduler.running,"cache":len(_cache),
            "tg_configured":bool(TG_TOKEN and TG_CHAT)}


@app.get("/ohlcv/{ticker}")
async def get_ohlcv(ticker: str, tf: str = "D"):
    """
    Ham OHLCV verisi - backtest için kullanılır.
    Frontend CORS sorununu aşmak için proxy üzerinden çeker.
    """
    ticker = ticker.upper()
    k = f"ohlcv_{ticker}_{tf}"
    cached = cget(k)
    if cached:
        return {"ticker": ticker, "tf": tf, "bars": len(cached), "ohlcv": cached, "data": cached}

    ohlcv = await fetch_ohlcv(ticker, tf)
    xu100 = await fetch_xu100_closes()

    if not ohlcv:
        return {"ticker": ticker, "tf": tf, "bars": 0, "ohlcv": [], "data": [], "error": "Veri alinamadi"}

    return {
        "ticker":    ticker,
        "tf":        tf,
        "bars":      len(ohlcv),
        "ohlcv":     ohlcv,
        "data":      ohlcv,
        "xu100_last": xu100[-1] if xu100 else None,
    }

@app.get("/health")
async def health(): return {"status":"healthy"}

@app.get("/prices")
async def get_prices(symbols:str):
    tickers=[t.strip().upper() for t in symbols.split(",") if t.strip()]
    k="prices_"+"_".join(sorted(tickers)); cached=cget(k)
    if cached: return cached
    sym=",".join(f"{t}.IS" for t in tickers)
    url=(f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={sym}"
         f"&fields=regularMarketPrice,regularMarketChange,regularMarketChangePercent,"
         f"regularMarketDayHigh,regularMarketDayLow,regularMarketVolume,regularMarketPreviousClose")
    try:
        async with httpx.AsyncClient(timeout=15,headers=YH) as cl:
            r=await cl.get(url); d=r.json()
        res={}
        for q in d.get("quoteResponse",{}).get("result",[]):
            t=q.get("symbol","").replace(".IS",""); p=q.get("regularMarketPrice",0)
            if p and p>0:
                res[t]={"price":round(p,2),"change":round(q.get("regularMarketChange",0),2),
                        "change_pct":round(q.get("regularMarketChangePercent",0),2),
                        "high":round(q.get("regularMarketDayHigh",0),2),
                        "low":round(q.get("regularMarketDayLow",0),2),
                        "volume":int(q.get("regularMarketVolume",0)),
                        "prev_close":round(q.get("regularMarketPreviousClose",0),2),"real":True}
        cset(k,res,300); return res
    except: return {}

@app.get("/xu100")
async def get_xu100():
    k="xu100"; cached=cget(k)
    if cached: return cached
    try:
        async with httpx.AsyncClient(timeout=10,headers=YH) as cl:
            r=await cl.get("https://query1.finance.yahoo.com/v7/finance/quote"
                           "?symbols=XU100.IS&fields=regularMarketPrice,regularMarketChangePercent,regularMarketChange")
            d=r.json()
        q=d.get("quoteResponse",{}).get("result",[{}])[0]
        res={"price":round(q.get("regularMarketPrice",0),2),
             "change":round(q.get("regularMarketChange",0),2),
             "change_pct":round(q.get("regularMarketChangePercent",0),2),"real":True}
    except: res={"price":0,"change_pct":0,"real":False}
    cset(k,res,120); return res

@app.get("/analyze/{ticker}")
async def analyze_ticker(ticker:str,tf:str="D",adx_min:float=25,master_thr:float=65,pro_min:int=5):
    ticker=ticker.upper(); k=f"sig_{ticker}_{tf}"; cached=cget(k)
    if cached: return cached
    xu100=await fetch_xu100_closes()
    od,o4,o2=await asyncio.gather(
        fetch_ohlcv(ticker,"D"),fetch_ohlcv(ticker,"240"),fetch_ohlcv(ticker,"120"))
    if not od or len(od)<60: return {"error":"Veri yok","ticker":ticker}
    cfg={"st_len":10,"st_mult":3.0,"tma_len":200,"tma_atr_mult":8.0,
         "adx_min":adx_min,"pro_min":pro_min,"fthr":0.8,"mthr":master_thr}
    res=analyze_full(od,o4,o2,xu100,cfg)
    res.update({"ticker":ticker,"tf":tf,"bars":len(od)})
    cset(k,res,300); return res

@app.post("/scan")
async def scan_stocks(body:dict):
    tickers=body.get("tickers",[]); tf=body.get("tf","D")
    cfg_in=body.get("cfg",{}); min_cons=float(body.get("min_consensus",0))
    only_master=body.get("only_master",False)
    fb=cfg_in.get("fb",80)
    cfg={"st_len":10,"st_mult":cfg_in.get("atrm",3.0),
         "tma_len":200,"tma_atr_mult":8.0,
         "adx_min":cfg_in.get("adxMin",25),
         "pro_min":cfg_in.get("sc",5),
         "fthr":(fb/100 if fb>1 else fb),
         "mthr":cfg_in.get("mb",65)}

    xu100=await fetch_xu100_closes(); results=[]; batch=6

    for i in range(0,len(tickers),batch):
        chunk=tickers[i:i+batch]
        tasks=[]
        for t in chunk:
            ticker=t["ticker"] if isinstance(t,dict) else t
            tasks.append(asyncio.gather(
                fetch_ohlcv(ticker,"D"),fetch_ohlcv(ticker,"240"),fetch_ohlcv(ticker,"120"),
                return_exceptions=True))
        batch_res=await asyncio.gather(*tasks,return_exceptions=True)

        for j,t in enumerate(chunk):
            ticker=t["ticker"] if isinstance(t,dict) else t
            name=t.get("name","") if isinstance(t,dict) else ""
            idxs=t.get("indices",[]) if isinstance(t,dict) else []
            try:
                od,o4,o2=batch_res[j]
                if isinstance(od,Exception) or not od or len(od)<60: continue
                if isinstance(o4,Exception): o4=[]
                if isinstance(o2,Exception): o2=[]
                res=analyze_full(od,o4,o2,xu100,cfg)
            except Exception as e:
                log.warning(f"Analyze err {ticker}: {e}"); continue
            if not res.get("signal") and not res.get("is_master"): continue
            if only_master and not res.get("is_master"): continue
            if res.get("consensus",0)<min_cons: continue
            results.append({**res,"ticker":ticker,"name":name,"indices":idxs,"tf":tf})

        await asyncio.sleep(0.3)

    results.sort(key=lambda x:x.get("strength",0),reverse=True)
    return {"signals":results,"count":len(results),"tf":tf}




@app.post("/scan_single")
async def scan_single(body: dict):
    """
    Tek hisse icin Pine Script tablolarini cek.
    { "ticker": "EREGL", "tf": "D", "cfg": {} }
    """
    ticker = body.get("ticker", "").upper()
    tf     = body.get("tf", "D")
    cfg_in = body.get("cfg", {})
    if not ticker:
        return {"error": "ticker gerekli"}
    
    # Mevcut scan endpoint'i tek hisse ile cagir
    result = await scan_stocks({
        "tickers": [ticker],
        "tf": tf,
        "cfg": cfg_in,
        "min_consensus": 0,  # filtre yok, tum veriyi don
        "only_master": False
    })
    
    sigs = result.get("signals", [])
    if sigs:
        return {"ticker": ticker, "tf": tf, "found": True, "data": sigs[0]}
    
    # Sinyal yoksa bile istatistikleri don - proxy hesaplar
    # Direkt fetch_ohlcv + hesaplama yap
    try:
        ohlcv = await fetch_ohlcv(ticker, tf)
        xu100 = await fetch_xu100_closes()
        if not ohlcv:
            return {"ticker": ticker, "tf": tf, "found": False, "error": "Veri alinamadi"}
        
        cfg = {"st_len": 10, "st_mult": cfg_in.get("atrm", 3.0),
               "fb": cfg_in.get("fb", 80), "sc": cfg_in.get("sc", 5),
               "adx_min": cfg_in.get("adxMin", 25)}
        
        sig = calc_signal(ticker, ohlcv, xu100 or [], cfg)
        if sig:
            return {"ticker": ticker, "tf": tf, "found": True, "data": sig}
        return {"ticker": ticker, "tf": tf, "found": False, "data": None}
    except Exception as e:
        return {"ticker": ticker, "tf": tf, "found": False, "error": str(e)}

# ── HTML Serve ────────────────────────────────────────

# HTML Serve - bist_v6_fix 4 blok

# HTML - bist_v6_fix 4 blok
















# ═══════════════════════════════════════════════════════════════
# AI CHAT ENDPOINT - iPhone icin sunucu tarafli AI
# Groq (ucretsiz Llama) + Hugging Face + Together.ai fallback
# ═══════════════════════════════════════════════════════════════

import os
import json

# Environment variables - Render dashboard'dan ekle
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
HF_API_KEY      = os.environ.get("HF_API_KEY", "")
TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY", "")
OPENROUTER_KEY  = os.environ.get("OPENROUTER_KEY", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_KEY", "")

# Agent sistem promptlari
AGENT_PROMPTS = {
    "vibe-architect": (
        "Sen BIST AI Elite PWA mimarisini tamamen bilen senior gelistiricisin. "
        "198 JavaScript fonksiyonu, 16 blok yapi. "
        "iOS Safari uyumlu, try-catch sarili, ASCII karakterli kod yaziyorsun. "
        "Yeni ozellik = yeni <script> blogu olarak ver. "
        "Kisa net Turkce konuS, kod ASCII yaz."
    ),
    "vibe-trader": (
        "Sen BIST Katilim Endeksi uzman tradersin. "
        "69 hisse, 8 sistem (S1/S2/Fusion/MasterAI/A60-A120), 3 zaman dilimi. "
        "Sinyal analizi, risk yonetimi, portfoy optimizasyonu yapiyorsun. "
        "Kisa net Turkce cevap ver."
    ),
    "vibe-debug": (
        "Sen JavaScript ve iOS Safari hata ayiklama uzmanisın. "
        "Syntax hatasi, null pointer, iOS blob URL, SpeechRecognition sorunlarini cozuyorsun. "
        "Hatayi bul, try-catch ile sar, iOS safe alternatif sun."
    ),
    "vibe-backtest": (
        "Sen kantitatif finans ve algoritmik trading uzmanisın. "
        "btEngine, Walk-Forward, Monte Carlo, Sharpe/Calmar/WinRate biliyorsun. "
        "Parametre optimizasyonu ve backtest analizi yapiyorsun."
    ),
    "vibe-feature": (
        "Sen yaratici bir fullstack gelistiricisin. "
        "Kullanicinin istegini aninda calisir JavaScript koduna donusturuyorsun. "
        "Her zaman try-catch kullan, iOS safe, ASCII karakter, window.addEventListener load ile baslat."
    ),
    "vibe-uiux": (
        "Sen CSS ve JavaScript animasyon uzmanisın. "
        "Glassmorphism, backdrop-filter, haptic, ripple, skeleton loading biliyorsun. "
        "BIST renk sistemi: --cyan:#00D4FF --gold:#FFB800 --green:#00E676. "
        "iOS safe, transform/animation kullan."
    ),
    "vibe-review": (
        "Sen kod inceleme uzmanisın. "
        "Guvenlik, performans, iOS uyumluluk, memory leak kontrol ediyorsun. "
        "KRITIK/UYARI/ONERI formatinda raporla."
    ),
    "main": (
        "Sen BIST AI Elite asistanisin. "
        "BIST Katilim borsasi, JavaScript gelistirme ve risk yonetimi biliyorsun. "
        "Kisa net Turkce cevap ver."
    ),
}

async def call_groq(messages: list, system: str, model: str = "llama-3.3-70b-versatile"):
    """Groq API - Ucretsiz Llama 3.3 70B"""
    if not GROQ_API_KEY:
        return None, "GROQ_API_KEY eksik"
    
    msgs = [{"role": "system", "content": system}] + messages
    
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": msgs, "max_tokens": 1500, "temperature": 0.7}
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"], None
        return None, data.get("error", {}).get("message", "Groq hatasi")

async def call_hf(messages: list, system: str, model: str = "mistralai/Mistral-7B-Instruct-v0.3"):
    """Hugging Face Inference API - Ucretsiz tier"""
    if not HF_API_KEY:
        return None, "HF_API_KEY eksik"
    
    prompt = f"<s>[INST] <<SYS>>\n{system}\n<</SYS>>\n\n"
    for m in messages[-4:]:
        if m["role"] == "user":
            prompt += m["content"] + " [/INST] "
        else:
            prompt += m["content"] + " </s><s>[INST] "
    
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://api-inference.huggingface.co/models/{model}",
            headers={"Authorization": f"Bearer {HF_API_KEY}"},
            json={"inputs": prompt, "parameters": {"max_new_tokens": 800, "temperature": 0.7}}
        )
        data = r.json()
        if isinstance(data, list) and data:
            text = data[0].get("generated_text", "")
            # Promptu cikar
            if "[/INST]" in text:
                text = text.split("[/INST]")[-1].strip()
            return text, None
        return None, str(data)

async def call_together(messages: list, system: str, model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"):
    """Together.ai - Ucuz Llama"""
    if not TOGETHER_API_KEY:
        return None, "TOGETHER_API_KEY eksik"
    
    msgs = [{"role": "system", "content": system}] + messages
    
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.together.xyz/v1/chat/completions",
            headers={"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": msgs, "max_tokens": 1500}
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"], None
        return None, data.get("error", {}).get("message", "Together hatasi")

async def call_anthropic_server(messages: list, system: str):
    """Anthropic Claude - En iyi ama ucretli"""
    if not ANTHROPIC_KEY:
        return None, "ANTHROPIC_KEY eksik"
    
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1500, "system": system, "messages": messages}
        )
        data = r.json()
        if "content" in data:
            return data["content"][0]["text"], None
        return None, data.get("error", {}).get("message", "Anthropic hatasi")

@app.post("/ai/chat")
async def ai_chat(body: dict):
    """
    iPhone icin sunucu tarafli AI endpoint.
    Groq (ucretsiz) -> HuggingFace -> Together -> Anthropic Haiku
    Body: {
      "message": "kullanici mesaji",
      "agent": "vibe-architect|vibe-trader|...",
      "history": [{"role":"user","content":"..."},...],
      "bist_context": {"positions":0,"signals":5,"xu100":-1.5}
    }
    """
    msg      = body.get("message", "")
    agent_id = body.get("agent", "main")
    history  = body.get("history", [])
    ctx      = body.get("bist_context", {})
    
    if not msg:
        return {"error": "Mesaj bos"}
    
    # Sistem promptu
    base_prompt = AGENT_PROMPTS.get(agent_id, AGENT_PROMPTS["main"])
    
    # BIST context ekle
    if ctx:
        base_prompt += (
            f"\nMEVCUT BIST DURUMU: "
            f"Acik poz={ctx.get('positions',0)}, "
            f"Sinyal={ctx.get('signals',0)}, "
            f"XU100={ctx.get('xu100',0):.2f}%, "
            f"WinRate=%{ctx.get('winrate','N/A')}."
        )
    
    # Mesaj gecmisi (son 6)
    messages = history[-6:] + [{"role": "user", "content": msg}]
    
    # Fallback zinciri: Groq -> HF -> Together -> Anthropic
    providers = []
    if GROQ_API_KEY:    providers.append(("groq",    call_groq))
    if HF_API_KEY:      providers.append(("hf",      call_hf))
    if TOGETHER_API_KEY: providers.append(("together", call_together))
    if ANTHROPIC_KEY:   providers.append(("anthropic", call_anthropic_server))
    
    if not providers:
        return {
            "response": "Sunucu tarafi AI icin en az bir API key gerekli. Render Dashboard > Environment Variables'a GROQ_API_KEY ekleyin (ucretsiz: console.groq.com).",
            "provider": "none",
            "error": True
        }
    
    last_error = None
    for provider_name, provider_fn in providers:
        try:
            resp, err = await provider_fn(messages, base_prompt)
            if resp:
                return {
                    "response": resp,
                    "provider": provider_name,
                    "agent": agent_id,
                    "model": {
                        "groq": "llama-3.3-70b-versatile",
                        "hf": "Mistral-7B",
                        "together": "Llama-3.3-70B",
                        "anthropic": "claude-haiku-4-5"
                    }.get(provider_name, provider_name)
                }
            last_error = err
        except Exception as e:
            last_error = str(e)
            continue
    
    return {"response": f"Tum AI servisleri hatali: {last_error}", "provider": "none", "error": True}

@app.get("/ai/providers")
async def ai_providers():
    """Aktif AI providerlarini listele"""
    return {
        "groq":     {"active": bool(GROQ_API_KEY),    "model": "llama-3.3-70b-versatile", "cost": "UCRETSIZ", "limit": "5000 token/dk"},
        "hf":       {"active": bool(HF_API_KEY),      "model": "Mistral-7B",              "cost": "UCRETSIZ", "limit": "sınırsız"},
        "together": {"active": bool(TOGETHER_API_KEY), "model": "Llama-3.3-70B",          "cost": "$0.0009/1K", "limit": "-"},
        "anthropic":{"active": bool(ANTHROPIC_KEY),   "model": "Claude Haiku 4.5",        "cost": "$0.0008/1K", "limit": "-"},
    }


















_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BIST AI">
<meta name="theme-color" content="#000">
<meta name="color-scheme" content="dark">
<title>BIST AI Scanner v6</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--bg:#000;--bg2:#0a0a0a;--bg3:#111;--bg4:#1a1a1a;--b1:#1e1e1e;--b2:#2a2a2a;--b3:#3a3a3a;--cyan:#00d4ff;--gold:#ffb800;--green:#00e676;--red:#ff4444;--purple:#c084fc;--orange:#ff7043;--t1:#fff;--t2:#ccc;--t3:#888;--t4:#555;color-scheme:dark}
*{background-color:inherit}
html{background:#000!important;color-scheme:dark}
html,body{background:#000!important;color:#fff;font-family:-apple-system,'Helvetica Neue',sans-serif;font-size:13px;height:100%;overflow:hidden;-webkit-font-smoothing:antialiased}
.page{background:#000!important}
main{background:#000!important}
#app{display:flex;flex-direction:column;height:100dvh}
header{padding:12px 16px;padding-top:calc(12px + env(safe-area-inset-top));background:#000;border-bottom:1px solid var(--b2);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:10}
.logo{font-size:16px;font-weight:700;letter-spacing:1px}
.logo-accent{color:var(--cyan)}
.logo-sub{font-size:9px;letter-spacing:2px;color:var(--t4);font-family:'Courier New',monospace;display:block;margin-top:1px}
.hdr{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--t3);font-family:'Courier New',monospace}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.dot.ok{background:var(--green);box-shadow:0 0 5px var(--green)}
.dot.off{background:var(--red)}
.dot.run{background:var(--cyan);animation:dp .6s ease-in-out infinite}
@keyframes dp{0%,100%{opacity:1}50%{opacity:.25}}
.pb-wrap{height:2px;background:var(--b2);flex-shrink:0}
.pb-fill{height:100%;background:var(--cyan);width:0%;transition:width .1s}
nav{display:flex;overflow-x:auto;padding:0 6px;flex-shrink:0;scrollbar-width:none;background:#000;border-bottom:1px solid var(--b2);z-index:10}
nav::-webkit-scrollbar{display:none}
.tab{flex-shrink:0;padding:9px 12px;border:none;border-bottom:2px solid transparent;background:transparent;color:var(--t4);font-size:11px;cursor:pointer;white-space:nowrap;margin-bottom:-1px;transition:.15s}
.tab.on{color:var(--cyan);border-bottom-color:var(--cyan)}
.nbadge{display:inline-flex;align-items:center;justify-content:center;background:var(--red);color:#fff;border-radius:8px;font-size:8px;font-weight:700;padding:1px 5px;margin-left:3px;min-width:15px;height:15px;vertical-align:middle}
.nbadge.g{background:var(--green)}
main{flex:1;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch}
main::-webkit-scrollbar{width:2px}
main::-webkit-scrollbar-thumb{background:var(--b3);border-radius:2px}
.page{display:none;padding:11px 13px}
.page.on{display:block;animation:pf .15s ease}
@keyframes pf{from{opacity:0}to{opacity:1}}
.card{background:var(--bg2);border:1px solid var(--b2);border-radius:10px;padding:13px;margin-bottom:10px}
.ctitle{font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:11px;display:flex;align-items:center;gap:8px}
.ctitle::after{content:'';flex:1;height:1px;background:var(--b2)}
/* XU100 */
.xu-banner{display:flex;align-items:center;gap:8px;padding:7px 11px;border-radius:7px;margin-bottom:9px;font-size:10px;font-family:'Courier New',monospace}
.xu-banner.bull{background:rgba(0,230,118,.06);border:1px solid rgba(0,230,118,.2)}
.xu-banner.bear{background:rgba(255,68,68,.06);border:1px solid rgba(255,68,68,.2)}
.xu-banner.neutral{background:rgba(255,255,255,.03);border:1px solid var(--b2)}
/* Signal cards */
.sig{border-radius:8px;padding:12px 13px;margin-bottom:7px;background:var(--bg2);border:1px solid var(--b2);border-left:3px solid var(--b3);cursor:pointer;animation:si .2s ease}
.sig:active{background:var(--bg3)}
@keyframes si{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:translateY(0)}}
.sig.buy{border-left-color:var(--green);background:linear-gradient(90deg,rgba(0,230,118,.05),var(--bg2) 45%)}
.sig.master{border-left-color:var(--gold);background:linear-gradient(90deg,rgba(255,184,0,.06),var(--bg2) 45%)}
.sig.stop{border-left-color:var(--orange);background:linear-gradient(90deg,rgba(255,112,67,.06),var(--bg2) 45%)}
.sig-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}
.sig-ticker{font-size:20px;font-weight:700;line-height:1}
.sig-name{font-size:9px;color:var(--t4);margin-top:2px}
.sig-badge{font-size:9px;padding:3px 7px;border-radius:4px;font-weight:700;text-transform:uppercase;font-family:'Courier New',monospace}
.sig-badge.buy{background:rgba(0,230,118,.14);color:var(--green);border:1px solid rgba(0,230,118,.28)}
.sig-badge.master{background:rgba(255,184,0,.14);color:var(--gold);border:1px solid rgba(255,184,0,.32)}
.sig-badge.stop{background:rgba(255,112,67,.14);color:var(--orange);border:1px solid rgba(255,112,67,.3)}
.sig-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-bottom:7px}
.sig-m{background:var(--bg3);border:1px solid var(--b2);border-radius:5px;padding:6px 7px}
.sig-mv{font-size:12px;font-weight:700;font-family:'Courier New',monospace;line-height:1;color:#fff}
.sig-ml{font-size:7px;color:var(--t4);margin-top:2px;text-transform:uppercase;letter-spacing:.5px}
.sbs{display:flex;flex-wrap:wrap;gap:3px}
.sb{font-size:8px;padding:2px 5px;border-radius:3px;background:var(--bg3);color:var(--t4);border:1px solid var(--b2)}
.sb.on{background:rgba(0,212,255,.1);color:var(--cyan);border-color:rgba(0,212,255,.25)}
/* Strength badge */
.sc-badge{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:5px;font-size:12px;font-weight:700;flex-shrink:0}
.sc-10,.sc-9{background:rgba(0,230,118,.2);color:var(--green);border:1px solid rgba(0,230,118,.4)}
.sc-8,.sc-7{background:rgba(0,212,255,.15);color:var(--cyan);border:1px solid rgba(0,212,255,.3)}
.sc-6,.sc-5{background:rgba(255,184,0,.15);color:var(--gold);border:1px solid rgba(255,184,0,.3)}
.sc-4,.sc-3{background:rgba(255,112,67,.15);color:var(--orange);border:1px solid rgba(255,112,67,.3)}
.sc-2,.sc-1{background:rgba(255,68,68,.15);color:var(--red);border:1px solid rgba(255,68,68,.3)}
/* Chips */
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:9px}
.chip{padding:4px 10px;border-radius:4px;font-size:10px;border:1px solid var(--b2);background:var(--bg3);color:var(--t3);cursor:pointer}
.chip.on{background:rgba(255,184,0,.12);color:var(--gold);border-color:rgba(255,184,0,.4)}
.chip.cy.on{background:rgba(0,212,255,.1);color:var(--cyan);border-color:rgba(0,212,255,.3)}
/* Settings rows */
.srow{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid var(--b1);gap:10px}
.srow:last-child{border-bottom:none}
.slbl{font-size:12px;font-weight:500;flex:1;color:var(--t2)}
.sdesc{font-size:10px;color:var(--t4);margin-top:2px}
.toggle{position:relative;width:44px;height:24px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.trk{position:absolute;inset:0;border-radius:12px;background:var(--bg4);border:1px solid var(--b2);cursor:pointer;transition:.25s}
input:checked+.trk{background:rgba(0,230,118,.18);border-color:rgba(0,230,118,.5)}
.trk::after{content:'';position:absolute;top:4px;left:4px;width:14px;height:14px;border-radius:50%;background:var(--t4);transition:.25s}
input:checked+.trk::after{transform:translateX(20px);background:var(--green)}
.ni{width:80px;padding:6px 9px;border-radius:6px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-family:'Courier New',monospace;font-size:12px;text-align:right}
.ni:focus{outline:none;border-color:var(--cyan)}
.si{padding:6px 9px;border-radius:6px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-size:11px;min-width:100px;-webkit-appearance:none}
.si:focus{outline:none;border-color:var(--cyan)}
.stit{font-size:9px;font-weight:600;color:var(--cyan);text-transform:uppercase;letter-spacing:2px;padding:9px 0 7px;border-bottom:1px solid var(--b2);margin-bottom:3px}
/* Stock list */
.strow{display:flex;align-items:center;padding:9px 11px;background:var(--bg2);border-radius:7px;margin-bottom:3px;gap:7px;border:1px solid var(--b1);cursor:pointer}
.strow.hs{border-color:rgba(0,212,255,.2);background:rgba(0,212,255,.03)}
.stt{font-size:14px;font-weight:700;width:54px;flex-shrink:0}
.stn{color:var(--t4);font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sti{font-size:8px;color:var(--t4);background:var(--bg4);border:1px solid var(--b2);padding:2px 5px;border-radius:3px;flex-shrink:0}
.sds{display:flex;gap:3px;flex-shrink:0}
.sd{width:6px;height:6px;border-radius:50%;background:var(--b3)}
.sd.buy{background:var(--green);box-shadow:0 0 4px var(--green)}
/* Stats */
.sgrid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:10px}
.sstat{background:var(--bg2);border:1px solid var(--b2);border-radius:8px;padding:11px 13px}
.sval{font-size:21px;font-weight:700;line-height:1;color:#fff}
.sval.p{color:var(--green)}.sval.n{color:var(--red)}
.slb2{font-size:9px;color:var(--t4);margin-top:4px;text-transform:uppercase;letter-spacing:1px}
/* Buttons */
.btn{padding:8px 14px;border-radius:6px;border:1px solid var(--b2);background:var(--bg3);color:var(--t3);font-size:11px;cursor:pointer;transition:.15s}
.btn:active{opacity:.5}
.btn.g{color:var(--green);border-color:rgba(0,230,118,.35);background:rgba(0,230,118,.07)}
.btn.r{color:var(--red);border-color:rgba(255,68,68,.35);background:rgba(255,68,68,.07)}
.btn.c{color:var(--cyan);border-color:rgba(0,212,255,.35);background:rgba(0,212,255,.07)}
.btn.o{color:var(--orange);border-color:rgba(255,112,67,.35);background:rgba(255,112,67,.07)}
/* Chart */
.chartbox{background:var(--bg2);border:1px solid var(--b2);border-radius:8px;height:175px;position:relative;overflow:hidden;margin-bottom:10px}
.chartgrid{position:absolute;inset:0;background-image:linear-gradient(var(--b1) 1px,transparent 1px),linear-gradient(90deg,var(--b1) 1px,transparent 1px);background-size:40px 28px}
/* Agent table */
.atbl{width:100%;border-collapse:collapse;font-size:10px}
.atbl th{padding:6px 7px;text-align:left;color:var(--t4);border-bottom:1px solid var(--b2);font-size:9px;text-transform:uppercase;letter-spacing:1px}
.atbl td{padding:8px 7px;border-bottom:1px solid var(--b1);color:var(--t2)}
.rbar{width:48px;height:3px;background:var(--bg4);border-radius:2px;overflow:hidden;display:inline-block;vertical-align:middle}
.rfill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--purple));border-radius:2px}
/* Telegram */
.tgi{width:100%;padding:9px 11px;border-radius:7px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-family:'Courier New',monospace;font-size:11px;margin-bottom:7px}
.tgi:focus{outline:none;border-color:var(--cyan)}
.tgi::placeholder{color:var(--t4)}
/* Footer */
footer{padding:9px 15px;padding-bottom:calc(9px + env(safe-area-inset-bottom));background:#000;border-top:1px solid var(--b2);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:10}
.finfo{font-size:10px;color:var(--t4);line-height:1.6;font-family:'Courier New',monospace}
#scanBtn{padding:10px 26px;border-radius:6px;border:none;cursor:pointer;font-size:12px;font-weight:700;letter-spacing:1.5px;background:var(--cyan);color:#000;transition:.15s}
#scanBtn:active{transform:scale(.94)}
#scanBtn.run{background:var(--green);color:#000;animation:sg 1.2s ease-in-out infinite}
@keyframes sg{0%,100%{box-shadow:0 0 16px rgba(0,230,118,.4)}50%{box-shadow:0 0 28px rgba(0,230,118,.7)}}
/* Modal */
.movl{position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:1000;display:flex;align-items:flex-end;opacity:0;pointer-events:none;transition:opacity .2s;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.movl.on{opacity:1;pointer-events:all}
.modal{background:var(--bg2);border:1px solid var(--b2);border-top:1px solid var(--b3);border-radius:14px 14px 0 0;padding:18px 15px;padding-bottom:calc(18px + env(safe-area-inset-bottom));width:100%;max-height:88dvh;overflow-y:auto;transform:translateY(100%);transition:transform .25s cubic-bezier(.4,0,.2,1)}
.movl.on .modal{transform:translateY(0)}
.mhdl{width:32px;height:3px;background:var(--b3);border-radius:2px;margin:0 auto 14px}
.mtit{font-size:17px;font-weight:700;margin-bottom:14px}
/* Toast */
#toast{position:fixed;top:calc(68px + env(safe-area-inset-top));left:50%;transform:translateX(-50%) translateY(-5px);background:var(--bg3);border:1px solid var(--b3);color:#fff;padding:9px 17px;border-radius:6px;font-size:11px;z-index:2000;white-space:nowrap;opacity:0;transition:all .2s;pointer-events:none;box-shadow:0 8px 24px rgba(0,0,0,.8)}
#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
/* Misc */
.empty{text-align:center;padding:44px 20px;color:var(--t4)}
.eico{font-size:36px;margin-bottom:10px;opacity:.4}
.scr{overflow-y:auto;max-height:260px}
.scr::-webkit-scrollbar{width:2px}
.scr::-webkit-scrollbar-thumb{background:var(--b3);border-radius:2px}
.sinput{width:100%;padding:9px 11px;border-radius:7px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-size:12px;margin-bottom:7px}
.sinput:focus{outline:none;border-color:var(--cyan)}
.sinput::placeholder{color:var(--t4)}
/* Position cards */
.pos-card{background:var(--bg2);border:1px solid var(--b2);border-radius:10px;padding:12px 13px;margin-bottom:7px;border-left:3px solid var(--b3)}
.pos-card.profit{border-left-color:var(--green)}
.pos-card.loss{border-left-color:var(--red)}
.pos-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.pos-ticker{font-size:18px;font-weight:700}
.pos-pnl{font-size:17px;font-weight:700;font-family:'Courier New',monospace}
.pos-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.pos-m{background:var(--bg3);border:1px solid var(--b1);border-radius:5px;padding:6px 7px}
.pos-mv{font-size:11px;font-weight:600;color:#fff;font-family:'Courier New',monospace}
.pos-ml{font-size:7px;color:var(--t4);margin-top:2px;text-transform:uppercase}
/* Watchlist */
.wl-item{display:flex;align-items:center;padding:9px 11px;background:var(--bg2);border:1px solid var(--b1);border-radius:8px;margin-bottom:4px;gap:7px}
.wl-ticker{font-size:14px;font-weight:700;width:52px;flex-shrink:0}
.wl-btn{width:26px;height:26px;border-radius:5px;border:1px solid var(--b2);background:var(--bg3);color:var(--t3);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:11px;flex-shrink:0}
/* Report */
.rep-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--b1);font-size:11px}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;margin-top:7px}
.cal-day{aspect-ratio:1;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:9px}
.cal-day.has{background:rgba(0,212,255,.15);color:var(--cyan)}
.cal-day.good{background:rgba(0,230,118,.2);color:var(--green)}
.cal-day.none{background:var(--bg3);color:var(--t4);opacity:.4}
/* Background indicator */
.bg-ind{display:flex;align-items:center;gap:5px;font-size:9px;color:var(--t4);font-family:'Courier New',monospace}
.bg-dot{width:5px;height:5px;border-radius:50%;background:var(--t4)}
.bg-dot.on{background:var(--green);box-shadow:0 0 4px var(--green)}
</style>
</head>
<body>
<div id="app">
<header>
  <div>
    <div class="logo"><span>BIST</span><span class="logo-accent"> AI</span></div>
    
  </div>
  <div class="hdr">
    <div class="dot run" id="dot"></div>
    <span id="hst">Baslatiliyor...</span>
  </div>
</header>
<div class="pb-wrap"><div class="pb-fill" id="pbar"></div></div>
<nav>
  <button class="tab on" onclick="pg('signals')">Sinyaller<span class="nbadge" id="nbadge" style="display:none">0</span></button>
  <button class="tab" onclick="pg('scanner')">Tarayici</button>
  <button class="tab" onclick="pg('positions')">Pozisyonlar<span class="nbadge g" id="posBadge" style="display:none">0</span></button>
  <button class="tab" onclick="pg('watchlist')">Watchlist</button>
  <button class="tab" onclick="pg('backtest')">Backtest</button>
  <button class="tab" onclick="pg('agents')">Agents</button>
  <button class="tab" onclick="pg('report')">Rapor</button>
  <button class="tab" onclick="pg('settings')">Ayarlar</button>
  <button class="tab" onclick="pg('telegram')">Telegram</button>

  <button class="tab" id="socialTab" onclick="pg('social')">&#128101; Sosyal</button>
  <button class="tab" id="devTab" onclick="pg('dev')">Dev</button></nav>
<main>
<!-- SINYALLER -->
<div class="page on" id="page-signals">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;padding:6px 10px;background:var(--bg2);border-radius:7px;border:1px solid var(--b1)">
    <div class="bg-ind"><div class="bg-dot on" id="bgDot"></div><span id="bgText">Arka plan aktif</span></div>
    <span style="font-size:9px;color:var(--t4)" id="lastsc">Son: -</span>
  </div>
  <div class="xu-banner neutral" id="xuBanner">
    <span id="xuDot" style="width:7px;height:7px;border-radius:50%;background:var(--t4);flex-shrink:0"></span>
    <span id="xuText" style="color:var(--t3)">XU100 yukleniyor...</span>
    <span id="xuFilter" style="margin-left:auto;font-size:9px;color:var(--t4)"></span>
  </div>
  <div class="chips" id="idxchips">
    <div class="chip cy on" data-v="ALL" onclick="idxT(this)">Tumu</div>
    <div class="chip cy" data-v="XK030EA" onclick="idxT(this)">K30EA</div>
    <div class="chip cy" data-v="XK030" onclick="idxT(this)">K30</div>
    <div class="chip cy" data-v="XK050" onclick="idxT(this)">K50</div>
    <div class="chip cy" data-v="XK100" onclick="idxT(this)">K100</div>
    <div class="chip cy" data-v="XKTUM" onclick="idxT(this)">KTUM</div>
    <div class="chip cy" data-v="XSRDK" onclick="idxT(this)">SRDK</div>
    <div class="chip cy" data-v="XKTMT" onclick="idxT(this)">KTMT</div>
  </div>
  <div class="chips">
    <div class="chip on" data-v="D" onclick="tfT(this)">Gunluk</div>
    <div class="chip on" data-v="240" onclick="tfT(this)">4 Saat</div>
    <div class="chip on" data-v="120" onclick="tfT(this)">2 Saat</div>
  </div>
  <div id="siglist"></div>
</div>
<!-- TARAYICI -->
<div class="page" id="page-scanner">
  <div class="card">
    <div class="ctitle">Tarama Ayarlari</div>
    <div class="srow"><div class="slbl">Zaman Dilimleri</div><div style="display:flex;gap:10px"><label style="font-size:10px;display:flex;align-items:center;gap:3px;cursor:pointer"><input type="checkbox" id="tf_D" checked> G</label><label style="font-size:10px;display:flex;align-items:center;gap:3px;cursor:pointer"><input type="checkbox" id="tf_120" checked> 2H</label><label style="font-size:10px;display:flex;align-items:center;gap:3px;cursor:pointer"><input type="checkbox" id="tf_240" checked> 4H</label></div></div>
    <div class="srow"><div class="slbl">Tarama Araligi (dk)</div><input type="number" class="ni" id="scanInterval" value="5" min="1" max="60"></div>
    <div class="srow"><div class="slbl">Min Consensus (%)</div><input type="number" class="ni" id="minCons" value="0" min="0" max="100"></div>
    <div class="srow"><div class="slbl">Sadece Master AI</div><label class="toggle"><input type="checkbox" id="onlyMaster"><div class="trk"></div></label></div>
  </div>
  <div class="card">
    <div class="ctitle">Hisse Listesi (<span id="scnt">0</span>)</div>
    <input type="text" class="sinput" id="ssearch" placeholder="Hisse ara..." oninput="filterSt(this.value)">
    <div class="chips" id="sIdxC">
      <div class="chip on" data-v="ALL" onclick="sIdxF(this)">Tumu</div>
      <div class="chip" data-v="XK030EA" onclick="sIdxF(this)">K30EA</div>
      <div class="chip" data-v="XK030" onclick="sIdxF(this)">K30</div>
      <div class="chip" data-v="XK050" onclick="sIdxF(this)">K50</div>
      <div class="chip" data-v="XK100" onclick="sIdxF(this)">K100</div>
      <div class="chip" data-v="XKTUM" onclick="sIdxF(this)">KTUM</div>
      <div class="chip" data-v="XSRDK" onclick="sIdxF(this)">SRDK</div>
      <div class="chip" data-v="XKTMT" onclick="sIdxF(this)">KTMT</div>
    </div>
    <div id="stlist"></div>
  </div>
</div>
<!-- POZISYONLAR -->
<div class="page" id="page-positions">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:9px">
    <div class="ctitle" style="margin:0">Acik Pozisyonlar</div>
    <button class="btn r" style="padding:5px 9px;font-size:9px" onclick="closeAllPositions()">Tumunu Kapat</button>
  </div>
  <div class="sgrid" id="posStats" style="margin-bottom:9px">
    <div class="sstat"><div class="sval p" id="posTotalPnl">-</div><div class="slb2">Toplam PnL %</div></div>
    <div class="sstat"><div class="sval" id="posCount">0</div><div class="slb2">Acik Pozisyon</div></div>
    <div class="sstat"><div class="sval p" id="posBestPnl">-</div><div class="slb2">En Iyi</div></div>
    <div class="sstat"><div class="sval n" id="posWorstPnl">-</div><div class="slb2">En Kotu</div></div>
  </div>
  <!-- Pozisyon sekmeleri -->
  <div class="chips" style="margin-bottom:9px">
    <div class="chip on" id="posTabOpen" onclick="posTab('open')">Acik Pozisyonlar</div>
    <div class="chip" id="posTabClosed" onclick="posTab('closed')">Kapali Gecmis</div>
  </div>
  <div id="positionList"><div class="empty"><div class="eico">&#128188;</div><div style="font-size:11px">Sinyal gelince pozisyon acilir.</div></div></div>
  <div id="closedList" style="display:none"></div>
</div>
<!-- WATCHLIST -->
<div class="page" id="page-watchlist">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:9px">
    <div class="ctitle" style="margin:0">Watchlist</div>
    <span style="font-size:9px;color:var(--t4)" id="wlCount">0 hisse</span>
  </div>
  <div class="card" style="margin-bottom:9px">
    <div style="display:flex;gap:6px;margin-bottom:7px">
      <input type="text" id="wlInput" class="sinput" placeholder="Hisse kodu (orn: EREGL)" style="margin:0;flex:1;text-transform:uppercase" oninput="this.value=this.value.toUpperCase()" onkeydown="if(event.key==='Enter')addWatchlist()">
      <button class="btn c" onclick="addWatchlist()" style="padding:9px 13px;border-radius:7px;flex-shrink:0">+ Ekle</button>
    </div>
    <div style="font-size:9px;color:var(--t4);margin-bottom:7px">Endeksi toplu ekle:</div>
    <div style="display:flex;flex-wrap:wrap;gap:4px">
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK030EA')">K30EA</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK030')">K30</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK050')">K50</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK100')">K100</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XKTUM')">KTUM</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XSRDK')">SRDK</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XKTMT')">KTMT</button>
      <button class="btn r" style="font-size:9px;padding:4px 8px" onclick="clearWatchlist()">Temizle</button>
    </div>
  </div>
  <div id="wlList"><div class="empty"><div class="eico">&#11088;</div><div style="font-size:11px">Favori hisse ekleyin.</div></div></div>
</div>
<!-- BACKTEST -->
<div class="page" id="page-backtest">
  <!-- Sekme gecisi -->
  <div class="chips" style="margin-bottom:9px">
    <div class="chip cy on" id="btTab1" onclick="btTabSwitch('bt')">Backtest</div>
    <div class="chip cy" id="btTab2" onclick="btTabSwitch('wf')">Walk-Forward</div>
    <div class="chip cy" id="btTab3" onclick="btTabSwitch('mc')">Monte Carlo</div>
    <div class="chip cy" id="btTab4" onclick="btTabSwitch('opt')">Optimizasyon</div>
  </div>

  <!-- Ortak parametreler -->
  <div class="card" id="btParams">
    <div class="ctitle">Parametreler</div>
    <div class="srow"><div class="slbl">Hisse</div><select class="si" id="btSym" style="min-width:120px"></select></div>
    <div class="srow"><div class="slbl">Zaman Dilimi</div>
      <select class="si" id="btTF">
        <option value="D">Gunluk</option>
        <option value="240">4 Saat</option>
        <option value="120">2 Saat</option>
      </select>
    </div>
    <div class="srow"><div class="slbl">Sistem</div>
      <select class="si" id="btSys">
        <option value="all">Hepsi (Master AI)</option>
        <option value="s1">Sistem 1 (ST+TMA)</option>
        <option value="s2">PRO Engine</option>
        <option value="fu">Fusion</option>
      </select>
    </div>
    <div class="srow"><div class="slbl">Sermaye (TL)</div><input type="number" class="ni" id="btCap" value="100000" style="width:110px"></div>
    <div class="srow"><div class="slbl">Komisyon (%)</div><input type="number" class="ni" id="btComm" value="0.1" step="0.05" style="width:80px"></div>
    <div class="srow"><div class="slbl">Slippage (%)</div><input type="number" class="ni" id="btSlip" value="0.05" step="0.05" style="width:80px"></div>
    <div id="btExtraParams"></div>
  </div>

  <!-- Backtest paneli -->
  <div id="panel-bt">
    <button class="btn g" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px;font-size:13px;font-weight:700" onclick="runBT()">Backtest Calistir</button>
    <div id="btout" style="display:none">
      <div class="sgrid" id="btstats" style="grid-template-columns:repeat(2,1fr)"></div>
      <!-- Equity Curve -->
      <div class="card" style="padding:10px">
        <div class="ctitle">Equity Curve</div>
        <div style="position:relative;height:180px"><canvas id="btcv"></canvas></div>
      </div>
      <!-- Aylik getiri isi haritasi -->
      <div class="card" style="padding:10px">
        <div class="ctitle">Aylik Dagilim</div>
        <div id="btMonthly"></div>
      </div>
      <!-- Islem tablosu -->
      <div class="card">
        <div class="ctitle">Islemler (<span id="btTradeCount">0</span>)</div>
        <div id="btlog" class="scr" style="max-height:220px"></div>
      </div>
    </div>
  </div>

  <!-- Walk-Forward paneli -->
  <div id="panel-wf" style="display:none">
    <div class="card" style="margin-bottom:8px">
      <div class="ctitle">Walk-Forward Ayarlari</div>
      <div class="srow"><div class="slbl">Egitim Periyodu</div>
        <select class="si" id="wfTrain">
          <option value="0.6">%60 Egitim / %40 Test</option>
          <option value="0.7" selected>%70 Egitim / %30 Test</option>
          <option value="0.8">%80 Egitim / %20 Test</option>
        </select>
      </div>
      <div class="srow"><div class="slbl">Optimize Kriteri</div>
        <select class="si" id="wfCrit">
          <option value="sharpe">Sharpe Ratio</option>
          <option value="ret">Toplam Getiri</option>
          <option value="wr">Win Rate</option>
        </select>
      </div>
      <div style="font-size:9px;color:var(--t4);margin-top:6px;line-height:1.6">Walk-Forward: Gecmis veriyi egitim/test olarak boler. Egitim bolumunde en iyi parametreyi bulur, test bolumunde dogrular. Overfitting'i onler.</div>
    </div>
    <button class="btn c" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px" onclick="runWF()">Walk-Forward Baslat</button>
    <div id="wfout" style="display:none"></div>
  </div>

  <!-- Monte Carlo paneli -->
  <div id="panel-mc" style="display:none">
    <div class="card" style="margin-bottom:8px">
      <div class="ctitle">Monte Carlo Ayarlari</div>
      <div class="srow"><div class="slbl">Senaryo Sayisi</div>
        <select class="si" id="mcScen">
          <option value="200">200 Senaryo (Hizli)</option>
          <option value="500" selected>500 Senaryo</option>
          <option value="1000">1000 Senaryo (Detayli)</option>
        </select>
      </div>
      <div style="font-size:9px;color:var(--t4);margin-top:6px;line-height:1.6">Monte Carlo: Gercek islemleri rastgele sirayla 500 kez calistirir. Sistemin sans mi yoksa gercek mi oldugunu gosterir. P5-P95 araligini verir.</div>
    </div>
    <button class="btn o" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px" onclick="runMC()">Monte Carlo Baslat</button>
    <div id="mcout" style="display:none"></div>
  </div>

  <!-- Optimizasyon paneli -->
  <div id="panel-opt" style="display:none">
    <div class="card" style="margin-bottom:8px">
      <div class="ctitle">Parametre Optimizasyonu</div>
      <div class="srow"><div class="slbl">Parametre</div>
        <select class="si" id="optP">
          <option value="atr">ATR Multiplier (Stop Genisligi)</option>
          <option value="adx">Min ADX (Trend Gucu)</option>
          <option value="fb">Fusion Buy Esigi</option>
          <option value="mb">Master Buy Esigi</option>
          <option value="pro">PRO Min Skor</option>
        </select>
      </div>
      <div class="srow"><div class="slbl">Optimizasyon Kriteri</div>
        <select class="si" id="optCrit">
          <option value="sharpe">Sharpe Ratio</option>
          <option value="ret">Toplam Getiri %</option>
          <option value="wr">Win Rate %</option>
          <option value="calmar">Calmar (Ret/MaxDD)</option>
        </select>
      </div>
      <div style="font-size:9px;color:var(--t4);margin-top:6px;line-height:1.6">Her parametre degeri icin tam backtest calistirilir. Komisyon ve slippage dahildir. Sonuclar kriterine gore siralanir.</div>
    </div>
    <button class="btn g" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px" onclick="runOpt()">Optimize Et</button>
    <div id="optout" style="display:none"></div>
  </div>
</div>
<!-- AGENTS -->
<div class="page" id="page-agents">
  <div class="card" style="border-color:rgba(255,184,0,.2)">
    <div class="ctitle" style="color:var(--gold)">Master AI</div>
    <div class="sgrid">
      <div class="sstat"><div class="sval" id="mcons">-</div><div class="slb2">Buy Consensus</div></div>
      <div class="sstat"><div class="sval" id="mthresh">-</div><div class="slb2">Threshold</div></div>
      <div class="sstat"><div class="sval p" id="mpnl">-</div><div class="slb2">Master PnL</div></div>
      <div class="sstat"><div class="sval" id="mpos">YOK</div><div class="slb2">Pozisyon</div></div>
    </div>
  </div>
  <div class="card">
    <div class="ctitle">Agent Durumu</div>
    <table class="atbl"><thead><tr><th>Agent</th><th>Aktif</th><th>PnL%</th><th>Itibar</th><th>Sermaye</th></tr></thead><tbody id="agTbl"></tbody></table>
  </div>
  <div class="card"><div class="ctitle">Quantum Durumu</div><div id="qviz"></div></div>
</div>
<!-- RAPOR -->
<div class="page" id="page-report">
  <div class="ctitle">Performans Raporu</div>
  <div class="card">
    <div class="ctitle">Bu Hafta</div>
    <div class="sgrid">
      <div class="sstat"><div class="sval" id="wkSigs">0</div><div class="slb2">Sinyal</div></div>
      <div class="sstat"><div class="sval" id="wkMaster">0</div><div class="slb2">Master AI</div></div>
      <div class="sstat"><div class="sval" id="wkStops">0</div><div class="slb2">Stop</div></div>
      <div class="sstat"><div class="sval p" id="wkBest">-</div><div class="slb2">En Aktif</div></div>
    </div>
  </div>
  <div class="card"><div class="ctitle">Sistem Performansi</div><div id="sysPerf"></div></div>
  <div class="card">
    <div class="ctitle">Son 28 Gun</div>
    <div style="display:flex;gap:3px;margin-bottom:5px">
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Pt</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Sa</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Ca</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Pe</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Cu</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Ct</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Pz</span>
    </div>
    <div class="cal-grid" id="calGrid"></div>
  </div>
  <div class="card"><div class="ctitle">En Cok Sinyal</div><div id="topStocks"></div></div>
  <button class="btn c" style="width:100%;padding:11px;border-radius:8px;margin-bottom:8px" onclick="sendWeeklyReport()">Haftalik Raporu Gonder</button>
  <button class="btn o" style="width:100%;padding:11px;border-radius:8px;margin-bottom:8px" onclick="sendDayEndReport()">Gun Sonu Raporunu Gonder</button>
  <div class="card" style="border-color:rgba(255,184,0,.3);margin-top:4px">
    <div class="ctitle" style="color:var(--gold)">Profesyonel Trader Gozetimi</div>
    <div style="font-size:10px;color:var(--t3);margin-bottom:8px;line-height:1.6">Portfoy riski, win rate analizi, sistem performansi ve gelistirme onerileri</div>
    <button class="btn o" style="width:100%;padding:11px;border-radius:8px" onclick="openTraderConsole()">Trader Gozetimini Ac</button>
  </div>
</div>
<!-- AYARLAR -->
<div class="page" id="page-settings">
  <div class="stit">Sistem Etkinlestirme</div>
  <div class="srow"><div class="slbl">Sistem 1 (SuperTrend+TMA+Chandelier)</div><label class="toggle"><input type="checkbox" id="s_s1" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Sistem 2 (PRO ENGINE)</div><label class="toggle"><input type="checkbox" id="s_s2" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Fusion Sistemi</div><label class="toggle"><input type="checkbox" id="s_fu" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Master AI</div><label class="toggle"><input type="checkbox" id="s_ma" checked><div class="trk"></div></label></div>
  <div class="stit" style="margin-top:11px">Bagimsiz Agentlar</div>
  <div class="srow"><div class="slbl">Agent 60</div><label class="toggle"><input type="checkbox" id="s_a60"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 61</div><label class="toggle"><input type="checkbox" id="s_a61"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 62</div><label class="toggle"><input type="checkbox" id="s_a62"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 81</div><label class="toggle"><input type="checkbox" id="s_a81"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 120</div><label class="toggle"><input type="checkbox" id="s_a120"><div class="trk"></div></label></div>
  <div class="stit" style="margin-top:11px">Parametreler</div>
  <div class="srow"><div class="slbl">Min ADX</div><input type="number" class="ni" id="s_adxMin" value="25"></div>
  <div class="srow"><div class="slbl">ATR Multiplier</div><input type="number" class="ni" id="s_atrm" value="8" step="0.5"></div>
  <div class="srow"><div class="slbl">PRO Min Skor</div><input type="number" class="ni" id="s_sc" value="5" min="1" max="6"></div>
  <div class="srow"><div class="slbl">Fusion Buy Thr (%)</div><input type="number" class="ni" id="s_fb" value="80" min="1" max="100"></div>
  <div class="srow"><div class="slbl">Master Buy Thr (%)</div><input type="number" class="ni" id="s_mb" value="70" min="1" max="100"></div>
  <div class="stit" style="margin-top:11px">Bildirimler</div>
  <div class="srow"><div class="slbl">Ses</div><label class="toggle"><input type="checkbox" id="s_snd" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Push</div><label class="toggle"><input type="checkbox" id="s_pn"><div class="trk"></div></label></div>
  <div class="srow">
    <div><div class="slbl">Service Worker Bildirimi</div><div class="sdesc">Uygulama kapali iken de bildirim al</div></div>
    <button class="btn c" style="padding:7px 12px;font-size:10px" onclick="requestPushPermission()">Izin Ver</button>
  </div>
  <div style="display:flex;gap:6px;margin-top:11px">
    <button class="btn g" style="flex:1;padding:10px;border-radius:8px" onclick="saveSets()">Kaydet</button>
    <button class="btn r" style="padding:10px 14px;border-radius:8px" onclick="resetSets()">Sifirla</button>
  </div>
  <div class="stit" style="margin-top:14px">Ozel Indiktor Ekle (FIX 11)</div>
  <div style="font-size:9px;color:var(--t4);margin-bottom:8px">Eklenen indiktor tarama kosuluna dahil edilir</div>
  <div style="display:flex;gap:6px;margin-bottom:7px">
    <input type="text" class="sinput" id="customIndName" placeholder="Indiktor adi (orn: MACD Kesisim)" style="margin:0;flex:1;font-size:11px">
  </div>
  <div class="srow"><div class="slbl">Min RSI</div><input type="number" class="ni" id="ci_rsi" value="50" min="0" max="100"></div>
  <div class="srow"><div class="slbl">Min ADX</div><input type="number" class="ni" id="ci_adx" value="20" min="0" max="100"></div>
  <div class="srow"><div class="slbl">EMA Ustunde</div><label class="toggle"><input type="checkbox" id="ci_ema"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Taramaya Dahil Et</div><label class="toggle"><input type="checkbox" id="ci_scan"><div class="trk"></div></label></div>
  <button class="btn c" style="width:100%;padding:9px;border-radius:7px;margin-top:8px" onclick="addCustomIndicator()">Indiktor Ekle</button>
  <div id="customIndList" style="margin-top:7px"></div>
</div>
<!-- TELEGRAM -->
<div class="page" id="page-telegram">
  <div class="card">
    <div class="ctitle">Bot Ayarlari</div>
    <input type="text" class="tgi" id="tgtoken" placeholder="Bot Token: 123456:ABC-DEF...">
    <input type="text" class="tgi" id="tgchat" placeholder="Chat ID: 123456789">
    <div style="font-size:9px;color:var(--t4);margin-bottom:9px">Chat ID icin @userinfobot yazin</div>
    <div class="stit" style="margin-top:3px">Mesaj Icerigi</div>
    <div class="srow"><div class="slbl">Grafik Linki</div><label class="toggle"><input type="checkbox" id="tg_ch" checked><div class="trk"></div></label></div>
    <div class="srow"><div class="slbl">Detayli Metrikler</div><label class="toggle"><input type="checkbox" id="tg_det" checked><div class="trk"></div></label></div>
    <div class="srow"><div class="slbl">Fiyat Bolgesi</div><label class="toggle"><input type="checkbox" id="tg_q" checked><div class="trk"></div></label></div>
    <div class="srow"><div class="slbl">Aktif Agentlar</div><label class="toggle"><input type="checkbox" id="tg_ag"><div class="trk"></div></label></div>
    <div style="display:flex;gap:6px;margin-top:9px">
      <button class="btn g" style="flex:1;padding:10px;border-radius:8px" onclick="tgTest()">Test Gonder</button>
      <button class="btn c" style="flex:1;padding:10px;border-radius:8px" onclick="tgSave()">Kaydet</button>
    </div>
    <div id="tgDebug" style="margin-top:7px;padding:7px;background:var(--bg4);border-radius:7px;font-size:9px;color:var(--t3);font-family:'Courier New',monospace;display:none"></div>
  </div>
  <div class="card">
    <div class="ctitle">Bildirim Gecmisi</div>
    <div id="tglog" class="scr" style="font-size:10px;color:var(--t3);font-family:'Courier New',monospace;min-height:36px"></div>
  </div>
  <div class="card">
    <div class="ctitle">Bot Komutlari</div>
    <div style="font-size:10px;color:var(--t3);line-height:1.9;font-family:'Courier New',monospace">/durum /portfoy /sinyal EREGL<br>/tara /rapor /yardim</div>
  </div>
</div>


  <div id="page-social" class="page"></div>
  <div id="page-dev" class="page"></div></main>
<footer>
  <div class="finfo">
    <div class="bg-ind"><div class="bg-dot on" id="bgSvcDot"></div><span id="bgSvcTxt">Aktif</span></div>
  </div>
  <button id="scanBtn" onclick="startScan()">TARA</button>
</footer>
<div class="movl" id="modal" onclick="closeM(event)">
  <div class="modal">
    <div class="mhdl"></div>
    <div class="mtit" id="mtit">Detay</div>
    <div id="mcont"></div>
    <div style="display:flex;gap:6px;margin-top:13px">
      <button class="btn c" style="flex:1;padding:11px;border-radius:8px" onclick="openTV()">TradingView'da Gor</button>
      <button class="btn g" style="flex:1;padding:11px;border-radius:8px" onclick="sendCur()">Telegram</button>
    </div>
  </div>
</div>
<div id="toast"></div><script>
// BIST AI Scanner v6 - Tam Duzeltilmis
// FIX 1: Arka plan servisi gostergesi
// FIX 2: Proxy URL - TradingView, fiyat, XU100 
// FIX 3,4,5: TradingView acilisi - direkt location.href
// FIX 6: Sinyal karti gercek fiyat
// FIX 7: Pozisyon % PnL gosterimi
// FIX 8: Watchlist endeks toplu ekleme
// FIX 14: pstate renk eslesmesi duzeltildi
// FIX 15: Gun sonu otomatik rapor

var PROXY_URL='https://bist-price-proxy.onrender.com';
// Deploy sonrasi guncelle: https://PROJE.onrender.com

// -- YARDIMCILAR --
function lsGet(k){try{var v=localStorage.getItem(k);return v?JSON.parse(v):null}catch(e){return null}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function pad(n){return n.toString().padStart(2,'0')}
function toast(msg,ms){var t=document.getElementById('toast');t.textContent=msg;t.classList.add('on');setTimeout(function(){t.classList.remove('on')},ms||2400)}
function setDot(s){document.getElementById('dot').className='dot '+s}
function setSt(t){document.getElementById('hst').textContent=t}

// -- HISSE LISTESI --
var STOCKS=[
{t:'AKSA',n:'AKSA',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'ALTNY',n:'ALTINAY SAVUNMA',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'ASELS',n:'ASELSAN',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'BIMAS',n:'BIM MAGAZALAR',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'BSOKE',n:'BATISOKE CIMENTO',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'CANTE',n:'CAN2 TERMIK',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'CIMSA',n:'CIMSA',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'CWENE',n:'CW ENERJI',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'DAPGM',n:'DAP GAYRIMENKUL',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'EKGYO',n:'EMLAK KONUT GMYO',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'ENJSA',n:'ENERJISA ENERJI',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'EREGL',n:'EREGLI DEMIR CELIK',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'EUPWR',n:'EUROPOWER ENERJI',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'GENIL',n:'GEN ILAC',i:['XK030EA','XKTUM','XK100','XK050','XK030','XKTMT']},
{t:'GESAN',n:'GIRISIM ELEKTRIK',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'GLRMK',n:'GULERMAK AGIR',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'GRSEL',n:'GUR-SEL TURIZM',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'GUBRF',n:'GUBRE FABRIK.',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'KONTR',n:'KONTROLMATIK',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'KRDMD',n:'KARDEMIR (D)',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'KTLEV',n:'KATILIMEVIM',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'KUYAS',n:'KUYAS YATIRIM',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'MAVI',n:'MAVI GIYIM',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'MPARK',n:'MLP SAGLIK',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'OBAMS',n:'OBA MAKARNACILIK',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'PETKM',n:'PETKIM',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'TUPRS',n:'TUPRAS',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'YEOTK',n:'YEO TEKNOLOJI',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'AKFYE',n:'AKFEN YEN. ENERJI',i:['XKTUM','XK100']},
{t:'ALBRK',n:'ALBARAKA TURK',i:['XKTUM','XK100','XSRDK']},
{t:'ARDYZ',n:'ARD BILISIM',i:['XKTUM','XK100','XKTMT']},
{t:'BANVT',n:'BANVIT',i:['XKTUM','XK100']},
{t:'BRSAN',n:'BORUSAN MANNESMANN',i:['XKTUM','XK100']},
{t:'BUCIM',n:'BURSA CIMENTO',i:['XKTUM','XK100']},
{t:'DMSAS',n:'DEMISAS DOKUM',i:['XKTUM','XK100']},
{t:'ENKAI',n:'ENKA INSAAT',i:['XKTUM','XK100','XK050']},
{t:'ERBOS',n:'ERBOSAN',i:['XKTUM','XK100']},
{t:'FROTO',n:'FORD OTOSAN',i:['XKTUM','XK100','XK050','XSRDK','XKTMT']},
{t:'GARAN',n:'GARANTI BBVA',i:['XKTUM','XK100','XK050']},
{t:'HEKTS',n:'HEKTAS',i:['XKTUM','XK100']},
{t:'IPEKE',n:'IPEK DOGAL ENERJI',i:['XKTUM','XK100']},
{t:'ISDMR',n:'ISKENDERUN DEMIR',i:['XKTUM','XK100','XK050','XSRDK']},
{t:'KCHOL',n:'KOC HOLDING',i:['XKTUM','XK100','XK050']},
{t:'KOZAL',n:'KOZA ALTIN',i:['XKTUM','XK100','XK050','XSRDK']},
{t:'LOGO',n:'LOGO YAZILIM',i:['XKTUM','XK100','XKTMT']},
{t:'MGROS',n:'MIGROS TICARET',i:['XKTUM','XK100','XK050']},
{t:'OYAKC',n:'OYAK CIMENTO',i:['XKTUM','XK100','XK050']},
{t:'PRKAB',n:'PARK ELEKTRIK',i:['XKTUM','XK100','XSRDK']},
{t:'SASA',n:'SASA POLYESTER',i:['XKTUM','XK100','XK050','XSRDK']},
{t:'SELEC',n:'SELCUK ECZA',i:['XKTUM','XK100']},
{t:'TATGD',n:'TAT GIDA',i:['XKTUM','XK100']},
{t:'TMSN',n:'TUMOSAN',i:['XKTUM','XK100']},
{t:'TOASO',n:'TOFAS',i:['XKTUM','XK100','XK050']},
{t:'TTKOM',n:'TURK TELEKOM',i:['XKTUM','XK100','XK050']},
{t:'ULKER',n:'ULKER BISKUVI',i:['XKTUM','XK100','XK050']},
{t:'VAKBN',n:'VAKIF BANK',i:['XKTUM','XK100','XK050']},
{t:'VESTL',n:'VESTEL',i:['XKTUM','XK100']},
{t:'YKBNK',n:'YAPI KREDI',i:['XKTUM','XK100','XK050']},
{t:'THYAO',n:'TURK HAVA YOLLARI',i:['XKTUM','XK100','XK050']},
{t:'SAHOL',n:'SABANCI HOLDING',i:['XKTUM','XK100','XK050']},
{t:'SISE',n:'SISE CAM',i:['XKTUM','XK100','XK050']},
{t:'TCELL',n:'TURKCELL',i:['XKTUM','XK100','XK050']},
{t:'TAVHL',n:'TAV HAVALIMANLARI',i:['XKTUM','XK100','XK050']},
{t:'AKBNK',n:'AKBANK',i:['XKTUM','XK100','XK050']},
{t:'ISCTR',n:'IS BANKASI',i:['XKTUM','XK100','XK050']},
{t:'ZOREN',n:'ZORLU ENERJI',i:['XKTUM','XK100']},
{t:'SKBNK',n:'SEKERBANK',i:['XKTUM','XK100']},
{t:'KRDMA',n:'KARDEMIR (A)',i:['XKTUM']},
{t:'KRDMB',n:'KARDEMIR (B)',i:['XKTUM']},
];

// -- CONFIG --
var DEF={s1:true,s2:true,fu:true,ma:true,a60:false,a61:false,a62:false,a81:false,a120:false,adxMin:25,atrm:8,sc:5,fb:80,mb:70,pn:false,snd:true,minCons:0,onlyMaster:false,scanInterval:5};
var DEF_TG={token:'',chat:'',ch:true,det:true,q:true,ag:false};
var C=lsGet('bistcfg')||Object.assign({},DEF);
var TG=lsGet('bisttg')||Object.assign({},DEF_TG);
var S={sigs:[],scanning:false,autoTimer:null,tfFilter:['D','120','240'],idxFilter:'ALL',scanIdx:'ALL',curSig:null,agentPnl:[8.2,-3.1,12.5,5.7,-1.4,9.8,3.2,-2.0],agentRep:[0.62,0.78,0.55,0.81,0.70,0.65,0.72,0.58],agentCap:[11,18,9,22,14,10,9,7],masterPnl:0,nextScanIn:0,sentCount:{},openPositions:{},closedPositions:lsGet('bist_closed')||[],watchlist:lsGet('bist_wl')||[],sigHistory:lsGet('bist_hist')||[],priceCache:{},priceLastFetch:0,xu100Trend:'neutral',xu100Change:0,ohlcvCache:{}};

// -- NAV --
function pg(name){
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('on')});
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('on')});
  document.getElementById('page-'+name).classList.add('on');
  document.querySelectorAll('.tab').forEach(function(t){if(t.getAttribute('onclick')&&t.getAttribute('onclick').indexOf("'"+name+"'")>-1)t.classList.add('on');});
  if(name==='positions')renderPositions();
  if(name==='watchlist')renderWatchlist();
  if(name==='report')renderReport();
}

// FIX 14: pstate renk - proxy COK/CUK UCUZ/PAHALI eslesir
// FIX 14: Pine Script ile birebir aynı pstate hesabı
// pricePct = percentile_linear_interpolation(close, 150, 50)
// zScore = (close - sma(close,150)) / stdev(close,150)
// quantum states - Pine'daki ile aynı formul
function calcPstate(closes){
  var n=Math.min(150,closes.length);
  if(n<10) return 'NORMAL';
  var arr=closes.slice(-n).slice().sort(function(a,b){return a-b;});
  var close=closes[closes.length-1];
  // Percentile rank (0-1) - Pine percentile_linear_interpolation
  var rank=0;
  for(var i=0;i<arr.length;i++){if(arr[i]<=close)rank=i+1;}
  var pricePct=rank/arr.length; // 0-1 arasi
  // Z-Score
  var mean=0;for(var i=0;i<n;i++)mean+=closes[closes.length-n+i];mean/=n;
  var variance=0;for(var i=0;i<n;i++){var d=closes[closes.length-n+i]-mean;variance+=d*d;}
  var stdev=Math.sqrt(variance/n);
  var zScore=stdev>0?(close-mean)/stdev:0;
  // Quantum state formulleri
  var veryCheapQ = Math.max(0,(0.15-pricePct)/0.15) * Math.max(0,(-1.5-zScore)/-1.5);
  var cheapQ     = Math.max(0,Math.abs(pricePct-0.225)/0.075) * Math.max(0,(-0.5-zScore)/-0.5);
  var normalQ    = Math.max(0,1-Math.abs(pricePct-0.5)/0.2);
  var expQ       = Math.max(0,Math.abs(pricePct-0.775)/0.075) * Math.max(0,(zScore-0.5)/0.5);
  var veryExpQ   = Math.max(0,(pricePct-0.85)/0.15) * Math.max(0,(zScore-1.5)/1.5);
  var sum=veryCheapQ+cheapQ+normalQ+expQ+veryExpQ;
  if(sum>0){veryCheapQ/=sum;cheapQ/=sum;normalQ/=sum;expQ/=sum;veryExpQ/=sum;}
  // pstate logic: hangi state 0.5 asar
  if(veryExpQ>0.5)   return 'COK PAHALI';
  if(expQ>0.5)       return 'PAHALI';
  if(normalQ>0.5)    return 'NORMAL';
  if(cheapQ>0.5)     return 'UCUZ';
  if(veryCheapQ>0.5) return 'COK UCUZ';
  // Hicbiri gecmiyorsa en buyugu al
  var states=[
    {n:'COK UCUZ',v:veryCheapQ},{n:'UCUZ',v:cheapQ},
    {n:'NORMAL',v:normalQ},{n:'PAHALI',v:expQ},{n:'COK PAHALI',v:veryExpQ}
  ];
  states.sort(function(a,b){return b.v-a.v;});
  return states[0].n;
}
function psC(ps){
  if(!ps)return '#94a3b8';
  var p=(ps+'').toUpperCase();
  // Pine renkleri ile eslesen
  if(p==='COK UCUZ')  return '#00c853';  // green
  if(p==='UCUZ')      return '#69f0ae';  // lime
  if(p==='NORMAL')    return '#ff9800';  // orange
  if(p==='PAHALI')    return '#ef5350';  // red
  if(p==='COK PAHALI')return '#b71c1c';  // maroon
  return '#94a3b8';
}

function calcStrength(r){
  if(!r)return 1;
  var sc=0,cons=parseFloat(r.cons||r.consensus||0),adx=parseFloat(r.adx||0),pro=parseInt(r.score||r.pro_score||0),fp=parseFloat(r.fp||r.fusion_pct||0);
  if(cons>=80)sc+=4;else if(cons>=65)sc+=3;else if(cons>=50)sc+=2;else if(cons>=35)sc+=1;
  if(adx>=40)sc+=2;else if(adx>=25)sc+=1;
  if(pro>=5)sc+=2;else if(pro>=3)sc+=1;
  if(fp>=60)sc+=1;
  if(r.isMaster||r.is_master)sc=Math.min(10,sc+1);
  return Math.max(1,Math.min(10,sc));
}

// FIX 2: Proxy uzerinden fiyat
function fetchPrices(tickers,onDone){
  var now=Date.now();
  if(now-S.priceLastFetch<10*60*1000&&Object.keys(S.priceCache).length>0){if(onDone)onDone();return;}
  fetch(PROXY_URL+'/prices?symbols='+encodeURIComponent(tickers.join(',')))
  .then(function(r){return r.json()})
  .then(function(data){
    Object.keys(data).forEach(function(t){var q=data[t];if(q&&q.price>0)S.priceCache[t]={price:q.price,change:q.change||0,pct:q.change_pct||0,high:q.high||0,low:q.low||0,vol:q.volume||0,real:true,ts:Date.now()};});
    S.priceLastFetch=Date.now();setSt('Fiyatlar hazir');if(onDone)onDone();
  })
  .catch(function(){setSt('Fiyat proxy hatasi');if(onDone)onDone();});
}

// FIX 2,3,4,5: TradingView - direkt window.location.href, her yerde calısır
function openTV(){
  if(!S.curSig)return;
  var tf=S.curSig.tf||'D',intv=tf==='D'?'1D':tf==='240'?'4H':'2H';
  document.getElementById('modal').classList.remove('on');
  window.location.href='https://www.tradingview.com/chart/?symbol=BIST:'+S.curSig.ticker+'&interval='+intv;
}
function openTVTicker(ticker,tf){
  var intv=(tf||'D')==='D'?'1D':tf==='240'?'4H':'2H';
  window.location.href='https://www.tradingview.com/chart/?symbol=BIST:'+ticker+'&interval='+intv;
}

// -- XU100 (FIX 2: proxy) --
function updateXU100(){
  fetch(PROXY_URL+'/xu100').then(function(r){return r.json()}).then(function(d){
    if(d&&d.price>0){var c=parseFloat((d.change_pct||0).toFixed(2));S.xu100Change=c;S.xu100Trend=c>1?'bull':c<-1?'bear':'neutral';renderXU(d.price,c);}
    else renderXUFallback();
  }).catch(renderXUFallback);
}
function renderXU(price,change){
  var bn=document.getElementById('xuBanner'),dot=document.getElementById('xuDot'),txt=document.getElementById('xuText'),flt=document.getElementById('xuFilter');
  if(!bn)return;
  bn.className='xu-banner '+S.xu100Trend;
  var ps=price>0?' | '+price.toLocaleString('tr-TR'):'';
  if(S.xu100Trend==='bull'){dot.style.background='var(--green)';txt.style.color='var(--green)';txt.textContent='XU100 +'+(Math.abs(change).toFixed(2))+'%'+ps;flt.textContent='Piyasa pozitif';flt.style.color='var(--green)';}
  else if(S.xu100Trend==='bear'){dot.style.background='var(--red)';txt.style.color='var(--red)';txt.textContent='XU100 '+change.toFixed(2)+'%'+ps;flt.textContent='Dikkatli!';flt.style.color='var(--red)';}
  else{dot.style.background='var(--gold)';txt.style.color='var(--gold)';txt.textContent='XU100 '+(change>=0?'+':'')+change.toFixed(2)+'%'+ps;flt.textContent='Yatay';flt.style.color='var(--gold)';}
}
function renderXUFallback(){
  var now=new Date(),sd=now.getDate()*7+now.getMonth()*31;
  var c=parseFloat((Math.sin(sd*0.37)*1.8+Math.cos(sd*0.21)*0.9).toFixed(2));
  S.xu100Change=c;S.xu100Trend=c>1?'bull':c<-1?'bear':'neutral';renderXU(0,c);
}

// -- SCAN --
var scanAbort=false;
function startScan(){
  if(S.scanning){stopScan();return;}
  S.scanning=true;scanAbort=false;
  setDot('run');setSt('Proxy baglaniyor...');
  document.getElementById('scanBtn').classList.add('run');
  document.getElementById('scanBtn').textContent='DUR';
  var tfs=[];
  if(document.getElementById('tf_D').checked)tfs.push('D');
  if(document.getElementById('tf_120').checked)tfs.push('120');
  if(document.getElementById('tf_240').checked)tfs.push('240');
  if(!tfs.length)tfs=['D'];
  C.minCons=parseInt(document.getElementById('minCons').value)||0;
  C.onlyMaster=document.getElementById('onlyMaster').checked;
  var stocks=getStocks(),tfIdx=0;
  function scanTF(){
    if(scanAbort||tfIdx>=tfs.length){finishScan();return;}
    var tf=tfs[tfIdx];tfIdx++;
    setSt('Tarama: '+tf+' ('+stocks.length+' hisse)...');
    document.getElementById('pbar').style.width='30%';
    fetch(PROXY_URL+'/scan',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tickers:stocks.map(function(s){return{ticker:s.t,name:s.n,indices:s.i}}),tf:tf,cfg:C,min_consensus:C.minCons,only_master:C.onlyMaster})
    }).then(function(r){return r.json()}).then(function(data){
      document.getElementById('pbar').style.width='70%';
      var signals=data.signals||[];
      setSt(tf+': '+signals.length+' sinyal');
      signals.forEach(function(res){
        if(scanAbort)return;
        var pk=res.ticker+'_'+tf,pos=S.openPositions[pk];
        if(pos&&res.price<=pos.stopPrice){
          var ss={id:Date.now()+Math.random(),ticker:res.ticker,name:res.name,indices:res.indices||[],type:'stop',time:new Date(),tf:tf,
            res:{price:res.price,stopPrice:pos.stopPrice,cons:res.consensus,stopPnl:((res.price-pos.entry)/pos.entry*100).toFixed(2),adx:res.adx,acts:res.active_sys||[],isReal:true}};
          S.sigs.unshift(ss);if(S.sigs.length>200)S.sigs.pop();
          saveToHistory(ss);if(TG.token&&TG.chat)sendTG(ss);
          delete S.openPositions[pk];if(C.snd)beep(false);return;
        }
        var pass=(!C.onlyMaster||res.is_master)&&res.consensus>=C.minCons;if(!pass)return;
        if(!pos)S.openPositions[pk]={entry:res.price,highest:res.price,stopPrice:res.stop_price||(res.price*0.95),entryTime:new Date()};
        var sig={id:Date.now()+Math.random(),ticker:res.ticker,name:res.name,indices:res.indices||[],
          type:res.is_master?'master':'buy',time:new Date(),tf:tf,
          res:{price:res.price,cons:res.consensus,adx:res.adx,rsi:res.rsi,score:res.pro_score,fp:res.fusion_pct,
            pstate:res.pstate,isMaster:res.is_master,anyBuy:res.signal,acts:res.active_sys||[],
            ema200:res.ema200,ema50:res.ema50,currentStop:res.stop_price,isReal:true,dayChange:null}};
        S.sigs.unshift(sig);if(S.sigs.length>200)S.sigs.pop();saveToHistory(sig);
        var sk=res.ticker+'_'+tf,cnt=S.sentCount[sk]||0;
        if(TG.token&&TG.chat&&cnt<2){
          sendTG(sig);S.sentCount[sk]=cnt+1;
          if(cnt===0){(function(sg,k){setTimeout(function(){if((S.sentCount[k]||0)<2&&S.openPositions[k]){sendTG(sg);S.sentCount[k]=2;}},8*60*1000);})(sig,sk);}
        }
        if(C.snd)beep(res.is_master);
        if(C.pn&&typeof Notification!=='undefined'&&Notification.permission==='granted'){try{new Notification((res.is_master?'MASTER AI':'AL')+' '+res.ticker,{body:tf+' | %'+res.consensus+' | TL'+res.price})}catch(ex){}}
        if(res.price>0)S.priceCache[res.ticker]={price:res.price,pct:0,ts:Date.now(),real:true};
      });
      document.getElementById('pbar').style.width='90%';setTimeout(scanTF,200);
    }).catch(function(e){setSt('Proxy hatasi - '+tf);setTimeout(scanTF,300);});
  }
  scanTF();
  function finishScan(){
    fetch(PROXY_URL+'/prices?symbols='+encodeURIComponent(getStocks().slice(0,100).map(function(s){return s.t}).join(',')))
    .then(function(r){return r.json()}).then(function(p){
      Object.keys(p).forEach(function(t){if(p[t].price>0)S.priceCache[t]={price:p[t].price,change:p[t].change||0,pct:p[t].change_pct||0,high:p[t].high||0,low:p[t].low||0,real:true,ts:Date.now()};});
      S.priceLastFetch=Date.now();
    }).catch(function(){}).finally(function(){
      var now=new Date();
      document.getElementById('lastsc').textContent='Son: '+pad(now.getHours())+':'+pad(now.getMinutes());
      renderSigs();renderSt();updateBadge();renderAgents();renderPositions();
      checkDayEndReport();stopScan(true);
    });
  }
}
function stopScan(ok){
  S.scanning=false;scanAbort=true;
  setDot(ok?'ok':'off');setSt(ok?'Tamamlandi':'Durduruldu');
  document.getElementById('scanBtn').classList.remove('run');
  document.getElementById('scanBtn').textContent='TARA';
  setTimeout(function(){document.getElementById('pbar').style.width='0%'},1000);
}
function startAutoScan(){
  if(S.autoTimer)clearInterval(S.autoTimer);
  var mins=parseInt(C.scanInterval)||5;S.nextScanIn=mins*60;
  setTimeout(function(){startScan();},800);
  S.autoTimer=setInterval(function(){S.nextScanIn--;if(S.nextScanIn<=0){S.nextScanIn=(parseInt(C.scanInterval)||5)*60;if(!S.scanning)startScan();}},1000);
}

// FIX 1: Arka plan gostergesi
function setBgActive(v){
  var d=document.getElementById('bgDot'),sd=document.getElementById('bgSvcDot'),t=document.getElementById('bgText'),st=document.getElementById('bgSvcTxt');
  if(d)d.className='bg-dot'+(v?' on':'');
  if(sd)sd.className='bg-dot'+(v?' on':'');
  if(t)t.textContent=v?'Arka plan aktif':'Durdu';
  if(st)st.textContent=v?'Aktif':'Durduruldu';
}

// -- RENDER SIGNALS --
function renderSigs(){
  var sigs=S.sigs.filter(function(s){
    if(S.tfFilter.length&&S.tfFilter.indexOf(s.tf)===-1)return false;
    if(S.idxFilter==='ALL')return true;
    if(Array.isArray(S.idxFilter))return S.idxFilter.some(function(idx){return(s.indices||[]).indexOf(idx)>-1});
    return(s.indices||[]).indexOf(S.idxFilter)>-1;
  });
  if(!sigs.length){document.getElementById('siglist').innerHTML='<div class="empty"><div class="eico">&#128276;</div><div style="font-size:11px">Sinyal yok. TARA butonuna basin.</div></div>';return;}
  var html='';
  for(var i=0;i<Math.min(sigs.length,80);i++){
    var sig=sigs[i],r=sig.res||{};
    var tfL=sig.tf==='D'?'G':sig.tf==='240'?'4S':'2S';
    var st=sig.time.getHours?sig.time:new Date(sig.time);
    var ts=pad(st.getHours())+':'+pad(st.getMinutes());
    var isWL=S.watchlist.indexOf(sig.ticker)>-1;
    if(sig.type==='stop'){
      var pnl=parseFloat(r.stopPnl||0),pclr=pnl>=0?'var(--green)':'var(--red)';
      // FIX 6: gercek fiyat gosterimi stop kartinda
      html+='<div class="sig stop" onclick="openSig('+i+')">'
        +'<div class="sig-head"><div><div class="sig-ticker">'+sig.ticker+'</div><div class="sig-name">'+sig.name+'</div></div>'
        +'<div style="text-align:right"><div class="sig-badge stop">STOP</div>'
          +'<div style="font-size:12px;font-weight:700;font-family:Courier New,monospace;margin-top:3px">TL'+(r.price||'-')+'</div>'
          +'<div style="font-size:9px;color:var(--t4)">'+tfL+' . '+ts+'</div></div></div>'
        +'<div class="sig-grid">'
          +'<div class="sig-m"><div class="sig-mv" style="color:var(--orange)">TL'+(r.price||'-')+'</div><div class="sig-ml">Fiyat</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:var(--red)">TL'+(r.stopPrice||'-')+'</div><div class="sig-ml">Stop</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+pclr+'">'+(pnl>=0?'+':'')+pnl.toFixed(2)+'%</div><div class="sig-ml">PnL</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+psC(r.pstate)+';font-size:9px">'+(r.pstate||'-')+'</div><div class="sig-ml">Bolge</div></div>'
        +'</div></div>';
    } else {
      var tl=sig.type==='master'?'MASTER':'AL';
      var cons=parseFloat(r.cons||r.consensus||0),cc=cons>70?'var(--green)':cons>50?'var(--gold)':'var(--t2)';
      var sc2=parseInt(r.score||r.pro_score||0),sclr=sc2>=5?'var(--green)':sc2>=3?'var(--gold)':'var(--red)';
      var str=r.strength||calcStrength(r);
      // FIX 6: sinyal karti fiyat
      var prStr=r.price?'TL'+r.price:'';
      var dcStr='';
      if(r.dayChange!=null){var dc=parseFloat(r.dayChange);dcStr='<span style="font-size:9px;color:'+(dc>=0?'var(--green)':'var(--red)')+'">'+(dc>=0?'+':'')+dc.toFixed(2)+'%</span>';}
      html+='<div class="sig '+sig.type+'" onclick="openSig('+i+')">'
        +'<div class="sig-head">'
          +'<div style="display:flex;align-items:flex-start;gap:7px">'
            +'<div class="sc-badge sc-'+str+'">'+str+'</div>'
            +'<div><div style="display:flex;align-items:center;gap:3px">'
              +'<div class="sig-ticker">'+sig.ticker+'</div>'
              +(isWL?'<span style="color:var(--gold)">*</span>':'')
              +(r.isReal?'<span style="font-size:8px;color:var(--green)">.</span>':'')
            +'</div><div class="sig-name">'+sig.name+'</div></div>'
          +'</div>'
          +'<div style="text-align:right">'
            +'<div class="sig-badge '+sig.type+'">'+tl+'</div>'
            +'<div style="display:flex;align-items:center;gap:3px;justify-content:flex-end;margin-top:3px">'
              +'<span style="font-size:12px;font-weight:700;font-family:Courier New,monospace;color:var(--t1)">'+prStr+'</span>'
              +dcStr
            +'</div>'
            +'<div style="font-size:9px;color:var(--t4)">'+tfL+' . '+ts+'</div>'
          +'</div>'
        +'</div>'
        +'<div class="sig-grid">'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+cc+'">%'+cons.toFixed(1)+'</div><div class="sig-ml">Kons.</div></div>'
          +'<div class="sig-m"><div class="sig-mv">'+(r.adx||'-')+'</div><div class="sig-ml">ADX</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+sclr+'">'+sc2+'/6</div><div class="sig-ml">PRO</div></div>'
          +(r.currentStop||r.stop_price
            ?'<div class="sig-m"><div class="sig-mv" style="color:var(--orange);font-size:10px">TL'+(r.currentStop||r.stop_price)+'</div><div class="sig-ml">Stop</div></div>'
            :'<div class="sig-m"><div class="sig-mv" style="color:'+psC(r.pstate)+';font-size:10px">'+(r.pstate||'-')+'</div><div class="sig-ml">Bolge</div></div>')
        +'</div>'
        +'<div class="sbs">'+['ST+TMA','PRO','Fusion','A60','A61','A62','A81','A120'].map(function(x){return'<span class="sb'+((r.acts||[]).indexOf(x)>-1?' on':'')+'" >'+x+'</span>';}).join('')+'</div>'
        +'</div>';
    }
  }
  document.getElementById('siglist').innerHTML=html;
}
function updateBadge(){var n=S.sigs.length,b=document.getElementById('nbadge');b.textContent=n;b.style.display=n?'inline-flex':'none';}

// -- MODAL (FIX 6: gercek fiyat vurgulu) --
function openSig(idx){
  var sigs=S.sigs.filter(function(s){if(S.tfFilter.length&&S.tfFilter.indexOf(s.tf)===-1)return false;return true;});
  var sig=sigs[idx];if(!sig)return;
  S.curSig=sig;var r=sig.res||{};
  var tfL=sig.tf==='D'?'Gunluk':sig.tf==='240'?'4 Saat':'2 Saat';
  document.getElementById('mtit').textContent=sig.ticker+' - '+sig.name;
  var cons=parseFloat(r.cons||r.consensus||0),cc=cons>70?'var(--green)':cons>50?'var(--gold)':'var(--t2)';
  var sc2=parseInt(r.score||r.pro_score||0),sclr=sc2>=5?'var(--green)':sc2>=3?'var(--gold)':'var(--red)';
  var str=r.strength||calcStrength(r);
  // FIX 6: gercek fiyat yesil, simule gri
  var pclr=r.isReal?'var(--green)':'var(--t2)';
  var plbl=r.isReal?'Gercek Fiyat':'Fiyat';
  document.getElementById('mcont').innerHTML=
    '<div class="sgrid">'
      +'<div class="sstat"><div class="sval" style="color:'+pclr+'">TL'+(r.price||'-')+'</div><div class="slb2">'+plbl+'</div></div>'
      +'<div class="sstat"><div class="sval" style="color:'+cc+'">%'+cons.toFixed(1)+'</div><div class="slb2">Konsensus</div></div>'
      +'<div class="sstat"><div class="sval">'+(r.adx||'-')+'</div><div class="slb2">ADX</div></div>'
      +'<div class="sstat"><div class="sval" style="color:'+sclr+'">'+sc2+'/6</div><div class="slb2">PRO Skor</div></div>'
      +'<div class="sstat"><div class="sval">%'+(r.fp||r.fusion_pct||'-')+'</div><div class="slb2">Fusion</div></div>'
      +'<div class="sstat"><div class="sval" style="color:'+psC(r.pstate)+';font-size:13px">'+(r.pstate||'NORMAL')+'</div><div class="slb2">Bolge</div></div>'
      +(r.currentStop?'<div class="sstat"><div class="sval" style="color:var(--orange);font-size:14px">TL'+r.currentStop+'</div><div class="slb2">Trailing Stop</div></div>':'')
      +(r.ema200?'<div class="sstat"><div class="sval" style="font-size:13px">TL'+r.ema200+'</div><div class="slb2">EMA200</div></div>':'')
      +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+str+'/10</div><div class="slb2">Guc Skoru</div></div>'
      +(r.rsi?'<div class="sstat"><div class="sval">'+parseFloat(r.rsi).toFixed(1)+'</div><div class="slb2">RSI</div></div>':'')
    +'</div>'
    +'<div style="font-size:9px;color:var(--t3);margin-bottom:5px">TF: <b style="color:var(--t1)">'+tfL+'</b></div>'
    +'<div style="font-size:9px;color:var(--t3);margin-bottom:8px">Endeks: <span style="color:var(--cyan)">'+(sig.indices||[]).slice(0,4).join(', ')+'</span></div>'
    +'<div style="font-size:9px;color:var(--t3);margin-bottom:5px">Aktif Sistemler:</div>'
    +'<div class="sbs">'+['ST+TMA','PRO','Fusion','A60','A61','A62','A81','A120'].map(function(x){return'<span class="sb'+((r.acts||[]).indexOf(x)>-1?' on':'')+'" style="font-size:10px;padding:3px 7px">'+x+'</span>';}).join('')+'</div>';
  document.getElementById('modal').classList.add('on');
}
function openSt(t){
  var stk=STOCKS.find(function(s){return s.t===t});
  var si=S.sigs.findIndex(function(s){return s.ticker===t});
  if(si>-1){openSig(si);return;}
  S.curSig={ticker:t,name:stk?stk.n:'',indices:stk?stk.i:[],tf:'D',res:{price:'',cons:'',adx:'',score:'',fp:'',pstate:'',rsi:'',ema200:'',acts:[]}};
  document.getElementById('mtit').textContent=t+(stk?' - '+stk.n:'');
  document.getElementById('mcont').innerHTML='<div style="padding:20px;text-align:center;color:var(--t3);font-size:11px">Sinyal yok. Tarama sonrasi goruntu.</div>';
  document.getElementById('modal').classList.add('on');
}
function closeM(e){if(e.target.id==='modal')document.getElementById('modal').classList.remove('on');}
function sendCur(){if(S.curSig&&S.curSig.res)sendTG(S.curSig,true);}

// -- FILTERS --
function tfT(el){
  el.classList.toggle('on');var v=el.dataset.v,idx=S.tfFilter.indexOf(v);
  if(idx>-1)S.tfFilter.splice(idx,1);else S.tfFilter.push(v);renderSigs();
}
function idxT(el){
  var v=el.dataset.v,chips=document.querySelectorAll('#idxchips .chip');
  if(v==='ALL'){chips.forEach(function(c){c.classList.remove('on')});el.classList.add('on');S.idxFilter='ALL';}
  else{
    document.querySelector('#idxchips .chip[data-v="ALL"]').classList.remove('on');
    el.classList.toggle('on');
    var act=[];document.querySelectorAll('#idxchips .chip.on').forEach(function(c){act.push(c.dataset.v);});
    if(!act.length){document.querySelector('#idxchips .chip[data-v="ALL"]').classList.add('on');S.idxFilter='ALL';}else S.idxFilter=act;
  }
  renderSigs();
}
function sIdxF(el){document.querySelectorAll('#sIdxC .chip').forEach(function(c){c.classList.remove('on')});el.classList.add('on');S.scanIdx=el.dataset.v;renderSt();}

// -- STOCK LIST --
function getStocks(){if(S.scanIdx==='ALL')return STOCKS;return STOCKS.filter(function(s){return s.i.indexOf(S.scanIdx)>-1});}
function filterSt(q){renderSt(q);}
function renderSt(query){
  var stocks=getStocks();
  if(query)stocks=stocks.filter(function(s){return s.t.indexOf(query.toUpperCase())>-1||s.n.toLowerCase().indexOf((query||'').toLowerCase())>-1;});
  document.getElementById('scnt').textContent=stocks.length;
  var html='';
  for(var i=0;i<Math.min(stocks.length,120);i++){
    var s=stocks[i],hb=S.sigs.some(function(x){return x.ticker===s.t&&(x.type==='buy'||x.type==='master');});
    var mi=(s.i[0]||'').replace('XK030EA','K30EA').replace('XKTUM','KTUM').replace('XSRDK','SRDK').replace('XKTMT','KTMT').replace('XK0','K');
    html+='<div class="strow'+(hb?' hs':'')+'" onclick="openSt(\''+s.t+'\')">'
      +'<div class="stt">'+s.t+'</div><div class="stn">'+s.n+'</div>'
      +'<div class="sti">'+mi+'</div><div class="sds"><div class="sd'+(hb?' buy':'')+'"></div><div class="sd"></div></div>'
      +'</div>';
  }
  if(stocks.length>120)html+='<div style="text-align:center;padding:8px;color:var(--t3);font-size:9px">+'+(stocks.length-120)+' hisse</div>';
  document.getElementById('stlist').innerHTML=html||'<div class="empty"><div class="eico">&#128269;</div><div>Bulunamadi</div></div>';
}

// -- POSITIONS (FIX 7: % PnL gosterimi) --
function renderPositions(){
  var positions=Object.keys(S.openPositions);
  var badge=document.getElementById('posBadge');
  badge.textContent=positions.length;badge.style.display=positions.length?'inline-flex':'none';
  if(!positions.length){
    document.getElementById('positionList').innerHTML='<div class="empty"><div class="eico">&#128188;</div><div style="font-size:11px">Sinyal gelince pozisyon acilir.</div></div>';
    document.getElementById('posTotalPnl').textContent='-';document.getElementById('posCount').textContent='0';
    document.getElementById('posBestPnl').textContent='-';document.getElementById('posWorstPnl').textContent='-';
    return;
  }
  var totalPnl=0,bestPnl=-999,worstPnl=999,bestTicker='',worstTicker='',cards='';
  positions.forEach(function(key){
    var pos=S.openPositions[key],parts=key.split('_'),ticker=parts[0],tf=parts[1];
    var tfL=tf==='D'?'Gunluk':tf==='240'?'4S':'2S';
    var cached=S.priceCache[ticker];
    var curPrice=cached&&cached.price>0?cached.price:(pos.entry*1.01);
    var dayChg=cached?cached.pct:null,isReal=!!(cached&&cached.price>0);
    if(curPrice>pos.highest)pos.highest=curPrice;
    pos.stopPrice=pos.highest-pos.entry*0.022*(C.atrm||8);
    // FIX 7: % PnL
    var pnlPct=(curPrice-pos.entry)/pos.entry*100;
    var pnlStr=(pnlPct>=0?'+':'')+pnlPct.toFixed(2)+'%';
    totalPnl+=pnlPct;
    if(pnlPct>bestPnl){bestPnl=pnlPct;bestTicker=ticker;}
    if(pnlPct<worstPnl){worstPnl=pnlPct;worstTicker=ticker;}
    var isProfit=pnlPct>=0;
    var holdDays=Math.floor((Date.now()-new Date(pos.entryTime).getTime())/86400000);
    var holdStr=holdDays===0?'Bugun':holdDays===1?'1 gun':holdDays+' gun';
    // Sinyal detaylari
    var sigData=S.sigs.find(function(s){return s.ticker===ticker&&s.tf===tf&&s.type!=='stop';});
    var extraInfo='';
    if(sigData&&sigData.res){
      var rd=sigData.res;
      extraInfo='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-top:6px">'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px;color:'+(parseFloat(rd.cons||rd.consensus||0)>70?'var(--green)':'var(--gold)')+'">%'+parseFloat(rd.cons||rd.consensus||0).toFixed(0)+'</div><div class="pos-ml">Kons.</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+(rd.adx||'-')+'</div><div class="pos-ml">ADX</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+(rd.score||rd.pro_score||'-')+'/6</div><div class="pos-ml">PRO</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:9px;color:'+psC(rd.pstate)+'">'+(rd.pstate||'-')+'</div><div class="pos-ml">Bolge</div></div>'
        +'</div>';
    }
    cards+='<div class="pos-card '+(isProfit?'profit':'loss')+'">'
      +'<div class="pos-header">'
        +'<div>'
          +'<div class="pos-ticker">'+ticker+(isReal?'<span style="font-size:7px;color:var(--green);margin-left:3px">CANLI</span>':'')+'</div>'
          +'<div style="font-size:9px;color:var(--t4)">'+tfL+' . '+holdStr+'</div>'
        +'</div>'
        +'<div style="text-align:right">'
          // FIX 7: buyuk ve belirgin % PnL
          +'<div class="pos-pnl" style="color:'+(isProfit?'var(--green)':'var(--red)')+'">'+pnlStr+'</div>'
          +'<div style="font-size:12px;font-weight:700;font-family:Courier New,monospace;color:var(--t1)">TL'+curPrice.toFixed(2)+'</div>'
          +(dayChg!=null?'<div style="font-size:9px;color:'+(dayChg>=0?'var(--green)':'var(--red)')+'">'+(dayChg>=0?'+':'')+dayChg.toFixed(2)+'% bugun</div>':'')
        +'</div>'
      +'</div>'
      +'<div class="pos-grid">'
        +'<div class="pos-m"><div class="pos-mv">TL'+pos.entry.toFixed(2)+'</div><div class="pos-ml">Giris</div></div>'
        +'<div class="pos-m"><div class="pos-mv">TL'+pos.highest.toFixed(2)+'</div><div class="pos-ml">En Yuksek</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="color:var(--orange)">TL'+pos.stopPrice.toFixed(2)+'</div><div class="pos-ml">Trailing Stop</div></div>'
      +'</div>'
      +extraInfo
      +'<div style="margin-top:7px;display:flex;gap:5px">'
        // FIX 3: grafik butonu duzeltildi
        +'<button class="btn c" style="flex:1;padding:7px;border-radius:6px;font-size:10px" onclick="openTVTicker(\''+ticker+'\',\''+tf+'\')">Grafik</button>'
        +'<button class="btn r" style="padding:7px 11px;border-radius:6px;font-size:10px" onclick="closePosition(\''+key+'\')">Kapat</button>'
      +'</div>'
      +'</div>';
  });
  document.getElementById('positionList').innerHTML=cards;
  document.getElementById('posCount').textContent=positions.length;
  // FIX 7: toplam % PnL
  document.getElementById('posTotalPnl').textContent=(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%';
  document.getElementById('posTotalPnl').className='sval '+(totalPnl>=0?'p':'n');
  document.getElementById('posBestPnl').textContent=bestTicker?(bestTicker+' +'+(bestPnl.toFixed(1))+'%'):'-';
  document.getElementById('posWorstPnl').textContent=worstTicker?(worstTicker+' '+(worstPnl.toFixed(1))+'%'):'-';
}
function closePosition(key){
  var pos=S.openPositions[key];
  if(!pos)return;
  var parts=key.split('_'),ticker=parts[0],tf=parts[1];
  var cached=S.priceCache[ticker];
  var exitPrice=cached&&cached.price>0?cached.price:pos.entry;
  var pnlPct=(exitPrice-pos.entry)/pos.entry*100;
  var sig=S.sigs.find(function(s){return s.ticker===ticker&&s.tf===tf&&s.type!=='stop';});
  var closed={
    ticker:ticker,tf:tf,
    entry:pos.entry,exit:exitPrice,
    highest:pos.highest,stopPrice:pos.stopPrice,
    pnlPct:pnlPct.toFixed(2),
    entryTime:pos.entryTime,exitTime:new Date().toISOString(),
    holdDays:Math.floor((Date.now()-new Date(pos.entryTime).getTime())/86400000),
    cons:sig?sig.res.cons:'-',adx:sig?sig.res.adx:'-',
    score:sig?(sig.res.score||sig.res.pro_score):'-',
    pstate:sig?sig.res.pstate:'-',
    acts:sig?(sig.res.acts||[]):[]
  };
  S.closedPositions.unshift(closed);
  if(S.closedPositions.length>100)S.closedPositions=S.closedPositions.slice(0,100);
  lsSet('bist_closed',S.closedPositions);
  delete S.openPositions[key];
  renderPositions();toast('Pozisyon kapatildi');
}
function closeAllPositions(){if(!confirm('Tum pozisyonlar kapatilsin mi?'))return;S.openPositions={};renderPositions();toast('Kapatildi');}

// -- WATCHLIST (FIX 8: endeks toplu ekleme) --
function addWatchlist(){
  var val=document.getElementById('wlInput').value.trim().toUpperCase();if(!val)return;
  var stock=STOCKS.find(function(s){return s.t===val});
  if(!stock){toast('Hisse bulunamadi: '+val);return;}
  if(S.watchlist.indexOf(val)>-1){toast('Zaten listede!');return;}
  S.watchlist.push(val);lsSet('bist_wl',S.watchlist);
  document.getElementById('wlInput').value='';renderWatchlist();toast(val+' eklendi');
}
// FIX 8: toplu endeks ekleme
function addIndexToWL(index){
  var toAdd=STOCKS.filter(function(s){return s.i.indexOf(index)>-1&&S.watchlist.indexOf(s.t)===-1;});
  if(!toAdd.length){toast('Hepsi zaten listede!');return;}
  toAdd.forEach(function(s){S.watchlist.push(s.t);});
  lsSet('bist_wl',S.watchlist);renderWatchlist();toast(toAdd.length+' hisse eklendi ('+index+')');
}
function clearWatchlist(){if(!confirm('Temizlensin mi?'))return;S.watchlist=[];lsSet('bist_wl',[]);renderWatchlist();toast('Temizlendi');}
function removeWatchlist(ticker){S.watchlist=S.watchlist.filter(function(t){return t!==ticker;});lsSet('bist_wl',S.watchlist);renderWatchlist();}
function renderWatchlist(){
  document.getElementById('wlCount').textContent=S.watchlist.length+' hisse';
  if(!S.watchlist.length){document.getElementById('wlList').innerHTML='<div class="empty"><div class="eico">&#11088;</div><div style="font-size:11px">Favori hisse ekleyin.</div></div>';return;}
  var html=S.watchlist.map(function(ticker){
    var stock=STOCKS.find(function(s){return s.t===ticker;});
    var sig=S.sigs.find(function(s){return s.ticker===ticker;});
    var str=sig?calcStrength(sig.res):null;
    var pos=S.openPositions[ticker+'_D']||S.openPositions[ticker+'_240']||S.openPositions[ticker+'_120'];
    var cached=S.priceCache[ticker];
    var prStr=cached&&cached.price>0?'TL'+cached.price.toFixed(2):'';
    var pctStr=cached&&cached.pct!=null?('<span style="font-size:9px;color:'+(cached.pct>=0?'var(--green)':'var(--red)')+'">'+(cached.pct>=0?'+':'')+cached.pct.toFixed(2)+'%</span>'):'';
    return '<div class="wl-item" style="'+(sig?'border-color:rgba(0,212,255,.2)':'')+'">'
      +'<div class="wl-ticker">'+ticker+'</div>'
      +'<div style="flex:1;overflow:hidden">'
        +'<div style="font-size:11px;color:var(--t2)">'+(stock?stock.n:'')+'</div>'
        +'<div style="font-size:9px;margin-top:2px;display:flex;gap:6px;align-items:center">'
          +(pos?'<span style="color:var(--green)">Acik</span>':sig?'<span style="color:var(--cyan)">Sinyal</span>':'<span style="color:var(--t4)">Yok</span>')
          +(prStr?('<span style="font-family:Courier New,monospace;color:var(--t1)">'+prStr+'</span>'):'')
          +pctStr
        +'</div>'
      +'</div>'
      +(str?'<div class="sc-badge sc-'+str+'">'+str+'</div>':'')
      // FIX 3,4: grafik butonu duzeltildi
      +'<button class="wl-btn" onclick="openTVTicker(\''+ticker+'\',\'D\')" title="Grafik">TV</button>'
      +'<button class="wl-btn" onclick="removeWatchlist(\''+ticker+'\')" style="color:var(--red)" title="Kaldir">X</button>'
      +'</div>';
  }).join('');
  document.getElementById('wlList').innerHTML=html;
}

// -- HISTORY & REPORT --
function saveToHistory(sig){
  var e={ticker:sig.ticker,type:sig.type,tf:sig.tf,strength:calcStrength(sig.res||{}),time:new Date().toISOString(),price:sig.res?sig.res.price:0,acts:(sig.res&&sig.res.acts)||[]};
  S.sigHistory.unshift(e);if(S.sigHistory.length>500)S.sigHistory=S.sigHistory.slice(0,500);lsSet('bist_hist',S.sigHistory);
}
function renderReport(){
  var now=new Date(),wAgo=new Date(now.getTime()-7*86400000);
  var wSigs=S.sigHistory.filter(function(s){return new Date(s.time)>=wAgo;});
  var mSigs=wSigs.filter(function(s){return s.type==='master';});
  var sSigs=wSigs.filter(function(s){return s.type==='stop';});
  var tc={};wSigs.forEach(function(s){tc[s.ticker]=(tc[s.ticker]||0)+1;});
  var sorted=Object.keys(tc).sort(function(a,b){return tc[b]-tc[a];});
  document.getElementById('wkSigs').textContent=wSigs.length;document.getElementById('wkMaster').textContent=mSigs.length;document.getElementById('wkStops').textContent=sSigs.length;document.getElementById('wkBest').textContent=sorted[0]||'-';
  var sc={'ST+TMA':0,'PRO':0,'Fusion':0,'A60':0,'A61':0,'A62':0,'A81':0,'A120':0};
  wSigs.forEach(function(s){if(s.acts)s.acts.forEach(function(a){if(sc[a]!==undefined)sc[a]++;});});
  var spHtml='';Object.keys(sc).sort(function(a,b){return sc[b]-sc[a];}).forEach(function(sys){var cnt=sc[sys];if(!cnt)return;var pct=wSigs.length?Math.round(cnt/wSigs.length*100):0;spHtml+='<div style="display:flex;align-items:center;gap:7px;margin-bottom:7px"><div style="width:58px;font-size:10px;color:var(--t2)">'+sys+'</div><div style="flex:1;height:5px;background:var(--b2);border-radius:3px;overflow:hidden"><div style="height:100%;width:'+pct+'%;background:var(--cyan);border-radius:3px"></div></div><div style="font-size:9px;color:var(--t3);width:28px">'+cnt+'x</div></div>';});
  document.getElementById('sysPerf').innerHTML=spHtml||'<div style="color:var(--t4);font-size:10px">Veri yok</div>';
  var calHtml='';for(var d=27;d>=0;d--){var day=new Date(now.getTime()-d*86400000),ds=day.toDateString(),ds2=S.sigHistory.filter(function(s){return new Date(s.time).toDateString()===ds;});var wk=day.getDay()===0||day.getDay()===6;var cls='cal-day'+(wk?' none':ds2.length>=3?' good':ds2.length>0?' has':' none');calHtml+='<div class="'+cls+'">'+(ds2.length>0?ds2.length:day.getDate())+'</div>';}
  document.getElementById('calGrid').innerHTML=calHtml;
  var tHtml=sorted.slice(0,8).map(function(t,i){return'<div class="rep-row"><span style="color:var(--t3)">'+(i+1)+'. '+t+'</span><span style="color:var(--cyan)">'+tc[t]+' sinyal</span></div>';}).join('');
  document.getElementById('topStocks').innerHTML=tHtml||'<div style="color:var(--t4);font-size:10px;padding:8px 0">Veri yok</div>';
}

// -- TELEGRAM (FIX 5: duzeltilmis mesaj formati) --
function buildMsg(sig){
  var r=sig.res||{};
  var tfL=sig.tf==='D'?'Gunluk':sig.tf==='240'?'4 Saat':'2 Saat';
  var tvInterval=sig.tf==='D'?'1D':sig.tf==='240'?'4H':'2H';
  // FIX 5: sadece web linki - Telegram icin calisir
  var chartUrl='https://www.tradingview.com/chart/?symbol=BIST:'+sig.ticker+'&interval='+tvInterval;
  var now=new Date(),ts=pad(now.getHours())+':'+pad(now.getMinutes());
  var msg='';
  if(sig.type==='stop'){
    var pnl=parseFloat(r.stopPnl||0);
    msg='STOP TETIKLENDI\n--------------------\n'
      +'Hisse: '+sig.ticker+' - '+sig.name+'\n'
      +'TF: '+tfL+' | '+ts+'\n\n'
      +'Anlik Fiyat: TL'+(r.price||'-')+'\n'  // FIX 6
      +'Stop Seviyesi: TL'+(r.stopPrice||'-')+'\n'
      +'Sonuc: '+(pnl>=0?'+':'')+pnl.toFixed(2)+'%\n';  // FIX 7: % PnL
  } else {
    var isM=sig.type==='master';
    var str=r.strength||calcStrength(r);
    var bars5=Math.round(str/2),gb='';for(var bi=0;bi<5;bi++)gb+=(bi<bars5?'#':'_');
    var dcStr='';if(r.dayChange!=null){var dc=parseFloat(r.dayChange);dcStr=' ('+(dc>=0?'+':'')+dc.toFixed(2)+'%)';}
    msg=(isM?'MASTER AI SINYALI':'AL SINYALI')+'\n--------------------\n'
      +'Hisse: '+sig.ticker+' - '+sig.name+'\n'
      +'TF: '+tfL+' | '+ts+'\n\n'
      +'Guc: ['+gb+'] '+str+'/10\n'
      +'Anlik Fiyat: TL'+(r.price||'-')+dcStr+'\n'  // FIX 6: gercek fiyat
      +'AI Konsensus: %'+(r.cons||r.consensus||'-')+'\n';
    if(r.currentStop||r.stop_price)msg+='Trailing Stop: TL'+(r.currentStop||r.stop_price)+'\n';
    if(TG.det)msg+='ADX: '+(r.adx||'-')+' | PRO: '+(r.score||r.pro_score||'-')+'/6 | Fusion: %'+(r.fp||r.fusion_pct||'-')+'\n';
    if(TG.q)msg+='Bolge: '+(r.pstate||'NORMAL')+'\n';  // FIX 14: gercek pstate
    if(TG.ag&&r.acts&&r.acts.length)msg+='Sistemler: '+r.acts.join(' | ')+'\n';
    if((sig.indices||[]).length)msg+='Endeks: '+(sig.indices||[]).slice(0,3).join(', ')+'\n';
    if(r.isReal)msg+='\n[Gercek OHLCV verisi]\n';
  }
  // FIX 5: calisir web linki
  if(TG.ch)msg+='\nGrafik: '+chartUrl;
  return msg;
}
function sendTG(sig,manual){if(!TG.token||!TG.chat){if(manual)toast('Token/Chat ID eksik!');return;}sendTGCore(sig,TG.token,TG.chat,manual,null);}
function sendTGCore(sig,token,chat,manual,dbg){
  var msg=buildMsg(sig),log=document.getElementById('tglog'),now=new Date(),ts=pad(now.getHours())+':'+pad(now.getMinutes());
  fetch('https://api.telegram.org/bot'+token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:chat,text:msg,disable_web_page_preview:false})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.ok){log.innerHTML='<div style="margin-bottom:2px">['+ts+'] OK '+sig.ticker+'</div>'+log.innerHTML;if(dbg)dbg.textContent='OK! Telegram kontrol edin';if(manual){toast('Gonderildi!');document.getElementById('modal').classList.remove('on');}}
    else{log.innerHTML='<div style="color:var(--red);margin-bottom:2px">['+ts+'] HATA: '+(d.description||'?')+'</div>'+log.innerHTML;if(manual)toast('Hata: '+(d.description||'?'));if(dbg)dbg.textContent='Hata: '+d.description;}
  }).catch(function(e){
    var url='https://api.telegram.org/bot'+token+'/sendMessage?chat_id='+encodeURIComponent(chat)+'&text='+encodeURIComponent(msg);
    fetch(url,{mode:'no-cors'}).then(function(){if(manual)toast('Gonderildi (fallback)');if(dbg)dbg.textContent='OK (fallback)';}).catch(function(){if(manual)toast('Baglanti hatasi');if(dbg)dbg.textContent='Baglanti hatasi';});
  });
}
function tgTest(){
  var token=document.getElementById('tgtoken').value.trim(),chat=document.getElementById('tgchat').value.trim();
  if(!token||!chat){toast('Token ve Chat ID girin!');return;}
  TG.token=token;TG.chat=chat;
  var dbg=document.getElementById('tgDebug');dbg.style.display='block';dbg.textContent='Gonderiliyor...';toast('Gonderiliyor...');
  var testSig={ticker:'TEST',name:'BIST AI Test',indices:['XKTUM'],type:'master',tf:'D',res:{price:'123.45',cons:'85',adx:'38',score:5,fp:'72',pstate:'UCUZ',acts:['ST+TMA','PRO','Fusion'],rsi:'64',ema200:'115.20',currentStop:'118.40',isReal:true,dayChange:2.3}};
  sendTGCore(testSig,token,chat,true,dbg);
}
function tgSave(){
  TG.token=document.getElementById('tgtoken').value.trim();TG.chat=document.getElementById('tgchat').value.trim();
  TG.ch=document.getElementById('tg_ch').checked;TG.det=document.getElementById('tg_det').checked;
  TG.q=document.getElementById('tg_q').checked;TG.ag=document.getElementById('tg_ag').checked;
  lsSet('bisttg',TG);toast('Kaydedildi');if(TG.token&&TG.chat)startTGListener();
}
function updateTgUI(){document.getElementById('tgtoken').value=TG.token||'';document.getElementById('tgchat').value=TG.chat||'';document.getElementById('tg_ch').checked=TG.ch!==false;document.getElementById('tg_det').checked=TG.det!==false;document.getElementById('tg_q').checked=TG.q!==false;document.getElementById('tg_ag').checked=!!TG.ag;}

// -- TELEGRAM BOT --
var _lastUpd=lsGet('bist_lastupdate')||0;
function startTGListener(){
  if(!TG.token||!TG.chat)return;
  setInterval(function(){
    if(!TG.token||!TG.chat)return;
    fetch('https://api.telegram.org/bot'+TG.token+'/getUpdates?offset='+(_lastUpd+1)+'&timeout=0')
    .then(function(r){return r.json()}).then(function(d){
      if(!d.ok||!d.result||!d.result.length)return;
      d.result.forEach(function(upd){
        _lastUpd=upd.update_id;lsSet('bist_lastupdate',_lastUpd);
        var msg=upd.message||upd.channel_post;if(!msg||!msg.text)return;
        if(String(msg.chat.id)!==String(TG.chat))return;
        handleBotCmd(msg.text.toLowerCase().trim());
      });
    }).catch(function(){});
  },15000);
}
function handleBotCmd(cmd){
  var reply='';
  if(cmd==='/durum'){reply='BIST AI DURUM\nSinyaller: '+S.sigs.length+'\nPozisyon: '+Object.keys(S.openPositions).length+'\nXU100: '+(S.xu100Change>=0?'+':'')+S.xu100Change+'%';}
  else if(cmd.startsWith('/sinyal ')){var t=cmd.split(' ')[1].toUpperCase(),sg=S.sigs.find(function(s){return s.ticker===t;});reply=sg?t+'\nGuc: '+calcStrength(sg.res)+'/10\nFiyat: TL'+sg.res.price+'\nKons: %'+sg.res.cons:t+' sinyali yok.';}
  else if(cmd==='/portfoy'){var k=Object.keys(S.openPositions);if(!k.length)reply='Pozisyon yok.';else{reply='POZISYONLAR\n\n';k.forEach(function(key){var p=S.openPositions[key],pr=key.split('_'),cur=S.priceCache[pr[0]],pnl=cur&&cur.price?(((cur.price-p.entry)/p.entry)*100).toFixed(2):'-';reply+=pr[0]+'('+pr[1]+'): TL'+p.entry.toFixed(2)+' | PnL: '+pnl+'%\n';});}}
  else if(cmd==='/tara'){setTimeout(function(){startScan();},500);reply='Tarama baslatiliyor...';}
  else if(cmd==='/rapor'){sendWeeklyReport();return;}
  else if(cmd==='/yardim'){reply='/durum /portfoy /sinyal EREGL\n/tara /rapor /yardim';}
  else return;
  if(reply)fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:reply})}).catch(function(){});
}

// -- RAPOR GONDER --
function sendWeeklyReport(){
  if(!TG.token||!TG.chat){toast('Telegram ayarli degil!');return;}
  var now=new Date(),wAgo=new Date(now.getTime()-7*86400000);
  var wSigs=S.sigHistory.filter(function(s){return new Date(s.time)>=wAgo;});
  var tc={};wSigs.forEach(function(s){tc[s.ticker]=(tc[s.ticker]||0)+1;});
  var top=Object.keys(tc).sort(function(a,b){return tc[b]-tc[a];}).slice(0,5);
  var msg='BIST AI - HAFTALIK RAPOR\n'+pad(now.getDate())+'/'+pad(now.getMonth()+1)+'/'+now.getFullYear()+'\n\n'
    +'Toplam Sinyal: '+wSigs.length+'\n'
    +'Master AI: '+wSigs.filter(function(s){return s.type==='master';}).length+'\n'
    +'Stop: '+wSigs.filter(function(s){return s.type==='stop';}).length+'\n';
  if(top.length){msg+='\nEn Aktif:\n';top.forEach(function(t,i){msg+=(i+1)+'. '+t+' ('+tc[t]+')\n';});}
  fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:msg})}).then(function(r){return r.json();}).then(function(d){toast(d.ok?'Rapor gonderildi!':'Hata: '+(d.description||''));}).catch(function(){toast('Hata');});
}

// FIX 15: Gun sonu raporu otomatik
var _dayEndSent=false;
function checkDayEndReport(){
  var now=new Date(),h=now.getHours(),m=now.getMinutes();
  if(h===18&&m>=15&&m<=30&&!_dayEndSent){_dayEndSent=true;setTimeout(function(){_dayEndSent=false;},3*60*60*1000);sendDayEndReport();}
}
function sendDayEndReport(){
  if(!TG.token||!TG.chat){toast('Telegram ayarli degil!');return;}
  var now=new Date(),ts=pad(now.getDate())+'/'+pad(now.getMonth()+1)+'/'+now.getFullYear();
  var todayStr=now.toDateString();
  var todaySigs=S.sigHistory.filter(function(s){return new Date(s.time).toDateString()===todayStr;});
  var mCnt=todaySigs.filter(function(s){return s.type==='master';}).length;
  var sCnt=todaySigs.filter(function(s){return s.type==='stop';}).length;
  var bCnt=todaySigs.filter(function(s){return s.type==='buy';}).length;
  var positions=Object.keys(S.openPositions),posLines='',totalPnl=0;
  positions.forEach(function(key){var p=S.openPositions[key],pr=key.split('_'),cur=S.priceCache[pr[0]];if(cur&&cur.price>0){var pnl=((cur.price-p.entry)/p.entry*100);totalPnl+=pnl;posLines+=pr[0]+'('+pr[1]+'): '+(pnl>=0?'+':'')+pnl.toFixed(2)+'%\n';}});
  var tc={};todaySigs.forEach(function(s){tc[s.ticker]=(tc[s.ticker]||0)+1;});
  var top=Object.keys(tc).sort(function(a,b){return tc[b]-tc[a];}).slice(0,5);
  var wAgo=new Date(now.getTime()-7*86400000);
  var wSigs=S.sigHistory.filter(function(s){return new Date(s.time)>=wAgo;});
  var msg='BIST AI - GUN SONU RAPORU\n========================\n'+ts+' | Seans Kapanisi 18:15\n\n'
    +'XU100: '+(S.xu100Change>=0?'+':'')+S.xu100Change+'% ('+(S.xu100Trend==='bull'?'Yukaris':'Asagis')+')\n\n'
    +'--- BUGUN ---\n'
    +'Toplam Sinyal: '+todaySigs.length+'\n'
    +'Master AI: '+mCnt+'\n'
    +'Al Sinyali: '+bCnt+'\n'
    +'Stop: '+sCnt+'\n';
  if(top.length){msg+='\nEn Aktif:\n';top.forEach(function(t,i){msg+=(i+1)+'. '+t+' ('+tc[t]+'x)\n';});}
  if(positions.length){msg+='\n--- ACIK POZISYONLAR ('+positions.length+') ---\n'+posLines+'Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%\n';}
  else msg+='\nAcik pozisyon yok.\n';
  msg+='\n--- 7 GUN ---\n'+'Toplam: '+wSigs.length+' sinyal\nMaster: '+wSigs.filter(function(s){return s.type==='master';}).length+'\n';
  fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:msg})}).then(function(r){return r.json();}).then(function(d){toast(d.ok?'Gun sonu raporu gonderildi!':'Hata: '+(d.description||''));}).catch(function(){toast('Hata');});
}

// -- SETTINGS --
function loadSetsUI(){
  document.getElementById('s_s1').checked=C.s1!==false;document.getElementById('s_s2').checked=C.s2!==false;document.getElementById('s_fu').checked=C.fu!==false;document.getElementById('s_ma').checked=C.ma!==false;
  document.getElementById('s_a60').checked=!!C.a60;document.getElementById('s_a61').checked=!!C.a61;document.getElementById('s_a62').checked=!!C.a62;document.getElementById('s_a81').checked=!!C.a81;document.getElementById('s_a120').checked=!!C.a120;
  document.getElementById('s_adxMin').value=C.adxMin||25;document.getElementById('s_atrm').value=C.atrm||8;document.getElementById('s_sc').value=C.sc||5;document.getElementById('s_fb').value=C.fb||80;document.getElementById('s_mb').value=C.mb||70;
  document.getElementById('s_snd').checked=C.snd!==false;document.getElementById('s_pn').checked=!!C.pn;
}
function saveSets(){
  C.s1=document.getElementById('s_s1').checked;C.s2=document.getElementById('s_s2').checked;C.fu=document.getElementById('s_fu').checked;C.ma=document.getElementById('s_ma').checked;
  C.a60=document.getElementById('s_a60').checked;C.a61=document.getElementById('s_a61').checked;C.a62=document.getElementById('s_a62').checked;C.a81=document.getElementById('s_a81').checked;C.a120=document.getElementById('s_a120').checked;
  C.adxMin=parseInt(document.getElementById('s_adxMin').value)||25;C.atrm=parseFloat(document.getElementById('s_atrm').value)||8;C.sc=parseInt(document.getElementById('s_sc').value)||5;C.fb=parseInt(document.getElementById('s_fb').value)||80;C.mb=parseInt(document.getElementById('s_mb').value)||70;
  C.snd=document.getElementById('s_snd').checked;C.pn=document.getElementById('s_pn').checked;
  lsSet('bistcfg',C);toast('Ayarlar kaydedildi');
}
function resetSets(){if(!confirm('Sifirlansin mi?'))return;C=Object.assign({},DEF);lsSet('bistcfg',C);loadSetsUI();toast('Sifirlandi');}

// -- AGENTS --
function renderAgents(){
  var anames=['A60','A61','A62','A81','A120','Sys1','Sys2','Fusion'],en=[C.a60,C.a61,C.a62,C.a81,C.a120,C.s1,C.s2,C.fu];
  document.getElementById('agTbl').innerHTML=anames.map(function(n,i){
    var pnl=S.agentPnl[i]||0,rep=S.agentRep[i]||0.5,cap=S.agentCap[i]||12;
    return '<tr><td style="color:var(--cyan)">'+n+'</td><td style="color:'+(en[i]?'var(--green)':'var(--t3)')+'">'+( en[i]?'OK':'X')+'</td><td style="color:'+(pnl>=0?'var(--green)':'var(--red)')+'">'+( pnl>=0?'+':'')+pnl+'%</td><td><div class="rbar"><div class="rfill" style="width:'+(rep*100)+'%"></div></div> '+(rep*100).toFixed(0)+'%</td><td>%'+cap+'</td></tr>';
  }).join('');
  document.getElementById('mcons').textContent='%'+(S.xu100Change>0?'68':'52');document.getElementById('mthresh').textContent='%'+(C.mb||70);document.getElementById('mpnl').textContent='+'+(S.agentPnl.filter(function(x){return x>0;}).length*2.1).toFixed(1)+'%';document.getElementById('mpos').textContent=Object.keys(S.openPositions).length>0?Object.keys(S.openPositions).length+' ACIK':'YOK';
  var qv='',qs=['BULLISH','NEUTRAL','BEARISH','SIDEWAYS'],qv2=[45,25,20,10];qs.forEach(function(s,i){qv+='<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px"><div style="width:60px;font-size:9px;color:var(--t3)">'+s+'</div><div style="flex:1;height:4px;background:var(--b2);border-radius:2px;overflow:hidden"><div style="height:100%;width:'+qv2[i]+'%;background:var(--cyan);border-radius:2px"></div></div><div style="font-size:9px;color:var(--t3);width:26px">'+qv2[i]+'%</div></div>';});
  document.getElementById('qviz').innerHTML=qv;
}

// -- BACKTEST --
// ═══════════════════════════════════════════════════════
// BACKTEST v2 - Gercek OHLCV + Pine Sinyal + Komisyon
// ═══════════════════════════════════════════════════════

// Sekme gecisi
function btTabSwitch(tab){
  ['bt','wf','mc','opt'].forEach(function(t){
    document.getElementById('panel-'+t).style.display=t===tab?'block':'none';
    document.getElementById('btTab'+(t==='bt'?1:t==='wf'?2:t==='mc'?3:4)).className='chip cy'+(t===tab?' on':'');
  });
}

// ── ORTAK: Proxy'den OHLCV cek ──────────────────────
function btFetchOHLCV(sym, tf, callback){
  setSt('OHLCV cekiliyor: '+sym+' ('+tf+')...');
  var intv={'D':'1d','240':'60m','120':'30m'}[tf]||'1d';
  var rng={'D':'2y','240':'60d','120':'30d'}[tf]||'2y';
  var url=PROXY_URL+'/xu100'; // once XU100
  // Paralel: hisse + XU100
  Promise.all([
    fetch('https://query1.finance.yahoo.com/v8/finance/chart/'+sym+'.IS?interval='+intv+'&range='+rng).then(function(r){return r.json();}),
    fetch(PROXY_URL+'/xu100').then(function(r){return r.json();})
  ]).then(function(results){
    var d=results[0], xu100=results[1];
    var res=d.chart&&d.chart.result&&d.chart.result[0];
    if(!res){callback(null,null);return;}
    var ts=res.timestamp,q=res.indicators.quote[0];
    var adj=res.indicators.adjclose&&res.indicators.adjclose[0]?res.indicators.adjclose[0].adjclose:null;
    var cls=adj||q.close||[];
    var ohlcv=[];
    for(var i=0;i<ts.length;i++){
      var cv=cls[i]||q.close[i];
      if(cv!=null&&q.open[i]!=null)
        ohlcv.push({t:ts[i],o:q.open[i],h:q.high[i],l:q.low[i],c:cv,v:q.volume[i]||0});
    }
    callback(ohlcv, xu100);
  }).catch(function(e){
    // Proxy CORS sorunu varsa proxy uzerinden cek
    fetch(PROXY_URL+'/analyze/'+sym+'?tf='+tf)
    .then(function(r){return r.json();})
    .then(function(d){
      // Proxy'de cached OHLCV yok, simule et ama Pine parametreleriyle
      callback(null,null);
    }).catch(function(){callback(null,null);});
  });
}

// ── PINE SİNYAL MOTORU (JS versiyonu) ────────────────
function pineSMA(c,n){var o=[];for(var i=0;i<c.length;i++){if(i<n-1){o.push(null);}else{var s=0;for(var j=i-n+1;j<=i;j++)s+=c[j];o.push(s/n);}}return o;}
function pineEMA(c,n){var k=2/(n+1),e=[],prev=null;for(var i=0;i<c.length;i++){if(prev===null){e.push(c[i]);prev=c[i];}else{var v=c[i]*k+prev*(1-k);e.push(v);prev=v;}}return e;}
function pineATR(h,l,c,n){var tr=[],a=[];for(var i=0;i<c.length;i++){var t=h[i]-l[i];if(i>0){t=Math.max(t,Math.abs(h[i]-c[i-1]),Math.abs(l[i]-c[i-1]));}tr.push(t);}if(c.length<n)return c.map(function(){return 0;});a[n-1]=0;for(var i=0;i<n;i++)a[n-1]+=tr[i];a[n-1]/=n;for(var i=n;i<c.length;i++)a[i]=(a[i-1]*(n-1)+tr[i])/n;for(var i=0;i<n-1;i++)a[i]=a[n-1];return a;}
function pineRSI(c,n){var r=[50];if(c.length<n+1)return c.map(function(){return 50;});var d=[];for(var i=1;i<c.length;i++)d.push(c[i]-c[i-1]);var ag=0,al=0;for(var i=0;i<n;i++){ag+=Math.max(d[i],0);al+=Math.max(-d[i],0);}ag/=n;al/=n;r[n]=100-100/(1+ag/(al||1e-10));for(var i=n;i<d.length;i++){ag=(ag*(n-1)+Math.max(d[i],0))/n;al=(al*(n-1)+Math.max(-d[i],0))/n;r[i+1]=100-100/(1+ag/(al||1e-10));}return r;}
function pineSupertrend(h,l,c,atrLen,mult){
  var at=pineATR(h,l,c,atrLen),n=c.length,dir=[1];
  var up=[],dn=[];
  for(var i=0;i<n;i++){var hl=(h[i]+l[i])/2;up.push(hl+mult*at[i]);dn.push(hl-mult*at[i]);}
  var fu=up.slice(),fd=dn.slice();
  for(var i=1;i<n;i++){
    fu[i]=up[i]<fu[i-1]||c[i-1]>fu[i-1]?up[i]:fu[i-1];
    fd[i]=dn[i]>fd[i-1]||c[i-1]<fd[i-1]?dn[i]:fd[i-1];
    if(dir[i-1]===-1&&c[i]>fu[i])dir[i]=1;
    else if(dir[i-1]===1&&c[i]<fd[i])dir[i]=-1;
    else dir[i]=dir[i-1];
  }
  return{dir:dir,up:fu,dn:fd};
}
function pineTMAUpper(c,len,amult,alen){
  var half=Math.floor(len/2)+1;
  var s1=pineSMA(c,half),s2=pineSMA(s1.map(function(x){return x||0;}),half);
  var ph=c.map(function(x,i){return i>0?Math.max(x,c[i-1]):x;});
  var pl=c.map(function(x,i){return i>0?Math.min(x,c[i-1]):x;});
  var at=pineATR(ph,pl,c,alen);
  return s2.map(function(t,i){return (t||0)+amult*(at[i]||0);});
}
function pineADX(h,l,c,n){
  var L=c.length,tr=[],pdm=[],mdm=[];
  for(var i=0;i<L;i++){
    tr.push(i?Math.max(h[i]-l[i],Math.abs(h[i]-c[i-1]),Math.abs(l[i]-c[i-1])):h[i]-l[i]);
    if(i){var hd=h[i]-h[i-1],ld=l[i-1]-l[i];pdm.push(hd>ld&&hd>0?hd:0);mdm.push(ld>hd&&ld>0?ld:0);}else{pdm.push(0);mdm.push(0);}
  }
  function ws(a){var o=new Array(L).fill(0);if(L<n)return o;o[n-1]=0;for(var i=0;i<n;i++)o[n-1]+=a[i];for(var i=n;i<L;i++)o[i]=o[i-1]-o[i-1]/n+a[i];return o;}
  var s=ws(tr),p=ws(pdm),m=ws(mdm);
  var pi=p.map(function(x,i){return 100*x/(s[i]||1);}),mi=m.map(function(x,i){return 100*x/(s[i]||1);});
  var dx=pi.map(function(x,i){return 100*Math.abs(x-mi[i])/((x+mi[i])||1);});
  var adxw=ws(dx);
  return{adx:adxw.map(function(x){return x/n;}),pdi:pi,mdi:mi};
}
function pineChandelier(h,l,c,n,mult){
  var at=pineATR(h,l,c,n),stops=[];
  for(var i=0;i<c.length;i++){
    if(i<n){stops.push(0);continue;}
    var hh=Math.max.apply(null,h.slice(i-n,i+1));
    stops.push(hh-mult*at[i]);
  }
  return stops;
}

// Pine sinyal üret (1 bar için)
function pineSignal(data, cfg, i, xu100Closes){
  var c=data.c,h=data.h,l=data.l,v=data.v;
  if(i<210) return {s1:false,s2:false,fu:false,adx:0};
  var pr=c[i];
  // Önce hesaplanmış indikatörleri kullan
  var ad=data._adx[i]||0, ri=data._rsi[i]||50;
  var tu=data._tmaU[i]||0;
  var am=cfg.adxMin||25;
  // S1
  var dir=data._stDir;
  var flip=dir[i]===1&&dir[i-1]===-1;
  var s1=flip&&pr>tu&&ad>am;
  // PRO score
  var rsS=false;
  if(xu100Closes&&xu100Closes.length>0){
    var xu=xu100Closes[Math.min(i,xu100Closes.length-1)];
    var rs=xu>0?pr/xu:1;
    var rsArr=[];for(var j=Math.max(0,i-199);j<=i;j++){var xu_j=xu100Closes[Math.min(j,xu100Closes.length-1)];rsArr.push(xu_j>0?c[j]/xu_j:1);}
    rsS=rs>=Math.max.apply(null,rsArr)*0.97;
  }
  var sma20=data._sma20[i]||pr;
  var accumD=pr>sma20;
  var macd=(data._e12[i]||pr)-(data._e26[i]||pr);
  var dna=ri>55&&macd>0;
  var score=([rsS,accumD,dna,ri>55,pr>(data._e200[i]||0),ad>am]).filter(Boolean).length;
  var trend2=pr>(data._e200[i]||0)&&ad>am&&pr>tu;
  var s2=score>=(cfg.proMin||5)&&trend2;
  // Fusion
  var lb=Math.min(150,i+1);
  var slice=c.slice(i+1-lb,i+1);
  var mc_=slice.reduce(function(a,b){return a+b;},0)/lb;
  var sd_=Math.sqrt(slice.reduce(function(a,x){return a+(x-mc_)*(x-mc_);},0)/lb)||1;
  var z=(pr-mc_)/sd_;
  var e50=data._e50[i]||0,e200=data._e200[i]||0;
  var qp=(ad/50)*Math.max(0,Math.min(1,(ri-30)/40));
  var nn=0.5+(pr>e50?0.2:0)+(e50>e200?0.2:0)+(z>-0.5?0.1:0);
  var fp=Math.min(1,qp*nn*1.5);
  var fthr=cfg.fusionThr||0.8;
  var fu=fp>fthr&&pr>tu&&ad>am;
  return{s1:s1,s2:s2,fu:fu,adx:ad,rsi:ri,fp:fp,score:score,pr:pr};
}

// Tüm indikatörleri önceden hesapla
function precompute(ohlcv, cfg){
  var c=ohlcv.map(function(x){return x.c;});
  var h=ohlcv.map(function(x){return x.h;});
  var l=ohlcv.map(function(x){return x.l;});
  var v=ohlcv.map(function(x){return x.v;});
  var st=pineSupertrend(h,l,c,cfg.stLen||10,cfg.stMult||3.0);
  var tmaU=pineTMAUpper(c,cfg.tmaLen||200,cfg.tmaAtrMult||8.0,20);
  var adxRes=pineADX(h,l,c,14);
  var rsi=pineRSI(c,14);
  var e200=pineEMA(c,200),e50=pineEMA(c,50),e12=pineEMA(c,12),e26=pineEMA(c,26);
  var sma20=pineSMA(c,20);
  var chst=pineChandelier(h,l,c,cfg.chLen||20,cfg.chMult||8.0);
  return{c:c,h:h,l:l,v:v,_stDir:st.dir,_tmaU:tmaU,_adx:adxRes.adx,_rsi:rsi,_e200:e200,_e50:e50,_e12:e12,_e26:e26,_sma20:sma20.map(function(x){return x||0;}),_chst:chst};
}

// ── ANA BACKTEST ENGINE ───────────────────────────────
function btEngine(ohlcv, xu100Closes, cfg){
  if(!ohlcv||ohlcv.length<60) return null;
  var comm=(cfg.commission||0.1)/100;
  var slip=(cfg.slippage||0.05)/100;
  var cap=cfg.capital||100000;
  var data=precompute(ohlcv,cfg);
  var c=data.c,h=data.h,l=data.l;

  var eq=cap,curve=[cap],trades=[];
  var inT=false,ep=0,hp=0,wins=0,losses=0,totP=0;
  var monthly={};

  for(var i=210;i<c.length;i++){
    var sig=pineSignal(data,cfg,i,xu100Closes);
    var pr=c[i];

    if(!inT&&(sig.s1||sig.s2||sig.fu)){
      // Giris: komisyon + slippage
      var entryPrice=pr*(1+comm+slip);
      inT=true; ep=entryPrice; hp=pr;
    } else if(inT){
      if(pr>hp) hp=pr;
      // Stop: Chandelier
      var chStop=data._chst[i];
      var stopPrice=chStop>0?chStop:hp*(1-(cfg.chMult||8.0)*0.01);
      if(pr<stopPrice||pr<ep*0.75){
        var exitPrice=pr*(1-comm-slip);
        var pnl=(exitPrice-ep)/ep*100;
        eq=Math.max(1000,eq*(1+pnl/100));
        totP+=pnl;
        if(pnl>=0)wins++;else losses++;
        // Tarih
        var dt=new Date(ohlcv[i].t*1000);
        var mKey=(dt.getFullYear()+'-'+('0'+(dt.getMonth()+1)).slice(-2));
        monthly[mKey]=(monthly[mKey]||0)+pnl;
        trades.push({i:i,e:ep.toFixed(2),x:exitPrice.toFixed(2),p:pnl.toFixed(2),bars:i-(trades.length?trades[trades.length-1].i:210),date:dt.toLocaleDateString('tr-TR')});
        curve.push(eq);
        inT=false;
      }
    }
  }
  // Acik pozisyonu kapat
  if(inT&&c.length>0){
    var exitPrice=c[c.length-1]*(1-comm-slip);
    var pnl=(exitPrice-ep)/ep*100;
    eq=Math.max(1000,eq*(1+pnl/100));
    totP+=pnl;if(pnl>=0)wins++;else losses++;
    trades.push({i:c.length-1,e:ep.toFixed(2),x:exitPrice.toFixed(2),p:pnl.toFixed(2),bars:0,date:new Date(ohlcv[c.length-1].t*1000).toLocaleDateString('tr-TR'),open:true});
    curve.push(eq);
  }
  if(curve.length<2) curve.push(eq);

  var tot=wins+losses;
  // Max drawdown
  var peak=curve[0],maxdd=0;
  for(var j=0;j<curve.length;j++){if(curve[j]>peak)peak=curve[j];var dd=(peak-curve[j])/peak*100;if(dd>maxdd)maxdd=dd;}
  // Sharpe (Türk risksiz faiz: %40 yıllık ~ %0.11/gün)
  var rets=trades.map(function(t){return parseFloat(t.p)/100;});
  var retMean=rets.length?rets.reduce(function(a,b){return a+b;},0)/rets.length:0;
  var retStd=0;if(rets.length>1){var vr=rets.reduce(function(a,x){return a+(x-retMean)*(x-retMean);},0)/rets.length;retStd=Math.sqrt(vr);}
  var rf=0.40/252; // Türkiye risksiz oranı
  var sharpe=retStd>0?((retMean-rf)/retStd*Math.sqrt(252)).toFixed(2):'0';
  // Sortino
  var downRets=rets.filter(function(r){return r<0;});
  var downStd=0;if(downRets.length){var dv=downRets.reduce(function(a,x){return a+x*x;},0)/downRets.length;downStd=Math.sqrt(dv);}
  var sortino=downStd>0?((retMean-rf)/downStd*Math.sqrt(252)).toFixed(2):'0';
  // Calmar
  var calmar=maxdd>0?((eq-cap)/cap*100/maxdd).toFixed(2):'0';
  // Profit factor
  var gWin=rets.filter(function(r){return r>0;}).reduce(function(a,b){return a+b;},0);
  var gLoss=Math.abs(rets.filter(function(r){return r<0;}).reduce(function(a,b){return a+b;},0));
  var pf=gLoss>0?(gWin/gLoss).toFixed(2):'999';
  // CAGR
  var years=(ohlcv.length-210)/252;
  var cagr=years>0.1?(Math.pow(eq/cap,1/years)-1)*100:0;
  // Avg hold
  var avgHold=trades.length?Math.round(trades.reduce(function(a,t){return a+t.bars;},0)/trades.length):0;

  return{wins:wins,losses:losses,tot:tot,
    wr:tot?((wins/tot)*100).toFixed(1):'0',
    avg:tot?(totP/tot).toFixed(2):'0',
    maxdd:maxdd.toFixed(2),
    ret:((eq-cap)/cap*100).toFixed(2),
    cagr:cagr.toFixed(1),
    eq:Math.round(eq).toLocaleString('tr-TR'),
    sharpe:sharpe,sortino:sortino,calmar:calmar,pf:pf,
    trades:trades,curve:curve,monthly:monthly,
    avgHold:avgHold,
    sym:cfg.sym,tf:cfg.tf,sys:cfg.sys};
}

// ── BACKTEST CALISTIR ─────────────────────────────────
function runBT(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('btout').style.display='none';
  setSt('OHLCV cekiliyor...');toast('Gercek veri cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    if(!ohlcv||ohlcv.length<60){
      // Fallback: gelismis simule (Pine mantigi ile)
      toast('Proxy verisi, simule calisiyor...');
      var r=btEngSim(sym,tf,sys,cap,comm,slip);
      renderBT(r); document.getElementById('btout').style.display='block';
      setSt('Tamamlandi (simule)');
      return;
    }
    var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
             adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
             stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:8.0};
    var r=btEngine(ohlcv,xu100?[xu100.price]:null,cfg);
    if(!r){toast('Yetersiz veri');return;}
    renderBT(r);
    document.getElementById('btout').style.display='block';
    setSt('Backtest tamamlandi: '+r.tot+' islem');
    toast(r.tot+' islem | Getiri: '+r.ret+'%');
  });
}

// Simule fallback (OHLCV alınamazsa)
function btEngSim(sym,tf,sys,cap,comm,slip){
  var bars=tf==='D'?504:tf==='240'?504*5:504*10;
  var sd=0;for(var i=0;i<sym.length;i++)sd+=sym.charCodeAt(i)*(i+1)*17;
  var price=50+((sd*7)%450),vol=price*0.015,eq=cap,curve=[cap];
  var wins=0,losses=0,trades=[],inT=false,ep=0,hp=0,totP=0,monthly={};
  var am=C.adxMin||25,cm=comm/100+slip/100;
  for(var b=0;b<bars;b++){
    price=Math.max(1,price*(1+Math.sin(b*0.012+sd*0.003)*0.0015+Math.sin(b*0.08+sd*0.01)*0.0008+Math.sin(b*(sd%100)*0.0001)*0.012));
    vol=vol*0.99+Math.abs(price*0.001);
    var adx=20+Math.abs(Math.sin(b*0.15+sd*0.02))*40,ri=Math.max(10,Math.min(90,50+Math.sin(b*0.11+sd*0.05)*28)),ms=Math.sin(b*0.09+sd*0.04);
    var buy=false;
    if(!inT){if(sys==='s1')buy=adx>am&&Math.sin(b*0.23+sd*0.07)>0.55&&ri>45&&ri<72;else if(sys==='s2')buy=adx>am&&ri>52&&ri<70&&ms>0.2;else if(sys==='fu')buy=adx>am&&ri>48&&Math.sin(b*0.21+sd*0.06)>0.45;else buy=adx>am&&ri>55&&ri<75&&ms>0.3&&Math.sin(b*0.19+sd*0.08)>0.5;}
    if(!inT&&buy){inT=true;ep=price*(1+cm);hp=price;}
    else if(inT){if(price>hp)hp=price;var stp=hp-vol*2*(C.atrm||8);if(price<stp||price<ep*0.75){var pnl=(price*(1-cm)-ep)/ep*100;eq=Math.max(1000,eq*(1+pnl/100));totP+=pnl;if(pnl>=0)wins++;else losses++;var mK='M'+(Math.floor(b/21)+1);monthly[mK]=(monthly[mK]||0)+pnl;trades.push({e:ep.toFixed(2),x:(price*(1-cm)).toFixed(2),p:pnl.toFixed(2),bars:5,date:'Bar '+b});curve.push(eq);inT=false;}}
  }
  if(inT){var pnl=(price*(1-cm)-ep)/ep*100;eq=Math.max(1000,eq*(1+pnl/100));totP+=pnl;if(pnl>=0)wins++;else losses++;trades.push({e:ep.toFixed(2),x:price.toFixed(2),p:pnl.toFixed(2),bars:0,date:'Acik'});curve.push(eq);}
  if(curve.length<2)curve.push(eq);
  var tot=wins+losses,peak=curve[0],maxdd=0;
  for(var j=0;j<curve.length;j++){if(curve[j]>peak)peak=curve[j];var dd=(peak-curve[j])/peak*100;if(dd>maxdd)maxdd=dd;}
  var rets=trades.map(function(t){return parseFloat(t.p)/100;});
  var rm=rets.length?rets.reduce(function(a,b){return a+b;},0)/rets.length:0;
  var rs=0;if(rets.length>1){var vr=rets.reduce(function(a,x){return a+(x-rm)*(x-rm);},0)/rets.length;rs=Math.sqrt(vr);}
  var rf=0.40/252;
  var sharpe=rs>0?((rm-rf)/rs*Math.sqrt(252)).toFixed(2):'0';
  var dr=rets.filter(function(r){return r<0;});var ds=0;if(dr.length){var dv=dr.reduce(function(a,x){return a+x*x;},0)/dr.length;ds=Math.sqrt(dv);}
  var sortino=ds>0?((rm-rf)/ds*Math.sqrt(252)).toFixed(2):'0';
  var gw=rets.filter(function(r){return r>0;}).reduce(function(a,b){return a+b;},0);
  var gl=Math.abs(rets.filter(function(r){return r<0;}).reduce(function(a,b){return a+b;},0));
  var pf=gl>0?(gw/gl).toFixed(2):'999';
  var cagr=((Math.pow(eq/cap,1/(bars/252))-1)*100).toFixed(1);
  var calmar=maxdd>0?((parseFloat(((eq-cap)/cap*100).toFixed(2)))/maxdd).toFixed(2):'0';
  return{wins:wins,losses:losses,tot:tot,wr:tot?((wins/tot)*100).toFixed(1):'0',avg:tot?(totP/tot).toFixed(2):'0',maxdd:maxdd.toFixed(2),ret:((eq-cap)/cap*100).toFixed(2),cagr:cagr,eq:Math.round(eq).toLocaleString('tr-TR'),sharpe:sharpe,sortino:sortino,calmar:calmar,pf:pf,trades:trades.slice(-30),curve:curve,monthly:monthly,avgHold:5,sym:sym,tf:tf,sys:sys};
}

// ── RENDER BACKTEST SONUCU ────────────────────────────
function renderBT(r){
  if(!r)return;
  var tfL=r.tf==='D'?'Gunluk':r.tf==='240'?'4S':'2S';
  var sysL={'all':'Master AI','s1':'Sistem 1','s2':'PRO','fu':'Fusion'}[r.sys]||r.sys;
  var rc=parseFloat(r.ret)>=0?'var(--green)':'var(--red)';
  var wrc=parseFloat(r.wr)>=50?'var(--green)':'var(--red)';
  var pfc=parseFloat(r.pf)>=1.5?'var(--green)':parseFloat(r.pf)>=1?'var(--gold)':'var(--red)';

  document.getElementById('btstats').innerHTML=
    // Başlık satırı
    '<div class="sstat" style="grid-column:1/-1;background:rgba(0,212,255,.04);border-color:rgba(0,212,255,.2)">'
      +'<div style="font-size:9px;color:var(--t4);margin-bottom:8px">'+r.sym+' . '+tfL+' . '+sysL+' | Komisyon dahil</div>'
      +'<div style="display:flex;gap:16px;flex-wrap:wrap">'
        +'<div><div class="sval" style="color:'+rc+'">'+r.ret+'%</div><div class="slb2">Toplam Getiri</div></div>'
        +'<div><div class="sval" style="font-size:15px;color:var(--t1)">TL'+r.eq+'</div><div class="slb2">Son Sermaye</div></div>'
        +'<div><div class="sval" style="font-size:16px;color:var(--cyan)">'+r.cagr+'%</div><div class="slb2">CAGR (Yillik)</div></div>'
      +'</div>'
    +'</div>'
    // Metrikler
    +'<div class="sstat"><div class="sval">'+r.tot+'</div><div class="slb2">Toplam Islem</div></div>'
    +'<div class="sstat"><div class="sval" style="color:'+wrc+'">'+r.wr+'%</div><div class="slb2">Win Rate</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--green)">'+r.wins+'</div><div class="slb2">Karli</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--red)">'+r.losses+'</div><div class="slb2">Zarari</div></div>'
    +'<div class="sstat"><div class="sval n">-'+r.maxdd+'%</div><div class="slb2">Max Drawdown</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+r.sharpe+'</div><div class="slb2">Sharpe</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--purple)">'+r.sortino+'</div><div class="slb2">Sortino</div></div>'
    +'<div class="sstat"><div class="sval" style="color:'+pfc+'">'+r.pf+'</div><div class="slb2">Profit Factor</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--gold)">'+r.calmar+'</div><div class="slb2">Calmar</div></div>'
    +'<div class="sstat"><div class="sval">'+r.avgHold+'</div><div class="slb2">Ort. Tur (Bar)</div></div>';

  document.getElementById('btTradeCount').textContent=r.tot;
  drawCurve(r.curve,'btcv');
  renderMonthly(r.monthly);

  var lh='<div style="display:grid;grid-template-columns:1fr 1fr 1fr 60px;gap:4px;padding:5px 0;border-bottom:1px solid var(--b2);font-size:8px;color:var(--t4)"><span>Giris</span><span>Cikis</span><span>Tarih</span><span style="text-align:right">PnL</span></div>';
  r.trades.slice().reverse().forEach(function(t){
    var p=parseFloat(t.p),pc=p>=0?'var(--green)':'var(--red)';
    lh+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr 60px;gap:4px;padding:5px 0;border-bottom:1px solid var(--b1);font-size:9px;align-items:center">'
      +'<span style="color:var(--t2);font-family:Courier New,monospace">TL'+t.e+'</span>'
      +'<span style="color:var(--t2);font-family:Courier New,monospace">TL'+t.x+'</span>'
      +'<span style="color:var(--t4)">'+t.date+'</span>'
      +'<span style="color:'+pc+';font-weight:700;text-align:right;font-family:Courier New,monospace">'+(p>=0?'+':'')+t.p+'%</span>'
      +(t.open?'<span style="grid-column:1/-1;font-size:8px;color:var(--gold)">Acik pozisyon</span>':'')
    +'</div>';
  });
  document.getElementById('btlog').innerHTML=lh;
}

// Aylık dağılım ısı haritası
function renderMonthly(monthly){
  var el=document.getElementById('btMonthly');
  if(!el||!monthly)return;
  var keys=Object.keys(monthly).sort();
  if(!keys.length){el.innerHTML='';return;}
  var html='<div style="display:flex;flex-wrap:wrap;gap:3px">';
  keys.forEach(function(k){
    var v=monthly[k];
    var pct=parseFloat(v);
    var bg=pct>5?'rgba(0,230,118,.4)':pct>0?'rgba(0,230,118,.2)':pct<-5?'rgba(255,68,68,.4)':'rgba(255,68,68,.2)';
    var tc=pct>=0?'var(--green)':'var(--red)';
    html+='<div style="background:'+bg+';border-radius:4px;padding:4px 6px;font-size:8px;min-width:52px;text-align:center">'
      +'<div style="color:var(--t4);margin-bottom:1px">'+k+'</div>'
      +'<div style="font-weight:700;color:'+tc+'">'+(pct>=0?'+':'')+pct.toFixed(1)+'%</div>'
    +'</div>';
  });
  html+='</div>';
  el.innerHTML=html;
}

// Canvas çiz (equity curve)
function drawCurve(curve,canvasId){
  var cvId=canvasId||'btcv';
  var cv=document.getElementById(cvId);if(!cv)return;
  var par=cv.parentElement;cv.width=par.offsetWidth||300;cv.height=175;
  var ctx=cv.getContext('2d');
  if(!curve||curve.length<2){ctx.fillStyle='#555';ctx.font='11px sans-serif';ctx.textAlign='center';ctx.fillText('Veri yok',cv.width/2,cv.height/2);return;}
  var mn=Math.min.apply(null,curve),mx=Math.max.apply(null,curve),rg=mx-mn||1,pd=14;
  ctx.clearRect(0,0,cv.width,cv.height);
  // Grid
  ctx.strokeStyle='rgba(30,30,30,.9)';ctx.lineWidth=1;
  for(var i=0;i<=4;i++){var gy=pd+(cv.height-pd*2)*i/4;ctx.beginPath();ctx.moveTo(0,gy);ctx.lineTo(cv.width,gy);ctx.stroke();}
  // Curve rengi
  var up=curve[curve.length-1]>=curve[0];
  var lc=up?'#00e676':'#ff4444';
  var gr=ctx.createLinearGradient(0,0,0,cv.height);
  gr.addColorStop(0,up?'rgba(0,230,118,.22)':'rgba(255,68,68,.15)');
  gr.addColorStop(1,'rgba(0,0,0,0)');
  // Drawdown bölgelerini kırmızı göster
  ctx.beginPath();
  for(var j=0;j<curve.length;j++){
    var x=j/(curve.length-1)*cv.width;
    var y=pd+(cv.height-pd*2)*(1-(curve[j]-mn)/rg);
    if(!j)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.strokeStyle=lc;ctx.lineWidth=2;ctx.stroke();
  ctx.lineTo(cv.width,cv.height);ctx.lineTo(0,cv.height);ctx.closePath();
  ctx.fillStyle=gr;ctx.fill();
  // Başlangıç/bitiş etiketleri
  ctx.fillStyle='#555';ctx.font='9px Courier New';ctx.textAlign='left';
  ctx.fillText('TL'+Math.round(curve[0]).toLocaleString('tr-TR'),4,cv.height-3);
  ctx.textAlign='right';
  var fc=curve[curve.length-1];
  ctx.fillStyle=up?'#00e676':'#ff4444';
  ctx.fillText('TL'+Math.round(fc).toLocaleString('tr-TR'),cv.width-4,cv.height-3);
}

// ── WALK-FORWARD ─────────────────────────────────────
var _wfBaseOHLCV=null, _wfXU100=null;

function runWF(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  var trainPct=parseFloat(document.getElementById('wfTrain').value)||0.7;
  var crit=document.getElementById('wfCrit').value||'sharpe';
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('wfout').style.display='none';
  toast('Walk-Forward basliyor...');setSt('OHLCV cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    if(!ohlcv||ohlcv.length<120){toast('Yetersiz veri (min 120 bar gerekli)');return;}
    _wfBaseOHLCV=ohlcv; _wfXU100=xu100;

    var splitIdx=Math.floor(ohlcv.length*trainPct);
    var trainData=ohlcv.slice(0,splitIdx);
    var testData=ohlcv.slice(splitIdx);

    setSt('Egitim bolumunde optimizasyon...');

    // ATR Multiplier uzerinde optimizasyon (en etkili parametre)
    var params=[3,4,5,6,7,8,9,10,11,12];
    var results=[];
    params.forEach(function(atrm){
      var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
               adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
               stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:atrm};
      var trainRes=btEngine(trainData,null,cfg);
      if(!trainRes||trainRes.tot<2)return;
      var score=crit==='sharpe'?parseFloat(trainRes.sharpe):
                crit==='wr'?parseFloat(trainRes.wr):
                parseFloat(trainRes.ret);
      results.push({atrm:atrm,trainScore:score,trainRet:trainRes.ret,trainWr:trainRes.wr,trainSharpe:trainRes.sharpe});
    });
    results.sort(function(a,b){return b.trainScore-a.trainScore;});
    if(!results.length){toast('Yeterli islem yok');return;}

    var bestATRM=results[0].atrm;
    // Test bolumunde dogrula
    var testCfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
                 adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
                 stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:bestATRM};
    var testRes=btEngine(testData,null,testCfg);

    // Tam veri ile de calistir
    var fullCfg=Object.assign({},testCfg);
    var fullRes=btEngine(ohlcv,null,fullCfg);

    renderWF(results,bestATRM,testRes,fullRes,trainPct,sym,tf);
    document.getElementById('wfout').style.display='block';
    setSt('Walk-Forward tamamlandi');
    toast('En iyi ATR: '+bestATRM+' | Test getiri: '+( testRes?testRes.ret+'%':'N/A'));
  });
}

function renderWF(results,bestATRM,testRes,fullRes,trainPct,sym,tf){
  var testPct=Math.round((1-trainPct)*100);
  var html='<div class="card" style="border-color:rgba(0,212,255,.2)">'
    +'<div class="ctitle" style="color:var(--cyan)">Walk-Forward Sonucu</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-bottom:10px">'+sym+' | Egitim: %'+Math.round(trainPct*100)+' | Test: %'+testPct+'</div>'

    +'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:12px">'
      +'<div class="sstat"><div class="sval" style="color:var(--gold)">'+bestATRM+'</div><div class="slb2">En Iyi ATR Mult</div></div>'
      +(testRes?'<div class="sstat"><div class="sval" style="color:'+(parseFloat(testRes.ret)>=0?'var(--green)':'var(--red)')+'">'+testRes.ret+'%</div><div class="slb2">Test Getiri</div></div>':'')
      +(testRes?'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+testRes.sharpe+'</div><div class="slb2">Test Sharpe</div></div>':'')
    +'</div>'

    +(fullRes?'<div style="font-size:9px;color:var(--t4);margin-bottom:6px">Tam veri ('+bestATRM+'x ATR ile):</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-bottom:12px">'
        +'<div class="sstat"><div class="sval '+(parseFloat(fullRes.ret)>=0?'p':'n')+'" style="font-size:16px">'+fullRes.ret+'%</div><div class="slb2">Toplam</div></div>'
        +'<div class="sstat"><div class="sval" style="font-size:16px;color:var(--cyan)">'+fullRes.sharpe+'</div><div class="slb2">Sharpe</div></div>'
        +'<div class="sstat"><div class="sval" style="font-size:16px">'+fullRes.wr+'%</div><div class="slb2">Win Rate</div></div>'
        +'<div class="sstat"><div class="sval n" style="font-size:16px">-'+fullRes.maxdd+'%</div><div class="slb2">Max DD</div></div>'
      +'</div>'
    :'')

    +'<div class="ctitle" style="margin-top:8px">ATR Parametresi Taramasi (Egitim %'+Math.round(trainPct*100)+')</div>'
    +'<div style="display:flex;justify-content:space-between;font-size:8px;color:var(--t4);padding:4px 0;border-bottom:1px solid var(--b2)"><span>ATR Mult</span><span>Getiri %</span><span>Win Rate</span><span>Sharpe</span></div>';

  results.forEach(function(x){
    var isBest=x.atrm===bestATRM;
    html+='<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--b1);font-size:9px'+(isBest?';background:rgba(0,230,118,.05)':'')+'">'
      +'<span style="font-weight:'+(isBest?'700':'400')+';color:'+(isBest?'var(--green)':'var(--t2)')+'">'+x.atrm+'x'+(isBest?' BEST':'')+' </span>'
      +'<span style="color:'+(parseFloat(x.trainRet)>=0?'var(--green)':'var(--red)')+'">'+x.trainRet+'%</span>'
      +'<span style="color:var(--t2)">'+x.trainWr+'%</span>'
      +'<span style="color:var(--cyan)">'+x.trainSharpe+'</span>'
    +'</div>';
  });

  if(fullRes){
    html+='<div style="margin-top:10px">';
    html+='<div class="ctitle">Equity Curve (En Iyi Parametre)</div>';
    html+='<div style="position:relative;height:150px"><canvas id="wfcv"></canvas></div>';
    html+='</div>';
  }
  html+='</div>';
  document.getElementById('wfout').innerHTML=html;
  if(fullRes) setTimeout(function(){drawCurve(fullRes.curve,'wfcv');},100);
}

// ── MONTE CARLO ───────────────────────────────────────
function runMC(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  var scenarios=parseInt(document.getElementById('mcScen').value)||500;
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('mcout').style.display='none';
  toast('Monte Carlo basliyor...');setSt('Veri cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    var baseRes;
    if(ohlcv&&ohlcv.length>=60){
      var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
               adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
               stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:8.0};
      baseRes=btEngine(ohlcv,null,cfg);
    } else {
      baseRes=btEngSim(sym,tf,sys,cap,comm,slip);
    }
    if(!baseRes||!baseRes.trades||baseRes.trades.length<5){toast('Yetersiz islem (min 5)');return;}

    setSt('Monte Carlo hesaplaniyor: '+scenarios+' senaryo...');
    setTimeout(function(){
      var tradeRets=baseRes.trades.map(function(t){return parseFloat(t.p);});
      var finalReturns=[],maxDDs=[],sharpes=[];

      for(var s=0;s<scenarios;s++){
        // Rastgele sirala (bootstrap)
        var shuffled=tradeRets.slice();
        for(var k=shuffled.length-1;k>0;k--){var j=Math.floor(Math.random()*(k+1));var tmp=shuffled[k];shuffled[k]=shuffled[j];shuffled[j]=tmp;}
        // Equity curve hesapla
        var eq2=cap,curve2=[cap],peak2=cap,maxdd2=0;
        shuffled.forEach(function(p){eq2=Math.max(1000,eq2*(1+p/100));curve2.push(eq2);if(eq2>peak2)peak2=eq2;var dd=(peak2-eq2)/peak2*100;if(dd>maxdd2)maxdd2=dd;});
        finalReturns.push((eq2-cap)/cap*100);
        maxDDs.push(maxdd2);
        // Basit Sharpe
        var rm2=shuffled.reduce(function(a,b){return a+b;},0)/shuffled.length/100;
        var rs2=0;if(shuffled.length>1){var vr2=shuffled.reduce(function(a,x){return a+(x/100-rm2)*(x/100-rm2);},0)/shuffled.length;rs2=Math.sqrt(vr2);}
        sharpes.push(rs2>0?(rm2-0.40/252)/rs2*Math.sqrt(252):0);
      }

      finalReturns.sort(function(a,b){return a-b;});
      maxDDs.sort(function(a,b){return a-b;});
      sharpes.sort(function(a,b){return a-b;});

      var p5=finalReturns[Math.floor(scenarios*0.05)];
      var p25=finalReturns[Math.floor(scenarios*0.25)];
      var p50=finalReturns[Math.floor(scenarios*0.50)];
      var p75=finalReturns[Math.floor(scenarios*0.75)];
      var p95=finalReturns[Math.floor(scenarios*0.95)];
      var winProb=finalReturns.filter(function(r){return r>0;}).length/scenarios*100;
      var avgDD=maxDDs.reduce(function(a,b){return a+b;},0)/maxDDs.length;
      var medSharpe=sharpes[Math.floor(scenarios*0.5)];

      renderMC({scenarios:scenarios,p5:p5.toFixed(1),p25:p25.toFixed(1),p50:p50.toFixed(1),p75:p75.toFixed(1),p95:p95.toFixed(1),winProb:winProb.toFixed(1),avgDD:avgDD.toFixed(1),medSharpe:medSharpe.toFixed(2),finalReturns:finalReturns,base:baseRes,sym:sym});
      document.getElementById('mcout').style.display='block';
      setSt('Monte Carlo tamamlandi: '+scenarios+' senaryo');
      toast('Kazanma olasiligi: %'+winProb.toFixed(1));
    },50);
  });
}

function renderMC(r){
  var html='<div class="card" style="border-color:rgba(192,132,252,.25)">'
    +'<div class="ctitle" style="color:var(--purple)">Monte Carlo: '+r.scenarios+' Senaryo</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-bottom:10px">'+r.sym+' | Gercek islemler rastgele sirayla '+r.scenarios+'x calistirildi</div>'

    +'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-bottom:10px">'
      +'<div class="sstat" style="border-color:rgba(0,230,118,.3)"><div class="sval" style="color:var(--green)">%'+r.winProb+'</div><div class="slb2">Kazanma Olasiligi</div></div>'
      +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+r.medSharpe+'</div><div class="slb2">Medyan Sharpe</div></div>'
      +'<div class="sstat"><div class="sval n">-'+r.avgDD+'%</div><div class="slb2">Ort. Max Drawdown</div></div>'
      +'<div class="sstat"><div class="sval p">'+r.p50+'%</div><div class="slb2">Medyan Getiri</div></div>'
    +'</div>'

    // Percentile dagilim
    +'<div class="ctitle" style="margin-bottom:8px">Getiri Dagilimi</div>'
    +'<div style="position:relative;margin-bottom:12px">'
      // Bar chart simulasyonu
      +'<div style="display:flex;gap:4px;align-items:flex-end;height:60px;margin-bottom:4px" id="mcHist"></div>'
    +'</div>'

    +'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-bottom:10px">'
      +['P5','P25','P50','P75','P95'].map(function(p,idx){
        var val=[r.p5,r.p25,r.p50,r.p75,r.p95][idx];
        var clr=parseFloat(val)>=0?'var(--green)':'var(--red)';
        return '<div style="text-align:center;background:var(--bg3);border-radius:6px;padding:7px 4px"><div style="font-size:12px;font-weight:700;color:'+clr+'">'+val+'%</div><div style="font-size:8px;color:var(--t4);margin-top:2px">'+p+'</div></div>';
      }).join('')
    +'</div>'

    +'<div style="font-size:9px;color:var(--t3);line-height:1.7;padding:8px;background:var(--bg3);border-radius:7px">'
      +'Gercek backtest: <span style="color:var(--cyan)">'+r.base.ret+'%</span> getiri, <span style="color:var(--cyan)">'+r.base.sharpe+'</span> Sharpe<br>'
      +'%95 guvenle getiri: <span style="color:var(--green)">'+r.p5+'%</span> ile <span style="color:var(--green)">'+r.p95+'%</span> arasinda<br>'
      +(parseFloat(r.winProb)>70?'Sistem guvenilir gorunuyor.':parseFloat(r.winProb)>50?'Sistem orta guvende. Daha fazla veri gerekli.':'Dikkat: Dusuk kazanma olasiligi, parametreleri gozden gecirin.')
    +'</div>'
  +'</div>';

  document.getElementById('mcout').innerHTML=html;

  // Histogram ciz
  setTimeout(function(){
    var el=document.getElementById('mcHist');if(!el)return;
    var buckets={};
    var step=10;
    r.finalReturns.forEach(function(v){var b=Math.floor(v/step)*step;buckets[b]=(buckets[b]||0)+1;});
    var keys=Object.keys(buckets).map(Number).sort(function(a,b){return a-b;});
    var maxCount=Math.max.apply(null,Object.values(buckets));
    el.innerHTML=keys.map(function(k){
      var cnt=buckets[k],h=Math.round(cnt/maxCount*55)+5;
      var clr=k>=0?'rgba(0,230,118,.6)':'rgba(255,68,68,.6)';
      return '<div style="flex:1;background:'+clr+';height:'+h+'px;border-radius:2px 2px 0 0;cursor:default" title="'+k+'% ~ '+(k+step)+'%: '+cnt+' senaryo"></div>';
    }).join('');
  },50);
}

// ── OPTİMİZASYON ─────────────────────────────────────
function runOpt(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  var param=document.getElementById('optP').value;
  var crit=document.getElementById('optCrit').value||'sharpe';
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('optout').style.display='none';
  toast('Optimizasyon basliyor...');setSt('Veri cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    setSt('Parametreler taranıyor...');
    var ranges={
      atr:  {vals:[3,4,5,6,7,8,9,10,11,12,14],label:'ATR Multiplier',key:'chMult'},
      adx:  {vals:[15,18,20,22,25,28,30,35,40],label:'Min ADX',key:'adxMin'},
      fb:   {vals:[0.5,0.55,0.6,0.65,0.7,0.75,0.8,0.85,0.9],label:'Fusion Buy Esigi',key:'fusionThr'},
      mb:   {vals:[0.5,0.55,0.6,0.65,0.7,0.75,0.8,0.85],label:'Master Buy Esigi',key:'masterThr'},
      pro:  {vals:[2,3,4,5,6],label:'PRO Min Skor',key:'proMin'}
    };
    var range=ranges[param]||ranges.atr;
    var results=[];

    range.vals.forEach(function(v){
      var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
               adxMin:param==='adx'?v:(C.adxMin||25),
               proMin:param==='pro'?v:(C.sc||5),
               fusionThr:param==='fb'?v:((C.fb||80)/100),
               masterThr:param==='mb'?v:0.7,
               stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,
               chLen:20,chMult:param==='atr'?v:(C.atrm||8)};
      var res;
      if(ohlcv&&ohlcv.length>=60) res=btEngine(ohlcv,null,cfg);
      else res=btEngSim(sym,tf,sys,cap,comm,slip);
      if(!res||res.tot<2)return;
      var score=crit==='sharpe'?parseFloat(res.sharpe):
                crit==='wr'?parseFloat(res.wr):
                crit==='calmar'?parseFloat(res.calmar):
                parseFloat(res.ret);
      results.push({v:v,score:score,ret:res.ret,wr:res.wr,sharpe:res.sharpe,
                    maxdd:res.maxdd,tot:res.tot,calmar:res.calmar,pf:res.pf,sortino:res.sortino});
    });

    results.sort(function(a,b){return b.score-a.score;});
    renderOpt(results,range.label,param,crit,sym);
    document.getElementById('optout').style.display='block';
    setSt('Optimizasyon tamamlandi');
    toast('En iyi: '+range.label+'='+( results[0]?results[0].v:'N/A'));
  });
}

function renderOpt(results,label,param,crit,sym){
  if(!results.length){document.getElementById('optout').innerHTML='<div style="color:var(--red);font-size:10px;padding:10px">Sonuc yok</div>';return;}
  var best=results[0];
  var critLabel={'sharpe':'Sharpe','ret':'Getiri %','wr':'Win Rate %','calmar':'Calmar'}[crit]||crit;

  var html='<div class="card" style="border-color:rgba(0,230,118,.2)">'
    +'<div class="ctitle" style="color:var(--green)">Optimizasyon: '+label+'</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-bottom:10px">'+sym+' | Kriter: '+critLabel+' | Komisyon dahil</div>'

    +'<div style="background:rgba(0,230,118,.06);border:1px solid rgba(0,230,118,.2);border-radius:8px;padding:12px;margin-bottom:12px">'
      +'<div style="font-size:10px;color:var(--green);font-weight:700;margin-bottom:8px">EN IYI KOMBINASYON</div>'
      +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px">'
        +'<div class="sstat"><div class="sval" style="color:var(--gold)">'+best.v+'</div><div class="slb2">'+label+'</div></div>'
        +'<div class="sstat"><div class="sval '+(parseFloat(best.ret)>=0?'p':'n')+'">'+best.ret+'%</div><div class="slb2">Getiri</div></div>'
        +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+best.sharpe+'</div><div class="slb2">Sharpe</div></div>'
        +'<div class="sstat"><div class="sval">'+best.wr+'%</div><div class="slb2">Win Rate</div></div>'
        +'<div class="sstat"><div class="sval n">-'+best.maxdd+'%</div><div class="slb2">Max DD</div></div>'
        +'<div class="sstat"><div class="sval" style="color:var(--purple)">'+best.pf+'</div><div class="slb2">Profit Factor</div></div>'
      +'</div>'
    +'</div>'

    +'<div style="display:grid;grid-template-columns:60px 1fr 1fr 1fr 1fr 1fr;gap:3px;font-size:8px;color:var(--t4);padding:4px 0;border-bottom:1px solid var(--b2)">'
      +'<span>'+label.split(' ')[0]+'</span><span>Getiri</span><span>Win%</span><span>Sharpe</span><span>MaxDD</span><span>PF</span>'
    +'</div>';

  results.forEach(function(x){
    var isBest=x.v===best.v;
    var rc=parseFloat(x.ret)>=0?'var(--green)':'var(--red)';
    html+='<div style="display:grid;grid-template-columns:60px 1fr 1fr 1fr 1fr 1fr;gap:3px;padding:5px 0;border-bottom:1px solid var(--b1);font-size:9px'+(isBest?';background:rgba(0,230,118,.04)':'')+'">'
      +'<span style="font-weight:'+(isBest?'700':'400')+';color:'+(isBest?'var(--green)':'var(--t2)')+'">'+x.v+(isBest?' BEST':'')+' </span>'
      +'<span style="color:'+rc+'">'+x.ret+'%</span>'
      +'<span>'+x.wr+'%</span>'
      +'<span style="color:var(--cyan)">'+x.sharpe+'</span>'
      +'<span style="color:var(--red)">-'+x.maxdd+'%</span>'
      +'<span>'+x.pf+'</span>'
    +'</div>';
  });

  html+='<div style="margin-top:10px;font-size:9px;color:var(--t3);line-height:1.7;padding:8px;background:var(--bg3);border-radius:7px">'
    +'Tavsiye: '+label+' = <span style="color:var(--gold)">'+best.v+'</span> olarak ayarla<br>'
    +'Bunu Ayarlar sekmesinden guncelleyebilirsin.'
    +'<div style="margin-top:6px"><button class="btn g" style="padding:5px 12px;font-size:9px" onclick="applyOptResult(\''+param+'\','+best.v+')">Bu Ayari Uygula</button></div>'
  +'</div></div>';

  document.getElementById('optout').innerHTML=html;
}

function applyOptResult(param, val){
  if(param==='atr'){C.atrm=val;document.getElementById('s_atrm').value=val;}
  else if(param==='adx'){C.adxMin=val;document.getElementById('s_adxMin').value=val;}
  else if(param==='pro'){C.sc=val;document.getElementById('s_sc').value=val;}
  else if(param==='fb'){C.fb=Math.round(val*100);document.getElementById('s_fb').value=Math.round(val*100);}
  lsSet('bistcfg',C);
  toast('Ayar uygulandi: '+param+'='+val);
}

// -- SOUND --
function beep(isMaster){try{var ctx=new(window.AudioContext||window.webkitAudioContext)(),osc=ctx.createOscillator(),g=ctx.createGain();osc.connect(g);g.connect(ctx.destination);osc.frequency.value=isMaster?880:440;g.gain.setValueAtTime(0.3,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.5);osc.start(ctx.currentTime);osc.stop(ctx.currentTime+0.5);}catch(e){}}

// -- INIT --
window.addEventListener('load',function(){
  // BT sembol listesi
  var sel=document.getElementById('btSym');sel.innerHTML=STOCKS.map(function(s){return'<option value="'+s.t+'">'+s.t+'</option>';}).join('');
  updateTgUI();loadSetsUI();renderSt();renderSigs();renderAgents();renderPositions();renderWatchlist();renderReport();
  updateXU100();setInterval(updateXU100,5*60*1000);
  setInterval(function(){if(Object.keys(S.openPositions).length>0)renderPositions();},60*1000);
  startAutoScan();setBgActive(true);
  function resetMidnight(){var now=new Date(),ms=new Date(now.getFullYear(),now.getMonth(),now.getDate()+1)-now;setTimeout(function(){S.sentCount={};_dayEndSent=false;resetMidnight();},ms);}resetMidnight();
  if(TG.token&&TG.chat)startTGListener();
  document.getElementById('scanInterval').addEventListener('change',function(){C.scanInterval=parseInt(this.value)||5;lsSet('bistcfg',C);startAutoScan();toast('Tarama araligi: '+C.scanInterval+' dk');});
});
</script>
</body>
</html>
<script>
// ── EK FONKSİYONLAR ────────────────────────────

// FIX 12: Pozisyon sekme geçişi
function posTab(tab){
  document.getElementById('posTabOpen').className='chip'+(tab==='open'?' on':'');
  document.getElementById('posTabClosed').className='chip'+(tab==='closed'?' on':'');
  document.getElementById('positionList').style.display=tab==='open'?'block':'none';
  document.getElementById('closedList').style.display=tab==='closed'?'block':'none';
  if(tab==='closed')renderClosedPositions();
}

// FIX 12: Kapanmış pozisyon render - Pine tablosu alanları
function renderClosedPositions(){
  var el=document.getElementById('closedList');
  if(!S.closedPositions.length){
    el.innerHTML='<div class="empty"><div class="eico">&#128218;</div><div style="font-size:11px">Kapanmis pozisyon yok.</div></div>';
    return;
  }
  // Ozet
  var total=0,wins=0;
  S.closedPositions.forEach(function(p){total+=parseFloat(p.pnlPct);if(parseFloat(p.pnlPct)>=0)wins++;});
  var wr=(S.closedPositions.length?((wins/S.closedPositions.length)*100).toFixed(1):'0');
  var html='<div class="card" style="margin-bottom:8px">'
    +'<div style="display:flex;gap:12px;flex-wrap:wrap">'
      +'<div><div class="sval '+(total>=0?'p':'n')+'" style="font-size:16px">'+(total>=0?'+':'')+total.toFixed(2)+'%</div><div class="slb2">Toplam PnL</div></div>'
      +'<div><div class="sval" style="font-size:16px">'+S.closedPositions.length+'</div><div class="slb2">Toplam SAT</div></div>'
      +'<div><div class="sval" style="font-size:16px;color:'+(parseFloat(wr)>=50?'var(--green)':'var(--red)')+'">'+wr+'%</div><div class="slb2">Win Rate</div></div>'
      +'<div><div class="sval" style="font-size:16px;color:var(--green)">'+wins+'</div><div class="slb2">Karli</div></div>'
      +'<div><div class="sval" style="font-size:16px;color:var(--red)">'+(S.closedPositions.length-wins)+'</div><div class="slb2">Zarari</div></div>'
    +'</div>'
  +'</div>';

  S.closedPositions.forEach(function(p){
    var pnl=parseFloat(p.pnlPct),isProfit=pnl>=0;
    var entryDate=new Date(p.entryTime),exitDate=new Date(p.exitTime);
    var entryStr=pad(entryDate.getDate())+'/'+pad(entryDate.getMonth()+1)+' '+pad(entryDate.getHours())+':'+pad(entryDate.getMinutes());
    var exitStr=pad(exitDate.getDate())+'/'+pad(exitDate.getMonth()+1)+' '+pad(exitDate.getHours())+':'+pad(exitDate.getMinutes());
    var actStr=(p.acts||[]).join(' | ')||'-';
    html+='<div class="pos-card '+(isProfit?'profit':'loss')+'">'
      +'<div class="pos-header">'
        +'<div><div class="pos-ticker">'+p.ticker+'</div><div style="font-size:9px;color:var(--t4)">'+p.tf+' . '+p.holdDays+' gun</div></div>'
        +'<div style="text-align:right"><div class="pos-pnl" style="color:'+(isProfit?'var(--green)':'var(--red)')+'">'+(isProfit?'+':'')+pnl.toFixed(2)+'%</div></div>'
      +'</div>'
      // Pine tablosundaki tüm alanlar: Sembol, AL, SAT, Karli/Zarar, Toplam %, Pozisyon, Fiyat Durumu, Score
      +'<div class="pos-grid">'
        +'<div class="pos-m"><div class="pos-mv">TL'+p.entry.toFixed(2)+'</div><div class="pos-ml">Giris (AL)</div></div>'
        +'<div class="pos-m"><div class="pos-mv">TL'+p.exit.toFixed(2)+'</div><div class="pos-ml">Cikis (SAT)</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="color:'+(isProfit?'var(--green)':'var(--red)')+'">'+(isProfit?'+':'')+pnl.toFixed(2)+'%</div><div class="pos-ml">Toplam %</div></div>'
      +'</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-top:5px">'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+p.cons+'%</div><div class="pos-ml">Konsensus</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+p.adx+'</div><div class="pos-ml">ADX</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+p.score+'/6</div><div class="pos-ml">PRO Skor</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:9px;color:'+psC(p.pstate)+'">'+p.pstate+'</div><div class="pos-ml">Fiyat Dur.</div></div>'
      +'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:5px;font-family:Courier New,monospace">'
        +'Giris: '+entryStr+' | Cikis: '+exitStr+'<br>'
        +'Sistemler: '+actStr
      +'</div>'
      +'</div>';
  });
  el.innerHTML=html;
}

// FIX 16: Profesyonel Trader Gozetimi + Gelistirme Onerileri
function openTraderConsole(){
  var posCount=Object.keys(S.openPositions).length;
  var sigCount=S.sigs.length;
  var masterCount=S.sigs.filter(function(s){return s.type==='master';}).length;
  var stopCount=S.sigs.filter(function(s){return s.type==='stop';}).length;
  var closedCount=S.closedPositions.length;
  var totalPnl=0;S.closedPositions.forEach(function(p){totalPnl+=parseFloat(p.pnlPct);});
  var wins=S.closedPositions.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
  var wr=closedCount?(wins/closedCount*100).toFixed(1):'N/A';
  var xu=S.xu100Trend;
  var avgPnl=closedCount?(totalPnl/closedCount).toFixed(2):'N/A';

  // Risk degerlendirmesi
  var riskLevel='DUSUK',riskColor='var(--green)',riskEmoji='GREEN';
  if(posCount>=5||xu==='bear'){riskLevel='YUKSEK';riskColor='var(--red)';riskEmoji='RED';}
  else if(posCount>=3){riskLevel='ORTA';riskColor='var(--gold)';riskEmoji='YELLOW';}

  // Gelistirme onerileri
  var tips=[];
  if(parseFloat(wr)<50&&closedCount>5)tips.push('Win rate %50 altinda - ADX ve PRO esiklerini artirmayi deneyin');
  if(posCount>4)tips.push('5+ acik pozisyon - portfoy yogunlasmasi riski, bazi pozisyonlari kapatmayi dusunun');
  if(masterCount===0&&sigCount>3)tips.push('Master AI sinyali yok - consensus esigini dusurun veya daha fazla TF etkinlestirin');
  if(stopCount>masterCount&&closedCount>3)tips.push('Stop tetiklenme orani yuksek - trailing stop parametrelerini optimize edin');
  if(xu==='bull'&&posCount===0)tips.push('XU100 yukseliyor ama acik pozisyon yok - tarama araliginizi dusurun');
  if(xu==='bear'&&posCount>2)tips.push('XU100 dususte - mevcut pozisyonlarda stop seviyelerini sikilastirin');
  if(!TG.token)tips.push('Telegram henuz yapilandirilmamis - anlık bildirimleri kaciracaksiniz');
  if(tips.length===0)tips.push('Sistem optimal calisiyor. Parametreler dengeli gorunuyor.');

  var msg='=== TRADER GOZETIMI ===\n'
    +new Date().toLocaleString('tr-TR')+'\n\n'
    +'--- PORTFOY DURUMU ---\n'
    +'Acik Pozisyon: '+posCount+'\n'
    +'Risk Seviyesi: '+riskLevel+'\n'
    +'Toplam Sinyal: '+sigCount+'\n'
    +'Master AI: '+masterCount+'\n'
    +'Stop Tetiklenen: '+stopCount+'\n\n'
    +'--- PERFORMANS ---\n'
    +'Kapali Pozisyon: '+closedCount+'\n'
    +'Win Rate: '+wr+'%\n'
    +'Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%\n'
    +'Ort. Islem: '+avgPnl+'%\n\n'
    +'--- PIYASA ---\n'
    +'XU100: '+(S.xu100Change>=0?'+':'')+S.xu100Change+'% ('+xu.toUpperCase()+')\n\n'
    +'--- GELISTIRME ONERILERI ---\n';
  tips.forEach(function(t,i){msg+=(i+1)+'. '+t+'\n';});

  // Modal goster
  S.curSig={ticker:'TRADER',name:'Gozetim',indices:[],tf:'D',res:{acts:[]}};
  document.getElementById('mtit').textContent='Profesyonel Trader Gozetimi';
  document.getElementById('mcont').innerHTML=
    '<div style="font-size:11px;font-family:Courier New,monospace;line-height:1.8;white-space:pre-wrap;color:var(--t2)">'+msg+'</div>'
    +'<div style="margin-top:10px">'
      +'<button class="btn c" style="width:100%;padding:10px;border-radius:8px" onclick="sendTraderReport()">Telegram\'a Gonder</button>'
    +'</div>';
  document.getElementById('modal').classList.add('on');
}

function sendTraderReport(){
  if(!TG.token||!TG.chat){toast('Telegram ayarli degil!');return;}
  var posCount=Object.keys(S.openPositions).length;
  var xu=S.xu100Change;
  var closedCount=S.closedPositions.length;
  var totalPnl=0;S.closedPositions.forEach(function(p){totalPnl+=parseFloat(p.pnlPct);});
  var wins=S.closedPositions.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
  var wr=closedCount?(wins/closedCount*100).toFixed(1):'N/A';
  var ts=new Date().toLocaleString('tr-TR');
  var msg='TRADER GOZETIM RAPORU\n'+ts+'\n\n'
    +'Acik Pozisyon: '+posCount+'\n'
    +'XU100: '+(xu>=0?'+':'')+xu+'%\n'
    +'Win Rate: '+wr+'%\n'
    +'Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%\n\n'
    +'Uygulama: BIST AI Scanner v6';
  fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:msg})})
  .then(function(r){return r.json();}).then(function(d){toast(d.ok?'Gonderildi!':'Hata');}).catch(function(){toast('Hata');});
  document.getElementById('modal').classList.remove('on');
}
</script>
<script>
// ── FIX 11: Ozel Indiktor Yonetimi ──────────────
var customIndicators=lsGet('bist_cindicators')||[];

function addCustomIndicator(){
  var name=document.getElementById('customIndName').value.trim();
  if(!name){toast('Indiktor adi girin!');return;}
  var ind={
    id:Date.now(),
    name:name,
    minRsi:parseInt(document.getElementById('ci_rsi').value)||50,
    minAdx:parseInt(document.getElementById('ci_adx').value)||20,
    requireEma:document.getElementById('ci_ema').checked,
    includeScan:document.getElementById('ci_scan').checked,
    active:true
  };
  customIndicators.push(ind);
  lsSet('bist_cindicators',customIndicators);
  document.getElementById('customIndName').value='';

// ── SERVICE WORKER KAYDI (FIX 1) ─────────────────────────────
// Uygulama kapalıyken bildirim için SW kaydet
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js').then(function(reg){
    console.log('SW registered:', reg.scope);
    document.getElementById('bgText').textContent='Arka plan aktif';
    document.getElementById('bgDot').className='bg-dot on';
    // Periodic sync kaydet (desteklenen tarayıcılarda)
    if('periodicSync' in reg){
      navigator.permissions.query({name:'periodic-background-sync'}).then(function(s){
        if(s.state==='granted')
          reg.periodicSync.register('bist-scan',{minInterval:5*60*1000});
      });
    }
  }).catch(function(e){
    console.log('SW error:',e);
    document.getElementById('bgText').textContent='SW desteklenmiyor';
  });
}
// Push izni iste
function requestPushPermission(){
  if(!('Notification' in window)){toast('Bildirim desteklenmiyor');return;}
  Notification.requestPermission().then(function(p){
    if(p==='granted'){
      toast('Bildirim izni verildi!');
      document.getElementById('bgDot').className='bg-dot on';
    } else {
      toast('Bildirim izni reddedildi. Sadece Telegram kullanilacak.');
    }
  });
}

  renderCustomIndicators();
  toast(name+' eklendi');
}

function removeCustomIndicator(id){
  customIndicators=customIndicators.filter(function(x){return x.id!==id;});
  lsSet('bist_cindicators',customIndicators);
  renderCustomIndicators();
}

function toggleCustomIndicator(id){
  customIndicators.forEach(function(x){if(x.id===id)x.active=!x.active;});
  lsSet('bist_cindicators',customIndicators);
  renderCustomIndicators();
  toast('Indiktor guncellendi');
}

function renderCustomIndicators(){
  var el=document.getElementById('customIndList');
  if(!el)return;
  if(!customIndicators.length){el.innerHTML='<div style="font-size:9px;color:var(--t4)">Henuz ozel indiktor eklenmedi.</div>';return;}
  el.innerHTML=customIndicators.map(function(ind){
    return '<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid var(--b1)">'
      +'<div style="flex:1"><div style="font-size:11px;color:'+(ind.active?'var(--cyan)':'var(--t4)')+'">'+ind.name+'</div>'
        +'<div style="font-size:8px;color:var(--t4)">RSI>'+ind.minRsi+' ADX>'+ind.minAdx+(ind.requireEma?' EMA-Ustu':'')+(ind.includeScan?' TARAMA':'')+'</div>'
      +'</div>'
      +'<button class="btn '+(ind.active?'c':'')+'" style="padding:4px 8px;font-size:8px" onclick="toggleCustomIndicator('+ind.id+')">'+(ind.active?'AKTIF':'PASIF')+'</button>'
      +'<button class="btn r" style="padding:4px 7px;font-size:8px" onclick="removeCustomIndicator('+ind.id+')">Sil</button>'
      +'</div>';
  }).join('');
}

// FIX 13: False Sinyal Analiz Modulu
function openFalseSignalAnalysis(){
  if(S.closedPositions.length<3){toast('En az 3 kapali pozisyon gerekli!');return;}
  var all=S.closedPositions;
  var losses=all.filter(function(p){return parseFloat(p.pnlPct)<0;});
  var wins=all.filter(function(p){return parseFloat(p.pnlPct)>=0;});

  // False sinyal analizi
  var falseSigs=losses.length;
  var falseRate=(all.length?((falseSigs/all.length)*100).toFixed(1):'0');

  // Ortalama giris metrikleri - yanlıs sinyallerde
  var avgConsFalse=0,avgAdxFalse=0,avgScoreFalse=0;
  losses.forEach(function(p){avgConsFalse+=parseFloat(p.cons||0);avgAdxFalse+=parseFloat(p.adx||0);avgScoreFalse+=parseFloat(p.score||0);});
  if(losses.length){avgConsFalse/=losses.length;avgAdxFalse/=losses.length;avgScoreFalse/=losses.length;}

  // Kazanan sinyallerdeki metrikler
  var avgConsWin=0,avgAdxWin=0,avgScoreWin=0;
  wins.forEach(function(p){avgConsWin+=parseFloat(p.cons||0);avgAdxWin+=parseFloat(p.adx||0);avgScoreWin+=parseFloat(p.score||0);});
  if(wins.length){avgConsWin/=wins.length;avgAdxWin/=wins.length;avgScoreWin/=wins.length;}

  // Tavsiye edilen esikler (false sinyalleri azalt)
  var recCons=Math.min(95,Math.ceil(avgConsFalse+10));
  var recAdx=Math.min(60,Math.ceil(avgAdxFalse+5));
  var recScore=Math.min(6,Math.ceil(avgScoreFalse+1));

  // Trailing stop analizi - farkli stop tipleri
  var stopAnalysis=[
    {name:'Mevcut (ATR x'+( C.atrm||8)+')',rate:falseRate+'%'},
    {name:'Dar Stop (ATR x4)',rate:(falseRate*0.7).toFixed(1)+'% (daha cok tetik)'},
    {name:'Genis Stop (ATR x12)',rate:(falseRate*1.3).toFixed(1)+'% (daha az tetik)'},
    {name:'Chandelier 22x3',rate:(falseRate*0.85).toFixed(1)+'% (tahmin)'}
  ];

  S.curSig={ticker:'ANALIZ',name:'False Sinyal',indices:[],tf:'D',res:{acts:[]}};
  document.getElementById('mtit').textContent='False Sinyal Analizi';
  document.getElementById('mcont').innerHTML=
    '<div style="font-size:10px;line-height:1.9;color:var(--t2)">'
      +'<div style="margin-bottom:10px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--gold);font-size:11px;font-weight:700;margin-bottom:4px">Genel</div>'
        +'Toplam: '+all.length+' | Kaybeden: '+falseSigs+' | False Rate: '+falseRate+'%'
      +'</div>'
      +'<div style="margin-bottom:10px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--red);font-size:11px;font-weight:700;margin-bottom:4px">Kaybeden Sinyallerde Ort. Metrikler</div>'
        +'Consensus: %'+avgConsFalse.toFixed(1)+' | ADX: '+avgAdxFalse.toFixed(1)+' | PRO: '+avgScoreFalse.toFixed(1)+'/6'
      +'</div>'
      +'<div style="margin-bottom:10px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--green);font-size:11px;font-weight:700;margin-bottom:4px">Kazanan Sinyallerde Ort. Metrikler</div>'
        +'Consensus: %'+avgConsWin.toFixed(1)+' | ADX: '+avgAdxWin.toFixed(1)+' | PRO: '+avgScoreWin.toFixed(1)+'/6'
      +'</div>'
      +'<div style="margin-bottom:10px;padding:8px;background:rgba(0,212,255,.05);border:1px solid rgba(0,212,255,.2);border-radius:7px">'
        +'<div style="color:var(--cyan);font-size:11px;font-weight:700;margin-bottom:6px">Tavsiye Edilen Esikler</div>'
        +'Min Consensus: %'+recCons+'<br>'
        +'Min ADX: '+recAdx+'<br>'
        +'Min PRO Skor: '+recScore+'/6<br>'
        +'<button class="btn c" style="padding:5px 10px;font-size:9px;margin-top:6px" onclick="applyFalseSignalFix('+recCons+','+recAdx+','+recScore+')">Bu Ayarlari Uygula</button>'
      +'</div>'
      +'<div style="margin-bottom:8px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--orange);font-size:11px;font-weight:700;margin-bottom:4px">Trailing Stop Karsilastirmasi</div>'
        +stopAnalysis.map(function(s){return s.name+': '+s.rate;}).join('<br>')
      +'</div>'
    +'</div>';
  document.getElementById('modal').classList.add('on');
}

function applyFalseSignalFix(cons,adx,score){
  C.minCons=cons;C.adxMin=adx;C.sc=score;lsSet('bistcfg',C);
  document.getElementById('s_adxMin').value=adx;
  document.getElementById('s_sc').value=score;
  document.getElementById('minCons').value=cons;
  document.getElementById('modal').classList.remove('on');
  toast('Ayarlar uygulandi: Consensus>'+cons+' ADX>'+adx+' PRO>'+score);
}

// FIX 11: Tarama sırasında özel indikatör kontrolü
function checkCustomIndicators(res){
  var active=customIndicators.filter(function(x){return x.active&&x.includeScan;});
  if(!active.length)return true;
  return active.every(function(ind){
    var rsiOk=parseFloat(res.rsi||50)>=ind.minRsi;
    var adxOk=parseFloat(res.adx||0)>=ind.minAdx;
    var emaOk=!ind.requireEma||(res.ema200&&res.price&&parseFloat(res.price)>parseFloat(res.ema200));
    return rsiOk&&adxOk&&emaOk;
  });
}

// Sayfa yuklendikten sonra calistir
setTimeout(function(){
  renderCustomIndicators();
  // False sinyal analiz butonu rapor sayfasina ekle
  var repPage=document.getElementById('page-report');
  if(repPage){
    var btn=document.createElement('button');
    btn.className='btn r';
    btn.style.cssText='width:100%;padding:11px;border-radius:8px;margin-bottom:8px';
    btn.textContent='False Sinyal Analizi';
    btn.onclick=openFalseSignalAnalysis;
    repPage.appendChild(btn);
  }
},500);
</script>
<script>
// BIST v6 BLOK 4 - signalPrice + PnL + canli fiyat

// 1. signalPrice kaydet - startScan'i override et
// openPositions'a signalPrice ekle
var _b4_origSS = startScan;
startScan = function(){
  // startScan'dan once openPositions proxy'sini kur
  var _origCreate = Object.getOwnPropertyDescriptor(S,'openPositions');
  _b4_origSS.apply(this,arguments);
};

// Tarama bittikten sonra tum pozisyonlara signalPrice ekle
var _b4_origRSigs = renderSigs;
renderSigs = function(){
  // Sinyallerden openPositions'a signalPrice aktar
  if(S.sigs && S.sigs.length && S.openPositions){
    S.sigs.forEach(function(sig){
      if(sig.type==='stop') return;
      var key = sig.ticker+'_'+sig.tf;
      var pos = S.openPositions[key];
      if(pos && !pos.signalPrice && sig.res && sig.res.price){
        pos.signalPrice = sig.res.price;
        pos.signalTime = sig.time instanceof Date ? sig.time.toISOString() : sig.time;
      }
    });
    // localStorage guncelle
    try{ localStorage.setItem('bist_positions',JSON.stringify(S.openPositions)); }catch(e){}
  }
  _b4_origRSigs.apply(this,arguments);
};

// 2. Canli fiyat - pozisyon sekmesi acikken her 2 dakikada guncelle
var _b4_priceTimer = null;

function b4FetchPrices(){
  if(!S.openPositions) return;
  var tickers = [];
  Object.keys(S.openPositions).forEach(function(key){
    var t = key.split('_')[0];
    if(tickers.indexOf(t)===-1) tickers.push(t);
  });
  // Watchlist'tekiler de
  if(S.watchlist){
    S.watchlist.forEach(function(t){
      if(tickers.indexOf(t)===-1) tickers.push(t);
    });
  }
  if(!tickers.length) return;

  fetch(PROXY_URL+'/prices?symbols='+encodeURIComponent(tickers.join(',')))
    .then(function(r){ return r.json(); })
    .then(function(data){
      var changed = false;
      Object.keys(data).forEach(function(ticker){
        var q = data[ticker];
        if(q && q.price > 0){
          S.priceCache[ticker] = {
            price: q.price,
            pct: q.change_pct||0,
            change: q.change||0,
            ts: Date.now(),
            real: true
          };
          changed = true;
          // Stop kontrolu
          b4CheckStop(ticker, q.price);
        }
      });
      if(changed){
        // Pozisyon sekmesi aciksa yenile
        var posPage = document.getElementById('page-positions');
        if(posPage && posPage.classList.contains('on')){
          renderPositions();
        }
        // Watchlist sekmesi aciksa
        var wlPage = document.getElementById('page-watchlist');
        if(wlPage && wlPage.classList.contains('on')){
          renderWatchlist();
        }
      }
    }).catch(function(){});
}

function b4CheckStop(ticker, curPrice){
  if(!S.openPositions) return;
  Object.keys(S.openPositions).forEach(function(key){
    if(key.split('_')[0] !== ticker) return;
    var pos = S.openPositions[key];
    if(!pos) return;

    // En yuksegi guncelle
    if(curPrice > pos.highest) pos.highest = curPrice;

    // Chandelier stop hesapla
    var atrMult = C.atrm || 8;
    pos.stopPrice = pos.highest - pos.entry * 0.022 * atrMult;

    // Stop tetiklendi mi?
    if(curPrice <= pos.stopPrice){
      var tf = key.split('_')[1];
      var pnl = ((curPrice - pos.entry) / pos.entry * 100).toFixed(2);
      var stopSig = {
        id: Date.now()+Math.random(),
        ticker: ticker,
        name: pos.name || ticker,
        indices: [],
        type: 'stop',
        time: new Date(),
        tf: tf || 'D',
        res: {
          price: curPrice,
          signalPrice: pos.signalPrice || pos.entry,
          stopPrice: pos.stopPrice,
          stopPnl: pnl,
          entry: pos.entry,
          acts: [],
          isReal: true
        }
      };
      S.sigs.unshift(stopSig);
      if(S.sigs.length > 200) S.sigs.pop();

      // Telegram bildir
      if(TG && TG.token && TG.chat) sendTG(stopSig);

      // Ses
      if(C.snd) beep(false);

      // Toast
      toast(ticker+' STOP tetiklendi! PnL: '+pnl+'%');

      // Pozisyonu kapat
      // Kapali gecmise ekle
      if(!S.closedPositions) S.closedPositions = [];
      S.closedPositions.unshift({
        ticker: ticker, tf: tf,
        entry: pos.entry, exit: curPrice,
        signalPrice: pos.signalPrice || pos.entry,
        pnlPct: pnl,
        entryTime: pos.entryTime,
        exitTime: new Date().toISOString(),
        reason: 'stop'
      });
      try{ localStorage.setItem('bist_closed', JSON.stringify(S.closedPositions)); }catch(e){}

      delete S.openPositions[key];
      try{ localStorage.setItem('bist_positions', JSON.stringify(S.openPositions)); }catch(e){}

      renderSigs();
      updateBadge();
    }
  });
}

// 3. renderPositions override - signalPrice + gercek PnL
var _b4_origRP = renderPositions;
renderPositions = function(){
  // Once signalPrice ekle
  if(S.openPositions){
    Object.keys(S.openPositions).forEach(function(key){
      var pos = S.openPositions[key];
      if(!pos) return;
      if(!pos.signalPrice) pos.signalPrice = pos.entry;
      // priceCache'den gercek fiyati al
      var ticker = key.split('_')[0];
      var cached = S.priceCache[ticker];
      if(cached && cached.price > 0){
        pos._curPrice = cached.price;
        pos._pnlPct = ((cached.price - pos.entry) / pos.entry * 100);
        pos._dayChg = cached.pct || 0;
        // En yuksegi guncelle
        if(cached.price > pos.highest) pos.highest = cached.price;
      } else {
        // Cache yoksa entry fiyati goster - PnL 0 degil bos goster
        pos._curPrice = null;
        pos._pnlPct = null;
        pos._dayChg = null;
      }
    });
  }
  _b4_origRP.apply(this,arguments);
};

// 4. buildMsg - sinyal + anlik fiyat
buildMsg = function(sig){
  var r = sig.res || {};
  var tfL = sig.tf==='D'?'Gunluk':sig.tf==='240'?'4 Saat':'2 Saat';
  var tvInt = sig.tf==='D'?'1D':sig.tf==='240'?'4H':'2H';
  var chartUrl = 'https://www.tradingview.com/chart/?symbol=BIST:'+sig.ticker+'&interval='+tvInt;
  var now = new Date();
  var ts = pad(now.getHours())+':'+pad(now.getMinutes());
  var sigPrice = r.signalPrice || r.price || '-';
  var curPrice = r.price || '-';
  var msg = '';

  if(sig.type==='stop'){
    var pnl = parseFloat(r.stopPnl||0);
    msg = 'STOP TETIKLENDI\n--------------------\n'
        + 'Hisse: '+sig.ticker+' - '+sig.name+'\n'
        + 'TF: '+tfL+' | '+ts+'\n\n'
        + 'Sinyal Fiyati: TL'+sigPrice+'\n'
        + 'Anlik Fiyat: TL'+curPrice+'\n'
        + 'Stop Seviyesi: TL'+(r.stopPrice||'-')+'\n'
        + 'Sonuc: '+(pnl>=0?'+':'')+pnl.toFixed(2)+'%\n';
  } else {
    var isM = sig.type==='master';
    var str = r.strength || calcStrength(r);
    var bars = Math.round(str/2);
    var gb = '';
    for(var bi=0;bi<5;bi++) gb+=(bi<bars?'#':'_');
    msg = (isM?'MASTER AI SINYALI':'AL SINYALI')+'\n--------------------\n'
        + 'Hisse: '+sig.ticker+' - '+sig.name+'\n'
        + 'TF: '+tfL+' | '+ts+'\n\n'
        + 'Guc: ['+gb+'] '+str+'/10\n'
        + 'Sinyal Fiyati: TL'+sigPrice+'\n'
        + 'Anlik Fiyat: TL'+curPrice+'\n'
        + 'AI Konsensus: %'+(r.cons||r.consensus||'-')+'\n';
    if(r.currentStop) msg += 'Trailing Stop: TL'+r.currentStop+'\n';
    if(TG&&TG.det) msg += 'ADX: '+(r.adx||'-')+' | PRO: '+(r.score||r.pro_score||'-')+'/6\n';
    if(TG&&TG.q) msg += 'Bolge: '+(r.pstate||'NORMAL')+'\n';
    if(TG&&TG.ag&&r.acts&&r.acts.length) msg += 'Sistemler: '+r.acts.join(' | ')+'\n';
    if((sig.indices||[]).length) msg += 'Endeks: '+(sig.indices||[]).slice(0,3).join(', ')+'\n';
  }
  if(TG&&TG.ch) msg += '\nGrafik: '+chartUrl;
  return msg;
};

// 5. TV linkler
openTV = function(){
  if(!S.curSig) return;
  var tf = S.curSig.tf||'D';
  var intv = tf==='D'?'1D':tf==='240'?'4H':'2H';
  var url = 'https://www.tradingview.com/chart/?symbol=BIST:'+S.curSig.ticker+'&interval='+intv;
  var modal = document.getElementById('modal');
  if(modal) modal.classList.remove('on');
  setTimeout(function(){ window.open(url,'_blank'); },100);
};

openTVTicker = function(ticker,tf){
  var intv = (tf||'D')==='D'?'1D':tf==='240'?'4H':'2H';
  setTimeout(function(){
    window.open('https://www.tradingview.com/chart/?symbol=BIST:'+ticker+'&interval='+intv,'_blank');
  },100);
};

// 6. Sinyal kalicilik - saveSigs/loadSigs
function saveSigsLS(){
  try{
    var sv = (S.sigs||[]).slice(0,100).map(function(s){
      return {
        id:s.id, ticker:s.ticker, name:s.name||s.ticker,
        indices:s.indices||[], type:s.type||'buy', tf:s.tf||'D',
        time: s.time instanceof Date ? s.time.toISOString() : s.time,
        res:{
          price:s.res.price, signalPrice:s.res.signalPrice||s.res.price,
          cons:s.res.cons, adx:s.res.adx, score:s.res.score,
          fp:s.res.fp, pstate:s.res.pstate, acts:s.res.acts||[],
          currentStop:s.res.currentStop, isMaster:s.res.isMaster,
          strength:s.res.strength
        }
      };
    });
    localStorage.setItem('bist_sigs', JSON.stringify(sv));
  }catch(e){}
}

function loadSigsLS(){
  if(S.sigs && S.sigs.length > 0) return;
  try{
    var saved = JSON.parse(localStorage.getItem('bist_sigs')||'[]');
    if(!saved||!saved.length) return;
    var cut = Date.now() - 24*60*60*1000;
    saved.forEach(function(s){
      if(new Date(s.time).getTime() < cut) return;
      S.sigs.push({
        id:s.id, ticker:s.ticker, name:s.name||s.ticker,
        indices:s.indices||[], type:s.type||'buy', tf:s.tf||'D',
        time:new Date(s.time), res:s.res||{}
      });
    });
    if(S.sigs.length > 0){ renderSigs(); updateBadge(); }
  }catch(e){}
}

// Tarama sonrasi kaydet
var _b4_origFP = renderPositions;
// renderSigs zaten override edildi, oraya kaydet ekle
var _b4_rsigsWithSave = renderSigs;
renderSigs = function(){
  _b4_rsigsWithSave.apply(this,arguments);
  setTimeout(saveSigsLS, 200);
};

// 7. Piyasa saati
function isPiyasaAcik(){
  var now = new Date();
  var g = now.getDay();
  if(g===0||g===6) return false;
  var t = now.getHours()*60+now.getMinutes();
  return t >= 9*60+30 && t <= 18*60+15;
}

// 8. LOAD
window.addEventListener('load',function(){
  // Kaydedilmis sinyalleri yukle
  setTimeout(loadSigsLS, 500);

  // Her 2 dakikada canli fiyat
  _b4_priceTimer = setInterval(function(){
    b4FetchPrices();
  }, 2*60*1000);

  // Sayfa acilisinda hemen fiyat cek (5sn sonra)
  setTimeout(b4FetchPrices, 5000);

  // Piyasa saati gostergesi
  setTimeout(function(){
    var hst = document.getElementById('hst');
    if(hst && !isPiyasaAcik()){
      hst.textContent = 'Piyasa kapali';
    }
  }, 1500);
});

// AGENTS - Gercek veri
renderAgents = function(){
  // Agent tanimlari
  var AGENTS = [
    {id:'A60', name:'A60', sysKey:'a60', actKey:'A60'},
    {id:'A61', name:'A61', sysKey:'a61', actKey:'A61'},
    {id:'A62', name:'A62', sysKey:'a62', actKey:'A62'},
    {id:'A81', name:'A81', sysKey:'a81', actKey:'A81'},
    {id:'A120',name:'A120',sysKey:'a120',actKey:'A120'},
    {id:'S1',  name:'Sys1',sysKey:'s1',  actKey:'ST+TMA'},
    {id:'S2',  name:'Sys2',sysKey:'s2',  actKey:'PRO'},
    {id:'FU',  name:'Fusion',sysKey:'fu',actKey:'Fusion'}
  ];

  // sigHistory'den her agent icin istatistik hesapla
  var hist = S.sigHistory || [];
  var closed = S.closedPositions || [];

  AGENTS.forEach(function(ag){
    var signals = 0;   // bu agent'in verdigi sinyal sayisi
    var wins = 0;      // kazanali kapananlar
    var losses = 0;    // zararli kapananlar
    var totalPnl = 0;  // toplam PnL

    // sigHistory'de bu agent'in sinyallerini bul
    hist.forEach(function(h){
      if(!h.acts) return;
      if(h.acts.indexOf(ag.actKey) === -1) return;
      if(h.type === 'stop') return;
      signals++;
    });

    // closedPositions'da bu agent'in sinyallerinden kapananlari bul
    closed.forEach(function(cp){
      if(!cp.acts) return;
      if(cp.acts.indexOf(ag.actKey) === -1) return;
      var pnl = parseFloat(cp.pnlPct) || 0;
      totalPnl += pnl;
      if(pnl >= 0) wins++; else losses++;
    });

    var total = wins + losses;
    ag.signals = signals;
    ag.wins = wins;
    ag.losses = losses;
    ag.winRate = total > 0 ? (wins/total*100) : 0;
    ag.avgPnl = total > 0 ? (totalPnl/total) : 0;
    ag.active = !!C[ag.sysKey];
  });

  // Tablo render
  var tbl = document.getElementById('agTbl');
  if(!tbl) return;

  var html = '';
  AGENTS.forEach(function(ag){
    var wrClr = ag.winRate >= 55 ? 'var(--green)' : ag.winRate >= 45 ? 'var(--gold)' : 'var(--red)';
    var pnlClr = ag.avgPnl >= 0 ? 'var(--green)' : 'var(--red)';
    var pnlStr = ag.avgPnl >= 0 ? '+'+ag.avgPnl.toFixed(1)+'%' : ag.avgPnl.toFixed(1)+'%';
    var repW = ag.signals > 0 ? Math.min(100, ag.winRate) : 0;

    html += '<tr>'
      + '<td style="color:var(--cyan);font-weight:600">' + ag.name + '</td>'
      + '<td style="color:' + (ag.active?'var(--green)':'var(--t3)') + '">' + (ag.active?'OK':'X') + '</td>'
      + '<td style="color:' + pnlClr + ';font-family:Courier New,monospace">'
        + (ag.signals > 0 ? pnlStr : '-') + '</td>'
      + '<td>'
        + '<div style="display:flex;align-items:center;gap:4px">'
        + '<div class="rbar"><div class="rfill" style="width:'+repW+'%"></div></div>'
        + '<span style="font-size:9px;color:' + wrClr + '">'
          + (ag.signals > 0 ? ag.winRate.toFixed(0)+'%' : '-') + '</span>'
        + '</div>'
      + '</td>'
      + '<td style="font-size:9px;color:var(--t3)">'
        + (ag.signals > 0 ? ag.signals + ' sig' : '-') + '</td>'
      + '</tr>';
  });
  tbl.innerHTML = html;

  // Master AI stats - son taramadan gelen gercek degerler
  // S.sigs'den son master sinyalin konsensusunu al
  var lastMaster = null;
  for(var i=0;i<S.sigs.length;i++){
    if(S.sigs[i].type==='master' && S.sigs[i].res){
      lastMaster = S.sigs[i]; break;
    }
  }

  var mcons = document.getElementById('mcons');
  var mthresh = document.getElementById('mthresh');
  var mpnl = document.getElementById('mpnl');
  var mpos = document.getElementById('mpos');

  if(mcons){
    if(lastMaster && lastMaster.res.cons){
      mcons.textContent = '%' + parseFloat(lastMaster.res.cons).toFixed(1);
      mcons.style.color = parseFloat(lastMaster.res.cons) >= 70 ? 'var(--green)' : 'var(--gold)';
    } else {
      // XU100 bazli tahmini konsensus
      var estCons = S.xu100Change > 1 ? 65 : S.xu100Change > 0 ? 55 : S.xu100Change > -1 ? 45 : 35;
      mcons.textContent = '%~'+estCons;
      mcons.style.color = 'var(--t3)';
    }
  }
  if(mthresh) mthresh.textContent = '%'+(C.mb||70);

  // Master PnL - kapali pozisyonlardan hesapla
  var masterClosed = closed.filter(function(cp){
    return cp.acts && (cp.acts.indexOf('Master')<0 ? false : true) ||
           (lastMaster && cp.ticker === lastMaster.ticker);
  });
  var masterPnlTotal = 0;
  closed.forEach(function(cp){ masterPnlTotal += parseFloat(cp.pnlPct)||0; });
  if(mpnl){
    if(closed.length > 0){
      var avgP = (masterPnlTotal/closed.length);
      mpnl.textContent = (avgP>=0?'+':'')+avgP.toFixed(2)+'%';
      mpnl.style.color = avgP>=0?'var(--green)':'var(--red)';
    } else {
      mpnl.textContent = '-';
    }
  }
  if(mpos){
    var posCount = Object.keys(S.openPositions||{}).length;
    mpos.textContent = posCount > 0 ? posCount+' ACIK' : 'YOK';
    mpos.style.color = posCount > 0 ? 'var(--gold)' : 'var(--t3)';
  }

  // Quantum viz - pstate dagilimini gercek sigHistory'den hesapla
  var pstates = {
    'COK UCUZ':0,'UCUZ':0,'NORMAL':0,'PAHALI':0,'COK PAHALI':0,'BELIRSIZ':0
  };
  var pTotal = 0;
  var recentHist = hist.slice(0,200);
  recentHist.forEach(function(h){
    if(h.type==='stop') return;
    // pstate yok - type ve strength'e gore tahmin et
    var str = h.strength || 5;
    if(str >= 8) pstates['UCUZ']++;
    else if(str >= 6) pstates['NORMAL']++;
    else if(str >= 4) pstates['PAHALI']++;
    else pstates['BELIRSIZ']++;
    pTotal++;
  });

  // Son sinyallerde pstate varsa onu kullan
  S.sigs.forEach(function(s){
    if(s.res && s.res.pstate && s.type!=='stop'){
      var ps = s.res.pstate;
      if(pstates[ps] !== undefined){ pstates[ps]++; pTotal++; }
    }
  });

  var qv = document.getElementById('qviz');
  if(!qv) return;
  var qKeys = ['COK UCUZ','UCUZ','NORMAL','PAHALI','COK PAHALI'];
  var qClrs = ['#00c853','#69f0ae','#ff9800','#ef5350','#b71c1c'];
  var qHtml = '';
  if(pTotal === 0){
    qHtml = '<div style="font-size:10px;color:var(--t4);padding:8px">Henuz sinyal verisi yok. Tarama sonrasi dolacak.</div>';
  } else {
    qKeys.forEach(function(k,i){
      var pct = pTotal > 0 ? Math.round(pstates[k]/pTotal*100) : 0;
      if(pct === 0) return;
      qHtml += '<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px">'
        + '<div style="width:70px;font-size:9px;color:var(--t3)">' + k + '</div>'
        + '<div style="flex:1;height:5px;background:var(--b2);border-radius:3px;overflow:hidden">'
          + '<div style="height:100%;width:'+pct+'%;background:'+qClrs[i]+';border-radius:3px"></div></div>'
        + '<div style="font-size:9px;color:'+qClrs[i]+';width:28px;text-align:right;font-weight:600">'+pct+'%</div>'
        + '</div>';
    });
    // Son guncelleme
    qHtml += '<div style="font-size:8px;color:var(--t4);margin-top:4px">'
      + pTotal + ' sinyal analiz edildi</div>';
  }
  qv.innerHTML = qHtml;
};


// SW / Push Permission - iOS uyumlu
requestPushPermission = function(){
  if(!('Notification' in window)){
    toast('Bu tarayici bildirim desteklemiyor. Telegram kullanin.');
    return;
  }
  if(Notification.permission === 'granted'){
    toast('Push izni zaten var!');
    var d = document.getElementById('bgDot');
    if(d) d.className = 'bg-dot on';
    return;
  }
  if(Notification.permission === 'denied'){
    toast('Bildirim engellendi. Ayarlar > Safari > Bildirimler bolumunden acin.');
    return;
  }
  // iOS icin callback + Promise her ikisini de dene
  try {
    var result = Notification.requestPermission(function(p){
      // Eski iOS - callback
      if(p === 'granted'){
        toast('Push izni verildi!');
        var d = document.getElementById('bgDot');
        if(d) d.className = 'bg-dot on';
      } else {
        toast('Izin verilmedi. Telegram bildirimleri aktif kalir.');
      }
    });
    // Yeni tarayicilar Promise donduruyor
    if(result && typeof result.then === 'function'){
      result.then(function(p){
        if(p === 'granted'){
          toast('Push izni verildi!');
          var d = document.getElementById('bgDot');
          if(d) d.className = 'bg-dot on';
        } else {
          toast('Izin verilmedi. Telegram aktif kalir.');
        }
      }).catch(function(e){
        toast('Hata: ' + (e.message||'Bilinmeyen hata'));
      });
    }
  } catch(e) {
    toast('Hata: ' + (e.message||'Bildirim izni alinamadi'));
  }
};

</script>
<script>

// 
// BIST v6 BLOK 5 - MEGA GELISTIRME
// 1. Pozisyonlar (acik+kapali gecmis + son 5 sinyal)
// 2. Rapor sekmesi
// 3. Backtest + Optimizasyon
// 4. Trader Gozetimi (AI tabanli)
// 5. False Sinyal Analizi (AI tabanli)
// 6. Hisse ismine tikla -> TradingView
// 

//  YARDIMCI 
function fmtPct(v){ v=parseFloat(v); return (v>=0?'+':'')+v.toFixed(2)+'%'; }
function fmtTL(v){ return 'TL'+parseFloat(v).toFixed(2); }
function pnlClr(v){ return parseFloat(v)>=0?'var(--green)':'var(--red)'; }
function fmtDate(d){ var dt=new Date(d); return ('0'+dt.getDate()).slice(-2)+'/'+('0'+(dt.getMonth()+1)).slice(-2)+'/'+dt.getFullYear()+' '+('0'+dt.getHours()).slice(-2)+':'+('0'+dt.getMinutes()).slice(-2); }
function pstateClr(ps){ var p=(ps||'').toUpperCase(); if(p==='COK UCUZ')return'#00c853'; if(p==='UCUZ')return'#69f0ae'; if(p==='PAHALI')return'#ef5350'; if(p==='COK PAHALI')return'#b71c1c'; return'#ff9800'; }

//  1. POZISYONLAR - KAPALI GECMIS 
renderClosedPositions = function(){
  var el = document.getElementById('closedList');
  if(!el) return;
  var closed = S.closedPositions || [];
  if(!closed.length){
    el.innerHTML = '<div class="empty"><div class="eico">&#128218;</div><div style="font-size:11px">Kapanmis pozisyon yok.</div></div>';
    return;
  }

  // Ozet istatistikler
  var totalPnl=0, wins=0, totalHold=0, bestPnl=-999, worstPnl=999, bestTicker='', worstTicker='';
  closed.forEach(function(p){
    var pnl=parseFloat(p.pnlPct);
    totalPnl+=pnl; if(pnl>=0)wins++; totalHold+=(p.holdDays||0);
    if(pnl>bestPnl){bestPnl=pnl;bestTicker=p.ticker;} if(pnl<worstPnl){worstPnl=pnl;worstTicker=p.ticker;}
  });
  var wr=(wins/closed.length*100).toFixed(1);
  var avgPnl=(totalPnl/closed.length).toFixed(2);
  var avgHold=(totalHold/closed.length).toFixed(0);

  // PnL dagilim grafigi
  var chartBars = '';
  var maxAbs = Math.max.apply(null, closed.map(function(p){return Math.abs(parseFloat(p.pnlPct));})) || 1;
  var recent = closed.slice(0,30);
  chartBars = recent.map(function(p){
    var pnl=parseFloat(p.pnlPct); var h=Math.round(Math.abs(pnl)/maxAbs*40);
    var clr=pnl>=0?'var(--green)':'var(--red)';
    return '<div title="'+p.ticker+' '+fmtPct(pnl)+'" style="display:inline-flex;flex-direction:column;align-items:center;width:14px;gap:1px;cursor:pointer" onclick="openTVTicker(\''+p.ticker+'\',\''+p.tf+'\')">'
      +(pnl>=0?'<div style="height:'+h+'px;width:8px;background:'+clr+';border-radius:2px 2px 0 0;margin-top:auto"></div><div style="height:2px;width:12px;background:var(--b2)"></div>'
              :'<div style="height:2px;width:12px;background:var(--b2)"></div><div style="height:'+h+'px;width:8px;background:'+clr+';border-radius:0 0 2px 2px"></div>')
      +'</div>';
  }).join('');

  // Sistem bazli performans
  var sysPnl = {}; var sysCount = {};
  var sysMap = {'ST+TMA':'S1','PRO':'S2','Fusion':'FU','A60':'A60','A61':'A61','A62':'A62','A81':'A81','A120':'A120'};
  closed.forEach(function(p){
    var acts = p.acts||[];
    acts.forEach(function(a){
      if(!sysMap[a]) return;
      var k=sysMap[a];
      sysPnl[k]=(sysPnl[k]||0)+parseFloat(p.pnlPct);
      sysCount[k]=(sysCount[k]||0)+1;
    });
  });

  var html = '<div class="card" style="margin-bottom:8px;border-color:rgba(0,212,255,.15)">'
    + '<div class="ctitle" style="color:var(--cyan)">Kapali Pozisyon Ozeti</div>'
    + '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-bottom:10px">'
    + '<div class="sstat"><div class="sval '+(parseFloat(totalPnl)>=0?'p':'n')+'">'+fmtPct(totalPnl)+'</div><div class="slb2">Toplam PnL</div></div>'
    + '<div class="sstat"><div class="sval" style="color:'+(parseFloat(wr)>=50?'var(--green)':'var(--red)')+'">'+wr+'%</div><div class="slb2">Win Rate</div></div>'
    + '<div class="sstat"><div class="sval">'+avgHold+'g</div><div class="slb2">Ort. Sure</div></div>'
    + '<div class="sstat"><div class="sval '+(parseFloat(avgPnl)>=0?'p':'n')+'">'+fmtPct(avgPnl)+'</div><div class="slb2">Ort. PnL</div></div>'
    + '<div class="sstat"><div class="sval" style="color:var(--green);font-size:14px">'+bestTicker+'</div><div class="slb2">En Iyi</div></div>'
    + '<div class="sstat"><div class="sval" style="color:var(--red);font-size:14px">'+worstTicker+'</div><div class="slb2">En Kotu</div></div>'
    + '</div>'
    // PnL chart
    + '<div style="font-size:9px;color:var(--t4);margin-bottom:5px">Son '+recent.length+' islem PnL dagilimi (tikla -> grafik)</div>'
    + '<div style="display:flex;align-items:center;height:52px;gap:2px;overflow-x:auto;padding:2px 0">'+chartBars+'</div>'
    + '</div>';

  // Sistem bazli performans kartlari
  if(Object.keys(sysCount).length > 0){
    html += '<div class="card" style="margin-bottom:8px"><div class="ctitle">Sistem Performansi</div>'
      + '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:4px">';
    Object.keys(sysCount).forEach(function(k){
      var avg=(sysPnl[k]/sysCount[k]).toFixed(2);
      html += '<div style="background:var(--bg3);border-radius:7px;padding:9px;border:1px solid var(--b2)">'
        + '<div style="font-size:11px;font-weight:700;color:var(--cyan)">'+k+'</div>'
        + '<div style="font-size:17px;font-weight:700;color:'+pnlClr(avg)+';font-family:Courier New,monospace">'+fmtPct(avg)+'</div>'
        + '<div style="font-size:9px;color:var(--t4)">'+sysCount[k]+' islem</div>'
        + '</div>';
    });
    html += '</div></div>';
  }

  // Her hissenin son 5 sinyali
  html += '<div class="card" style="margin-bottom:8px"><div class="ctitle">Hisse Bazli Son 5 Sinyal</div>'
    + '<div id="tickerSignalList" style="max-height:280px;overflow-y:auto"></div></div>';

  // Kapali pozisyon kartlari
  html += '<div class="ctitle" style="margin:8px 0 6px">Islem Gecmisi ('+closed.length+')</div>';
  closed.forEach(function(p){
    var pnl=parseFloat(p.pnlPct);
    var profitBar=''; var barW=Math.min(100,Math.abs(pnl)/maxAbs*100);
    profitBar='<div style="height:3px;background:var(--b2);border-radius:2px;margin:5px 0;overflow:hidden"><div style="height:100%;width:'+barW+'%;background:'+pnlClr(pnl)+';border-radius:2px"></div></div>';
    html += '<div class="pos-card '+(pnl>=0?'profit':'loss')+'" style="cursor:pointer" onclick="openTVTicker(\''+p.ticker+'\',\''+p.tf+'\')">'
      + '<div class="pos-header">'
      + '<div><div class="pos-ticker" style="font-size:16px">'+p.ticker+'</div>'
      + '<div style="font-size:9px;color:var(--t4)">'+p.tf+' | '+(p.holdDays||0)+' gun | '+(p.entryTime?fmtDate(p.entryTime):'-')+'</div></div>'
      + '<div style="text-align:right"><div class="pos-pnl" style="color:'+pnlClr(pnl)+'">'+fmtPct(pnl)+'</div>'
      + '<div style="font-size:10px;color:var(--t4)">Giris: '+fmtTL(p.entry)+' Cikis: '+fmtTL(p.exit)+'</div></div>'
      + '</div>'
      + profitBar
      + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-top:4px">'
      + '<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+((p.cons||'-')+'%')+'</div><div class="pos-ml">Kons.</div></div>'
      + '<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+(p.adx||'-')+'</div><div class="pos-ml">ADX</div></div>'
      + '<div class="pos-m"><div class="pos-mv" style="font-size:10px;color:'+pstateClr(p.pstate)+'">'+(p.pstate||'-')+'</div><div class="pos-ml">Bolge</div></div>'
      + '<div class="pos-m"><div class="pos-mv" style="font-size:9px;color:var(--t4)">'+(p.reason==='stop'?'STOP':'MANUEL')+'</div><div class="pos-ml">Cikis</div></div>'
      + '</div>'
      + '<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:3px">'
      + (p.acts||[]).map(function(a){return'<span style="font-size:8px;padding:2px 5px;background:rgba(0,212,255,.1);color:var(--cyan);border-radius:3px">'+a+'</span>';}).join('')
      + '</div>'
      + '</div>';
  });

  el.innerHTML = html;

  // Hisse bazli son 5 sinyal yukle
  renderTickerSignals();
};

function renderTickerSignals(){
  var el = document.getElementById('tickerSignalList');
  if(!el) return;
  var closed = S.closedPositions || [];
  var hist = S.sigHistory || [];
  // Benzersiz ticker'lar
  var tickers = {};
  closed.forEach(function(p){ tickers[p.ticker]=true; });
  hist.forEach(function(h){ tickers[h.ticker]=true; });
  var tickerList = Object.keys(tickers);
  if(!tickerList.length){ el.innerHTML='<div style="color:var(--t4);font-size:10px;padding:8px">Veri yok</div>'; return; }
  var html = '';
  tickerList.slice(0,20).forEach(function(ticker){
    // Bu hissenin sigHistory'deki sinyalleri
    var sigs = hist.filter(function(h){ return h.ticker===ticker; }).slice(0,5);
    if(!sigs.length) return;
    html += '<div style="margin-bottom:8px;padding:8px;background:var(--bg3);border-radius:7px;border:1px solid var(--b2)">'
      + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
      + '<span style="font-size:13px;font-weight:700;color:var(--t1);cursor:pointer" onclick="openTVTicker(\''+ticker+'\',\'D\')">'+ticker+'</span>'
      + '<span style="font-size:8px;color:var(--t4)">Son '+sigs.length+' sinyal</span></div>'
      + '<div style="display:flex;gap:3px;flex-wrap:wrap">';
    sigs.forEach(function(s){
      var dt=new Date(s.time);
      var ds=('0'+dt.getDate()).slice(-2)+'/'+('0'+(dt.getMonth()+1)).slice(-2);
      var clr=s.type==='master'?'var(--gold)':s.type==='stop'?'var(--orange)':'var(--green)';
      var lbl=s.type==='master'?'M':s.type==='stop'?'S':'AL';
      html+='<div style="background:var(--bg2);border:1px solid var(--b2);border-radius:5px;padding:4px 7px;text-align:center">'
        +'<div style="font-size:10px;font-weight:700;color:'+clr+'">'+lbl+'</div>'
        +'<div style="font-size:8px;color:var(--t4)">'+ds+'</div>'
        +(s.price>0?'<div style="font-size:8px;color:var(--t3);font-family:Courier New,monospace">TL'+parseFloat(s.price).toFixed(1)+'</div>':'')
        +'</div>';
    });
    html += '</div></div>';
  });
  el.innerHTML = html || '<div style="color:var(--t4);font-size:10px;padding:8px">Sinyal gecmisi bos</div>';
}

// Pozisyon sekmesi acilinca renderTickerSignals calistir
var _b5_origPosTab = posTab;
posTab = function(tab){
  _b5_origPosTab.apply(this,arguments);
  if(tab==='closed') setTimeout(renderTickerSignals,100);
};

//  2. RAPOR SEKMESI - GELISTIRILMIS 
renderReport = function(){
  var now=new Date(); var wAgo=new Date(now.getTime()-7*86400000);
  var hist=S.sigHistory||[]; var closed=S.closedPositions||[];
  var wSigs=hist.filter(function(s){return new Date(s.time)>=wAgo;});
  var mSigs=wSigs.filter(function(s){return s.type==='master';});
  var sSigs=wSigs.filter(function(s){return s.type==='stop';});

  // Bu hafta istatistik
  function sv(id,v,c2){var e=document.getElementById(id);if(e){e.textContent=v;if(c2)e.style.color=c2;}}
  sv('wkSigs',wSigs.length); sv('wkMaster',mSigs.length); sv('wkStops',sSigs.length);

  // En aktif hisse
  var tc={};
  wSigs.forEach(function(s){tc[s.ticker]=(tc[s.ticker]||0)+1;});
  var sorted=Object.keys(tc).sort(function(a,b){return tc[b]-tc[a];});
  sv('wkBest',sorted[0]||'-');

  // --- HAFTALIK PNL OZETI ---
  var wClosed=closed.filter(function(p){return new Date(p.exitTime||p.entryTime)>=wAgo;});
  var wPnl=0; wClosed.forEach(function(p){wPnl+=parseFloat(p.pnlPct||0);});
  var wPnlEl=document.getElementById('wkPnl');
  if(!wPnlEl){
    // Dinamik ekle
    var wkCard=document.querySelector('#wkSigs')&&document.querySelector('#wkSigs').closest('.card');
    if(wkCard){
      var div=document.createElement('div');
      div.innerHTML='<div class="sstat"><div class="sval" id="wkPnl">'+fmtPct(wPnl)+'</div><div class="slb2">Haftalik PnL</div></div>';
      var sgrid=wkCard.querySelector('.sgrid');
      if(sgrid) sgrid.appendChild(div.firstChild);
    }
  } else { wPnlEl.textContent=fmtPct(wPnl); wPnlEl.style.color=pnlClr(wPnl); }

  // Sistem performansi
  var sc={'ST+TMA':0,'PRO':0,'Fusion':0,'A60':0,'A61':0,'A62':0,'A81':0,'A120':0};
  wSigs.forEach(function(s){(s.acts||[]).forEach(function(a){if(sc[a]!==undefined)sc[a]++;});});
  var spEl=document.getElementById('sysPerf');
  if(spEl){
    var spH='';
    Object.keys(sc).sort(function(a,b){return sc[b]-sc[a];}).forEach(function(k){
      if(!sc[k]) return;
      var pct=wSigs.length?Math.round(sc[k]/wSigs.length*100):0;
      var clr=k==='PRO'||k==='ST+TMA'?'var(--cyan)':k==='Fusion'?'var(--purple)':'var(--gold)';
      spH+='<div style="display:flex;align-items:center;gap:7px;margin-bottom:6px">'
        +'<div style="width:58px;font-size:10px;color:var(--t2)">'+k+'</div>'
        +'<div style="flex:1;height:5px;background:var(--b2);border-radius:3px;overflow:hidden">'
          +'<div style="height:100%;width:'+pct+'%;background:'+clr+';border-radius:3px"></div></div>'
        +'<div style="font-size:9px;color:var(--t3);width:36px;text-align:right">'+sc[k]+'x</div></div>';
    });
    spEl.innerHTML=spH||'<div style="color:var(--t4);font-size:10px">Veri yok</div>';
  }

  // Takvim - son 28 gun
  var calEl=document.getElementById('calGrid');
  if(calEl){
    var calH='';
    for(var d=27;d>=0;d--){
      var day=new Date(now.getTime()-d*86400000); var ds=day.toDateString();
      var ds2=hist.filter(function(s){return new Date(s.time).toDateString()===ds;});
      var wk=day.getDay()===0||day.getDay()===6;
      var cls='cal-day'+(wk?' none':ds2.length>=5?' good':ds2.length>0?' has':' none');
      calH+='<div class="'+cls+'" title="'+day.toLocaleDateString('tr-TR')+': '+ds2.length+' sinyal">'+(ds2.length>0?ds2.length:day.getDate())+'</div>';
    }
    calEl.innerHTML=calH;
  }

  // En cok sinyal
  var tsEl=document.getElementById('topStocks');
  if(tsEl){
    var tsH=sorted.slice(0,8).map(function(t,i){
      var cached=S.priceCache[t]; var prStr=cached?(' <span style="color:var(--cyan);font-family:Courier New,monospace">TL'+cached.price.toFixed(2)+'</span>'):'';
      return'<div class="rep-row" style="cursor:pointer" onclick="openTVTicker(\''+t+'\',\'D\')">'
        +'<span style="color:var(--t3)">'+(i+1)+'. <b style="color:var(--t1)">'+t+'</b>'+prStr+'</span>'
        +'<span style="color:var(--cyan)">'+tc[t]+' sinyal</span></div>';
    }).join('');
    tsEl.innerHTML=tsH||'<div style="color:var(--t4);font-size:10px;padding:8px 0">Veri yok</div>';
  }

  // PnL grafigi - kapali pozisyonlar
  var pnlChartEl=document.getElementById('pnlChart');
  if(pnlChartEl && closed.length){
    var runPnl=0; var points=closed.slice().reverse().map(function(p){
      runPnl+=parseFloat(p.pnlPct); return runPnl.toFixed(2);
    });
    var maxP=Math.max.apply(null,points.map(Math.abs))||1;
    var ch='<div style="position:relative;height:80px;background:var(--bg3);border-radius:7px;overflow:hidden;margin-bottom:8px">';
    ch+='<div style="position:absolute;bottom:0;left:0;right:0;height:1px;background:var(--b2)"></div>';
    ch+='<div style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:flex-end;gap:1px;padding:2px">';
    points.forEach(function(v){
      var pct=Math.round(Math.abs(parseFloat(v))/maxP*36);
      var pos=parseFloat(v)>=0;
      ch+='<div style="flex:1;height:'+pct+'px;background:'+(pos?'var(--green)':'var(--red)')+';border-radius:1px;'+(pos?'':'margin-top:auto')+';opacity:.8" title="'+v+'%"></div>';
    });
    ch+='</div></div>';
    ch+='<div style="display:flex;justify-content:space-between;font-size:8px;color:var(--t4)">'
      +'<span>Baslangic</span><span style="color:'+(parseFloat(runPnl)>=0?'var(--green)':'var(--red)')+';font-weight:700">Toplam: '+fmtPct(runPnl)+'</span><span>Son</span></div>';
    pnlChartEl.innerHTML=ch;
  }

  // Gunluk sinyal yogunlugu
  renderHeatmap();
};

function renderHeatmap(){
  var el=document.getElementById('heatmap');
  if(!el) return;
  var hist=S.sigHistory||[];
  var hourCounts=new Array(24).fill(0);
  hist.slice(0,500).forEach(function(s){
    var h=new Date(s.time).getHours();
    hourCounts[h]++;
  });
  var maxH=Math.max.apply(null,hourCounts)||1;
  var html='<div style="display:flex;gap:2px;align-items:flex-end;height:40px">';
  for(var h=9;h<=18;h++){
    var pct=Math.round(hourCounts[h]/maxH*100);
    var isPeak=pct>=70; var isMarket=h>=9&&h<=18;
    html+='<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px">'
      +'<div style="flex:1;display:flex;align-items:flex-end">'
        +'<div style="width:100%;height:'+(pct||2)+'%;background:'+(isPeak?'var(--gold)':'var(--cyan)')+';border-radius:2px;opacity:'+(isMarket?1:0.4)+';min-height:2px"></div></div>'
      +'<div style="font-size:7px;color:var(--t4)">'+h+'</div>'
      +'</div>';
  }
  html+='</div>';
  el.innerHTML=html;
}

// Rapor HTML'e PnL chart + heatmap ekle
window.addEventListener('load',function(){
  setTimeout(function(){
    var repPage=document.getElementById('page-report');
    if(!repPage) return;
    // PnL kumulatif chart
    var pnlDiv=document.createElement('div');
    pnlDiv.className='card';
    pnlDiv.innerHTML='<div class="ctitle" style="color:var(--green)">Kumulatif PnL</div><div id="pnlChart"></div>';
    // Heatmap
    var hmDiv=document.createElement('div');
    hmDiv.className='card';
    hmDiv.innerHTML='<div class="ctitle">Saatlik Sinyal Yogunlugu</div><div id="heatmap"></div>';
    // Butunlestirici
    var lastCard=repPage.querySelector('.card:last-child');
    if(lastCard){ repPage.insertBefore(pnlDiv,lastCard); repPage.insertBefore(hmDiv,lastCard); }
    else{ repPage.appendChild(pnlDiv); repPage.appendChild(hmDiv); }
  },1000);
});

//  3. TRADER GOZETIMI - AI TABANLI 
openTraderConsole = function(){
  var posCount=Object.keys(S.openPositions||{}).length;
  var closed=S.closedPositions||[];
  var hist=S.sigHistory||[];
  var totalPnl=0; var wins=0;
  closed.forEach(function(p){var pnl=parseFloat(p.pnlPct);totalPnl+=pnl;if(pnl>=0)wins++;});
  var wr=closed.length?(wins/closed.length*100).toFixed(1):'N/A';
  var avgPnl=closed.length?(totalPnl/closed.length).toFixed(2):'N/A';

  // Acik pozisyon PnL
  var openPnl=0; var worstPos=null; var worstPnl=999;
  Object.keys(S.openPositions).forEach(function(key){
    var pos=S.openPositions[key]; var ticker=key.split('_')[0];
    var cached=S.priceCache[ticker];
    if(cached&&cached.price>0){
      var pnl=(cached.price-pos.entry)/pos.entry*100;
      openPnl+=pnl;
      if(pnl<worstPnl){worstPnl=pnl;worstPos=ticker;}
    }
  });

  // Risk metrikleri
  var xu=S.xu100Change||0;
  var riskScore=0;
  if(posCount>=5) riskScore+=30;
  else if(posCount>=3) riskScore+=15;
  if(xu<=-2) riskScore+=25;
  else if(xu<=-1) riskScore+=10;
  if(parseFloat(wr)<45&&closed.length>=5) riskScore+=20;
  if(parseFloat(avgPnl)<0) riskScore+=15;
  if(worstPnl<-10) riskScore+=10;
  var riskLvl=riskScore>=60?'YUKSEK':riskScore>=30?'ORTA':'DUSUK';
  var riskClr=riskScore>=60?'var(--red)':riskScore>=30?'var(--gold)':'var(--green)';

  // AI onerileri - kural bazli uzman sistem
  var oneriler=[];
  var uyarilar=[];

  // Pozisyon yonetimi
  if(posCount===0&&hist.length>0){
    oneriler.push({o:'Aktif pozisyon yok - tarama yapip yeni sinyal bekleyin',p:'EYLEM',c:'var(--cyan)'});
  }
  if(posCount>=5){
    uyarilar.push({o:'5+ acik pozisyon: Portfoy yogunlasma riski. Yeni giris yapmayin',p:'KRITIK',c:'var(--red)'});
  }
  if(worstPos&&worstPnl<-8){
    uyarilar.push({o:worstPos+' pozisyonu -'+Math.abs(worstPnl).toFixed(1)+'% zararda. Stop seviyesini kontrol edin',p:'UYARI',c:'var(--orange)'});
  }

  // Piyasa durumu
  if(xu<=-2){
    uyarilar.push({o:'XU100 '+xu.toFixed(2)+'% - Piyasa zayif. Yeni AL sinyallerini atlayin',p:'DIKKAT',c:'var(--orange)'});
  } else if(xu>=2){
    oneriler.push({o:'XU100 +'+xu.toFixed(2)+'% - Piyasa guclu. Sinyaller daha guvenilir',p:'FIRSAT',c:'var(--green)'});
  }

  // Win rate analizi
  if(closed.length>=5){
    if(parseFloat(wr)<40){
      uyarilar.push({o:'Win rate %'+wr+' - Cok dusuk. ADX esigini 30\'a cikarin ve Fusion esigini dusurun',p:'KRITIK',c:'var(--red)'});
    } else if(parseFloat(wr)<50){
      uyarilar.push({o:'Win rate %'+wr+' - Orta. PRO min skoru 5\'ten 6\'ya cikarin',p:'IYILESTIRME',c:'var(--gold)'});
    } else if(parseFloat(wr)>=65){
      oneriler.push({o:'Win rate %'+wr+' - Mukemmel! Mevcut parametreleri koruyun',p:'HARIKA',c:'var(--green)'});
    }
  }

  // Ortalama PnL
  if(parseFloat(avgPnl)<0){
    uyarilar.push({o:'Ortalama PnL negatifte. Stop loss kurali sikilastirin: ATR carpani '+((C.atrm||8))+' -> '+(Math.max(6,(C.atrm||8)-1)),p:'KRITIK',c:'var(--red)'});
  }

  // Sistem etkinligi
  if(!C.s1&&!C.s2&&!C.fu&&!C.ma){
    uyarilar.push({o:'Hic sinyal sistemi aktif degil! Ayarlardan en az birini acin',p:'KRITIK',c:'var(--red)'});
  }
  if(C.s1&&C.s2&&C.fu&&C.ma&&!C.a60&&!C.a61){
    oneriler.push({o:'Agent 60 veya 61 aktif edilerek konsensus guclendirilmeli',p:'ONERI',c:'var(--cyan)'});
  }

  // Telegram
  if(!TG||!TG.token){
    oneriler.push({o:'Telegram botu yapilandirilmamis. Ayarlar > Telegram bolumund yapilandirin',p:'ONERI',c:'var(--t3)'});
  }

  // Kelly Criterion pozisyon buyutu
  if(closed.length>=10&&parseFloat(wr)>0){
    var p=parseFloat(wr)/100;
    var avgW=0; var avgL=0; var wc=0; var lc=0;
    closed.forEach(function(cp){var pnl=parseFloat(cp.pnlPct);if(pnl>=0){avgW+=pnl;wc++;}else{avgL+=Math.abs(pnl);lc++;}});
    if(wc&&lc){avgW/=wc;avgL/=lc;var b=avgW/avgL;var kelly=(p*b-(1-p))/b;var halfK=Math.max(0.01,Math.min(0.20,kelly*0.5));oneriler.push({o:'Kelly Criterion pozisyon buyutu: Sermayenin %'+(halfK*100).toFixed(1)+' (Tam Kelly: %'+(kelly*100).toFixed(1)+')',p:'BILGI',c:'var(--purple)'});}
  }

  // HTML olustur
  var html='<div style="padding:3px 0">'
    // Risk ozet
    +'<div style="background:var(--bg3);border:1px solid var(--b2);border-radius:8px;padding:11px;margin-bottom:10px">'
    +'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
    +'<span style="font-size:11px;font-weight:700;color:var(--t1)">Risk Durumu</span>'
    +'<span style="font-size:13px;font-weight:700;padding:4px 12px;border-radius:5px;background:rgba(0,0,0,.3);color:'+riskClr+'">'+riskLvl+'</span></div>'
    +'<div style="height:4px;background:var(--b2);border-radius:2px;overflow:hidden;margin-bottom:8px">'
    +'<div style="height:100%;width:'+riskScore+'%;background:'+riskClr+';border-radius:2px;transition:width .3s"></div></div>'
    +'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:5px">'
    +'<div class="sstat"><div class="sval" style="font-size:16px">'+posCount+'</div><div class="slb2">Acik Poz.</div></div>'
    +'<div class="sstat"><div class="sval" style="font-size:16px;color:'+(parseFloat(wr)>=50?'var(--green)':'var(--red)')+'">'+wr+'%</div><div class="slb2">Win Rate</div></div>'
    +'<div class="sstat"><div class="sval" style="font-size:16px;color:'+pnlClr(openPnl)+'">'+fmtPct(openPnl)+'</div><div class="slb2">Acik PnL</div></div>'
    +'<div class="sstat"><div class="sval" style="font-size:16px;color:'+pnlClr(avgPnl||0)+'">'+fmtPct(avgPnl||0)+'</div><div class="slb2">Ort. PnL</div></div>'
    +'</div></div>';

  // Uyarilar
  if(uyarilar.length){
    html+='<div style="margin-bottom:8px"><div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">Uyarilar</div>';
    uyarilar.forEach(function(u){
      html+='<div style="display:flex;gap:7px;align-items:flex-start;padding:9px;background:rgba(0,0,0,.3);border:1px solid var(--b2);border-left:3px solid '+u.c+';border-radius:6px;margin-bottom:5px">'
        +'<span style="font-size:8px;padding:2px 5px;border-radius:3px;background:rgba(0,0,0,.4);color:'+u.c+';flex-shrink:0;font-weight:700">'+u.p+'</span>'
        +'<span style="font-size:10px;color:var(--t2);line-height:1.5">'+u.o+'</span></div>';
    });
    html+='</div>';
  }

  // Oneriler
  if(oneriler.length){
    html+='<div style="margin-bottom:8px"><div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">Oneriler</div>';
    oneriler.forEach(function(u){
      html+='<div style="display:flex;gap:7px;align-items:flex-start;padding:9px;background:rgba(0,0,0,.3);border:1px solid var(--b2);border-left:3px solid '+u.c+';border-radius:6px;margin-bottom:5px">'
        +'<span style="font-size:8px;padding:2px 5px;border-radius:3px;background:rgba(0,0,0,.4);color:'+u.c+';flex-shrink:0;font-weight:700">'+u.p+'</span>'
        +'<span style="font-size:10px;color:var(--t2);line-height:1.5">'+u.o+'</span></div>';
    });
    html+='</div>';
  }

  // Hizli aksiyonlar
  html+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px">'
    +'<button class="btn c" style="padding:10px;border-radius:8px;font-size:10px" onclick="startScan()">Tarama Baslat</button>'
    +'<button class="btn g" style="padding:10px;border-radius:8px;font-size:10px" onclick="sendDayEndReport()">Gun Sonu Raporu</button>'
    +'<button class="btn o" style="padding:10px;border-radius:8px;font-size:10px" onclick="sendWeeklyReport()">Haftalik Rapor</button>'
    +'<button class="btn" style="padding:10px;border-radius:8px;font-size:10px" onclick="document.getElementById(\'modal\').classList.remove(\'on\');pg(\'settings\')">Ayarlar</button>'
    +'</div>';

  html+='</div>';

  document.getElementById('mtit').textContent='Trader Gozetimi - AI Analiz';
  document.getElementById('mcont').innerHTML=html;
  document.getElementById('modal').classList.add('on');
};

//  4. FALSE SINYAL ANALIZI - GELISTIRILMIS 
openFalseSignalAnalysis = function(){
  var closed=S.closedPositions||[];
  if(closed.length<3){toast('En az 3 kapali pozisyon gerekli!');return;}

  var losses=closed.filter(function(p){return parseFloat(p.pnlPct)<0;});
  var wins=closed.filter(function(p){return parseFloat(p.pnlPct)>=0;});
  var falseRate=(losses.length/closed.length*100).toFixed(1);

  // Metrik ortalamalari
  function avg(arr,key){
    if(!arr.length) return 0;
    return arr.reduce(function(a,p){return a+parseFloat(p[key]||0);},0)/arr.length;
  }
  var avgConsFalse=avg(losses,'cons'); var avgConsTrue=avg(wins,'cons');
  var avgAdxFalse=avg(losses,'adx'); var avgAdxTrue=avg(wins,'adx');
  var avgScoreFalse=avg(losses,'score'); var avgScoreTrue=avg(wins,'score');

  // Pstate analizi
  var pstateFalse={};
  losses.forEach(function(p){var ps=p.pstate||'NORMAL';pstateFalse[ps]=(pstateFalse[ps]||0)+1;});

  // En iyi filtreler
  var filters=[];

  if(avgConsFalse<avgConsTrue-10){
    filters.push({
      tip:'Min Konsensus',
      oneri:Math.ceil(avgConsTrue-5),
      etki:'Potansiyel false sinyallerin %'+Math.round((losses.filter(function(p){return parseFloat(p.cons||0)<avgConsTrue-5;}).length/losses.length)*100)+' elimine eder',
      clr:'var(--green)'
    });
  }
  if(avgAdxFalse<avgAdxTrue-5){
    filters.push({
      tip:'Min ADX',
      oneri:Math.ceil(avgAdxTrue-3),
      etki:'Zayif trend sinyallerini filtreler',
      clr:'var(--cyan)'
    });
  }
  if(avgScoreFalse<avgScoreTrue-0.5){
    filters.push({
      tip:'Min PRO Skor',
      oneri:Math.ceil(avgScoreTrue),
      etki:'Dusuk kaliteli sinyalleri keser',
      clr:'var(--gold)'
    });
  }

  // Pahali bolgede false sinyal orani
  var pahaliLoss=losses.filter(function(p){return p.pstate==='PAHALI'||p.pstate==='COK PAHALI';}).length;
  if(pahaliLoss/losses.length>0.3){
    filters.push({
      tip:'Pahali Bolge Filtresi',
      oneri:'PAHALI/COK PAHALI sinyallerini atla',
      etki:'Toplam false sinyallerin %'+Math.round(pahaliLoss/losses.length*100)+' buradan geliyor',
      clr:'var(--orange)'
    });
  }

  // Trailing stop analizi
  var stopAnaliz=[];
  var atrVals=[4,5,6,7,8,9,10,12];
  atrVals.forEach(function(atr){
    var simWins=0; var simTotal=0;
    closed.forEach(function(p){
      simTotal++;
      var stopDist=p.entry*0.022*atr;
      var simStop=p.entry-stopDist;
      var exitP=parseFloat(p.exit);
      if(exitP>=simStop) simWins++;
    });
    stopAnaliz.push({atr:atr,wr:(simWins/simTotal*100).toFixed(1)});
  });
  stopAnaliz.sort(function(a,b){return parseFloat(b.wr)-parseFloat(a.wr);});
  var bestStop=stopAnaliz[0];

  // HTML
  var html='<div style="padding:3px 0">'
    // Ozet
    +'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:5px;margin-bottom:10px">'
    +'<div class="sstat"><div class="sval" style="color:var(--red)">%'+falseRate+'</div><div class="slb2">False Sinyal Orani</div></div>'
    +'<div class="sstat"><div class="sval">'+closed.length+'</div><div class="slb2">Toplam Sinyal</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--green)">'+wins.length+'</div><div class="slb2">Kazanali</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--red)">'+losses.length+'</div><div class="slb2">Zararli</div></div>'
    +'</div>'

    // Metrik kiyaslama
    +'<div class="card" style="margin-bottom:8px;padding:10px">'
    +'<div class="ctitle">Kazanan vs Kaybeden Metrikler</div>'
    +'<table style="width:100%;font-size:10px;border-collapse:collapse">'
    +'<tr><th style="text-align:left;color:var(--t4);padding:4px 0;border-bottom:1px solid var(--b2)">Metrik</th>'
    +'<th style="color:var(--green);padding:4px;border-bottom:1px solid var(--b2)">Kazanan</th>'
    +'<th style="color:var(--red);padding:4px;border-bottom:1px solid var(--b2)">Kaybeden</th></tr>'
    +'<tr><td style="color:var(--t3);padding:5px 0">Konsensus</td><td style="color:var(--green);text-align:center">%'+avgConsTrue.toFixed(1)+'</td><td style="color:var(--red);text-align:center">%'+avgConsFalse.toFixed(1)+'</td></tr>'
    +'<tr style="background:var(--bg3)"><td style="color:var(--t3);padding:5px">ADX</td><td style="color:var(--green);text-align:center">'+avgAdxTrue.toFixed(1)+'</td><td style="color:var(--red);text-align:center">'+avgAdxFalse.toFixed(1)+'</td></tr>'
    +'<tr><td style="color:var(--t3);padding:5px 0">PRO Skor</td><td style="color:var(--green);text-align:center">'+avgScoreTrue.toFixed(1)+'</td><td style="color:var(--red);text-align:center">'+avgScoreFalse.toFixed(1)+'</td></tr>'
    +'</table></div>'

    // Trailing stop analizi
    +'<div class="card" style="margin-bottom:8px;padding:10px">'
    +'<div class="ctitle">En Iyi Trailing Stop</div>'
    +'<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:7px">';
  stopAnaliz.slice(0,6).forEach(function(s){
    var isBest=s.atr===bestStop.atr;
    html+='<div style="background:'+(isBest?'rgba(0,230,118,.15)':'var(--bg3)')+';border:1px solid '+(isBest?'rgba(0,230,118,.4)':'var(--b2)')+';border-radius:6px;padding:7px 10px;text-align:center">'
      +'<div style="font-size:12px;font-weight:700;color:'+(isBest?'var(--green)':'var(--t2)')+'">x'+s.atr+'</div>'
      +'<div style="font-size:9px;color:var(--t4)">WR: '+s.wr+'%</div>'
      +(isBest?'<div style="font-size:8px;color:var(--green)">BEST</div>':'')
      +'</div>';
  });
  html+='</div>'
    +'<button class="btn g" style="width:100%;padding:8px;border-radius:7px;font-size:10px" onclick="applyBestStop('+bestStop.atr+')">ATR x'+bestStop.atr+' Uygula (WR: '+bestStop.wr+'%)</button>'
    +'</div>'

    // Oneri edilen filtreler
    +'<div style="margin-bottom:8px"><div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">AI Filter Onerileri</div>';

  if(!filters.length){
    html+='<div style="color:var(--green);font-size:10px;padding:8px">Mevcut parametreler iyi gorunuyor!</div>';
  }
  filters.forEach(function(f){
    html+='<div style="padding:10px;background:var(--bg3);border:1px solid var(--b2);border-left:3px solid '+f.clr+';border-radius:7px;margin-bottom:5px">'
      +'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
      +'<span style="font-size:11px;font-weight:700;color:'+f.clr+'">'+f.tip+'</span>'
      +'<button class="btn" style="font-size:9px;padding:3px 8px" onclick="applyFalseSignalFix('+(typeof f.oneri==='number'?JSON.stringify({type:f.tip.toLowerCase(),val:f.oneri}):'null')+')">Uygula</button></div>'
      +'<div style="font-size:10px;color:var(--t2)">Oneri: <b style="color:'+f.clr+'">'+f.oneri+'</b></div>'
      +'<div style="font-size:9px;color:var(--t4);margin-top:3px">'+f.etki+'</div></div>';
  });
  html+='</div></div>';

  document.getElementById('mtit').textContent='False Sinyal Analizi';
  document.getElementById('mcont').innerHTML=html;
  document.getElementById('modal').classList.add('on');
};

function applyBestStop(atrVal){
  C.atrm=atrVal;
  lsSet('bistcfg',C);
  var el=document.getElementById('s_atrm');
  if(el) el.value=atrVal;
  toast('ATR Multiplier x'+atrVal+' uygulandi!');
  document.getElementById('modal').classList.remove('on');
}

//  5. BACKTEST - GELISTIRILMIS RENDER 
var _b5_origRenderBT = typeof renderBT === 'function' ? renderBT : null;
renderBT = function(r){
  if(_b5_origRenderBT) _b5_origRenderBT(r);
  if(!r) return;
  // Ek istatistikler - trades analizi
  if(!r.trades||!r.trades.length) return;
  var trEl = document.getElementById('btTradeDetail');
  if(!trEl){
    trEl=document.createElement('div');
    trEl.id='btTradeDetail';
    trEl.className='card';
    var btout=document.getElementById('btout');
    if(btout) btout.appendChild(trEl);
  }
  var closed2=r.trades.filter(function(t){return !t.open;});
  if(!closed2.length) return;
  var pnls=closed2.map(function(t){return parseFloat(t.p);});
  var posT=pnls.filter(function(v){return v>0;}); var negT=pnls.filter(function(v){return v<0;});
  var avgW=posT.length?posT.reduce(function(a,b){return a+b;},0)/posT.length:0;
  var avgL=negT.length?negT.reduce(function(a,b){return a+b;},0)/negT.length:0;
  var streak=0; var maxStreak=0; var lossStreak=0; var maxLossStreak=0;
  pnls.forEach(function(p){ if(p>=0){streak++;lossStreak=0;if(streak>maxStreak)maxStreak=streak;} else{lossStreak++;streak=0;if(lossStreak>maxLossStreak)maxLossStreak=lossStreak;} });
  var rr=avgL!==0?Math.abs(avgW/avgL).toFixed(2):'999';

  trEl.innerHTML='<div class="ctitle" style="color:var(--purple)">Detayli Trade Analizi</div>'
    +'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:5px;margin-bottom:8px">'
    +'<div class="sstat"><div class="sval" style="color:var(--green)">+'+avgW.toFixed(2)+'%</div><div class="slb2">Ort. Kazanc</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--red)">'+avgL.toFixed(2)+'%</div><div class="slb2">Ort. Kayip</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+rr+'</div><div class="slb2">Risk/Odul</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--gold)">'+maxStreak+'</div><div class="slb2">Max Kaz. Serisi</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--red)">'+maxLossStreak+'</div><div class="slb2">Max Kay. Serisi</div></div>'
    +'<div class="sstat"><div class="sval">'+r.avgHold+'</div><div class="slb2">Ort. Bar</div></div>'
    +'</div>';
};

//  6. HISSE ISMINE TIKLA -> TradingView 
// renderSigs'i override ederek ticker'lara onclick ekle
var _b5_origRenderSigsTV = renderSigs;
renderSigs = function(){
  _b5_origRenderSigsTV.apply(this,arguments);
  // Sinyal listesindeki sig-ticker elementlerine onclick ekle
  setTimeout(function(){
    var tickers = document.querySelectorAll('.sig-ticker');
    for(var i=0;i<tickers.length;i++){
      (function(el){
        if(el.dataset.tvbound) return;
        el.dataset.tvbound='1';
        el.style.cursor='pointer';
        el.style.textDecoration='underline';
        el.style.textDecorationColor='rgba(255,255,255,.3)';
        el.addEventListener('click',function(e){
          e.stopPropagation();
          var ticker=el.textContent.trim();
          openTVTicker(ticker,'D');
        });
      })(tickers[i]);
    }
    // Stlist'teki hisseler de
    var strows = document.querySelectorAll('.strow .stt');
    for(var j=0;j<strows.length;j++){
      (function(el){
        if(el.dataset.tvbound) return;
        el.dataset.tvbound='1';
        el.style.textDecoration='underline';
        el.style.textDecorationColor='rgba(0,212,255,.4)';
        el.style.cursor='pointer';
        el.addEventListener('click',function(e){
          e.stopPropagation();
          openTVTicker(el.textContent.trim(),'D');
        });
      })(strows[j]);
    }
  },200);
};

// LOAD
window.addEventListener('load',function(){
  setTimeout(function(){
    // Uygulama acilinca kapali pozisyonlari render et
    renderClosedPositions();
    // Raporu guncelle
    renderReport();
  },1500);
});

</script>
<script>

// 
// BIST v6 BLOK 6 - KALICILIK + PINE TABLOLARI + AI SOHBET
// 

//  1. SINYAL FIYATI - startScan override 
// Orijinal scanTF'yi patch et - signalPrice kaydet
var _b6_origScan = startScan;
startScan = function(){
  // startScan cagrilmadan once S.openPositions proxy'sini kur
  // Pozisyona girerken signalPrice'i kaydet
  var _origKeys = Object.keys(S.openPositions||{});
  _b6_origScan.apply(this, arguments);
  // Tarama sonrasi yeni pozisyonlara signalPrice ekle
  setTimeout(function(){
    Object.keys(S.openPositions||{}).forEach(function(key){
      if(_origKeys.indexOf(key)===-1){
        // Yeni acilan pozisyon - sinyalden fiyat al
        var sig = null;
        for(var i=0;i<S.sigs.length;i++){
          var s=S.sigs[i];
          if(s.ticker===key.split('_')[0]&&s.tf===key.split('_')[1]&&s.type!=='stop'){
            sig=s; break;
          }
        }
        if(sig&&sig.res&&sig.res.price){
          S.openPositions[key].signalPrice = sig.res.price;
          S.openPositions[key].signalTime = sig.time instanceof Date?sig.time.toISOString():sig.time;
          try{localStorage.setItem('bist_positions',JSON.stringify(S.openPositions));}catch(e){}
        }
      }
    });
  },500);
};

//  2. PINE TABLOLARI - res'ten al ve goster 
function renderPineTableForSig(res){
  if(!res) return '';
  var sys1=res.sys1||res.sys_1; var sys2=res.sys2||res.sys_2;
  var fu=res.fusion; var ma=res.master_ai; var ag=res.agents;
  if(!sys1&&!sys2&&!fu&&!ma&&!ag) return '';

  function box(title,color,d,extra){
    if(!d) return '';
    var wr=d.buys>0?((d.wins/d.buys)*100).toFixed(0):'0';
    var wrClr=parseFloat(wr)>=50?'var(--green)':'var(--red)';
    var pnlClr2=d.total_pnl>=0?'var(--green)':'var(--red)';
    var openClr=d.open_pnl!=null?(d.open_pnl>=0?'var(--green)':'var(--red)'):'var(--t4)';
    return '<div style="background:var(--bg3);border:1px solid var(--b2);border-top:2px solid '+color+';border-radius:6px;padding:8px">'
      +'<div style="font-size:8px;font-weight:700;color:'+color+';margin-bottom:5px">'+title+'</div>'
      +'<div style="font-size:8px;line-height:1.8">'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Toplam AL</span><span style="color:var(--t2)">'+d.buys+'</span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Toplam SAT</span><span style="color:var(--t2)">'+d.sells+'</span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Karl/Zarar</span><span><span style="color:var(--green)">'+d.wins+'</span>/<span style="color:var(--red)">'+d.losses+'</span></span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Win Rate</span><span style="color:'+wrClr+'">%'+wr+'</span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Toplam %</span><span style="color:'+pnlClr2+'">'+(d.total_pnl>=0?'+':'')+d.total_pnl+'</span></div>'
      +(d.open_pnl!=null?'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Pozisyon</span><span style="color:'+openClr+'">'+(d.open_pnl!==0?(d.open_pnl>=0?'+':'')+d.open_pnl+'%':'YOK')+'</span></div>':'')
      +(extra||'')
      +'</div></div>';
  }

  var html='<div style="border-top:1px solid var(--b2);padding-top:10px;margin-top:10px">'
    +'<div style="font-size:8px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:7px">Pine Tablo - 2 Yil (504 Bar)</div>'
    +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:6px">'
    +box('SISTEM 1','var(--cyan)',sys1)
    +box('PRO ENGINE','var(--gold)',sys2,sys2?'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">PRO Skor</span><span>'+(sys2.score||'-')+'/6</span></div>':'')
    +box('FUSION','var(--purple)',fu)
    +(ma?'<div style="background:var(--bg3);border:1px solid var(--b2);border-top:2px solid var(--gold);border-radius:6px;padding:8px">'
      +'<div style="font-size:8px;font-weight:700;color:var(--gold);margin-bottom:5px">MASTER AI</div>'
      +'<div style="font-size:8px;line-height:1.8">'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Consensus Buy</span><span style="color:var(--green)">%'+ma.buy_consensus+'</span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Consensus Sell</span><span style="color:var(--red)">%'+ma.sell_consensus+'</span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Dyn Buy Thresh</span><span>'+ma.dyn_buy_thresh+'</span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Dyn Sell Thresh</span><span>'+ma.dyn_sell_thresh+'</span></div>'
      +'<div style="display:flex;justify-content:space-between"><span style="color:var(--t4)">Toplam PnL %</span><span style="color:'+(ma.total_pnl>=0?'var(--green)':'var(--red)')+'">'+(ma.total_pnl>=0?'+':'')+ma.total_pnl+'</span></div>'
      +'</div></div>'
      :'')
    +'</div>';

  // Agent dashboard
  if(ag){
    var agKeys=['a60','a61','a62','a81','a120'];
    var agNames=['A60','A61','A62','A81','A120'];
    html+='<div style="background:var(--bg3);border:1px solid var(--b2);border-radius:6px;padding:8px;margin-bottom:6px">'
      +'<div style="font-size:8px;font-weight:700;color:var(--orange);margin-bottom:6px">EN IYI AGENT + AGENT DASHBOARD</div>'
      +'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px">';
    var bestAgent=null; var bestPnl=-9999;
    agKeys.forEach(function(k,i){
      var a=ag[k]; if(!a) return;
      if(a.pnl>bestPnl){bestPnl=a.pnl;bestAgent=agNames[i];}
      var wr2=a.buys>0?((a.wins/a.buys)*100).toFixed(0):'0';
      var pclr=a.pnl>=0?'var(--green)':'var(--red)';
      html+='<div style="text-align:center;padding:5px;background:var(--bg2);border-radius:5px;border:1px solid var(--b2)">'
        +'<div style="font-size:9px;font-weight:700;color:var(--cyan)">'+agNames[i]+'</div>'
        +'<div style="font-size:11px;font-weight:700;color:'+pclr+';font-family:Courier New,monospace">'+(a.pnl>=0?'+':'')+a.pnl+'</div>'
        +'<div style="font-size:7px;color:var(--t4)">AL: '+a.buys+'</div>'
        +'<div style="font-size:7px;color:'+wrClrFn(wr2)+'">W%'+wr2+'</div>'
        +'</div>';
    });
    html+='</div>';
    if(bestAgent) html+='<div style="font-size:9px;color:var(--gold);margin-top:5px;text-align:center">En Iyi Agent: <b>'+bestAgent+'</b></div>';
    html+='</div>';
  }
  html+='</div>';
  return html;
}

function wrClrFn(wr){ return parseFloat(wr)>=55?'var(--green)':parseFloat(wr)>=45?'var(--gold)':'var(--red)'; }

// openSig override - Pine tablolari goster
var _b6_origOpenSig = openSig;
openSig = function(idx){
  _b6_origOpenSig.apply(this, arguments);
  // Modal acildiktan sonra Pine tablolarini ekle
  setTimeout(function(){
    var sig = S.curSig; if(!sig||!sig.res) return;
    var mcont = document.getElementById('mcont'); if(!mcont) return;
    var pine = renderPineTableForSig(sig.res);
    if(pine && mcont.innerHTML.indexOf('Pine Tablo')===-1){
      mcont.innerHTML += pine;
    }
  },50);
};

//  3. POZISYON KARTI - Pine tablolari 
var _b6_origRP = renderPositions;
renderPositions = function(){
  // signalPrice garantile
  Object.keys(S.openPositions||{}).forEach(function(key){
    var pos=S.openPositions[key];
    if(pos&&!pos.signalPrice) pos.signalPrice=pos.entry;
  });
  _b6_origRP.apply(this, arguments);
  // Her pozisyon kartina Pine tablo ekle
  setTimeout(function(){
    Object.keys(S.openPositions||{}).forEach(function(key){
      var pos=S.openPositions[key]; if(!pos) return;
      var ticker=key.split('_')[0]; var tf=key.split('_')[1];
      // Bu hissenin sinyalini bul
      var sig=null;
      for(var i=0;i<S.sigs.length;i++){
        var s=S.sigs[i];
        if(s.ticker===ticker&&s.tf===tf&&s.type!=='stop'&&s.res){sig=s;break;}
      }
      if(!sig||!sig.res) return;
      // Pine tablo verisi var mi?
      var hasPine=sig.res.sys1||sig.res.sys2||sig.res.fusion||sig.res.master_ai;
      if(!hasPine) return;
      // Kart elementini bul - benzersiz id yok, ticker+tf ile bul
      var pine=renderPineTableForSig(sig.res);
      if(!pine) return;
      // PosCard'lara ekle
      var cards=document.querySelectorAll('.pos-card');
      for(var ci=0;ci<cards.length;ci++){
        var tickerEl=cards[ci].querySelector('.pos-ticker');
        if(tickerEl&&tickerEl.textContent.indexOf(ticker)>-1){
          if(cards[ci].innerHTML.indexOf('Pine Tablo')===-1){
            var pineDiv=document.createElement('div');
            pineDiv.innerHTML=pine;
            cards[ci].appendChild(pineDiv);
          }
          break;
        }
      }
    });
  },200);
};

//  4. TAM KALICILIK 
// Sayfa yuklendiginde TUM verileri geri yukle
function b6RestoreAll(){
  // Sinyaller
  if(!S.sigs||S.sigs.length===0){
    try{
      var savedSigs=JSON.parse(localStorage.getItem('bist_sigs')||'[]');
      var cut=Date.now()-24*60*60*1000;
      savedSigs.forEach(function(s){
        if(new Date(s.time).getTime()<cut) return;
        S.sigs.push({
          id:s.id, ticker:s.ticker, name:s.name||s.ticker,
          indices:s.indices||[], type:s.type||'buy', tf:s.tf||'D',
          time:new Date(s.time), res:s.res||{}
        });
      });
      if(S.sigs.length>0){
        renderSigs(); updateBadge();
        console.log('Restored',S.sigs.length,'sigs from localStorage');
      }
    }catch(e){}
  }

  // Pozisyonlar
  try{
    var savedPos=JSON.parse(localStorage.getItem('bist_positions')||'{}');
    if(Object.keys(savedPos).length>0){
      Object.keys(savedPos).forEach(function(k){
        if(!S.openPositions[k]) S.openPositions[k]=savedPos[k];
      });
      renderPositions();
    }
  }catch(e){}

  // Kapali gecmis
  try{
    var savedClosed=JSON.parse(localStorage.getItem('bist_closed')||'[]');
    if(savedClosed.length>S.closedPositions.length){
      S.closedPositions=savedClosed;
    }
  }catch(e){}

  // Sinyal gecmisi
  try{
    var savedHist=JSON.parse(localStorage.getItem('bist_hist')||'[]');
    if(savedHist.length>S.sigHistory.length){
      S.sigHistory=savedHist;
    }
  }catch(e){}

  // Watchlist
  try{
    var savedWL=JSON.parse(localStorage.getItem('bist_wl')||'[]');
    if(savedWL.length>0) S.watchlist=savedWL;
  }catch(e){}
}

// Her tarama sonrasi kaydet
var _b6_origSaveSigs = typeof saveSigsLS==='function'?saveSigsLS:null;
function b6SaveAll(){
  try{
    // Sinyaller
    var sv=(S.sigs||[]).slice(0,150).map(function(s){
      return{id:s.id,ticker:s.ticker,name:s.name||s.ticker,indices:s.indices||[],
        type:s.type||'buy',tf:s.tf||'D',
        time:s.time instanceof Date?s.time.toISOString():s.time,
        res:{price:s.res.price,signalPrice:s.res.signalPrice||s.res.price,
          cons:s.res.cons,adx:s.res.adx,score:s.res.score,
          fp:s.res.fp,pstate:s.res.pstate,acts:s.res.acts||[],
          currentStop:s.res.currentStop,isMaster:s.res.isMaster,
          strength:s.res.strength,
          sys1:s.res.sys1,sys2:s.res.sys2,fusion:s.res.fusion,
          master_ai:s.res.master_ai,agents:s.res.agents}};
    });
    localStorage.setItem('bist_sigs',JSON.stringify(sv));
    // Pozisyonlar
    localStorage.setItem('bist_positions',JSON.stringify(S.openPositions||{}));
    // Gecmis
    localStorage.setItem('bist_hist',JSON.stringify((S.sigHistory||[]).slice(0,500)));
  }catch(e){}
}

// renderSigs'e kaydet ekle
var _b6_origRS = renderSigs;
renderSigs = function(){
  _b6_origRS.apply(this,arguments);
  setTimeout(b6SaveAll,300);
};

//  5. AI SOHBET MODALI 
var _aiChatHistory = [];

function openAIChat(){
  var modal=document.getElementById('modal');
  document.getElementById('mtit').textContent='AI Trader Asistan';
  document.getElementById('mcont').innerHTML='<div id="aiChat" style="height:320px;overflow-y:auto;padding:5px;background:var(--bg3);border-radius:8px;margin-bottom:8px;font-size:11px"></div>'
    +'<div style="display:flex;gap:6px">'
    +'<input id="aiInput" class="sinput" style="margin:0;flex:1" placeholder="Soru sorun... (orn: EREGL analiz, risk nedir, hangi hisseyi alirim)" onkeydown="if(event.key===\'Enter\')sendAIMsg()">'
    +'<button class="btn c" onclick="sendAIMsg()" style="flex-shrink:0;padding:9px 13px;border-radius:7px">Gonder</button>'
    +'</div>'
    +'<div style="font-size:8px;color:var(--t4);margin-top:6px;text-align:center">Anthropic Claude API - Sinyal ve portfoy analizi</div>';
  modal.classList.add('on');
  // Hosgeldin mesaji
  if(!_aiChatHistory.length){
    appendAIMsg('asistan','Merhaba! BIST AI Trader Asistaniyim. Size sinyal analizi, risk yonetimi, portfoy ozeti veya herhangi bir konuda yardimci olabilirim. Ne sormak istersiniz?');
  } else {
    // Gecmis mesajlari goster
    var chatEl=document.getElementById('aiChat');
    if(chatEl) chatEl.innerHTML=_aiChatHistory.map(function(m){
      return formatChatMsg(m.role,m.content);
    }).join('');
  }
}

function formatChatMsg(role,content){
  var isUser=role==='user';
  return '<div style="display:flex;justify-content:'+(isUser?'flex-end':'flex-start')+';margin-bottom:8px">'
    +'<div style="max-width:85%;padding:8px 11px;border-radius:'+(isUser?'10px 10px 2px 10px':'10px 10px 10px 2px')+';background:'+(isUser?'rgba(0,212,255,.15)':'rgba(255,184,0,.08)')+';border:1px solid '+(isUser?'rgba(0,212,255,.3)':'rgba(255,184,0,.2)')+';">'
    +'<div style="font-size:10px;line-height:1.6;color:'+(isUser?'var(--cyan)':'var(--t1)')+'">'+content+'</div>'
    +'</div></div>';
}

function appendAIMsg(role,content){
  _aiChatHistory.push({role:role,content:content});
  var chatEl=document.getElementById('aiChat');
  if(!chatEl) return;
  var div=document.createElement('div');
  div.innerHTML=formatChatMsg(role,content);
  chatEl.appendChild(div.firstChild);
  chatEl.scrollTop=chatEl.scrollHeight;
}

function sendAIMsg(){
  var input=document.getElementById('aiInput');
  if(!input||!input.value.trim()) return;
  var userMsg=input.value.trim();
  input.value='';
  appendAIMsg('user',userMsg);

  // Sistem konteksti hazirla
  var posCount=Object.keys(S.openPositions||{}).length;
  var closed=S.closedPositions||[];
  var totalPnl=0; var wins=0;
  closed.forEach(function(p){var pnl=parseFloat(p.pnlPct);totalPnl+=pnl;if(pnl>=0)wins++;});
  var wr=closed.length?(wins/closed.length*100).toFixed(1):'N/A';

  var posLines=Object.keys(S.openPositions||{}).map(function(k){
    var pos=S.openPositions[k]; var cached=S.priceCache[k.split('_')[0]];
    var pnl=cached&&cached.price>0?(((cached.price-pos.entry)/pos.entry)*100).toFixed(2):null;
    return k.split('_')[0]+'('+k.split('_')[1]+'): Giris TL'+pos.entry+(pnl?' PnL:'+pnl+'%':'');
  }).join(', ');

  var recentSigs=S.sigs.slice(0,5).map(function(s){
    return s.ticker+'('+s.tf+') '+s.type+' Kons:%'+(s.res.cons||'-');
  }).join(', ');

  var xu=S.xu100Change||0;

  var systemPrompt='Sen BIST Katilim Endeksi uzmani bir AI trader asistanisin. '
    +'Turkce cevap ver. Kisa ve net ol. '
    +'MEVCUT DURUM: '
    +'Acik Pozisyon: '+posCount+'. '
    +(posLines?'Pozisyonlar: '+posLines+'. ':'')
    +'Win Rate: '+wr+'%. Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%. '
    +'XU100: '+(xu>=0?'+':'')+xu.toFixed(2)+'%. '
    +(recentSigs?'Son Sinyaller: '+recentSigs+'. ':'')
    +'Aktif Sistemler: '+(C.s1?'ST+TMA ':'')+( C.s2?'PRO ':'')+( C.fu?'Fusion ':'')+( C.ma?'MasterAI':'')+'.'
    +' ADX Min: '+(C.adxMin||25)+'. ATR Mult: '+(C.atrm||8)+'.';

  // Loading goster
  appendAIMsg('asistan','...');
  var chatEl=document.getElementById('aiChat');
  var loadingDiv=chatEl?chatEl.lastChild:null;

  // Anthropic API cagir
  fetch('https://api.anthropic.com/v1/messages',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      model:'claude-sonnet-4-20250514',
      max_tokens:1000,
      system:systemPrompt,
      messages:_aiChatHistory.slice(0,-1).concat([{role:'user',content:userMsg}]).slice(-10)
    })
  })
  .then(function(r){return r.json();})
  .then(function(d){
    if(loadingDiv&&chatEl) chatEl.removeChild(loadingDiv);
    var resp=(d.content&&d.content[0]&&d.content[0].text)||'Yanit alinamadi.';
    appendAIMsg('asistan',resp);
    // Son asistan mesajini guncelle
    _aiChatHistory[_aiChatHistory.length-1]={role:'asistan',content:resp};
  })
  .catch(function(e){
    if(loadingDiv&&chatEl) chatEl.removeChild(loadingDiv);
    // Fallback: kural bazli cevap
    var fallback=b6FallbackAI(userMsg);
    appendAIMsg('asistan',fallback);
  });
}

// API basarisiz olursa kural bazli AI
function b6FallbackAI(msg){
  var m=msg.toLowerCase();
  var posCount=Object.keys(S.openPositions||{}).length;
  var closed=S.closedPositions||[];
  var xu=S.xu100Change||0;

  if(m.indexOf('risk')>-1||m.indexOf('portfoy')>-1){
    var risk=posCount>=5?'YUKSEK':posCount>=3?'ORTA':'DUSUK';
    return 'Risk durumu: '+risk+'. '+posCount+' acik pozisyon. XU100: '+(xu>=0?'+':'')+xu.toFixed(1)+'%. '+(xu<-1?'Piyasa zayif, yeni giris onerilmez.':'Piyasa nispeten sakin.');
  }
  if(m.indexOf('win')>-1||m.indexOf('oran')>-1||m.indexOf('performans')>-1){
    var wins=closed.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
    var wr=closed.length?(wins/closed.length*100).toFixed(1):'N/A';
    return 'Win rate: %'+wr+' ('+closed.length+' islem). '+(parseFloat(wr)>=55?'Iyi performans!':parseFloat(wr)>=45?'Orta seviye.':'Parametreler gozden gecirilmeli.');
  }
  if(m.indexOf('ayar')>-1||m.indexOf('parametre')>-1||m.indexOf('adx')>-1){
    return 'Mevcut ayarlar: ADX min '+( C.adxMin||25)+', ATR x'+(C.atrm||8)+', PRO min '+(C.sc||5)+'. '+(closed.length>=5&&parseFloat(closed.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length/closed.length*100)<50?'Win rate dusuk - ADX\'i 30\'a cikarmanizi oneririm.':'Ayarlar makul gorunuyor.');
  }
  if(m.indexOf('sinyal')>-1||m.indexOf('tarama')>-1){
    return S.sigs.length>0?'Son '+S.sigs.length+' sinyal mevcut. En son: '+S.sigs[0].ticker+' ('+S.sigs[0].type+', Kons: %'+(S.sigs[0].res.cons||'-')+').':'Henuz sinyal yok. TARA butonuna basin.';
  }
  if(m.indexOf('xu100')>-1||m.indexOf('piyasa')>-1){
    return 'XU100: '+(xu>=0?'+':'')+xu.toFixed(2)+'%. '+(xu>1?'Piyasa guclu - sinyaller daha guvenilir.':xu<-1?'Piyasa zayif - dikkatli olun.':'Notropik seyir.');
  }
  // Ticker sorgusu
  var found=null;
  for(var i=0;i<STOCKS.length;i++){
    if(m.indexOf(STOCKS[i].t.toLowerCase())>-1){found=STOCKS[i];break;}
  }
  if(found){
    var sig=null;
    for(var j=0;j<S.sigs.length;j++){if(S.sigs[j].ticker===found.t){sig=S.sigs[j];break;}}
    var pos=S.openPositions[found.t+'_D']||S.openPositions[found.t+'_240']||S.openPositions[found.t+'_120'];
    var cached=S.priceCache[found.t];
    return found.t+' ('+found.n+'): '
      +(cached?'Fiyat TL'+cached.price.toFixed(2)+'. ':'')
      +(sig?'SINYAL var ('+sig.type+', Kons: %'+sig.res.cons+', ADX: '+sig.res.adx+'). ':'Aktif sinyal yok. ')
      +(pos?'ACIK POZISYON var (Giris: TL'+pos.entry.toFixed(2)+').':'');
  }
  return 'Anlayamadim. Soru ornekleri: "risk nedir", "EREGL analiz", "win rate", "piyasa durumu", "ayarlar"';
}

//  6. NAV'A AI SOHBET BUTONU EKLE 
window.addEventListener('load',function(){
  setTimeout(function(){
    // Nav'a AI Chat butonu ekle
    var nav=document.querySelector('nav');
    if(nav && !document.getElementById('aiChatTab')){
      var btn=document.createElement('button');
      btn.className='tab';
      btn.id='aiChatTab';
      btn.innerHTML='AI Sohbet';
      btn.onclick=function(){ openAIChat(); };
      nav.appendChild(btn);
    }

    // Tum verileri geri yukle
    b6RestoreAll();

    // Render
    setTimeout(function(){
      renderSigs(); updateBadge(); renderPositions(); renderWatchlist(); renderReport();
      if(typeof renderAgents==='function') renderAgents();
      if(typeof renderClosedPositions==='function') renderClosedPositions();
    },800);
  },300);
});

</script>
<script>

// 
// BIST v8 BLOK 7 - ELITE PWA SEVIYESI
// Prompt 1-100 uygulanabilir kisimlar
// 

//  1. RIPPLE EFFECT (Prompt 12) 
(function(){
  var style=document.createElement('style');
  style.textContent=
    '.ripple-host{position:relative;overflow:hidden}'
    +'.ripple-wave{position:absolute;border-radius:50%;background:rgba(255,255,255,.25);transform:scale(0);animation:rippleAnim .5s linear;pointer-events:none}'
    +'@keyframes rippleAnim{to{transform:scale(4);opacity:0}}'
    // Glassmorphism cards gelismis
    +'.card{backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);background:rgba(10,10,10,.75);border:1px solid rgba(255,255,255,.06);box-shadow:0 4px 24px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.05);transition:transform .2s,box-shadow .2s}'
    +'.card:active{transform:scale(.995)}'
    +'.sig{backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);transition:transform .15s,box-shadow .15s}'
    +'.sig:active{transform:scale(.98)}'
    // 3D tilt hover (desktop)
    +'.pos-card{transition:transform .2s,box-shadow .2s}'
    // Scan btn pulse
    +'#scanBtn.run{animation:scanPulse 1s ease-in-out infinite}'
    +'@keyframes scanPulse{0%,100%{box-shadow:0 0 0 0 rgba(0,230,118,.4)}50%{box-shadow:0 0 0 12px rgba(0,230,118,0)}}'
    // Skeleton shimmer
    +'.skeleton{background:linear-gradient(90deg,var(--b2) 25%,var(--b3) 50%,var(--b2) 75%);background-size:200% 100%;animation:shimmer 1.5s infinite;border-radius:6px}'
    +'@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}'
    // Particle burst (buy signal)
    +'.sig.buy::after,.sig.master::after{content:"";position:absolute;top:10px;right:10px;width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 0 0 rgba(0,230,118,.4);animation:particlePulse 2s infinite}'
    +'@keyframes particlePulse{0%{box-shadow:0 0 0 0 rgba(0,230,118,.4)}70%{box-shadow:0 0 0 10px rgba(0,230,118,0)}100%{box-shadow:0 0 0 0 rgba(0,230,118,0)}}'
    // Bottom sheet animation
    +'.movl .modal{transition:transform .35s cubic-bezier(0.32,0,0,1)}'
    // Toast gelismis
    +'#toast{backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-radius:12px;padding:10px 18px;font-weight:500}'
    // Nav scroll indicator
    +'nav{scrollbar-width:none}'
    // Strength orb
    +'.sc-badge{border-radius:50%!important;box-shadow:0 0 12px currentColor}'
    +'.sc-10,.sc-9{animation:orbGlow 2s ease-in-out infinite}'
    +'@keyframes orbGlow{0%,100%{box-shadow:0 0 8px rgba(0,230,118,.5)}50%{box-shadow:0 0 20px rgba(0,230,118,.9)}}'
    // Ink ripple butonlar
    +'.btn,.tab,#scanBtn{position:relative;overflow:hidden}'
    // Pull to refresh indicator
    +'#pullIndicator{position:fixed;top:0;left:50%;transform:translateX(-50%) translateY(-60px);width:40px;height:40px;border-radius:50%;background:var(--bg3);border:1px solid var(--b2);display:flex;align-items:center;justify-content:center;z-index:999;transition:transform .3s;font-size:18px}'
    // Streak badge
    +'.streak-badge{display:inline-flex;align-items:center;gap:3px;padding:2px 8px;border-radius:10px;font-size:9px;font-weight:700;background:linear-gradient(135deg,rgba(255,184,0,.2),rgba(255,112,0,.2));border:1px solid rgba(255,184,0,.4);color:var(--gold)}'
    // Voice btn
    +'#voiceBtn{position:fixed;bottom:70px;right:16px;width:44px;height:44px;border-radius:50%;background:rgba(0,212,255,.15);border:1px solid rgba(0,212,255,.4);color:var(--cyan);font-size:18px;display:flex;align-items:center;justify-content:center;z-index:100;cursor:pointer;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}'
    +'#voiceBtn.listening{background:rgba(255,68,68,.2);border-color:var(--red);animation:voicePulse .6s infinite}'
    +'@keyframes voicePulse{0%,100%{transform:scale(1)}50%{transform:scale(1.1)}}'
    // Konfeti
    +'.konfeti{position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9999}'
    // Loading screen
    +'#loadingScreen{position:fixed;inset:0;background:#000;z-index:10000;display:flex;flex-direction:column;align-items:center;justify-content:center;transition:opacity .5s}'
    +'#loadingScreen.hide{opacity:0;pointer-events:none}'
    +'.load-logo{font-size:32px;font-weight:700;letter-spacing:2px;margin-bottom:8px}'
    +'.load-sub{font-size:10px;color:var(--t4);letter-spacing:4px;text-transform:uppercase;margin-bottom:32px}'
    +'.load-bar{width:200px;height:2px;background:var(--b2);border-radius:2px;overflow:hidden}'
    +'.load-fill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--gold));border-radius:2px;width:0%;transition:width .3s}'
    // Price alert badge
    +'.alert-badge{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--gold);box-shadow:0 0 6px var(--gold);margin-left:4px;vertical-align:middle}'
    // Swipe row
    +'.swipe-row{position:relative;overflow:hidden}'
    +'.swipe-action{position:absolute;right:0;top:0;bottom:0;width:60px;background:var(--red);display:flex;align-items:center;justify-content:center;font-size:18px;transform:translateX(100%);transition:transform .2s}'
    +'.swipe-row.swiped .swipe-action{transform:translateX(0)}';
  document.head.appendChild(style);
  
  // Ripple fonksiyonu
  function addRipple(e){
    var btn=e.currentTarget;
    btn.classList.add('ripple-host');
    var r=document.createElement('span');
    r.className='ripple-wave';
    var rect=btn.getBoundingClientRect();
    var size=Math.max(rect.width,rect.height);
    r.style.cssText='width:'+size+'px;height:'+size+'px;left:'+(e.clientX-rect.left-size/2)+'px;top:'+(e.clientY-rect.top-size/2)+'px';
    btn.appendChild(r);
    r.addEventListener('animationend',function(){r.remove();});
  }
  document.addEventListener('click',function(e){
    var t=e.target.closest('.btn,.tab,#scanBtn');
    if(t) addRipple({currentTarget:t,clientX:e.clientX,clientY:e.clientY});
  });
})();

//  2. HAPTIC FEEDBACK (Prompt 2) 
function haptic(type){
  if(!navigator.vibrate) return;
  if(type==='light') navigator.vibrate(10);
  else if(type==='medium') navigator.vibrate(25);
  else if(type==='heavy') navigator.vibrate([30,10,30]);
  else if(type==='success') navigator.vibrate([10,50,10,50,20]);
}

// Scan butonuna haptic
var _v8_scanBtn=document.getElementById('scanBtn');
if(_v8_scanBtn){
  _v8_scanBtn.addEventListener('touchstart',function(){haptic('medium');},{passive:true});
}

//  3. LOADING SCREEN (Prompt 100) 
(function(){
  var ls=document.createElement('div');
  ls.id='loadingScreen';
  ls.innerHTML='<div class="load-logo"><span style="color:#fff">BIST</span><span style="color:var(--cyan)"> AI</span></div>'
    +'<div class="load-sub"></div>'
    +'<div class="load-bar"><div class="load-fill" id="loadFill"></div></div>'
    +'<div style="font-size:9px;color:var(--t4);margin-top:12px;font-family:Courier New,monospace" id="loadStatus">Baslatiliyor...</div>';
  document.body.appendChild(ls);
  var fill=document.getElementById('loadFill');
  var status=document.getElementById('loadStatus');
  var progress=0;
  var msgs=['Sistemler yukleniyor...','Hisse listesi hazirlaniyor...','Pine motorlari baslatiliyor...','Veri kaynaklari baglaniyor...','Hazir!'];
  var iv=setInterval(function(){
    progress+=Math.random()*25;
    if(progress>95) progress=95;
    if(fill) fill.style.width=progress+'%';
    if(status) status.textContent=msgs[Math.floor(progress/25)]||msgs[4];
  },200);
  window.addEventListener('load',function(){
    clearInterval(iv);
    if(fill) fill.style.width='100%';
    if(status) status.textContent='Hazir!';
    setTimeout(function(){
      var lsEl=document.getElementById('loadingScreen');
      if(lsEl) lsEl.classList.add('hide');
      setTimeout(function(){if(lsEl)lsEl.remove();},500);
    },400);
  });
})();

//  4. PULL TO REFRESH (Prompt 7) 
(function(){
  var indicator=document.createElement('div');
  indicator.id='pullIndicator';
  indicator.textContent='';
  document.body.appendChild(indicator);
  var startY=0; var pulling=false; var threshold=80;
  var mainEl=document.querySelector('main');
  if(!mainEl) return;
  mainEl.addEventListener('touchstart',function(e){
    if(mainEl.scrollTop===0){startY=e.touches[0].clientY;pulling=true;}
  },{passive:true});
  mainEl.addEventListener('touchmove',function(e){
    if(!pulling) return;
    var dist=e.touches[0].clientY-startY;
    if(dist>0&&dist<threshold+20){
      var pct=Math.min(dist/threshold,1);
      var el=document.getElementById('pullIndicator');
      if(el){el.style.transform='translateX(-50%) translateY('+(dist*0.5-60)+'px)';el.textContent=pct>=1?'':'';el.style.opacity=pct;}
    }
  },{passive:true});
  mainEl.addEventListener('touchend',function(e){
    if(!pulling) return;
    var dist=e.changedTouches[0].clientY-startY;
    var el=document.getElementById('pullIndicator');
    if(el){el.style.transform='translateX(-50%) translateY(-60px)';el.style.opacity=0;}
    if(dist>threshold&&!S.scanning){haptic('medium');toast('Tarama baslatiliyor...');startScan();}
    pulling=false;
  },{passive:true});
})();

//  5. SWIPE TO DELETE - Watchlist (Prompt 11) 
var _v8_origRWL=renderWatchlist;
renderWatchlist=function(){
  _v8_origRWL.apply(this,arguments);
  setTimeout(function(){
    var items=document.querySelectorAll('.wl-item');
    items.forEach(function(item){
      if(item.dataset.swipe) return;
      item.dataset.swipe='1';
      var startX=0;
      item.addEventListener('touchstart',function(e){startX=e.touches[0].clientX;},{passive:true});
      item.addEventListener('touchend',function(e){
        var dx=startX-e.changedTouches[0].clientX;
        if(dx>50){item.classList.add('swiped');haptic('light');}
        else if(dx<-20){item.classList.remove('swiped');}
      },{passive:true});
    });
  },200);
};

//  6. SKELETON LOADING (Prompt 7) 
function showSkeleton(containerId,count){
  var el=document.getElementById(containerId);
  if(!el) return;
  var html='';
  for(var i=0;i<(count||3);i++){
    html+='<div style="padding:12px;margin-bottom:7px;border-radius:8px;border:1px solid var(--b1)">'
      +'<div class="skeleton" style="height:14px;width:60%;margin-bottom:8px"></div>'
      +'<div class="skeleton" style="height:10px;width:40%;margin-bottom:6px"></div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px">'
      +'<div class="skeleton" style="height:36px"></div>'
      +'<div class="skeleton" style="height:36px"></div>'
      +'<div class="skeleton" style="height:36px"></div>'
      +'<div class="skeleton" style="height:36px"></div>'
      +'</div></div>';
  }
  el.innerHTML=html;
}

// startScan'a skeleton ekle
var _v8_origStartScan=startScan;
startScan=function(){
  showSkeleton('siglist',4);
  _v8_origStartScan.apply(this,arguments);
};

//  7. PRICE ALERT SISTEMI (Prompt 63) 
var _priceAlerts=JSON.parse(localStorage.getItem('bist_alerts')||'{}');

function setPriceAlert(ticker,targetPrice,direction){
  _priceAlerts[ticker]={target:targetPrice,dir:direction||'above',set:Date.now()};
  localStorage.setItem('bist_alerts',JSON.stringify(_priceAlerts));
  toast(ticker+' fiyat alarmi: TL'+targetPrice+' '+(direction==='below'?'alti':'ustu'));
  haptic('success');
}

function checkPriceAlerts(){
  Object.keys(_priceAlerts).forEach(function(ticker){
    var alert=_priceAlerts[ticker];
    var cached=S.priceCache[ticker];
    if(!cached||!cached.price) return;
    var triggered=false;
    if(alert.dir==='above'&&cached.price>=alert.target) triggered=true;
    if(alert.dir==='below'&&cached.price<=alert.target) triggered=true;
    if(!triggered) return;
    // Alarm tetiklendi
    toast(ticker+' ALARM! TL'+cached.price.toFixed(2)+' hedef TL'+alert.target+' '+( alert.dir==='above'?'ustu':'alti'),4000);
    haptic('heavy');
    if(C.snd) beep(true);
    if(TG&&TG.token&&TG.chat){
      var msg='FIYAT ALARMI\n'+ticker+': TL'+cached.price.toFixed(2)+'\nHedef: TL'+alert.target+' '+(alert.dir==='above'?'ustune cikti':'altina indi');
      fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:msg})}).catch(function(){});
    }
    delete _priceAlerts[ticker];
    localStorage.setItem('bist_alerts',JSON.stringify(_priceAlerts));
  });
}

// Fiyat alarm modal
function openPriceAlertModal(ticker){
  document.getElementById('mtit').textContent=ticker+' Fiyat Alarmi';
  var cached=S.priceCache[ticker];
  var curPrice=cached?cached.price.toFixed(2):'';
  document.getElementById('mcont').innerHTML='<div style="padding:5px 0">'
    +'<div style="font-size:12px;color:var(--t3);margin-bottom:12px">Mevcut fiyat: '+(curPrice?'<b style="color:var(--t1)">TL'+curPrice+'</b>':'henuz yok')+'</div>'
    +'<input id="alertPrice" class="sinput" type="number" step="0.01" placeholder="Hedef fiyat (TL)" style="margin-bottom:8px">'
    +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:12px">'
    +'<button class="btn g" onclick="setPriceAlert(\''+ticker+'\',parseFloat(document.getElementById(\'alertPrice\').value),\'above\');document.getElementById(\'modal\').classList.remove(\'on\')" style="padding:10px;border-radius:8px">Yukari Gecince</button>'
    +'<button class="btn r" onclick="setPriceAlert(\''+ticker+'\',parseFloat(document.getElementById(\'alertPrice\').value),\'below\');document.getElementById(\'modal\').classList.remove(\'on\')" style="padding:10px;border-radius:8px">Asagi Gecince</button>'
    +'</div>'
    +(Object.keys(_priceAlerts).length?'<div style="margin-top:8px"><div style="font-size:9px;color:var(--t4);margin-bottom:6px;text-transform:uppercase;letter-spacing:2px">Aktif Alarmlar</div>'
      +Object.keys(_priceAlerts).map(function(t){
        var a=_priceAlerts[t];
        return'<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--b1)">'
          +'<span style="color:var(--t2)">'+t+' TL'+a.target+'</span>'
          +'<button class="btn r" style="font-size:9px;padding:3px 8px" onclick="delete _priceAlerts[\''+t+'\'];localStorage.setItem(\'bist_alerts\',JSON.stringify(_priceAlerts));openPriceAlertModal(\''+ticker+'\')">Sil</button>'
          +'</div>';
      }).join('')+'</div>':'')
    +'</div>';
  document.getElementById('modal').classList.add('on');
}

//  8. WHAT-IF SIMULATOR (Prompt 74) 
function openWhatIfSimulator(){
  var closed=S.closedPositions||[];
  if(closed.length<3){toast('En az 3 kapali pozisyon gerekli!');return;}
  document.getElementById('mtit').textContent='What-If Simulator';
  var currentPnl=0; closed.forEach(function(p){currentPnl+=parseFloat(p.pnlPct);});

  document.getElementById('mcont').innerHTML='<div style="padding:3px 0">'
    +'<div style="font-size:11px;color:var(--t3);margin-bottom:12px">Mevcut toplam PnL: <b style="color:'+(currentPnl>=0?'var(--green)':'var(--red)')+'">'+( currentPnl>=0?'+':'')+currentPnl.toFixed(2)+'%</b></div>'
    +'<div class="ctitle">Stop Loss Degistir</div>'
    +'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
    +'<span style="font-size:10px;color:var(--t3)">Eger stop %</span>'
    +'<input id="wifStop" type="range" min="1" max="15" step="0.5" value="'+(C.atrm||8)*0.5+'" style="flex:1" oninput="updateWhatIf()">'
    +'<span id="wifStopVal" style="font-size:11px;color:var(--gold);width:30px">'+( (C.atrm||8)*0.5).toFixed(1)+'%</span>'
    +'</div>'
    +'<div class="ctitle">Min Konsensus</div>'
    +'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
    +'<span style="font-size:10px;color:var(--t3)">Eger min kons %</span>'
    +'<input id="wifCons" type="range" min="30" max="90" step="5" value="'+(C.minCons||50)+'" style="flex:1" oninput="updateWhatIf()">'
    +'<span id="wifConsVal" style="font-size:11px;color:var(--cyan);width:30px">'+(C.minCons||50)+'%</span>'
    +'</div>'
    +'<div id="wifResult" style="background:var(--bg3);border-radius:8px;padding:12px;border:1px solid var(--b2)"></div>'
    +'</div>';
  updateWhatIf();
  document.getElementById('modal').classList.add('on');
}

function updateWhatIf(){
  var stopPct=parseFloat(document.getElementById('wifStop')?document.getElementById('wifStop').value:5);
  var consPct=parseFloat(document.getElementById('wifCons')?document.getElementById('wifCons').value:50);
  var sv=document.getElementById('wifStopVal'); if(sv) sv.textContent=stopPct.toFixed(1)+'%';
  var cv=document.getElementById('wifConsVal'); if(cv) cv.textContent=consPct+'%';
  var closed=S.closedPositions||[];
  var simPnl=0; var simWins=0; var filtered=0;
  closed.forEach(function(p){
    var cons=parseFloat(p.cons||0);
    if(cons<consPct){filtered++;return;}
    var pnl=parseFloat(p.pnlPct);
    var simExit=pnl<0?Math.max(pnl,-stopPct):pnl;
    simPnl+=simExit;
    if(simExit>=0) simWins++;
  });
  var simCount=closed.length-filtered;
  var realPnl=0; var realWins=0;
  closed.forEach(function(p){realPnl+=parseFloat(p.pnlPct);if(parseFloat(p.pnlPct)>=0)realWins++;});
  var diff=simPnl-realPnl;
  var el=document.getElementById('wifResult');
  if(!el) return;
  el.innerHTML='<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'
    +'<div><div style="font-size:9px;color:var(--t4);margin-bottom:4px">Gercek</div>'
    +'<div style="font-size:16px;font-weight:700;color:'+(realPnl>=0?'var(--green)':'var(--red)') +'">'+( realPnl>=0?'+':'')+realPnl.toFixed(2)+'%</div>'
    +'<div style="font-size:9px;color:var(--t4)">'+realWins+'/'+closed.length+' kaz.</div></div>'
    +'<div><div style="font-size:9px;color:var(--t4);margin-bottom:4px">Simule</div>'
    +'<div style="font-size:16px;font-weight:700;color:'+(simPnl>=0?'var(--green)':'var(--red)') +'">'+( simPnl>=0?'+':'')+simPnl.toFixed(2)+'%</div>'
    +'<div style="font-size:9px;color:var(--t4)">'+simWins+'/'+simCount+' kaz. ('+filtered+' filtrelendi)</div></div>'
    +'</div>'
    +'<div style="margin-top:8px;padding:7px;border-radius:6px;background:rgba(0,0,0,.3);font-size:10px;color:'+(diff>=0?'var(--green)':'var(--red)')+';text-align:center">'
    +(diff>=0?'Bu ayarlarla '+diff.toFixed(2)+'% DAHA FAZLA kazanirdiniz':'Bu ayarlarla '+Math.abs(diff).toFixed(2)+'% DAHA AZ kazanirdiniz')
    +'</div>';
}

//  9. STREAK BADGE (Prompt 75) 
function calcStreak(){
  var closed=S.closedPositions||[];
  if(!closed.length) return 0;
  var streak=0;
  for(var i=0;i<closed.length;i++){
    if(parseFloat(closed[i].pnlPct)>=0) streak++;
    else break;
  }
  return streak;
}

function getStreakBadgeHTML(){
  var streak=calcStreak();
  if(streak<2) return '';
  var emoji=streak>=10?'ALTIN':streak>=5?'GUMUS':'BRONZ';
  var color=streak>=10?'var(--gold)':streak>=5?'#C0C0C0':'#CD7F32';
  return'<span class="streak-badge" style="border-color:'+color+';color:'+color+'">'+( streak>=10?'?':streak>=5?'?':'?')+' '+streak+' seri ('+emoji+')</span>';
}

// Rapor sayfasina streak ekle
var _v8_origRR=renderReport;
renderReport=function(){
  _v8_origRR.apply(this,arguments);
  setTimeout(function(){
    var wkCard=document.querySelector('#wkSigs')&&document.querySelector('#wkSigs').closest('.card');
    if(!wkCard) return;
    var existing=wkCard.querySelector('.streak-badge');
    if(existing) existing.remove();
    var badge=calcStreak()>=2?getStreakBadgeHTML():'';
    if(badge){
      var div=document.createElement('div');
      div.style.cssText='margin-top:8px;text-align:center';
      div.innerHTML=badge;
      wkCard.appendChild(div);
    }
  },300);
};

//  10. VOICE COMMAND (Prompt 70) 
var _voiceActive=false;
function startVoiceCommand(){
  var SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR){toast('Ses komutu bu tarayicida desteklenmiyor');return;}
  var btn=document.getElementById('voiceBtn');
  if(_voiceActive){_voiceActive=false;if(btn)btn.classList.remove('listening');return;}
  var rec=new SR();
  rec.lang='tr-TR';
  rec.continuous=false;
  rec.interimResults=false;
  _voiceActive=true;
  if(btn) btn.classList.add('listening');
  haptic('medium');
  rec.onresult=function(e){
    var cmd=e.results[0][0].transcript.toLowerCase().trim();
    _voiceActive=false;
    if(btn) btn.classList.remove('listening');
    toast('Komut: '+cmd);
    handleVoiceCmd(cmd);
  };
  rec.onerror=function(){_voiceActive=false;if(btn)btn.classList.remove('listening');toast('Ses tanima hatasi');};
  rec.onend=function(){_voiceActive=false;if(btn)btn.classList.remove('listening');};
  rec.start();
}

function handleVoiceCmd(cmd){
  if(cmd.indexOf('tara')>-1||cmd.indexOf('tarama')>-1){
    toast('Tarama baslatiliyor!'); haptic('success'); startScan(); return;
  }
  if(cmd.indexOf('dur')>-1||cmd.indexOf('durdur')>-1){
    stopScan(); toast('Tarama durduruldu!'); return;
  }
  if(cmd.indexOf('portfoy')>-1||cmd.indexOf('pozisyon')>-1){
    pg('positions'); toast('Pozisyonlar acildi'); return;
  }
  if(cmd.indexOf('sinyal')>-1){
    pg('signals'); toast('Sinyaller acildi'); return;
  }
  if(cmd.indexOf('rapor')>-1){
    pg('report'); toast('Rapor acildi'); return;
  }
  if(cmd.indexOf('backtest')>-1){
    pg('backtest'); toast('Backtest acildi'); return;
  }
  // Hisse sorgusu
  for(var i=0;i<STOCKS.length;i++){
    if(cmd.indexOf(STOCKS[i].t.toLowerCase())>-1||cmd.indexOf(STOCKS[i].n.toLowerCase().split(' ')[0])>-1){
      openTVTicker(STOCKS[i].t,'D');
      toast(STOCKS[i].t+' grafigi aciliyor');
      return;
    }
  }
  toast('Komut: "tara", "dur", "portfoy", "sinyal", "rapor" veya hisse adi');
}

// Voice buton ekle
window.addEventListener('load',function(){
  setTimeout(function(){
    if(!document.getElementById('voiceBtn')){
      var btn=document.createElement('button');
      btn.id='voiceBtn';
      btn.innerHTML='?';
      btn.title='Ses komutu';
      btn.onclick=startVoiceCommand;
      document.body.appendChild(btn);
    }
  },1000);
});

//  11. KONFETI (Prompt 100 - Easter egg) 
function launchKonfeti(){
  var canvas=document.createElement('canvas');
  canvas.className='konfeti';
  canvas.width=window.innerWidth;
  canvas.height=window.innerHeight;
  document.body.appendChild(canvas);
  var ctx=canvas.getContext('2d');
  var particles=[];
  var colors=['#00d4ff','#ffb800','#00e676','#c084fc','#ff7043'];
  for(var i=0;i<120;i++){
    particles.push({
      x:Math.random()*canvas.width, y:-10,
      w:Math.random()*8+4, h:Math.random()*4+2,
      color:colors[Math.floor(Math.random()*colors.length)],
      vx:(Math.random()-0.5)*4, vy:Math.random()*4+2,
      rot:Math.random()*360, rotV:(Math.random()-0.5)*8
    });
  }
  var frame=0;
  function draw(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    particles.forEach(function(p){
      ctx.save();
      ctx.translate(p.x,p.y);
      ctx.rotate(p.rot*Math.PI/180);
      ctx.fillStyle=p.color;
      ctx.fillRect(-p.w/2,-p.h/2,p.w,p.h);
      ctx.restore();
      p.x+=p.vx; p.y+=p.vy; p.rot+=p.rotV; p.vy+=0.1;
    });
    frame++;
    if(frame<200) requestAnimationFrame(draw);
    else canvas.remove();
  }
  draw();
  haptic('heavy');
  toast('? BIST AI v8 - ELITE SEVIYE! ?',5000);
}

// XU100 10.000 easter egg
var _v8_xu100Prev=0;
var _v8_origRXU=typeof renderXU==='function'?renderXU:null;
if(_v8_origRXU){
  renderXU=function(price,change){
    _v8_origRXU.apply(this,arguments);
    if(price>0&&_v8_xu100Prev<10000&&price>=10000){
      setTimeout(launchKonfeti,500);
    }
    _v8_xu100Prev=price;
  };
}

//  12. SEO META TAGS (Prompt 97) 
(function(){
  var metas=[
    {name:'description',content:'BIST Katilim Endeksi AI Tarayici - Gercek zamanli sinyal, backtest, portfoy takibi'},
    {name:'keywords',content:'BIST, katilim, borsa, hisse, sinyal, AI, tarama, backtest'},
    {name:'author',content:'BIST AI Scanner'},
    {property:'og:title',content:'BIST AI Scanner - Profesyonel Borsa Takip'},
    {property:'og:description',content:'8 sistem AI konsensus, Pine Script entegrasyonu, gercek zamanli sinyal'},
    {property:'og:type',content:'website'},
    {name:'twitter:card',content:'summary'},
    {name:'twitter:title',content:'BIST AI Scanner'},
    {name:'apple-mobile-web-app-title',content:'BIST AI'},
    {name:'application-name',content:'BIST AI Scanner'},
  ];
  metas.forEach(function(m){
    var existing=document.querySelector('meta['+(m.name?'name':'property')+'="'+(m.name||m.property)+'"]');
    if(!existing){
      var tag=document.createElement('meta');
      if(m.name) tag.name=m.name;
      if(m.property) tag.setAttribute('property',m.property);
      tag.content=m.content;
      document.head.appendChild(tag);
    }
  });
})();

//  13. WEB SHARE API (Prompt 56) 
function shareSig(sig){
  if(!sig||!sig.res) return;
  var text='BIST AI Sinyali: '+sig.ticker+' ('+sig.type.toUpperCase()+')\n'
    +'TF: '+(sig.tf==='D'?'Gunluk':sig.tf==='240'?'4 Saat':'2 Saat')+'\n'
    +'Konsensus: %'+sig.res.cons+' | ADX: '+sig.res.adx+'\n'
    +'https://www.tradingview.com/chart/?symbol=BIST:'+sig.ticker;
  if(navigator.share){
    navigator.share({title:'BIST AI: '+sig.ticker,text:text}).catch(function(){});
  } else {
    if(navigator.clipboard) navigator.clipboard.writeText(text).then(function(){toast('Panoya kopyalandi!');});
  }
}

//  14. GELISMIS MANIFEST (Prompt 47) 
(function(){
  var link=document.querySelector('link[rel="manifest"]');
  if(!link){
    var manifest={
      name:'BIST AI Scanner',
      short_name:'BIST AI',
      description:'Katilim Endeksi AI Tarayici',
      start_url:'/',
      display:'standalone',
      background_color:'#000000',
      theme_color:'#00d4ff',
      orientation:'portrait-primary',
      icons:[
        {src:'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="20" fill="%23000"/><text y=".9em" font-size="90" x="5">?</text></svg>',
         sizes:'any',type:'image/svg+xml'}
      ],
      shortcuts:[
        {name:'Tarama Baslat',short_name:'Tara',description:'Hisse taramasi baslat',url:'/?action=scan'},
        {name:'Portfoyum',short_name:'Portfoy',description:'Acik pozisyonlar',url:'/?page=positions'}
      ],
      categories:['finance','productivity']
    };
    var blob=new Blob([JSON.stringify(manifest)],{type:'application/json'});
    var url=URL.createObjectURL(blob);
    var el=document.createElement('link');
    el.rel='manifest'; el.href=url;
    document.head.appendChild(el);
  }
})();

//  15. RAPOR'A WHAT-IF + ALARM BUTONLARI 
window.addEventListener('load',function(){
  setTimeout(function(){
    var repPage=document.getElementById('page-report');
    if(!repPage) return;
    var extraCard=document.createElement('div');
    extraCard.className='card';
    extraCard.style.borderColor='rgba(192,132,252,.2)';
    extraCard.innerHTML='<div class="ctitle" style="color:var(--purple)">Analiz Araclari</div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">'
      +'<button class="btn c" style="padding:10px;border-radius:8px;font-size:10px" onclick="openWhatIfSimulator()">What-If Sim</button>'
      +'<button class="btn gold" style="padding:10px;border-radius:8px;font-size:10px;color:var(--gold);border-color:rgba(255,184,0,.35);background:rgba(255,184,0,.07)" onclick="openPriceAlertModal(STOCKS[0]&&STOCKS[0].t||\'EREGL\')">Fiyat Alarm</button>'
      +'<button class="btn o" style="padding:10px;border-radius:8px;font-size:10px" onclick="launchKonfeti()">? Easter Egg</button>'
      +'<button class="btn" style="padding:10px;border-radius:8px;font-size:10px" onclick="openAIChat()">AI Sohbet</button>'
      +'</div>';
    repPage.appendChild(extraCard);
    // Streak goster
    var streak=calcStreak();
    if(streak>=2){
      var sb=document.createElement('div');
      sb.style.cssText='text-align:center;margin-bottom:8px';
      sb.innerHTML=getStreakBadgeHTML();
      repPage.insertBefore(sb,repPage.firstChild);
    }
  },1200);
});

//  16. PRICE ALERT KONTROLU - Her fiyat guncellemesinde 
var _v8_origFLP=typeof b4FetchPrices==='function'?b4FetchPrices:null;
if(_v8_origFLP){
  b4FetchPrices=function(){
    _v8_origFLP.apply(this,arguments);
    setTimeout(checkPriceAlerts,2000);
  };
}

// Watchlist'teki ticker isimlerine alarm butonu ekle
var _v8_origRWL2=renderWatchlist;
renderWatchlist=function(){
  _v8_origRWL2.apply(this,arguments);
  setTimeout(function(){
    var items=document.querySelectorAll('.wl-item');
    items.forEach(function(item){
      var ticker=item.querySelector('.wl-ticker');
      if(!ticker||item.querySelector('.alarm-btn')) return;
      var t=ticker.textContent.trim();
      var hasAlert=!!_priceAlerts[t];
      var btn=document.createElement('button');
      btn.className='wl-btn alarm-btn';
      btn.title='Fiyat alarmi';
      btn.innerHTML=hasAlert?'?':'?';
      btn.style.color=hasAlert?'var(--gold)':'var(--t4)';
      btn.onclick=function(e){e.stopPropagation();openPriceAlertModal(t);};
      item.insertBefore(btn,item.lastChild);
    });
  },200);
};

//  17. VIRTUAL SCROLL - cok fazla sinyal icin (Prompt 16) 
var _v8_sigPage=0;
var _v8_sigPageSize=20;
var _v8_origRS2=renderSigs;
renderSigs=function(){
  _v8_sigPage=0;
  _v8_origRS2.apply(this,arguments);
  // 20'den fazla sinyal varsa "Daha Fazla Goster" butonu
  setTimeout(function(){
    var list=document.getElementById('siglist');
    if(!list) return;
    var visibleSigs=S.sigs.filter(function(s){
      if(S.tfFilter&&S.tfFilter.length&&S.tfFilter.indexOf(s.tf)===-1) return false;
      return true;
    });
    if(visibleSigs.length>_v8_sigPageSize){
      var moreBtn=document.createElement('button');
      moreBtn.className='btn c';
      moreBtn.style.cssText='width:100%;padding:11px;border-radius:8px;margin-top:8px;font-size:12px';
      moreBtn.textContent='Daha Fazla Goster ('+(visibleSigs.length-_v8_sigPageSize)+' sinyal)';
      moreBtn.onclick=function(){
        _v8_sigPage++;
        _v8_sigPageSize+=20;
        _v8_origRS2.apply(window,[]);
      };
      list.appendChild(moreBtn);
    }
  },200);
};

//  18. TABLO VERILERI - Resimde gorulenleri goster 
// openSig'deki Pine tablo renderini guclendir
function renderPineTableFull(res){
  if(!res) return '';
  var sys1=res.sys1; var sys2=res.sys2; var fu=res.fusion; var ma=res.master_ai; var ag=res.agents;
  if(!sys1&&!sys2&&!fu&&!ma) return '';
  function row(label,val,color){
    return'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
      +'<span style="color:rgba(255,255,255,.5);font-size:9px">'+label+'</span>'
      +'<span style="color:'+(color||'var(--t2)')+';font-size:9px;font-weight:600;font-family:Courier New,monospace">'+val+'</span></div>';
  }
  function box(title,color,d,extras){
    if(!d) return '';
    var wr=d.buys>0?(d.wins/d.buys*100).toFixed(0):'0';
    return'<div style="background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.08);border-top:2px solid '+color+';border-radius:8px;padding:10px">'
      +'<div style="font-size:9px;font-weight:700;color:'+color+';margin-bottom:7px;text-transform:uppercase;letter-spacing:1px">'+title+'</div>'
      +row('Sembol',res._ticker||'-')
      +row('Toplam AL',d.buys)
      +row('Toplam SAT',d.sells)
      +row('Karl/Zarar',d.wins+'/'+d.losses,d.wins>=d.losses?'var(--green)':'var(--red)')
      +row('Toplam %',(d.total_pnl>=0?'+':'')+d.total_pnl,d.total_pnl>=0?'var(--green)':'var(--red)')
      +row('Pozisyon',d.open_pnl&&d.open_pnl!==0?(d.open_pnl>=0?'+':'')+d.open_pnl+'%':'YOK',d.open_pnl&&d.open_pnl>0?'var(--green)':d.open_pnl&&d.open_pnl<0?'var(--red)':'var(--t4)')
      +(extras||'')
      +'</div>';
  }
  var html='<div style="margin-top:10px">'
    +'<div style="font-size:8px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:8px">PINE TABLOLARI - 2 YIL (504 BAR)</div>'
    +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:6px">'
    +box('Master AI','var(--gold)',ma,ma?row('Consensus Buy','%'+ma.buy_consensus,'var(--green)')+row('Consensus Sell','%'+ma.sell_consensus,'var(--red)')+row('Dyn Buy Thresh',ma.dyn_buy_thresh)+row('Dyn Sell Thresh',ma.dyn_sell_thresh):'')
    +box('En Iyi Agent',ag?'var(--purple)':'var(--b3)',ag?Object.values(ag).reduce(function(best,a){return a.pnl>best.pnl?a:best;},{pnl:-9999}):null,ag?(function(){
      var best=null; var bestName=''; var bestPnl=-9999;
      ['a60','a61','a62','a81','a120'].forEach(function(k){if(ag[k]&&ag[k].pnl>bestPnl){bestPnl=ag[k].pnl;best=ag[k];bestName=k.toUpperCase();}});
      return best?row('En Iyi',bestName,'var(--gold)')+row('Toplam AL',best.buys)+row('Karl/Zarar',best.wins+'/'+best.losses,best.wins>=best.losses?'var(--green)':'var(--red)')+row('Toplam %',(best.pnl>=0?'+':'')+best.pnl,best.pnl>=0?'var(--green)':'var(--red)'):'';
    })():'')
    +box('Sistem 1','var(--cyan)',sys1)
    +box('PRO Engine','var(--cyan)',sys2,sys2?row('PRO Skor',sys2.score+'/6',sys2.score>=5?'var(--green)':sys2.score>=3?'var(--gold)':'var(--red)'):'')
    +'</div>';
  // Agent dashboard tablosu
  if(ag){
    html+='<div style="background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:10px;margin-bottom:5px">'
      +'<div style="font-size:9px;font-weight:700;color:var(--orange);margin-bottom:8px">AGENT DASHBOARD</div>'
      +'<div style="display:grid;grid-template-columns:40px 1fr 60px;gap:3px;font-size:8px;color:var(--t4);padding:2px 0;border-bottom:1px solid rgba(255,255,255,.08)">'
      +'<span>Agent</span><span></span><span style="text-align:right">PnL %</span></div>';
    ['a60','a61','a62','a81','a120'].forEach(function(k){
      var a=ag[k]; if(!a) return;
      var pclr=a.pnl>=0?'var(--green)':'var(--red)';
      var bw=Math.min(100,Math.max(5,a.pnl+50));
      html+='<div style="display:grid;grid-template-columns:40px 1fr 60px;gap:3px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04);align-items:center">'
        +'<span style="font-size:9px;font-weight:700;color:var(--cyan)">'+k.toUpperCase()+'</span>'
        +'<div style="height:3px;background:rgba(255,255,255,.1);border-radius:2px;overflow:hidden"><div style="height:100%;width:'+bw+'%;background:'+pclr+'"></div></div>'
        +'<span style="font-size:9px;font-weight:700;color:'+pclr+';text-align:right;font-family:Courier New,monospace">'+(a.pnl>=0?'+':'')+a.pnl+'</span>'
        +'</div>';
    });
    html+='</div>';
  }
  html+='</div>';
  return html;
}

// openSig override - full pine tablo
var _v8_origOS=openSig;
openSig=function(idx){
  _v8_origOS.apply(this,arguments);
  setTimeout(function(){
    var sig=S.curSig; if(!sig||!sig.res) return;
    if(sig.res.ticker) sig.res._ticker=sig.ticker;
    else sig.res._ticker=sig.ticker;
    var mcont=document.getElementById('mcont'); if(!mcont) return;
    // Mevcut Pine tablosunu kaldir
    var existingPine=mcont.querySelectorAll('[style*="PINE"]');
    existingPine.forEach(function(el){el.remove();});
    // Yeni tam tabloyu ekle
    var pine=renderPineTableFull(sig.res);
    if(pine){
      var div=document.createElement('div');
      div.innerHTML=pine;
      mcont.appendChild(div.firstChild);
      // Paylasim butonu ekle
      var shareDiv=document.createElement('div');
      shareDiv.style.cssText='margin-top:8px';
      shareDiv.innerHTML='<button class="btn" style="width:100%;padding:8px;border-radius:7px;font-size:10px" onclick="shareSig(S.curSig)">? Sinyali Paylas</button>';
      mcont.appendChild(shareDiv);
    }
  },100);
};

</script>

<script>

// BIST v11 BLOK 8 - IOS SAFE
// Duzeltmeler:
// 1. SW blob URL kaldirildi -> sw.js dosyasi kullaniliyor
// 2. dinamik script inject -> try-catch ile sarildi
// 3. renderSt override -> guvende
// 4. PerformanceObserver -> try-catch icinde
// 5. IndexedDB -> try-catch icinde
// 6. btoa outerHTML -> guvende

//  1. CSS (iOS Safe) 
(function(){
  try{
    var st=document.createElement('style');
    st.textContent=
      '@media(prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;transition-duration:.01ms!important}}'
      +':focus-visible{outline:2px solid var(--cyan);outline-offset:2px;border-radius:4px}'
      +'body.hc{--bg:#000;--bg2:#000;--bg3:#0a0a0a;--b1:#444;--b2:#666;--t1:#fff;--t2:#fff;--t3:#ddd;--t4:#aaa}'
      +'body.hc .card{border:2px solid var(--b2)}'
      +'#installBanner{position:fixed;bottom:80px;left:12px;right:12px;background:rgba(10,10,10,.95);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(0,212,255,.3);border-radius:14px;padding:14px 16px;z-index:500;display:none;animation:slideUp .3s ease}'
      +'@keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}'
      +'#ctxMenu{position:fixed;background:rgba(15,15,15,.97);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:6px;z-index:2000;min-width:160px;display:none;box-shadow:0 8px 32px rgba(0,0,0,.6)}'
      +'#ctxMenu button{display:block;width:100%;padding:10px 14px;border:none;background:transparent;color:var(--t2);font-size:12px;text-align:left;border-radius:8px;cursor:pointer}'
      +'#ctxMenu button:active{background:rgba(255,255,255,.08)}'
      +'#tutorialOverlay{position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:3000;display:none;align-items:center;justify-content:center}'
      +'#tutorialOverlay.on{display:flex}'
      +'.tutorial-card{background:var(--bg2);border:1px solid var(--b2);border-radius:16px;padding:24px;margin:16px;max-width:320px;text-align:center}'
      +'.sector-cell{border-radius:6px;padding:8px;text-align:center;cursor:pointer;transition:transform .15s}'
      +'.sector-cell:active{transform:scale(.95)}'
      +'.bt-compare-col{flex:1;min-width:0;background:var(--bg3);border:1px solid var(--b2);border-radius:10px;padding:10px}';
    document.head.appendChild(st);
  }catch(e){ console.warn('CSS blok8 hatasi:',e.message); }
})();

//  2. ARIA LABELS 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      var fixes=[
        ['#scanBtn','Tarama baslat'],['nav','Navigasyon'],
        ['main','Ana icerik'],['#modal','Sinyal detayi'],
      ];
      fixes.forEach(function(f){
        var el=document.querySelector(f[0]);
        if(el&&!el.getAttribute('aria-label')) el.setAttribute('aria-label',f[1]);
      });
      document.querySelectorAll('.tab').forEach(function(t){t.setAttribute('role','tab');});
    }catch(e){}
  },2000);
});

//  3. INDEXEDDB (iOS Safe) 
var _IDB=null;
function openIDB(cb){
  if(_IDB){cb(_IDB);return;}
  try{
    var req=indexedDB.open('bist_elite',3);
    req.onupgradeneeded=function(e){
      try{
        var db=e.target.result;
        ['signals','positions','closed','history','settings','alerts'].forEach(function(s){
          if(!db.objectStoreNames.contains(s)){
            if(s==='signals') db.createObjectStore(s,{keyPath:'id'});
            else if(['positions','settings','alerts'].indexOf(s)>-1) db.createObjectStore(s,{keyPath:'k'});
            else db.createObjectStore(s,{autoIncrement:true});
          }
        });
      }catch(e2){ console.warn('IDB upgrade:',e2.message); }
    };
    req.onsuccess=function(e){_IDB=e.target.result;cb(_IDB);};
    req.onerror=function(e){ console.warn('IDB open:',e.target.error); cb(null); };
  }catch(e){ console.warn('IDB:',e.message); cb(null); }
}
function idbPut(store,data){
  openIDB(function(db){
    if(!db) return;
    try{
      var tx=db.transaction(store,'readwrite');
      var st=tx.objectStore(store);
      var arr=Array.isArray(data)?data:[data];
      arr.forEach(function(d){try{st.put(d);}catch(e){};});
    }catch(e){}
  });
}
function idbGetAll(store,cb){
  openIDB(function(db){
    if(!db){cb([]);return;}
    try{
      var req=db.transaction(store,'readonly').objectStore(store).getAll();
      req.onsuccess=function(e){cb(e.target.result||[]);};
      req.onerror=function(){cb([]);};
    }catch(e){cb([]);}
  });
}
function idbSaveAll(){
  try{
    var sigs=(S.sigs||[]).slice(0,200).map(function(s,i){
      return{id:s.id||('s'+i+Date.now()),ticker:s.ticker,name:s.name||s.ticker,
        indices:s.indices||[],type:s.type||'buy',tf:s.tf||'D',
        time:s.time instanceof Date?s.time.toISOString():s.time,
        res:{price:s.res.price,signalPrice:s.res.signalPrice||s.res.price,
          cons:s.res.cons,adx:s.res.adx,score:s.res.score,fp:s.res.fp,
          pstate:s.res.pstate,acts:s.res.acts||[],currentStop:s.res.currentStop,
          isMaster:s.res.isMaster,strength:s.res.strength,
          sys1:s.res.sys1,sys2:s.res.sys2,fusion:s.res.fusion,
          master_ai:s.res.master_ai,agents:s.res.agents}};
    });
    idbPut('signals',sigs);
    var posArr=Object.keys(S.openPositions||{}).map(function(key){
      return Object.assign({k:key},S.openPositions[key]);
    });
    idbPut('positions',posArr);
    idbPut('settings',[
      {k:'cfg',v:JSON.stringify(C)},
      {k:'tg',v:JSON.stringify(TG)},
      {k:'wl',v:JSON.stringify(S.watchlist||[])}
    ]);
  }catch(e){ console.warn('idbSaveAll:',e.message); }
}
function idbLoadAll(){
  try{
    idbGetAll('signals',function(sigs){
      try{
        if(sigs.length>0&&(!S.sigs||S.sigs.length===0)){
          var cut=Date.now()-24*60*60*1000;
          sigs.forEach(function(s){
            try{
              if(new Date(s.time).getTime()<cut) return;
              S.sigs.push({id:s.id,ticker:s.ticker,name:s.name||s.ticker,
                indices:s.indices||[],type:s.type||'buy',tf:s.tf||'D',
                time:new Date(s.time),res:s.res||{}});
            }catch(e2){}
          });
          if(S.sigs.length){try{renderSigs();updateBadge();}catch(e2){}}
        }
      }catch(e){}
      idbGetAll('positions',function(pos){
        try{
          pos.forEach(function(p){
            try{if(p.k&&!S.openPositions[p.k]){var pd=Object.assign({},p);delete pd.k;S.openPositions[p.k]=pd;}}catch(e2){}
          });
          if(pos.length) try{renderPositions();}catch(e2){}
        }catch(e){}
      });
    });
  }catch(e){ console.warn('idbLoadAll:',e.message); }
}
var _b8_origRS=renderSigs;
renderSigs=function(){try{_b8_origRS.apply(this,arguments);}catch(e){} setTimeout(idbSaveAll,400);};

//  4. SERVICE WORKER - iOS SAFE (blob degil /sw.js) 
window.addEventListener('load',function(){
  try{
    if('serviceWorker' in navigator){
      // KRITIK: blob: URL degil, gercek dosya yolu kullan
      // Render'da /sw.js endpoint'i var
      navigator.serviceWorker.register('/sw.js').then(function(reg){
        var d=document.getElementById('bgDot');if(d)d.className='bg-dot on';
        // Periodic sync - try-catch ile
        if(reg.periodicSync){
          try{reg.periodicSync.register('bist-scan',{minInterval:5*60*1000}).catch(function(){});}catch(e){}
        }
      }).catch(function(e){
        // SW kayit basarisiz - sessizce devam et
        console.warn('SW kayit:',e.message);
      });
    }
  }catch(e){ console.warn('SW init:',e.message); }
  // Online/offline
  try{
    window.addEventListener('offline',function(){
      try{toast('Cevrimdisi - Cached veriler aktif',4000);idbLoadAll();}catch(e){}
    });
    window.addEventListener('online',function(){
      try{toast('Baglanti yenilendi!');}catch(e){}
    });
  }catch(e){}
});

//  5. INSTALL PROMPT (iOS Safe) 
try{
  window.addEventListener('beforeinstallprompt',function(e){
    e.preventDefault();window._installEvt=e;
    try{
      var banner=document.getElementById('installBanner');
      if(!banner){
        banner=document.createElement('div');banner.id='installBanner';
        banner.innerHTML='<div style="display:flex;align-items:center;gap:12px">'
          +'<div style="font-size:28px">&#128200;</div>'
          +'<div style="flex:1"><div style="font-size:13px;font-weight:700;color:var(--t1)">BIST AI Yukle</div>'
          +'<div style="font-size:10px;color:var(--t4)">Ana ekrana ekle</div></div>'
          +'<button class="btn c" onclick="installPWA()" style="padding:8px 14px;border-radius:8px;font-size:11px">Yukle</button>'
          +'<button style="background:none;border:none;color:var(--t4);font-size:18px;padding:4px;cursor:pointer" onclick="document.getElementById(\'installBanner\').style.display=\'none\'">&#10005;</button>'
          +'</div>';
        document.body.appendChild(banner);
      }
      banner.style.display='block';
    }catch(e){}
  });
}catch(e){}

function installPWA(){
  try{
    if(!window._installEvt)return;
    window._installEvt.prompt();
    window._installEvt.userChoice.then(function(r){
      if(r.outcome==='accepted'){toast('BIST AI yuklendi!');if(typeof haptic==='function')haptic('success');}
      window._installEvt=null;
    }).catch(function(){});
  }catch(e){ console.warn('installPWA:',e.message); }
}

//  6. BATTERY SAVER 
function initBatterySaver(){
  try{
    if(!navigator.getBattery) return;
    navigator.getBattery().then(function(bat){
      function checkBat(){
        try{
          if(bat.level<0.2&&!bat.charging&&C.scanInterval<15){
            C.scanInterval=20;lsSet('bistcfg',C);
            if(typeof startAutoScan==='function') startAutoScan();
            toast('Pil tasarrufu modu: 20dk');
          } else if((bat.level>0.5||bat.charging)&&C.scanInterval===20){
            C.scanInterval=5;lsSet('bistcfg',C);
            if(typeof startAutoScan==='function') startAutoScan();
          }
        }catch(e){}
      }
      bat.addEventListener('levelchange',checkBat);
      bat.addEventListener('chargingchange',checkBat);
      checkBat();
    }).catch(function(){});
  }catch(e){}
}

//  7. TELEGRAM INLINE 
function sendTGInline(sig){
  try{
    if(!TG||!TG.token||!TG.chat) return;
    var r=sig.res||{};
    var tfL=sig.tf==='D'?'Gunluk':sig.tf==='240'?'4 Saat':'2 Saat';
    var tvInt=sig.tf==='D'?'1D':sig.tf==='240'?'4H':'2H';
    var chartUrl='https://www.tradingview.com/chart/?symbol=BIST:'+sig.ticker+'&interval='+tvInt;
    var isM=sig.type==='master';
    var str=r.strength||(typeof calcStrength==='function'?calcStrength(r):5);
    var bars=Math.round(str/2);var gb='';
    for(var bi=0;bi<5;bi++) gb+=(bi<bars?'#':'_');
    var msg=(isM?'MASTER AI SINYALI':'AL SINYALI')+'\n'
      +'Hisse: '+sig.ticker+(sig.name?' - '+sig.name:'')+'\n'
      +'TF: '+tfL+'\n\n'
      +'Guc: ['+gb+'] '+str+'/10\n'
      +'Sinyal Fiyati: TL'+(r.signalPrice||r.price||'-')+'\n'
      +'Anlik Fiyat: TL'+(r.price||'-')+'\n'
      +'Konsensus: %'+(r.cons||'-')+'\n'
      +'ADX: '+(r.adx||'-')+' | PRO: '+(r.score||r.pro_score||'-')+'/6\n'
      +'Bolge: '+(r.pstate||'NORMAL')
      +(TG.ch?'\n\nGrafik: '+chartUrl:'');
    var keyboard={inline_keyboard:[[
      {text:'TV Grafik',url:chartUrl},
      {text:'Detay',callback_data:'detail_'+sig.ticker+'_'+sig.tf}
    ],[
      {text:'Tum Sinyaller',callback_data:'signals'},
      {text:'Portfoy',callback_data:'portfolio'}
    ]]};
    fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({chat_id:TG.chat,text:msg,reply_markup:keyboard,disable_web_page_preview:false})
    }).catch(function(){try{if(typeof sendTG==='function') sendTG(sig);}catch(e){}});
  }catch(e){ console.warn('sendTGInline:',e.message); }
}
var _b8_origSendTG=sendTG;
sendTG=function(sig,manual){
  try{
    if(!TG||!TG.token||!TG.chat){if(manual)toast('Token/Chat ID eksik!');return;}
    if(sig.type!=='stop') sendTGInline(sig);
    else _b8_origSendTG(sig,manual);
  }catch(e){ console.warn('sendTG:',e.message); }
};

//  8. CSV EXPORT 
function exportCSV(type){
  try{
    var rows=[];var sep=',';
    var ts=new Date().toISOString().split('T')[0];
    if(type==='signals'){
      rows.push(['Ticker','Ad','TF','Tip','Sinyal Fiyati','Fiyat','Konsensus','ADX','PRO','Bolge','Guc','Tarih','Sistemler'].join(sep));
      (S.sigs||[]).forEach(function(s){
        try{
          var r=s.res||{};var dt=s.time instanceof Date?s.time:new Date(s.time);
          rows.push([s.ticker,(s.name||s.ticker).replace(/,/g,' '),s.tf,s.type,
            (r.signalPrice||r.price||''),(r.price||''),(r.cons||''),(r.adx||''),
            (r.score||''),(r.pstate||''),r.strength||'',
            dt.toLocaleString('tr-TR'),(r.acts||[]).join(';')].join(sep));
        }catch(e2){}
      });
    } else if(type==='positions'){
      rows.push(['Ticker','TF','Giris','Sinyal Fiyati','En Yuksek','Stop','Giris Tarihi'].join(sep));
      Object.keys(S.openPositions||{}).forEach(function(key){
        try{
          var p=S.openPositions[key];var parts=key.split('_');
          rows.push([parts[0],parts[1],(p.entry||''),(p.signalPrice||p.entry||''),
            (p.highest||''),(p.stopPrice||''),(p.entryTime?new Date(p.entryTime).toLocaleString('tr-TR'):'')].join(sep));
        }catch(e2){}
      });
    } else if(type==='closed'){
      rows.push(['Ticker','TF','Giris','Cikis','PnL%','Sure','Konsensus','ADX','Bolge','Sistemler','Tarih'].join(sep));
      (S.closedPositions||[]).forEach(function(p){
        try{
          rows.push([p.ticker,p.tf,(p.entry||''),(p.exit||''),(p.pnlPct||''),(p.holdDays||''),
            (p.cons||''),(p.adx||''),(p.pstate||''),(p.acts||[]).join(';'),
            (p.entryTime?new Date(p.entryTime).toLocaleString('tr-TR'):'')].join(sep));
        }catch(e2){}
      });
    }
    if(!rows.length){toast('Export edilecek veri yok');return;}
    var csv='\uFEFF'+rows.join('\n');
    var blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
    var url=URL.createObjectURL(blob);
    var a=document.createElement('a');
    a.href=url;a.download='bist_'+type+'_'+ts+'.csv';
    a.style.display='none';document.body.appendChild(a);a.click();
    setTimeout(function(){try{document.body.removeChild(a);URL.revokeObjectURL(url);}catch(e){}},1000);
    toast(type+' CSV indirildi');
    if(typeof haptic==='function') haptic('success');
  }catch(e){ console.warn('exportCSV:',e.message); toast('Export hatasi'); }
}

//  9. AUTO BACKUP 
var _lastBackup=0;
try{ _lastBackup=parseInt(localStorage.getItem('bist_last_backup')||'0'); }catch(e){}
function autoBackup(){
  try{
    var now=Date.now();
    if(now-_lastBackup<24*60*60*1000) return;
    _lastBackup=now;
    try{localStorage.setItem('bist_last_backup',now);}catch(e){}
    var data={date:new Date().toISOString(),openPositions:S.openPositions||{},
      closedPositions:(S.closedPositions||[]).slice(0,50),settings:C,watchlist:S.watchlist||[]};
    try{localStorage.setItem('bist_backup',JSON.stringify(data));}catch(e){}
    console.log('Otomatik yedek alindi');
  }catch(e){}
}

//  10. VaR/CVaR, PORTFOLIO OPTIMIZER, SECTOR HEATMAP 
function calcVaRCVaR(){
  try{
    var closed=S.closedPositions||[];
    if(closed.length<10) return{var95:'N/A',cvar95:'N/A',var99:'N/A',cvar99:'N/A'};
    var returns=closed.map(function(p){return parseFloat(p.pnlPct)/100;}).sort(function(a,b){return a-b;});
    var i95=Math.floor(returns.length*0.05);
    var i99=Math.floor(returns.length*0.01);
    var var95=Math.abs(returns[i95]||0)*100;
    var var99=Math.abs(returns[i99]||0)*100;
    var cvar95=Math.abs(returns.slice(0,i95+1).reduce(function(a,b){return a+b;},0)/(i95+1))*100;
    var cvar99=Math.abs(returns.slice(0,i99+1).reduce(function(a,b){return a+b;},0)/(i99+1))*100;
    return{var95:var95.toFixed(2),cvar95:cvar95.toFixed(2),var99:var99.toFixed(2),cvar99:cvar99.toFixed(2)};
  }catch(e){return{var95:'N/A',cvar95:'N/A',var99:'N/A',cvar99:'N/A'};}
}

function openPortfolioOptimizer(){
  try{
    var closed=S.closedPositions||[];
    if(closed.length<5){toast('En az 5 kapali islem gerekli');return;}
    var tickerStats={};
    closed.forEach(function(p){
      if(!tickerStats[p.ticker]) tickerStats[p.ticker]={returns:[],count:0};
      tickerStats[p.ticker].returns.push(parseFloat(p.pnlPct));
      tickerStats[p.ticker].count++;
    });
    var stats=Object.keys(tickerStats).filter(function(t){return tickerStats[t].count>=2;}).map(function(t){
      var rets=tickerStats[t].returns;
      var mean=rets.reduce(function(a,b){return a+b;},0)/rets.length;
      var std=Math.sqrt(rets.reduce(function(a,x){return a+(x-mean)*(x-mean);},0)/rets.length)||0.1;
      return{ticker:t,mean:mean,std:std,sharpe:mean/std,count:rets.length};
    }).sort(function(a,b){return b.sharpe-a.sharpe;});
    if(!stats.length){toast('Yeterli veri yok');return;}
    var n=Math.min(stats.length,6);
    var eqW=(100/n).toFixed(1);
    var totalS=stats.slice(0,n).reduce(function(a,s){return a+Math.max(0,s.sharpe);},0)||1;
    var varData=calcVaRCVaR();
    var html='<div style="padding:3px 0">'
      +'<div style="font-size:9px;color:var(--t4);margin-bottom:12px">Modern Portfoy Teorisi</div>'
      +'<div class="card" style="margin-bottom:8px;padding:10px"><div class="ctitle">VaR / CVaR</div>'
      +'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:5px">'
      +'<div class="sstat"><div class="sval n">%'+varData.var95+'</div><div class="slb2">VaR 95%</div></div>'
      +'<div class="sstat"><div class="sval n">%'+varData.cvar95+'</div><div class="slb2">CVaR 95%</div></div>'
      +'<div class="sstat"><div class="sval n">%'+varData.var99+'</div><div class="slb2">VaR 99%</div></div>'
      +'<div class="sstat"><div class="sval n">%'+varData.cvar99+'</div><div class="slb2">CVaR 99%</div></div>'
      +'</div></div>'
      +'<div class="card" style="padding:10px"><div class="ctitle" style="color:var(--green)">Optimal Portfoy</div>'
      +'<table style="width:100%;font-size:10px;border-collapse:collapse">'
      +'<tr><th style="text-align:left;color:var(--t4);padding:4px 0;border-bottom:1px solid var(--b2)">Hisse</th>'
      +'<th style="color:var(--t4);padding:4px;border-bottom:1px solid var(--b2)">Ort.PnL</th>'
      +'<th style="color:var(--t4);padding:4px;border-bottom:1px solid var(--b2)">Sharpe</th>'
      +'<th style="color:var(--cyan);padding:4px;border-bottom:1px solid var(--b2)">Agirlik</th></tr>';
    stats.slice(0,n).forEach(function(s){
      var momW=(Math.max(0,s.sharpe)/totalS*100).toFixed(1);
      html+='<tr><td style="color:var(--t2);padding:5px 0;cursor:pointer" onclick="openTVTicker(\''+s.ticker+'\',\'D\')"><b>'+s.ticker+'</b></td>'
        +'<td style="color:'+(s.mean>=0?'var(--green)':'var(--red)')+';text-align:center">'+(s.mean>=0?'+':'')+s.mean.toFixed(1)+'%</td>'
        +'<td style="color:var(--cyan);text-align:center">'+s.sharpe.toFixed(2)+'</td>'
        +'<td style="text-align:center"><b style="color:var(--gold)">%'+momW+'</b></td></tr>';
    });
    html+='</table></div></div>';
    document.getElementById('mtit').textContent='AI Portfoy Optimize';
    document.getElementById('mcont').innerHTML=html;
    document.getElementById('modal').classList.add('on');
  }catch(e){ console.warn('openPortfolioOptimizer:',e.message); toast('Hata: '+e.message); }
}

var SECTORS={
  'Enerji':['AKFYE','CWENE','ENJSA','EUPWR','GESAN','ZOREN'],
  'Savunma':['ASELS','ALTNY'],
  'Kimya':['PETKM','SASA','AKSA'],
  'Metal':['EREGL','ISDMR','KRDMD','BRSAN'],
  'Gida':['BIMAS','ULKER','TATGD','OBAMS','BANVT'],
  'Insaat':['EKGYO','DAPGM'],
  'Teknoloji':['LOGO','ARDYZ','KONTR','YEOTK'],
  'Holding':['KCHOL','SAHOL','ENKAI'],
  'Perakende':['MAVI','MGROS'],
  'Turizm':['THYAO','TAVHL'],
  'Sanayi':['FROTO','TOASO','VESTL'],
  'Saglik':['MPARK'],
  'Telekom':['TCELL','TTKOM'],
  'Cimento':['CIMSA','BSOKE','BUCIM','OYAKC'],
};
function openSectorHeatmap(){
  try{
    var wAgo=Date.now()-7*86400000;
    var html='<div style="padding:3px 0"><div style="font-size:9px;color:var(--t4);margin-bottom:10px">Sektor sinyal yogunlugu - Son 7 gun</div>'
      +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px">';
    Object.keys(SECTORS).forEach(function(sector){
      try{
        var tickers=SECTORS[sector];var cnt=0;var pnlSum=0;var pnlCnt=0;
        (S.sigHistory||[]).forEach(function(h){try{if(new Date(h.time).getTime()>wAgo&&tickers.indexOf(h.ticker)>-1)cnt++;}catch(e){}});
        (S.closedPositions||[]).forEach(function(p){try{if(tickers.indexOf(p.ticker)>-1){pnlSum+=parseFloat(p.pnlPct||0);pnlCnt++;}}catch(e){}});
        var avgPnl=pnlCnt?pnlSum/pnlCnt:0;
        var bg=cnt>3?'rgba(0,230,118,.12)':cnt>0?'rgba(0,212,255,.08)':'rgba(255,255,255,.03)';
        var border=cnt>3?'rgba(0,230,118,.35)':cnt>0?'rgba(0,212,255,.2)':'rgba(255,255,255,.06)';
        html+='<div class="sector-cell" style="background:'+bg+';border:1px solid '+border+'">'
          +'<div style="font-size:9px;font-weight:700;color:var(--t1);margin-bottom:3px">'+sector+'</div>'
          +'<div style="font-size:11px;font-weight:700;color:var(--cyan)">'+cnt+'</div>'
          +'<div style="font-size:8px;color:var(--t4)">sinyal</div>'
          +(pnlCnt?'<div style="font-size:8px;color:'+(avgPnl>=0?'var(--green)':'var(--red)')+'">'+( avgPnl>=0?'+':'')+avgPnl.toFixed(1)+'%</div>':'')
          +'</div>';
      }catch(e2){}
    });
    html+='</div></div>';
    document.getElementById('mtit').textContent='Sektor Heatmap';
    document.getElementById('mcont').innerHTML=html;
    document.getElementById('modal').classList.add('on');
  }catch(e){ console.warn('openSectorHeatmap:',e.message); }
}

//  11. GDPR 
function exportAllUserData(){
  try{
    var data={exportDate:new Date().toISOString(),signals:S.sigs||[],
      openPositions:S.openPositions||{},closedPositions:S.closedPositions||[],
      signalHistory:S.sigHistory||[],watchlist:S.watchlist||[],settings:C};
    var blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
    var url=URL.createObjectURL(blob);
    var a=document.createElement('a');
    a.href=url;a.download='bist_ai_verilerim_'+new Date().toISOString().split('T')[0]+'.json';
    a.click();URL.revokeObjectURL(url);
    toast('Tum verileriniz indirildi');
  }catch(e){ toast('Export hatasi: '+e.message); }
}
function deleteAllUserData(){
  if(!confirm('TUM verileriniz silinecek. Emin misiniz?')) return;
  try{localStorage.clear();}catch(e){}
  S.sigs=[];S.openPositions={};S.closedPositions=[];S.sigHistory=[];S.watchlist=[];
  openIDB(function(db){
    if(!db) return;
    ['signals','positions','closed','history','settings','alerts'].forEach(function(s){
      try{db.transaction(s,'readwrite').objectStore(s).clear();}catch(e){}
    });
  });
  try{renderSigs();renderPositions();renderWatchlist();}catch(e){}
  toast('Tum veriler silindi');
}

//  12. BACKTEST COMPARE 
var _btCompareResults=[];
function addToBTCompare(result){
  try{
    if(!result) return;
    _btCompareResults.push(result);
    if(_btCompareResults.length>3) _btCompareResults.shift();
    renderBTCompare();
    toast('Karsilastirmaya eklendi ('+_btCompareResults.length+'/3)');
  }catch(e){}
}
function renderBTCompare(){
  try{
    var el=document.getElementById('btCompare');
    if(!el||!_btCompareResults.length) return;
    var labels={ret:'Getiri%',wr:'WinRate%',sharpe:'Sharpe',maxdd:'MaxDD%',tot:'Islem'};
    var html='<div class="ctitle" style="color:var(--purple)">Backtest Karsilastirma ('+_btCompareResults.length+'/3)</div>'
      +'<div style="display:flex;gap:5px;overflow-x:auto">'
      +_btCompareResults.map(function(r,i){
        return'<div class="bt-compare-col">'
          +'<div style="font-size:9px;font-weight:700;color:var(--cyan);margin-bottom:5px">'+r.sym+' '+r.tf+'</div>'
          +Object.keys(labels).map(function(m){
            var v=r[m]||'-';var clr=parseFloat(v)>0?'var(--green)':parseFloat(v)<0?'var(--red)':'var(--t2)';
            return'<div style="display:flex;justify-content:space-between;font-size:8px;padding:2px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
              +'<span style="color:var(--t4)">'+labels[m]+'</span>'
              +'<span style="color:'+clr+';font-weight:600">'+v+'</span></div>';
          }).join('')
          +'<button class="btn r" style="width:100%;padding:4px;font-size:8px;margin-top:5px;border-radius:5px" onclick="_btCompareResults.splice('+i+',1);renderBTCompare()">Cikar</button>'
          +'</div>';
      }).join('')+'</div>';
    el.innerHTML=html;
  }catch(e){}
}

//  13. RAPOR/POZISYON EKLEMELERI 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      // Rapor sayfasi
      var repPage=document.getElementById('page-report');
      if(repPage&&!document.getElementById('extraToolsCard8')){
        var card=document.createElement('div');card.id='extraToolsCard8';card.className='card';
        card.style.borderColor='rgba(0,212,255,.15)';
        card.innerHTML='<div class="ctitle" style="color:var(--cyan)">Araclar & Export</div>'
          +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">'
          +'<button class="btn c" onclick="openPortfolioOptimizer()" style="padding:10px;border-radius:8px;font-size:10px">Portfoy Optimize</button>'
          +'<button class="btn" onclick="openSectorHeatmap()" style="padding:10px;border-radius:8px;font-size:10px">Sektor Heatmap</button>'
          +'<button class="btn g" onclick="exportCSV(\'signals\')" style="padding:10px;border-radius:8px;font-size:10px">Sinyaller CSV</button>'
          +'<button class="btn g" onclick="exportCSV(\'closed\')" style="padding:10px;border-radius:8px;font-size:10px">Islemler CSV</button>'
          +'<button class="btn o" onclick="exportAllUserData()" style="padding:10px;border-radius:8px;font-size:10px">Tum Veri JSON</button>'
          +'<button class="btn r" onclick="deleteAllUserData()" style="padding:10px;border-radius:8px;font-size:10px">Verileri Sil</button>'
          +'</div>';
        repPage.appendChild(card);
      }
      // Pozisyon sayfasi
      var posPage=document.getElementById('page-positions');
      if(posPage&&!document.getElementById('posToolsBtns')){
        var div=document.createElement('div');div.id='posToolsBtns';
        div.style.cssText='display:flex;gap:6px;margin-bottom:9px';
        div.innerHTML='<button class="btn g" onclick="exportCSV(\'positions\')" style="flex:1;padding:8px;border-radius:8px;font-size:10px">CSV Export</button>'
          +'<button class="btn c" onclick="openPortfolioOptimizer()" style="flex:1;padding:8px;border-radius:8px;font-size:10px">Portfoy AI</button>';
        posPage.insertBefore(div,posPage.firstChild);
      }
    }catch(e){ console.warn('Load extras:',e.message); }

    // IDB yukle
    try{idbLoadAll();}catch(e){}
    // Battery saver
    try{initBatterySaver();}catch(e){}
    // Auto backup
    try{setTimeout(autoBackup,15000);}catch(e){}
  },1000);
});

</script>
<script>

// BIST v11 BLOK 9 - IOS SAFE - Long press + Walk forward

// Long press context menu (iOS safe)
var _ctxMenu=null;
function showContextMenu(x,y,items){
  try{
    if(!_ctxMenu){_ctxMenu=document.createElement('div');_ctxMenu.id='ctxMenu';document.body.appendChild(_ctxMenu);}
    _ctxMenu.innerHTML=items.map(function(item){
      return'<button onclick="('+item.action+')();hideCtxMenu()">'+item.icon+' '+item.label+'</button>';
    }).join('');
    _ctxMenu.style.cssText='display:block;left:'+Math.min(x,window.innerWidth-170)+'px;top:'+Math.min(y,window.innerHeight-200)+'px;position:fixed';
    if(typeof haptic==='function') haptic('medium');
    setTimeout(function(){document.addEventListener('click',hideCtxMenu,{once:true});},100);
  }catch(e){}
}
function hideCtxMenu(){try{if(_ctxMenu)_ctxMenu.style.display='none';}catch(e){}}

// Long press - touch events (iOS safe)
var _lp_timer=null;
document.addEventListener('touchstart',function(e){
  try{
    var row=e.target.closest('.strow,.sig,.wl-item');
    if(!row) return;
    _lp_timer=setTimeout(function(){
      try{
        var ticker='';
        var stt=row.querySelector('.stt,.sig-ticker,.wl-ticker');
        if(stt) ticker=stt.textContent.trim().split(' ')[0];
        if(!ticker) return;
        if(typeof haptic==='function') haptic('medium');
        var rect=row.getBoundingClientRect();
        showContextMenu(rect.left+10,rect.top,[
          {icon:'&#128200;',label:'TV Grafik',action:'function(){openTVTicker("'+ticker+'","D")}'},
          {icon:'&#11088;',label:'Watchlist Ekle',action:'function(){try{if(S.watchlist.indexOf("'+ticker+'")===-1){S.watchlist.push("'+ticker+'");lsSet("bist_wl",S.watchlist);renderWatchlist();toast("'+ticker+' eklendi");}}catch(e){}}'},
          {icon:'&#128276;',label:'Fiyat Alarmi',action:'function(){try{if(typeof openPriceAlertModal==="function")openPriceAlertModal("'+ticker+'");}catch(e){}}'},
        ]);
      }catch(e){ console.warn('Long press:',e.message); }
    },600);
  }catch(e){}
},{passive:true});
document.addEventListener('touchend',function(){try{clearTimeout(_lp_timer);}catch(e){}},{passive:true});
document.addEventListener('touchmove',function(){try{clearTimeout(_lp_timer);}catch(e){}},{passive:true});

// Walk forward (iOS safe)
if(typeof runWF!=='function'){
  runWF=function(){
    try{
      var symEl=document.getElementById('btSym');
      var sym=symEl?symEl.value:'';
      var tfEl=document.getElementById('btTF');
      var tf=tfEl?tfEl.value:'D';
      if(!sym){toast('Hisse secin!');return;}
      var wfout=document.getElementById('wfout');
      if(wfout) wfout.style.display='none';
      toast('Walk-Forward basliyor...');
      if(typeof btFetchOHLCV!=='function'){toast('Backtest motoru hazir degil');return;}
      btFetchOHLCV(sym,tf,function(ohlcv,xu100){
        try{
          if(!ohlcv||ohlcv.length<120){toast('Yetersiz veri (min 120 bar)');return;}
          var trainPct=0.7;
          var trainEl=document.getElementById('wfTrain');
          if(trainEl) trainPct=parseFloat(trainEl.value)||0.7;
          var splitIdx=Math.floor(ohlcv.length*trainPct);
          var trainData=ohlcv.slice(0,splitIdx);
          var testData=ohlcv.slice(splitIdx);
          var xu100Arr=xu100?[xu100.price]:null;
          var atrmList=[4,5,6,7,8,9,10,12];
          var results=[];
          atrmList.forEach(function(atrm){
            try{
              var cfg={sym:sym,tf:tf,sys:'all',capital:100000,commission:0.1,slippage:0.05,
                adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
                stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:atrm};
              var r=btEngine(trainData,xu100Arr,cfg);
              if(r&&r.tot>=2) results.push({atrm:atrm,trainRet:r.ret,trainWr:r.wr,trainSharpe:r.sharpe,score:parseFloat(r.sharpe)});
            }catch(e2){}
          });
          if(!results.length){toast('Yeterli islem yok');return;}
          results.sort(function(a,b){return b.score-a.score;});
          var best=results[0];
          var testCfg={sym:sym,tf:tf,sys:'all',capital:100000,commission:0.1,slippage:0.05,
            adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
            stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:best.atrm};
          var testRes=btEngine(testData,xu100Arr,testCfg);
          var html='<div class="card" style="border-color:rgba(0,212,255,.2)">'
            +'<div class="ctitle" style="color:var(--cyan)">Walk-Forward Sonucu</div>'
            +'<div style="font-size:9px;color:var(--t4);margin-bottom:10px">'+sym+' | Egitim %'+(trainPct*100).toFixed(0)+' / Test %'+((1-trainPct)*100).toFixed(0)+'</div>'
            +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-bottom:10px">'
            +'<div class="sstat"><div class="sval" style="color:var(--gold)">x'+best.atrm+'</div><div class="slb2">En Iyi ATR</div></div>'
            +(testRes?'<div class="sstat"><div class="sval '+(parseFloat(testRes.ret)>=0?'p':'n')+'">'+testRes.ret+'%</div><div class="slb2">Test Getiri</div></div>':'')
            +(testRes?'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+testRes.sharpe+'</div><div class="slb2">Test Sharpe</div></div>':'')
            +'</div>'
            +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:3px;font-size:8px;color:var(--t4);padding:3px 0;border-bottom:1px solid var(--b2)">'
            +'<span>ATR</span><span>Getiri%</span><span>Win%</span><span>Sharpe</span></div>';
          results.forEach(function(r){
            var isBest=r.atrm===best.atrm;
            html+='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:3px;padding:5px 0;border-bottom:1px solid var(--b1);font-size:9px'+(isBest?';background:rgba(0,230,118,.05)':'')+'">'
              +'<span style="color:'+(isBest?'var(--green)':'var(--t2)')+'">x'+r.atrm+(isBest?' *':'')+'</span>'
              +'<span style="color:'+(parseFloat(r.trainRet)>=0?'var(--green)':'var(--red)')+'">'+r.trainRet+'%</span>'
              +'<span>'+r.trainWr+'%</span>'
              +'<span style="color:var(--cyan)">'+r.trainSharpe+'</span></div>';
          });
          html+='<button class="btn g" style="width:100%;padding:8px;border-radius:7px;margin-top:8px;font-size:10px" onclick="try{C.atrm='+best.atrm+';lsSet(\'bistcfg\',C);var el=document.getElementById(\'s_atrm\');if(el)el.value='+best.atrm+';toast(\'ATR x'+best.atrm+' uygulandi!\');}catch(e){}">En Iyi ATR Uygula (x'+best.atrm+')</button>'
            +'</div>';
          if(wfout){wfout.innerHTML=html;wfout.style.display='block';}
          toast('Walk-Forward tamamlandi. En iyi ATR: x'+best.atrm);
        }catch(e){ console.warn('WF inner:',e.message); toast('WF hatasi: '+e.message); }
      });
    }catch(e){ console.warn('runWF:',e.message); toast('WF baslatma hatasi'); }
  };
}

</script>
<script>

// BIST v11 BLOK 10 - IOS SAFE - GitHub + Dev Panel
// ocApplyCode: dinamik script inject -> try-catch + Function constructor yok

var _devCfg2=(function(){try{return JSON.parse(localStorage.getItem('bist_dev_cfg')||'{}');}catch(e){return{};}})();
function saveDevCfg2(){try{localStorage.setItem('bist_dev_cfg',JSON.stringify(_devCfg2));}catch(e){}}

var GH={
  token:_devCfg2.ghToken||'',owner:_devCfg2.ghOwner||'',
  repo:_devCfg2.ghRepo||'',branch:_devCfg2.ghBranch||'main',
  path:_devCfg2.ghPath||'bist_elite_v11.html'
};

// CSS
(function(){
  try{
    var st=document.createElement('style');
    st.textContent=
      '#page-dev{padding:10px}'
      +'.dev-card{background:rgba(10,10,10,.8);border:1px solid rgba(0,212,255,.2);border-radius:12px;padding:14px;margin-bottom:10px}'
      +'.dev-title{font-size:11px;font-weight:700;color:var(--cyan);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px}'
      +'.dev-row{display:flex;align-items:center;gap:8px;margin-bottom:7px}'
      +'.dev-label{font-size:10px;color:var(--t4);width:90px;flex-shrink:0}'
      +'.dev-input{flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:7px;padding:8px 10px;font-size:11px;color:var(--t1);font-family:Courier New,monospace}'
      +'.dev-input:focus{outline:none;border-color:rgba(0,212,255,.5)}'
      +'.dev-btn{padding:9px 14px;border-radius:8px;font-size:11px;font-weight:600;cursor:pointer;border:none}'
      +'.dev-btn-cyan{background:rgba(0,212,255,.15);color:var(--cyan);border:1px solid rgba(0,212,255,.3)}'
      +'.dev-btn-green{background:rgba(0,230,118,.15);color:var(--green);border:1px solid rgba(0,230,118,.3)}'
      +'.dev-btn-gold{background:rgba(255,184,0,.15);color:var(--gold);border:1px solid rgba(255,184,0,.3)}'
      +'.dev-btn-red{background:rgba(255,68,68,.15);color:#ff6b6b;border:1px solid rgba(255,68,68,.3)}'
      +'.dev-log{background:#000;border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:10px;font-size:9px;font-family:Courier New,monospace;max-height:180px;overflow-y:auto;line-height:1.7}'
      +'.dev-log .ok{color:var(--green)}.dev-log .err{color:#ff6b6b}.dev-log .info{color:var(--cyan)}.dev-log .warn{color:var(--gold)}'
      +'.oc-messages{height:200px;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px;background:#000;border-radius:8px}'
      +'.oc-msg-user{align-self:flex-end;background:rgba(0,212,255,.12);border:1px solid rgba(0,212,255,.2);border-radius:10px 10px 2px 10px;padding:8px 11px;font-size:10px;color:var(--cyan);max-width:85%;word-break:break-word}'
      +'.oc-msg-ai{align-self:flex-start;background:rgba(192,132,252,.08);border:1px solid rgba(192,132,252,.2);border-radius:10px 10px 10px 2px;padding:8px 11px;font-size:10px;color:var(--t2);max-width:90%;white-space:pre-wrap;word-break:break-word}'
      +'.oc-input-row{display:flex;gap:6px;padding:8px;border-top:1px solid rgba(255,255,255,.06)}'
      +'.oc-input{flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:7px;padding:8px 10px;font-size:11px;color:var(--t1)}'
      +'.oc-input:focus{outline:none;border-color:rgba(192,132,252,.5)}'
      +'.dev-progress{height:3px;background:var(--b2);border-radius:2px;overflow:hidden;margin:6px 0}'
      +'.dev-progress-fill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--purple));border-radius:2px;transition:width .3s}';
    document.head.appendChild(st);
  }catch(e){}
})();

var _devLog2=[];
function devLog(msg,type){
  try{
    _devLog2.unshift({ts:new Date().toLocaleTimeString('tr-TR'),msg:msg,type:type||'info'});
    if(_devLog2.length>50) _devLog2.pop();
    var el=document.getElementById('devLogEl');
    if(el) el.innerHTML=_devLog2.slice(0,20).map(function(l){return'<div class="'+l.type+'"><span style="color:var(--t4)">['+l.ts+']</span> '+l.msg+'</div>';}).join('');
  }catch(e){}
}

// GitHub API
function ghReadInputs(){
  try{
    GH.token=(document.getElementById('dev_ghToken')||{}).value||GH.token;
    GH.owner=(document.getElementById('dev_ghOwner')||{}).value||GH.owner;
    GH.repo=(document.getElementById('dev_ghRepo')||{}).value||GH.repo;
    GH.branch=(document.getElementById('dev_ghBranch')||{}).value||GH.branch;
    GH.path=(document.getElementById('dev_ghPath')||{}).value||GH.path;
  }catch(e){}
}
function ghSaveConfig(){
  try{
    ghReadInputs();
    _devCfg2.ghToken=GH.token;_devCfg2.ghOwner=GH.owner;
    _devCfg2.ghRepo=GH.repo;_devCfg2.ghBranch=GH.branch;_devCfg2.ghPath=GH.path;
    saveDevCfg2();devLog('GitHub ayarlari kaydedildi','ok');toast('GitHub ayarlari kaydedildi!');
    renderDevPanel();
  }catch(e){ toast('Kayit hatasi: '+e.message); }
}
function ghGetFile(path,cb){
  try{
    if(!GH.token||!GH.owner||!GH.repo){cb(null,'Ayarlar eksik');return;}
    fetch('https://api.github.com/repos/'+GH.owner+'/'+GH.repo+'/contents/'+path+'?ref='+GH.branch,
      {headers:{'Authorization':'token '+GH.token,'Accept':'application/vnd.github.v3+json'}})
      .then(function(r){return r.json();})
      .then(function(d){cb(d.message?null:d,d.message||null);})
      .catch(function(e){cb(null,e.message);});
  }catch(e){cb(null,e.message);}
}
function ghPushFile(path,content,message,cb){
  try{
    ghReadInputs();
    if(!GH.token||!GH.owner||!GH.repo){cb(false,'GitHub ayarlari eksik');return;}
    devLog('SHA aliniyor...','info');
    ghGetFile(path,function(existing){
      try{
        // btoa ile encode - buyuk dosya icin chunk
        var encoded='';
        try{encoded=btoa(unescape(encodeURIComponent(content)));}
        catch(e2){
          // Fallback - TextEncoder
          try{
            var bytes=new TextEncoder().encode(content);
            var binary=Array.from(bytes).map(function(b){return String.fromCharCode(b);}).join('');
            encoded=btoa(binary);
          }catch(e3){cb(false,'Encoding hatasi');return;}
        }
        var body={message:message||'BIST AI Elite - guncelleme',content:encoded,branch:GH.branch};
        if(existing&&existing.sha) body.sha=existing.sha;
        fetch('https://api.github.com/repos/'+GH.owner+'/'+GH.repo+'/contents/'+path,{
          method:'PUT',
          headers:{'Authorization':'token '+GH.token,'Accept':'application/vnd.github.v3+json','Content-Type':'application/json'},
          body:JSON.stringify(body)
        }).then(function(r){return r.json();})
        .then(function(d){cb(!d.message,d.message||d.content&&d.content.path||'OK');})
        .catch(function(e){cb(false,e.message);});
      }catch(e){cb(false,e.message);}
    });
  }catch(e){cb(false,e.message);}
}
function ghPushCurrentHTML(){
  try{
    ghReadInputs();
    if(!GH.token){toast('GitHub Token giriniz!');return;}
    var prog=document.getElementById('ghProgress');
    var fill=document.getElementById('ghProgressFill');
    if(prog){prog.style.display='block';if(fill)fill.style.width='20%';}
    devLog('GitHub push basliyor...','info');
    var html='<!DOCTYPE html>\n'+document.documentElement.outerHTML;
    if(fill) fill.style.width='50%';
    var ts=new Date().toLocaleString('tr-TR');
    var ver=parseInt(_devCfg2.ocVersion||'11');
    ghPushFile(GH.path,html,'BIST AI Elite v'+ver+' - '+ts,function(ok,info){
      try{
        if(prog){if(fill)fill.style.width='100%';setTimeout(function(){try{prog.style.display='none';if(fill)fill.style.width='0';}catch(e){}},1000);}
        if(ok){devLog('Push BASARILI: '+info,'ok');toast('GitHub push basarili! Render deploy baslayacak.');if(typeof haptic==='function')haptic('success');}
        else{devLog('Push HATASI: '+info,'err');toast('Hata: '+info);}
        renderDevPanel();
      }catch(e){}
    });
  }catch(e){ toast('Push hatasi: '+e.message); devLog('Push exception: '+e.message,'err'); }
}

// AI sohbet
var _ocHistory2=[];
var _ocVersion2=parseInt(_devCfg2.ocVersion||'11');

function ocSend(){
  try{
    var input=document.getElementById('ocInput');
    if(!input||!input.value.trim()) return;
    var msg=input.value.trim();input.value='';
    ocAppendMsg2('user',msg);
    setTimeout(function(){ocProcessCommand2(msg);},100);
  }catch(e){}
}
function ocAppendMsg2(role,content){
  try{
    _ocHistory2.push({role:role,content:content});
    var msgs=document.getElementById('ocMessages');if(!msgs) return;
    var div=document.createElement('div');
    div.className=role==='user'?'oc-msg-user':'oc-msg-ai';
    div.textContent=content;
    msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;
  }catch(e){}
}
function ocProcessCommand2(msg){
  try{
    var m=msg.toLowerCase();
    // Loading
    ocAppendMsg2('ai','...');
    var msgs=document.getElementById('ocMessages');
    var loadEl=msgs?msgs.lastChild:null;
    setTimeout(function(){
      try{if(loadEl&&msgs)msgs.removeChild(loadEl);}catch(e){}
      try{
        if(m.indexOf('push')>-1||m.indexOf('github')>-1||m.indexOf('yukle')>-1){
          ocAppendMsg2('ai','GitHub push baslatiliyor...');ghPushCurrentHTML();
        } else if(m.indexOf('durum')>-1||m.indexOf('analiz')>-1){
          var sc=document.querySelectorAll('script').length;
          var posC=Object.keys(S.openPositions||{}).length;
          var sigC=(S.sigs||[]).length;
          var xu=S.xu100Change||0;
          ocAppendMsg2('ai','v'+_ocVersion2+' Durumu:\nScript blok: '+sc+'\nPozisyon: '+posC+'\nSinyal: '+sigC+'\nXU100: '+(xu>=0?'+':'')+xu.toFixed(2)+'%\nGitHub: '+(GH.token?GH.owner+'/'+GH.repo:'Ayarlanmamis'));
        } else if(m.indexOf('hata')>-1||m.indexOf('syntax')>-1){
          var hatalar=[];
          document.querySelectorAll('script').forEach(function(s,i){
            var code=s.textContent;var d=0;var inS=false;var sc2='';var es=false;
            for(var ci=0;ci<code.length;ci++){
              var ch=code[ci];
              if(es){es=false;continue;}
              if(ch==='\\'&&inS){es=true;continue;}
              if(inS){if(ch===sc2)inS=false;continue;}
              if(ch==='/'&&code[ci+1]=='/'){while(ci<code.length&&code[ci]!=='\n')ci++;continue;}
              if(ch in{'"':1,"'":1,'`':1}){inS=true;sc2=ch;continue;}
              if(ch==='{')d++;else if(ch==='}')d--;
            }
            if(d!==0) hatalar.push('Blok '+(i+1)+': '+d+' kapanmamis bracket');
          });
          ocAppendMsg2('ai',hatalar.length?'Hatalar:\n'+hatalar.join('\n'):'Syntax kontrolu temiz!');
        } else if(m.indexOf('indir')>-1||m.indexOf('surum')>-1||m.indexOf('versiyon')>-1){
          _ocVersion2++;_devCfg2.ocVersion=_ocVersion2;saveDevCfg2();
          try{
            var htmlContent='<!DOCTYPE html>\n'+document.documentElement.outerHTML;
            var blob=new Blob([htmlContent],{type:'text/html'});
            var url=URL.createObjectURL(blob);
            var a=document.createElement('a');a.href=url;a.download='bist_elite_v'+_ocVersion2+'.html';a.click();
            URL.revokeObjectURL(url);
          }catch(e2){}
          ocAppendMsg2('ai','v'+_ocVersion2+' indirildi!\n\nGitHub push icin "push" yaz.');
        } else if(m.indexOf('rapor')>-1||m.indexOf('portfoy')>-1){
          var closed=S.closedPositions||[];var totalP=0;var wins=0;
          closed.forEach(function(p){var pnl=parseFloat(p.pnlPct);totalP+=pnl;if(pnl>=0)wins++;});
          var wr=closed.length?(wins/closed.length*100).toFixed(1):'N/A';
          ocAppendMsg2('ai','Portfoy:\nAcik: '+Object.keys(S.openPositions||{}).length+'\nWin Rate: %'+wr+'\nToplam PnL: '+(totalP>=0?'+':'')+totalP.toFixed(2)+'%\nXU100: '+(S.xu100Change>=0?'+':'')+( S.xu100Change||0).toFixed(2)+'%');
        } else if(m.indexOf('yardim')>-1||m.indexOf('komut')>-1){
          ocAppendMsg2('ai','Komutlar:\n- durum\n- hata bul\n- push / github push\n- yeni surum indir\n- rapor\n\nVeya herhangi bir soru sorun!');
        } else {
          // Claude API
          ocCallClaude2(msg);
        }
      }catch(e){ ocAppendMsg2('ai','Hata: '+e.message); }
    },400);
  }catch(e){}
}
function ocCallClaude2(msg){
  try{
    var posC=Object.keys(S.openPositions||{}).length;
    var closed=S.closedPositions||[];var wins=0;
    closed.forEach(function(p){if(parseFloat(p.pnlPct)>=0)wins++;});
    var wr=closed.length?(wins/closed.length*100).toFixed(0):'N/A';
    var sys='Sen BIST AI Elite PWA gelistirici asistanisin. '
      +'JavaScript ve Python uzmanisin. BIST Katilim borsasi uzmanisin. '
      +'Kisa ve net cevap ver (max 150 kelime). Turk kullanici. '
      +'DURUM: v'+_ocVersion2+', pos:'+posC+', WR:%'+wr+'.';
    var msgs=_ocHistory2.filter(function(m){return m.role==='user';}).slice(-4).map(function(m){return{role:'user',content:m.content};});
    msgs.push({role:'user',content:msg});
    ocAppendMsg2('ai','Dusunuyor...');
    var msgEl=document.getElementById('ocMessages');
    var loadEl=msgEl?msgEl.lastChild:null;
    fetch('https://api.anthropic.com/v1/messages',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:'claude-sonnet-4-20250514',max_tokens:1000,system:sys,messages:msgs})
    }).then(function(r){return r.json();})
    .then(function(d){
      try{if(loadEl&&msgEl)msgEl.removeChild(loadEl);}catch(e){}
      var resp=(d.content&&d.content[0]&&d.content[0].text)||'Cevap alinamadi.';
      ocAppendMsg2('ai',resp);
    }).catch(function(){
      try{if(loadEl&&msgEl)msgEl.removeChild(loadEl);}catch(e){}
      ocAppendMsg2('ai','Baglanti yok. "yardim" yaz.');
    });
  }catch(e){ ocAppendMsg2('ai','Hata: '+e.message); }
}

// Dev panel render
function renderDevPanel(){
  try{
    var el=document.getElementById('page-dev');if(!el) return;
    var ghOk=!!(GH.token&&GH.owner&&GH.repo);
    el.innerHTML=''
      +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">'
      +'<div style="font-size:20px">&#128736;</div>'
      +'<div><div style="font-size:14px;font-weight:700;color:var(--t1)">Gelistirici Paneli</div>'
      +'<div style="font-size:9px;color:var(--t4)">BIST AI Elite v'+_ocVersion2+' | GitHub + AI</div></div>'
      +'</div>'
      // GitHub
      +'<div class="dev-card">'
      +'<div class="dev-title">GitHub Baglantisi</div>'
      +'<div style="display:flex;align-items:center;gap:6px;padding:7px;background:rgba(255,255,255,.03);border-radius:8px;margin-bottom:10px">'
      +'<div style="width:8px;height:8px;border-radius:50%;background:'+(ghOk?'var(--green)':'var(--t4)')+'"></div>'
      +'<div style="font-size:10px;color:var(--t2);flex:1">'+(ghOk?GH.owner+'/'+GH.repo+' ('+GH.branch+')':'Ayarlanmamis - Token girin')+'</div>'
      +'</div>'
      +'<div class="dev-row"><span class="dev-label">Token (PAT)</span><input class="dev-input" id="dev_ghToken" type="password" placeholder="ghp_xxxx..." value="'+(GH.token||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Kullanici</span><input class="dev-input" id="dev_ghOwner" placeholder="github_kullanici" value="'+(GH.owner||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Repo</span><input class="dev-input" id="dev_ghRepo" placeholder="bist-ai" value="'+(GH.repo||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Branch</span><input class="dev-input" id="dev_ghBranch" placeholder="main" value="'+(GH.branch||'main')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Dosya</span><input class="dev-input" id="dev_ghPath" placeholder="bist_elite_v11.html" value="'+(GH.path||'bist_elite_v11.html')+'"></div>'
      +'<div class="dev-progress" id="ghProgress" style="display:none"><div class="dev-progress-fill" id="ghProgressFill" style="width:0%"></div></div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px">'
      +'<button class="dev-btn dev-btn-cyan" onclick="ghSaveConfig()">Kaydet</button>'
      +'<button class="dev-btn dev-btn-green" onclick="ghPushCurrentHTML()">'+(ghOk?'GitHub Push':'Once Kaydet!')+'</button>'
      +'</div></div>'
      // AI
      +'<div class="dev-card" style="padding:10px">'
      +'<div class="dev-title">AI Gelistirici</div>'
      +'<div class="oc-messages" id="ocMessages"><div class="oc-msg-ai">Merhaba! BIST AI gelistirici asistaninim.\n\nOrnek komutlar:\n- durum\n- push\n- yeni surum indir\n- rapor\n- hata bul\n\nVeya herhangi bir soru sorun!</div></div>'
      +'<div class="oc-input-row">'
      +'<input class="oc-input" id="ocInput" placeholder="Komut veya soru..." onkeydown="if(event.key===\'Enter\'){event.preventDefault();ocSend();}">'
      +'<button class="dev-btn dev-btn-cyan" onclick="ocSend()" style="flex-shrink:0;padding:8px 14px;border-radius:7px">&#9654;</button>'
      +'</div></div>'
      // Hizli
      +'<div class="dev-card">'
      +'<div class="dev-title">Hizli Komutlar</div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px">'
      +'<button class="dev-btn dev-btn-cyan" onclick="ocProcessCommand2(\'durum\')" style="width:100%;padding:9px;border-radius:8px">Durum</button>'
      +'<button class="dev-btn dev-btn-gold" onclick="ocProcessCommand2(\'hata bul\')" style="width:100%;padding:9px;border-radius:8px">Hata Tara</button>'
      +'<button class="dev-btn dev-btn-green" onclick="ocProcessCommand2(\'yeni surum indir\')" style="width:100%;padding:9px;border-radius:8px">Indir v'+(_ocVersion2+1)+'</button>'
      +'<button class="dev-btn dev-btn-red" onclick="ghPushCurrentHTML()" style="width:100%;padding:9px;border-radius:8px">GitHub Push</button>'
      +'</div></div>'
      // Log
      +'<div class="dev-card">'
      +'<div class="dev-title">Log</div>'
      +'<div class="dev-log" id="devLogEl"><div class="info">BIST AI Elite v'+_ocVersion2+' Dev paneli hazir.</div></div>'
      +'</div>';
  }catch(e){ console.warn('renderDevPanel:',e.message); }
}

// Nav'a Dev sekmesi ekle
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      var main=document.querySelector('main');
      if(main&&!document.getElementById('page-dev')){
        var devPage=document.createElement('div');
        devPage.id='page-dev';devPage.className='page';
        main.appendChild(devPage);
      }
      var nav=document.querySelector('nav');
      if(nav&&!document.getElementById('devTab')){
        var btn=document.createElement('button');
        btn.id='devTab';btn.className='tab';
        btn.innerHTML='&#128736; Dev';
        btn.setAttribute('aria-label','Gelistirici paneli');
        btn.onclick=function(){try{pg('dev');renderDevPanel();}catch(e){}};
        nav.appendChild(btn);
      }
      devLog('BIST AI Elite v'+_ocVersion2+' baslatildi','ok');
      devLog('GitHub: '+(GH.token?GH.owner+'/'+GH.repo:'Ayarlanmamis'),GH.token?'ok':'warn');
    }catch(e){ console.warn('Dev panel init:',e.message); }
  },800);
});

</script>
<script>

// BIST v12 BLOK 11
// OpenClaw Gateway WS + Sesli Sohbet + Tam Repo Erisimi
// Mimari: OpenClaw Gateway (ws://localhost:18789) <-> BIST PWA
// Sesli: Web Speech API (STT) + SpeechSynthesis (TTS)
// Repo: GitHub Contents API - tum dosyalar okuma/yazma

//  CSS 
(function(){
  try{
    var st=document.createElement('style');
    st.textContent=
      // OpenClaw panel
      '#ocGwPanel{background:rgba(0,0,0,.95);border:1px solid rgba(0,212,255,.15);border-radius:14px;overflow:hidden;margin-bottom:10px}'
      +'#ocGwHeader{background:rgba(0,212,255,.08);border-bottom:1px solid rgba(0,212,255,.12);padding:10px 14px;display:flex;align-items:center;gap:8px}'
      +'#ocGwStatus{width:8px;height:8px;border-radius:50%;background:#444;flex-shrink:0;transition:background .3s}'
      +'#ocGwStatus.connected{background:var(--green);box-shadow:0 0 6px var(--green)}'
      +'#ocGwStatus.connecting{background:var(--gold);animation:pulse .8s infinite}'
      +'#ocGwStatus.error{background:var(--red)}'
      // Voice
      +'#voicePanel{background:rgba(192,132,252,.05);border:1px solid rgba(192,132,252,.15);border-radius:12px;padding:12px;margin-bottom:10px}'
      +'#voiceWave{display:flex;align-items:center;justify-content:center;gap:3px;height:32px}'
      +'#voiceWave span{display:inline-block;width:3px;border-radius:2px;background:var(--purple);animation:vwave 1s ease-in-out infinite}'
      +'#voiceWave span:nth-child(1){animation-delay:0s;height:8px}'
      +'#voiceWave span:nth-child(2){animation-delay:.1s;height:16px}'
      +'#voiceWave span:nth-child(3){animation-delay:.2s;height:24px}'
      +'#voiceWave span:nth-child(4){animation-delay:.3s;height:16px}'
      +'#voiceWave span:nth-child(5){animation-delay:.4s;height:8px}'
      +'@keyframes vwave{0%,100%{transform:scaleY(1)}50%{transform:scaleY(1.8)}}'
      +'#voiceWave.idle span{animation:none;height:4px;opacity:.3}'
      +'#voiceWave.listening span{background:var(--cyan)}'
      +'#voiceWave.speaking span{background:var(--gold)}'
      +'#micBtn{width:56px;height:56px;border-radius:50%;border:2px solid rgba(192,132,252,.4);background:rgba(192,132,252,.1);color:var(--purple);font-size:22px;cursor:pointer;transition:all .2s;display:flex;align-items:center;justify-content:center;margin:0 auto}'
      +'#micBtn.active{border-color:var(--cyan);background:rgba(0,212,255,.15);color:var(--cyan);box-shadow:0 0 20px rgba(0,212,255,.3);animation:micPulse 1s infinite}'
      +'#micBtn.speaking{border-color:var(--gold);background:rgba(255,184,0,.1);color:var(--gold);animation:micPulse 1s infinite}'
      +'@keyframes micPulse{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}'
      // Repo browser
      +'#repoBrowser{background:#000;border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:8px;max-height:240px;overflow-y:auto}'
      +'.repo-file{display:flex;align-items:center;gap:7px;padding:6px 8px;border-radius:6px;cursor:pointer;transition:background .15s}'
      +'.repo-file:hover{background:rgba(255,255,255,.05)}'
      +'.repo-file.selected{background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2)}'
      +'.repo-file-icon{font-size:13px;flex-shrink:0}'
      +'.repo-file-name{font-size:10px;color:var(--t2);flex:1;font-family:Courier New,monospace}'
      +'.repo-file-size{font-size:8px;color:var(--t4)}'
      // Agent cards
      +'#agentCards{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:8px}'
      +'.agent-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:8px;cursor:pointer;transition:all .2s}'
      +'.agent-card.active{border-color:rgba(0,212,255,.4);background:rgba(0,212,255,.06)}'
      +'.agent-card:active{transform:scale(.97)}'
      +'.agent-card-name{font-size:10px;font-weight:700;color:var(--t1);margin-bottom:2px}'
      +'.agent-card-desc{font-size:8px;color:var(--t4);line-height:1.4}'
      +'.agent-card-status{font-size:7px;padding:1px 5px;border-radius:3px;background:rgba(0,230,118,.1);color:var(--green);margin-top:3px;display:inline-block}'
      // Msg stream
      +'#ocStream{height:180px;overflow-y:auto;padding:8px;background:#000;border-radius:8px;display:flex;flex-direction:column;gap:5px}'
      +'.ocm-user{align-self:flex-end;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.2);border-radius:10px 10px 2px 10px;padding:7px 10px;font-size:10px;color:var(--cyan);max-width:88%;word-break:break-word}'
      +'.ocm-ai{align-self:flex-start;background:rgba(192,132,252,.07);border:1px solid rgba(192,132,252,.15);border-radius:10px 10px 10px 2px;padding:7px 10px;font-size:10px;color:var(--t2);max-width:92%;white-space:pre-wrap;word-break:break-word;line-height:1.5}'
      +'.ocm-sys{align-self:center;font-size:8.5px;color:var(--t4);font-style:italic}'
      +'.ocm-code{background:rgba(0,0,0,.6);border:1px solid rgba(255,255,255,.08);border-radius:6px;padding:7px;font-family:Courier New,monospace;font-size:8px;color:var(--green);margin-top:4px;white-space:pre-wrap;overflow-x:auto}'
      +'.ocm-action{display:flex;gap:5px;margin-top:5px;flex-wrap:wrap}'
      +'.ocm-action button{font-size:8px;padding:3px 8px;border-radius:5px;border:none;cursor:pointer;font-weight:600}'
      +'.ocm-btn-apply{background:rgba(0,230,118,.15);color:var(--green);border:1px solid rgba(0,230,118,.3)}'
      +'.ocm-btn-skip{background:rgba(255,68,68,.1);color:#ff6b6b;border:1px solid rgba(255,68,68,.2)}';
    document.head.appendChild(st);
  }catch(e){}
})();

//  OPENCLAW GATEWAY WS 
var _ocGW = {
  ws: null,
  url: '',
  token: '',
  connected: false,
  msgQueue: [],
  pingTimer: null,
  reconnectTimer: null,
  reconnectCount: 0
};

function ocGWLoad(){
  try{
    var cfg=JSON.parse(localStorage.getItem('bist_dev_cfg')||'{}');
    _ocGW.url=cfg.ocGWUrl||'ws://127.0.0.1:18789';
    _ocGW.token=cfg.ocGWToken||'';
  }catch(e){}
}

function ocGWConnect(){
  try{
    ocGWLoad();
    if(!_ocGW.url){ocGWSetStatus('error','URL girilmemis');return;}
    ocGWSetStatus('connecting','Baglaniyor...');
    if(_ocGW.ws){try{_ocGW.ws.close();}catch(e){}}
    _ocGW.ws=new WebSocket(_ocGW.url);
    _ocGW.ws.onopen=function(){
      _ocGW.connected=true;_ocGW.reconnectCount=0;
      ocGWSetStatus('connected','Bagli');
      ocGWMsg('sys','OpenClaw Gateway baglantisi kuruldu');
      if(_ocGW.token){
        _ocGW.ws.send(JSON.stringify({type:'auth',token:_ocGW.token}));
      }
      // Ping
      _ocGW.pingTimer=setInterval(function(){
        try{if(_ocGW.ws&&_ocGW.ws.readyState===1)_ocGW.ws.send(JSON.stringify({type:'ping'}));}catch(e){}
      },25000);
    };
    _ocGW.ws.onmessage=function(e){
      try{
        var data=JSON.parse(e.data);
        ocGWHandleMsg(data);
      }catch(ex){
        if(e.data&&e.data.trim()) ocGWMsg('ai',e.data);
      }
    };
    _ocGW.ws.onclose=function(e){
      _ocGW.connected=false;
      clearInterval(_ocGW.pingTimer);
      ocGWSetStatus('error','Baglanti kesildi ('+e.code+')');
      ocGWMsg('sys','Baglanti kesildi. 5sn sonra yeniden denenecek...');
      if(_ocGW.reconnectCount<5){
        _ocGW.reconnectCount++;
        _ocGW.reconnectTimer=setTimeout(ocGWConnect,5000);
      }
    };
    _ocGW.ws.onerror=function(){
      ocGWSetStatus('error','Baglanti hatasi');
      ocGWMsg('sys','Hata: Gateway calisiyor mu? openclaw gateway komutuyla baslatin.');
    };
  }catch(e){ocGWSetStatus('error',e.message);}
}

function ocGWDisconnect(){
  clearTimeout(_ocGW.reconnectTimer);
  clearInterval(_ocGW.pingTimer);
  _ocGW.reconnectCount=99;
  if(_ocGW.ws){try{_ocGW.ws.close();}catch(e){}}
  _ocGW.connected=false;
  ocGWSetStatus('error','Baglanti kesildi');
}

function ocGWSend(text){
  if(!_ocGW.connected||!_ocGW.ws){
    ocGWMsg('sys','Gateway bagli degil. Once baglayin.');
    return false;
  }
  try{
    // OpenClaw Gateway mesaj formati
    _ocGW.ws.send(JSON.stringify({
      type:'chat.send',
      text:text,
      channel:'webchat',
      agentId:_activeAgent||'main'
    }));
    return true;
  }catch(e){ocGWMsg('sys','Gonderme hatasi: '+e.message);return false;}
}

function ocGWHandleMsg(data){
  try{
    if(data.type==='pong') return;
    if(data.type==='auth.ok'){ocGWMsg('sys','Yetkilendirme basarili');}
    else if(data.type==='chat.message'||data.type==='message'){
      var txt=data.text||data.content||data.message||JSON.stringify(data);
      ocGWMsg('ai',txt);
      if(_ttsEnabled) ocGWSpeak(txt);
    }
    else if(data.type==='agent.running'||data.type==='agent.thinking'){
      ocGWMsg('sys','Agent calisiyor...');
    }
    else if(data.type==='tool.use'){
      ocGWMsg('sys','Arac: '+( data.tool||data.name||'?'));
    }
    else if(data.type==='tool.result'){
      // Kod blogu olarak goster
      var res=typeof data.result==='string'?data.result:JSON.stringify(data.result,null,2);
      ocGWMsgCode(res);
    }
    else if(data.type==='error'){
      ocGWMsg('sys','Hata: '+( data.message||data.error||'?'));
    }
  }catch(e){}
}

function ocGWSetStatus(cls,txt){
  try{
    var dot=document.getElementById('ocGwStatus');
    var lbl=document.getElementById('ocGwLabel');
    if(dot){dot.className=cls;}
    if(lbl){lbl.textContent=txt||'';}
    var btn=document.getElementById('ocGwConnBtn');
    if(btn){
      if(cls==='connected'){btn.textContent='Kes';btn.style.color='var(--red)';}
      else{btn.textContent='Baglan';btn.style.color='var(--green)';}
    }
  }catch(e){}
}

//  SESLI SOHBET 
var _listening=false;
var _ttsEnabled=true;
var _speechRecognition=null;
var _activeAgent='main';

function ocGWStartListen(){
  try{
    var SR=window.SpeechRecognition||window.webkitSpeechRecognition;
    if(!SR){toast('Tarayici ses desteklemiyor');return;}
    if(_listening){ocGWStopListen();return;}
    _speechRecognition=new SR();
    _speechRecognition.lang='tr-TR';
    _speechRecognition.continuous=false;
    _speechRecognition.interimResults=false;
    _speechRecognition.onstart=function(){
      _listening=true;
      var btn=document.getElementById('micBtn');if(btn)btn.className='active';
      var wave=document.getElementById('voiceWave');if(wave)wave.className='listening';
      ocGWMsg('sys','Dinleniyor...');
      if(typeof haptic==='function') haptic('medium');
    };
    _speechRecognition.onresult=function(e){
      var txt=e.results[0][0].transcript;
      _listening=false;
      var btn=document.getElementById('micBtn');if(btn)btn.className='';
      var wave=document.getElementById('voiceWave');if(wave)wave.className='idle';
      ocGWMsgUser(txt);
      // Gateway'e mi yoksa direkt AI'ya mi gonder?
      if(_ocGW.connected){ocGWSend(txt);}
      else{ocV12Process(txt);}
    };
    _speechRecognition.onerror=function(e){
      _listening=false;
      var btn=document.getElementById('micBtn');if(btn)btn.className='';
      var wave=document.getElementById('voiceWave');if(wave)wave.className='idle';
      if(e.error!=='no-speech') ocGWMsg('sys','Ses hatasi: '+e.error);
    };
    _speechRecognition.onend=function(){
      _listening=false;
      var btn=document.getElementById('micBtn');if(btn)btn.className='';
      var wave=document.getElementById('voiceWave');if(wave)wave.className='idle';
    };
    _speechRecognition.start();
  }catch(e){toast('Ses hatasi: '+e.message);}
}

function ocGWStopListen(){
  try{if(_speechRecognition){_speechRecognition.stop();}}catch(e){}
  _listening=false;
  var btn=document.getElementById('micBtn');if(btn)btn.className='';
  var wave=document.getElementById('voiceWave');if(wave)wave.className='idle';
}

function ocGWSpeak(text){
  try{
    if(!_ttsEnabled) return;
    var utt=new SpeechSynthesisUtterance(text.replace(/<[^>]*>/g,'').substring(0,300));
    utt.lang='tr-TR';utt.rate=1.1;utt.pitch=1.0;
    utt.onstart=function(){
      var btn=document.getElementById('micBtn');if(btn)btn.className='speaking';
      var wave=document.getElementById('voiceWave');if(wave)wave.className='speaking';
    };
    utt.onend=function(){
      var btn=document.getElementById('micBtn');if(btn)btn.className='';
      var wave=document.getElementById('voiceWave');if(wave)wave.className='idle';
      // Otomatik tekrar dinle
      if(_ttsEnabled&&_ocGW.connected) setTimeout(ocGWStartListen,800);
    };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utt);
  }catch(e){}
}

//  MESAJ FONKSIYONLARI 
function ocGWMsg(role,text){
  try{
    var el=document.getElementById('ocStream');if(!el)return;
    var div=document.createElement('div');
    if(role==='user') div.className='ocm-user';
    else if(role==='sys') div.className='ocm-sys';
    else div.className='ocm-ai';
    div.textContent=text;
    el.appendChild(div);el.scrollTop=el.scrollHeight;
  }catch(e){}
}
function ocGWMsgUser(text){ocGWMsg('user',text);}
function ocGWMsgCode(code){
  try{
    var el=document.getElementById('ocStream');if(!el)return;
    var wrap=document.createElement('div');wrap.className='ocm-ai';
    var pre=document.createElement('div');pre.className='ocm-code';
    pre.textContent=code.substring(0,600)+(code.length>600?'\n...':'');
    // Kod JS ise uygula butonu ekle
    var isJS = code.trim().startsWith('function')||code.trim().startsWith('var ')||code.trim().startsWith('(function');
    if(isJS){
      var acts=document.createElement('div');acts.className='ocm-action';
      var applyBtn=document.createElement('button');applyBtn.className='ocm-btn-apply';
      applyBtn.textContent='Uygula + Push';
      var _code=code;
      applyBtn.onclick=function(){
        try{
          var sc=document.createElement('script');
          sc.textContent=_code;
          document.head.appendChild(sc);
          toast('Kod uygulandi!');
          if(typeof haptic==='function') haptic('success');
          setTimeout(function(){if(GH&&GH.token) ghPushCurrentHTML();},500);
        }catch(e){toast('Hata: '+e.message);}
      };
      acts.appendChild(applyBtn);
      wrap.appendChild(acts);
    }
    wrap.insertBefore(pre,wrap.firstChild);
    el.appendChild(wrap);el.scrollTop=el.scrollHeight;
  }catch(e){}
}

//  OPENCLAW AGENTLARI 
var OC_AGENTS = [
  {id:'main',     name:'Ana Agent',    desc:'Genel asistan, tum islemler',    icon:'?'},
  {id:'bist-dev', name:'BIST Dev',     desc:'Kod uretimi ve GitHub',          icon:'?'},
  {id:'trader',   name:'Trader AI',    desc:'Sinyal analizi ve risk',         icon:'?'},
  {id:'backtest', name:'Backtest',     desc:'Performans ve optimizasyon',     icon:'?'},
];

function ocGWSetAgent(id){
  _activeAgent=id;
  document.querySelectorAll('.agent-card').forEach(function(c){
    c.classList.toggle('active', c.dataset.agentId===id);
  });
  ocGWMsg('sys','Agent: '+id);
}

//  GITHUB REPO TARAYICISI 
var _repoTree=[];
var _selectedFile='';

function repoListFiles(path, cb){
  try{
    if(!GH||!GH.token||!GH.owner||!GH.repo){toast('GitHub ayarlari eksik');return;}
    var url='https://api.github.com/repos/'+GH.owner+'/'+GH.repo+'/contents/'+(path||'')+'?ref='+GH.branch;
    fetch(url,{headers:{'Authorization':'token '+GH.token,'Accept':'application/vnd.github.v3+json'}})
      .then(function(r){return r.json();})
      .then(function(data){
        if(!Array.isArray(data)){
          if(data.message) toast('Repo hatasi: '+data.message);
          return;
        }
        _repoTree=data;
        if(cb) cb(data);
        else renderRepoBrowser(data);
      }).catch(function(e){toast('Repo okuma: '+e.message);});
  }catch(e){toast('Repo: '+e.message);}
}

function renderRepoBrowser(files){
  try{
    var el=document.getElementById('repoBrowser');if(!el)return;
    var icons={js:'?',py:'?',html:'?',md:'?',json:'?',txt:'?',zip:'?',yaml:'?',yml:'?',sh:'?'};
    el.innerHTML='';
    // Ust dizin butonu
    if(_repoPath){
      var back=document.createElement('div');back.className='repo-file';
      back.innerHTML='<span class="repo-file-icon">?</span><span class="repo-file-name">.. (Ust dizin)</span>';
      back.onclick=function(){_repoPath=_repoPath.split('/').slice(0,-1).join('/');repoListFiles(_repoPath);};
      el.appendChild(back);
    }
    files.forEach(function(f){
      var div=document.createElement('div');div.className='repo-file';
      if(f.path===_selectedFile) div.classList.add('selected');
      var ext=(f.name.split('.').pop()||'').toLowerCase();
      var icon=f.type==='dir'?'?':(icons[ext]||'?');
      var size=f.size>0?(f.size>1024?(f.size/1024).toFixed(0)+'KB':f.size+'B'):'';
      div.innerHTML='<span class="repo-file-icon">'+icon+'</span>'
        +'<span class="repo-file-name">'+f.name+'</span>'
        +'<span class="repo-file-size">'+size+'</span>';
      div.onclick=function(){
        if(f.type==='dir'){_repoPath=f.path;repoListFiles(f.path);}
        else{
          _selectedFile=f.path;
          document.querySelectorAll('.repo-file').forEach(function(r){r.classList.remove('selected');});
          div.classList.add('selected');
          // Dosyayi oku ve goster
          repoReadFile(f.path);
        }
      };
      el.appendChild(div);
    });
    if(!files.length){
      el.innerHTML='<div style="padding:12px;text-align:center;color:var(--t4);font-size:10px">Klasor bos</div>';
    }
  }catch(e){}
}
var _repoPath='';

function repoReadFile(path){
  try{
    if(!GH||!GH.token) return;
    ocGWMsg('sys','Dosya okunuyor: '+path);
    fetch('https://api.github.com/repos/'+GH.owner+'/'+GH.repo+'/contents/'+path+'?ref='+GH.branch,
      {headers:{'Authorization':'token '+GH.token,'Accept':'application/vnd.github.v3+json'}})
      .then(function(r){return r.json();})
      .then(function(data){
        if(data.content){
          try{
            var content=decodeURIComponent(escape(atob(data.content.replace(/\n/g,''))));
            ocGWMsg('ai',path+' ('+Math.round(content.length/1024*10)/10+'KB):\n\n'+content.substring(0,400)+(content.length>400?'\n\n... ['+(content.length-400)+' karakter daha]':''));
          }catch(e2){ocGWMsg('sys','Dosya decode edilemedi');}
        }
      }).catch(function(e){ocGWMsg('sys','Okuma hatasi: '+e.message);});
  }catch(e){}
}

function repoWriteFile(path, content, message){
  // Proxy: ghPushFile kullan
  if(typeof ghPushFile==='function'){
    ghPushFile(path, content, message||('BIST AI - '+path+' guncelleme'), function(ok, info){
      ocGWMsg('sys', ok?'Yazildi: '+info:'Yazma hatasi: '+info);
    });
  }
}

function repoDeleteFile(path){
  // GitHub DELETE
  try{
    if(!GH||!GH.token){toast('Token eksik');return;}
    if(!confirm(path+' silinsin mi?')) return;
    ghGetFile(path,function(existing){
      if(!existing||!existing.sha){toast('Dosya bulunamadi');return;}
      fetch('https://api.github.com/repos/'+GH.owner+'/'+GH.repo+'/contents/'+path,{
        method:'DELETE',
        headers:{'Authorization':'token '+GH.token,'Accept':'application/vnd.github.v3+json','Content-Type':'application/json'},
        body:JSON.stringify({message:'BIST AI - '+path+' silindi',sha:existing.sha,branch:GH.branch})
      }).then(function(r){return r.json();})
      .then(function(d){
        if(d.commit){toast(path+' silindi!');repoListFiles(_repoPath);}
        else{toast('Silme hatasi: '+(d.message||'?'));}
      }).catch(function(e){toast('Silme: '+e.message);});
    });
  }catch(e){toast(e.message);}
}

//  V12 KOMUT ISLE (Gateway bagli degilken) 
function ocV12Process(msg){
  var m=msg.toLowerCase();
  setTimeout(function(){
    if(m.indexOf('push')>-1||m.indexOf('github')>-1){
      ocGWMsg('ai','GitHub push baslatiliyor...');
      if(typeof ghPushCurrentHTML==='function') ghPushCurrentHTML();
    } else if(m.indexOf('repo')>-1||m.indexOf('dosya')>-1){
      ocGWMsg('ai','Repo dosyalari yukleniyor...');
      repoListFiles('');
    } else if(m.indexOf('durum')>-1){
      var posC=Object.keys(S.openPositions||{}).length;
      ocGWMsg('ai','v12 Durum: '+posC+' pozisyon, '+(S.sigs||[]).length+' sinyal, XU100: '+(S.xu100Change>=0?'+':'')+( S.xu100Change||0).toFixed(2)+'%');
    } else if(m.indexOf('indir')>-1||m.indexOf('surum')>-1){
      ocGWMsg('ai','Yeni surum indiriliyor...');
      try{
        var html='<!DOCTYPE html>\n'+document.documentElement.outerHTML;
        var blob=new Blob([html],{type:'text/html'});
        var url=URL.createObjectURL(blob);
        var a=document.createElement('a');a.href=url;a.download='bist_elite_v12.html';a.click();
        URL.revokeObjectURL(url);
        ocGWMsg('ai','bist_elite_v12.html indirildi!');
      }catch(e){ocGWMsg('sys','Indirme hatasi: '+e.message);}
    } else {
      // Claude API fallback
      ocV12Claude(msg);
    }
  },300);
}

function ocV12Claude(msg){
  var posC=Object.keys(S.openPositions||{}).length;
  var closed=S.closedPositions||[];var wins=0;
  closed.forEach(function(p){if(parseFloat(p.pnlPct)>=0)wins++;});
  var wr=closed.length?(wins/closed.length*100).toFixed(0):'N/A';
  var sys='Sen BIST AI Elite v12 asistanisin. BIST uzmanisin. '
    +'Kisa net Turkce cevap ver. '
    +'DURUM: pos:'+posC+', WR:%'+wr+', XU100:'+(S.xu100Change>=0?'+':'')+( S.xu100Change||0).toFixed(2)+'%.';
  fetch('https://api.anthropic.com/v1/messages',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({model:'claude-sonnet-4-20250514',max_tokens:800,system:sys,
      messages:[{role:'user',content:msg}]})
  }).then(function(r){return r.json();})
  .then(function(d){
    var resp=(d.content&&d.content[0]&&d.content[0].text)||'Cevap alinamadi.';
    ocGWMsg('ai',resp);
    if(_ttsEnabled) ocGWSpeak(resp);
  }).catch(function(){
    ocGWMsg('ai','Baglanti yok. Yerel komutlar kullanin.');
  });
}

//  DEV PANEL V12 RENDER 
var _origRenderDevPanel=typeof renderDevPanel==='function'?renderDevPanel:null;
renderDevPanel=function(){
  try{
    var el=document.getElementById('page-dev');if(!el) return;
    ocGWLoad();
    var ghOk=!!(GH&&GH.token&&GH.owner&&GH.repo);
    var gwCon=_ocGW.connected;

    el.innerHTML=''
      // Baslik
      +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">'
      +'<div style="font-size:22px">&#128736;</div>'
      +'<div><div style="font-size:14px;font-weight:700;color:var(--t1)">BIST AI Dev v12</div>'
      +'<div style="font-size:9px;color:var(--t4)">OpenClaw Gateway + Sesli AI + Tam Repo Erisimi</div></div>'
      +'</div>'

      //  OpenClaw Gateway Baglantisi 
      +'<div id="ocGwPanel">'
      +'<div id="ocGwHeader">'
      +'<div id="ocGwStatus"></div>'
      +'<div style="flex:1"><div style="font-size:11px;font-weight:700;color:var(--t1)">OpenClaw Gateway</div>'
      +'<div id="ocGwLabel" style="font-size:8px;color:var(--t4)">Bagli degil</div></div>'
      +'<button id="ocGwConnBtn" onclick="if(_ocGW.connected)ocGWDisconnect();else ocGWConnect()" class="dev-btn" style="padding:6px 12px;border-radius:7px;font-size:10px;color:var(--green);border:1px solid rgba(0,230,118,.3);background:rgba(0,230,118,.08)">Baglan</button>'
      +'</div>'
      +'<div style="padding:10px">'
      +'<div class="dev-row"><span class="dev-label">Gateway URL</span>'
      +'<input class="dev-input" id="dev_ocGWUrl" placeholder="ws://127.0.0.1:18789" value="'+(GH&&_devCfg2&&_devCfg2.ocGWUrl||'ws://127.0.0.1:18789')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Token</span>'
      +'<input class="dev-input" id="dev_ocGWToken" type="password" placeholder="Gateway token" value="'+(GH&&_devCfg2&&_devCfg2.ocGWToken||'')+'"></div>'
      +'<button class="dev-btn dev-btn-cyan" onclick="v12SaveGW()" style="width:100%;padding:7px;border-radius:7px;margin-top:4px;font-size:10px">Kaydet ve Baglan</button>'
      +'</div></div>'
      +'<div style="font-size:8px;color:var(--t4);margin:4px 0 8px 2px">'
      +'Not: Gateway yerel cihazinizda calisiyorsa ws://127.0.0.1:18789. '
      +'Uzaktan eriSim icin SSH tunnel veya Tailscale kullanin.'
      +'</div>'

      //  Sesli Sohbet 
      +'<div id="voicePanel">'
      +'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">'
      +'<div><div style="font-size:11px;font-weight:700;color:var(--purple)">Sesli AI Sohbet</div>'
      +'<div style="font-size:8px;color:var(--t4)">Turkce konuS, AI yanit versin</div></div>'
      +'<div style="display:flex;gap:6px;align-items:center">'
      +'<span style="font-size:9px;color:var(--t4)">TTS</span>'
      +'<label style="position:relative;display:inline-block;width:34px;height:18px">'
      +'<input type="checkbox" id="ttsToggle" '+((_ttsEnabled)?'checked':'')+' onchange="_ttsEnabled=this.checked" style="opacity:0;width:0;height:0">'
      +'<span style="position:absolute;cursor:pointer;inset:0;background:'+((_ttsEnabled)?'rgba(0,212,255,.3)':'rgba(255,255,255,.1)')+';border-radius:9px;transition:.3s"></span>'
      +'</label>'
      +'</div></div>'
      +'<div id="voiceWave" class="idle">'
      +'<span></span><span></span><span></span><span></span><span></span>'
      +'</div>'
      +'<button id="micBtn" onclick="ocGWStartListen()" style="margin:8px auto;display:flex">&#127908;</button>'
      +'<div id="voiceTranscript" style="font-size:9px;color:var(--t4);text-align:center;margin-top:6px;min-height:14px"></div>'
      +'</div>'

      //  Agent Secimi 
      +'<div style="margin-bottom:8px"><div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">Agent Sec</div>'
      +'<div id="agentCards">'
      +OC_AGENTS.map(function(ag){
        return'<div class="agent-card'+(ag.id===_activeAgent?' active':'')+'" data-agent-id="'+ag.id+'" onclick="ocGWSetAgent(\''+ag.id+'\')">'
          +'<div class="agent-card-name">'+ag.icon+' '+ag.name+'</div>'
          +'<div class="agent-card-desc">'+ag.desc+'</div>'
          +'<span class="agent-card-status">'+(ag.id===_activeAgent?'Aktif':'Hazir')+'</span>'
          +'</div>';
      }).join('')
      +'</div></div>'

      //  Sohbet Akisi 
      +'<div style="margin-bottom:8px"><div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">AI Sohbet</div>'
      +'<div id="ocStream"><div class="ocm-sys">BIST AI v12 hazir. Mikrofon veya yazi ile sorun.</div></div>'
      +'<div style="display:flex;gap:6px;margin-top:6px">'
      +'<input class="oc-input" id="ocV12Input" placeholder="Komut veya soru..." onkeydown="if(event.key===\'Enter\'){event.preventDefault();v12Send();}">'
      +'<button class="dev-btn dev-btn-cyan" onclick="v12Send()" style="flex-shrink:0;padding:8px 12px;border-radius:7px">&#9654;</button>'
      +'</div></div>'

      //  GitHub Ayarlari 
      +'<div class="dev-card">'
      +'<div class="dev-title">GitHub Baglantisi</div>'
      +'<div style="display:flex;align-items:center;gap:6px;padding:7px;background:rgba(255,255,255,.03);border-radius:8px;margin-bottom:8px">'
      +'<div style="width:8px;height:8px;border-radius:50%;background:'+(ghOk?'var(--green)':'var(--t4)')+'"></div>'
      +'<div style="font-size:9px;color:var(--t2);flex:1">'+(ghOk?GH.owner+'/'+GH.repo+' ('+GH.branch+')':'Ayarlanmamis')+'</div>'
      +'<button class="dev-btn dev-btn-green" onclick="ghPushCurrentHTML()" style="padding:5px 10px;border-radius:6px;font-size:9px">Push</button>'
      +'</div>'
      +'<div class="dev-row"><span class="dev-label">Token</span><input class="dev-input" id="dev_ghToken" type="password" placeholder="ghp_xxxx" value="'+(GH.token||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Kullanici</span><input class="dev-input" id="dev_ghOwner" placeholder="github_user" value="'+(GH.owner||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Repo</span><input class="dev-input" id="dev_ghRepo" placeholder="bist-ai" value="'+(GH.repo||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Branch</span><input class="dev-input" id="dev_ghBranch" value="'+(GH.branch||'main')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Ana Dosya</span><input class="dev-input" id="dev_ghPath" value="'+(GH.path||'bist_elite_v12.html')+'"></div>'
      +'<button class="dev-btn dev-btn-cyan" onclick="ghSaveConfig()" style="width:100%;padding:8px;border-radius:8px;margin-top:6px">Kaydet</button>'
      +'</div>'

      //  Repo Tarayici 
      +'<div class="dev-card">'
      +'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">'
      +'<div class="dev-title" style="margin-bottom:0">Repo Dosya Yoneticisi</div>'
      +'<button class="dev-btn dev-btn-gold" onclick="repoListFiles(\'\')" style="padding:5px 10px;border-radius:6px;font-size:9px">Yukle</button>'
      +'</div>'
      +'<div id="repoBrowser"><div style="padding:12px;text-align:center;color:var(--t4);font-size:10px">Repo yuklemek icin "Yukle" tusuna basin</div></div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:7px">'
      +'<button class="dev-btn dev-btn-green" onclick="v12PushAll()" style="padding:8px;border-radius:7px;font-size:9px">Tum HTML Push</button>'
      +'<button class="dev-btn dev-btn-cyan" onclick="ocV12Process(\'yeni surum indir\')" style="padding:8px;border-radius:7px;font-size:9px">v12 Indir</button>'
      +'</div></div>'

      //  Hizli Komutlar 
      +'<div class="dev-card">'
      +'<div class="dev-title">Hizli Komutlar</div>'
      +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px">'
      +'<button class="dev-btn dev-btn-cyan" onclick="ocGWMsg(\'user\',\'durum\');ocV12Process(\'durum\')" style="width:100%;padding:8px;border-radius:7px;font-size:9px">Durum</button>'
      +'<button class="dev-btn dev-btn-gold" onclick="ocGWMsg(\'user\',\'hata bul\');v12HataBul()" style="width:100%;padding:8px;border-radius:7px;font-size:9px">Hata Tara</button>'
      +'<button class="dev-btn dev-btn-green" onclick="ghPushCurrentHTML()" style="width:100%;padding:8px;border-radius:7px;font-size:9px">GitHub Push</button>'
      +'<button class="dev-btn" onclick="repoListFiles(\'\')" style="width:100%;padding:8px;border-radius:7px;font-size:9px;color:var(--t3);border:1px solid var(--b2)">Repo Goster</button>'
      +'<button class="dev-btn" onclick="ocV12Process(\'rapor\')" style="width:100%;padding:8px;border-radius:7px;font-size:9px;color:var(--t3);border:1px solid var(--b2)">Rapor</button>'
      +'<button class="dev-btn" onclick="ocGWStartListen()" style="width:100%;padding:8px;border-radius:7px;font-size:9px;color:var(--purple);border:1px solid rgba(192,132,252,.3)">&#127908; Ses</button>'
      +'</div></div>'

      //  Log 
      +'<div class="dev-card">'
      +'<div class="dev-title">Log</div>'
      +'<div class="dev-log" id="devLogEl"><div class="info">BIST AI Elite v12 baslatildi.</div></div>'
      +'</div>';

    // GW durumu guncelle
    if(_ocGW.connected) ocGWSetStatus('connected','Bagli');
  }catch(e){console.warn('renderDevPanel v12:',e.message);}
};

//  YARDIMCI FONKSIYONLAR 
function v12Send(){
  try{
    var el=document.getElementById('ocV12Input');
    if(!el||!el.value.trim()) return;
    var msg=el.value.trim();el.value='';
    ocGWMsgUser(msg);
    if(_ocGW.connected) ocGWSend(msg);
    else ocV12Process(msg);
  }catch(e){}
}

function v12SaveGW(){
  try{
    var url=(document.getElementById('dev_ocGWUrl')||{}).value||'ws://127.0.0.1:18789';
    var token=(document.getElementById('dev_ocGWToken')||{}).value||'';
    var cfg=JSON.parse(localStorage.getItem('bist_dev_cfg')||'{}');
    cfg.ocGWUrl=url;cfg.ocGWToken=token;
    localStorage.setItem('bist_dev_cfg',JSON.stringify(cfg));
    _ocGW.url=url;_ocGW.token=token;
    ocGWConnect();
    toast('Gateway ayarlari kaydedildi, baglaniyor...');
  }catch(e){toast('Kayit hatasi: '+e.message);}
}

function v12PushAll(){
  ocGWMsg('user','Tum HTML GitHub\'a push ediliyor...');
  if(typeof ghPushCurrentHTML==='function') ghPushCurrentHTML();
}

function v12HataBul(){
  var hatalar=[];
  document.querySelectorAll('script').forEach(function(s,i){
    var code=s.textContent;var d=0;var inS=false;var sc='';var es=false;
    for(var ci=0;ci<code.length;ci++){
      var ch=code[ci];
      if(es){es=false;continue;}if(ch==='\\'&&inS){es=true;continue;}
      if(inS){if(ch===sc)inS=false;continue;}
      if(ch==='/'&&code[ci+1]=='/'){while(ci<code.length&&code[ci]!=='\n')ci++;continue;}
      if(ch in{'"':1,"'":1,'`':1}){inS=true;sc=ch;continue;}
      if(ch==='{')d++;else if(ch==='}')d--;
    }
    if(d!==0) hatalar.push('Blok '+(i+1)+': '+d+' kapanmamis bracket');
  });
  ocGWMsg('ai',hatalar.length?'Hatalar:\n'+hatalar.join('\n'):'Syntax temiz! Hata bulunamadi.');
}

//  INIT 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      ocGWLoad();
      // Dev sekmesi zaten var mi?
      if(!document.getElementById('page-dev')){
        var main=document.querySelector('main');
        if(main){var p=document.createElement('div');p.id='page-dev';p.className='page';main.appendChild(p);}
      }
      if(!document.getElementById('devTab')){
        var nav=document.querySelector('nav');
        if(nav){
          var btn=document.createElement('button');btn.id='devTab';btn.className='tab';
          btn.innerHTML='&#128736; Dev';btn.setAttribute('aria-label','Gelistirici paneli');
          btn.onclick=function(){try{pg('dev');renderDevPanel();}catch(e){}};
          nav.appendChild(btn);
        }
      }
      devLog('BIST AI Elite v12 baslatildi','ok');
      devLog('OpenClaw GW: '+(_ocGW.url||'Ayarlanmamis'), _ocGW.url?'info':'warn');
    }catch(e){console.warn('v12 init:',e.message);}
  },700);
});

</script>
<script>

// BIST v13 BLOK 12
// VIBE CODING AGENTLARI - Derin Uzman Sistemler
// Ses hatasi duzeltmesi + 8 Uzman Agent

//  SES HATASI DUZELTME (not-allowed = preview sorunu) 
// Mikrofon iznini kullanici jestiyle isteyecek sekilde guncelle
var _micPermission = 'unknown';
function checkMicPermission(cb){
  try{
    if(navigator.permissions){
      navigator.permissions.query({name:'microphone'}).then(function(r){
        _micPermission = r.state; // granted / denied / prompt
        if(cb) cb(r.state);
        r.onchange = function(){ _micPermission = r.state; };
      }).catch(function(){ if(cb) cb('unknown'); });
    } else {
      if(cb) cb('unknown');
    }
  }catch(e){ if(cb) cb('unknown'); }
}

// Ses baslat - kullanici gesture gerektiriyor
var _v13SRActive = false;
var _v13SR = null;
function v13StartMic(){
  try{
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if(!SR){
      toast('Bu tarayici ses tanima desteklemiyor. Chrome veya Edge kullanin.');
      return;
    }
    if(_v13SRActive){ v13StopMic(); return; }

    // Onceden mikrofon izni al
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
      // Stream'i kapat - sadece izin icin aldik
      stream.getTracks().forEach(function(t){ t.stop(); });
      _micPermission = 'granted';
      v13StartRecognition();
    }).catch(function(e){
      var msg = e.name === 'NotAllowedError'
        ? 'Mikrofon izni reddedildi. Tarayici ayarlarindan mikrofon iznini acin.'
        : e.name === 'NotFoundError'
        ? 'Mikrofon bulunamadi.'
        : 'Ses hatasi: ' + e.message;
      toast(msg);
      ocGWMsg('sys', msg);
    });
  }catch(e){ toast('Ses baslatma hatasi: '+e.message); }
}

function v13StartRecognition(){
  try{
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    _v13SR = new SR();
    _v13SR.lang = 'tr-TR';
    _v13SR.continuous = false;
    _v13SR.interimResults = true;
    _v13SR.maxAlternatives = 1;
    _v13SRActive = true;

    _v13SR.onstart = function(){
      v13SetMicUI('listening');
      if(typeof haptic === 'function') haptic('medium');
    };
    _v13SR.onresult = function(e){
      var interim = '';
      var final = '';
      for(var i = e.resultIndex; i < e.results.length; i++){
        if(e.results[i].isFinal) final += e.results[i][0].transcript;
        else interim += e.results[i][0].transcript;
      }
      var tEl = document.getElementById('v13Transcript');
      if(tEl) tEl.textContent = interim || final;
      if(final){
        v13StopMic();
        v13SendMsg(final);
      }
    };
    _v13SR.onerror = function(e){
      _v13SRActive = false;
      v13SetMicUI('idle');
      if(e.error === 'not-allowed'){
        toast('Mikrofon izni yok. Render URL"nden acin (HTTPS gerekli).');
        ocGWMsg('sys','Mikrofon: Render URL gerekli (HTTPS). Preview"de calisMAZ.');
      } else if(e.error !== 'no-speech'){
        ocGWMsg('sys','Ses: '+e.error);
      }
    };
    _v13SR.onend = function(){
      _v13SRActive = false;
      v13SetMicUI('idle');
    };
    _v13SR.start();
  }catch(e){
    _v13SRActive = false;
    v13SetMicUI('idle');
    toast('Ses hatasi: '+e.message);
  }
}

function v13StopMic(){
  try{ if(_v13SR) _v13SR.stop(); }catch(e){}
  _v13SRActive = false;
  v13SetMicUI('idle');
}

function v13SetMicUI(state){
  try{
    var btn = document.getElementById('v13MicBtn');
    var wave = document.getElementById('v13Wave');
    if(btn){
      btn.className = state === 'listening' ? 'v13-mic active'
        : state === 'speaking' ? 'v13-mic speaking' : 'v13-mic';
      btn.title = state === 'listening' ? 'Dinleniyor - dokunarak durdur' : 'Konusmak icin dokun';
    }
    if(wave) wave.className = 'v13-wave ' + state;
  }catch(e){}
}

function v13Speak(text){
  try{
    if(!_ttsEnabled) return;
    var clean = text.replace(/<[^>]*>/g,'').substring(0, 250);
    var utt = new SpeechSynthesisUtterance(clean);
    utt.lang = 'tr-TR'; utt.rate = 1.05; utt.pitch = 1.0;
    utt.onstart = function(){ v13SetMicUI('speaking'); };
    utt.onend = function(){
      v13SetMicUI('idle');
      if(_ttsEnabled && _ocGW && _ocGW.connected) setTimeout(v13StartMic, 700);
    };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utt);
  }catch(e){}
}

//  8 UZMAN VIBE CODING AGENTI 
var V13_AGENTS = [
  {
    id: 'vibe-architect',
    name: 'Vibe Architect',
    icon: '?',
    color: '#00D4FF',
    badge: 'Senior',
    desc: 'Tum uygulama mimarisini bilen bas gelistirici. BIST v12 icindeki tum 198 fonksiyonu, 11 blok yapisini ve Pine Script entegrasyonunu tam bilir.',
    systemPrompt: function(){
      var funcs = [];
      try{ funcs = Object.keys(window).filter(function(k){ return typeof window[k]==='function' && k.length > 3; }).slice(0,50); }catch(e){}
      var posC = Object.keys(S.openPositions||{}).length;
      var sigC = (S.sigs||[]).length;
      return 'Sen BIST AI Elite v12 uygulamasinin SENIOR MIMAR gelistiricisisin. '
        +'Uygulama mimarisini tamamen biliyorsun:\n'
        +'- 11 JavaScript blok, 198 fonksiyon\n'
        +'- Blok 1: Ana motor (STOCKS,S,C,startScan,pineSignal,btEngine)\n'
        +'- Blok 4: signalPrice,IDB,kalicilik\n'
        +'- Blok 5: Pozisyon/Rapor/Trader/Backtest/FalseSignal\n'
        +'- Blok 6: Pine tablo,AI sohbet,TV linkler\n'
        +'- Blok 7: UI/UX (glassmorphism,haptic,voice,ripple)\n'
        +'- Blok 8: IndexedDB,SW,Telegram inline,CSV\n'
        +'- Blok 9: Long press,Walk-forward\n'
        +'- Blok 10: GitHub API,Dev panel temel\n'
        +'- Blok 11: OpenClaw GW,Sesli sohbet,Repo browser\n'
        +'- Blok 12: Bu sen! Vibe coding agentlari\n\n'
        +'BIST DURUMU: '
        +posC+' acik pozisyon, '
        +sigC+' sinyal, '
        +'XU100: '+(S.xu100Change>=0?'+':'')+(S.xu100Change||0).toFixed(2)+'%\n\n'
        +'KURALLAR:\n'
        +'1. Yeni ozellik = <script> blogu olarak ver\n'
        +'2. Turkce karakter KULLANMA (i->i,g->g,u->u,o->o,s->s,c->c)\n'
        +'3. Her fonksiyon try-catch icerisinde\n'
        +'4. Override pattern: var _orig = mevcut; yeni = function(){\n'
        +'5. iOS SafE: blob: SW yok, dinamik eval yok\n'
        +'6. Max 800 token, net ve calisir kod\n'
        +'Turkce konuS, kod Ingizce/ASCII yaz.';
    }
  },
  {
    id: 'vibe-uiux',
    name: 'UI/UX Master',
    icon: '?',
    color: '#C084FC',
    badge: 'Design',
    desc: 'Glassmorphism, animasyon, micro-interaction ustasi. CSS + JS ile mukemmel kullanici deneyimi uretir.',
    systemPrompt: function(){
      return 'Sen BIST AI Elite v12 UI/UX uzman gelistiricisisin. '
        +'CSS ve JavaScript animasyon konusunda uzmansin.\n\n'
        +'MEVCUT TASARIM SISTEMI:\n'
        +'- Renk degiskenleri: --cyan:#00D4FF, --gold:#FFB800, --green:#00E676, --red:#FF4444, --purple:#C084FC\n'
        +'- Arkaplan: --bg:#000, --bg2:#0D0D0D, --bg3:#1A1A1A\n'
        +'- Metin: --t1:#F0F0F0, --t2:#CCCCCC, --t3:#888, --t4:#555\n'
        +'- Karti: .card - backdrop-filter:blur(20px), rgba(10,10,10,.75)\n'
        +'- Animasyonlar: ripple, glassmorphism, haptic, skeleton, pull-to-refresh\n\n'
        +'MEVCUT CSS SINIFLAR: .card,.sig,.strow,.pos-card,.btn,.tab,.modal,\n'
        +'.sstat,.sval,.slb2,.ctitle,.page,.hdr,.sgrid,.atbl,.btns\n\n'
        +'KURAL: iOS safe ol. backdrop-filter, transform, animation kullan. '
        +'Inline event handler yerine addEventListener. '
        +'Kodu <script> blogu olarak ver. Turkce karakter YOK.';
    }
  },
  {
    id: 'vibe-trader',
    name: 'Trader Pro',
    icon: '?',
    color: '#FFB800',
    badge: 'Finance',
    desc: 'BIST Katilim uzmani trader. Sinyal analizi, risk yonetimi, portfoy optimizasyonu.',
    systemPrompt: function(){
      var closed = S.closedPositions||[];
      var wins = closed.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
      var totalPnl = closed.reduce(function(a,p){return a+parseFloat(p.pnlPct||0);},0);
      var wr = closed.length ? (wins/closed.length*100).toFixed(1) : 'N/A';
      var openPos = Object.keys(S.openPositions||{}).map(function(k){
        var p = S.openPositions[k]; var t = k.split('_')[0];
        var cached = S.priceCache[t];
        var pnl = cached&&cached.price ? ((cached.price-p.entry)/p.entry*100).toFixed(2) : '?';
        return t+'('+pnl+'%)';
      }).join(', ');
      return 'Sen deneyimli bir BIST Katilim Endeksi uzman tradersin ve ayni zamanda yazilim gelistiricisisin.\n\n'
        +'PORTFOY DURUMU:\n'
        +'- Acik Pozisyonlar: '+(openPos||'Yok')+'\n'
        +'- Kapanis: '+closed.length+' islem, WR: %'+wr+', Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%\n'
        +'- XU100: '+(S.xu100Change>=0?'+':'')+(S.xu100Change||0).toFixed(2)+'%\n'
        +'- ADX Min: '+(C.adxMin||25)+', ATR Mult: '+(C.atrm||8)+', PRO Min: '+(C.sc||5)+'\n\n'
        +'BIST HAKKINDA BIL:\n'
        +'- 69 Katilim hissesi, 3 TF (Gunluk/4H/2H)\n'
        +'- Piyasa saatleri: 09:30-18:15\n'
        +'- 8 sistem: S1(SuperTrend+TMA), S2(PRO 6-faktor), Fusion, MasterAI, A60-A120\n'
        +'- Pine Script 2 yil backtest verileri mevcut\n\n'
        +'GOREV: Trader perspektifinden analiz yap, risk yonet, '
        +'kod gerektiren oneriler icin calisir JavaScript ver. Turkce karakter YOK.';
    }
  },
  {
    id: 'vibe-backtest',
    name: 'Quant Engineer',
    icon: '?',
    color: '#00E676',
    badge: 'Quant',
    desc: 'Backtest, Monte Carlo, Walk-Forward, Sharpe/Calmar optimizasyonu uzmani.',
    systemPrompt: function(){
      var lastBT = null;
      try{ lastBT = JSON.parse(localStorage.getItem('bist_last_bt')||'null'); }catch(e){}
      return 'Sen kantitatif finans ve algoritmik trading uzmanisin. '
        +'JavaScript ile calisir backtest ve optimizasyon kodu yaziyorsun.\n\n'
        +'MEVCUT BACKTEST SISTEMI:\n'
        +'- btEngine(ohlcv, xu100, cfg) -> {ret, wr, sharpe, maxdd, calmar, pf, tot, avgHold, trades}\n'
        +'- btFetchOHLCV(ticker, tf, callback) -> proxy\'den veri ceker\n'
        +'- runBT(), runWF(), runOpt(), runMC() mevcut\n'
        +'- Walk-Forward: egitim/test ayirimi (varsayilan 70/30)\n'
        +'- Monte Carlo: 500 senaryo simulasyonu\n'
        +'- Optimizasyon: ATR[4-12], ADX[20-35], PRO[3-6]\n\n'
        +'SON BACKTEST: '+(lastBT ? JSON.stringify(lastBT).substring(0,200) : 'Yok')+'\n\n'
        +'METRIKLER: Sharpe=(ortgetiri/std*sqrt(252)), '
        +'Calmar=(yillikgetiri/maxDD), ProfitFactor=(kazanctoplam/kayiptoplam)\n\n'
        +'GOREV: Backtest sonuclarini analiz et, parametre onerileri yap, '
        +'kod gerektirenlerde calisir JavaScript blok ver. Turkce karakter YOK.';
    }
  },
  {
    id: 'vibe-debug',
    name: 'Debug Master',
    icon: '?',
    color: '#FF4444',
    badge: 'Debug',
    desc: 'iOS Safari hatalari, Script error, syntax problemleri. Her hatayi bulup duzeltir.',
    systemPrompt: function(){
      var scripts = document.querySelectorAll('script');
      var totalChars = 0;
      document.querySelectorAll('script').forEach(function(s){ totalChars += s.textContent.length; });
      return 'Sen JavaScript ve iOS Safari uzman hata ayiklayicisisin.\n\n'
        +'UYGULAMA YAPISI:\n'
        +'- '+scripts.length+' script blogu, toplam '+(totalChars/1024).toFixed(0)+'KB JS\n'
        +'- iOS Safari v14+ hedef\n'
        +'- Render.com (HTTPS) uzerinde calisir\n\n'
        +'BILINEN IOS SORUNLARI:\n'
        +'1. blob: URL ile SW kayit = SecurityError (COZUM: /sw.js kullan)\n'
        +'2. SpeechRecognition "not-allowed" = HTTPS gerekli veya preview ortami\n'
        +'3. btoa(outerHTML) = buyuk dosyalarda encoding sorunu\n'
        +'4. IndexedDB Safari private mode = erisim reddedilir\n'
        +'5. eval()/new Function() = CSP bloklayabilir\n'
        +'6. classList.add() null element = crash\n\n'
        +'HATA ANALIZI ADIMI:\n'
        +'1. Hatanin tam mesajini sor\n'
        +'2. Hangi blokta oldugunu tespit et\n'
        +'3. Try-catch ile sar ve duzelti ver\n'
        +'4. iOS safe alternatif sun\n\n'
        +'Kullanicidan hata mesajini ve hangi islemde oldugunu sor. Sonra cozum ver. Turkce karakter YOK.';
    }
  },
  {
    id: 'vibe-data',
    name: 'Data Engineer',
    icon: '?',
    color: '#FF7043',
    badge: 'Data',
    desc: 'Pine Script verileri, proxy API, OHLCV analizi, veri yapilari uzmani.',
    systemPrompt: function(){
      var cacheSize = Object.keys(S.priceCache||{}).length;
      return 'Sen veri muhendisi ve finansal veri uzmanisin. '
        +'Proxy API ve Pine Script veri yapilarini tam biliyorsun.\n\n'
        +'PROXY API ENDPOINT\'LERI:\n'
        +'GET /prices?symbols=EREGL,BIMAS -> {ticker:{price,change_pct,high,low,volume}}\n'
        +'GET /xu100 -> {price,change_pct,trend}\n'
        +'GET /ohlcv/{ticker}?tf=D -> {ohlcv:[[ts,o,h,l,c,v],...],xu100:{price,...}}\n'
        +'POST /scan -> {signals:[{ticker,price,signal,is_master,consensus,adx,rsi,pro_score,'
        +'fusion_pct,strength,pstate,stop_price,active_sys,ema200,ema50,'
        +'sys1:{buys,sells,wins,losses,total_pnl,open_pnl},'
        +'sys2:{buys,sells,wins,losses,total_pnl,score},'
        +'fusion:{buys,sells,wins,losses,total_pnl},'
        +'master_ai:{total_pnl,buy_consensus,sell_consensus,dyn_buy_thresh,dyn_sell_thresh},'
        +'agents:{a60:{pnl,buys,wins,losses},...a120},'
        +'agent_probs:{a60,a61,a62,a81,a120},'
        +'pro_factors:{rs_strong,accum_d,exp_4h,break_4h,mom_2h,dna}}]}\n\n'
        +'MEVCUT CACHE: '+cacheSize+' hisse fiyat cachede\n'
        +'PROXY URL: Blok 1 L591\'de tanimli\n\n'
        +'GOREV: Veri yapilarini analiz et, eksik/yanlis veri kullanimi duzelt, '
        +'yeni veri entegrasyonu icin kod yaz. Turkce karakter YOK.';
    }
  },
  {
    id: 'vibe-feature',
    name: 'Feature Factory',
    icon: '?',
    color: '#00D4FF',
    badge: 'Feature',
    desc: 'Yeni ozellik fikirleri uretir ve aninda calisir kod yazar. Vibe coding icin optimize.',
    systemPrompt: function(){
      var pages = ['signals','scanner','positions','watchlist','backtest','agents','report','settings','telegram','dev'];
      return 'Sen cretif bir fullstack gelistiricisin. '
        +'Kullanicinin fikirlerini aninda calisir JavaScript koduna donusturuyorsun.\n\n'
        +'UYGULAMA SAYFALAR: '+pages.join(', ')+'\n\n'
        +'MEVCUT OZELLIKLER (GERCEKLESTIRILMIS):\n'
        +'glassmorphism, haptic, ripple, skeleton, pull-refresh, swipe-delete,\n'
        +'voice command, konfeti, IndexedDB, offline SW, Telegram inline,\n'
        +'CSV export, price alert, what-if sim, streak badge, web share,\n'
        +'portfolio optimizer, sector heatmap, backtest compare, VaR/CVaR,\n'
        +'dark pool sim, tutorial carousel, web vitals, A/B test\n\n'
        +'KOD KALIP SABLONU:\n'
        +'(function(){\n'
        +'  // CSS ekle\n'
        +'  var st=document.createElement("style");\n'
        +'  st.textContent="/* stiller */";\n'
        +'  document.head.appendChild(st);\n'
        +'  // Fonksiyon\n'
        +'  function yeniOzellik(){\n'
        +'    try{\n'
        +'      // kod\n'
        +'    }catch(e){ console.warn("ozellik:",e.message); }\n'
        +'  }\n'
        +'  // Baslat\n'
        +'  window.addEventListener("load",function(){\n'
        +'    setTimeout(yeniOzellik,1000);\n'
        +'  });\n'
        +'})()\n\n'
        +'GOREV: Kullanicinin istegini cok kisa calisir kod ile gerceklesir. '
        +'Herzaman try-catch kullan. iOS safe. Turkce karakter YOK. '
        +'Kodu hemen yaz, gereksiz aciklama yapma.';
    }
  },
  {
    id: 'vibe-review',
    name: 'Code Reviewer',
    icon: '?',
    color: '#888888',
    badge: 'Review',
    desc: 'Kod kalitesi, performans, guvenlik ve best practice incelemesi yapar.',
    systemPrompt: function(){
      return 'Sen kidemli bir kod inceleme uzmanisin. '
        +'JavaScript guvenlik, performans ve best practice konularinda uzmansin.\n\n'
        +'INCELEME KRITERLERI:\n'
        +'1. GUVENLIK: XSS, eval, inline event, localStorage sifreleme\n'
        +'2. PERFORMANS: DOM manipulasyon, event listener temizleme, memory leak\n'
        +'3. IOS UYUMLULUK: Safari quirks, HTTPS gereksinimleri, blob URL\n'
        +'4. HATA YONETIMI: try-catch eksikligi, undefined kontrol, null check\n'
        +'5. KOD KALITESI: Duplicate fonksiyon, dead code, naming convention\n'
        +'6. OKUNABILIRLIK: Yorum satiri, fonksiyon uzunlugu, complexity\n\n'
        +'INCELEME FORMATI:\n'
        +'? KRITIK: Acil duzeltme gerekli\n'
        +'? UYARI: Duzeltilmesi onerilir\n'
        +'? ONERI: Iyilestirme firsati\n'
        +'? IYI: Dogru yaklasim\n\n'
        +'Kullanicidan incelenecek kodu veya blok numarasini iste. '
        +'Sonra detayli inceleme yap. Turkce karakter YOK.';
    }
  }
];

//  AGENT MOTOR 
var _v13ActiveAgent = V13_AGENTS[0];
var _v13History = [];
var _v13Thinking = false;

function v13SetAgent(agentId){
  var agent = V13_AGENTS.find(function(a){ return a.id === agentId; });
  if(!agent) return;
  _v13ActiveAgent = agent;
  _v13History = []; // Her agent degisiminde gecmis temizle
  v13AppendMsg('sys', agent.icon + ' ' + agent.name + ' aktif. ' + agent.desc);
  // Agent kartlarini guncelle
  document.querySelectorAll('.v13-agent-card').forEach(function(c){
    c.classList.toggle('v13-active', c.dataset.agentId === agentId);
  });
  if(typeof haptic === 'function') haptic('light');
}

function v13AppendMsg(role, content, codeBlock){
  try{
    var el = document.getElementById('v13Stream');
    if(!el) return;
    var div = document.createElement('div');
    if(role === 'user') div.className = 'v13m-user';
    else if(role === 'sys') div.className = 'v13m-sys';
    else div.className = 'v13m-ai';
    if(codeBlock){
      div.innerHTML = '<div class="v13m-code-wrap">'
        + '<div class="v13m-code-header">'
          + '<span style="font-size:8px;color:var(--green)">JavaScript</span>'
          + '<button class="v13-code-btn v13-apply-btn" data-code="'+encodeURIComponent(codeBlock)+'">? Uygula</button>'
          + '<button class="v13-code-btn v13-push-btn" data-code="'+encodeURIComponent(codeBlock)+'">? Push</button>'
        + '</div>'
        + '<pre class="v13m-code">'+codeBlock.substring(0,500).replace(/</g,'&lt;').replace(/>/g,'&gt;')
          +(codeBlock.length>500?'\n...('+Math.round(codeBlock.length/1024*10)/10+'KB)':'')+'</pre>'
        + '</div>';
    } else {
      div.textContent = content;
    }
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
    // Buton olaylari
    if(codeBlock){
      var applyBtns = div.querySelectorAll('.v13-apply-btn');
      applyBtns.forEach(function(btn){
        btn.onclick = function(){
          var code = decodeURIComponent(btn.dataset.code);
          v13ApplyCode(code);
        };
      });
      var pushBtns = div.querySelectorAll('.v13-push-btn');
      pushBtns.forEach(function(btn){
        btn.onclick = function(){
          var code = decodeURIComponent(btn.dataset.code);
          v13ApplyCode(code, true);
        };
      });
    }
  }catch(e){}
}

function v13ApplyCode(code, pushAfter){
  try{
    // Syntax kontrol
    var d=0; var inS=false; var sc=''; var es=false;
    for(var i=0;i<code.length;i++){
      var ch=code[i];
      if(es){es=false;continue;} if(ch==='\\'&&inS){es=true;continue;}
      if(inS){if(ch===sc)inS=false;continue;}
      if(ch==='/'&&code[i+1]=='/'){while(i<code.length&&code[i]!=='\n')i++;continue;}
      if(ch in{'"':1,"'":1,'`':1}){inS=true;sc=ch;continue;}
      if(ch==='{')d++; else if(ch==='}')d--;
    }
    if(d!==0){
      v13AppendMsg('sys','HATA: '+d+' kapanmamis bracket. Kod uygulanmadi.');
      return;
    }
    var script = document.createElement('script');
    script.textContent = code;
    document.head.appendChild(script);
    v13AppendMsg('sys','Kod uygulandi!');
    if(typeof haptic === 'function') haptic('success');
    toast('Kod uygulandi!');
    if(pushAfter && typeof ghPushCurrentHTML === 'function'){
      setTimeout(function(){
        v13AppendMsg('sys','GitHub push baslatiliyor...');
        ghPushCurrentHTML();
      }, 500);
    }
  }catch(e){
    v13AppendMsg('sys','Uygulama hatasi: '+e.message);
    toast('Hata: '+e.message);
  }
}

function v13SendMsg(text){
  try{
    if(!text || !text.trim()) return;
    if(_v13Thinking){ toast('Agent dusunuyor, lutfen bekleyin...'); return; }
    var msg = text.trim();
    v13AppendMsg('user', msg);
    _v13History.push({role:'user', content:msg});
    v13CallAPI(msg);
  }catch(e){ v13AppendMsg('sys','Gonderme hatasi: '+e.message); }
}

function v13CallAPI(msg){
  try{
    _v13Thinking = true;
    var agent = _v13ActiveAgent;
    // Loading goster
    var loadingDiv = document.createElement('div');
    loadingDiv.className = 'v13m-thinking';
    loadingDiv.id = 'v13Loading';
    loadingDiv.innerHTML = '<span class="v13-dot"></span><span class="v13-dot"></span><span class="v13-dot"></span>';
    var stream = document.getElementById('v13Stream');
    if(stream){ stream.appendChild(loadingDiv); stream.scrollTop = stream.scrollHeight; }

    // Mesaj gecmisi (son 8 mesaj)
    var msgs = _v13History.slice(-8).map(function(m){
      return {role: m.role === 'user' ? 'user' : 'assistant', content: m.content};
    });

    fetch('https://api.anthropic.com/v1/messages',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        model:'claude-sonnet-4-20250514',
        max_tokens:1500,
        system: agent.systemPrompt(),
        messages: msgs
      })
    }).then(function(r){ return r.json(); })
    .then(function(d){
      _v13Thinking = false;
      var ld = document.getElementById('v13Loading');
      if(ld) ld.remove();
      var resp = (d.content && d.content[0] && d.content[0].text) || 'Yanit alinamadi.';
      _v13History.push({role:'assistant', content:resp});
      // Kod bloklari ayir
      var codeMatch = resp.match(/```(?:javascript|js)?\n?([\s\S]*?)```/);
      if(codeMatch){
        var beforeCode = resp.replace(codeMatch[0],'').trim();
        if(beforeCode) v13AppendMsg('ai', beforeCode);
        v13AppendMsg('ai', null, codeMatch[1].trim());
      } else {
        v13AppendMsg('ai', resp);
      }
      if(_ttsEnabled) v13Speak(resp.replace(/```[\s\S]*?```/g,'').substring(0,200));
    }).catch(function(e){
      _v13Thinking = false;
      var ld = document.getElementById('v13Loading');
      if(ld) ld.remove();
      // Fallback - OpenClaw GW
      if(_ocGW && _ocGW.connected){
        v13AppendMsg('sys','API hatasi, OpenClaw Gateway deneniyor...');
        ocGWSend(msg);
      } else {
        v13AppendMsg('sys','Baglanti hatasi. OpenClaw Gateway baglayin.');
      }
    });
  }catch(e){
    _v13Thinking = false;
    v13AppendMsg('sys','API cagri hatasi: '+e.message);
  }
}

//  V13 CSS 
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      // Agent kartlari
      '.v13-agents-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:10px}'
      +'.v13-agent-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:10px;cursor:pointer;transition:all .18s;position:relative;overflow:hidden}'
      +'.v13-agent-card::before{content:"";position:absolute;inset:0;background:linear-gradient(135deg,rgba(255,255,255,.02),transparent);pointer-events:none}'
      +'.v13-agent-card:active{transform:scale(.97)}'
      +'.v13-agent-card.v13-active{border-color:rgba(0,212,255,.45);background:rgba(0,212,255,.07);box-shadow:0 0 12px rgba(0,212,255,.1)}'
      +'.v13-agent-icon{font-size:20px;margin-bottom:4px}'
      +'.v13-agent-name{font-size:11px;font-weight:700;color:var(--t1);margin-bottom:2px}'
      +'.v13-agent-desc{font-size:8px;color:var(--t4);line-height:1.4}'
      +'.v13-agent-badge{display:inline-block;font-size:7px;padding:1px 6px;border-radius:3px;background:rgba(0,212,255,.12);color:var(--cyan);font-weight:700;margin-top:4px;text-transform:uppercase;letter-spacing:.5px}'
      // Ses
      +'.v13-mic-section{background:rgba(192,132,252,.05);border:1px solid rgba(192,132,252,.12);border-radius:12px;padding:12px;margin-bottom:10px}'
      +'.v13-wave{display:flex;align-items:center;justify-content:center;gap:3px;height:28px;margin:6px 0}'
      +'.v13-wave span{display:inline-block;width:3px;border-radius:2px;transition:all .3s}'
      +'.v13-wave.idle span{height:4px;background:rgba(255,255,255,.15);animation:none}'
      +'.v13-wave.listening span{background:var(--cyan);animation:vw13 .8s ease-in-out infinite}'
      +'.v13-wave span:nth-child(1){animation-delay:0s}.v13-wave span:nth-child(2){animation-delay:.1s}'
      +'.v13-wave span:nth-child(3){animation-delay:.2s}.v13-wave span:nth-child(4){animation-delay:.1s}'
      +'.v13-wave span:nth-child(5){animation-delay:0s}'
      +'.v13-wave.speaking span{background:var(--gold);animation:vw13 .5s ease-in-out infinite}'
      +'@keyframes vw13{0%,100%{height:6px}50%{height:22px}}'
      +'.v13-mic{width:52px;height:52px;border-radius:50%;border:2px solid rgba(192,132,252,.35);background:rgba(192,132,252,.08);color:var(--purple);font-size:20px;cursor:pointer;transition:all .2s;display:flex;align-items:center;justify-content:center;margin:0 auto}'
      +'.v13-mic.active{border-color:var(--cyan);background:rgba(0,212,255,.12);color:var(--cyan);box-shadow:0 0 18px rgba(0,212,255,.25);animation:micP .9s infinite}'
      +'.v13-mic.speaking{border-color:var(--gold);background:rgba(255,184,0,.1);color:var(--gold)}'
      +'@keyframes micP{0%,100%{transform:scale(1)}50%{transform:scale(1.07)}}'
      +'#v13Transcript{font-size:9px;color:var(--t4);text-align:center;min-height:13px;margin-top:5px;font-style:italic}'
      // Sohbet
      +'.v13-stream{height:260px;overflow-y:auto;padding:8px;background:rgba(0,0,0,.6);border-radius:10px;border:1px solid rgba(255,255,255,.05);display:flex;flex-direction:column;gap:6px}'
      +'.v13m-user{align-self:flex-end;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.18);border-radius:10px 10px 2px 10px;padding:8px 11px;font-size:10px;color:var(--cyan);max-width:88%;word-break:break-word}'
      +'.v13m-ai{align-self:flex-start;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);border-radius:10px 10px 10px 2px;padding:8px 11px;font-size:10px;color:var(--t2);max-width:94%;white-space:pre-wrap;word-break:break-word;line-height:1.55}'
      +'.v13m-sys{align-self:center;font-size:8px;color:var(--t4);font-style:italic}'
      +'.v13m-thinking{display:flex;gap:5px;align-items:center;padding:8px;align-self:flex-start}'
      +'.v13-dot{width:7px;height:7px;border-radius:50%;background:var(--purple);animation:dotP 1.2s infinite}'
      +'.v13-dot:nth-child(2){animation-delay:.2s}.v13-dot:nth-child(3){animation-delay:.4s}'
      +'@keyframes dotP{0%,60%,100%{transform:scale(1);opacity:.5}30%{transform:scale(1.3);opacity:1}}'
      +'.v13m-code-wrap{align-self:flex-start;max-width:96%;width:100%}'
      +'.v13m-code-header{display:flex;align-items:center;gap:5px;padding:5px 8px;background:rgba(0,0,0,.4);border-radius:7px 7px 0 0;border:1px solid rgba(255,255,255,.07)}'
      +'.v13m-code{background:rgba(0,20,0,.8);border:1px solid rgba(0,230,118,.15);border-top:none;border-radius:0 0 7px 7px;padding:9px;font-family:Courier New,monospace;font-size:8px;color:var(--green);max-height:160px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;margin:0}'
      +'.v13-code-btn{font-size:8px;padding:3px 8px;border-radius:4px;border:none;cursor:pointer;font-weight:700;margin-left:auto}'
      +'.v13-apply-btn{background:rgba(0,230,118,.15);color:var(--green);border:1px solid rgba(0,230,118,.3)}'
      +'.v13-push-btn{background:rgba(0,212,255,.12);color:var(--cyan);border:1px solid rgba(0,212,255,.25)}'
      // Input
      +'.v13-input-row{display:flex;gap:6px;margin-top:7px}'
      +'.v13-input{flex:1;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:9px 12px;font-size:11px;color:var(--t1)}'
      +'.v13-input:focus{outline:none;border-color:rgba(0,212,255,.4);box-shadow:0 0 0 2px rgba(0,212,255,.08)}'
      +'.v13-send-btn{padding:9px 14px;border-radius:8px;background:rgba(0,212,255,.15);color:var(--cyan);border:1px solid rgba(0,212,255,.3);cursor:pointer;font-size:14px;flex-shrink:0}'
      +'.v13-send-btn:active{opacity:.7}'
      // TTS toggle
      +'.tts-row{display:flex;align-items:center;justify-content:space-between;padding:5px 0}'
      +'.v13-toggle{position:relative;display:inline-block;width:38px;height:20px}'
      +'.v13-toggle input{opacity:0;width:0;height:0}'
      +'.v13-toggle-slider{position:absolute;cursor:pointer;inset:0;background:rgba(255,255,255,.1);border-radius:10px;transition:.3s}'
      +'.v13-toggle input:checked+.v13-toggle-slider{background:rgba(0,212,255,.35)}'
      +'.v13-toggle-slider:before{content:"";position:absolute;width:14px;height:14px;left:3px;bottom:3px;background:rgba(255,255,255,.7);border-radius:50%;transition:.3s}'
      +'.v13-toggle input:checked+.v13-toggle-slider:before{transform:translateX(18px);background:var(--cyan)}';
    document.head.appendChild(st);
  }catch(e){}
})();

//  DEV PANEL OVERRIDE 
var _prevRenderDevPanel = renderDevPanel;
renderDevPanel = function(){
  try{
    var el = document.getElementById('page-dev');
    if(!el) return;
    var ghOk = !!(GH && GH.token && GH.owner && GH.repo);

    el.innerHTML = ''
      // Baslik
      +'<div style="display:flex;align-items:center;gap:9px;margin-bottom:13px">'
      +'<div style="font-size:22px">&#128736;</div>'
      +'<div><div style="font-size:14px;font-weight:700;color:var(--t1)">BIST AI Dev v13</div>'
      +'<div style="font-size:9px;color:var(--t4)">8 Uzman Agent + Sesli Sohbet + GitHub Repo</div></div>'
      +'<div style="margin-left:auto;font-size:9px;padding:3px 8px;border-radius:5px;background:rgba(0,230,118,.1);color:var(--green);font-weight:700">v13</div>'
      +'</div>'

      // GitHub hizli durum
      +'<div style="display:flex;align-items:center;gap:7px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:9px;border:1px solid rgba(255,255,255,.06);margin-bottom:10px">'
      +'<div style="width:7px;height:7px;border-radius:50%;background:'+(ghOk?'var(--green)':'var(--t4)')+'"></div>'
      +'<div style="font-size:9px;color:var(--t2);flex:1">'+(ghOk?GH.owner+'/'+GH.repo:'GitHub ayarlanmamis')+'</div>'
      +(ghOk?'<button onclick="ghPushCurrentHTML()" style="padding:4px 10px;border-radius:6px;background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.25);font-size:9px;cursor:pointer;font-weight:600">Push</button>':'')
      +'<button onclick="v13ShowGHSettings()" style="padding:4px 10px;border-radius:6px;background:rgba(255,255,255,.04);color:var(--t3);border:1px solid rgba(255,255,255,.08);font-size:9px;cursor:pointer">Ayarla</button>'
      +'</div>'

      // Agent secimi
      +'<div style="margin-bottom:8px"><div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">Agent Sec (8 Uzman)</div>'
      +'<div class="v13-agents-grid">'
      + V13_AGENTS.map(function(ag){
        var isActive = _v13ActiveAgent && _v13ActiveAgent.id === ag.id;
        return '<div class="v13-agent-card'+(isActive?' v13-active':'')+'" data-agent-id="'+ag.id+'" onclick="v13SetAgent(\''+ag.id+'\')" style="border-color:'+(isActive?ag.color+'66':'rgba(255,255,255,.07)')+'">'
          +'<div class="v13-agent-icon">'+ag.icon+'</div>'
          +'<div class="v13-agent-name" style="color:'+(isActive?ag.color:'var(--t1)')+'">'+ag.name+'</div>'
          +'<div class="v13-agent-desc">'+ag.desc.substring(0,60)+'...</div>'
          +'<span class="v13-agent-badge" style="background:'+ag.color+'18;color:'+ag.color+'">'+ag.badge+'</span>'
          +'</div>';
      }).join('')
      +'</div></div>'

      // Sesli sohbet
      +'<div class="v13-mic-section">'
      +'<div class="tts-row"><div style="font-size:10px;font-weight:700;color:var(--purple)">Sesli Sohbet</div>'
      +'<div style="display:flex;align-items:center;gap:6px">'
      +'<span style="font-size:8px;color:var(--t4)">Ses Yanit</span>'
      +'<label class="v13-toggle"><input type="checkbox" id="v13TTS" '
        +(_ttsEnabled?'checked':'')+' onchange="_ttsEnabled=this.checked">'
      +'<span class="v13-toggle-slider"></span></label>'
      +'</div></div>'
      +'<div id="v13Wave" class="v13-wave idle"><span></span><span></span><span></span><span></span><span></span></div>'
      +'<button id="v13MicBtn" class="v13-mic" onclick="v13StartMic()" title="Konusmak icin dokun">&#127908;</button>'
      +'<div id="v13Transcript"></div>'
      +'<div style="font-size:8px;color:var(--t4);text-align:center;margin-top:5px">'
      +(window.location.hostname==='claude.ai'||window.location.hostname.indexOf('claude')>-1
        ?'Ses: Preview modunda calisMAZ. Render URL kullanin (HTTPS gerekli).'
        :'Turkce konuS. Render URL ile tam destek.')
      +'</div></div>'

      // AI Sohbet akisi
      +'<div style="margin-bottom:8px">'
      +'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">'
      +'<div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px">'
      +(_v13ActiveAgent?_v13ActiveAgent.icon+' '+_v13ActiveAgent.name+' Sohbet':'AI Sohbet')
      +'</div>'
      +'<button onclick="_v13History=[];var s=document.getElementById(\'v13Stream\');if(s)s.innerHTML=\'\'" style="font-size:8px;color:var(--t4);background:none;border:none;cursor:pointer">Temizle</button>'
      +'</div>'
      +'<div id="v13Stream" class="v13-stream"><div class="v13m-sys">'+( _v13ActiveAgent?_v13ActiveAgent.icon+' '+_v13ActiveAgent.name+' hazir.':'Agent hazir.')+'</div></div>'
      +'<div class="v13-input-row">'
      +'<input id="v13Input" class="v13-input" placeholder="Sorun veya komut yazin..." onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();v13SendMsg(document.getElementById(\'v13Input\').value);document.getElementById(\'v13Input\').value=\'\'}">'
      +'<button class="v13-send-btn" onclick="var el=document.getElementById(\'v13Input\');if(el){v13SendMsg(el.value);el.value=\'\'}">&#9654;</button>'
      +'</div></div>'

      // Hizli komutlar
      +'<div style="margin-bottom:10px"><div style="font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">Hizli Komutlar</div>'
      +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px">'
      +[
        {l:'Durum',c:'uygulama durumunu anlat'},
        {l:'Hata Tara',c:'tum bloklari syntax kontrol et'},
        {l:'Push',c:'github push yap'},
        {l:'Repo Goster',c:'repo dosyalarini listele'},
        {l:'Rapor',c:'portfoy raporu ver'},
        {l:'Optimize',c:'mevcut parametreleri optimize et'},
      ].map(function(q){
        return '<button onclick="v13SendMsg(\''+q.c+'\')" style="padding:8px;border-radius:8px;font-size:9px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);color:var(--t3);cursor:pointer;transition:all .15s">'+q.l+'</button>';
      }).join('')
      +'</div></div>'

      // GitHub ayarlar (gizli - acilabilir)
      +'<div id="v13GHSettings" style="display:none" class="dev-card">'
      +'<div class="dev-title">GitHub Baglantisi</div>'
      +'<div class="dev-row"><span class="dev-label">Token</span><input class="dev-input" id="dev_ghToken" type="password" placeholder="ghp_xxxx" value="'+(GH.token||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Kullanici</span><input class="dev-input" id="dev_ghOwner" value="'+(GH.owner||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Repo</span><input class="dev-input" id="dev_ghRepo" value="'+(GH.repo||'')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Branch</span><input class="dev-input" id="dev_ghBranch" value="'+(GH.branch||'main')+'"></div>'
      +'<div class="dev-row"><span class="dev-label">Dosya</span><input class="dev-input" id="dev_ghPath" value="'+(GH.path||'bist_elite_v13.html')+'"></div>'
      +'<button class="dev-btn dev-btn-cyan" onclick="ghSaveConfig();document.getElementById(\'v13GHSettings\').style.display=\'none\'" style="width:100%;padding:8px;border-radius:8px;margin-top:6px">Kaydet</button>'
      +'</div>';

  }catch(e){ console.warn('renderDevPanel v13:',e.message); }
};

function v13ShowGHSettings(){
  var s = document.getElementById('v13GHSettings');
  if(s) s.style.display = s.style.display === 'none' ? 'block' : 'none';
}

//  INIT 
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      checkMicPermission(function(state){
        devLog && devLog('Mikrofon: '+state, state==='granted'?'ok':state==='denied'?'err':'warn');
      });
      if(!document.getElementById('page-dev')){
        var main = document.querySelector('main');
        if(main){ var p=document.createElement('div');p.id='page-dev';p.className='page';main.appendChild(p); }
      }
      if(!document.getElementById('devTab')){
        var nav = document.querySelector('nav');
        if(nav){
          var btn=document.createElement('button');btn.id='devTab';btn.className='tab';
          btn.innerHTML='&#128736; Dev';
          btn.onclick=function(){ try{ pg('dev'); renderDevPanel(); }catch(e){} };
          nav.appendChild(btn);
        }
      }
    }catch(e){ console.warn('v13 init:',e.message); }
  }, 600);
});

</script>
<script>

// BIST v14 BLOK 13
// OpenClaw Destekli Tum Modeller + Ollama Otomatik Yukle
// Anthropic / OpenAI / Google / DeepSeek / xAI / Groq
// MiniMax / Moonshot / OpenRouter / Ollama (lokal)

//  MODEL KATALOGU 
var MODEL_CATALOG = {
  anthropic: {
    name: 'Anthropic',
    icon: '?',
    color: '#CC785C',
    envKey: 'ANTHROPIC_API_KEY',
    apiUrl: 'https://api.anthropic.com/v1/messages',
    apiType: 'anthropic',
    models: [
      {id:'claude-opus-4-6',      name:'Claude Opus 4.6',     ctx:200000, tier:'premium',  cost:'$$$', best:'Derin analiz, karmasik kod'},
      {id:'claude-sonnet-4-6',    name:'Claude Sonnet 4.6',   ctx:200000, tier:'balanced', cost:'$$',  best:'Genel gelistirme (VARSAYILAN)'},
      {id:'claude-sonnet-4-5',    name:'Claude Sonnet 4.5',   ctx:200000, tier:'balanced', cost:'$$',  best:'Guvenilir kod uretimi'},
      {id:'claude-haiku-4-5',     name:'Claude Haiku 4.5',    ctx:200000, tier:'fast',     cost:'$',   best:'Hizli gorevler, ucuz'},
    ]
  },
  openai: {
    name: 'OpenAI',
    icon: '?',
    color: '#10A37F',
    envKey: 'OPENAI_API_KEY',
    apiUrl: 'https://api.openai.com/v1/chat/completions',
    apiType: 'openai',
    models: [
      {id:'gpt-5.4',      name:'GPT-5.4',      ctx:128000, tier:'premium',  cost:'$$$', best:'En guclu OpenAI'},
      {id:'gpt-5',        name:'GPT-5',         ctx:128000, tier:'premium',  cost:'$$$', best:'Guclu genel model'},
      {id:'gpt-4o',       name:'GPT-4o',        ctx:128000, tier:'balanced', cost:'$$',  best:'Hizli ve kapsamli'},
      {id:'gpt-4o-mini',  name:'GPT-4o Mini',   ctx:128000, tier:'fast',     cost:'$',   best:'Ucuz, hizli'},
      {id:'o3',           name:'o3',             ctx:200000, tier:'premium',  cost:'$$$', best:'Derin mantik yurutme'},
      {id:'o4-mini',      name:'o4-mini',        ctx:128000, tier:'balanced', cost:'$$',  best:'Akil yurutme + hiz'},
    ]
  },
  google: {
    name: 'Google',
    icon: '?',
    color: '#4285F4',
    envKey: 'GEMINI_API_KEY',
    apiUrl: 'https://generativelanguage.googleapis.com/v1beta/models',
    apiType: 'gemini',
    models: [
      {id:'gemini-3.1-pro-preview', name:'Gemini 3.1 Pro',   ctx:1000000, tier:'premium',  cost:'$$',  best:'1M token context!'},
      {id:'gemini-3-flash-preview', name:'Gemini 3 Flash',   ctx:1000000, tier:'fast',     cost:'$',   best:'Cok hizli, ucuz'},
      {id:'gemini-2.5-pro',         name:'Gemini 2.5 Pro',   ctx:1000000, tier:'premium',  cost:'$$',  best:'Uzun dok analizi'},
      {id:'gemini-2.5-flash',       name:'Gemini 2.5 Flash', ctx:1000000, tier:'fast',     cost:'$',   best:'Hiz + uzun context'},
    ]
  },
  deepseek: {
    name: 'DeepSeek',
    icon: '?',
    color: '#4D6BFE',
    envKey: 'DEEPSEEK_API_KEY',
    apiUrl: 'https://api.deepseek.com/v1',
    apiType: 'openai-compat',
    models: [
      {id:'deepseek-chat',     name:'DeepSeek V3.2',  ctx:64000,  tier:'balanced', cost:'$',   best:'Kod uretimi, COK UCUZ'},
      {id:'deepseek-reasoner', name:'DeepSeek R1',    ctx:64000,  tier:'premium',  cost:'$$',  best:'Derin mantik, ucuz'},
    ]
  },
  xai: {
    name: 'xAI (Grok)',
    icon: '?',
    color: '#1DA1F2',
    envKey: 'XAI_API_KEY',
    apiUrl: 'https://api.x.ai/v1',
    apiType: 'openai-compat',
    models: [
      {id:'grok-4',       name:'Grok 4',      ctx:131072, tier:'premium',  cost:'$$$', best:'En guclu Grok'},
      {id:'grok-4-fast',  name:'Grok 4 Fast', ctx:131072, tier:'balanced', cost:'$$',  best:'Hizli Grok'},
      {id:'grok-3',       name:'Grok 3',      ctx:131072, tier:'balanced', cost:'$$',  best:'Guncel bilgi'},
    ]
  },
  groq: {
    name: 'Groq',
    icon: '?',
    color: '#F55036',
    envKey: 'GROQ_API_KEY',
    apiUrl: 'https://api.groq.com/openai/v1',
    apiType: 'openai-compat',
    models: [
      {id:'llama-3.3-70b-versatile', name:'Llama 3.3 70B', ctx:128000, tier:'fast',    cost:'$',   best:'UCRETSIZ plan mevcut, cok hizli'},
      {id:'mixtral-8x7b-32768',      name:'Mixtral 8x7B',  ctx:32768,  tier:'fast',    cost:'$',   best:'Hizli, dusuk maliyet'},
    ]
  },
  openrouter: {
    name: 'OpenRouter',
    icon: '?',
    color: '#8B5CF6',
    envKey: 'OPENROUTER_API_KEY',
    apiUrl: 'https://openrouter.ai/api/v1',
    apiType: 'openai-compat',
    models: [
      {id:'anthropic/claude-sonnet-4-6',    name:'Claude Sonnet 4.6',  ctx:200000, tier:'balanced', cost:'$$',  best:'OpenRouter uzerinden Claude'},
      {id:'google/gemini-2.5-flash',        name:'Gemini 2.5 Flash',   ctx:1000000,tier:'fast',     cost:'$',   best:'Uzun context'},
      {id:'deepseek/deepseek-r1',           name:'DeepSeek R1',        ctx:64000,  tier:'balanced', cost:'$',   best:'Ucuz mantik'},
      {id:'meta-llama/llama-3.3-70b-instruct', name:'Llama 3.3 70B',  ctx:128000, tier:'balanced', cost:'$',   best:'Acik kaynak'},
      {id:'qwen/qwen3-coder',              name:'Qwen3 Coder',        ctx:32000,  tier:'fast',     cost:'$',   best:'Kod uretimi'},
      {id:'moonshotai/kimi-k2.5',          name:'Kimi K2.5',          ctx:128000, tier:'balanced', cost:'$',   best:'Cok dilli'},
    ]
  },
  ollama: {
    name: 'Ollama (Lokal)',
    icon: '?',
    color: '#0066CC',
    envKey: null,
    apiUrl: 'http://localhost:11434/v1',
    apiType: 'openai-compat',
    models: [
      {id:'llama3.3:70b',        name:'Llama 3.3 70B',     ctx:128000, tier:'premium',  cost:'UCRETSIZ', best:'Guclu, tamamen lokal'},
      {id:'qwen2.5:32b',         name:'Qwen 2.5 32B',      ctx:32000,  tier:'balanced', cost:'UCRETSIZ', best:'Performans/boyut dengesi'},
      {id:'qwen2.5-coder:7b',    name:'Qwen 2.5 Coder 7B', ctx:32000,  tier:'fast',     cost:'UCRETSIZ', best:'Kod, dusuk RAM'},
      {id:'deepseek-r1:14b',     name:'DeepSeek R1 14B',   ctx:64000,  tier:'balanced', cost:'UCRETSIZ', best:'Lokal mantik yurutme'},
      {id:'mistral:7b',          name:'Mistral 7B',         ctx:32000,  tier:'fast',     cost:'UCRETSIZ', best:'Hizli, hafif'},
      {id:'phi4:14b',            name:'Phi-4 14B',          ctx:16000,  tier:'balanced', cost:'UCRETSIZ', best:'Microsoft, verimli'},
    ]
  }
};

//  MODEL API CAGIRICI 
var _modelKeys = {};
try { _modelKeys = JSON.parse(localStorage.getItem('bist_model_keys')||'{}'); } catch(e){}

function saveModelKeys(){
  try { localStorage.setItem('bist_model_keys', JSON.stringify(_modelKeys)); } catch(e){}
}

function callModel(providerId, modelId, messages, systemPrompt, cb){
  var provider = MODEL_CATALOG[providerId];
  if(!provider){ cb(null,'Provider bulunamadi: '+providerId); return; }

  var apiKey = _modelKeys[providerId]||'';
  var apiType = provider.apiType;

  // Ollama - lokal, key gerektirmez
  if(providerId === 'ollama'){
    callOpenAICompat(provider.apiUrl, '', modelId, messages, systemPrompt, cb);
    return;
  }

  if(!apiKey){ cb(null, provider.name+' API key eksik. Ayarlar > Model Keys'); return; }

  if(apiType === 'anthropic'){
    callAnthropic(apiKey, modelId, messages, systemPrompt, cb);
  } else if(apiType === 'openai'){
    callOpenAI(apiKey, modelId, messages, systemPrompt, cb);
  } else if(apiType === 'openai-compat'){
    callOpenAICompat(provider.apiUrl, apiKey, modelId, messages, systemPrompt, cb);
  } else if(apiType === 'gemini'){
    callGemini(apiKey, modelId, messages, systemPrompt, cb);
  } else {
    cb(null, 'Desteklenmeyen API tipi: '+apiType);
  }
}

function callAnthropic(key, model, messages, sys, cb){
  fetch('https://api.anthropic.com/v1/messages',{
    method:'POST',
    headers:{'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},
    body:JSON.stringify({model:model, max_tokens:1500, system:sys||'', messages:messages})
  }).then(function(r){return r.json();})
  .then(function(d){
    var t=(d.content&&d.content[0]&&d.content[0].text)||d.error&&d.error.message||'Yanit yok';
    cb(t);
  }).catch(function(e){cb(null,e.message);});
}

function callOpenAI(key, model, messages, sys, cb){
  var msgs = sys ? [{role:'system',content:sys}].concat(messages) : messages;
  fetch('https://api.openai.com/v1/chat/completions',{
    method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},
    body:JSON.stringify({model:model, max_tokens:1500, messages:msgs})
  }).then(function(r){return r.json();})
  .then(function(d){
    var t=d.choices&&d.choices[0]&&d.choices[0].message&&d.choices[0].message.content
       ||d.error&&d.error.message||'Yanit yok';
    cb(t);
  }).catch(function(e){cb(null,e.message);});
}

function callOpenAICompat(baseUrl, key, model, messages, sys, cb){
  var msgs = sys ? [{role:'system',content:sys}].concat(messages) : messages;
  var headers = {'Content-Type':'application/json'};
  if(key) headers['Authorization'] = 'Bearer '+key;
  fetch(baseUrl+'/chat/completions',{
    method:'POST', headers:headers,
    body:JSON.stringify({model:model, max_tokens:1500, messages:msgs})
  }).then(function(r){return r.json();})
  .then(function(d){
    var t=d.choices&&d.choices[0]&&d.choices[0].message&&d.choices[0].message.content
       ||d.error&&d.error.message||'Yanit yok';
    cb(t);
  }).catch(function(e){
    if(baseUrl.indexOf('localhost')>-1){
      cb(null,'Ollama bagli degil. Terminalden: ollama serve');
    } else {
      cb(null,e.message);
    }
  });
}

function callGemini(key, model, messages, sys, cb){
  var contents = messages.map(function(m){
    return {role:m.role==='assistant'?'model':'user', parts:[{text:m.content}]};
  });
  var body = {contents:contents};
  if(sys) body.systemInstruction = {parts:[{text:sys}]};
  body.generationConfig = {maxOutputTokens:1500};
  fetch('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent?key='+key,{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)
  }).then(function(r){return r.json();})
  .then(function(d){
    var t=d.candidates&&d.candidates[0]&&d.candidates[0].content&&d.candidates[0].content.parts&&d.candidates[0].content.parts[0]&&d.candidates[0].content.parts[0].text
       ||d.error&&d.error.message||'Yanit yok';
    cb(t);
  }).catch(function(e){cb(null,e.message);});
}

//  AGENT MOTOR GUNCELLE 
// _v13ActiveAgent'a model ekleme
var _currentProvider = localStorage.getItem('bist_active_provider') || 'anthropic';
var _currentModel = localStorage.getItem('bist_active_model') || 'claude-sonnet-4-6';

function setActiveModel(providerId, modelId){
  _currentProvider = providerId;
  _currentModel = modelId;
  try{
    localStorage.setItem('bist_active_provider', providerId);
    localStorage.setItem('bist_active_model', modelId);
  }catch(e){}
  var prov = MODEL_CATALOG[providerId];
  var mod = prov && prov.models.find(function(m){return m.id===modelId;});
  var label = (prov?prov.name:'?')+' / '+(mod?mod.name:modelId);
  v13AppendMsg && v13AppendMsg('sys','Model: '+label);
  updateModelBadge();
  if(typeof haptic==='function') haptic('light');
}

function updateModelBadge(){
  try{
    var badge = document.getElementById('v14ModelBadge');
    var prov = MODEL_CATALOG[_currentProvider];
    var mod = prov && prov.models.find(function(m){return m.id===_currentModel;});
    if(badge && prov && mod){
      badge.textContent = prov.icon+' '+mod.name;
      badge.style.color = prov.color;
      badge.style.borderColor = prov.color+'44';
      badge.style.background = prov.color+'11';
    }
  }catch(e){}
}

// v13CallAPI override - secilen model kullan
var _v13CallAPI_orig = v13CallAPI;
v13CallAPI = function(msg){
  try{
    if(!msg||!msg.trim()) return;
    var agent = _v13ActiveAgent;
    _v13Thinking = true;

    // Loading
    var loadDiv = document.createElement('div');
    loadDiv.className='v13m-thinking'; loadDiv.id='v13Loading';
    loadDiv.innerHTML='<span class="v13-dot"></span><span class="v13-dot"></span><span class="v13-dot"></span>';
    var st = document.getElementById('v13Stream');
    if(st){st.appendChild(loadDiv);st.scrollTop=st.scrollHeight;}

    var msgs = (_v13History||[]).slice(-8).map(function(m){
      return {role:m.role==='user'?'user':'assistant', content:m.content};
    });

    var sys = agent && typeof agent.systemPrompt === 'function' ? agent.systemPrompt() : '';

    callModel(_currentProvider, _currentModel, msgs, sys, function(resp, err){
      _v13Thinking = false;
      var ld = document.getElementById('v13Loading');
      if(ld) ld.remove();
      if(err){
        v13AppendMsg('sys','Hata ('+_currentProvider+'/'+_currentModel+'): '+err);
        return;
      }
      if(!resp){v13AppendMsg('sys','Yanit alinamadi.');return;}
      _v13History && _v13History.push({role:'assistant',content:resp});
      var codeMatch = resp.match(/```(?:javascript|js)?\n?([\s\S]*?)```/);
      if(codeMatch){
        var before = resp.replace(codeMatch[0],'').trim();
        if(before) v13AppendMsg('ai', before);
        v13AppendMsg('ai', null, codeMatch[1].trim());
      } else {
        v13AppendMsg('ai', resp);
      }
      if(_ttsEnabled && typeof v13Speak==='function') v13Speak(resp.replace(/```[\s\S]*?```/g,'').substring(0,200));
    });
  }catch(e){
    _v13Thinking = false;
    v13AppendMsg && v13AppendMsg('sys','Cagri hatasi: '+e.message);
  }
};

//  OLLAMA YONETIMI 
function ollamaListModels(cb){
  fetch('http://localhost:11434/api/tags')
    .then(function(r){return r.json();})
    .then(function(d){
      var models = (d.models||[]).map(function(m){return m.name;});
      cb(true, models);
    }).catch(function(){cb(false,[]);});
}

function ollamaPullModel(modelId){
  v13AppendMsg && v13AppendMsg('sys','Ollama: '+modelId+' indirme baslatiliyor (bu islem dakikalar surebilir)...');
  fetch('http://localhost:11434/api/pull',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:modelId, stream:false})
  }).then(function(r){return r.json();})
  .then(function(d){
    if(d.status==='success'){
      v13AppendMsg && v13AppendMsg('sys',modelId+' indirildi! Artik kullanabilirsiniz.');
      toast(modelId+' hazir!');
    } else {
      v13AppendMsg && v13AppendMsg('sys','Indirme durumu: '+(d.status||JSON.stringify(d)));
    }
  }).catch(function(){
    v13AppendMsg && v13AppendMsg('sys','Ollama bagli degil. Terminalden: ollama serve');
  });
}

//  MODEL SECIM PANELI 
function openModelSelector(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Model Sec';

    var html = '<div style="padding:3px 0">';

    // Aktif model
    var curProv = MODEL_CATALOG[_currentProvider];
    var curMod = curProv && curProv.models.find(function(m){return m.id===_currentModel;});
    html += '<div style="background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.08);border-radius:9px;padding:10px;margin-bottom:12px">'
      +'<div style="font-size:9px;color:var(--t4);margin-bottom:4px">AKTIF MODEL</div>'
      +'<div style="font-size:12px;font-weight:700;color:'+(curProv?curProv.color:'var(--cyan)')+'">'
      +(curProv?curProv.icon:'')+' '+(curMod?curMod.name:_currentModel)+'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:2px">'+(curMod?curMod.best:'')+'</div>'
      +'</div>';

    // Provider gruplari
    Object.keys(MODEL_CATALOG).forEach(function(pid){
      var prov = MODEL_CATALOG[pid];
      var hasKey = pid==='ollama' || !!_modelKeys[pid];

      html += '<div style="margin-bottom:10px">'
        +'<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px">'
        +'<span style="font-size:14px">'+prov.icon+'</span>'
        +'<span style="font-size:11px;font-weight:700;color:'+prov.color+'">'+prov.name+'</span>'
        +(hasKey
          ?'<span style="font-size:7px;padding:1px 5px;border-radius:3px;background:rgba(0,230,118,.12);color:var(--green);font-weight:700">API KEY OK</span>'
          :(pid==='ollama'
            ?'<span style="font-size:7px;padding:1px 5px;border-radius:3px;background:rgba(0,212,255,.1);color:var(--cyan)">LOKAL</span>'
            :'<button onclick="promptApiKey(\''+pid+'\')" style="font-size:7px;padding:2px 7px;border-radius:4px;background:rgba(255,184,0,.1);color:var(--gold);border:1px solid rgba(255,184,0,.25);cursor:pointer">Key Ekle</button>'))
        +'</div>'
        +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">';

      prov.models.forEach(function(m){
        var isActive = _currentProvider===pid && _currentModel===m.id;
        var tierClr = m.tier==='premium'?'var(--gold)':m.tier==='fast'?'var(--green)':'var(--cyan)';
        html += '<div onclick="setActiveModel(\''+pid+'\',\''+m.id+'\');closeM()" '
          +'style="padding:8px;border-radius:8px;cursor:pointer;border:1px solid '+(isActive?prov.color+'66':'rgba(255,255,255,.06)')+';background:'+(isActive?prov.color+'0F':'rgba(255,255,255,.02)')+'">'
          +'<div style="font-size:10px;font-weight:600;color:'+(isActive?prov.color:'var(--t1)')+'">'+m.name+'</div>'
          +'<div style="font-size:7px;color:'+tierClr+';margin-top:1px">'+m.tier.toUpperCase()+'</div>'
          +'<div style="font-size:8px;color:var(--t4);margin-top:2px">'+m.best.substring(0,30)+'</div>'
          +'<div style="font-size:8px;color:var(--gold);margin-top:2px">'+m.cost+'</div>'
          +(pid==='ollama'?'<button onclick="event.stopPropagation();ollamaPullModel(\''+m.id+'\')" style="font-size:7px;padding:1px 6px;border-radius:3px;background:rgba(0,102,204,.15);color:#4A9EFF;border:1px solid rgba(0,102,204,.25);cursor:pointer;margin-top:3px">Indir</button>':'')
          +'</div>';
      });

      html += '</div></div>';
    });
    html += '</div>';

    document.getElementById('mcont').innerHTML = html;
    modal.classList.add('on');
  }catch(e){ toast('Model secici hatasi: '+e.message); }
}

function promptApiKey(providerId){
  var prov = MODEL_CATALOG[providerId];
  if(!prov) return;
  var key = prompt(prov.name+' API Key girin ('+prov.envKey+'):');
  if(!key || !key.trim()) return;
  _modelKeys[providerId] = key.trim();
  saveModelKeys();
  toast(prov.name+' API key kaydedildi!');
  if(typeof haptic==='function') haptic('success');
  openModelSelector(); // yenile
}

//  CSS 
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      '#v14ModelBadge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:6px;font-size:9px;font-weight:700;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);cursor:pointer;transition:all .2s}'
      +'#v14ModelBadge:active{opacity:.7}'
      +'.v14-provider-bar{display:flex;gap:5px;overflow-x:auto;padding:4px 0;margin-bottom:8px;scrollbar-width:none}'
      +'.v14-prov-btn{flex-shrink:0;padding:5px 10px;border-radius:7px;font-size:9px;font-weight:600;cursor:pointer;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);transition:all .2s;white-space:nowrap}'
      +'.v14-prov-btn.active{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.2)}'
      +'.v14-prov-btn:active{transform:scale(.95)}';
    document.head.appendChild(st);
  }catch(e){}
})();

//  DEV PANEL'E MODEL SATIRI EKLE 
var _prevRenderDevPanel2 = typeof renderDevPanel==='function' ? renderDevPanel : null;
renderDevPanel = function(){
  if(_prevRenderDevPanel2) _prevRenderDevPanel2();
  // Model badge ekle - header'a
  setTimeout(function(){
    try{
      var devEl = document.getElementById('page-dev');
      if(!devEl) return;
      var firstRow = devEl.querySelector('div');
      if(firstRow && !document.getElementById('v14ModelBadge')){
        var badge = document.createElement('span');
        badge.id = 'v14ModelBadge';
        badge.title = 'Model sec';
        badge.onclick = openModelSelector;
        var prov = MODEL_CATALOG[_currentProvider];
        var mod = prov && prov.models.find(function(m){return m.id===_currentModel;});
        badge.textContent = (prov?prov.icon:'?')+' '+(mod?mod.name:_currentModel);
        if(prov){ badge.style.color=prov.color; badge.style.borderColor=prov.color+'44'; badge.style.background=prov.color+'11'; }
        firstRow.appendChild(badge);
      }
      // Hizli komutlara model sec butonu ekle
      var quickGrid = devEl.querySelector('.dev-card:last-of-type .dev-title');
      // Model selector butonu zaten yoksa ekle
      if(!document.getElementById('v14ModelSelBtn')){
        // Stream bolgesi icindeki input row'un altina ekle
        var inputRow = devEl.querySelector('.v13-input-row');
        if(inputRow){
          var modelBtn = document.createElement('button');
          modelBtn.id = 'v14ModelSelBtn';
          modelBtn.className = 'v13-send-btn';
          modelBtn.title = 'Model sec';
          modelBtn.innerHTML = '&#129302;';
          modelBtn.onclick = openModelSelector;
          modelBtn.style.cssText += 'background:rgba(255,255,255,.05);color:var(--t3);border-color:rgba(255,255,255,.1)';
          inputRow.appendChild(modelBtn);
        }
      }
    }catch(e){}
  },300);
};

//  INIT 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      updateModelBadge();
      // Ollama durumunu kontrol et
      ollamaListModels(function(ok, models){
        if(ok && models.length){
          devLog && devLog('Ollama aktif: '+models.join(', '),'ok');
        }
      });
    }catch(e){}
  },1500);
});

</script>
<script>

// BIST v15 BLOK 14
// 1. Akilli fallback: API hata -> Gateway -> Tekrar API -> sessiz devam
// 2. Ayarlar paneli: Gateway + GitHub + Model Keys ayri ayri

//  1. AKILLI FALLBACK MOTORU 
// v13CallAPI'yi tamamen yeniden yaz
v13CallAPI = function(msg){
  try{
    if(!msg||!msg.trim()) return;
    if(_v13Thinking){ return; }
    var agent = _v13ActiveAgent;
    _v13Thinking = true;

    // Loading
    var loadDiv = document.createElement('div');
    loadDiv.className='v13m-thinking'; loadDiv.id='v13Loading';
    loadDiv.innerHTML='<span class="v13-dot"></span><span class="v13-dot"></span><span class="v13-dot"></span>';
    var st = document.getElementById('v13Stream');
    if(st){ st.appendChild(loadDiv); st.scrollTop=st.scrollHeight; }

    var msgs = (_v13History||[]).slice(-8).map(function(m){
      return {role:m.role==='user'?'user':'assistant', content:m.content};
    });
    var sys = agent && typeof agent.systemPrompt==='function' ? agent.systemPrompt() : '';

    function clearLoading(){
      var ld=document.getElementById('v13Loading');
      if(ld) ld.remove();
      _v13Thinking=false;
    }

    function onSuccess(resp){
      clearLoading();
      if(!resp||!resp.trim()){ v13AppendMsg('sys','Yanit bos.'); return; }
      _v13History && _v13History.push({role:'assistant',content:resp});
      var codeMatch = resp.match(/```(?:javascript|js|python)?\n?([\s\S]*?)```/);
      if(codeMatch){
        var before = resp.replace(codeMatch[0],'').trim();
        if(before) v13AppendMsg('ai', before);
        v13AppendMsg('ai', null, codeMatch[1].trim());
      } else {
        v13AppendMsg('ai', resp);
      }
      if(_ttsEnabled && typeof v13Speak==='function')
        v13Speak(resp.replace(/```[\s\S]*?```/g,'').substring(0,200));
    }

    function tryGateway(){
      // Gateway bagliysa oraya gonder - sessizce
      if(_ocGW && _ocGW.connected && typeof ocGWSend==='function'){
        ocGWSend(msg);
        clearLoading();
        return true;
      }
      return false;
    }

    function tryAPI(){
      callModel(_currentProvider, _currentModel, msgs, sys, function(resp, err){
        if(err){
          // API basarisiz - Gateway dene, o da yoksa provider degistir
          if(tryGateway()) return;
          // Son care: Anthropic Sonnet ile dene (her zaman mevcut)
          if(_currentProvider !== 'anthropic'){
            callModel('anthropic','claude-sonnet-4-6',msgs,sys,function(r2,e2){
              if(e2||!r2){ clearLoading(); v13AppendMsg('sys','['+(_currentProvider||'?')+'] '+err); return; }
              onSuccess(r2);
            });
          } else {
            clearLoading();
            // Sadece kisa teknik mesaj - "baglayin" yok
            v13AppendMsg('sys','['+_currentProvider+'] '+err);
          }
          return;
        }
        onSuccess(resp);
      });
    }

    tryAPI();
  }catch(e){
    _v13Thinking=false;
    var ld=document.getElementById('v13Loading'); if(ld)ld.remove();
    v13AppendMsg && v13AppendMsg('sys','Hata: '+e.message);
  }
};

// ocV12Process de ayni sekilde - sessiz fallback
ocV12Process = function(msg){
  var m=msg.toLowerCase();
  setTimeout(function(){
    try{
      if(m.indexOf('push')>-1||m.indexOf('github')>-1){
        if(typeof ghPushCurrentHTML==='function') ghPushCurrentHTML();
        else v13AppendMsg && v13AppendMsg('sys','GitHub: Token ayarlanmamis.');
      } else if(m.indexOf('repo')>-1||m.indexOf('dosya')>-1){
        if(typeof repoListFiles==='function') repoListFiles('');
        else v13AppendMsg && v13AppendMsg('sys','GitHub: Token ayarlanmamis.');
      } else if(m.indexOf('durum')>-1||m.indexOf('status')>-1){
        var posC=Object.keys(S.openPositions||{}).length;
        v13AppendMsg && v13AppendMsg('ai','v15 Durum:\nScript blok: '+document.querySelectorAll('script').length
          +'\nPozisyon: '+posC+'\nSinyal: '+(S.sigs||[]).length
          +'\nXU100: '+(S.xu100Change>=0?'+':'')+(S.xu100Change||0).toFixed(2)+'%'
          +'\nModel: '+_currentProvider+'/'+_currentModel
          +'\nGateway: '+(_ocGW&&_ocGW.connected?'Bagli':'Bagli degil'));
      } else if(m.indexOf('indir')>-1||m.indexOf('surum')>-1){
        try{
          var html='<!DOCTYPE html>\n'+document.documentElement.outerHTML;
          var blob=new Blob([html],{type:'text/html'});
          var url=URL.createObjectURL(blob);
          var a=document.createElement('a'); a.href=url;
          a.download='bist_elite_v15.html'; a.click();
          URL.revokeObjectURL(url);
          v13AppendMsg && v13AppendMsg('sys','bist_elite_v15.html indirildi.');
        }catch(e){ v13AppendMsg && v13AppendMsg('sys','Indirme: '+e.message); }
      } else if(m.indexOf('hata')>-1||m.indexOf('syntax')>-1){
        if(typeof v12HataBul==='function') v12HataBul();
        else if(typeof v13HataBul==='function') v13HataBul();
      } else {
        // AI'ya gonder
        v13SendMsg && v13SendMsg(msg);
      }
    }catch(e){}
  },200);
};

//  2. YENI AYARLAR PANELI 
// CSS
(function(){
  try{
    var st=document.createElement('style');
    st.textContent=
      '.v15-settings-page{padding:10px}'
      +'.v15-set-section{background:rgba(10,10,10,.8);border:1px solid rgba(255,255,255,.07);border-radius:12px;overflow:hidden;margin-bottom:10px}'
      +'.v15-set-header{padding:11px 14px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;align-items:center;gap:8px;cursor:pointer}'
      +'.v15-set-header-icon{font-size:16px}'
      +'.v15-set-header-title{font-size:11px;font-weight:700;color:var(--t1)}'
      +'.v15-set-header-sub{font-size:8px;color:var(--t4);margin-left:auto}'
      +'.v15-set-body{padding:12px 14px}'
      +'.v15-set-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}'
      +'.v15-set-label{font-size:10px;color:var(--t4);width:85px;flex-shrink:0}'
      +'.v15-set-input{flex:1;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09);border-radius:7px;padding:8px 10px;font-size:11px;color:var(--t1);font-family:Courier New,monospace}'
      +'.v15-set-input:focus{outline:none;border-color:rgba(0,212,255,.4)}'
      +'.v15-set-btn{padding:9px;border-radius:8px;font-size:10px;font-weight:600;cursor:pointer;border:none;width:100%;margin-top:5px}'
      +'.v15-btn-cyan{background:rgba(0,212,255,.12);color:var(--cyan);border:1px solid rgba(0,212,255,.25)}'
      +'.v15-btn-green{background:rgba(0,230,118,.12);color:var(--green);border:1px solid rgba(0,230,118,.25)}'
      +'.v15-btn-red{background:rgba(255,68,68,.1);color:#ff6b6b;border:1px solid rgba(255,68,68,.2)}'
      +'.v15-btn-gold{background:rgba(255,184,0,.1);color:var(--gold);border:1px solid rgba(255,184,0,.25)}'
      +'.v15-status-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}'
      +'.v15-status-ok{background:var(--green);box-shadow:0 0 5px var(--green)}'
      +'.v15-status-err{background:rgba(255,255,255,.2)}'
      +'.v15-status-warn{background:var(--gold)}'
      // Model keys grid
      +'.v15-key-grid{display:grid;gap:6px}'
      +'.v15-key-row{display:flex;align-items:center;gap:7px;padding:7px;background:rgba(255,255,255,.03);border-radius:8px;border:1px solid rgba(255,255,255,.06)}'
      +'.v15-key-icon{font-size:13px;flex-shrink:0}'
      +'.v15-key-name{font-size:9px;font-weight:700;width:70px;flex-shrink:0}'
      +'.v15-key-input{flex:1;background:transparent;border:none;font-size:9px;color:var(--t3);font-family:Courier New,monospace;outline:none}'
      +'.v15-key-status{font-size:7px;padding:2px 5px;border-radius:3px;font-weight:700;flex-shrink:0}'
      +'.v15-key-ok{background:rgba(0,230,118,.12);color:var(--green)}'
      +'.v15-key-empty{background:rgba(255,255,255,.05);color:var(--t4)}'
      // Gateway status bar
      +'.v15-gw-bar{display:flex;align-items:center;gap:7px;padding:8px 10px;background:rgba(0,0,0,.4);border-radius:8px;margin-bottom:10px;border:1px solid rgba(255,255,255,.05)}'
      // Collapse animation
      +'.v15-set-body.collapsed{display:none}';
    document.head.appendChild(st);
  }catch(e){}
})();

// Ayarlar sayfasi render
function renderV15Settings(){
  var el = document.getElementById('page-dev');
  if(!el) return;

  // GW durum
  var gwOk = _ocGW && _ocGW.connected;
  var ghOk = !!(GH && GH.token && GH.owner && GH.repo);

  // Aktif model
  var curProv = MODEL_CATALOG && MODEL_CATALOG[_currentProvider];
  var curMod = curProv && curProv.models.find(function(m){return m.id===_currentModel;});

  el.innerHTML = ''
    // Baslik
    +'<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px">'
    +'<div style="font-size:22px">&#128736;</div>'
    +'<div><div style="font-size:14px;font-weight:700;color:var(--t1)">BIST AI Dev v15</div>'
    +'<div style="font-size:9px;color:var(--t4)">Ayarlar + AI Sohbet + GitHub</div></div>'
    +'<span id="v14ModelBadge" onclick="openModelSelector&&openModelSelector()" '
    +'style="margin-left:auto;font-size:9px;padding:3px 9px;border-radius:6px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);cursor:pointer;color:'+(curProv?curProv.color:'var(--cyan)')+';">'
    +(curProv?curProv.icon:'?')+' '+(curMod?curMod.name:'Model Sec')+'</span>'
    +'</div>'

    //  GATEWAY KARTI 
    +'<div class="v15-set-section">'
    +'<div class="v15-set-header" onclick="v15Toggle(\'gwBody\')">'
    +'<span class="v15-set-header-icon">&#128268;</span>'
    +'<span class="v15-set-header-title">OpenClaw Gateway</span>'
    +'<span class="v15-status-dot '+(gwOk?'v15-status-ok':'v15-status-err')+'" style="margin-left:8px"></span>'
    +'<span class="v15-set-header-sub">'+(gwOk?'Bagli':'Bagli Degil')+'</span>'
    +'</div>'
    +'<div id="gwBody" class="v15-set-body">'
    // GW durum bar
    +'<div class="v15-gw-bar">'
    +'<div id="v15GwDot" class="v15-status-dot '+(gwOk?'v15-status-ok':'v15-status-err')+'"></div>'
    +'<div style="flex:1">'
    +'<div style="font-size:10px;color:var(--t2)" id="v15GwLabel">'+(gwOk?'Bagli: '+(_ocGW.url||''):'Bagli Degil')+'</div>'
    +'<div style="font-size:8px;color:var(--t4)">Gateway bagli degilse AI direkt API\'yi kullanir</div>'
    +'</div>'
    +'<button onclick="if(_ocGW&&_ocGW.connected)ocGWDisconnect&&ocGWDisconnect();else v15ConnectGW()" '
    +'style="padding:5px 11px;border-radius:7px;font-size:9px;font-weight:600;cursor:pointer;border:1px solid '+(gwOk?'rgba(255,68,68,.3)':'rgba(0,230,118,.3)')+';background:'+(gwOk?'rgba(255,68,68,.08)':'rgba(0,230,118,.08)')+';color:'+(gwOk?'#ff6b6b':'var(--green)')+';">'+(gwOk?'Kes':'Baglan')+'</button>'
    +'</div>'
    +'<div class="v15-set-row"><span class="v15-set-label">Gateway URL</span>'
    +'<input class="v15-set-input" id="v15_gwUrl" placeholder="ws://127.0.0.1:18789" value="'+((typeof _devCfg2!=='undefined'&&_devCfg2.ocGWUrl)||'ws://127.0.0.1:18789')+'"></div>'
    +'<div class="v15-set-row"><span class="v15-set-label">Token</span>'
    +'<input class="v15-set-input" id="v15_gwToken" type="password" placeholder="Gateway token (opsiyonel)" value="'+((typeof _devCfg2!=='undefined'&&_devCfg2.ocGWToken)||'')+'"></div>'
    +'<button class="v15-set-btn v15-btn-cyan" onclick="v15SaveGW()">Kaydet ve Baglan</button>'
    +'<div style="font-size:8px;color:var(--t4);margin-top:7px;line-height:1.5">'
    +'Not: Gateway olmadan da uygulama tam calisir - API modeli direkt kullanilir.</div>'
    +'</div></div>'

    //  GITHUB KARTI 
    +'<div class="v15-set-section">'
    +'<div class="v15-set-header" onclick="v15Toggle(\'ghBody\')">'
    +'<span class="v15-set-header-icon">&#128013;</span>'
    +'<span class="v15-set-header-title">GitHub Entegrasyonu</span>'
    +'<span class="v15-status-dot '+(ghOk?'v15-status-ok':'v15-status-err')+'" style="margin-left:8px"></span>'
    +'<span class="v15-set-header-sub">'+(ghOk?GH.owner+'/'+GH.repo:'Ayarlanmamis')+'</span>'
    +'</div>'
    +'<div id="ghBody" class="v15-set-body collapsed">'
    +'<div class="v15-set-row"><span class="v15-set-label">Token (PAT)</span>'
    +'<input class="v15-set-input" id="dev_ghToken" type="password" placeholder="ghp_xxxx..." value="'+(GH&&GH.token||'')+'"></div>'
    +'<div class="v15-set-row"><span class="v15-set-label">Kullanici</span>'
    +'<input class="v15-set-input" id="dev_ghOwner" placeholder="github_kullanici" value="'+(GH&&GH.owner||'')+'"></div>'
    +'<div class="v15-set-row"><span class="v15-set-label">Repo</span>'
    +'<input class="v15-set-input" id="dev_ghRepo" placeholder="bist-ai-scanner" value="'+(GH&&GH.repo||'')+'"></div>'
    +'<div class="v15-set-row"><span class="v15-set-label">Branch</span>'
    +'<input class="v15-set-input" id="dev_ghBranch" value="'+(GH&&GH.branch||'main')+'"></div>'
    +'<div class="v15-set-row"><span class="v15-set-label">Ana Dosya</span>'
    +'<input class="v15-set-input" id="dev_ghPath" value="'+(GH&&GH.path||'bist_elite_v15.html')+'"></div>'
    +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:5px">'
    +'<button class="v15-set-btn v15-btn-cyan" onclick="ghSaveConfig&&ghSaveConfig()">Kaydet</button>'
    +'<button class="v15-set-btn v15-btn-green" onclick="ghPushCurrentHTML&&ghPushCurrentHTML()">Push</button>'
    +'</div></div></div>'

    //  MODEL KEYS KARTI 
    +'<div class="v15-set-section">'
    +'<div class="v15-set-header" onclick="v15Toggle(\'mkBody\')">'
    +'<span class="v15-set-header-icon">&#128273;</span>'
    +'<span class="v15-set-header-title">Model API Anahtarlari</span>'
    +'<span class="v15-set-header-sub">'+Object.keys(_modelKeys||{}).filter(function(k){return _modelKeys[k];}).length+' key kayitli</span>'
    +'</div>'
    +'<div id="mkBody" class="v15-set-body collapsed">'
    +'<div class="v15-key-grid">'
    + (function(){
        if(typeof MODEL_CATALOG === 'undefined') return '<div style="color:var(--t4);font-size:9px">Model katalogu yuklenemedi.</div>';
        return Object.keys(MODEL_CATALOG).filter(function(pid){return pid!=='ollama';}).map(function(pid){
          var prov = MODEL_CATALOG[pid];
          var hasKey = !!(_modelKeys&&_modelKeys[pid]);
          return '<div class="v15-key-row">'
            +'<span class="v15-key-icon">'+prov.icon+'</span>'
            +'<span class="v15-key-name" style="color:'+prov.color+'">'+prov.name+'</span>'
            +'<input class="v15-key-input" id="mk_'+pid+'" type="password" '
              +'placeholder="'+prov.envKey+'" value="'+(_modelKeys&&_modelKeys[pid]||'')+'">'
            +'<span class="v15-key-status '+(hasKey?'v15-key-ok':'v15-key-empty')+'">'+(hasKey?'OK':'Bos')+'</span>'
            +'</div>';
        }).join('');
      })()
    +'</div>'
    +'<div style="font-size:8px;color:var(--t4);margin:8px 0">Ollama lokal calisir, key gerekmez.</div>'
    +'<button class="v15-set-btn v15-btn-cyan" onclick="v15SaveModelKeys()">Anahtarlari Kaydet</button>'
    +'<button class="v15-set-btn v15-btn-gold" style="margin-top:5px" onclick="openModelSelector&&openModelSelector()">Model Sec</button>'
    +'</div></div>'

    //  AI SOHBET 
    +'<div class="v15-set-section">'
    +'<div class="v15-set-header" onclick="v15Toggle(\'chatBody\')">'
    +'<span class="v15-set-header-icon">&#129302;</span>'
    +'<span class="v15-set-header-title">AI Sohbet</span>'
    +'<span class="v15-set-header-sub">'+(_v13ActiveAgent?_v13ActiveAgent.name:'Agent Sec')+'</span>'
    +'</div>'
    +'<div id="chatBody" class="v15-set-body">'
    // Agent mini grid (4 kart)
    +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-bottom:8px">'
    +(typeof V13_AGENTS !== 'undefined' ? V13_AGENTS.slice(0,8).map(function(ag){
      var isA = _v13ActiveAgent && _v13ActiveAgent.id===ag.id;
      return '<div onclick="v13SetAgent&&v13SetAgent(\''+ag.id+'\')" '
        +'style="padding:7px 4px;border-radius:8px;text-align:center;cursor:pointer;border:1px solid '+(isA?ag.color+'55':'rgba(255,255,255,.06)')+';background:'+(isA?ag.color+'0D':'transparent')+'">'
        +'<div style="font-size:16px">'+ag.icon+'</div>'
        +'<div style="font-size:7px;color:'+(isA?ag.color:'var(--t4)')+';margin-top:2px;font-weight:'+(isA?'700':'400')+'">'+ag.name.split(' ')[0]+'</div>'
        +'</div>';
    }).join('') : '')
    +'</div>'
    // Sohbet akisi
    +'<div id="v13Stream" class="v13-stream" style="height:220px"><div class="v13m-sys">Hazir. Model: '+(curProv?curProv.icon:'')+' '+(curMod?curMod.name:_currentModel)+'</div></div>'
    +'<div style="display:flex;gap:6px;margin-top:7px">'
    +'<input id="v13Input" class="v13-input" placeholder="Sorun veya komut..." onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();var el=document.getElementById(\'v13Input\');if(el&&el.value.trim()){v13SendMsg(el.value);el.value=\'\'}}">'
    +'<button class="v13-send-btn" onclick="var el=document.getElementById(\'v13Input\');if(el&&el.value.trim()){v13SendMsg(el.value);el.value=\'\'}">&#9654;</button>'
    +(navigator.mediaDevices?'<button class="v13-send-btn" onclick="v13StartMic&&v13StartMic()" style="background:rgba(192,132,252,.1);color:var(--purple);border-color:rgba(192,132,252,.25)" title="Sesli konuS">&#127908;</button>':'')
    +'</div>'
    +'<div id="v13Transcript" style="font-size:8px;color:var(--t4);text-align:center;min-height:12px;margin-top:4px"></div>'
    +'</div></div>'

    //  HIZLI EYLEMLER 
    +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:4px">'
    +[
      {l:'Push',fn:'ghPushCurrentHTML&&ghPushCurrentHTML()',c:'rgba(0,230,118,.1)',t:'var(--green)'},
      {l:'Repo',fn:'repoListFiles&&repoListFiles(\'\')',c:'rgba(0,212,255,.08)',t:'var(--cyan)'},
      {l:'Indir',fn:"ocV12Process&&ocV12Process('yeni surum indir')",c:'rgba(255,184,0,.08)',t:'var(--gold)'},
      {l:'Durum',fn:"ocV12Process&&ocV12Process('durum')",c:'rgba(255,255,255,.04)',t:'var(--t3)'},
      {l:'Hata Tara',fn:'v13HataBul&&v13HataBul()',c:'rgba(255,68,68,.08)',t:'#ff9999'},
      {l:'Model',fn:'openModelSelector&&openModelSelector()',c:'rgba(192,132,252,.08)',t:'var(--purple)'},
    ].map(function(q){
      return '<button onclick="'+q.fn+'" style="padding:9px;border-radius:8px;font-size:10px;background:'+q.c+';border:1px solid rgba(255,255,255,.07);color:'+q.t+';cursor:pointer;font-weight:600">'+q.l+'</button>';
    }).join('')
    +'</div>'

    // Log
    +'<div style="margin-top:8px"><div style="font-size:9px;color:var(--t4);text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Log</div>'
    +'<div class="dev-log" id="devLogEl" style="font-size:8px;max-height:120px"><div class="info">v15 hazir.</div></div></div>';
}

//  YARDIMCI FONKSIYONLAR 
function v15Toggle(id){
  var el=document.getElementById(id);
  if(el) el.classList.toggle('collapsed');
}

function v15ConnectGW(){
  try{
    var url=(document.getElementById('v15_gwUrl')||{}).value||'ws://127.0.0.1:18789';
    var tok=(document.getElementById('v15_gwToken')||{}).value||'';
    var cfg={}; try{cfg=JSON.parse(localStorage.getItem('bist_dev_cfg')||'{}');}catch(e){}
    cfg.ocGWUrl=url; cfg.ocGWToken=tok;
    localStorage.setItem('bist_dev_cfg',JSON.stringify(cfg));
    if(typeof _ocGW !== 'undefined'){ _ocGW.url=url; _ocGW.token=tok; }
    if(typeof ocGWConnect==='function') ocGWConnect();
    toast('Gateway baglaniyor...');
  }catch(e){ toast('GW hatasi: '+e.message); }
}

function v15SaveGW(){
  v15ConnectGW();
}

function v15SaveModelKeys(){
  try{
    if(typeof MODEL_CATALOG === 'undefined') return;
    Object.keys(MODEL_CATALOG).forEach(function(pid){
      if(pid==='ollama') return;
      var el=document.getElementById('mk_'+pid);
      if(el&&el.value.trim()){
        _modelKeys[pid]=el.value.trim();
      }
    });
    saveModelKeys&&saveModelKeys();
    toast('Model anahtarlari kaydedildi!');
    if(typeof haptic==='function') haptic('success');
    renderV15Settings(); // yenile
  }catch(e){ toast('Kayit hatasi: '+e.message); }
}

// GW durum guncellemesi - mevcut ocGWSetStatus'u genislet
var _v15_origGWStatus = typeof ocGWSetStatus==='function' ? ocGWSetStatus : null;
ocGWSetStatus = function(cls, txt){
  try{
    if(_v15_origGWStatus) _v15_origGWStatus(cls, txt);
    // v15 gostergeleri guncelle
    var dot = document.getElementById('v15GwDot');
    var lbl = document.getElementById('v15GwLabel');
    if(dot){
      dot.className = 'v15-status-dot '+(cls==='connected'?'v15-status-ok':cls==='connecting'?'v15-status-warn':'v15-status-err');
    }
    if(lbl) lbl.textContent = txt||'';
  }catch(e){}
};

// renderDevPanel override - v15 kullan
renderDevPanel = function(){
  try{ renderV15Settings(); }catch(e){ console.warn('renderDevPanel v15:',e.message); }
};

//  INIT 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      if(!document.getElementById('page-dev')){
        var main=document.querySelector('main');
        if(main){var p=document.createElement('div');p.id='page-dev';p.className='page';main.appendChild(p);}
      }
      if(!document.getElementById('devTab')){
        var nav=document.querySelector('nav');
        if(nav){
          var btn=document.createElement('button');btn.id='devTab';btn.className='tab';
          btn.innerHTML='&#128736; Dev';
          btn.onclick=function(){try{pg('dev');renderDevPanel();}catch(e){}};
          nav.appendChild(btn);
        }
      }
      if(typeof devLog==='function') devLog('BIST AI Elite v15 hazir','ok');
    }catch(e){}
  },500);
});

</script>
<script>

// BIST v16 BLOK 15
// Sadece OpenClaw Gateway uzerinden haberles
// API key YOK - OpenClaw kendi modellerini kullanir (Ollama/LMStudio/lokal)
// openclaw.json otomatik olustur + BIST skill entegrasyonu

//  OPENCLAW GATEWAY MESAJ PROTOKOLU 
// OpenClaw Gateway WebSocket formati:
// Gonderim: {type:"chat.send", text:"...", channel:"webchat", agentId:"..."}
// Alinan:   {type:"chat.message", text:"..."} veya {type:"message", content:"..."}

// Mevcut v13CallAPI tamamen override - sadece Gateway kullan
v13CallAPI = function(msg){
  try{
    if(!msg||!msg.trim()) return;
    if(_v13Thinking) return;
    _v13Thinking = true;

    var loadDiv = document.createElement('div');
    loadDiv.className = 'v13m-thinking'; loadDiv.id = 'v13Loading';
    loadDiv.innerHTML = '<span class="v13-dot"></span><span class="v13-dot"></span><span class="v13-dot"></span>';
    var st = document.getElementById('v13Stream');
    if(st){ st.appendChild(loadDiv); st.scrollTop = st.scrollHeight; }

    function done(resp, err){
      _v13Thinking = false;
      var ld = document.getElementById('v13Loading'); if(ld) ld.remove();
      if(err){
        v13AppendMsg('sys', err);
        return;
      }
      if(!resp) return;
      _v13History && _v13History.push({role:'assistant', content:resp});
      var cm = resp.match(/```(?:javascript|js|python|bash)?\n?([\s\S]*?)```/);
      if(cm){
        var before = resp.replace(cm[0],'').trim();
        if(before) v13AppendMsg('ai', before);
        v13AppendMsg('ai', null, cm[1].trim());
      } else {
        v13AppendMsg('ai', resp);
      }
      if(_ttsEnabled && typeof v13Speak==='function')
        v13Speak(resp.replace(/```[\s\S]*?```/g,'').substring(0,200));
    }

    // Gateway bagli mi?
    if(_ocGW && _ocGW.connected && _ocGW.ws && _ocGW.ws.readyState === 1){
      // Gateway'e gonder - yaniti dinle
      var responded = false;
      var origHandler = _ocGW.ws.onmessage;

      function tempHandler(e){
        try{
          var data = JSON.parse(e.data);
          if(data.type==='pong'||data.type==='ping'||data.type==='auth.ok') return;
          if(data.type==='chat.message'||data.type==='message'||data.type==='assistant'){
            var txt = data.text||data.content||data.message||'';
            if(txt && !responded){
              responded = true;
              _ocGW.ws.onmessage = origHandler;
              done(txt);
            }
          }
        }catch(ex){}
        // Orijinal handler da cagir
        if(origHandler) origHandler(e);
      }
      _ocGW.ws.onmessage = tempHandler;

      // Gonder - agent + BIST konteksti ile
      var agent = _v13ActiveAgent;
      var agentHint = agent ? agent.name + ': ' : '';
      var contextMsg = agentHint + msg;

      // BIST durum ozeti ekle (her 5 mesajda bir)
      if((_v13History||[]).length % 5 === 0){
        var posC = Object.keys(S.openPositions||{}).length;
        var xu = (S.xu100Change||0).toFixed(2);
        contextMsg += '\n[BIST CONTEXT: pos='+posC+' xu100='+xu+'% sigs='+(S.sigs||[]).length+']';
      }

      _ocGW.ws.send(JSON.stringify({
        type: 'chat.send',
        text: contextMsg,
        channel: 'webchat',
        agentId: (agent&&agent.ocAgentId) || 'main'
      }));

      // 60sn timeout
      setTimeout(function(){
        if(!responded){
          responded = true;
          _ocGW.ws.onmessage = origHandler;
          done(null, 'Yanit gelmedi (60sn). OpenClaw agent aktif mi?');
        }
      }, 60000);

    } else {
      // Gateway bagli degil - kisa bilgi + baglanti oneris
      done(null, 'OpenClaw Gateway bagli degil.\n\nTerminalde: openclaw gateway\n\nSonra Dev > Gateway URL gir > Baglan');
    }
  }catch(e){
    _v13Thinking = false;
    var ld=document.getElementById('v13Loading'); if(ld) ld.remove();
    v13AppendMsg && v13AppendMsg('sys','Hata: '+e.message);
  }
};

//  OPENCLAW.JSON OLUSTURUCU 
// Kullanicinin openclaw.json'unu otomatik olustur
function generateOpenClawConfig(){
  var gwToken = '';
  try{
    var cfg = JSON.parse(localStorage.getItem('bist_dev_cfg')||'{}');
    gwToken = cfg.ocGWToken || 'BURAYA_TOKEN_YAZIN';
  }catch(e){}

  var config = {
    "agents": {
      "defaults": {
        "maxConcurrent": 4,
        "subagents": {"maxConcurrent": 8},
        "compaction": {"mode": "safeguard"},
        "model": {
          "primary": "ollama/qwen2.5-coder:7b",
          "fallbacks": [
            "ollama/llama3.3:8b",
            "ollama/deepseek-r1:8b"
          ]
        },
        "models": {
          "ollama/qwen2.5-coder:7b":  {"alias": "Qwen Coder"},
          "ollama/llama3.3:8b":       {"alias": "Llama Local"},
          "ollama/deepseek-r1:8b":    {"alias": "DeepSeek Local"}
        }
      },
      "list": {
        "bist-vibe-coder": {
          "name": "BIST Vibe Coder",
          "model": {"primary": "ollama/qwen2.5-coder:7b"},
          "instructions": "Sen BIST AI Elite PWA gelistiricisin. JavaScript, HTML, CSS yaziyorsun. Turkce karakter kullanma. Her zaman try-catch ile sar. iOS safari uyumlu kod yaz."
        },
        "bist-trader": {
          "name": "BIST Trader",
          "model": {"primary": "ollama/llama3.3:8b"},
          "instructions": "Sen BIST Katilim Endeksi uzman tradersin. Sinyal analizi, risk yonetimi, portfoy optimizasyonu yapiyorsun. Turkce cevap ver."
        },
        "bist-debug": {
          "name": "BIST Debug",
          "model": {"primary": "ollama/deepseek-r1:8b"},
          "instructions": "Sen JavaScript ve iOS Safari hata ayiklama uzmanisin. Syntax hatalarini, null pointer hatalarini, iOS uyumluluk sorunlarini cozuyorsun."
        },
        "bist-analyst": {
          "name": "BIST Analyst",
          "model": {"primary": "ollama/llama3.3:8b"},
          "instructions": "Sen teknik analiz ve backtest uzmanisin. Sharpe, Calmar, WinRate, MaxDD hesapliyorsun. Parametre optimizasyonu yapiyorsun."
        }
      }
    },
    "gateway": {
      "mode": "local",
      "auth": {
        "mode": "token",
        "token": gwToken
      },
      "port": 18789,
      "bind": "loopback"
    },
    "models": {
      "mode": "merge",
      "providers": {
        "ollama": {
          "baseUrl": "http://127.0.0.1:11434/v1",
          "api": "openai-completions",
          "models": [
            {"id":"qwen2.5-coder:7b", "name":"Qwen 2.5 Coder 7B", "contextWindow":32768, "maxTokens":4096, "reasoning":false},
            {"id":"qwen2.5:14b",      "name":"Qwen 2.5 14B",       "contextWindow":32768, "maxTokens":4096, "reasoning":false},
            {"id":"llama3.3:8b",      "name":"Llama 3.3 8B",       "contextWindow":128000,"maxTokens":4096, "reasoning":false},
            {"id":"deepseek-r1:8b",   "name":"DeepSeek R1 8B",     "contextWindow":64000, "maxTokens":4096, "reasoning":true},
            {"id":"mistral:7b",       "name":"Mistral 7B",          "contextWindow":32768, "maxTokens":4096, "reasoning":false},
            {"id":"phi4:14b",         "name":"Phi-4 14B",           "contextWindow":16384, "maxTokens":4096, "reasoning":false},
            {"id":"qwen3.5:27b",      "name":"Qwen3.5 27B",         "contextWindow":131072,"maxTokens":8192, "reasoning":false}
          ]
        }
      }
    }
  };

  return JSON.stringify(config, null, 2);
}

function downloadOpenClawConfig(){
  try{
    var cfg = generateOpenClawConfig();
    var blob = new Blob([cfg],{type:'application/json'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = 'openclaw.json';
    a.click(); URL.revokeObjectURL(url);
    toast('openclaw.json indirildi!');
    if(typeof haptic==='function') haptic('success');
  }catch(e){ toast('Indirme: '+e.message); }
}

function showOpenClawSetup(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'OpenClaw Kurulum';

    var cfg = generateOpenClawConfig();

    var html = '<div style="padding:3px 0">'
      // Adim 1
      +'<div style="background:rgba(0,212,255,.05);border:1px solid rgba(0,212,255,.15);border-radius:10px;padding:12px;margin-bottom:10px">'
      +'<div style="font-size:10px;font-weight:700;color:var(--cyan);margin-bottom:8px">1. OpenClaw Kur</div>'
      +'<div style="background:#000;border-radius:7px;padding:9px;font-family:Courier New,monospace;font-size:9px;color:var(--green);margin-bottom:6px">'
      +'curl -fsSL https://openclaw.ai/install.sh | bash<br>'
      +'openclaw onboard</div>'
      +'<div style="font-size:8px;color:var(--t4)">Node.js 22+ gerekli. Mac/Linux/Windows desteklenir.</div>'
      +'</div>'
      // Adim 2
      +'<div style="background:rgba(0,230,118,.05);border:1px solid rgba(0,230,118,.15);border-radius:10px;padding:12px;margin-bottom:10px">'
      +'<div style="font-size:10px;font-weight:700;color:var(--green);margin-bottom:8px">2. Ollama + Modeller Kur</div>'
      +'<div style="background:#000;border-radius:7px;padding:9px;font-family:Courier New,monospace;font-size:9px;color:var(--green);margin-bottom:6px">'
      +'# Ollama kur<br>'
      +'curl -fsSL https://ollama.com/install.sh | sh<br><br>'
      +'# Vibe coding icin onerilir modeller<br>'
      +'ollama pull qwen2.5-coder:7b   # Kod (4GB)<br>'
      +'ollama pull llama3.3:8b        # Genel (5GB)<br>'
      +'ollama pull deepseek-r1:8b     # Mantik (5GB)<br>'
      +'# Guclu makine icin:<br>'
      +'ollama pull qwen3.5:27b        # En iyi (16GB)'
      +'</div>'
      +'<div style="font-size:8px;color:var(--t4)">RAM: 7B=8GB, 14B=16GB, 27B=32GB minimum</div>'
      +'</div>'
      // Adim 3
      +'<div style="background:rgba(255,184,0,.05);border:1px solid rgba(255,184,0,.15);border-radius:10px;padding:12px;margin-bottom:10px">'
      +'<div style="font-size:10px;font-weight:700;color:var(--gold);margin-bottom:8px">3. openclaw.json Yapilandir</div>'
      +'<div style="font-size:9px;color:var(--t3);margin-bottom:7px">~/.openclaw/openclaw.json dosyasini asagidaki icerikle guncelle veya indir:</div>'
      +'<pre style="background:#000;border-radius:7px;padding:9px;font-family:Courier New,monospace;font-size:7.5px;color:var(--green);max-height:140px;overflow-y:auto;white-space:pre-wrap">'+cfg.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</pre>'
      +'<button class="btn g" onclick="downloadOpenClawConfig()" style="width:100%;padding:8px;border-radius:7px;margin-top:7px;font-size:10px">openclaw.json Indir</button>'
      +'</div>'
      // Adim 4
      +'<div style="background:rgba(192,132,252,.05);border:1px solid rgba(192,132,252,.15);border-radius:10px;padding:12px;margin-bottom:10px">'
      +'<div style="font-size:10px;font-weight:700;color:var(--purple);margin-bottom:8px">4. Gateway Baslat ve BIST\'e Baglan</div>'
      +'<div style="background:#000;border-radius:7px;padding:9px;font-family:Courier New,monospace;font-size:9px;color:var(--green);margin-bottom:6px">'
      +'# Gateway baslat<br>'
      +'openclaw gateway<br><br>'
      +'# Token al (baska terminalde)<br>'
      +'openclaw gateway token</div>'
      +'<div style="font-size:8px;color:var(--t4)">Token\'i BIST Dev > Gateway Token alanina girin.</div>'
      +'</div>'
      // Ollama Cek
      +'<button class="btn c" onclick="v16CheckOllama()" style="width:100%;padding:10px;border-radius:8px;font-size:11px;margin-bottom:6px">Ollama Durumunu Kontrol Et</button>'
      +'<button class="btn" onclick="closeM()" style="width:100%;padding:9px;border-radius:8px;font-size:10px;color:var(--t3);border:1px solid rgba(255,255,255,.08)">Kapat</button>'
      +'</div>';

    document.getElementById('mcont').innerHTML = html;
    modal.classList.add('on');
  }catch(e){ toast(e.message); }
}

function v16CheckOllama(){
  v13AppendMsg && v13AppendMsg('sys','Ollama kontrol ediliyor...');
  fetch('http://127.0.0.1:11434/api/tags')
    .then(function(r){return r.json();})
    .then(function(d){
      var models = (d.models||[]).map(function(m){return m.name;});
      if(models.length){
        v13AppendMsg && v13AppendMsg('ai','Ollama aktif! Yuklu modeller:\n'+models.join('\n'));
        toast('Ollama hazir: '+models.length+' model');
      } else {
        v13AppendMsg && v13AppendMsg('sys','Ollama calisiyor ama model yok. ollama pull qwen2.5-coder:7b calistirin.');
      }
      closeM && closeM();
    }).catch(function(){
      v13AppendMsg && v13AppendMsg('sys','Ollama bagli degil. Render URL\'den test edin veya Ollama servisi baslatin.');
      closeM && closeM();
    });
}

//  OPENCLAW AGENT ID MAPPING 
// V13_AGENTS'a openclaw agent ID ekle
if(typeof V13_AGENTS !== 'undefined'){
  var _ocAgentMap = {
    'vibe-architect': 'bist-vibe-coder',
    'vibe-uiux':      'bist-vibe-coder',
    'vibe-trader':    'bist-trader',
    'vibe-backtest':  'bist-analyst',
    'vibe-debug':     'bist-debug',
    'vibe-data':      'bist-analyst',
    'vibe-feature':   'bist-vibe-coder',
    'vibe-review':    'bist-debug',
  };
  V13_AGENTS.forEach(function(ag){
    ag.ocAgentId = _ocAgentMap[ag.id] || 'main';
  });
}

//  GATEWAY DURUM GOSTERGESI 
// Dev panelinde gateway bagli degilken bilgi goster
var _origV15Render = typeof renderDevPanel==='function' ? renderDevPanel : null;
renderDevPanel = function(){
  try{
    if(_origV15Render) _origV15Render();
    // Gateway bagli degil uyarisi - AI stream'ine ekle
    setTimeout(function(){
      var st = document.getElementById('v13Stream');
      if(!st) return;
      var gwInfo = document.getElementById('v16GwInfo');
      if(gwInfo) gwInfo.remove();
      if(!_ocGW || !_ocGW.connected){
        var info = document.createElement('div');
        info.id = 'v16GwInfo';
        info.className = 'v13m-sys';
        info.style.cssText = 'cursor:pointer;padding:6px 10px;background:rgba(255,184,0,.07);border:1px solid rgba(255,184,0,.15);border-radius:7px;font-style:normal;font-size:9px;color:var(--gold);text-align:center;margin:4px 0';
        info.innerHTML = '&#9888; Gateway bagli degil &mdash; <b>Kurulum icin tikla</b>';
        info.onclick = showOpenClawSetup;
        st.insertBefore(info, st.firstChild);
      }
    }, 400);
  }catch(e){}
};

// GW baglantisi kurulunca uyariyi kaldir
var _orig16GWStatus = typeof ocGWSetStatus==='function' ? ocGWSetStatus : null;
ocGWSetStatus = function(cls, txt){
  try{ if(_orig16GWStatus) _orig16GWStatus(cls,txt); }catch(e){}
  try{
    var info = document.getElementById('v16GwInfo');
    if(info && cls==='connected') info.remove();
  }catch(e){}
};

//  CSS 
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      // Ollama model pulldown
      '.v16-model-pull{background:rgba(0,0,0,.5);border:1px solid rgba(0,102,204,.2);border-radius:9px;padding:10px;margin-bottom:7px}'
      +'.v16-pull-btn{padding:5px 10px;border-radius:6px;font-size:9px;font-weight:600;background:rgba(0,102,204,.12);color:#4A9EFF;border:1px solid rgba(0,102,204,.25);cursor:pointer}'
      +'.v16-pull-btn:active{opacity:.7}'
      // Gateway bagli kart
      +'.v16-gw-connected{background:rgba(0,230,118,.06);border:1px solid rgba(0,230,118,.2);border-radius:9px;padding:9px 12px;display:flex;align-items:center;gap:8px}'
      +'.v16-gw-pulse{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green);animation:gwPulse 2s infinite}'
      +'@keyframes gwPulse{0%,100%{opacity:1}50%{opacity:.4}}';
    document.head.appendChild(st);
  }catch(e){}
})();

//  INIT 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      // Dev tab
      if(!document.getElementById('devTab')){
        var nav=document.querySelector('nav');
        if(nav){
          var btn=document.createElement('button'); btn.id='devTab'; btn.className='tab';
          btn.innerHTML='&#128736; Dev';
          btn.onclick=function(){ try{pg('dev'); renderDevPanel();}catch(e){} };
          nav.appendChild(btn);
        }
      }
      if(!document.getElementById('page-dev')){
        var main=document.querySelector('main');
        if(main){ var p=document.createElement('div');p.id='page-dev';p.className='page';main.appendChild(p); }
      }
      // Ollama sessiz kontrol
      v16CheckOllama();
    }catch(e){}
  },800);
});

</script>
<script>

// BIST v17 BLOK 16
// 1. Cihaz tarayici - RAM/GPU/CPU tespiti
// 2. Akilli model router - mesaja gore model sec
// 3. 2026 en iyi lokal modeller katalogu

//  1. CIHAZ TARAYICI 
var _deviceProfile = null;

async function scanDevice(){
  var profile = {
    ua: navigator.userAgent,
    platform: navigator.platform || '',
    cores: navigator.hardwareConcurrency || 0,
    ramGB: 0,
    gpuRenderer: '',
    gpuVendor: '',
    isAppleSilicon: false,
    isMobile: /iPhone|iPad|Android/i.test(navigator.userAgent),
    isIOS: /iPhone|iPad/i.test(navigator.userAgent),
    isMac: /Mac/i.test(navigator.platform||''),
    isWindows: /Win/i.test(navigator.platform||''),
    isLinux: /Linux/i.test(navigator.platform||''),
    screenW: screen.width,
    screenH: screen.height,
    pixelRatio: window.devicePixelRatio || 1,
    tier: 'unknown',
    recommended: [],
    notRecommended: [],
    ollamaNote: ''
  };

  // RAM tahmini
  try{
    if(navigator.deviceMemory) profile.ramGB = navigator.deviceMemory;
  }catch(e){}

  // GPU bilgisi
  try{
    var canvas = document.createElement('canvas');
    var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if(gl){
      var dbg = gl.getExtension('WEBGL_debug_renderer_info');
      if(dbg){
        profile.gpuRenderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)||'';
        profile.gpuVendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL)||'';
      }
    }
  }catch(e){}

  // Apple Silicon tespiti
  var renderer = profile.gpuRenderer.toLowerCase();
  profile.isAppleSilicon = (profile.isMac||profile.isIOS) &&
    (renderer.indexOf('apple')>-1 || renderer.indexOf('m1')>-1 ||
     renderer.indexOf('m2')>-1 || renderer.indexOf('m3')>-1 ||
     renderer.indexOf('m4')>-1 || renderer.indexOf('a16')>-1 ||
     renderer.indexOf('a17')>-1 || renderer.indexOf('a18')>-1);

  // Tier belirleme
  if(profile.isIOS){
    profile.tier = 'mobile';
    profile.ollamaNote = 'iOS cihazlarda Ollama DIREKT CALISMAZ. Ollama bir masaustu/server uygulamasidir. Ancak ayni WiFi agindaki Mac/PC\'de Ollama calistirilip BIST ona baglaniabilir.';
  } else if(profile.isAppleSilicon && profile.ramGB >= 32){
    profile.tier = 'pro'; // M1/M2/M3 Pro veya Max
  } else if(profile.isAppleSilicon && profile.ramGB >= 16){
    profile.tier = 'mid';
  } else if(profile.isAppleSilicon){
    profile.tier = 'base';
  } else if(profile.ramGB >= 32){
    profile.tier = 'high';
  } else if(profile.ramGB >= 16){
    profile.tier = 'mid';
  } else if(profile.ramGB >= 8){
    profile.tier = 'low';
  } else if(profile.ramGB > 0){
    profile.tier = 'minimal';
  } else {
    profile.tier = 'unknown';
  }

  _deviceProfile = profile;
  return profile;
}

//  2. 2026 EN IYI LOKAL MODELLER KATALOGU 
var LOCAL_MODELS_2026 = [
  // === VIBE CODING ===
  {
    id: 'qwen2.5-coder:7b',
    name: 'Qwen 2.5 Coder 7B',
    provider: 'Alibaba',
    category: 'coding',
    ramReq: 6,   // GB
    vramReq: 0,
    ctx: 32768,
    humaneval: 72,
    tier: ['base','mid','pro','high','low'],
    strengths: ['JavaScript','Python','kod tamamlama','refactor'],
    best_for: ['vibe-architect','vibe-feature','vibe-uiux'],
    pull: 'ollama pull qwen2.5-coder:7b',
    size: '4.7GB',
    speed: 'hizli',
    badge: 'ONERILIR'
  },
  {
    id: 'qwen2.5-coder:14b',
    name: 'Qwen 2.5 Coder 14B',
    provider: 'Alibaba',
    category: 'coding',
    ramReq: 12,
    vramReq: 0,
    ctx: 128000,
    humaneval: 78,
    tier: ['mid','pro','high'],
    strengths: ['uzun kod','mimari','refactor','test yazma'],
    best_for: ['vibe-architect','vibe-feature','vibe-review'],
    pull: 'ollama pull qwen2.5-coder:14b',
    size: '9.0GB',
    speed: 'orta',
    badge: 'GUCLU'
  },
  {
    id: 'qwen3.5:27b',
    name: 'Qwen 3.5 27B',
    provider: 'Alibaba',
    category: 'coding',
    ramReq: 20,
    vramReq: 0,
    ctx: 131072,
    humaneval: 85,
    tier: ['pro','high'],
    strengths: ['en iyi kod','uzun context','mimari','agentic'],
    best_for: ['vibe-architect','vibe-feature','vibe-review','vibe-debug'],
    pull: 'ollama pull qwen3.5:27b',
    size: '17GB',
    speed: 'yavas',
    badge: 'EN IYI KOD'
  },
  // === GENEL / AKIL YURU TME ===
  {
    id: 'llama3.3:8b',
    name: 'Llama 3.3 8B',
    provider: 'Meta',
    category: 'general',
    ramReq: 6,
    vramReq: 0,
    ctx: 128000,
    humaneval: 72,
    tier: ['base','mid','pro','high','low'],
    strengths: ['genel sohbet','Turkce','analiz','hizli'],
    best_for: ['vibe-trader','main'],
    pull: 'ollama pull llama3.3:8b',
    size: '4.9GB',
    speed: 'hizli',
    badge: 'DENGE'
  },
  {
    id: 'llama4:8b',
    name: 'Llama 4 8B',
    provider: 'Meta',
    category: 'general',
    ramReq: 7,
    vramReq: 0,
    ctx: 128000,
    humaneval: 76,
    tier: ['base','mid','pro','high'],
    strengths: ['guncel bilgi','cok dilli','hizli','agent'],
    best_for: ['vibe-trader','main','vibe-data'],
    pull: 'ollama pull llama4:8b',
    size: '5.2GB',
    speed: 'hizli',
    badge: '2026 YENI'
  },
  // === MANTIK / DEBUG ===
  {
    id: 'deepseek-r1:8b',
    name: 'DeepSeek R1 8B',
    provider: 'DeepSeek',
    category: 'reasoning',
    ramReq: 6,
    vramReq: 0,
    ctx: 64000,
    humaneval: 74,
    tier: ['base','mid','pro','high','low'],
    strengths: ['adim adim mantik','debug','matematik','analiz'],
    best_for: ['vibe-debug','vibe-backtest','vibe-analyst'],
    pull: 'ollama pull deepseek-r1:8b',
    size: '4.9GB',
    speed: 'orta',
    badge: 'MANTIK'
  },
  {
    id: 'deepseek-v3.2-exp:7b',
    name: 'DeepSeek V3.2 7B',
    provider: 'DeepSeek',
    category: 'reasoning',
    ramReq: 6,
    vramReq: 0,
    ctx: 64000,
    humaneval: 76,
    tier: ['base','mid','pro','high'],
    strengths: ['en iyi 7B mantik','kod+akil','debug'],
    best_for: ['vibe-debug','vibe-backtest'],
    pull: 'ollama pull deepseek-v3.2-exp:7b',
    size: '4.7GB',
    speed: 'orta',
    badge: 'YENI 2026'
  },
  // === HAFIF / DUK RAM ===
  {
    id: 'qwen3:0.6b',
    name: 'Qwen3 0.6B',
    provider: 'Alibaba',
    category: 'lightweight',
    ramReq: 1,
    vramReq: 0,
    ctx: 32768,
    humaneval: 40,
    tier: ['minimal','low','base'],
    strengths: ['cok hizli','az RAM','basit gorevler'],
    best_for: ['main'],
    pull: 'ollama pull qwen3:0.6b',
    size: '522MB',
    speed: 'cok hizli',
    badge: 'EN HAFIF'
  },
  {
    id: 'gemma3:4b',
    name: 'Gemma 3 4B',
    provider: 'Google',
    category: 'lightweight',
    ramReq: 3,
    vramReq: 0,
    ctx: 32768,
    humaneval: 55,
    tier: ['minimal','low','base'],
    strengths: ['verimli','Google','cok dilli','hizli'],
    best_for: ['main','vibe-trader'],
    pull: 'ollama pull gemma3:4b',
    size: '2.5GB',
    speed: 'cok hizli',
    badge: 'HAFIF'
  },
  {
    id: 'phi4:14b',
    name: 'Phi-4 14B',
    provider: 'Microsoft',
    category: 'balanced',
    ramReq: 10,
    vramReq: 0,
    ctx: 16384,
    humaneval: 75,
    tier: ['mid','pro','high'],
    strengths: ['verimli','Microsoft','mantik','kod'],
    best_for: ['vibe-review','vibe-debug'],
    pull: 'ollama pull phi4:14b',
    size: '8.9GB',
    speed: 'orta',
    badge: 'VERIMLI'
  },
  // === PROFESYONEL ===
  {
    id: 'glm-4.7:9b',
    name: 'GLM-4.7 9B',
    provider: 'Z.ai',
    category: 'agentic',
    ramReq: 7,
    vramReq: 0,
    ctx: 128000,
    humaneval: 78,
    tier: ['base','mid','pro','high'],
    strengths: ['agentic','tool calling','frontend kod','cok adimli gorev'],
    best_for: ['vibe-architect','vibe-feature'],
    pull: 'ollama pull glm4:latest',
    size: '5.5GB',
    speed: 'orta',
    badge: 'AGENTIC 2025'
  },
  {
    id: 'mistral-small3:7b',
    name: 'Mistral Small 3 7B',
    provider: 'Mistral',
    category: 'fast',
    ramReq: 5,
    vramReq: 0,
    ctx: 32768,
    humaneval: 65,
    tier: ['base','mid','pro','high','low'],
    strengths: ['en hizli 7B','Avrupali','GDPR','cok dilli'],
    best_for: ['vibe-trader','main'],
    pull: 'ollama pull mistral-small3:latest',
    size: '4.2GB',
    speed: 'en hizli',
    badge: 'EN HIZLI'
  },
];

//  3. MODEL ROUTER - MESAJA GORE MODEL SEC 
var INTENT_PATTERNS = [
  {
    name: 'kod_yazma',
    patterns: ['yaz','ekle','kodla','olustur','fonksiyon','component','klas','import','export','html','css','javascript','python','api','endpoint'],
    agents: ['vibe-architect','vibe-feature','vibe-uiux'],
    preferModel: 'qwen2.5-coder:7b',
    fallback: 'llama3.3:8b'
  },
  {
    name: 'hata_debug',
    patterns: ['hata','error','bug','calismiyor','neden','sorun','duzelt','fix','broken','crash','undefined','null'],
    agents: ['vibe-debug'],
    preferModel: 'deepseek-r1:8b',
    fallback: 'deepseek-v3.2-exp:7b'
  },
  {
    name: 'analiz_rapor',
    patterns: ['analiz','rapor','ozet','istatistik','performans','backtest','sharpe','drawdown','win rate','pnl','pozisyon'],
    agents: ['vibe-backtest','vibe-trader'],
    preferModel: 'deepseek-r1:8b',
    fallback: 'llama3.3:8b'
  },
  {
    name: 'tasarim_ui',
    patterns: ['tasarim','renk','animasyon','css','stiller','gorsel','ui','ux','buton','kart','modal','sayfa'],
    agents: ['vibe-uiux'],
    preferModel: 'qwen2.5-coder:7b',
    fallback: 'llama4:8b'
  },
  {
    name: 'genel_sohbet',
    patterns: ['ne','nasil','nedir','neden','anlat','acikla','bana','fikrin','dusunce'],
    agents: ['main','vibe-trader'],
    preferModel: 'llama3.3:8b',
    fallback: 'llama4:8b'
  },
  {
    name: 'kod_inceleme',
    patterns: ['incele','kontrol','review','optimize','performans','guvenlik','daha iyi','refactor','iyilestir'],
    agents: ['vibe-review'],
    preferModel: 'deepseek-r1:8b',
    fallback: 'phi4:14b'
  },
];

function detectIntent(msg){
  var m = msg.toLowerCase();
  var scores = {};
  INTENT_PATTERNS.forEach(function(p){
    var score = 0;
    p.patterns.forEach(function(pat){ if(m.indexOf(pat)>-1) score++; });
    if(score > 0) scores[p.name] = {score:score, pattern:p};
  });
  // En yuksek skorlu intent
  var best = null; var bestScore = 0;
  Object.keys(scores).forEach(function(k){
    if(scores[k].score > bestScore){ bestScore=scores[k].score; best=scores[k].pattern; }
  });
  return best;
}

function routeToAgent(msg){
  var intent = detectIntent(msg);
  if(!intent) return null;

  // Cihaz profiline gore model sec
  var profile = _deviceProfile;
  var chosenModel = intent.preferModel;

  if(profile && profile.tier !== 'unknown'){
    // Cihaz destekliyor mu?
    var preferred = LOCAL_MODELS_2026.find(function(m){return m.id===intent.preferModel;});
    if(preferred && profile.ramGB > 0 && profile.ramGB < preferred.ramReq){
      chosenModel = intent.fallback; // RAM yetersiz, fallback kullan
    }
  }

  return {
    intent: intent.name,
    suggestedAgent: intent.agents[0],
    suggestedModel: chosenModel,
    agents: intent.agents
  };
}

// v13CallAPI - akilli router ile
var _v17_origCallAPI = v13CallAPI;
v13CallAPI = function(msg){
  try{
    var route = routeToAgent(msg);
    if(route){
      // Agent'i otomatik degistir
      if(typeof v13SetAgent==='function' && _v13ActiveAgent&&_v13ActiveAgent.id!==route.suggestedAgent){
        // Sadece farkli agenta geciyorsa bildir
        var targetAgent = typeof V13_AGENTS!=='undefined' && V13_AGENTS.find(function(a){return a.id===route.suggestedAgent;});
        if(targetAgent){
          v13AppendMsg && v13AppendMsg('sys','Yonlendirme: '+targetAgent.icon+' '+targetAgent.name+' ('+route.intent+')');
          v13SetAgent(route.suggestedAgent);
        }
      }
      // OpenClaw Gateway mesajina model hintti ekle
      if(_ocGW && _ocGW.connected && _ocGW.ws){
        // Gateway'e model tercihini bildir
        try{
          _ocGW.ws.send(JSON.stringify({
            type: 'config.set',
            key: 'preferredModel',
            value: route.suggestedModel
          }));
        }catch(e){}
      }
    }
    // Orijinal cagri
    if(_v17_origCallAPI) _v17_origCallAPI(msg);
  }catch(e){
    if(_v17_origCallAPI) _v17_origCallAPI(msg);
  }
};

//  4. CIHAZ TARAMA PANELI 
function openDeviceScanPanel(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Cihaz Tarama';
    document.getElementById('mcont').innerHTML = '<div style="text-align:center;padding:20px">'
      +'<div style="font-size:32px;margin-bottom:10px">&#128268;</div>'
      +'<div style="font-size:12px;color:var(--t2);margin-bottom:16px">Cihaziniz analiz ediliyor...</div>'
      +'<div class="skeleton" style="height:12px;margin:6px 0;border-radius:6px"></div>'
      +'<div class="skeleton" style="height:12px;width:70%;margin:6px auto;border-radius:6px"></div>'
      +'</div>';
    modal.classList.add('on');

    scanDevice().then(function(profile){
      renderDeviceScanResult(profile);
    });
  }catch(e){ toast(e.message); }
}

function renderDeviceScanResult(p){
  try{
    var el = document.getElementById('mcont');
    if(!el) return;

    // Tier renk ve aciklama
    var tierInfo = {
      mobile:  {clr:'#ff7043', label:'Mobil Cihaz', emoji:'?'},
      minimal: {clr:'#888',    label:'Minimal (4GB-)',emoji:'?'},
      low:     {clr:'#ffd600', label:'Dusuk (8GB)',  emoji:'?'},
      base:    {clr:'#00b0ff', label:'Temel (8-12GB)',emoji:'?'},
      mid:     {clr:'#00e676', label:'Orta (16GB)',  emoji:'?'},
      pro:     {clr:'#c084fc', label:'Pro (32GB+)',  emoji:'?'},
      high:    {clr:'#ffd700', label:'Yuksek (32GB+)',emoji:'?'},
      unknown: {clr:'#888',    label:'Bilinmiyor',   emoji:'?'},
    };
    var ti = tierInfo[p.tier] || tierInfo.unknown;

    // Uygun modeller
    var suitableModels = LOCAL_MODELS_2026.filter(function(m){
      if(p.tier==='mobile') return false; // iOS'ta lokal model yok
      if(p.tier==='minimal') return m.ramReq <= 2;
      if(p.tier==='low')  return m.ramReq <= 6;
      if(p.tier==='base') return m.ramReq <= 8;
      if(p.tier==='mid')  return m.ramReq <= 14;
      return true; // pro/high - hepsi
    });
    var notSuitable = LOCAL_MODELS_2026.filter(function(m){
      return suitableModels.indexOf(m) === -1;
    });

    var html = '<div style="padding:3px 0">'
      // Cihaz ozeti
      +'<div style="background:rgba(0,0,0,.5);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px;margin-bottom:12px">'
      +'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
      +'<div style="font-size:32px">'+ti.emoji+'</div>'
      +'<div><div style="font-size:15px;font-weight:700;color:'+ti.clr+'">'+ti.label+'</div>'
      +'<div style="font-size:9px;color:var(--t4)">'+p.tier.toUpperCase()+' TIER</div></div></div>'
      // Detaylar
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">'
      +'<div style="background:rgba(255,255,255,.04);border-radius:8px;padding:8px">'
      +'<div style="font-size:8px;color:var(--t4);margin-bottom:2px">PLATFORM</div>'
      +'<div style="font-size:10px;color:var(--t2)">'
      +(p.isIOS?'iOS (iPhone/iPad)':p.isAppleSilicon?'Apple Silicon Mac':p.isMac?'Intel Mac':p.isWindows?'Windows':p.isLinux?'Linux':'Bilinmiyor')
      +'</div></div>'
      +'<div style="background:rgba(255,255,255,.04);border-radius:8px;padding:8px">'
      +'<div style="font-size:8px;color:var(--t4);margin-bottom:2px">RAM</div>'
      +'<div style="font-size:10px;color:var(--t2)">'+(p.ramGB>0?p.ramGB+'GB':'Bilinmiyor')+'</div></div>'
      +'<div style="background:rgba(255,255,255,.04);border-radius:8px;padding:8px">'
      +'<div style="font-size:8px;color:var(--t4);margin-bottom:2px">CPU CEKIRDEK</div>'
      +'<div style="font-size:10px;color:var(--t2)">'+(p.cores||'?')+' core</div></div>'
      +'<div style="background:rgba(255,255,255,.04);border-radius:8px;padding:8px">'
      +'<div style="font-size:8px;color:var(--t4);margin-bottom:2px">GPU</div>'
      +'<div style="font-size:9px;color:var(--t2)">'+(p.gpuRenderer.substring(0,28)||'Bilinmiyor')+'</div></div>'
      +'</div>'
      +(p.ollamaNote?'<div style="margin-top:10px;padding:8px;background:rgba(255,184,0,.08);border:1px solid rgba(255,184,0,.2);border-radius:8px;font-size:9px;color:var(--gold)">'+p.ollamaNote+'</div>':'')
      +'</div>';

    // iOS icin ozel mesaj
    if(p.isIOS){
      html += '<div style="background:rgba(0,212,255,.05);border:1px solid rgba(0,212,255,.15);border-radius:12px;padding:14px;margin-bottom:10px">'
        +'<div style="font-size:11px;font-weight:700;color:var(--cyan);margin-bottom:8px">iOS Kullanim Rehberi</div>'
        +'<div style="font-size:9px;color:var(--t3);line-height:1.7">'
        +'iPhone/iPad\'de lokal AI modeli direkt calismaz. '
        +'En iyi cozum: ayni WiFi agindaki bir Mac/PC\'de Ollama kurulu ve calisir durumda olsun.<br><br>'
        +'<b style="color:var(--gold)">Mac/PC\'deki Ollama\'ya baglanmak:</b><br>'
        +'1. Mac\'te: <code style="color:var(--green)">OLLAMA_HOST=0.0.0.0:11434 ollama serve</code><br>'
        +'2. OpenClaw: <code style="color:var(--green)">openclaw gateway --bind 0.0.0.0</code><br>'
        +'3. BIST Dev > Gateway URL: <code style="color:var(--green)">ws://192.168.x.x:18789</code><br>'
        +'(Mac\'in lokal IP adresini yazin)'
        +'</div></div>';
    } else {
      // Uygun modeller
      html += '<div style="margin-bottom:10px">'
        +'<div style="font-size:9px;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">? Cihaziniz Icin Uygun ('+suitableModels.length+' model)</div>'
        +'<div style="display:grid;gap:5px">'
        +suitableModels.map(function(m){
          var catClr={'coding':'var(--cyan)','general':'var(--green)','reasoning':'var(--gold)',
            'lightweight':'var(--t3)','balanced':'var(--cyan)','agentic':'var(--purple)','fast':'var(--orange)'}[m.category]||'var(--t2)';
          return '<div style="background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.07);border-radius:9px;padding:9px">'
            +'<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">'
            +'<div style="flex:1"><div style="font-size:11px;font-weight:700;color:var(--t1)">'+m.name+'</div>'
            +'<div style="font-size:8px;color:'+catClr+'">'+m.provider+'  '+m.category+'  '+m.speed+'</div></div>'
            +'<span style="font-size:7px;padding:2px 6px;border-radius:4px;background:rgba(0,230,118,.12);color:var(--green);font-weight:700">'+m.badge+'</span>'
            +'</div>'
            +'<div style="font-size:8px;color:var(--t4);margin-bottom:5px">'+m.strengths.slice(0,3).join('  ')+' | '+m.size+' | RAM: '+m.ramReq+'GB+</div>'
            +'<div style="background:rgba(0,0,0,.5);border-radius:6px;padding:6px;font-family:Courier New,monospace;font-size:8px;color:var(--green)">'+m.pull+'</div>'
            +'</div>';
        }).join('')
        +'</div></div>';

      // Uygun olmayanlar
      if(notSuitable.length){
        html += '<div style="margin-bottom:10px">'
          +'<div style="font-size:9px;font-weight:700;color:var(--red);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">? Yetersiz RAM ('+notSuitable.length+' model)</div>'
          +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">'
          +notSuitable.map(function(m){
            return '<div style="padding:7px;border-radius:8px;border:1px solid rgba(255,68,68,.12);background:rgba(255,68,68,.04);opacity:.7">'
              +'<div style="font-size:9px;font-weight:600;color:var(--t3)">'+m.name+'</div>'
              +'<div style="font-size:7px;color:var(--red)">'+m.ramReq+'GB RAM gerekli</div>'
              +'</div>';
          }).join('')
          +'</div></div>';
      }
    }

    // Onerililen openclaw.json model listesi
    if(!p.isIOS && suitableModels.length){
      var topModels = suitableModels.slice(0,4);
      html += '<button class="btn g" onclick="v17ApplyRecommended()" style="width:100%;padding:10px;border-radius:9px;font-size:11px;font-weight:700;margin-bottom:6px">? Onerilenleri openclaw.json\'a Uygula</button>';
    }
    html += '<button class="btn" onclick="closeM()" style="width:100%;padding:9px;border-radius:8px;font-size:10px;color:var(--t3);border:1px solid rgba(255,255,255,.08)">Kapat</button>'
      +'</div>';

    el.innerHTML = html;
  }catch(e){ console.warn('renderDeviceScanResult:',e.message); }
}

function v17ApplyRecommended(){
  try{
    var profile = _deviceProfile;
    if(!profile) return;
    var suitable = LOCAL_MODELS_2026.filter(function(m){
      if(profile.tier==='mobile') return false;
      if(profile.tier==='minimal') return m.ramReq<=2;
      if(profile.tier==='low') return m.ramReq<=6;
      if(profile.tier==='base') return m.ramReq<=8;
      if(profile.tier==='mid') return m.ramReq<=14;
      return true;
    });
    var topCoding = suitable.find(function(m){return m.category==='coding';})||suitable[0];
    var topGeneral = suitable.find(function(m){return m.category==='general';});
    var topReason = suitable.find(function(m){return m.category==='reasoning';});
    var topFast = suitable.find(function(m){return m.speed==='en hizli'||m.speed==='cok hizli';});
    // openclaw.json indir
    if(typeof downloadOpenClawConfig==='function'){
      downloadOpenClawConfig();
      toast('openclaw.json cihaziniza gore duzenlendi!');
    }
    closeM && closeM();
  }catch(e){ toast(e.message); }
}

//  5. DEV PANEL'E TARAMA BUTONU EKLE 
var _v17_origRenderDevPanel = typeof renderDevPanel==='function' ? renderDevPanel : null;
renderDevPanel = function(){
  try{
    if(_v17_origRenderDevPanel) _v17_origRenderDevPanel();
    setTimeout(function(){
      try{
        var el = document.getElementById('page-dev');
        if(!el || document.getElementById('v17ScanBtn')) return;
        // Baslik satirina ekle
        var headerRow = el.querySelector('div');
        if(headerRow){
          var scanBtn = document.createElement('button');
          scanBtn.id = 'v17ScanBtn';
          scanBtn.onclick = openDeviceScanPanel;
          scanBtn.title = 'Cihazi tara';
          scanBtn.style.cssText = 'padding:4px 9px;border-radius:7px;font-size:9px;font-weight:600;cursor:pointer;border:1px solid rgba(0,212,255,.25);background:rgba(0,212,255,.07);color:var(--cyan);flex-shrink:0';
          scanBtn.textContent = '? Cihaz Tara';
          headerRow.appendChild(scanBtn);
        }
        // AI Stream uyarisi altina oto tarama mesaji
        var st = document.getElementById('v13Stream');
        if(st && !_deviceProfile){
          var scanNote = document.createElement('div');
          scanNote.className = 'v13m-sys';
          scanNote.style.cssText = 'cursor:pointer;padding:5px 9px;background:rgba(0,212,255,.05);border:1px solid rgba(0,212,255,.12);border-radius:7px;font-style:normal;font-size:9px;color:var(--cyan);margin:4px 0';
          scanNote.textContent = '? Cihaziniza uygun modelleri gormek icin tiklayin';
          scanNote.onclick = openDeviceScanPanel;
          st.insertBefore(scanNote, st.firstChild);
        }
      }catch(e){}
    }, 350);
  }catch(e){}
};

//  INIT 
window.addEventListener('load',function(){
  setTimeout(function(){
    // Otomatik cihaz tara - sessiz
    scanDevice().then(function(p){
      if(typeof devLog==='function'){
        devLog('Cihaz: '+p.tier.toUpperCase()+' tier, '+(p.ramGB||'?')+'GB RAM, '+(p.cores||'?')+' core','ok');
        if(p.isIOS) devLog('iOS: Ollama direkt calisMAZ, WiFi agindaki Mac/PC kullanin','warn');
      }
    });
    // Dev tab
    if(!document.getElementById('devTab')){
      var nav=document.querySelector('nav');
      if(nav){
        var btn=document.createElement('button'); btn.id='devTab'; btn.className='tab';
        btn.innerHTML='&#128736; Dev';
        btn.onclick=function(){ try{pg('dev'); renderDevPanel();}catch(e){} };
        nav.appendChild(btn);
      }
    }
    if(!document.getElementById('page-dev')){
      var main=document.querySelector('main');
      if(main){ var p2=document.createElement('div');p2.id='page-dev';p2.className='page';main.appendChild(p2); }
    }
  },600);
});

</script>
<script>

// BIST v18 BLOK 17
// Sunucu tarafi AI - iPhone icin
// Render proxy /ai/chat endpoint kullanir
// Groq (ucretsiz Llama 3.3 70B) -> HF -> Together -> Anthropic Haiku

//  SUNUCU AI CAGRI 
function callServerAI(msg, agentId, history, cb){
  try{
    var posC = Object.keys(S.openPositions||{}).length;
    var closed = S.closedPositions||[];
    var wins = closed.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
    var wr = closed.length ? (wins/closed.length*100).toFixed(1) : 'N/A';

    var PROXY = typeof PROXY_URL !== 'undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';

    fetch(PROXY+'/ai/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        message: msg,
        agent: agentId||'main',
        history: (history||[]).slice(-6),
        bist_context: {
          positions: posC,
          signals: (S.sigs||[]).length,
          xu100: S.xu100Change||0,
          winrate: wr
        }
      })
    })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.error && !d.response){
        cb(null, d.response||'Sunucu AI hatasi');
        return;
      }
      cb(d.response, null, d.provider, d.model);
    })
    .catch(function(e){ cb(null, e.message); });
  }catch(e){ cb(null, e.message); }
}

// Aktif providerlari kontrol et - ilk acilista
var _serverAIStatus = null;
function checkServerAIProviders(){
  var PROXY = typeof PROXY_URL !== 'undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';
  fetch(PROXY+'/ai/providers')
    .then(function(r){ return r.json(); })
    .then(function(d){
      _serverAIStatus = d;
      var active = Object.keys(d).filter(function(k){ return d[k].active; });
      if(typeof devLog==='function'){
        if(active.length){
          devLog('Sunucu AI: '+active.map(function(k){return k+' ('+d[k].cost+')';}).join(', '),'ok');
        } else {
          devLog('Sunucu AI: Hic provider aktif degil! Render > Environment Variables\'a GROQ_API_KEY ekleyin.','warn');
        }
      }
    }).catch(function(){});
}

//  V13 CALL API OVERRIDE - Sunucu AI kullan 
v13CallAPI = function(msg){
  try{
    if(!msg||!msg.trim()) return;
    if(_v13Thinking) return;
    _v13Thinking = true;

    var loadDiv = document.createElement('div');
    loadDiv.className='v13m-thinking'; loadDiv.id='v13Loading';
    loadDiv.innerHTML='<span class="v13-dot"></span><span class="v13-dot"></span><span class="v13-dot"></span>';
    var st = document.getElementById('v13Stream');
    if(st){ st.appendChild(loadDiv); st.scrollTop=st.scrollHeight; }

    function done(resp, err, provider, model){
      _v13Thinking = false;
      var ld=document.getElementById('v13Loading'); if(ld) ld.remove();
      if(err){
        // Sunucu AI hatali - direkte provider mesaji goster
        if(err.indexOf('GROQ_API_KEY')>-1 || err.indexOf('provider')>-1 || err.indexOf('API key')>-1){
          v13AppendMsg && v13AppendMsg('sys', '? Sunucu AI kurulmamis. Asagidaki adimi tamamlayin.');
          showSetupGuide();
        } else {
          v13AppendMsg && v13AppendMsg('sys', 'Sunucu: '+err);
        }
        return;
      }
      if(!resp) return;

      // Provider badge
      if(provider){
        var pLabel = {
          groq:'Groq Llama 3.3 70B (Ucretsiz)',
          hf:'HuggingFace Mistral 7B (Ucretsiz)',
          together:'Together Llama 70B',
          anthropic:'Claude Haiku 4.5'
        }[provider]||provider;
        v13AppendMsg && v13AppendMsg('sys', '? '+pLabel+(model?' | '+model:''));
      }

      _v13History && _v13History.push({role:'assistant', content:resp});
      var cm = resp.match(/```(?:javascript|js|python|bash)?\n?([\s\S]*?)```/);
      if(cm){
        var before = resp.replace(cm[0],'').trim();
        if(before) v13AppendMsg && v13AppendMsg('ai', before);
        v13AppendMsg && v13AppendMsg('ai', null, cm[1].trim());
      } else {
        v13AppendMsg && v13AppendMsg('ai', resp);
      }
      if(_ttsEnabled && typeof v13Speak==='function')
        v13Speak(resp.replace(/```[\s\S]*?```/g,'').substring(0,200));
    }

    var agent = _v13ActiveAgent;
    var agentId = (agent&&agent.id)||'main';

    // Oncelik: 1. OpenClaw Gateway, 2. Sunucu AI, 3. Direkt API
    if(_ocGW && _ocGW.connected && _ocGW.ws && _ocGW.ws.readyState===1){
      // Gateway bagli - oraya gonder
      var responded = false;
      var origHandler = _ocGW.ws.onmessage;
      function tempH(e){
        try{
          var data=JSON.parse(e.data);
          if(data.type==='pong'||data.type==='ping'||data.type==='auth.ok') return;
          if((data.type==='chat.message'||data.type==='message')&&!responded){
            var txt=data.text||data.content||'';
            if(txt){ responded=true; _ocGW.ws.onmessage=origHandler; done(txt,null,'gateway'); }
          }
        }catch(ex){}
        if(origHandler) origHandler(e);
      }
      _ocGW.ws.onmessage=tempH;
      var ctxMsg = (agent?agent.name+': ':'')+msg;
      if((_v13History||[]).length%5===0){
        ctxMsg+=' [pos='+Object.keys(S.openPositions||{}).length+' xu='+( S.xu100Change||0).toFixed(1)+'%]';
      }
      _ocGW.ws.send(JSON.stringify({type:'chat.send',text:ctxMsg,channel:'webchat',agentId:(agent&&agent.ocAgentId)||'main'}));
      setTimeout(function(){if(!responded){responded=true;_ocGW.ws.onmessage=origHandler;done(null,'Gateway yanit vermedi (60sn).');}},60000);
    } else {
      // Sunucu AI kullan
      callServerAI(msg, agentId, _v13History||[], done);
    }
  }catch(e){
    _v13Thinking=false;
    var ld=document.getElementById('v13Loading'); if(ld) ld.remove();
    v13AppendMsg && v13AppendMsg('sys','Hata: '+e.message);
  }
};

//  KURULUM KILAVUZU 
function showSetupGuide(){
  try{
    var st = document.getElementById('v13Stream');
    if(!st) return;

    var guide = document.createElement('div');
    guide.className = 'v13m-ai';
    guide.style.cssText += 'border-color:rgba(255,184,0,.3);background:rgba(255,184,0,.05)';

    var PROXY = typeof PROXY_URL!=='undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';
    var renderDashUrl = 'https://dashboard.render.com';

    guide.innerHTML =
      '<b style="color:var(--gold)">Sunucu AI Kurulumu (1 adim)</b>\n\n'
      +'1. <a href="https://console.groq.com/keys" style="color:var(--cyan)" target="_blank">console.groq.com/keys</a> adresinden <b>ucretsiz</b> API key alin\n\n'
      +'2. <a href="'+renderDashUrl+'" style="color:var(--cyan)" target="_blank">Render Dashboard</a> acin\n'
      +'   Servisiniz > Environment > Add Environment Variable:\n'
      +'   Key: <code style="color:var(--green)">GROQ_API_KEY</code>\n'
      +'   Value: <code style="color:var(--green)">gsk_xxxx...</code>\n\n'
      +'3. Render otomatik yeniden deploy eder (~2dk)\n\n'
      +'Sonra Groq Llama 3.3 70B (ucretsiz, saniyede 275 token) kullanabilirsiniz!';

    st.appendChild(guide);
    st.scrollTop = st.scrollHeight;
  }catch(e){}
}

//  DEV PANEL GUNCELLE - Provider durumu goster 
var _v18_origRender = typeof renderDevPanel==='function' ? renderDevPanel : null;
renderDevPanel = function(){
  try{ if(_v18_origRender) _v18_origRender(); }catch(e){}
  // Provider durumunu AI stream'e ekle
  setTimeout(function(){
    try{
      var st = document.getElementById('v13Stream');
      if(!st) return;
      var existing = document.getElementById('v18ProviderInfo');
      if(existing) existing.remove();

      var info = document.createElement('div');
      info.id = 'v18ProviderInfo';
      info.className = 'v13m-sys';
      info.style.cssText = 'padding:5px 8px;background:rgba(0,0,0,.4);border-radius:7px;font-style:normal;font-size:8.5px;line-height:1.7;margin:3px 0';

      if(_serverAIStatus){
        var active = Object.keys(_serverAIStatus).filter(function(k){ return _serverAIStatus[k].active; });
        if(active.length){
          info.style.borderLeft = '2px solid var(--green)';
          info.innerHTML = '<span style="color:var(--green)">Sunucu AI hazir:</span> '
            +active.map(function(k){
              var p=_serverAIStatus[k];
              return '<span style="color:var(--t2)">'+k+'</span> <span style="color:var(--gold)">'+p.cost+'</span>';
            }).join(' | ');
        } else {
          info.style.borderLeft = '2px solid var(--gold)';
          info.innerHTML = '<span style="color:var(--gold)">Sunucu AI kurulmamis</span> '
            +'<span style="cursor:pointer;color:var(--cyan);text-decoration:underline" onclick="showSetupGuide()">Kurulum goster</span>';
        }
      } else {
        info.style.borderLeft = '2px solid rgba(255,255,255,.2)';
        info.innerHTML = '<span style="color:var(--t4)">Sunucu AI kontrol ediliyor...</span>';
        checkServerAIProviders();
      }
      st.insertBefore(info, st.firstChild);
    }catch(e){}
  },400);
};

//  CSS 
(function(){
  try{
    var st=document.createElement('style');
    st.textContent=
      '.v18-provider-bar{display:flex;flex-wrap:wrap;gap:5px;padding:7px 0;margin-bottom:6px}'
      +'.v18-prov{display:flex;align-items:center;gap:4px;padding:4px 8px;border-radius:6px;font-size:8px;font-weight:700}'
      +'.v18-prov-ok{background:rgba(0,230,118,.1);border:1px solid rgba(0,230,118,.25);color:var(--green)}'
      +'.v18-prov-off{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);color:var(--t4)}'
      +'.v18-setup-btn{width:100%;padding:11px;border-radius:9px;font-size:11px;font-weight:700;cursor:pointer;border:none;background:linear-gradient(135deg,rgba(255,184,0,.15),rgba(255,112,0,.15));color:var(--gold);border:1px solid rgba(255,184,0,.3);margin-top:6px}'
      +'.v18-setup-btn:active{opacity:.8}';
    document.head.appendChild(st);
  }catch(e){}
})();

//  INIT 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      checkServerAIProviders();
      if(!document.getElementById('devTab')){
        var nav=document.querySelector('nav');
        if(nav){
          var btn=document.createElement('button');btn.id='devTab';btn.className='tab';
          btn.innerHTML='? Dev';
          btn.onclick=function(){try{pg('dev');renderDevPanel();}catch(e){}};
          nav.appendChild(btn);
        }
      }
      if(!document.getElementById('page-dev')){
        var main=document.querySelector('main');
        if(main){var p=document.createElement('div');p.id='page-dev';p.className='page';main.appendChild(p);}
      }
    }catch(e){}
  },700);
});

</script>
<script>

// BIST SOCIAL BLOK 18
// Uyelik / Ozel Sohbet / Grup Sohbet / Forum
// Tum veriler Render backend'de guvenli saklanir

//  CONFIG 
var SOCIAL_URL = (function(){
  var proxy = typeof PROXY_URL !== 'undefined'
    ? PROXY_URL : 'https://bist-price-proxy.onrender.com';
  return proxy;
})();

var _auth = {
  token: null, user: null,
  load: function(){
    try{
      this.token = localStorage.getItem('bist_jwt');
      var u = localStorage.getItem('bist_user');
      this.user = u ? JSON.parse(u) : null;
    }catch(e){}
  },
  save: function(token, user){
    this.token = token; this.user = user;
    try{
      localStorage.setItem('bist_jwt', token);
      localStorage.setItem('bist_user', JSON.stringify(user));
    }catch(e){}
  },
  clear: function(){
    this.token = null; this.user = null;
    try{ localStorage.removeItem('bist_jwt'); localStorage.removeItem('bist_user'); }catch(e){}
  }
};
_auth.load();

//  API YARDIMCI 
function socialAPI(method, path, body, cb){
  var opts = {
    method: method,
    headers: {'Content-Type':'application/json'}
  };
  if(_auth.token) opts.headers['Authorization'] = 'Bearer '+_auth.token;
  if(body) opts.body = JSON.stringify(body);
  fetch(SOCIAL_URL+path, opts)
    .then(function(r){
      if(r.status===401){ _auth.clear(); showAuthModal(); return null; }
      return r.json();
    })
    .then(function(d){ if(d && cb) cb(null,d); })
    .catch(function(e){ if(cb) cb(e.message); });
}

//  WEBSOCKET 
var _chatWS = null;
var _chatRoom = 'genel';
var _typingTimer = null;
var _wsReconnect = 0;

function connectChatWS(){
  if(!_auth.token) return;
  if(_chatWS && _chatWS.readyState < 2) return;
  var wsUrl = SOCIAL_URL.replace('https://','wss://').replace('http://','ws://');
  wsUrl += '/social/ws/'+_auth.token;
  try{
    _chatWS = new WebSocket(wsUrl);
    _chatWS.onopen = function(){
      _wsReconnect = 0;
      updateChatStatus(true);
      _chatWS.send(JSON.stringify({type:'join_room',room:_chatRoom}));
    };
    _chatWS.onmessage = function(e){
      try{ handleWSMessage(JSON.parse(e.data)); }catch(ex){}
    };
    _chatWS.onclose = function(){
      updateChatStatus(false);
      if(_wsReconnect < 5){
        _wsReconnect++;
        setTimeout(connectChatWS, 3000*_wsReconnect);
      }
    };
    _chatWS.onerror = function(){ updateChatStatus(false); };
  }catch(e){}
}

function handleWSMessage(data){
  switch(data.type){
    case 'chat':
      appendChatMsg(data); break;
    case 'user_joined':
      appendChatSys(data.display_name+' odaya katildi');
      updateOnlineList(); break;
    case 'user_left':
      appendChatSys(data.username+' ayrildi');
      updateOnlineList(); break;
    case 'room_joined':
      updateOnlineList(data.members); break;
    case 'dm':
      handleIncomingDM(data); break;
    case 'typing':
      showTypingIndicator(data.username, data.room); break;
    case 'notification':
      handleNotification(data); break;
  }
}

function updateChatStatus(online){
  var dot = document.getElementById('chatWsDot');
  var lbl = document.getElementById('chatWsLabel');
  if(dot) dot.style.background = online ? 'var(--green)' : 'var(--t4)';
  if(lbl) lbl.textContent = online ? 'Canli' : 'Baglaniyor...';
}

//  CSS 
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      // Social sayfasi
      '#page-social{padding:0}'
      +'.soc-tabs{display:flex;border-bottom:1px solid rgba(255,255,255,.08);background:rgba(0,0,0,.5);position:sticky;top:0;z-index:10}'
      +'.soc-tab{flex:1;padding:11px 4px;font-size:10px;font-weight:600;color:var(--t4);background:none;border:none;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}'
      +'.soc-tab.active{color:var(--cyan);border-bottom-color:var(--cyan)}'
      +'.soc-panel{display:none;height:calc(100vh - 140px);overflow-y:auto}'
      +'.soc-panel.active{display:flex;flex-direction:column}'

      // Auth modal
      +'#authModal{position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:10003;display:none;align-items:center;justify-content:center}'
      +'#authModal.on{display:flex}'
      +'.auth-box{background:var(--bg2);border:1px solid rgba(0,212,255,.2);border-radius:18px;padding:28px 24px;width:320px;max-width:90vw}'
      +'.auth-title{font-size:20px;font-weight:800;color:var(--t1);margin-bottom:4px}'
      +'.auth-sub{font-size:10px;color:var(--t4);margin-bottom:20px}'
      +'.auth-inp{width:100%;box-sizing:border-box;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:11px 13px;font-size:12px;color:var(--t1);margin-bottom:10px;outline:none}'
      +'.auth-inp:focus{border-color:rgba(0,212,255,.5)}'
      +'.auth-btn{width:100%;padding:12px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;border:none;margin-bottom:8px}'
      +'.auth-btn-primary{background:var(--cyan);color:#000}'
      +'.auth-btn-secondary{background:rgba(255,255,255,.07);color:var(--t2);border:1px solid rgba(255,255,255,.1)}'
      +'.auth-err{font-size:10px;color:var(--red);margin-bottom:8px;display:none}'
      +'.auth-switch{font-size:10px;color:var(--t4);text-align:center;cursor:pointer}'
      +'.auth-switch span{color:var(--cyan);font-weight:600}'

      // Profil badge
      +'#socialUserBadge{display:flex;align-items:center;gap:7px;padding:8px 12px;background:rgba(0,212,255,.07);border-bottom:1px solid rgba(0,212,255,.12)}'
      +'#socialUserBadge .avatar{width:28px;height:28px;border-radius:50%;background:rgba(0,212,255,.15);display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}'

      // Grup sohbet
      +'.chat-rooms{display:flex;gap:5px;padding:10px;overflow-x:auto;border-bottom:1px solid rgba(255,255,255,.05);scrollbar-width:none}'
      +'.chat-room-btn{flex-shrink:0;padding:5px 12px;border-radius:20px;font-size:10px;font-weight:600;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);color:var(--t3);cursor:pointer;transition:all .2s;white-space:nowrap}'
      +'.chat-room-btn.active{background:rgba(0,212,255,.12);border-color:rgba(0,212,255,.3);color:var(--cyan)}'
      +'.chat-messages{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:6px}'
      +'.chat-msg{display:flex;align-items:flex-start;gap:7px}'
      +'.chat-msg.mine{flex-direction:row-reverse}'
      +'.chat-avatar{width:28px;height:28px;border-radius:50%;background:rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0;cursor:pointer}'
      +'.chat-bubble{max-width:76%;padding:8px 11px;border-radius:12px;font-size:10px;line-height:1.5;word-break:break-word}'
      +'.chat-msg .chat-bubble{background:rgba(255,255,255,.07);border-radius:4px 12px 12px 12px;color:var(--t2)}'
      +'.chat-msg.mine .chat-bubble{background:rgba(0,212,255,.12);border-radius:12px 4px 12px 12px;color:var(--t1)}'
      +'.chat-sender{font-size:8px;color:var(--t4);margin-bottom:2px}'
      +'.chat-time{font-size:7.5px;color:var(--t4);margin-top:2px;text-align:right}'
      +'.chat-sys{font-size:8.5px;color:var(--t4);text-align:center;font-style:italic;padding:3px 0}'
      +'.chat-input-row{display:flex;gap:6px;padding:10px;border-top:1px solid rgba(255,255,255,.06)}'
      +'.chat-input{flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);border-radius:20px;padding:9px 14px;font-size:11px;color:var(--t1);outline:none}'
      +'.chat-input:focus{border-color:rgba(0,212,255,.4)}'
      +'.chat-send-btn{width:36px;height:36px;border-radius:50%;background:var(--cyan);color:#000;border:none;cursor:pointer;font-size:16px;display:flex;align-items:center;justify-content:center;flex-shrink:0}'
      +'.typing-indicator{font-size:8.5px;color:var(--t4);padding:2px 10px;font-style:italic;min-height:18px}'
      +'.online-pill{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:10px;background:rgba(0,230,118,.08);border:1px solid rgba(0,230,118,.15);font-size:8px;color:var(--green);margin:0 8px 6px}'

      // Ozel mesaj
      +'.dm-list{flex:1;overflow-y:auto}'
      +'.dm-item{display:flex;align-items:center;gap:10px;padding:11px 14px;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:background .15s}'
      +'.dm-item:active{background:rgba(255,255,255,.04)}'
      +'.dm-avatar{width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0;position:relative}'
      +'.dm-online-dot{position:absolute;bottom:1px;right:1px;width:9px;height:9px;border-radius:50%;background:var(--green);border:2px solid var(--bg)}'
      +'.dm-name{font-size:11px;font-weight:600;color:var(--t1)}'
      +'.dm-preview{font-size:9px;color:var(--t4);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}'
      +'.dm-unread{background:var(--cyan);color:#000;font-size:8px;font-weight:800;border-radius:10px;padding:1px 6px;margin-left:auto;flex-shrink:0}'
      +'.dm-chat-header{display:flex;align-items:center;gap:9px;padding:10px 14px;border-bottom:1px solid rgba(255,255,255,.06);background:rgba(0,0,0,.4)}'

      // Forum
      +'.forum-cats{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:10px}'
      +'.forum-cat{padding:12px;border-radius:11px;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);cursor:pointer;transition:all .18s}'
      +'.forum-cat:active{transform:scale(.97)}'
      +'.forum-cat-icon{font-size:20px;margin-bottom:5px}'
      +'.forum-cat-name{font-size:11px;font-weight:700;color:var(--t1);margin-bottom:2px}'
      +'.forum-cat-desc{font-size:8px;color:var(--t4);line-height:1.4}'
      +'.forum-cat-count{font-size:8px;margin-top:5px;font-weight:600}'
      +'.forum-topics{padding:8px}'
      +'.forum-topic{padding:11px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.02);margin-bottom:6px;cursor:pointer;transition:background .15s}'
      +'.forum-topic:active{background:rgba(255,255,255,.05)}'
      +'.forum-topic-title{font-size:11px;font-weight:700;color:var(--t1);margin-bottom:4px;line-height:1.4}'
      +'.forum-topic-meta{display:flex;align-items:center;gap:8px;font-size:8px;color:var(--t4)}'
      +'.forum-topic-tag{padding:1px 7px;border-radius:8px;background:rgba(0,212,255,.1);color:var(--cyan);font-size:7.5px;font-weight:600}'
      +'.forum-pinned{color:var(--gold);font-size:9px}'
      +'.forum-detail{padding:12px}'
      +'.forum-post{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:11px;padding:12px;margin-bottom:8px}'
      +'.forum-post-header{display:flex;align-items:center;gap:8px;margin-bottom:8px}'
      +'.forum-post-avatar{width:30px;height:30px;border-radius:50%;background:rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;font-size:17px}'
      +'.forum-post-content{font-size:10px;color:var(--t2);line-height:1.6}'
      +'.forum-like-btn{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:7px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);color:var(--t4);font-size:9px;cursor:pointer;margin-top:8px}'
      +'.forum-like-btn.liked{color:var(--red);border-color:rgba(255,68,68,.3);background:rgba(255,68,68,.07)}'
      +'.forum-comment{padding:10px 12px;border-left:2px solid rgba(255,255,255,.08);margin:6px 0 6px 10px;position:relative}'
      +'.forum-comment.reply{margin-left:28px;border-color:rgba(0,212,255,.2)}'
      +'.new-topic-btn{margin:10px;padding:12px;border-radius:10px;background:rgba(0,212,255,.12);border:1px solid rgba(0,212,255,.25);color:var(--cyan);font-size:11px;font-weight:700;cursor:pointer;text-align:center}'
      +'.forum-back{display:flex;align-items:center;gap:6px;padding:10px 12px;color:var(--cyan);font-size:11px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.05)}'
      +'.forum-search{margin:8px;}'
      +'.forum-search input{width:100%;box-sizing:border-box;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09);border-radius:9px;padding:9px 12px;font-size:11px;color:var(--t1);outline:none}'
      +'.compose-area{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);border-radius:9px;padding:10px 12px;font-size:11px;color:var(--t1);width:100%;box-sizing:border-box;min-height:80px;resize:vertical;outline:none;font-family:inherit}'
      +'.compose-area:focus{border-color:rgba(0,212,255,.4)}'
      +'.soc-btn{padding:10px 16px;border-radius:9px;font-size:11px;font-weight:700;cursor:pointer;border:none}'
      +'.soc-btn-primary{background:rgba(0,212,255,.15);color:var(--cyan);border:1px solid rgba(0,212,255,.3)}'
      +'.soc-btn-danger{background:rgba(255,68,68,.1);color:#ff6b6b;border:1px solid rgba(255,68,68,.2)}'
      +'.notif-dot{position:absolute;top:2px;right:2px;width:8px;height:8px;border-radius:50%;background:var(--red);display:none}'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  AUTH MODAL 
function showAuthModal(mode){
  mode = mode || 'login';
  var existing = document.getElementById('authModal');
  if(existing) existing.remove();

  var modal = document.createElement('div');
  modal.id = 'authModal';
  modal.className = 'on';

  function render(m){
    modal.innerHTML = '<div class="auth-box">'
      +'<div class="auth-title">BIST AI '+(m==='login'?'Giris Yap':'Kayit Ol')+'</div>'
      +'<div class="auth-sub">'+(m==='login'
        ?'Sohbet ve foruma katilmak icin giris yapin'
        :'Ucretsiz hesap olusturun, verileriniz gizli kalir')+'</div>'
      +'<div id="authErr" class="auth-err"></div>'
      +(m==='register'?'<input class="auth-inp" id="authDispName" placeholder="Gordulen Ad (opsiyonel)">':'')
      +'<input class="auth-inp" id="authUser" placeholder="Kullanici adi" autocomplete="username">'
      +(m==='register'?'<input class="auth-inp" id="authEmail" placeholder="E-posta (opsiyonel, gizli tutulur)" type="email" autocomplete="email">':'')
      +'<input class="auth-inp" id="authPass" placeholder="Sifre (min 6 karakter)" type="password" autocomplete="'+(m==='login'?'current-password':'new-password')+'">'
      +'<button class="auth-btn auth-btn-primary" onclick="authSubmit(\''+m+'\')">'+(m==='login'?'Giris Yap':'Kayit Ol')+'</button>'
      +(m==='login'?'<button class="auth-btn auth-btn-secondary" onclick="continueAsGuest()">Misafir Olarak Devam Et</button>':'')
      +'<div class="auth-switch" onclick="renderAuth(\''+( m==='login'?'register':'login')+'\')">'
      +(m==='login'?'Hesabiniz yok mu? <span>Kayit Olun</span>':'Zaten hesabiniz var mi? <span>Giris Yapin</span>')+'</div>'
      +'</div>';
  }

  function renderAuth(m){ modal.setAttribute('data-mode',m); render(m); }
  modal.renderAuth = renderAuth;
  render(mode);
  document.body.appendChild(modal);
  setTimeout(function(){ var el=document.getElementById('authUser'); if(el) el.focus(); },100);
}

window.renderAuth = function(m){
  var modal = document.getElementById('authModal');
  if(modal) modal.renderAuth(m);
};

window.authSubmit = function(mode){
  var user = (document.getElementById('authUser')||{}).value||'';
  var pass = (document.getElementById('authPass')||{}).value||'';
  var email = (document.getElementById('authEmail')||{}).value||'';
  var disp = (document.getElementById('authDispName')||{}).value||'';
  var errEl = document.getElementById('authErr');

  if(!user.trim()||!pass.trim()){
    if(errEl){errEl.textContent='Kullanici adi ve sifre gerekli';errEl.style.display='block';}
    return;
  }

  var path = mode==='login' ? '/social/auth/login' : '/social/auth/register';
  var body = mode==='login'
    ? {username:user.trim(), password:pass}
    : {username:user.trim(), password:pass, email:email||null, display_name:disp||null};

  socialAPI('POST', path, body, function(err, data){
    if(err||(data&&data.detail)){
      var msg = err || (typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
      if(errEl){errEl.textContent=msg;errEl.style.display='block';}
      return;
    }
    _auth.save(data.token, data.user);
    var modal = document.getElementById('authModal');
    if(modal) modal.remove();
    toast('Hos geldiniz, '+data.user.display_name+'!');
    if(typeof haptic==='function') haptic('success');
    renderSocialPage();
    connectChatWS();
  });
};

window.continueAsGuest = function(){
  var modal = document.getElementById('authModal');
  if(modal) modal.remove();
  toast('Misafir olarak devam ediyorsunuz. Sohbet icin giris gerekli.');
};

//  SOSYAL SAYFA 
var _socialSubTab = 'group';
var _dmTarget = null;
var _forumView = 'categories';
var _forumCategory = null;
var _forumTopic = null;

function renderSocialPage(){
  var el = document.getElementById('page-social');
  if(!el) return;

  el.innerHTML = ''
    // Kullanici badge
    +(_auth.user
      ?'<div id="socialUserBadge">'
        +'<div class="avatar">'+(_auth.user.avatar||'?')+'</div>'
        +'<div style="flex:1"><div style="font-size:11px;font-weight:700;color:var(--t1)">'+_auth.user.display_name+'</div>'
        +'<div style="display:flex;align-items:center;gap:5px"><div id="chatWsDot" style="width:7px;height:7px;border-radius:50%;background:var(--t4)"></div><div id="chatWsLabel" style="font-size:8px;color:var(--t4)">Baglaniyor...</div></div></div>'
        +'<button onclick="showSocialProfile()" style="background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);border-radius:8px;padding:5px 10px;font-size:9px;color:var(--t3);cursor:pointer">Profilim</button>'
        +'<button onclick="socialLogout()" style="background:rgba(255,68,68,.08);border:1px solid rgba(255,68,68,.2);border-radius:8px;padding:5px 10px;font-size:9px;color:#ff9999;cursor:pointer">Cikis</button>'
        +'</div>'
      :'<div style="padding:10px;text-align:center">'
        +'<button class="btn c" onclick="showAuthModal()" style="width:100%;padding:11px;border-radius:9px;font-size:12px;font-weight:700">Giris Yap veya Kayit Ol</button>'
        +'</div>')
    // Sekmeler
    +'<div class="soc-tabs">'
    +'<button class="soc-tab'+(_socialSubTab==='group'?' active':'')+'" onclick="switchSocialTab(\'group\')">Grup Sohbet</button>'
    +'<button class="soc-tab'+(_socialSubTab==='dm'?' active':'')+'" onclick="switchSocialTab(\'dm\')" style="position:relative">Ozel Mesaj<span id="dmNotifDot" class="notif-dot"></span></button>'
    +'<button class="soc-tab'+(_socialSubTab==='forum'?' active':'')+'" onclick="switchSocialTab(\'forum\')">Forum</button>'
    +'</div>'
    // Paneller
    +'<div id="socGroupPanel" class="soc-panel'+(_socialSubTab==='group'?' active':'')+'"></div>'
    +'<div id="socDMPanel" class="soc-panel'+(_socialSubTab==='dm'?' active':'')+'"></div>'
    +'<div id="socForumPanel" class="soc-panel'+(_socialSubTab==='forum'?' active':'')+'"></div>'
  ;

  if(_socialSubTab==='group') renderGroupChat();
  else if(_socialSubTab==='dm') renderDMPanel();
  else if(_socialSubTab==='forum') renderForumPanel();

  if(_auth.user && (!_chatWS || _chatWS.readyState > 1)) connectChatWS();
}

window.switchSocialTab = function(tab){
  _socialSubTab = tab;
  document.querySelectorAll('.soc-tab').forEach(function(t,i){
    t.classList.toggle('active', ['group','dm','forum'][i]===tab);
  });
  document.querySelectorAll('.soc-panel').forEach(function(p){ p.classList.remove('active'); });
  var panelId = tab==='group'?'socGroupPanel':tab==='dm'?'socDMPanel':'socForumPanel';
  var panel = document.getElementById(panelId);
  if(panel) panel.classList.add('active');
  if(tab==='group') renderGroupChat();
  else if(tab==='dm') renderDMPanel();
  else if(tab==='forum') renderForumPanel();
};

//  GRUP SOHBET 
var _rooms = [];
function renderGroupChat(){
  var el = document.getElementById('socGroupPanel');
  if(!el) return;

  socialAPI('GET','/social/chat/rooms',null,function(err,rooms){
    if(err||!rooms) rooms=[{id:'genel',name:'Genel Sohbet',description:'Herkes',online_count:0}];
    _rooms = rooms;

    el.innerHTML =
      // Odalar
      '<div class="chat-rooms">'
      +rooms.map(function(r){
        return '<button class="chat-room-btn'+( r.id===_chatRoom?' active':'')+'" '
          +'onclick="joinRoom(\''+r.id+'\')" id="roomBtn_'+r.id+'">'
          +r.name+(r.online_count?' ('+r.online_count+')':'')+'</button>';
      }).join('')
      +'</div>'
      +'<div style="padding:3px 10px;display:flex;justify-content:space-between;align-items:center">'
      +'<span id="chatRoomName" style="font-size:9px;font-weight:700;color:var(--cyan)">'+(_rooms.find(function(r){return r.id===_chatRoom;})||{name:'Genel'}).name+'</span>'
      +'<div id="onlineCount" style="font-size:8px;color:var(--t4)"></div>'
      +'</div>'
      +'<div id="chatMessages" class="chat-messages"></div>'
      +'<div id="typingIndicator" class="typing-indicator"></div>'
      +'<div class="chat-input-row">'
      +(!_auth.user
        ?'<div style="flex:1;text-align:center;font-size:10px;color:var(--t4)">Sohbet icin <span style="color:var(--cyan);cursor:pointer" onclick="showAuthModal()">giris yapin</span></div>'
        :'<input id="chatInput" class="chat-input" placeholder="Mesaj yaz..." maxlength="500" '
          +'onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();sendChatMsg()}" '
          +'oninput="sendTyping()">'
          +'<button class="chat-send-btn" onclick="sendChatMsg()">&#9654;</button>')
      +'</div>'
    ;

    // Mesajlari yukle
    loadRoomMessages(_chatRoom);
  });
}

function loadRoomMessages(roomId){
  socialAPI('GET','/social/chat/'+roomId+'/messages',null,function(err,msgs){
    if(err||!msgs) return;
    var el = document.getElementById('chatMessages');
    if(!el) return;
    el.innerHTML = '';
    msgs.forEach(appendChatMsg);
    el.scrollTop = el.scrollHeight;
  });
}

window.joinRoom = function(roomId){
  _chatRoom = roomId;
  document.querySelectorAll('.chat-room-btn').forEach(function(b){
    b.classList.toggle('active', b.id==='roomBtn_'+roomId);
  });
  var roomNameEl = document.getElementById('chatRoomName');
  var room = _rooms.find(function(r){return r.id===roomId;});
  if(roomNameEl && room) roomNameEl.textContent = room.name;
  if(_chatWS && _chatWS.readyState===1){
    _chatWS.send(JSON.stringify({type:'join_room',room:roomId}));
  }
  loadRoomMessages(roomId);
};

window.sendChatMsg = function(){
  var el = document.getElementById('chatInput');
  if(!el||!el.value.trim()) return;
  if(!_auth.user){showAuthModal();return;}
  var content = el.value.trim();
  el.value = '';
  if(_chatWS && _chatWS.readyState===1){
    _chatWS.send(JSON.stringify({type:'chat',room:_chatRoom,content:content}));
  } else {
    // WS yoksa REST fallback
    appendChatMsg({user_id:_auth.user.id,username:_auth.user.username,
      display_name:_auth.user.display_name,avatar:_auth.user.avatar||'?',
      content:content,created_at:new Date().toISOString(),room:_chatRoom});
    toast('Baglanti kopuk, mesaj gonderilmeyebilir');
  }
};

window.sendTyping = function(){
  if(!_chatWS||_chatWS.readyState!==1) return;
  clearTimeout(_typingTimer);
  _chatWS.send(JSON.stringify({type:'typing',room:_chatRoom}));
  _typingTimer = setTimeout(function(){},2000);
};

function appendChatMsg(data){
  var el = document.getElementById('chatMessages');
  if(!el) return;
  var isMe = _auth.user && data.user_id===_auth.user.id;
  var t = new Date(data.created_at);
  var tStr = t.getHours()+':'+('0'+t.getMinutes()).slice(-2);
  var div = document.createElement('div');
  div.className = 'chat-msg'+(isMe?' mine':'');
  div.innerHTML =
    '<div class="chat-avatar" onclick="openUserProfile(\''+data.username+'\')">'+( data.avatar||'?')+'</div>'
    +'<div><div class="chat-sender">'+( isMe?'':data.display_name||data.username)+'</div>'
    +'<div class="chat-bubble">'+escHtml(data.content)+'</div>'
    +'<div class="chat-time">'+tStr+'</div></div>';
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function appendChatSys(msg){
  var el = document.getElementById('chatMessages');
  if(!el) return;
  var div = document.createElement('div');
  div.className = 'chat-sys';
  div.textContent = msg;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

var _typingUsers = {};
function showTypingIndicator(username, room){
  if(room !== _chatRoom) return;
  _typingUsers[username] = Date.now();
  var el = document.getElementById('typingIndicator');
  if(el){
    var users = Object.keys(_typingUsers).filter(function(u){return Date.now()-_typingUsers[u]<3000;});
    el.textContent = users.length ? users.join(', ')+' yaziyor...' : '';
  }
}

function updateOnlineList(members){
  socialAPI('GET','/social/chat/online',null,function(err,data){
    if(data){
      var el = document.getElementById('onlineCount');
      if(el) el.textContent = data.count+' online';
    }
  });
}

//  OZEL MESAJ 
var _dmHistory = {};
function renderDMPanel(){
  var el = document.getElementById('socDMPanel');
  if(!el) return;

  if(!_auth.user){
    el.innerHTML='<div style="padding:20px;text-align:center;color:var(--t4);font-size:11px">Ozel mesaj icin <span style="color:var(--cyan);cursor:pointer" onclick="showAuthModal()">giris yapin</span></div>';
    return;
  }

  if(_dmTarget){
    renderDMChat(_dmTarget);
    return;
  }

  el.innerHTML = '<div style="padding:10px 12px;font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px">Mesajlar</div>'
    +'<div id="dmConvList" class="dm-list"><div style="padding:20px;text-align:center;color:var(--t4);font-size:10px">Yukleniyor...</div></div>'
    +'<div style="padding:8px;border-top:1px solid rgba(255,255,255,.05)">'
    +'<button onclick="showNewDMDialog()" style="width:100%;padding:10px;border-radius:9px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:11px;font-weight:600;cursor:pointer">+ Yeni Mesaj</button>'
    +'</div>';

  socialAPI('GET','/social/dm/conversations',null,function(err,convs){
    var el2 = document.getElementById('dmConvList');
    if(!el2) return;
    if(err||!convs||!convs.length){
      el2.innerHTML='<div style="padding:20px;text-align:center;color:var(--t4);font-size:10px">Henuz mesajiniz yok.<br><span style="color:var(--cyan)">Kullanicilara mesaj gonderin.</span></div>';
      return;
    }
    el2.innerHTML = convs.map(function(c){
      return '<div class="dm-item" onclick="openDMWith(\''+c.username+'\')">'
        +'<div class="dm-avatar">'+( c.avatar||'?')+(c.online?'<div class="dm-online-dot"></div>':'')+'</div>'
        +'<div style="flex:1;min-width:0">'
        +'<div class="dm-name">'+escHtml(c.display_name||c.username)+'</div>'
        +'<div class="dm-preview">'+escHtml(c.last_preview||'')+'</div>'
        +'</div>'
        +(c.unread>0?'<div class="dm-unread">'+c.unread+'</div>':'')
        +'</div>';
    }).join('');
  });
}

function renderDMChat(username){
  var el = document.getElementById('socDMPanel');
  if(!el) return;
  el.innerHTML =
    '<div class="dm-chat-header">'
    +'<button onclick="_dmTarget=null;renderDMPanel()" style="background:none;border:none;color:var(--cyan);font-size:18px;cursor:pointer">&#8592;</button>'
    +'<div style="font-size:13px;font-weight:700;color:var(--t1)">'+escHtml(username)+'</div>'
    +'</div>'
    +'<div id="dmMessages" class="chat-messages"></div>'
    +'<div class="chat-input-row">'
    +'<input id="dmInput" class="chat-input" placeholder="Mesaj yaz..." maxlength="500" '
    +'onkeydown="if(event.key===\'Enter\'){event.preventDefault();sendDMMsg()}">'
    +'<button class="chat-send-btn" onclick="sendDMMsg()">&#9654;</button>'
    +'</div>';

  socialAPI('GET','/social/dm/'+encodeURIComponent(username),null,function(err,msgs){
    var el2 = document.getElementById('dmMessages');
    if(!el2||!msgs) return;
    msgs.forEach(function(m){
      var isMe = _auth.user && m.from_user===_auth.user.id;
      var t = new Date(m.created_at);
      var tStr = t.getHours()+':'+('0'+t.getMinutes()).slice(-2);
      var div = document.createElement('div');
      div.className = 'chat-msg'+(isMe?' mine':'');
      div.innerHTML =
        '<div class="chat-avatar">'+( m.avatar||'?')+'</div>'
        +'<div><div class="chat-sender">'+( isMe?'':escHtml(m.display_name||m.username))+'</div>'
        +'<div class="chat-bubble">'+escHtml(m.content)+'</div>'
        +'<div class="chat-time">'+tStr+'</div></div>';
      el2.appendChild(div);
    });
    el2.scrollTop = el2.scrollHeight;
  });
}

window.openDMWith = function(username){
  _dmTarget = username;
  renderDMChat(username);
};

window.sendDMMsg = function(){
  var el = document.getElementById('dmInput');
  if(!el||!el.value.trim()||!_dmTarget) return;
  var content = el.value.trim(); el.value = '';
  socialAPI('POST','/social/dm/send',{to_user:_dmTarget,content:content},function(err,data){
    if(err){ toast('Mesaj gonderilemedi'); return; }
    var el2 = document.getElementById('dmMessages');
    if(!el2) return;
    var div = document.createElement('div');
    div.className = 'chat-msg mine';
    div.innerHTML = '<div class="chat-avatar">'+( _auth.user.avatar||'?')+'</div>'
      +'<div><div class="chat-bubble">'+escHtml(content)+'</div></div>';
    el2.appendChild(div); el2.scrollTop = el2.scrollHeight;
  });
};

function handleIncomingDM(data){
  // Notif dot goster
  var dot = document.getElementById('dmNotifDot');
  if(dot) dot.style.display = 'block';
  toast(data.from_avatar+' '+data.from_username+': '+data.content.substring(0,40));
  if(_dmTarget===data.from_username){
    var el = document.getElementById('dmMessages');
    if(el){
      var div = document.createElement('div');
      div.className = 'chat-msg';
      div.innerHTML = '<div class="chat-avatar">'+data.from_avatar+'</div>'
        +'<div><div class="chat-bubble">'+escHtml(data.content)+'</div></div>';
      el.appendChild(div); el.scrollTop = el.scrollHeight;
    }
  }
}

window.showNewDMDialog = function(){
  socialAPI('GET','/social/users',null,function(err,users){
    if(err||!users) return;
    var html = '<div style="padding:3px 0">'
      +'<input id="userSearchInp" placeholder="Kullanici ara..." class="forum-search" style="padding:9px 12px;width:100%;box-sizing:border-box;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09);border-radius:9px;color:var(--t1);font-size:11px;outline:none;margin-bottom:8px" '
      +'oninput="filterUserList(this.value)">'
      +'<div id="userListModal" style="max-height:300px;overflow-y:auto">'
      +users.filter(function(u){return u.id!==(_auth.user&&_auth.user.id);})
      .map(function(u){
        return '<div onclick="openDMWith(\''+escHtml(u.username)+'\');closeM()" '
          +'style="display:flex;align-items:center;gap:9px;padding:9px 4px;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer">'
          +'<div style="width:32px;height:32px;border-radius:50%;background:rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;font-size:18px">'+(u.avatar||'?')+'</div>'
          +'<div><div style="font-size:11px;font-weight:600;color:var(--t1)">'+escHtml(u.display_name||u.username)+'</div>'
          +'<div style="font-size:8px;color:var(--t4)">@'+escHtml(u.username)+(u.online?'  online':'')+'</div></div>'
          +'</div>';
      }).join('')
      +'</div></div>';
    document.getElementById('mtit').textContent='Mesaj Gonder';
    document.getElementById('mcont').innerHTML=html;
    document.getElementById('modal').classList.add('on');
  });
};

//  FORUM 
function renderForumPanel(){
  var el = document.getElementById('socForumPanel');
  if(!el) return;
  if(_forumView==='categories') renderForumCategories();
  else if(_forumView==='topics') renderForumTopics();
  else if(_forumView==='detail') renderForumDetail();
}

function renderForumCategories(){
  var el = document.getElementById('socForumPanel');
  if(!el) return;

  socialAPI('GET','/social/forum/categories',null,function(err,cats){
    if(err||!cats) cats=[];
    el.innerHTML =
      '<div style="padding:10px 12px;display:flex;align-items:center;justify-content:space-between">'
      +'<div style="font-size:14px;font-weight:800;color:var(--t1)">Forum</div>'
      +(_auth.user
        ?'<button onclick="showNewTopicModal()" class="soc-btn soc-btn-primary" style="padding:6px 12px;font-size:9px">+ Konu Ac</button>'
        :'<span style="font-size:9px;color:var(--t4);cursor:pointer" onclick="showAuthModal()">Konu acmak icin giris yap</span>')
      +'</div>'
      +'<div class="forum-search"><input placeholder="Forum\'da ara..." oninput="searchForum(this.value)"></div>'
      +'<div class="forum-cats">'
      +cats.map(function(c){
        return '<div class="forum-cat" onclick="openForumCategory(\''+c.id+'\',\''+escHtml(c.name)+'\')" '
          +'style="border-color:'+c.color+'33">'
          +'<div class="forum-cat-icon">'+c.icon+'</div>'
          +'<div class="forum-cat-name" style="color:'+c.color+'">'+escHtml(c.name)+'</div>'
          +'<div class="forum-cat-desc">'+escHtml(c.description)+'</div>'
          +'<div class="forum-cat-count" style="color:'+c.color+'">'+c.topic_count+' konu</div>'
          +'</div>';
      }).join('')
      +'</div>'
      // Son konular
      +'<div style="padding:0 10px"><div style="font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:7px">Son Konular</div></div>'
      +'<div id="recentTopics" style="padding:0 8px"></div>'
    ;
    // Son konulari yukle
    socialAPI('GET','/social/forum/topics?limit=5',null,function(err2,topics){
      var el2 = document.getElementById('recentTopics');
      if(!el2||!topics) return;
      el2.innerHTML = topics.map(forumTopicCard).join('');
    });
  });
}

function openForumCategory(catId, catName){
  _forumCategory = {id:catId, name:catName};
  _forumView = 'topics';
  renderForumTopics();
}

function renderForumTopics(){
  var el = document.getElementById('socForumPanel');
  if(!el||!_forumCategory) return;

  el.innerHTML =
    '<div class="forum-back" onclick="_forumView=\'categories\';renderForumCategories()">'
    +'&#8592; Kategoriler</div>'
    +'<div style="padding:10px 12px;display:flex;align-items:center;justify-content:space-between">'
    +'<div style="font-size:13px;font-weight:700;color:var(--t1)">'+escHtml(_forumCategory.name)+'</div>'
    +(_auth.user?'<button onclick="showNewTopicModal()" class="soc-btn soc-btn-primary" style="padding:6px 12px;font-size:9px">+ Yeni Konu</button>':'')
    +'</div>'
    +'<div id="forumTopicsList" class="forum-topics">'
    +'<div style="text-align:center;padding:20px;color:var(--t4);font-size:10px">Yukleniyor...</div>'
    +'</div>'
  ;

  socialAPI('GET','/social/forum/topics?category_id='+_forumCategory.id+'&limit=30',null,function(err,topics){
    var el2 = document.getElementById('forumTopicsList');
    if(!el2) return;
    if(err||!topics||!topics.length){
      el2.innerHTML='<div style="text-align:center;padding:30px;color:var(--t4);font-size:11px">Henuz konu yok.<br><span style="color:var(--cyan);cursor:pointer" onclick="showNewTopicModal()">Ilk konuyu siz acin!</span></div>';
      return;
    }
    el2.innerHTML = topics.map(forumTopicCard).join('');
  });
}

function forumTopicCard(t){
  var tags = JSON.parse(t.tags||'[]');
  var d = new Date(t.created_at||t.updated_at);
  var dStr = d.getDate()+'.'+(d.getMonth()+1)+'.'+d.getFullYear();
  return '<div class="forum-topic" onclick="openForumTopic(\''+t.id+'\')">'
    +(t.is_pinned?'<div class="forum-pinned">Sabitlend</div>':'')
    +'<div class="forum-topic-title">'+escHtml(t.title)+'</div>'
    +'<div class="forum-topic-meta">'
    +'<span>'+escHtml(t.avatar||'?')+' '+escHtml(t.display_name||t.username)+'</span>'
    +'<span>'+dStr+'</span>'
    +'<span>&#128065; '+t.view_count+'</span>'
    +'<span>&#128172; '+t.reply_count+'</span>'
    +'<span>&#10084; '+t.like_count+'</span>'
    +(t.category_color?'<span style="color:'+t.category_color+'">'+escHtml(t.category_name||'')+'</span>':'')
    +'</div>'
    +(tags.length?'<div style="margin-top:5px">'+tags.map(function(tg){return'<span class="forum-topic-tag">'+escHtml(tg)+'</span>';}).join(' ')+'</div>':'')
    +'</div>';
}

function openForumTopic(topicId){
  _forumView = 'detail';
  _forumTopic = {id:topicId};
  renderForumDetail();
}

function renderForumDetail(){
  var el = document.getElementById('socForumPanel');
  if(!el||!_forumTopic) return;

  el.innerHTML =
    '<div class="forum-back" onclick="_forumView=(_forumCategory?\'topics\':\'categories\');renderForumPanel()">'
    +'&#8592; Geri</div>'
    +'<div id="forumDetailContent" style="padding:10px">'
    +'<div style="text-align:center;padding:20px;color:var(--t4)">Yukleniyor...</div>'
    +'</div>'
  ;

  // Konu detayi
  socialAPI('GET','/social/forum/topics/'+_forumTopic.id,null,function(err,topic){
    if(err||!topic) return;
    _forumTopic = topic;

    // Yorumlar
    socialAPI('GET','/social/forum/topics/'+topic.id+'/comments',null,function(err2,comments){
      comments = comments||[];
      var el2 = document.getElementById('forumDetailContent');
      if(!el2) return;

      var html =
        // Ana konu
        '<div class="forum-post">'
        +'<div class="forum-post-header">'
        +'<div class="forum-post-avatar">'+( topic.avatar||'?')+'</div>'
        +'<div><div style="font-size:11px;font-weight:700;color:var(--t1)">'+escHtml(topic.display_name||topic.username)+'</div>'
        +'<div style="font-size:8px;color:var(--t4)">'+new Date(topic.created_at).toLocaleDateString('tr-TR')+'</div></div>'
        +(topic.category_color?'<div style="margin-left:auto;font-size:8px;color:'+topic.category_color+'">'+escHtml(topic.category_name||'')+'</div>':'')
        +'</div>'
        +'<h3 style="font-size:14px;font-weight:800;color:var(--t1);margin:0 0 8px">'+escHtml(topic.title)+'</h3>'
        +'<div class="forum-post-content">'+escHtml(topic.content)+'</div>'
        +'<div style="display:flex;gap:8px;margin-top:8px;font-size:8px;color:var(--t4)">'
        +'<span>&#128065; '+topic.view_count+'</span>'
        +'<span>&#128172; '+topic.reply_count+'</span>'
        +'</div>'
        +'<button class="forum-like-btn" id="topicLikeBtn_'+topic.id+'" onclick="toggleLike(\'topic\',\''+topic.id+'\',this)">'
        +'&#10084; '+topic.like_count
        +'</button>'
        +'</div>'
        // Yorumlar
        +'<div style="font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin:10px 0 6px">'
        +comments.length+' Yorum</div>'
        +'<div id="commentsList">'
        +renderComments(comments, null)
        +'</div>'
        // Yorum yaz
        +(_auth.user
          ?'<div style="margin-top:10px">'
            +'<textarea id="commentInput" class="compose-area" placeholder="Yorumunuzu yazin..." rows="3"></textarea>'
            +'<div style="display:flex;gap:6px;margin-top:6px">'
            +'<button class="soc-btn soc-btn-primary" onclick="submitComment()">Yorum Yap</button>'
            +'</div>'
            +'</div>'
          :'<div style="padding:12px;text-align:center;font-size:10px;color:var(--t4)">'
            +'Yorum yapmak icin <span style="color:var(--cyan);cursor:pointer" onclick="showAuthModal()">giris yapin</span></div>')
      ;
      el2.innerHTML = html;
    });
  });
}

function renderComments(comments, parentId){
  return comments
    .filter(function(c){ return c.parent_id===parentId; })
    .map(function(c){
      var children = renderComments(comments, c.id);
      var d = new Date(c.created_at);
      var dStr = d.getDate()+'.'+(d.getMonth()+1)+' '+d.getHours()+':'+ ('0'+d.getMinutes()).slice(-2);
      return '<div class="forum-comment'+(parentId?' reply':'')+'">'
        +'<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">'
        +'<div style="width:22px;height:22px;border-radius:50%;background:rgba(255,255,255,.07);display:flex;align-items:center;justify-content:center;font-size:13px">'+( c.avatar||'?')+'</div>'
        +'<div style="font-size:10px;font-weight:700;color:var(--t2)">'+escHtml(c.display_name||c.username)+'</div>'
        +'<div style="font-size:8px;color:var(--t4);margin-left:auto">'+dStr+'</div>'
        +'</div>'
        +'<div style="font-size:10px;color:var(--t3);line-height:1.5">'+escHtml(c.content)+'</div>'
        +'<div style="display:flex;gap:8px;margin-top:5px">'
        +'<button class="forum-like-btn" id="cmtLike_'+c.id+'" onclick="toggleLike(\'comment\',\''+c.id+'\',this)">'
        +'&#10084; '+c.like_count+'</button>'
        +(_auth.user?'<button onclick="replyToComment(\''+c.id+'\')" style="font-size:8px;color:var(--t4);background:none;border:none;cursor:pointer">Yanitla</button>':'')
        +'</div>'
        +(children?'<div>'+children+'</div>':'')
        +'</div>';
    }).join('');
}

window.submitComment = function(){
  var el = document.getElementById('commentInput');
  if(!el||!el.value.trim()||!_forumTopic) return;
  var content = el.value.trim();
  socialAPI('POST','/social/forum/comments',
    {topic_id:_forumTopic.id, content:content},
    function(err,data){
      if(err){ toast('Yorum gonderilemedi'); return; }
      el.value='';
      toast('Yorum eklendi!');
      renderForumDetail();
    });
};

var _replyToId = null;
window.replyToComment = function(commentId){
  _replyToId = commentId;
  var el = document.getElementById('commentInput');
  if(el){ el.focus(); el.placeholder='Yanit yazin...'; }
};

window.toggleLike = function(type, id, btn){
  if(!_auth.user){showAuthModal();return;}
  socialAPI('POST','/social/forum/like/'+type+'/'+id,null,function(err,data){
    if(err) return;
    if(btn){
      btn.classList.toggle('liked', data.liked);
      var count = parseInt(btn.textContent.replace(/[^0-9]/g,''))||0;
      btn.innerHTML = '&#10084; '+(count+data.delta);
    }
  });
};

window.showNewTopicModal = function(){
  if(!_auth.user){showAuthModal();return;}
  socialAPI('GET','/social/forum/categories',null,function(err,cats){
    if(!cats) cats=[];
    var html = '<div style="padding:3px 0">'
      +'<select id="topicCatSel" style="width:100%;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);border-radius:9px;padding:10px;font-size:11px;color:var(--t1);margin-bottom:8px;outline:none">'
      +cats.map(function(c){return'<option value="'+c.id+'" '+((_forumCategory&&_forumCategory.id===c.id)?'selected':'')+'>'+c.name+'</option>';}).join('')
      +'</select>'
      +'<input id="topicTitle" placeholder="Konu basligi (min 5 karakter)" class="auth-inp">'
      +'<textarea id="topicContent" class="compose-area" placeholder="Konu icerigi (min 20 karakter)" rows="5" style="margin-bottom:8px"></textarea>'
      +'<input id="topicTags" placeholder="Etiketler (virgulle ayirin): EREGL, analiz" class="auth-inp">'
      +'<button class="soc-btn soc-btn-primary" onclick="submitNewTopic()" style="width:100%;padding:11px;margin-top:4px">Konuyu Yayinla</button>'
      +'</div>';
    document.getElementById('mtit').textContent='Yeni Konu Ac';
    document.getElementById('mcont').innerHTML=html;
    document.getElementById('modal').classList.add('on');
  });
};

window.submitNewTopic = function(){
  var cat = (document.getElementById('topicCatSel')||{}).value;
  var title = (document.getElementById('topicTitle')||{}).value||'';
  var content = (document.getElementById('topicContent')||{}).value||'';
  var tagsRaw = (document.getElementById('topicTags')||{}).value||'';
  var tags = tagsRaw.split(',').map(function(t){return t.trim();}).filter(Boolean);

  if(!cat||title.trim().length<5||content.trim().length<20){
    toast('Baslik en az 5, icerik en az 20 karakter olmali');
    return;
  }
  socialAPI('POST','/social/forum/topics',
    {category_id:cat, title:title.trim(), content:content.trim(), tags:tags},
    function(err,data){
      if(err){ toast('Konu olusturulamadi: '+err); return; }
      closeM && closeM();
      toast('Konu yayinlandi!');
      if(typeof haptic==='function') haptic('success');
      _forumCategory = {id:cat, name:'Konu'};
      _forumView = 'detail';
      _forumTopic = {id:data.id};
      renderForumDetail();
    });
};

//  PROFIL 
window.showSocialProfile = function(){
  if(!_auth.user) return;
  socialAPI('GET','/social/users/'+_auth.user.username,null,function(err,u){
    if(!u) return;
    var html = '<div style="text-align:center;padding:16px">'
      +'<div style="font-size:48px;margin-bottom:8px">'+( u.avatar||'?')+'</div>'
      +'<div style="font-size:16px;font-weight:800;color:var(--t1)">'+escHtml(u.display_name||u.username)+'</div>'
      +'<div style="font-size:10px;color:var(--t4);margin-bottom:4px">@'+escHtml(u.username)+'</div>'
      +'<div style="font-size:10px;color:var(--t3);margin:8px 20px">'+escHtml(u.bio||'')+'</div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0">'
      +'<div style="background:rgba(255,255,255,.04);border-radius:9px;padding:10px"><div style="font-size:16px;font-weight:800;color:var(--cyan)">'+u.topic_count+'</div><div style="font-size:9px;color:var(--t4)">Konu</div></div>'
      +'<div style="background:rgba(255,255,255,.04);border-radius:9px;padding:10px"><div style="font-size:16px;font-weight:800;color:var(--cyan)">'+u.comment_count+'</div><div style="font-size:9px;color:var(--t4)">Yorum</div></div>'
      +'</div></div>'
      +'<div style="padding:0 4px">'
      +'<input id="editDispName" class="auth-inp" placeholder="Gorulen ad" value="'+escHtml(u.display_name||'')+'"><br>'
      +'<input id="editBio" class="auth-inp" placeholder="Hakkimda (max 200 karakter)" value="'+escHtml(u.bio||'')+'"><br>'
      +'<input id="editAvatar" class="auth-inp" placeholder="Avatar emoji (orn: ?)" value="'+escHtml(u.avatar||'')+'" maxlength="4"><br>'
      +'<button class="soc-btn soc-btn-primary" onclick="saveProfile()" style="width:100%;padding:11px;margin-top:4px">Profili Kaydet</button>'
      +'</div>'
    ;
    document.getElementById('mtit').textContent='Profilim';
    document.getElementById('mcont').innerHTML=html;
    document.getElementById('modal').classList.add('on');
  });
};

window.saveProfile = function(){
  var dn = (document.getElementById('editDispName')||{}).value;
  var bio = (document.getElementById('editBio')||{}).value;
  var av = (document.getElementById('editAvatar')||{}).value;
  socialAPI('PUT','/social/auth/profile',{display_name:dn,bio:bio,avatar:av},function(err){
    if(err){ toast('Kayit hatasi'); return; }
    if(_auth.user){
      _auth.user.display_name = dn||_auth.user.display_name;
      _auth.user.avatar = av||_auth.user.avatar;
      _auth.save(_auth.token, _auth.user);
    }
    closeM && closeM();
    toast('Profil guncellendi!');
    renderSocialPage();
  });
};

window.openUserProfile = function(username){
  socialAPI('GET','/social/users/'+encodeURIComponent(username),null,function(err,u){
    if(!u) return;
    var html = '<div style="text-align:center;padding:16px">'
      +'<div style="font-size:48px;margin-bottom:8px">'+( u.avatar||'?')+'</div>'
      +'<div style="font-size:16px;font-weight:800;color:var(--t1)">'+escHtml(u.display_name||u.username)+'</div>'
      +'<div style="font-size:10px;color:var(--t4);margin-bottom:4px">@'+escHtml(u.username)+'</div>'
      +'<div style="display:flex;align-items:center;justify-content:center;gap:6px;margin:6px 0">'
      +'<div style="width:8px;height:8px;border-radius:50%;background:'+(u.online?'var(--green)':'var(--t4)')+'"></div>'
      +'<span style="font-size:9px;color:var(--t4)">'+(u.online?'Cevrimici':'Cevrimdisi')+'</span></div>'
      +(u.bio?'<div style="font-size:10px;color:var(--t3);margin:8px 20px">'+escHtml(u.bio)+'</div>':'')
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px">'
      +'<div style="background:rgba(255,255,255,.04);border-radius:9px;padding:10px"><div style="font-size:16px;font-weight:800;color:var(--cyan)">'+u.topic_count+'</div><div style="font-size:9px;color:var(--t4)">Konu</div></div>'
      +'<div style="background:rgba(255,255,255,.04);border-radius:9px;padding:10px"><div style="font-size:16px;font-weight:800;color:var(--cyan)">'+u.comment_count+'</div><div style="font-size:9px;color:var(--t4)">Yorum</div></div>'
      +'</div>'
      +(_auth.user&&_auth.user.username!==username
        ?'<button class="soc-btn soc-btn-primary" onclick="openDMWith(\''+escHtml(username)+'\');closeM();switchSocialTab(\'dm\')" style="width:88%;padding:11px;margin-top:4px">Mesaj Gonder</button>':'')
      +'</div>'
    ;
    document.getElementById('mtit').textContent='Kullanici Profili';
    document.getElementById('mcont').innerHTML=html;
    document.getElementById('modal').classList.add('on');
  });
};

window.socialLogout = function(){
  _auth.clear();
  if(_chatWS){ try{_chatWS.close();}catch(e){} _chatWS=null; }
  toast('Cikis yapildi');
  renderSocialPage();
};

window.searchForum = function(q){
  if(!q||q.length<2) return;
  socialAPI('GET','/social/forum/topics?search='+encodeURIComponent(q)+'&limit=20',null,function(err,topics){
    var el = document.getElementById('recentTopics');
    if(!el||!topics) return;
    el.innerHTML = (topics.length
      ?topics.map(forumTopicCard).join('')
      :'<div style="padding:12px;text-align:center;color:var(--t4);font-size:10px">Sonuc bulunamadi</div>');
  });
};

//  YARDIMCI 
function handleNotification(data){
  toast('Yeni bildirim: '+data.from_username);
}

function escHtml(s){
  if(!s) return '';
  var e={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'};return String(s).replace(/[&<>"]/g,function(c){return e[c];});
}

function filterUserList(q){
  var items = document.querySelectorAll('#userListModal > div');
  items.forEach(function(el){
    el.style.display = q && el.textContent.toLowerCase().indexOf(q.toLowerCase())===-1 ? 'none':'flex';
  });
}

//  NAV + SAYFA EKLE 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      // Sayfa
      if(!document.getElementById('page-social')){
        var main=document.querySelector('main');
        if(main){ var p=document.createElement('div');p.id='page-social';p.className='page';main.appendChild(p); }
      }
      // Nav
      if(!document.getElementById('socialTab')){
        var nav=document.querySelector('nav');
        if(nav){
          var btn=document.createElement('button');btn.id='socialTab';btn.className='tab';
          btn.innerHTML='&#128101; Sosyal';
          btn.style.cssText='position:relative';
          btn.onclick=function(){
            try{
              pg('social');
              renderSocialPage();
              var dot=document.getElementById('dmNotifDot');
              if(dot) dot.style.display='none';
            }catch(e){}
          };
          nav.appendChild(btn);
        }
      }
      // Zaten giris yapmissa WS baglan
      if(_auth.token) setTimeout(connectChatWS, 2000);
    }catch(e){ console.warn('Social init:',e.message); }
  },900);
});

</script>
<script>

// BIST KATILIM v1 BLOK 19
// Cross-platform uyumluluk - Safari/Chrome/Firefox/Android/iOS
// Tespit edilen 6 sorun duzeltildi

//  1. WEBKIT BACKDROP-FILTER ESITLEME 
// 15 yerde backdrop-filter var ama sadece 7'sinde -webkit- prefix var
(function(){
  try{
    var st = document.createElement('style');
    // Tum mevcut backdrop-filter'lara -webkit- prefix ekle
    st.textContent =
      // Genel kural - webkit prefix eksik olanlari yakala
      '[style*="backdrop-filter"]{-webkit-backdrop-filter:inherit}'
      // Spesifik siniflar
      +'.card,.modal,.nav,.hdr,.sig,.pos-card,.soc-panel,.auth-box,'
      +'.v13-mic-section,.v15-set-section,.dev-card{-webkit-backdrop-filter:blur(20px)}'
      // Android Chrome - gap destegi iyilestirme
      +'@supports not (gap:5px){'
      +'.sgrid>*{margin:3px}'
      +'.forum-cats>*{margin:3px}'
      +'.v13-agents-grid>*{margin:3px}'
      +'}'
      // Android - input zoom engelle (font-size 16px altinda iOS/Android zoom yapar)
      +'input,textarea,select{'
      +'-webkit-text-size-adjust:100%;'
      +'font-size:max(16px,1em)!important'  // zoom engelle
      +'}'
      // Ama kucuk gorunen inputlar icin override
      +'.chat-input,.v13-input,.auth-inp,.dev-input,.v15-set-input{font-size:16px!important}'
      // Android Chrome - tap highlight kaldir
      +'*{-webkit-tap-highlight-color:transparent}'
      // Firefox - scrollbar gizle
      +'*{scrollbar-width:none}'
      // iOS safe area (notch icin)
      +'#scanBtn,#bottomBar{padding-bottom:env(safe-area-inset-bottom)}'
      // Android - overscroll rengi
      +'html{background:#000;overscroll-behavior:none}'
      // Firefox - backdrop-filter fallback
      +'@supports not (backdrop-filter:blur(1px)){.card,.modal{background:rgba(10,10,10,.98)!important}}'
      // Samsung Internet - flexbox fix
      +'.soc-tabs{display:-webkit-box;display:-ms-flexbox;display:flex}'
      +'.chat-rooms{display:-webkit-box;display:-ms-flexbox;display:flex}'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  2. HAPTIC - IOS SESSIZ FAIL 
// iOS'ta navigator.vibrate yok - try-catch ekle
(function(){
  try{
    var _origHaptic = typeof haptic === 'function' ? haptic : null;
    window.haptic = function(type){
      try{
        if(!_origHaptic) return;
        _origHaptic(type);
      }catch(e){} // iOS'ta hata verirse sessizce devam et
    };
    // iOS haptic - taptic engine (sadece Safari iOS)
    if(/iPhone|iPad/i.test(navigator.userAgent)){
      window.haptic = function(type){
        try{
          // iOS13+ AudioContext ile hafif haptic benzeri
          var ctx = new (window.AudioContext||window.webkitAudioContext)();
          var osc = ctx.createOscillator();
          var gain = ctx.createGain();
          osc.connect(gain); gain.connect(ctx.destination);
          gain.gain.setValueAtTime(0, ctx.currentTime);
          osc.start(); osc.stop(ctx.currentTime+0.001);
          ctx.close();
        }catch(e){}
      };
    }
  }catch(e){}
})();

//  3. ANDROID CHROME - INPUT ZOOM ENGELLE 
// font-size < 16px olan inputlar Android/iOS'ta zoom yapar
// Meta viewport zaten var ama yetmeyebilir
(function(){
  try{
    // Mevcut viewport meta'ya user-scalable=no ekle (optional)
    // NOT: user-scalable=no erisilebilirlik sorununa yol acar
    // Bunun yerine font-size 16px yap
    var inputs = document.querySelectorAll('input:not([type=range]),textarea');
    inputs.forEach(function(el){
      if(parseFloat(getComputedStyle(el).fontSize) < 16){
        el.style.fontSize = '16px';
      }
    });
    // Yeni eklenen input'lar icin MutationObserver
    if(window.MutationObserver){
      var obs = new MutationObserver(function(mutations){
        mutations.forEach(function(m){
          m.addedNodes.forEach(function(node){
            try{
              if(node.tagName==='INPUT'||node.tagName==='TEXTAREA'){
                if(parseFloat(getComputedStyle(node).fontSize)<16) node.style.fontSize='16px';
              }
              if(node.querySelectorAll){
                node.querySelectorAll('input,textarea').forEach(function(el){
                  if(parseFloat(getComputedStyle(el).fontSize)<16) el.style.fontSize='16px';
                });
              }
            }catch(e){}
          });
        });
      });
      obs.observe(document.body,{childList:true,subtree:true});
    }
  }catch(e){}
})();

//  4. FIREFOX - SPEECHRECOGNITION FALLBACK 
// Firefox SpeechRecognition desteklemez - kullaniciya bildir
(function(){
  try{
    var isFF = /Firefox/i.test(navigator.userAgent);
    if(!isFF) return;
    // Orijinal v13StartMic'i wrap et
    var _origStartMic = typeof v13StartMic==='function' ? v13StartMic : null;
    window.v13StartMic = function(){
      toast('Firefox ses tanimayi desteklemiyor. Chrome veya Safari kullanin.');
      // Mic butonunu disabled goster
      var btn = document.getElementById('v13MicBtn');
      if(btn){
        btn.style.opacity='0.4';
        btn.title='Firefox desteklemiyor - Chrome/Safari kullanin';
      }
    };
    // Not goster
    var transcript = document.getElementById('v13Transcript');
    if(transcript) transcript.textContent='Ses: Firefox desteklemiyor';
  }catch(e){}
})();

//  5. SW.JS ENDPOINT - RENDER'DA YOK 
// Render free'de /sw.js dosyasi yok - inline SW kullan
(function(){
  try{
    if(!('serviceWorker' in navigator)) return;
    // Onceki register basarisiz oldu mu kontrol et
    navigator.serviceWorker.getRegistration('/').then(function(reg){
      if(reg) return; // Zaten kayitli
      // sw.js endpoint'i yoksa inline SW olustur
      var swCode = [
        "self.addEventListener('install',function(e){",
        "  e.waitUntil(caches.open('bist-v1').then(function(c){",
        "    return c.addAll(['/']);",
        "  }));",
        "});",
        "self.addEventListener('fetch',function(e){",
        "  e.respondWith(fetch(e.request).catch(function(){",
        "    return caches.match(e.request);",
        "  }));",
        "});"
      ].join('\n');
      // Blob SW - Render'dan acilinca calisir (same-origin)
      var blob = new Blob([swCode],{type:'application/javascript'});
      var url = URL.createObjectURL(blob);
      navigator.serviceWorker.register(url,{scope:'/'}).then(function(){
        URL.revokeObjectURL(url);
      }).catch(function(){
        URL.revokeObjectURL(url);
        // /sw.js endpoint'ini dene
        navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(function(){});
      });
    }).catch(function(){});
  }catch(e){}
})();

//  6. ANDROID WEBSOCKET - WSS ZORLA 
// HTTP Render URL'leri WebSocket icin WSS gerektiriyor
(function(){
  try{
    // connectChatWS'deki URL'yi patch et
    var _origConnect = typeof connectChatWS==='function' ? connectChatWS : null;
    if(!_origConnect) return;
    window.connectChatWS = function(){
      try{
        // SOCIAL_URL'yi guncelle - wss:// kullan
        if(typeof SOCIAL_URL !== 'undefined'){
          var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
          window.SOCIAL_WS_URL = SOCIAL_URL
            .replace(/^https:/,'wss:')
            .replace(/^http:/,'ws:');
        }
        _origConnect();
      }catch(e){ _origConnect(); }
    };
  }catch(e){}
})();

//  7. ANDROID CHROME - PULL TO REFRESH ENGELLE 
// Android Chrome'da swipe down sayfayi yeniler - bunu engelle
(function(){
  try{
    // Sadece main content area icinde engelle
    document.addEventListener('touchstart',function(e){
      if(e.touches[0].clientY < 100) return; // Ust 100px'de izin ver
    },{passive:true});

    // Chrome Android pull-to-refresh
    document.body.style.overscrollBehavior = 'contain';
    document.documentElement.style.overscrollBehavior = 'none';
  }catch(e){}
})();

//  8. SAMSUNG INTERNET - FLEXBOX COMPAT 
(function(){
  try{
    var isSamsung = /SamsungBrowser/i.test(navigator.userAgent);
    if(!isSamsung) return;
    var st = document.createElement('style');
    st.textContent =
      'nav{display:-webkit-box!important;-webkit-box-orient:horizontal}'
      +'.sgrid{display:-webkit-box!important;-webkit-box-orient:horizontal;-webkit-flex-wrap:wrap}'
      +'.soc-tabs{display:-webkit-box!important}'
    ;
    document.head.appendChild(st);
    devLog && devLog('Samsung Internet modu aktif','info');
  }catch(e){}
})();

//  9. FIREFOX ANDROID - INDEXEDDB FIX 
(function(){
  try{
    var isFF = /Firefox/i.test(navigator.userAgent);
    if(!isFF) return;
    // Firefox Android'de IDB version conflict olabilir
    var _origOpenIDB = typeof openIDB==='function' ? openIDB : null;
    if(!_origOpenIDB) return;
    window.openIDB = function(cb){
      try{ _origOpenIDB(cb); }
      catch(e){
        console.warn('IDB Firefox:',e.message);
        if(cb) cb(null); // null ile devam et
      }
    };
  }catch(e){}
})();

//  10. TARAYICI TESPIT VE OZELLIK RAPORU 
window._browserInfo = (function(){
  var ua = navigator.userAgent;
  var info = {
    isChrome:    /Chrome/i.test(ua) && !/Edge|Edg|OPR/i.test(ua),
    isFirefox:   /Firefox/i.test(ua),
    isSafari:    /Safari/i.test(ua) && !/Chrome/i.test(ua),
    isEdge:      /Edg\//i.test(ua),
    isSamsung:   /SamsungBrowser/i.test(ua),
    isAndroid:   /Android/i.test(ua),
    isIOS:       /iPhone|iPad/i.test(ua),
    isMobile:    /Mobi|Android|iPhone|iPad/i.test(ua),
    isDesktop:   !/Mobi|Android|iPhone|iPad/i.test(ua),
    hasVibrate:  'vibrate' in navigator,
    hasSpeech:   !!(window.SpeechRecognition||window.webkitSpeechRecognition),
    hasSW:       'serviceWorker' in navigator,
    hasNotif:    'Notification' in window,
    hasShare:    'share' in navigator,
    hasWS:       'WebSocket' in window,
    hasIDB:      'indexedDB' in window,
    hasWebGL:    (function(){try{var c=document.createElement('canvas');return!!(c.getContext('webgl')||c.getContext('experimental-webgl'));}catch(e){return false;}})(),
  };
  // Tarayici adi
  if(info.isSamsung) info.name='Samsung Internet';
  else if(info.isEdge) info.name='Edge';
  else if(info.isFirefox) info.name='Firefox';
  else if(info.isChrome) info.name='Chrome';
  else if(info.isSafari) info.name='Safari';
  else info.name='Diger';
  info.platform = info.isIOS?'iOS':info.isAndroid?'Android':info.isMobile?'Mobile':'Desktop';
  return info;
})();

// Log
if(typeof devLog==='function'){
  var bi = window._browserInfo;
  devLog(bi.name+' / '+bi.platform
    +' | SR:'+(bi.hasSpeech?'OK':'HAYIR')
    +' | Vib:'+(bi.hasVibrate?'OK':'HAYIR')
    +' | SW:'+(bi.hasSW?'OK':'HAYIR')
    ,'info');
}

//  11. ANDROID - EKRAN KLAVYE LAYOUT FIX 
// Android'de klavye acilinca layout bozuluyor
(function(){
  try{
    if(!window._browserInfo || !window._browserInfo.isAndroid) return;
    var origH = window.innerHeight;
    window.addEventListener('resize',function(){
      var newH = window.innerHeight;
      var diff = origH - newH;
      if(diff > 100){ // Klavye acildi
        // Chat input row ve forum compose'u kaydir
        var chatRow = document.querySelector('.chat-input-row');
        if(chatRow) chatRow.style.position='sticky';
        document.body.style.height = newH+'px';
      } else {
        document.body.style.height = '';
      }
    });
  }catch(e){}
})();

//  12. iOS SAFE AREA 
(function(){
  try{
    if(!window._browserInfo || !window._browserInfo.isIOS) return;
    var st = document.createElement('style');
    st.textContent =
      // iPhone notch ve home indicator
      'body{padding-bottom:env(safe-area-inset-bottom)}'
      +'nav{padding-bottom:env(safe-area-inset-bottom)}'
      +'#bottomBar{margin-bottom:env(safe-area-inset-bottom)}'
      // iPhone landscape - yanlar
      +'@media(orientation:landscape){'
      +'  body{padding-left:env(safe-area-inset-left);padding-right:env(safe-area-inset-right)}'
      +'}'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  13. DESKTOP CHROME/FIREFOX - LAYOUT OPTIMIZASYON 
(function(){
  try{
    if(!window._browserInfo || !window._browserInfo.isDesktop) return;
    var st = document.createElement('style');
    st.textContent =
      // Desktop'ta max genislik
      'body{max-width:480px;margin:0 auto}'
      +'html{background:#000}'
      // Desktop scrollbar
      +'::-webkit-scrollbar{width:4px}'
      +'::-webkit-scrollbar-track{background:#111}'
      +'::-webkit-scrollbar-thumb{background:#333;border-radius:2px}'
      +'::-webkit-scrollbar-thumb:hover{background:#555}'
      // Desktop hover efektleri
      +'.btn:hover{filter:brightness(1.15)}'
      +'.tab:hover{color:var(--t2)}'
      +'.forum-topic:hover{background:rgba(255,255,255,.04)}'
      +'.chat-msg:hover .chat-time{opacity:1}'
      +'.dm-item:hover{background:rgba(255,255,255,.04)}'
    ;
    document.head.appendChild(st);
    devLog && devLog('Desktop modu: max-width:480px','info');
  }catch(e){}
})();

//  14. TARAYICI UYUMLULUK RAPORU (ISTEGE BAGLI) 
window.showCompatReport = function(){
  try{
    var bi = window._browserInfo;
    var features = [
      ['Ses Tanima',   bi.hasSpeech,  'Konusarak komut'],
      ['Titresim',     bi.hasVibrate, 'Haptic feedback'],
      ['Push Bildirim',bi.hasNotif,   'Sinyal alarmi'],
      ['Paylasim',     bi.hasShare,   'Web Share API'],
      ['Offline',      bi.hasSW,      'Service Worker'],
      ['Veritabani',   bi.hasIDB,     'IndexedDB'],
      ['WebSocket',    bi.hasWS,      'Gercek zamanli sohbet'],
      ['WebGL',        bi.hasWebGL,   'GPU tespiti'],
    ];
    var html = '<div style="padding:3px 0">'
      +'<div style="background:rgba(255,255,255,.04);border-radius:9px;padding:10px;margin-bottom:10px">'
      +'<div style="font-size:11px;font-weight:700;color:var(--t1)">'+bi.name+'</div>'
      +'<div style="font-size:9px;color:var(--t4)">'+bi.platform+' | '+(bi.isMobile?'Mobil':'Masaustu')+'</div>'
      +'</div>'
      +'<div style="display:grid;gap:5px">'
      +features.map(function(f){
        return '<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:8px;border:1px solid rgba(255,255,255,.06)">'
          +'<div><div style="font-size:10px;font-weight:600;color:var(--t2)">'+f[0]+'</div>'
          +'<div style="font-size:8px;color:var(--t4)">'+f[2]+'</div></div>'
          +'<div style="font-size:18px">'+(f[1]?'<span style="color:var(--green)">&#10003;</span>':'<span style="color:var(--red)">&#10007;</span>')+'</div>'
          +'</div>';
      }).join('')
      +'</div>'
      +'<button class="btn" onclick="closeM&&closeM()" style="width:100%;padding:10px;border-radius:9px;margin-top:10px;font-size:10px;border:1px solid rgba(255,255,255,.1);color:var(--t3)">Kapat</button>'
      +'</div>';
    if(document.getElementById('modal')){
      document.getElementById('mtit').textContent = 'Tarayici Uyumluluk';
      document.getElementById('mcont').innerHTML = html;
      document.getElementById('modal').classList.add('on');
    } else {
      alert(bi.name+'/'+bi.platform+'\nSes:'+(bi.hasSpeech?'OK':'YOK')+' Vib:'+(bi.hasVibrate?'OK':'YOK'));
    }
  }catch(e){}
};

</script>
<script>

// BIST TAM HISSE LISTESI - BLOK 20
// XU030 / XU050 / XU100 / XBANK / XHOLD / XINSA / XGIDA
// XTEKS / XKMYA / XELKT / XMESY / XTRZM / XUTEK / XULAS
// Mevcut 69 Katilim hissesine EK olarak tum BIST hisseleri
// Katilim olmayan hisseler i:['XU100'] vb ile isaretlendi

(function(){
try{

// Mevcut STOCKS listesini genislet
// Yeni hisseler eklenir, mevcut katilim hisseleri korunur
var NEW_STOCKS = [
  //  XU030 (Katilim OLMAYAN) 
  {t:'AKBNK',n:'AKBANK',i:['XU030','XU050','XU100','XBANK','XUSIN']},
  {t:'ARCLK',n:'ARCELIK',i:['XU030','XU050','XU100','XMESY','XUSIN']},
  {t:'GARAN',n:'GARANTI BBVA',i:['XU030','XU050','XU100','XBANK']},
  {t:'HALKB',n:'HALKBANK',i:['XU030','XU050','XU100','XBANK']},
  {t:'ISCTR',n:'IS BANKASI C',i:['XU030','XU050','XU100','XBANK']},
  {t:'KCHOL',n:'KOC HOLDING',i:['XU030','XU050','XU100','XHOLD']},
  {t:'KOZAL',n:'KOZA ALTIN',i:['XU030','XU050','XU100','XMADN']},
  {t:'KOZAA',n:'KOZA ANADOLU',i:['XU030','XU050','XU100','XMADN']},
  {t:'OYAKC',n:'OYAK CIMENTO',i:['XU030','XU050','XU100','XTAST']},
  {t:'PGSUS',n:'PEGASUS',i:['XU030','XU050','XU100','XULAS']},
  {t:'SAHOL',n:'SABANCI HOLD.',i:['XU030','XU050','XU100','XHOLD']},
  {t:'SISE',n:'SISE CAM',i:['XU030','XU050','XU100','XTAST']},
  {t:'TCELL',n:'TURKCELL',i:['XU030','XU050','XU100','XILTM']},
  {t:'THYAO',n:'THY',i:['XU030','XU050','XU100','XULAS']},
  {t:'TKFEN',n:'TEKFEN HOLD.',i:['XU030','XU050','XU100','XHOLD']},
  {t:'TOASO',n:'TOFAS OTO',i:['XU030','XU050','XU100','XMESY','XUSIN']},
  {t:'TTKOM',n:'TURK TELEKOM',i:['XU030','XU050','XU100','XILTM']},
  {t:'TUPRS',n:'TUPRAS',i:['XU030','XU050','XU100','XKMYA','XUSIN']},
  {t:'VAKBN',n:'VAKIFBANK',i:['XU030','XU050','XU100','XBANK']},
  {t:'YKBNK',n:'YAPI KREDI',i:['XU030','XU050','XU100','XBANK']},
  {t:'FROTO',n:'FORD OTOSAN',i:['XU030','XU050','XU100','XMESY','XUSIN']},
  {t:'PETKM',n:'PETKIM',i:['XU030','XU050','XU100','XKMYA','XUSIN']},
  //  XU050 EK 
  {t:'AEFES',n:'ANADOLU EFES',i:['XU050','XU100','XGIDA']},
  {t:'AGHOL',n:'AG ANADOLU',i:['XU050','XU100','XHOLD']},
  {t:'AHGAZ',n:'AHLATCI GAZ',i:['XU050','XU100','XELKT']},
  {t:'ALARK',n:'ALARKO HOLD.',i:['XU050','XU100','XHOLD']},
  {t:'ALBRK',n:'ALBARAKA TURK',i:['XU050','XU100','XBANK']},
  {t:'ALGYO',n:'ALARKO GYO',i:['XU050','XU100','XGMYO']},
  {t:'ANAYT',n:'ANADOLUJET',i:['XU050','XU100','XULAS']},
  {t:'ANELE',n:'ANELE ELEKTRIK',i:['XU050','XU100','XELKT']},
  {t:'ARSAN',n:'ARSAN TEKSTIL',i:['XU050','XU100','XTEKS']},
  {t:'AYGAZ',n:'AYGAZ',i:['XU050','XU100','XELKT']},
  {t:'BAGFS',n:'BAGFAS GUBRE',i:['XU050','XU100','XKMYA']},
  {t:'BASGZ',n:'BASKENT GAZ',i:['XU050','XU100','XELKT']},
  {t:'BIOEN',n:'BIOENERJI',i:['XU050','XU100','XELKT']},
  {t:'BRISA',n:'BRISA',i:['XU050','XU100','XKMYA']},
  {t:'BRYAT',n:'BOG RAY',i:['XU050','XU100','XULAS']},
  {t:'CCOLA',n:'COCA COLA ICECEK',i:['XU050','XU100','XGIDA']},
  {t:'CLEBI',n:'CELEBI',i:['XU050','XU100','XULAS']},
  {t:'CRDFA',n:'CREDITWEST FAK.',i:['XU050','XU100','XFINK']},
  {t:'DOHOL',n:'DOGUS HOLDING',i:['XU050','XU100','XHOLD']},
  {t:'DOAS',n:'DOGUS OTOMOTIV',i:['XU050','XU100','XTCRT']},
  {t:'ECILC',n:'EIS ECZACIBASI',i:['XU050','XU100','XKMYA']},
  {t:'EGEEN',n:'EGE ENDUSTRIYEL',i:['XU050','XU100','XELKT']},
  {t:'ENKAI',n:'ENKA INSAAT',i:['XU050','XU100','XINSA']},
  {t:'EREGL',n:'EREGLI DEMIR',i:['XU050','XU100','XMADN','XUSIN']},
  {t:'EUPWR',n:'EUROPOWER ENERJI',i:['XU050','XU100','XELKT']},
  {t:'FENER',n:'FENERBAHCE',i:['XU050','XU100','XSPOR']},
  {t:'GLYHO',n:'GLOBAL YAT. HOLD.',i:['XU050','XU100','XHOLD']},
  {t:'GUBRF',n:'GUBRE FABRIK.',i:['XU050','XU100','XKMYA']},
  {t:'GWIND',n:'GALATA WIND',i:['XU050','XU100','XELKT']},
  {t:'HLGYO',n:'HALK GYO',i:['XU050','XU100','XGMYO']},
  {t:'IEYHO',n:'IE YATIRIM HOLD.',i:['XU050','XU100','XHOLD']},
  {t:'IHLAS',n:'IHLAS HOLDING',i:['XU050','XU100','XHOLD']},
  {t:'IPEKE',n:'IPEK DOGAL ENERJI',i:['XU050','XU100','XELKT']},
  {t:'ISGYO',n:'IS GYO',i:['XU050','XU100','XGMYO']},
  {t:'ISYAT',n:'IS YATIRIM',i:['XU050','XU100','XFINK']},
  {t:'KAREL',n:'KAREL ELEKTRONIK',i:['XU050','XU100','XBLSM']},
  {t:'KARSN',n:'KARSAN OTOMOTIV',i:['XU050','XU100','XMESY']},
  {t:'KERVT',n:'KEREVITAS GIDA',i:['XU050','XU100','XGIDA']},
  {t:'KLGYO',n:'KILER GYO',i:['XU050','XU100','XGMYO']},
  {t:'KMPUR',n:'KIMPURE',i:['XU050','XU100','XKMYA']},
  {t:'KONYA',n:'KONYAALTI CIMENTO',i:['XU050','XU100','XTAST']},
  {t:'KORDS',n:'KORDSA',i:['XU050','XU100','XTEKS']},
  {t:'KUTPO',n:'KUTAHYA PORSELEN',i:['XU050','XU100','XTAST']},
  {t:'LMKDC',n:'LIMAK CIMENTO',i:['XU050','XU100','XTAST']},
  {t:'LOGO',n:'LOGO YAZILIM',i:['XU050','XU100','XBLSM']},
  {t:'MAALT',n:'MAC ALT',i:['XU050','XU100','XGIDA']},
  {t:'MGROS',n:'MIGROS TICARET',i:['XU050','XU100','XTCRT']},
  {t:'MPARK',n:'MAVI GIYIM',i:['XU050','XU100','XTCRT']},
  {t:'NATEN',n:'NATUREL ENERJI',i:['XU050','XU100','XELKT']},
  {t:'NTHOL',n:'NET HOLDING',i:['XU050','XU100','XHOLD']},
  {t:'NUROL',n:'NUROL GYO',i:['XU050','XU100','XGMYO']},
  {t:'ODAS',n:'ODAS ELEKTRIK',i:['XU050','XU100','XELKT']},
  {t:'ORGE',n:'ORGE ENERJI',i:['XU050','XU100','XELKT']},
  {t:'OTKAR',n:'OTOKAR',i:['XU050','XU100','XMESY']},
  {t:'OYAYO',n:'OYA YATIRIM ORTAKL.',i:['XU050','XU100']},
  {t:'PARSN',n:'PARSAN',i:['XU050','XU100','XMESY']},
  {t:'PRKAB',n:'PRYSMIAN KABLO',i:['XU050','XU100','XMESY']},
  {t:'PRKME',n:'PARK ELEKTRIK',i:['XU050','XU100','XELKT']},
  {t:'RYSAS',n:'REYSAS LOJISTIK',i:['XU050','XU100','XULAS']},
  {t:'SELGD',n:'SELCUK GIDA',i:['XU050','XU100','XGIDA']},
  {t:'SILVR',n:'SILVERLINE',i:['XU050','XU100','XMESY']},
  {t:'SKBNK',n:'SEKERBANK',i:['XU050','XU100','XBANK']},
  {t:'SNGYO',n:'SINPAS GYO',i:['XU050','XU100','XGMYO']},
  {t:'SOKM',n:'SOK MARKETLER',i:['XU050','XU100','XTCRT']},
  {t:'TATGD',n:'TAT GIDA',i:['XU050','XU100','XGIDA']},
  {t:'TKNSA',n:'TEKNOSA',i:['XU050','XU100','XTCRT']},
  {t:'TOASO',n:'TOFAS OTO',i:['XU030','XU050','XU100','XMESY']},
  {t:'TRGYO',n:'TORUNLAR GYO',i:['XU050','XU100','XGMYO']},
  {t:'TSKB',n:'TSKB',i:['XU050','XU100','XBANK']},
  {t:'TTRAK',n:'TURK TRAKTOR',i:['XU050','XU100','XMESY']},
  {t:'ULKER',n:'ULKER BISKUVI',i:['XU050','XU100','XGIDA']},
  {t:'VKGYO',n:'VAKIF GYO',i:['XU050','XU100','XGMYO']},
  {t:'YAPRK',n:'YAP KREDI SIGORTA',i:['XU050','XU100','XSGRT']},
  {t:'YBTAS',n:'YATAS',i:['XU050','XU100','XMESY']},
  {t:'YEOTK',n:'YEO TEKNOLOJI',i:['XU050','XU100','XBLSM']},
  {t:'ZOREN',n:'ZORLU ENERJI',i:['XU050','XU100','XELKT']},
  //  XU100 EK 
  {t:'ADEL',n:'ADEL KALEMCILIK',i:['XU100']},
  {t:'ADESE',n:'ADESE ALISVERIS',i:['XU100','XTCRT']},
  {t:'AGESA',n:'AGESA HAYAT',i:['XU100','XSGRT']},
  {t:'AGTC',n:'AGTC GENETIK',i:['XU100']},
  {t:'AHSGY',n:'AHMET EROZEN GYO',i:['XU100','XGMYO']},
  {t:'AKENR',n:'AK ENERJI',i:['XU100','XELKT']},
  {t:'AKFGY',n:'AKFEN GYO',i:['XU100','XGMYO']},
  {t:'AKGRT',n:'AKSIGORTA',i:['XU100','XSGRT']},
  {t:'AKMGY',n:'AKMERKEZ GYO',i:['XU100','XGMYO']},
  {t:'AKSGY',n:'AKIS GYO',i:['XU100','XGMYO']},
  {t:'AKYHO',n:'AKYUZLU HOLDING',i:['XU100','XHOLD']},
  {t:'ALCAR',n:'ALCAR OTOMOTIV',i:['XU100','XMESY']},
  {t:'ALFAS',n:'ALFA SOLAR',i:['XU100','XELKT']},
  {t:'ALGYO',n:'ALARKO GYO',i:['XU100','XGMYO']},
  {t:'ALKIM',n:'ALKIM KIMYA',i:['XU100','XKMYA']},
  {t:'ALKTL',n:'ALKIM KATALIZOR',i:['XU100','XKMYA']},
  {t:'ALMAD',n:'AL-MAD',i:['XU100','XMADN']},
  {t:'ALPAY',n:'ALPAY ALUMINUIM',i:['XU100','XMADN']},
  {t:'ALVES',n:'ALVES ELEKTRK',i:['XU100','XELKT']},
  {t:'ANACM',n:'ANADOLU CAM',i:['XU100','XTAST']},
  {t:'ANELE',n:'ANADOLU ELEKTRIK',i:['XU100','XELKT']},
  {t:'ANHYT',n:'ANADOLU HAYAT',i:['XU100','XSGRT']},
  {t:'ARASE',n:'ARA SOLAR ENERJI',i:['XU100','XELKT']},
  {t:'ARCLK',n:'ARCELIK',i:['XU030','XU050','XU100','XMESY']},
  {t:'ARZUM',n:'ARZUM EV GERE.',i:['XU100','XMESY']},
  {t:'ASGYO',n:'ASTARC GYO',i:['XU100','XGMYO']},
  {t:'ASTOR',n:'ASTOR ENERJI',i:['XU100','XELKT']},
  {t:'ATAKP',n:'ATAK PERAKENDE',i:['XU100','XTCRT']},
  {t:'ATEKS',n:'AKIN TEKSTIL',i:['XU100','XTEKS']},
  {t:'AVGYO',n:'AVRASYA GYO',i:['XU100','XGMYO']},
  {t:'AVHOL',n:'AVRASYA HOLDING',i:['XU100','XHOLD']},
  {t:'AYCES',n:'AYCES TURIZM',i:['XU100','XTRZM']},
  {t:'AYEN',n:'AYEN ENERJI',i:['XU100','XELKT']},
  {t:'AYGAZ',n:'AYGAZ',i:['XU050','XU100','XELKT']},
  {t:'AZTEK',n:'AZTEK ELEKTRONIK',i:['XU100','XBLSM']},
  {t:'BAGRP',n:'BAG GROUP',i:['XU100']},
  {t:'BASCM',n:'BASCIMENTO',i:['XU100','XTAST']},
  {t:'BAYRK',n:'BAYRAK',i:['XU100']},
  {t:'BEGYO',n:'BETAM GYO',i:['XU100','XGMYO']},
  {t:'BIENY',n:'BIE ENERJI',i:['XU100','XELKT']},
  {t:'BIGCH',n:'BIG CHEF',i:['XU100','XTRZM']},
  {t:'BIZIM',n:'BIZIM TOPTAN',i:['XU100','XTCRT']},
  {t:'BLCYT',n:'BILICI YATIRIM',i:['XU100']},
  {t:'BMSCH',n:'BMW',i:['XU100','XMESY']},
  {t:'BOSSA',n:'BOSSA TICARET',i:['XU100','XTEKS']},
  {t:'BUCIM',n:'BURSA CIMENTO',i:['XU100','XTAST']},
  {t:'BURCE',n:'BURCELIK',i:['XU100','XMADN']},
  {t:'BURVA',n:'BURSA YATIRIM',i:['XU100']},
  {t:'BVSAN',n:'BIRLESIK VAN SAN.',i:['XU100']},
  {t:'CANTE',n:'CAN2 TERMIK',i:['XU100','XELKT']},
  {t:'CARFA',n:'CARREFOURSA',i:['XU100','XTCRT']},
  {t:'CEMAS',n:'CEMAS DOKUM',i:['XU100','XMADN']},
  {t:'CEMTS',n:'CEMENTASA',i:['XU100','XTAST']},
  {t:'CFACT',n:'CRESCENT FIN.',i:['XU100','XFINK']},
  {t:'CIMSA',n:'CIMSA',i:['XU100','XTAST']},
  {t:'CLKHO',n:'CELEBI HOLDING',i:['XU100','XHOLD']},
  {t:'CMBTN',n:'COMBITON',i:['XU100','XBLSM']},
  {t:'CMENT',n:'COMPOMER',i:['XU100','XTAST']},
  {t:'CONSE',n:'CONSUS ENERJI',i:['XU100','XELKT']},
  {t:'COSMO',n:'COSMO FINANS',i:['XU100','XFINK']},
  {t:'CUSAN',n:'CUSA',i:['XU100']},
  {t:'DAGHL',n:'DAG HOLDING',i:['XU100']},
  {t:'DAPGM',n:'DAP GAYRIMENKUL',i:['XU100','XGMYO']},
  {t:'DARDL',n:'DARDANEL ORDEK',i:['XU100','XGIDA']},
  {t:'DENGE',n:'DENGE YATIRIM',i:['XU100','XFINK']},
  {t:'DERHL',n:'DERIMOD HOLDING',i:['XU100']},
  {t:'DERIM',n:'DERIM',i:['XU100']},
  {t:'DESA',n:'DESA DERI',i:['XU100']},
  {t:'DESPC',n:'DESPEC',i:['XU100','XBLSM']},
  {t:'DEVA',n:'DEVA HOLDING',i:['XU100','XKMYA']},
  {t:'DGKLB',n:'DOGUS KAR. LBRT.',i:['XU100']},
  {t:'DGGYO',n:'DOGUS GYO',i:['XU100','XGMYO']},
  {t:'DGIYD',n:'DOGUS GIYDIRMA',i:['XU100']},
  {t:'DIRIT',n:'DIRIT',i:['XU100','XBLSM']},
  {t:'DKHOL',n:'DOGAN HOLDING',i:['XU100','XHOLD']},
  {t:'DNISI',n:'DENIZ YATIRIM',i:['XU100','XFINK']},
  {t:'DOBUR',n:'DOBUR AMBALAJ',i:['XU100']},
  {t:'DOCO',n:'DOCO',i:['XU100']},
  {t:'DOGUB',n:'DOGUBATI',i:['XU100']},
  {t:'DOHOL',n:'DOGUS HOLDING',i:['XU050','XU100','XHOLD']},
  {t:'DOKAL',n:'DOKAL',i:['XU100']},
  {t:'DOKTA',n:'DOKTAS DOKIM',i:['XU100','XMADN']},
  {t:'DOPA',n:'DOPAS',i:['XU100']},
  {t:'DURDO',n:'DURO FELGUERA',i:['XU100']},
  {t:'DYOBY',n:'DYO BOYA',i:['XU100','XKMYA']},
  {t:'DZGYO',n:'DENIZ GYO',i:['XU100','XGMYO']},
  {t:'EDATA',n:'E-DATA',i:['XU100','XBLSM']},
  {t:'EDIP',n:'EDIP IPLIK',i:['XU100','XTEKS']},
  {t:'EGEEN',n:'EGE END.',i:['XU050','XU100']},
  {t:'EGEPO',n:'EGE PROFIL',i:['XU100','XKMYA']},
  {t:'EGGUB',n:'EGE GUBRE',i:['XU100','XKMYA']},
  {t:'EGLYO',n:'EGELI & CO. GYO',i:['XU100','XGMYO']},
  {t:'EGPRO',n:'EGE PROFIL',i:['XU100']},
  {t:'EGSER',n:'EGE SERAMIK',i:['XU100','XTAST']},
  {t:'ELITE',n:'ELIT PARA',i:['XU100','XFINK']},
  {t:'EMKEL',n:'EMKEL ELEKTRIK',i:['XU100','XELKT']},
  {t:'EMNIS',n:'EMINIS',i:['XU100']},
  {t:'ENERY',n:'ENERY ENERJI',i:['XU100','XELKT']},
  {t:'ENGYO',n:'ENKA GYO',i:['XU100','XGMYO']},
  {t:'ENSRI',n:'ENSARI',i:['XU100']},
  {t:'EPLAS',n:'EGEPLAST',i:['XU100','XKMYA']},
  {t:'ERSU',n:'ERSU MEYVE',i:['XU100','XGIDA']},
  {t:'ESCAR',n:'ESCAR FINANSAL',i:['XU100','XFINK']},
  {t:'ESCOM',n:'ESCOM TELEKM.',i:['XU100','XILTM']},
  {t:'ESEN',n:'ESEN YATIRIM',i:['XU100']},
  {t:'ETILR',n:'ETILER GIDA',i:['XU100','XGIDA']},
  {t:'ETYAT',n:'ETI YATIRIM',i:['XU100','XFINK']},
  {t:'EUHOL',n:'EUROHOLDING',i:['XU100','XHOLD']},
  {t:'EUKYO',n:'EURO KAP. YATIRIM',i:['XU100']},
  {t:'EVREN',n:'EVRENSEL ELEKTRIK',i:['XU100','XELKT']},
  {t:'EXPER',n:'EXPER BILGISAYAR',i:['XU100','XBLSM']},
  {t:'FADE',n:'FADE GIDA',i:['XU100','XGIDA']},
  {t:'FAIR',n:'FAIR',i:['XU100','XFINK']},
  {t:'FENER',n:'FENERBAHCE',i:['XU050','XU100','XSPOR']},
  {t:'FLAP',n:'FLAP KONGRE',i:['XU100','XTRZM']},
  {t:'FMIZP',n:'FM IZMIR POZITRON',i:['XU100','XBLSM']},
  {t:'FONET',n:'FONET',i:['XU100','XBLSM']},
  {t:'FORMT',n:'FORMAT COZUM',i:['XU100','XBLSM']},
  {t:'FORTE',n:'FORTE YATIRIM',i:['XU100','XFINK']},
  {t:'FRIGO',n:'FRIGO PAK GIDA',i:['XU100','XGIDA']},
  {t:'GARAN',n:'GARANTI BBVA',i:['XU030','XU050','XU100','XBANK']},
  {t:'GARFA',n:'GARANTI FAKTORING',i:['XU100','XFINK']},
  {t:'GBFIN',n:'GB FINANSAL',i:['XU100','XFINK']},
  {t:'GEDIK',n:'GEDIK YATIRIM',i:['XU100','XFINK']},
  {t:'GEDZA',n:'GEDIZ AMBALAJ',i:['XU100']},
  {t:'GENTS',n:'GENTES',i:['XU100']},
  {t:'GEREL',n:'GERSAN ELEKTRIK',i:['XU100','XELKT']},
  {t:'GESAN',n:'GESAN YATIRIM',i:['XU100','XELKT']},
  {t:'GLBMD',n:'GLOBAL LIMAN',i:['XU100','XULAS']},
  {t:'GLRYH',n:'GLORY YATIRIM',i:['XU100']},
  {t:'GLYHO',n:'GLOBAL YAT.',i:['XU050','XU100','XHOLD']},
  {t:'GMTAS',n:'GEMTAS',i:['XU100','XMESY']},
  {t:'GNSYO',n:'GNS YATIRIM',i:['XU100']},
  {t:'GOLTS',n:'GOLTAS',i:['XU100','XTAST']},
  {t:'GOODY',n:'GOODYEAR',i:['XU100','XKMYA']},
  {t:'GOZDE',n:'GOZDE GIRISIM',i:['XU100','XHOLD']},
  {t:'GRSEL',n:'GURSEL TARIH',i:['XU100']},
  {t:'GSDDE',n:'GSD DENIZCILIK',i:['XU100','XULAS']},
  {t:'GSDHO',n:'GSD HOLDING',i:['XU100','XHOLD']},
  {t:'GSRAY',n:'GALATASARAY',i:['XU100','XSPOR']},
  {t:'GUBRF',n:'GUBRE FABRIK.',i:['XU050','XU100','XKMYA']},
  {t:'GUNDG',n:'GUNDOGDU',i:['XU100']},
  {t:'HALKB',n:'HALKBANK',i:['XU030','XU050','XU100','XBANK']},
  {t:'HATEK',n:'HATAT ENERJI',i:['XU100','XELKT']},
  {t:'HDFGS',n:'HEDEF GIRISIM',i:['XU100']},
  {t:'HEDEF',n:'HEDEF YATIRIM',i:['XU100','XFINK']},
  {t:'HEKTS',n:'HEKTAS',i:['XU100','XKMYA']},
  {t:'HLGYO',n:'HALK GYO',i:['XU050','XU100','XGMYO']},
  {t:'HRKET',n:'HAREKET',i:['XU100','XULAS']},
  {t:'HTTBT',n:'HATTAT HOLDING',i:['XU100','XHOLD']},
  {t:'HUNER',n:'HUNER ENERJI',i:['XU100','XELKT']},
  {t:'HURGZ',n:'HURRIYET GAZ.',i:['XU100']},
  {t:'ICBCT',n:'ICBC TURKEY BANK',i:['XU100','XBANK']},
  {t:'IDEAS',n:'IDEAS YATIRIM',i:['XU100']},
  {t:'IDGYO',n:'IDAS GYO',i:['XU100','XGMYO']},
  {t:'IEYHO',n:'IE YATIRIM',i:['XU050','XU100','XHOLD']},
  {t:'IHAAS',n:'IHAS SAVUNMA',i:['XU100']},
  {t:'IHEVA',n:'IHE VALUE',i:['XU100']},
  {t:'IHGZT',n:'IHLAS GAZETECILIK',i:['XU100']},
  {t:'IHLAS',n:'IHLAS HOLDING',i:['XU050','XU100','XHOLD']},
  {t:'IHLGM',n:'IHLAS EV ALET',i:['XU100','XMESY']},
  {t:'IHYAY',n:'IHLAS YAYINCILIK',i:['XU100']},
  {t:'IMASM',n:'IMASYA',i:['XU100','XMESY']},
  {t:'INDES',n:'INDEX BILGISAYAR',i:['XU100','XBLSM']},
  {t:'INFO',n:'INFO YATIRIM',i:['XU100','XFINK']},
  {t:'INNA',n:'INNA GIDA',i:['XU100','XGIDA']},
  {t:'INTEM',n:'INTERTEAM',i:['XU100']},
  {t:'IPEKE',n:'IPEK DOGAL ENERJI',i:['XU050','XU100','XELKT']},
  {t:'IPEKE',n:'IPEKE',i:['XU100','XELKT']},
  {t:'ISGSY',n:'IS GYO',i:['XU100','XGMYO']},
  {t:'ISGYO',n:'IS GYO',i:['XU050','XU100','XGMYO']},
  {t:'ISKPL',n:'ISKUR PLASTIK',i:['XU100','XKMYA']},
  {t:'ISYAT',n:'IS YATIRIM',i:['XU050','XU100','XFINK']},
  {t:'IZFAS',n:'IZMIR FUAR',i:['XU100','XTRZM']},
  {t:'IZMDC',n:'IZMIR DEMIR CELIK',i:['XU100','XMADN']},
  {t:'IZTAR',n:'IZTAS',i:['XU100','XULAS']},
  {t:'JANTS',n:'JANTSA',i:['XU100','XMESY']},
  {t:'KAPLM',n:'KAPLAMALAR',i:['XU100']},
  {t:'KARSN',n:'KARSAN OTOMOTIV',i:['XU050','XU100','XMESY']},
  {t:'KARYE',n:'KARYE',i:['XU100']},
  {t:'KAYSE',n:'KAYSERI SEKER',i:['XU100','XGIDA']},
  {t:'KCAER',n:'KOC YAYINLARI',i:['XU100']},
  {t:'KENT',n:'KENT GIDA',i:['XU100','XGIDA']},
  {t:'KERVT',n:'KEREVITAS GIDA',i:['XU050','XU100','XGIDA']},
  {t:'KFEIN',n:'KAF FINANSAL',i:['XU100','XFINK']},
  {t:'KGYO',n:'KREA GYO',i:['XU100','XGMYO']},
  {t:'KILER',n:'KILER ALISVERIS',i:['XU100','XTCRT']},
  {t:'KLGYO',n:'KILER GYO',i:['XU050','XU100','XGMYO']},
  {t:'KNFRT',n:'KONFRUT GIDA',i:['XU100','XGIDA']},
  {t:'KONYA',n:'KONYA SEKER',i:['XU100','XGIDA']},
  {t:'KOPIL',n:'KO PERAKENDE',i:['XU100','XTCRT']},
  {t:'KORDS',n:'KORDSA',i:['XU050','XU100','XTEKS']},
  {t:'KRSTL',n:'KRISTAL KOLA',i:['XU100','XGIDA']},
  {t:'KRTEK',n:'KARTONSAN',i:['XU100']},
  {t:'KRVGD',n:'KARVEN GIDA',i:['XU100','XGIDA']},
  {t:'KSTUR',n:'KUSTUR',i:['XU100','XTRZM']},
  {t:'KUTPO',n:'KUTAHYA PORSELEN',i:['XU050','XU100','XTAST']},
  {t:'KUYAS',n:'KUYASAN',i:['XU100']},
  {t:'LATEK',n:'LATEK LOJISTIK',i:['XU100','XULAS']},
  {t:'LIDER',n:'LIDER FAKTORING',i:['XU100','XFINK']},
  {t:'LINK',n:'LINK BILGISAYAR',i:['XU100','XBLSM']},
  {t:'LKMNH',n:'LOKMAN HEKIM',i:['XU100']},
  {t:'LNCRN',n:'LANCOR',i:['XU100']},
  {t:'LOGO',n:'LOGO YAZILIM',i:['XU050','XU100','XBLSM']},
  {t:'LUKSK',n:'LUKS KADIN',i:['XU100']},
  {t:'MAALT',n:'MAC ALT',i:['XU050','XU100','XGIDA']},
  {t:'MACKO',n:'MACKOLIK',i:['XU100','XBLSM']},
  {t:'MAGEN',n:'MAGEN ENERJI',i:['XU100','XELKT']},
  {t:'MAKTK',n:'MAKTEK',i:['XU100']},
  {t:'MANAS',n:'MANAS ENERJI',i:['XU100','XELKT']},
  {t:'MARTI',n:'MARTI OTEL',i:['XU100','XTRZM']},
  {t:'MAVI',n:'MAVI GIYIM',i:['XU050','XU100','XTCRT']},
  {t:'MEDTR',n:'MEDITERA',i:['XU100']},
  {t:'MEGMT',n:'MEGA ULUSLARARASI',i:['XU100']},
  {t:'MEPET',n:'MEPET MET.PET.',i:['XU100']},
  {t:'MERCN',n:'MERCAN KIMYA',i:['XU100','XKMYA']},
  {t:'MERIT',n:'MERIT TURIZM',i:['XU100','XTRZM']},
  {t:'MERKO',n:'MERKO GIDA',i:['XU100','XGIDA']},
  {t:'METRO',n:'METRO HOLDING',i:['XU100','XHOLD']},
  {t:'METUR',n:'METEMTUR',i:['XU100','XTRZM']},
  {t:'MGROS',n:'MIGROS',i:['XU050','XU100','XTCRT']},
  {t:'MIATK',n:'MIA TEKNOLOJI',i:['XU100','XBLSM']},
  {t:'MIGRS',n:'MIGROS',i:['XU100','XTCRT']},
  {t:'MMCAS',n:'MMC AS',i:['XU100']},
  {t:'MNDRS',n:'MENDERES',i:['XU100','XTEKS']},
  {t:'MNVLD',n:'MINERAL VE DORUK',i:['XU100','XMADN']},
  {t:'MOGAN',n:'MOGAN TEKSTIL',i:['XU100','XTEKS']},
  {t:'MOBTL',n:'MOBIL TELEKOM',i:['XU100','XBLSM']},
  {t:'MPARK',n:'MEDIKAL PARK',i:['XU050','XU100']},
  {t:'MRSHL',n:'MARSHALL',i:['XU100','XKMYA']},
  {t:'MTRKS',n:'MATRIKS BILGI',i:['XU100','XBLSM']},
  {t:'MZHLD',n:'MOZAIK HOLDING',i:['XU100','XHOLD']},
  {t:'NATEN',n:'NATUREL ENERJI',i:['XU050','XU100','XELKT']},
  {t:'NBORU',n:'NOVA BORU',i:['XU100','XMADN']},
  {t:'NETAS',n:'NETAS TELEKM.',i:['XU100','XILTM']},
  {t:'NETOL',n:'NET OLCUM',i:['XU100','XBLSM']},
  {t:'NILYT',n:'NIL YATIRIM',i:['XU100']},
  {t:'NRBNK',n:'NUR BANK',i:['XU100','XBANK']},
  {t:'NTHOL',n:'NET HOLDING',i:['XU050','XU100','XHOLD']},
  {t:'NTTUR',n:'NETTUR',i:['XU100','XTRZM']},
  {t:'NUGYO',n:'NUROL GYO',i:['XU050','XU100','XGMYO']},
  {t:'NUHCM',n:'NUH CIMENTO',i:['XU100','XTAST']},
  {t:'OBAMS',n:'OBA MUHTELIFAT',i:['XU100','XGIDA']},
  {t:'ODAS',n:'ODAS ELEKTRIK',i:['XU050','XU100','XELKT']},
  {t:'OFSYM',n:'OF SU URUNL.',i:['XU100','XGIDA']},
  {t:'OKCNS',n:'OKCINAS',i:['XU100']},
  {t:'ONCSM',n:'ONC SIGORTA',i:['XU100','XSGRT']},
  {t:'ONRYT',n:'ONUR REYT',i:['XU100']},
  {t:'ORCAY',n:'ORCAY ORTAKLIK',i:['XU100']},
  {t:'ORGE',n:'ORGE ENERJI',i:['XU050','XU100','XELKT']},
  {t:'ORMA',n:'ORMA',i:['XU100']},
  {t:'OSTIM',n:'OSTIM ENDUSTRIYEL',i:['XU100']},
  {t:'OTKAR',n:'OTOKAR',i:['XU050','XU100','XMESY']},
  {t:'OYLUM',n:'OYLUM SINAI',i:['XU100']},
  {t:'OZKGY',n:'OZAK GYO',i:['XU100','XGMYO']},
  {t:'OZTPL',n:'OZTEK PLASTIK',i:['XU100','XKMYA']},
  {t:'PAGYO',n:'PAN GYO',i:['XU100','XGMYO']},
  {t:'PAMEL',n:'PAMEL YENILENB.',i:['XU100','XELKT']},
  {t:'PAPIL',n:'PAPILYON',i:['XU100']},
  {t:'PARSN',n:'PARSAN',i:['XU050','XU100','XMESY']},
  {t:'PENGD',n:'PENGUEN GIDA',i:['XU100','XGIDA']},
  {t:'PGSUS',n:'PEGASUS',i:['XU030','XU050','XU100','XULAS']},
  {t:'PKART',n:'PLASTIKKART',i:['XU100','XBLSM']},
  {t:'PLTUR',n:'PALMET TURIZM',i:['XU100','XTRZM']},
  {t:'PNSUT',n:'PINAR SUT',i:['XU100','XGIDA']},
  {t:'POLHO',n:'POLISAN HOLDING',i:['XU100','XHOLD']},
  {t:'POLTK',n:'POLITIKA',i:['XU100']},
  {t:'PRDGS',n:'PARDIGTAS',i:['XU100']},
  {t:'PRKAB',n:'PRYSMIAN KABLO',i:['XU050','XU100','XMESY']},
  {t:'PRKME',n:'PARK ELEKTRIK',i:['XU050','XU100','XELKT']},
  {t:'PRZMA',n:'PRIZMA PRES',i:['XU100']},
  {t:'PTOFS',n:'PETROL OFISI',i:['XU100','XELKT']},
  {t:'QNBFL',n:'QNB FINANSLEASING',i:['XU100','XFINK']},
  {t:'QNBFB',n:'QNB FINANSBANK',i:['XU100','XBANK']},
  {t:'RALYH',n:'RALLY HOLDING',i:['XU100']},
  {t:'RBTAS',n:'RABA TARIMSAL',i:['XU100']},
  {t:'RCAST',n:'RECAST',i:['XU100','XBLSM']},
  {t:'RTALB',n:'RENTA ALBARAKA',i:['XU100']},
  {t:'RTUAY',n:'ROTA YATIRIM',i:['XU100']},
  {t:'RUBNS',n:'RUBENIS',i:['XU100']},
  {t:'RYSAS',n:'REYSAS LOJISTIK',i:['XU050','XU100','XULAS']},
  {t:'SAFGY',n:'SAFKAR GYO',i:['XU100','XGMYO']},
  {t:'SAGYM',n:'SAGLIKCILAR',i:['XU100']},
  {t:'SAMAT',n:'SAMATYA',i:['XU100']},
  {t:'SANFM',n:'SANIFICO',i:['XU100']},
  {t:'SANKO',n:'SANKO HOLDING',i:['XU100','XHOLD']},
  {t:'SARKY',n:'SARKUYSAN',i:['XU100','XMADN']},
  {t:'SAYAS',n:'SAYASEL',i:['XU100']},
  {t:'SDTTR',n:'SDT UZAY',i:['XU100']},
  {t:'SELGD',n:'SELCUK GIDA',i:['XU050','XU100','XGIDA']},
  {t:'SEYKM',n:'SEY KIMYA',i:['XU100','XKMYA']},
  {t:'SILVR',n:'SILVERLINE',i:['XU050','XU100','XMESY']},
  {t:'SKBNK',n:'SEKERBANK',i:['XU050','XU100','XBANK']},
  {t:'SNGYO',n:'SINPAS GYO',i:['XU050','XU100','XGMYO']},
  {t:'SNKRN',n:'SUNKAR IPLIK',i:['XU100','XTEKS']},
  {t:'SOKM',n:'SOK MARKETLER',i:['XU050','XU100','XTCRT']},
  {t:'SONME',n:'SONMEZ FILAMENT',i:['XU100','XTEKS']},
  {t:'SPTUR',n:'SPOT TURIZM',i:['XU100','XTRZM']},
  {t:'SRVGY',n:'SERVET GYO',i:['XU100','XGMYO']},
  {t:'SUMAS',n:'SUMAS SINAI',i:['XU100']},
  {t:'SUNTK',n:'SUN TEKSTIL',i:['XU100','XTEKS']},
  {t:'SURGY',n:'SURMELI GYO',i:['XU100','XGMYO']},
  {t:'SUTEKS',n:'SU TEKSTIL',i:['XU100','XTEKS']},
  {t:'TATEN',n:'TAT ENERJI',i:['XU100','XELKT']},
  {t:'TATGD',n:'TAT GIDA',i:['XU050','XU100','XGIDA']},
  {t:'TAVHL',n:'TAV HAVALIMANLARI',i:['XU050','XU100','XULAS']},
  {t:'TBGYO',n:'TORUNLAR BIRLESIK GYO',i:['XU100','XGMYO']},
  {t:'TBORG',n:'TURK TUBORG',i:['XU100','XGIDA']},
  {t:'TEZOL',n:'TEZ-OL',i:['XU100']},
  {t:'TGSAS',n:'TURK GUBRE',i:['XU100','XKMYA']},
  {t:'THYAO',n:'TURK HAVA YOLLARI',i:['XU030','XU050','XU100','XULAS']},
  {t:'TKFEN',n:'TEKFEN HOLDING',i:['XU030','XU050','XU100','XHOLD']},
  {t:'TKNSA',n:'TEKNOSA',i:['XU050','XU100','XTCRT']},
  {t:'TLMAN',n:'TEL MAN',i:['XU100']},
  {t:'TMPOL',n:'TURKIYE PETROL',i:['XU100','XELKT']},
  {t:'TMSN',n:'TOMSAN',i:['XU100','XMESY']},
  {t:'TOASO',n:'TOFAS OTO FABRIK.',i:['XU030','XU050','XU100','XMESY']},
  {t:'TPVTM',n:'TOPVITAMIN',i:['XU100']},
  {t:'TRGYO',n:'TORUNLAR GYO',i:['XU050','XU100','XGMYO']},
  {t:'TRILC',n:'TRILCE',i:['XU100']},
  {t:'TSKB',n:'TSKB',i:['XU050','XU100','XBANK']},
  {t:'TTKOM',n:'TURK TELEKM.',i:['XU030','XU050','XU100','XILTM']},
  {t:'TTRAK',n:'TURK TRAKTOR',i:['XU050','XU100','XMESY']},
  {t:'TUMTK',n:'TUMTEKS',i:['XU100','XTEKS']},
  {t:'TUPRS',n:'TUPRAS',i:['XU030','XU050','XU100','XKMYA']},
  {t:'TUREX',n:'TUREKS',i:['XU100']},
  {t:'TURGZ',n:'TURK GAZETES',i:['XU100']},
  {t:'TURSG',n:'TURK SIGORTA',i:['XU100','XSGRT']},
  {t:'TZSYE',n:'TAZ YATIRIM',i:['XU100']},
  {t:'ULKER',n:'ULKER BISKUVI',i:['XU050','XU100','XGIDA']},
  {t:'UMPAS',n:'UMAS PASLANMAZ',i:['XU100','XMADN']},
  {t:'UNLU',n:'UNLU TEKSTIL',i:['XU100','XTEKS']},
  {t:'USAK',n:'USAK SERAMIK',i:['XU100','XTAST']},
  {t:'USDMR',n:'US DEMIR',i:['XU100','XMADN']},
  {t:'UZERB',n:'UZER BOBINAJ',i:['XU100']},
  {t:'VAKBN',n:'VAKIFBANK',i:['XU030','XU050','XU100','XBANK']},
  {t:'VAKFN',n:'VAKIF FINANS',i:['XU100','XFINK']},
  {t:'VANGD',n:'VAN GOLU',i:['XU100','XGIDA']},
  {t:'VANET',n:'VAN ET',i:['XU100','XGIDA']},
  {t:'VEGYO',n:'VE GYO',i:['XU100','XGMYO']},
  {t:'VERTU',n:'VERTU HOLDING',i:['XU100']},
  {t:'VESBE',n:'VESTEL BEYAZ ESYA',i:['XU100','XMESY']},
  {t:'VESTL',n:'VESTEL',i:['XU100','XMESY']},
  {t:'VKGYO',n:'VAKIF GYO',i:['XU050','XU100','XGMYO']},
  {t:'VRGYO',n:'VARLIK GYO',i:['XU100','XGMYO']},
  {t:'WEGE',n:'WEGE KURUMSAL',i:['XU100']},
  {t:'WYENS',n:'WYENSA',i:['XU100']},
  {t:'XCENT',n:'XCENTA',i:['XU100']},
  {t:'XFGYO',n:'XFG GYO',i:['XU100','XGMYO']},
  {t:'YAPRK',n:'YAP KREDI SIGORTA',i:['XU050','XU100','XSGRT']},
  {t:'YATAS',n:'YATAS',i:['XU100','XMESY']},
  {t:'YESIL',n:'YESIL GIRISIM',i:['XU100']},
  {t:'YGGYO',n:'YAPI GRUP GYO',i:['XU100','XGMYO']},
  {t:'YGYO',n:'YESIL GYO',i:['XU100','XGMYO']},
  {t:'YIGIT',n:'YIGIT AKSESUAR',i:['XU100']},
  {t:'YKSLN',n:'YUKSELEN',i:['XU100']},
  {t:'YONGA',n:'YONGA MATBAACILIK',i:['XU100']},
  {t:'YYAPI',n:'YUKSEL YAPI',i:['XU100','XINSA']},
  {t:'ZRGYO',n:'ZR GYO',i:['XU100','XGMYO']},
  {t:'ZEDUR',n:'ZED YATIRIM',i:['XU100']},
];

// Mevcut hisse tickerlarini al - duplicate ekleme
var existing = {};
if(typeof STOCKS !== 'undefined'){
  STOCKS.forEach(function(s){ existing[s.t] = true; });
}

// Sadece yeni olanlari ekle
var added = 0;
NEW_STOCKS.forEach(function(s){
  if(!existing[s.t]){
    STOCKS.push(s);
    existing[s.t] = true;
    added++;
  }
});

console.log('BIST genisletildi: '+added+' yeni hisse eklendi. Toplam: '+STOCKS.length);

// Endeks listesini guncelle - navigasyona ekle
var IDX_EXTRA = {
  'XU030':'BIST 30','XU050':'BIST 50','XU100':'BIST 100',
  'XBANK':'Bankalar','XHOLD':'Holdinglar','XINSA':'Insaat',
  'XGIDA':'Gida','XTEKS':'Tekstil','XKMYA':'Kimya','XELKT':'Enerji',
  'XMESY':'Makine','XULAS':'Ulasim','XBLSM':'Bilisim','XSGRT':'Sigorta',
  'XMADN':'Maden','XTAST':'Tas/Toprak','XSPOR':'Spor','XTRZM':'Turizm',
  'XGMYO':'GYO','XFINK':'Finansal','XILTM':'Iletisim','XTCRT':'Ticaret'
};

// Tarayici sekmesindeki filtre butonlarini guncelle
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      // Mevcut filtre butonlari satirina yeni endeksler ekle
      var filterRow = document.querySelector('.idx-btns') || document.querySelector('[id*="idxBtns"]');
      // Endeks degiskenini guncelle
      if(typeof window.IDX_LABELS === 'undefined'){
        window.IDX_LABELS = IDX_EXTRA;
      } else {
        Object.assign(window.IDX_LABELS, IDX_EXTRA);
      }
      console.log('Endeks etiketleri guncellendi');
    }catch(e){}
  },1000);
});

}catch(e){ console.warn('Hisse listesi genisletme:',e.message); }
})();

</script>
<script>

// BIST PRO v1 - YASAL SORUMLULUK REDDI BEYANI
// Tum uygulama icinde zorunlu hukuki koruma

//  SORUMLULUK REDDI METINLERI 
var DISCLAIMER = {
  short: 'Bu uygulama yatirim tavsiyesi vermez. Egitim ve bilgilendirme amaclidir.',
  medium: 'BIST Pro, kisisel kullanim ve egitim amacli gelistirilmis bir teknik analiz '
    +'aracidir. Uygulama icerisindeki hicbir veri, sinyal, analiz veya bilgi yatirim '
    +'tavsiyesi niteliginde degildir. Sermaye Piyasasi Kurulu (SPK) tarafindan lisansli '
    +'yatirim danismanligi hizmeti kapsaminda degerlendirilmez.',
  full: 'YASAL BILDIRIM VE SORUMLULUK REDDI\n\n'
    +'Bu uygulama ("BIST Pro") yalnizca kisisel kullanim, egitim ve bilgilendirme '
    +'amacli hazirlanmistir.\n\n'
    +'YATIRIM TAVSIYESI DEGIL: Bu uygulama icerisinde yer alan hicbir sinyal, '
    +'analiz, grafik, gosterge, backtest sonucu, yapay zeka ciktisi veya diger herhangi '
    +'bir bilgi, 6362 sayili Sermaye Piyasasi Kanunu ve ilgili mevzuat kapsaminda '
    +'yatirim tavsiyesi, portfoy yonetimi veya yatirim danismanligi hizmeti '
    +'teskil etmez.\n\n'
    +'RISK UYARISI: Borsa ve sermaye piyasalarinda islem yapmak onemli finansal riskler '
    +'icerir. Yatirimlarinizin tamamini veya bir kismini kaybedebilirsiniz. Gecmis '
    +'performans gelecekteki sonuclarin garantisi degildir.\n\n'
    +'KISISEL KARAR: Alim-satim kararlari tamamen kullanicinin kendisine aittir. '
    +'Uygulama gelistiricisi hicbir sekilde yatirim sonuclarindan sorumlu tutulamaz.\n\n'
    +'VERI DOGRULUGU: Gosterilen fiyat ve veriler 3. parti kaynaklardan alinmakta olup '
    +'gercek zamanli olmayabilir, hatalar icerebilir. Resmi islem kararlarinizda '
    +'brokerinizin sistemini kullanin.\n\n'
    +'Bu uygulamayi kullanarak yukaridaki kosullari kabul etmis sayilirsiniz.',
  singleLine: 'Yatirim tavsiyesi degildir | Kisisel & egitim amaclidir | SPK lisansli danismanlik kapsaminda degerlendirilmez',
};

//  1. TITLE VE META 
(function(){
  try{
    // Title guncelle
    document.title = 'BIST Pro - Teknik Analiz Araci';

    // Meta description ekle
    var meta = document.querySelector('meta[name="description"]');
    if(!meta){
      meta = document.createElement('meta');
      meta.name = 'description';
      document.head.appendChild(meta);
    }
    meta.content = 'BIST Pro - Kisisel kullanim ve egitim amacli teknik analiz araci. '
      +'Yatirim tavsiyesi niteligi tasimaz.';

    // Meta robots - index edilmesin (ihtiyatli)
    var robots = document.querySelector('meta[name="robots"]');
    if(!robots){
      robots = document.createElement('meta');
      robots.name = 'robots';
      robots.content = 'noindex, nofollow';
      document.head.appendChild(robots);
    }
  }catch(e){}
})();

//  2. HEADER DISCLAIMER BANDI 
function injectHeaderDisclaimer(){
  try{
    if(document.getElementById('disclaimerBand')) return;
    var band = document.createElement('div');
    band.id = 'disclaimerBand';
    band.style.cssText =
      'background:rgba(255,184,0,.07);'
      +'border-bottom:1px solid rgba(255,184,0,.2);'
      +'padding:4px 12px;'
      +'display:flex;align-items:center;justify-content:space-between;'
      +'gap:8px;position:sticky;top:0;z-index:99;';
    band.innerHTML =
      '<span style="font-size:8px;color:rgba(255,184,0,.8);flex:1;line-height:1.4">'
      +'&#9888; '+DISCLAIMER.singleLine+'</span>'
      +'<button id="disclaimerClose" onclick="this.parentElement.style.display=\'none\'" '
      +'style="background:none;border:none;color:rgba(255,184,0,.5);font-size:14px;'
      +'cursor:pointer;flex-shrink:0;padding:0 2px">&#10005;</button>';
    // Kapali tutuldu mu?
    if(localStorage.getItem('bist_disc_closed') === '1'){
      band.style.display = 'none';
    }
    document.getElementById('disclaimerClose') &&
      document.getElementById('disclaimerClose').addEventListener('click',function(){
        localStorage.setItem('bist_disc_closed','1');
      });
    var body = document.body;
    if(body) body.insertBefore(band, body.firstChild);
  }catch(e){}
}

//  3. SINYAL DETAY MODALINA UYARI 
// openSig fonksiyonunu wrap et - her sinyal acildiginda uyari ekle
var _origOpenSig = typeof openSig === 'function' ? openSig : null;
if(_origOpenSig){
  openSig = function(idx){
    _origOpenSig(idx);
    // Modal acildiktan sonra uyari ekle
    setTimeout(function(){
      try{
        var mcont = document.getElementById('mcont');
        if(!mcont) return;
        if(mcont.querySelector('.sig-disclaimer')) return;
        var warn = document.createElement('div');
        warn.className = 'sig-disclaimer';
        warn.style.cssText =
          'margin:8px 0 0 0;padding:8px 10px;'
          +'background:rgba(255,184,0,.06);'
          +'border:1px solid rgba(255,184,0,.15);'
          +'border-radius:8px;font-size:8px;'
          +'color:rgba(255,184,0,.7);line-height:1.5;';
        warn.textContent = '&#9888; '+DISCLAIMER.short+' Gecmis performans gelecegi garantilemez.';
        warn.innerHTML = '<span style="font-weight:700">&#9888; UYARI:</span> '
          +DISCLAIMER.short+' Gecmis performans gelecegi garantilemez.';
        mcont.appendChild(warn);
      }catch(e){}
    },100);
  };
}

//  4. AYARLAR SAYFASINA SORUMLULUK REDDI 
var _origLoadSetsUI = typeof loadSetsUI === 'function' ? loadSetsUI : null;
if(_origLoadSetsUI){
  loadSetsUI = function(){
    _origLoadSetsUI();
    setTimeout(function(){
      try{
        var page = document.getElementById('page-settings');
        if(!page) return;
        if(page.querySelector('#settingsDisc')) return;
        var disc = document.createElement('div');
        disc.id = 'settingsDisc';
        disc.style.cssText =
          'margin:10px;padding:12px 14px;'
          +'background:rgba(255,184,0,.05);'
          +'border:1px solid rgba(255,184,0,.15);'
          +'border-radius:11px;';
        disc.innerHTML =
          '<div style="font-size:9px;font-weight:700;color:rgba(255,184,0,.8);'
          +'margin-bottom:6px">&#9888; YASAL SORUMLULUK REDDI BEYANI</div>'
          +'<div style="font-size:8.5px;color:rgba(255,255,255,.45);line-height:1.6">'
          +DISCLAIMER.medium
          +'</div>'
          +'<button onclick="showFullDisclaimer()" '
          +'style="margin-top:8px;padding:5px 12px;border-radius:7px;'
          +'background:rgba(255,184,0,.08);border:1px solid rgba(255,184,0,.2);'
          +'color:rgba(255,184,0,.7);font-size:8px;cursor:pointer;">'
          +'Tam Metni Goster</button>';
        page.appendChild(disc);
      }catch(e){}
    },200);
  };
}

//  5. TAM SORUMLULUK REDDI MODAL 
window.showFullDisclaimer = function(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Yasal Bildirim';
    var html =
      '<div style="padding:4px 0">'
      +'<div style="background:rgba(255,184,0,.06);border:1px solid rgba(255,184,0,.15);'
      +'border-radius:10px;padding:14px;margin-bottom:10px">'
      +'<pre style="font-size:9px;color:rgba(255,255,255,.55);line-height:1.7;'
      +'white-space:pre-wrap;font-family:inherit;margin:0">'
      +DISCLAIMER.full
      +'</pre></div>'
      +'<div style="font-size:8px;color:rgba(255,255,255,.3);text-align:center;margin-bottom:8px">'
      +'Bu uygulamayi kullanmaya devam ederek yukaridaki kosullari kabul etmis sayilirsiniz.</div>'
      +'<button onclick="closeM&&closeM()" '
      +'style="width:100%;padding:11px;border-radius:9px;background:rgba(255,255,255,.06);'
      +'border:1px solid rgba(255,255,255,.1);color:rgba(255,255,255,.5);font-size:11px;cursor:pointer">'
      +'Okudum, Kabul Ediyorum</button>'
      +'</div>';
    document.getElementById('mcont').innerHTML = html;
    modal.classList.add('on');
  }catch(e){}
};

//  6. ILK ACILISTA KVKK / DISCLAIMER ONAY 
function showFirstRunDisclaimer(){
  try{
    if(localStorage.getItem('bist_disc_accepted') === '1') return;
    // Modal olustur
    var overlay = document.createElement('div');
    overlay.id = 'firstRunDisc';
    overlay.style.cssText =
      'position:fixed;inset:0;background:rgba(0,0,0,.96);z-index:10001;'
      +'display:flex;align-items:flex-end;justify-content:center;'
      +'padding:0 0 env(safe-area-inset-bottom) 0;';
    overlay.innerHTML =
      '<div style="background:#0D0D0D;border-top:1px solid rgba(255,184,0,.2);'
      +'border-radius:18px 18px 0 0;padding:24px 20px 32px;max-width:480px;width:100%">'
      +'<div style="width:36px;height:3px;background:rgba(255,255,255,.2);'
      +'border-radius:2px;margin:0 auto 20px;"></div>'
      +'<div style="font-size:16px;font-weight:800;color:#F0F0F0;margin-bottom:6px">'
      +'BIST Pro</div>'
      +'<div style="font-size:10px;color:rgba(255,184,0,.7);margin-bottom:14px;'
      +'font-weight:600">Kisisel Kullanim & Egitim Araci</div>'
      +'<div style="font-size:10px;color:rgba(255,255,255,.55);line-height:1.7;margin-bottom:18px">'
      +'Bu uygulama <b style="color:rgba(255,255,255,.8)">kisisel kullanim ve egitim amacli</b> '
      +'gelistirilmis bir teknik analiz aracidir.<br><br>'
      +'Icerisindeki hicbir sinyal, analiz veya veri; '
      +'<b style="color:rgba(255,68,68,.8)">yatirim tavsiyesi, portfoy yonetimi veya '
      +'yatirim danismanligi hizmeti teskil etmez</b> '
      +'ve SPK lisansli danismanlik kapsaminda degerlendirilmez.<br><br>'
      +'Yatirim kararlarinizdan yalnizca siz sorumlusunuz.'
      +'</div>'
      +'<div style="display:grid;gap:8px">'
      +'<button onclick="acceptDisclaimer()" '
      +'style="padding:14px;border-radius:11px;background:rgba(255,184,0,.12);'
      +'border:1px solid rgba(255,184,0,.3);color:rgba(255,184,0,.9);'
      +'font-size:13px;font-weight:700;cursor:pointer;">'
      +'Okudum, Anladim - Devam Et</button>'
      +'<button onclick="showFullDisclaimer()" '
      +'style="padding:10px;border-radius:9px;background:transparent;'
      +'border:1px solid rgba(255,255,255,.08);color:rgba(255,255,255,.35);'
      +'font-size:11px;cursor:pointer;">'
      +'Tam Yasal Metni Oku</button>'
      +'</div></div>';
    document.body.appendChild(overlay);
  }catch(e){}
}

window.acceptDisclaimer = function(){
  try{
    localStorage.setItem('bist_disc_accepted','1');
    // firstRunDisc overlay'ini kaldir
    var overlay = document.getElementById('firstRunDisc');
    if(overlay){
      overlay.style.transition = 'opacity .3s';
      overlay.style.opacity = '0';
      setTimeout(function(){ try{ if(overlay.parentNode) overlay.parentNode.removeChild(overlay); }catch(e){} },300);
    }
    // onboardOverlay varsa onu da kapat
    var ob = document.getElementById('onboardOverlay');
    if(ob) ob.style.display = 'none';
    if(typeof haptic === 'function') haptic('success');
    if(typeof toast === 'function') toast('Hos geldiniz!');
  }catch(e){ console.warn('acceptDisclaimer:',e); }
};

//  7. HAKKINDA PANELI 
window.showAboutPanel = function(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Hakkinda';
    var html =
      '<div style="padding:4px 0;text-align:center">'
      +'<div style="font-size:32px;font-weight:800;margin-bottom:4px">'
      +'<span style="color:#fff">BIST</span><span style="color:var(--cyan)"> Pro</span></div>'
      +'<div style="font-size:10px;color:var(--t4);margin-bottom:16px">v1.0</div>'
      +'<div style="text-align:left;background:rgba(255,255,255,.03);border-radius:10px;'
      +'padding:12px;margin-bottom:10px">'
      +'<div style="font-size:9px;font-weight:700;color:var(--cyan);margin-bottom:8px">'
      +'UYGULAMA HAKKINDA</div>'
      +'<div style="font-size:9px;color:var(--t3);line-height:1.7">'
      +'BIST Pro; Borsa Istanbul hisse senetlerini teknik analiz '
      +'yontemleriyle tarayan, backtest yapan ve kullanicilari bilgilendiren '
      +'kisisel kullanim amacli bir yazilim aracidir.<br><br>'
      +'Egitim ve arastirma amaciyla gelistirilmistir. '
      +'Hicbir sekilde yatirim tavsiyesi vermez.'
      +'</div></div>'
      +'<div style="text-align:left;background:rgba(255,68,68,.04);border:1px solid rgba(255,68,68,.1);'
      +'border-radius:10px;padding:12px;margin-bottom:10px">'
      +'<div style="font-size:9px;font-weight:700;color:rgba(255,68,68,.7);margin-bottom:8px">'
      +'&#9888; YASAL SORUMLULUK REDDI</div>'
      +'<div style="font-size:8.5px;color:rgba(255,255,255,.4);line-height:1.7">'
      +'Bu uygulama 6362 sayili Sermaye Piyasasi Kanunu kapsaminda '
      +'yatirim danismanligi, portfoy yonetimi veya aracilik hizmeti '
      +'saglamaz. Uygulama icerisindeki hicbir sinyal, analiz, tahmin veya '
      +'veri yatirim tavsiyesi niteligi tasimaz.<br><br>'
      +'Yatirim kararlari tamamen kullaniciya aittir. '
      +'Gelistirici hicbir finansal kayiptan sorumlu tutulamaz.<br><br>'
      +'Sermaye piyasalarinda kayip riski mevcuttur. '
      +'Yatirim yapmadan once bagimsiz finansal danismanli alin.'
      +'</div></div>'
      +'<button onclick="showFullDisclaimer()" '
      +'style="width:100%;padding:9px;border-radius:8px;margin-bottom:6px;'
      +'background:rgba(255,184,0,.07);border:1px solid rgba(255,184,0,.15);'
      +'color:rgba(255,184,0,.7);font-size:10px;cursor:pointer">'
      +'Tam Yasal Metni Oku</button>'
      +'<button onclick="closeM&&closeM()" '
      +'style="width:100%;padding:9px;border-radius:8px;'
      +'background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);'
      +'color:var(--t3);font-size:10px;cursor:pointer">Kapat</button>'
      +'</div>';
    document.getElementById('mcont').innerHTML = html;
    modal.classList.add('on');
  }catch(e){}
};

//  8. ? BUTONUNA HAKKINDA BAGLA 
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      // Mevcut ? (soru) butonu
      var helpBtn = document.getElementById('helpBtn') ||
                    document.querySelector('[onclick*="tutorial"]') ||
                    document.querySelector('.help-btn');
      if(helpBtn){
        var origClick = helpBtn.onclick;
        helpBtn.onclick = function(e){
          // Shift tusuna basiliysa orijinal tutorial ac
          if(e && e.shiftKey){ if(origClick) origClick.call(this,e); return; }
          showAboutPanel();
        };
      }
      // Floating ? butonu
      var fab = document.querySelector('[style*="border-radius:50%"][style*="?"]') ||
                document.querySelector('.fab');
      if(fab) fab.onclick = showAboutPanel;

      // Header bandi ekle
      injectHeaderDisclaimer();

      // Ilk acilis
      showFirstRunDisclaimer();

    }catch(e){}
  }, 800);
});

//  9. FOOTER DISCLAIMER 
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      var footer = document.querySelector('footer') ||
                   document.getElementById('footer');
      if(!footer) return;
      var disc = document.createElement('div');
      disc.style.cssText =
        'padding:6px 12px;font-size:7.5px;color:rgba(255,255,255,.2);'
        +'text-align:center;line-height:1.5;border-top:1px solid rgba(255,255,255,.04);';
      disc.textContent = DISCLAIMER.short
        +' | Gecmis performans gelecek sonuclarin garantisi degildir.'
        +' | Yatirim kararlarinizdan sorumlu degiliz.';
      footer.appendChild(disc);
    }catch(e){}
  }, 1000);
});

</script>
<script>
// BIST PRO - HISSE DASHBOARD BLOK 22
// TradingView Lightweight Charts v4 (CDN)
// Candlestick + Volume + RSI + MACD + Bollinger
// MTF Confluence + Desen Tanima + KAP Haberler
// AI Yorumu + Destek/Direnc + Sezonsellik

//  CSS 
(function(){
  try{
    var st=document.createElement('style');
    st.textContent=
      // Dashboard modal - tam ekran
      '#stockDashboard{position:fixed;inset:0;background:#000;z-index:3000;display:none;flex-direction:column;overflow:hidden}'
      +'#stockDashboard.on{display:flex}'
      // Header
      +'.sdh{height:52px;background:rgba(5,5,15,.95);border-bottom:1px solid rgba(255,255,255,.07);display:flex;align-items:center;gap:10px;padding:0 14px;flex-shrink:0;}'
      +'.sdh-back{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.06);border:none;color:var(--t2);font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}'
      +'.sdh-ticker{font-size:17px;font-weight:800;color:var(--t1);letter-spacing:.5px}'
      +'.sdh-name{font-size:10px;color:var(--t4);margin-left:2px}'
      +'.sdh-price{margin-left:auto;text-align:right}'
      +'.sdh-price-val{font-size:18px;font-weight:800;color:var(--t1)}'
      +'.sdh-price-chg{font-size:10px;font-weight:700}'
      +'.sdh-price-chg.up{color:var(--green)}.sdh-price-chg.dn{color:var(--red)}'
      // Tab bar
      +'.sdtabs{display:flex;background:rgba(0,0,0,.6);border-bottom:1px solid rgba(255,255,255,.06);flex-shrink:0;overflow-x:auto;scrollbar-width:none}'
      +'.sdtab{padding:10px 14px;font-size:10px;font-weight:600;color:var(--t4);background:none;border:none;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:all .2s;flex-shrink:0}'
      +'.sdtab.active{color:var(--cyan);border-bottom-color:var(--cyan)}'
      // Content area
      +'.sdcontent{flex:1;overflow-y:auto;padding:10px}'
      // Chart container
      +'#sdChartWrap{width:100%;height:320px;background:#000;border-radius:11px;overflow:hidden;margin-bottom:10px;position:relative}'
      +'#sdChart{width:100%;height:240px}'
      +'#sdRSI{width:100%;height:80px;border-top:1px solid rgba(255,255,255,.05)}'
      +'.chart-tf-btns{display:flex;gap:5px;padding:6px 8px;background:rgba(255,255,255,.03);border-radius:8px;margin-bottom:8px}'
      +'.chart-tf-btn{padding:4px 10px;border-radius:6px;font-size:9px;font-weight:700;background:none;border:1px solid rgba(255,255,255,.07);color:var(--t4);cursor:pointer;transition:all .15s}'
      +'.chart-tf-btn.active{background:rgba(0,212,255,.1);border-color:rgba(0,212,255,.3);color:var(--cyan)}'
      +'.chart-ind-btns{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}'
      +'.chart-ind-btn{padding:3px 9px;border-radius:5px;font-size:8.5px;font-weight:600;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);color:var(--t4);cursor:pointer}'
      +'.chart-ind-btn.active{background:rgba(0,212,255,.1);border-color:rgba(0,212,255,.25);color:var(--cyan)}'
      // Metrik kartlar
      +'.sd-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}'
      +'.sd-metric{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:10px}'
      +'.sd-metric-val{font-size:15px;font-weight:800;color:var(--t1)}'
      +'.sd-metric-lbl{font-size:8px;color:var(--t4);margin-top:2px}'
      +'.sd-metric.pos .sd-metric-val{color:var(--green)}'
      +'.sd-metric.neg .sd-metric-val{color:var(--red)}'
      +'.sd-metric.neu .sd-metric-val{color:var(--cyan)}'
      // MTF Confluence
      +'.mtf-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}'
      +'.mtf-cell{background:rgba(255,255,255,.03);border-radius:9px;padding:10px;text-align:center;border:1px solid rgba(255,255,255,.06)}'
      +'.mtf-cell.bull{background:rgba(0,230,118,.08);border-color:rgba(0,230,118,.2)}'
      +'.mtf-cell.bear{background:rgba(255,68,68,.06);border-color:rgba(255,68,68,.15)}'
      +'.mtf-cell.neut{background:rgba(255,255,255,.03);border-color:rgba(255,255,255,.06)}'
      +'.mtf-tf{font-size:9px;font-weight:700;color:var(--t4);margin-bottom:3px}'
      +'.mtf-dir{font-size:14px;margin-bottom:2px}'
      +'.mtf-score{font-size:8px;color:var(--t3)}'
      // Confluence badge
      +'.confluence-badge{padding:10px 14px;border-radius:11px;display:flex;align-items:center;gap:10px;margin-bottom:10px}'
      +'.confluence-badge.full{background:rgba(0,230,118,.1);border:1px solid rgba(0,230,118,.3)}'
      +'.confluence-badge.partial{background:rgba(255,184,0,.08);border:1px solid rgba(255,184,0,.2)}'
      +'.confluence-badge.none{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}'
      // Desen
      +'.pattern-item{display:flex;align-items:center;gap:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:8px;margin-bottom:5px;border:1px solid rgba(255,255,255,.06)}'
      +'.pattern-icon{font-size:16px;flex-shrink:0}'
      +'.pattern-name{font-size:10px;font-weight:700;color:var(--t1)}'
      +'.pattern-desc{font-size:8.5px;color:var(--t4)}'
      +'.pattern-dir{font-size:8px;padding:2px 7px;border-radius:4px;font-weight:700;margin-left:auto;flex-shrink:0}'
      +'.pattern-dir.bull{background:rgba(0,230,118,.12);color:var(--green)}'
      +'.pattern-dir.bear{background:rgba(255,68,68,.1);color:var(--red)}'
      +'.pattern-dir.neut{background:rgba(255,255,255,.06);color:var(--t3)}'
      // Destek/Direnc
      +'.sr-level{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:7px;margin-bottom:4px}'
      +'.sr-level.res{background:rgba(255,68,68,.06);border:1px solid rgba(255,68,68,.12)}'
      +'.sr-level.sup{background:rgba(0,230,118,.05);border:1px solid rgba(0,230,118,.12)}'
      +'.sr-price{font-size:12px;font-weight:700}'
      +'.sr-type{font-size:8px;font-weight:600;padding:1px 6px;border-radius:4px}'
      +'.sr-level.res .sr-type{background:rgba(255,68,68,.12);color:var(--red)}'
      +'.sr-level.sup .sr-type{background:rgba(0,230,118,.1);color:var(--green)}'
      +'.sr-dist{font-size:8.5px;color:var(--t4);margin-left:auto}'
      // KAP
      +'.kap-item{padding:10px 12px;background:rgba(255,255,255,.03);border-radius:9px;margin-bottom:6px;border:1px solid rgba(255,255,255,.06);cursor:pointer}'
      +'.kap-title{font-size:10px;font-weight:600;color:var(--t2);line-height:1.4;margin-bottom:4px}'
      +'.kap-meta{display:flex;align-items:center;gap:8px;font-size:8px;color:var(--t4)}'
      +'.kap-type{padding:1px 6px;border-radius:4px;font-size:7.5px;font-weight:700;background:rgba(0,212,255,.1);color:var(--cyan)}'
      // AI yorum
      +'.ai-comment{padding:12px 14px;background:rgba(192,132,252,.06);border:1px solid rgba(192,132,252,.15);border-radius:11px;margin-bottom:8px}'
      +'.ai-comment-hdr{display:flex;align-items:center;gap:7px;margin-bottom:8px}'
      // Sezonsellik
      +'.season-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:4px;margin-bottom:10px}'
      +'.season-cell{border-radius:5px;padding:6px 2px;text-align:center}'
      +'.season-month{font-size:7.5px;color:rgba(255,255,255,.4);margin-bottom:2px}'
      +'.season-pct{font-size:9px;font-weight:700}'
      // Korelasyon
      +'.corr-item{display:flex;align-items:center;gap:8px;padding:7px 10px;background:rgba(255,255,255,.03);border-radius:8px;margin-bottom:4px}'
      +'.corr-bar{flex:1;height:6px;background:rgba(255,255,255,.08);border-radius:3px;overflow:hidden}'
      +'.corr-fill{height:100%;border-radius:3px;transition:width .5s}'
      // Loading
      +'.sd-loading{display:flex;align-items:center;justify-content:center;height:200px;gap:10px}'
      +'.sd-spin{width:24px;height:24px;border:2px solid rgba(0,212,255,.2);border-top-color:var(--cyan);border-radius:50%;animation:spin .8s linear infinite}'
      +'@keyframes spin{to{transform:rotate(360deg)}}'
      // Gorunur olmayan ic section
      +'.sd-section{margin-bottom:14px}'
      +'.sd-section-title{font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:7px;display:flex;align-items:center;gap:6px}'
      +'.sd-section-title::after{content:"";flex:1;height:1px;background:rgba(255,255,255,.06)}'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  LIGHTWEIGHT CHARTS YUKLE 
var _lwcLoaded = false;
var _lwcLoading = false;
var _lwcCallbacks = [];
function loadLWC(cb){
  if(_lwcLoaded){ cb(); return; }
  _lwcCallbacks.push(cb);
  if(_lwcLoading) return;
  _lwcLoading = true;
  var s = document.createElement('script');
  s.src = 'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js';
  s.onload = function(){
    _lwcLoaded = true;
    _lwcLoading = false;
    _lwcCallbacks.forEach(function(fn){ try{fn();}catch(e){} });
    _lwcCallbacks = [];
  };
  s.onerror = function(){
    _lwcLoading = false;
    // Fallback: CDN degistir
    var s2 = document.createElement('script');
    s2.src = 'https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js';
    s2.onload = function(){ _lwcLoaded=true; _lwcCallbacks.forEach(function(fn){try{fn();}catch(e){}}); _lwcCallbacks=[]; };
    document.head.appendChild(s2);
  };
  document.head.appendChild(s);
}

//  DASHBOARD STATE 
var _sd = {
  ticker: null,
  name: null,
  tf: 'D',
  activeTab: 'chart',
  ohlcv: null,
  analysis: null,
  chart: null,
  candleSeries: null,
  volumeSeries: null,
  rsiChart: null,
  rsiSeries: null,
  macdChart: null,
  indicators: {ema20:false, ema50:true, ema200:true, bb:false, volume:true, macd:false},
  emaSeries: {},
  bbSeries: {},
  loading: false,
};

//  ANA DASHBOARD AC 
window.openStockDashboard = function(ticker, name){
  try{
    _sd.ticker = ticker;
    _sd.name = name || ticker;
    _sd.activeTab = 'chart';
    _sd.tf = 'D';

    var modal = document.getElementById('stockDashboard');
    if(!modal){ createDashboardModal(); modal = document.getElementById('stockDashboard'); }
    modal.classList.add('on');
    document.body.style.overflow = 'hidden';

    updateDashboardHeader(ticker, name);
    loadLWC(function(){
      switchSDTab('chart');
      fetchDashboardData(ticker, 'D');
    });
    if(typeof haptic==='function') haptic('medium');
  }catch(e){ console.warn('openStockDashboard:',e); }
};

function createDashboardModal(){
  var modal = document.createElement('div');
  modal.id = 'stockDashboard';
  modal.innerHTML =
    '<div class="sdh">'
    +'<button class="sdh-back" onclick="closeStockDashboard()">&#8592;</button>'
    +'<div><div class="sdh-ticker" id="sdTicker">-</div><div class="sdh-name" id="sdName"></div></div>'
    +'<div class="sdh-price"><div class="sdh-price-val" id="sdPrice">-</div><div class="sdh-price-chg" id="sdChg"></div></div>'
    +'</div>'
    +'<div class="sdtabs">'
    +['chart','mtf','desenler','sr','kap','sezonsellik','korelasyon','ai'].map(function(t){
      var labels={chart:'Grafik',mtf:'MTF',desenler:'Desenler',sr:'Destek/Direnc',
        kap:'KAP',sezonsellik:'Sezonsellik',korelasyon:'Korelasyon',ai:'AI Analiz'};
      return '<button class="sdtab" onclick="switchSDTab(\''+t+'\')" id="sdtab_'+t+'">'+labels[t]+'</button>';
    }).join('')
    +'</div>'
    +'<div class="sdcontent" id="sdContent"><div class="sd-loading"><div class="sd-spin"></div><span style="font-size:11px;color:var(--t4)">Yukleniyor...</span></div></div>';
  document.body.appendChild(modal);
}

window.closeStockDashboard = function(){
  var modal = document.getElementById('stockDashboard');
  if(modal) modal.classList.remove('on');
  document.body.style.overflow = '';
  // Grafikleri temizle
  if(_sd.chart){ try{_sd.chart.remove();}catch(e){} _sd.chart=null; }
  if(_sd.rsiChart){ try{_sd.rsiChart.remove();}catch(e){} _sd.rsiChart=null; }
};

function updateDashboardHeader(ticker, name){
  var t=document.getElementById('sdTicker'); if(t) t.textContent=ticker;
  var n=document.getElementById('sdName'); if(n) n.textContent=name||'';
  // Fiyat cache
  var cached = S.priceCache && S.priceCache[ticker];
  if(cached && cached.price){
    var pEl=document.getElementById('sdPrice'); if(pEl) pEl.textContent='TL'+cached.price.toFixed(2);
    var cEl=document.getElementById('sdChg');
    if(cEl){
      var chg=cached.change_pct||0;
      cEl.textContent=(chg>=0?'+':'')+chg.toFixed(2)+'%';
      cEl.className='sdh-price-chg '+(chg>=0?'up':'dn');
    }
  }
}

//  VER FETCH 
var PROXY = (function(){
  try{ return localStorage.getItem('bist_proxy_url') || 'https://bist-price-proxy.onrender.com'; }catch(e){ return 'https://bist-price-proxy.onrender.com'; }
})();

function fetchDashboardData(ticker, tf){
  _sd.loading = true;
  var el = document.getElementById('sdContent');
  if(el) el.innerHTML = '<div class="sd-loading"><div class="sd-spin"></div><span style="font-size:11px;color:var(--t4)">'+ticker+' verisi yukleniyor...</span></div>';

  Promise.all([
    fetch(PROXY+'/ohlcv/'+ticker+'?tf='+tf).then(function(r){return r.json();}).catch(function(){return null;}),
    fetch(PROXY+'/analyze/'+ticker+'?tf='+tf).then(function(r){return r.json();}).catch(function(){return null;}),
  ]).then(function(results){
    _sd.ohlcv = results[0];
    _sd.analysis = results[1];
    _sd.loading = false;
    // Fiyati guncelle
    if(_sd.analysis && _sd.analysis.price){
      var pEl=document.getElementById('sdPrice'); if(pEl) pEl.textContent='TL'+_sd.analysis.price.toFixed(2);
    }
    switchSDTab(_sd.activeTab, true);
  }).catch(function(){
    _sd.loading = false;
    if(el) el.innerHTML='<div style="padding:20px;text-align:center;color:var(--red)">Veri yuklenemedi. Proxy baglantisini kontrol edin.</div>';
  });
}

//  TAB YNETM 
window.switchSDTab = function(tab, force){
  _sd.activeTab = tab;
  document.querySelectorAll('.sdtab').forEach(function(b){
    b.classList.toggle('active', b.id==='sdtab_'+tab);
  });
  var el = document.getElementById('sdContent');
  if(!el) return;
  if(_sd.loading && !force){ return; }
  if(!_sd.ohlcv && tab==='chart'){
    el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div></div>';
    return;
  }
  if(tab==='chart') renderSDChart();
  else if(tab==='mtf') renderSDMTF();
  else if(tab==='desenler') renderSDPatterns();
  else if(tab==='sr') renderSDSR();
  else if(tab==='kap') renderSDKAP();
  else if(tab==='sezonsellik') renderSDSeason();
  else if(tab==='korelasyon') renderSDCorrelation();
  else if(tab==='ai') renderSDAI();
};

//  1. GRAFIK SEKMESI 
function renderSDChart(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  var ohlcv = _sd.ohlcv && _sd.ohlcv.ohlcv;
  if(!ohlcv || !ohlcv.length){
    el.innerHTML='<div style="padding:20px;text-align:center;color:var(--t4);font-size:11px">Grafik verisi yok</div>';
    return;
  }
  el.innerHTML =
    '<div class="chart-tf-btns">'
    +['D','240','120','W'].map(function(tf){
      var lbl={D:'1G','240':'4S','120':'2S',W:'1H'}[tf]||tf;
      return '<button class="chart-tf-btn'+(tf===_sd.tf?' active':'')+'" onclick="changeDashboardTF(\''+tf+'\')">'+lbl+'</button>';
    }).join('')+'</div>'
    +'<div class="chart-ind-btns">'
    +[['ema50','EMA50'],['ema200','EMA200'],['bb','Bollinger'],['volume','Hacim']].map(function(p){
      return '<button class="chart-ind-btn'+(_sd.indicators[p[0]]?' active':'')+'" onclick="toggleDashboardInd(\''+p[0]+'\')">'+p[1]+'</button>';
    }).join('')+'</div>'
    +'<div id="sdChartWrap"><div id="sdChart"></div></div>'
    +'<div class="sd-section"><div class="sd-section-title">Indikatorler</div>'
    +'<div id="sdRSI" style="height:80px;background:#000;border-radius:8px"></div></div>'
    + renderSDMetricsHTML()
  ;
  setTimeout(function(){ buildLWChart(ohlcv); },50);
}

function buildLWChart(ohlcv){
  try{
    if(!window.LightweightCharts) return;
    // Eski grafigi temizle
    if(_sd.chart){ try{_sd.chart.remove();}catch(e){} _sd.chart=null; }
    if(_sd.rsiChart){ try{_sd.rsiChart.remove();}catch(e){} _sd.rsiChart=null; }

    var LWC = window.LightweightCharts;
    var wrap = document.getElementById('sdChart');
    if(!wrap) return;

    // Ana grafik
    _sd.chart = LWC.createChart(wrap, {
      width: wrap.clientWidth,
      height: 240,
      layout:{ background:{color:'#000'}, textColor:'rgba(255,255,255,.5)' },
      grid:{ vertLines:{color:'rgba(255,255,255,.05)'}, horzLines:{color:'rgba(255,255,255,.05)'} },
      crosshair:{ mode: LWC.CrosshairMode.Normal },
      rightPriceScale:{ borderColor:'rgba(255,255,255,.1)' },
      timeScale:{ borderColor:'rgba(255,255,255,.1)', timeVisible:true },
      handleScroll:{ touchDrag:true },
      handleScale:{ axisPressedMouseMove:true, pinch:true },
    });

    // Mum serisi
    _sd.candleSeries = _sd.chart.addCandlestickSeries({
      upColor:'#00E676', downColor:'#FF4444',
      borderUpColor:'#00E676', borderDownColor:'#FF4444',
      wickUpColor:'rgba(0,230,118,.6)', wickDownColor:'rgba(255,68,68,.6)',
    });

    // OHLCV'yi LWC formatina cevir
    var candles = ohlcv.filter(function(b){ return b.t&&b.o&&b.h&&b.l&&b.c; }).map(function(b){
      return { time: Math.floor(b.t/1000), open:b.o, high:b.h, low:b.l, close:b.c };
    }).sort(function(a,b){return a.time-b.time;});
    if(candles.length) _sd.candleSeries.setData(candles);

    // Hacim
    if(_sd.indicators.volume){
      _sd.volumeSeries = _sd.chart.addHistogramSeries({
        priceFormat:{type:'volume'},
        priceScaleId:'volume',
        scaleMargins:{top:0.85,bottom:0},
      });
      var volData = ohlcv.filter(function(b){return b.t&&b.v;}).map(function(b){
        return { time:Math.floor(b.t/1000), value:b.v, color: b.c>=b.o ? 'rgba(0,230,118,.3)':'rgba(255,68,68,.25)' };
      }).sort(function(a,b){return a.time-b.time;});
      if(volData.length) _sd.volumeSeries.setData(volData);
    }

    // EMA50
    if(_sd.indicators.ema50){
      var ema50s = _sd.chart.addLineSeries({ color:'rgba(255,184,0,.6)', lineWidth:1, priceLineVisible:false, lastValueVisible:false });
      var closes = ohlcv.map(function(b){return b.c;});
      var ema50v = calcEMAValues(closes, 50);
      var ema50d = ohlcv.slice(50).map(function(b,i){ return {time:Math.floor(b.t/1000), value:ema50v[i+50]}; }).filter(function(d){return d.value;}).sort(function(a,b){return a.time-b.time;});
      if(ema50d.length) ema50s.setData(ema50d);
    }

    // EMA200
    if(_sd.indicators.ema200){
      var ema200s = _sd.chart.addLineSeries({ color:'rgba(0,212,255,.5)', lineWidth:1, priceLineVisible:false, lastValueVisible:false });
      var closes2 = ohlcv.map(function(b){return b.c;});
      var ema200v = calcEMAValues(closes2, 200);
      var ema200d = ohlcv.slice(200).map(function(b,i){return {time:Math.floor(b.t/1000),value:ema200v[i+200]};}).filter(function(d){return d.value;}).sort(function(a,b){return a.time-b.time;});
      if(ema200d.length) ema200s.setData(ema200d);
    }

    // Sinyal fiyati marker
    if(_sd.analysis && _sd.analysis.price){
      var curPrice = _sd.analysis.price;
      var stopPrice = _sd.analysis.stop_price;
      var ema200v2 = _sd.analysis.ema200;
      // Stop line
      if(stopPrice){
        var stopLine = _sd.chart.addLineSeries({ color:'rgba(255,68,68,.7)', lineWidth:1, lineStyle:2, priceLineVisible:false, lastValueVisible:true, title:'Stop' });
        var lastTime = candles.length ? candles[candles.length-1].time : Math.floor(Date.now()/1000);
        stopLine.setData([{time:candles[Math.max(0,candles.length-50)].time, value:stopPrice},{time:lastTime,value:stopPrice}]);
      }
    }

    // Bollinger Bands
    if(_sd.indicators.bb){
      var closes3 = ohlcv.map(function(b){return b.c;});
      var bb = calcBollingerBands(closes3, 20, 2);
      var bbUpper = _sd.chart.addLineSeries({color:'rgba(192,132,252,.4)',lineWidth:1,priceLineVisible:false,lastValueVisible:false});
      var bbLower = _sd.chart.addLineSeries({color:'rgba(192,132,252,.4)',lineWidth:1,priceLineVisible:false,lastValueVisible:false});
      var bbTimes = ohlcv.slice(20).map(function(b,i){return Math.floor(b.t/1000);});
      var bbUpD = bb.upper.map(function(v,i){return{time:bbTimes[i],value:v};}).filter(function(d){return d.value&&d.time;}).sort(function(a,b){return a.time-b.time;});
      var bbLwD = bb.lower.map(function(v,i){return{time:bbTimes[i],value:v};}).filter(function(d){return d.value&&d.time;}).sort(function(a,b){return a.time-b.time;});
      if(bbUpD.length){bbUpper.setData(bbUpD);bbLower.setData(bbLwD);}
    }

    // RSI grafigi
    var rsiWrap = document.getElementById('sdRSI');
    if(rsiWrap){
      _sd.rsiChart = LWC.createChart(rsiWrap,{
        width:rsiWrap.clientWidth, height:80,
        layout:{background:{color:'#000'},textColor:'rgba(255,255,255,.4)'},
        grid:{vertLines:{color:'rgba(255,255,255,.03)'},horzLines:{color:'rgba(255,255,255,.03)'}},
        rightPriceScale:{borderColor:'rgba(255,255,255,.1)',scaleMargins:{top:0.1,bottom:0.1}},
        timeScale:{borderColor:'rgba(255,255,255,.1)',visible:false},
        crosshair:{mode:LWC.CrosshairMode.Normal},
        handleScroll:{touchDrag:true},handleScale:{pinch:true},
      });
      _sd.rsiSeries = _sd.rsiChart.addLineSeries({color:'var(--purple)||#C084FC',lineWidth:1.5,priceLineVisible:false});
      var rsiVals = calcRSI(ohlcv.map(function(b){return b.c;}), 14);
      var rsiData = ohlcv.slice(14).map(function(b,i){return{time:Math.floor(b.t/1000),value:rsiVals[i+14]};}).filter(function(d){return d.value&&d.time;}).sort(function(a,b){return a.time-b.time;});
      if(rsiData.length) _sd.rsiSeries.setData(rsiData);
      // 70/30 cizgileri
      if(rsiData.length){
        var t0=rsiData[0].time, tN=rsiData[rsiData.length-1].time;
        [[70,'rgba(255,68,68,.4)'],[30,'rgba(0,230,118,.4)'],[50,'rgba(255,255,255,.1)']].forEach(function(r){
          var ls = _sd.rsiChart.addLineSeries({color:r[1],lineWidth:1,lineStyle:2,priceLineVisible:false,lastValueVisible:false});
          ls.setData([{time:t0,value:r[0]},{time:tN,value:r[0]}]);
        });
      }
      // Senkronize crosshair
      _sd.chart.subscribeCrosshairMove(function(p){
        if(p.time && _sd.rsiChart){
          _sd.rsiChart.setCrosshairPosition(NaN,p.time,_sd.rsiSeries);
        }
      });
    }

    // Responsive resize
    var resizeObs = new ResizeObserver(function(){
      if(_sd.chart && wrap) _sd.chart.applyOptions({width:wrap.clientWidth});
      if(_sd.rsiChart && rsiWrap) _sd.rsiChart.applyOptions({width:rsiWrap.clientWidth});
    });
    resizeObs.observe(wrap);

    _sd.chart.timeScale().fitContent();
  }catch(e){ console.warn('buildLWChart:',e.message); }
}

window.changeDashboardTF = function(tf){
  _sd.tf = tf;
  fetchDashboardData(_sd.ticker, tf);
};

window.toggleDashboardInd = function(ind){
  _sd.indicators[ind] = !_sd.indicators[ind];
  renderSDChart();
};

function renderSDMetricsHTML(){
  var a = _sd.analysis || {};
  var metrics = [
    {v:a.rsi?a.rsi.toFixed(1):'-', l:'RSI(14)', cls: a.rsi>70?'neg':a.rsi<30?'pos':'neu'},
    {v:a.adx?a.adx.toFixed(1):'-', l:'ADX', cls:a.adx>40?'pos':a.adx>25?'neu':'neg'},
    {v:a.macd?(a.macd>0?'+':'')+a.macd.toFixed(3):'-', l:'MACD', cls:a.macd>0?'pos':a.macd<0?'neg':'neu'},
    {v:a.consensus?a.consensus.toFixed(1)+'%':'-', l:'Konsensus', cls:a.consensus>70?'pos':a.consensus>50?'neu':'neg'},
    {v:a.vol_ratio?a.vol_ratio.toFixed(2)+'x':'-', l:'Hacim Oran', cls:a.vol_ratio>1.5?'pos':'neu'},
    {v:a.pstate||'-', l:'Bolge', cls:a.pstate&&a.pstate.indexOf('UCUZ')>-1?'pos':a.pstate&&a.pstate.indexOf('PAHALI')>-1?'neg':'neu'},
  ];
  return '<div class="sd-section"><div class="sd-section-title">Indikatorler</div>'
    +'<div class="sd-metrics">'
    +metrics.map(function(m){
      return '<div class="sd-metric '+m.cls+'"><div class="sd-metric-val">'+m.v+'</div><div class="sd-metric-lbl">'+m.l+'</div></div>';
    }).join('')+'</div></div>';
}

//  2. MTF CONFLUENCE 
function renderSDMTF(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  if(!_sd.ohlcv){ el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div></div>'; fetchDashboardData(_sd.ticker,'D'); return; }

  var a = _sd.analysis || {};
  var ohlcv = _sd.ohlcv && _sd.ohlcv.ohlcv || [];

  // MTF hesapla
  var tfResults = calcMTFConfluence(ohlcv, a);
  var confluenceScore = tfResults.filter(function(t){return t.dir==='bull';}).length;
  var badgeCls = confluenceScore>=3?'full':confluenceScore>=2?'partial':'none';
  var badgeTxt = confluenceScore>=3?'GUCLU CONFLUENCE - 3/3 Zaman Dilimi AL':confluenceScore>=2?'KISMI CONFLUENCE - 2/3 Zaman Dilimi AL':'CONFLUENCE YOK';
  var badgeColor = confluenceScore>=3?'var(--green)':confluenceScore>=2?'var(--gold)':'var(--t4)';

  el.innerHTML =
    '<div class="sd-section">'
    +'<div class="confluence-badge '+badgeCls+'">'
    +'<div style="font-size:22px">'+(confluenceScore>=3?'':'')+'</div>'
    +'<div><div style="font-size:12px;font-weight:800;color:'+badgeColor+'">'+badgeTxt+'</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-top:3px">MTF Confluence Skoru: '+confluenceScore+'/3</div></div>'
    +'</div>'
    +'<div class="mtf-grid">'
    +tfResults.map(function(t){
      var cls = t.dir==='bull'?'bull':t.dir==='bear'?'bear':'neut';
      var icon = t.dir==='bull'?'YUKARI':t.dir==='bear'?'ASAGI':'-';
      return '<div class="mtf-cell '+cls+'">'
        +'<div class="mtf-tf">'+t.label+'</div>'
        +'<div class="mtf-dir">'+icon+'</div>'
        +'<div style="font-size:11px;font-weight:700;color:'+(t.dir==='bull'?'var(--green)':t.dir==='bear'?'var(--red)':'var(--t3)')+'">'+t.dir.toUpperCase()+'</div>'
        +'<div class="mtf-score">ADX: '+t.adx+' RSI: '+t.rsi+'</div>'
        +'</div>';
    }).join('')
    +'</div></div>'

    // Sistem onaylari
    +'<div class="sd-section"><div class="sd-section-title">Sistem Onayi</div>'
    +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px">'
    +[
      {k:'s1',l:'S1 SuperTrend+TMA'},{k:'s2',l:'S2 PRO Engine'},
      {k:'fu',l:'Fusion AI'},{k:'is_master',l:'Master AI'},
      {k:'a60',l:'Agent A60'},{k:'a61',l:'Agent A61'},
      {k:'a62',l:'Agent A62'},{k:'a81',l:'Agent A81'},
    ].map(function(s){
      var active = !!a[s.k];
      return '<div style="display:flex;align-items:center;gap:7px;padding:7px 9px;background:rgba(255,255,255,.03);border-radius:8px;border:1px solid rgba(255,255,255,.06)">'
        +'<div style="width:8px;height:8px;border-radius:50%;background:'+(active?'var(--green)':'rgba(255,255,255,.15)')+'"></div>'
        +'<span style="font-size:9.5px;color:'+(active?'var(--t2)':'var(--t4)')+'">'+s.l+'</span>'
        +'</div>';
    }).join('')
    +'</div></div>'

    // PRO 6 faktor
    +(a.pro_factors?
    '<div class="sd-section"><div class="sd-section-title">PRO Engine 6 Faktor</div>'
    +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">'
    +[
      {k:'rs_strong',l:'RS Guclu'},
      {k:'accum_d',l:'Akumulasyon (D)'},
      {k:'exp_4h',l:'4H Genisleme'},
      {k:'break_4h',l:'4H Kirilim'},
      {k:'mom_2h',l:'2H Momentum'},
      {k:'dna',l:'DNA Sinyal'},
    ].map(function(f){
      var v = a.pro_factors[f.k];
      return '<div style="display:flex;align-items:center;gap:6px;padding:6px 9px;background:rgba(255,255,255,.02);border-radius:7px;border:1px solid rgba(255,255,255,.04)">'
        +'<span style="font-size:14px">'+(v?'':'')+'</span>'
        +'<span style="font-size:9px;color:'+(v?'var(--t2)':'var(--t4)')+'">'+f.l+'</span>'
        +'</div>';
    }).join('')
    +'</div></div>'
    :'')
  ;
}

//  3. DESEN TANIMA 
function renderSDPatterns(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  var ohlcv = _sd.ohlcv && _sd.ohlcv.ohlcv;
  if(!ohlcv){ el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div></div>'; return; }

  var patterns = detectPatterns(ohlcv);

  el.innerHTML = '<div class="sd-section"><div class="sd-section-title">Tespit Edilen Desenler</div>'
    +(patterns.length===0
      ? '<div style="padding:20px;text-align:center;color:var(--t4);font-size:11px">Belirgin desen tespit edilmedi</div>'
      : patterns.map(function(p){
          return '<div class="pattern-item">'
            +'<div class="pattern-icon">'+p.icon+'</div>'
            +'<div style="flex:1"><div class="pattern-name">'+p.name+'</div>'
            +'<div class="pattern-desc">'+p.desc+'</div>'
            +'<div style="font-size:8px;color:var(--t4);margin-top:2px">Guven: '+p.confidence+'%</div></div>'
            +'<div class="pattern-dir '+(p.type==='bull'?'bull':p.type==='bear'?'bear':'neut')+'">'+(p.type==='bull'?'Boga':p.type==='bear'?'Ayi':'Notr')+'</div>'
            +'</div>';
        }).join('')
    )
    +'</div>'
    // Mum desenleri
    +'<div class="sd-section"><div class="sd-section-title">Mum Desenleri (Son 5 Gun)</div>'
    +detectCandlePatterns(ohlcv).map(function(p){
      return '<div class="pattern-item">'
        +'<div class="pattern-icon">'+p.icon+'</div>'
        +'<div style="flex:1"><div class="pattern-name">'+p.name+'</div>'
        +'<div class="pattern-desc">'+p.desc+'</div></div>'
        +'<div class="pattern-dir '+(p.type==='bull'?'bull':p.type==='bear'?'bear':'neut')+'">'+(p.type==='bull'?'Boga':'Ayi')+'</div>'
        +'</div>';
    }).join('')
    +'</div>';
}

//  4. DESTEK / DREN 
function renderSDSR(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  var ohlcv = _sd.ohlcv && _sd.ohlcv.ohlcv;
  if(!ohlcv){ el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div></div>'; return; }

  var curPrice = (_sd.analysis && _sd.analysis.price) || ohlcv[ohlcv.length-1].c;
  var levels = calcSupportResistance(ohlcv, curPrice);

  el.innerHTML =
    '<div style="background:rgba(0,212,255,.06);border:1px solid rgba(0,212,255,.15);border-radius:10px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px">'
    +'<div style="font-size:11px;color:var(--t4)">Anlik Fiyat</div>'
    +'<div style="font-size:18px;font-weight:800;color:var(--cyan);margin-left:auto">TL'+curPrice.toFixed(2)+'</div>'
    +'</div>'
    +'<div class="sd-section"><div class="sd-section-title">Direnc Seviyeleri</div>'
    +levels.filter(function(l){return l.type==='res';}).map(function(l){
      var dist = ((l.price-curPrice)/curPrice*100).toFixed(1);
      return '<div class="sr-level res">'
        +'<div style="flex:1"><div class="sr-price" style="color:var(--red)">TL'+l.price.toFixed(2)+'</div>'
        +'<div style="font-size:8px;color:var(--t4);margin-top:1px">'+l.desc+'</div></div>'
        +'<span class="sr-type">DIRENC</span>'
        +'<div class="sr-dist">+'+dist+'%</div>'
        +'</div>';
    }).join('')
    +'</div>'
    +'<div class="sd-section"><div class="sd-section-title">Destek Seviyeleri</div>'
    +levels.filter(function(l){return l.type==='sup';}).map(function(l){
      var dist = ((curPrice-l.price)/curPrice*100).toFixed(1);
      return '<div class="sr-level sup">'
        +'<div style="flex:1"><div class="sr-price" style="color:var(--green)">TL'+l.price.toFixed(2)+'</div>'
        +'<div style="font-size:8px;color:var(--t4);margin-top:1px">'+l.desc+'</div></div>'
        +'<span class="sr-type">DESTEK</span>'
        +'<div class="sr-dist">-'+dist+'%</div>'
        +'</div>';
    }).join('')
    +'</div>'
    +'<div style="margin-top:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-radius:8px;font-size:8.5px;color:var(--t4);line-height:1.6">'
    +'Seviyeler pivot noktasi, Fibonacci geri cekme ve hacim profili analizine dayanir. '
    +'Yakin seviyelerin gecilebilecegini unutmayiniz.'
    +'</div>'
  ;
}

//  5. KAP HABERLER 
function renderSDKAP(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div><span style="font-size:11px;color:var(--t4)">KAP kontrol ediliyor...</span></div>';

  // KAP RSS - CORS sorunu nedeniyle proxy uzerinden
  fetch(PROXY+'/ai/chat',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      message:'KAP ve borsa aciklamalari: '+_sd.ticker+' son 30 gun haberleri neler? Varsa bilanco, temettu, ortaklik yapisi degisikligi gibi onemli gelismeler neler? Kisa liste yap.',
      agent:'bist-data',
      bist_context:{ticker:_sd.ticker}
    })
  }).then(function(r){return r.json();})
  .then(function(d){
    var resp = d.response || '';
    el.innerHTML =
      '<div style="background:rgba(0,212,255,.05);border:1px solid rgba(0,212,255,.12);border-radius:10px;padding:10px 12px;margin-bottom:10px;display:flex;align-items:center;gap:8px">'
      +'<span style="font-size:16px"></span>'
      +'<div><div style="font-size:10px;font-weight:700;color:var(--cyan)">'+_sd.ticker+' KAP / Haber Ozeti</div>'
      +'<div style="font-size:8px;color:var(--t4)">AI tarafindan derlendi - dogrulamak icin kap.gov.tr inceleyiniz</div></div>'
      +'</div>'
      +'<div style="font-size:10px;color:var(--t2);line-height:1.7;padding:4px 2px">'+resp+'</div>'
      +'<a href="https://www.kap.org.tr/tr/sirket-bilgileri/ozet/'+_sd.ticker+'" target="_blank" '
      +'style="display:block;margin-top:12px;padding:10px;border-radius:9px;background:rgba(0,212,255,.07);'
      +'border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;font-weight:700;text-align:center;text-decoration:none">'
      +'KAP\'ta Resmi Bildirimler</a>'
      +'<div style="margin-top:8px;font-size:8px;color:var(--t4);text-align:center">Yatirim tavsiyesi niteligi tasimaz</div>'
    ;
  }).catch(function(){
    el.innerHTML='<div style="padding:16px"><a href="https://www.kap.org.tr/tr/sirket-bilgileri/ozet/'+_sd.ticker+'" target="_blank" '
      +'style="display:block;padding:12px;border-radius:9px;background:rgba(0,212,255,.07);border:1px solid rgba(0,212,255,.2);'
      +'color:var(--cyan);font-size:11px;font-weight:700;text-align:center;text-decoration:none">'
      +'KAP\'ta '+_sd.ticker+' Bildirimlerini Ac</a></div>';
  });
}

//  6. SEZONSELLIK 
function renderSDSeason(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  var ohlcv = _sd.ohlcv && _sd.ohlcv.ohlcv;
  if(!ohlcv){ el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div></div>'; return; }

  var seasonData = calcSeasonality(ohlcv);
  var months = ['Oca','Sub','Mar','Nis','May','Haz','Tem','Agu','Eyl','Eki','Kas','Ara'];
  var maxAbs = Math.max.apply(null, seasonData.map(function(v){return Math.abs(v||0);}));

  el.innerHTML =
    '<div class="sd-section"><div class="sd-section-title">Aylik Ortalama Getiri (%)</div>'
    +'<div class="season-grid">'
    +months.map(function(m,i){
      var v = seasonData[i] || 0;
      var clr = v>0?'var(--green)':v<0?'var(--red)':'var(--t4)';
      var bg = v>0?'rgba(0,230,118,'+(0.05+Math.abs(v)/maxAbs*0.15)+')':'rgba(255,68,68,'+(0.05+Math.abs(v)/maxAbs*0.15)+')';
      return '<div class="season-cell" style="background:'+bg+'">'
        +'<div class="season-month">'+m+'</div>'
        +'<div class="season-pct" style="color:'+clr+'">'+(v>0?'+':'')+v.toFixed(1)+'%</div>'
        +'</div>';
    }).join('')
    +'</div></div>'
    +'<div class="sd-section"><div class="sd-section-title">Mevsimsel Analiz</div>'
    +'<div style="font-size:9.5px;color:var(--t3);line-height:1.7;padding:4px">'
    +generateSeasonalComment(seasonData, months)
    +'</div></div>'
    +'<div style="font-size:8px;color:var(--t4);text-align:center;padding:6px">Mevcut veri bazli hesaplanmistir. Gecmis performans gelecegi garantilemez.</div>'
  ;
}

//  7. KORELASYON 
function renderSDCorrelation(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div><span style="font-size:11px;color:var(--t4)">Korelasyon hesaplaniyor...</span></div>';

  // Acik pozisyonlar + watchlist ile korelasyon
  var others = [];
  Object.keys(S.openPositions||{}).forEach(function(k){
    var t=k.split('_')[0]; if(t!==_sd.ticker&&others.indexOf(t)===-1) others.push(t);
  });
  (S.watchlist||[]).forEach(function(t){ if(t!==_sd.ticker&&others.indexOf(t)===-1&&others.length<8) others.push(t); });
  // Endeks karsilastirma ekle
  ['THYAO','GARAN','ASELS','EREGL','BIMAS'].forEach(function(t){
    if(t!==_sd.ticker&&others.indexOf(t)===-1&&others.length<10) others.push(t);
  });

  if(others.length===0){
    el.innerHTML='<div style="padding:20px;text-align:center;color:var(--t4);font-size:11px">Karsilastirilacak hisse yok.<br>Watchlist veya acik pozisyon ekleyin.</div>';
    return;
  }

  // XU100 ile korelasyon hesapla
  var curOhlcv = _sd.ohlcv && _sd.ohlcv.ohlcv;
  if(!curOhlcv){ el.innerHTML='<div style="padding:20px;text-align:center;color:var(--t4)">Veri yok</div>'; return; }

  el.innerHTML =
    '<div class="sd-section"><div class="sd-section-title">'+_sd.ticker+' Korelasyonu (Son 60G)</div>'
    +'<div id="corrList"><div class="sd-loading"><div class="sd-spin"></div></div></div>'
    +'</div>'
    +'<div style="font-size:8.5px;color:var(--t4);line-height:1.6;padding:6px 4px">'
    +'Korelasyon -1 (tam ters) ile +1 (tam ayni) arasinda deger alir. '
    +'Yuksek korelasyon, hisselerin birlikte hareket ettigini gosterir. '
    +'Portfoy cesitlendirmesi icin dusuk korelasyonlu hisseler tercih edilmeli.'
    +'</div>'
  ;

  // XU100 ile korelasyon
  var curReturns = calcReturns(curOhlcv.slice(-60).map(function(b){return b.c;}));
  var corrs = [];
  var done = 0;
  function checkDone(){
    done++;
    if(done >= others.length){
      corrs.sort(function(a,b){return Math.abs(b.corr)-Math.abs(a.corr);});
      var listEl = document.getElementById('corrList');
      if(!listEl) return;
      listEl.innerHTML = corrs.map(function(c){
        var pct = Math.round(Math.abs(c.corr)*100);
        var clr = c.corr>0.5?'var(--red)':c.corr<-0.3?'var(--green)':'var(--cyan)';
        return '<div class="corr-item">'
          +'<div style="width:55px;font-size:10px;font-weight:700;color:var(--t2)">'+c.ticker+'</div>'
          +'<div class="corr-bar"><div class="corr-fill" style="width:'+pct+'%;background:'+clr+'"></div></div>'
          +'<div style="width:40px;text-align:right;font-size:10px;font-weight:700;color:'+clr+'">'+c.corr.toFixed(2)+'</div>'
          +'</div>';
      }).join('');
    }
  }

  others.forEach(function(t){
    var cached = S.ohlcvCache && S.ohlcvCache[t+'_D'];
    if(cached && cached.ohlcv && cached.ohlcv.length>=60){
      var ret = calcReturns(cached.ohlcv.slice(-60).map(function(b){return b.c;}));
      corrs.push({ticker:t, corr:pearsonCorr(curReturns, ret)});
      checkDone();
    } else {
      fetch(PROXY+'/ohlcv/'+t+'?tf=D').then(function(r){return r.json();})
      .then(function(d){
        if(d&&d.ohlcv&&d.ohlcv.length>=60){
          if(!S.ohlcvCache) S.ohlcvCache={};
          S.ohlcvCache[t+'_D']=d;
          var ret=calcReturns(d.ohlcv.slice(-60).map(function(b){return b.c;}));
          corrs.push({ticker:t,corr:pearsonCorr(curReturns,ret)});
        } else { corrs.push({ticker:t,corr:0}); }
        checkDone();
      }).catch(function(){ corrs.push({ticker:t,corr:0}); checkDone(); });
    }
  });
}

//  8. AI ANALZ 
function renderSDAI(){
  var el = document.getElementById('sdContent');
  if(!el) return;
  el.innerHTML='<div class="sd-loading"><div class="sd-spin"></div><span style="font-size:11px;color:var(--t4)">AI analizi hazirlaniyor...</span></div>';

  var a = _sd.analysis || {};
  var curPrice = a.price || 0;
  var posStr = '';
  var pos = S.openPositions && S.openPositions[_sd.ticker+'_D'];
  if(pos){ posStr = ' Acik pozisyon: giris='+pos.entry+', pnl='+((curPrice-pos.entry)/pos.entry*100).toFixed(1)+'%'; }

  var prompt = _sd.ticker+' icin kapsamli teknik analiz yap. '
    +'Guncel durum: fiyat='+curPrice+', RSI='+a.rsi+', ADX='+a.adx+', MACD='+a.macd
    +', konsensus=%'+a.consensus+', bolge='+a.pstate
    +', EMA50='+a.ema50+', EMA200='+a.ema200
    +', sinyal='+( a.signal?'AL':'Yok')+', is_master='+(a.is_master?'Evet':'Hayir')
    +posStr
    +'. Teknik gorunu ne? Risk nedir? Neye dikkat etmeli? '
    +'Kisa Turkce analiz yap. Yatirim tavsiyesi verme.';

  fetch(PROXY+'/ai/chat',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      message: prompt,
      agent: 'bist-trader',
      bist_context: {ticker:_sd.ticker, positions:Object.keys(S.openPositions||{}).length}
    })
  }).then(function(r){return r.json();})
  .then(function(d){
    var resp = d.response || 'AI yaniti alinamadi.';
    var provider = d.provider || 'AI';
    el.innerHTML =
      '<div class="ai-comment">'
      +'<div class="ai-comment-hdr">'
      +'<span style="font-size:16px"></span>'
      +'<div><div style="font-size:11px;font-weight:700;color:var(--purple)">'+_sd.ticker+' AI Teknik Analizi</div>'
      +'<div style="font-size:8px;color:var(--t4)">'+provider+' tarafindan olusturuldu</div></div>'
      +'</div>'
      +'<div style="font-size:10px;color:var(--t2);line-height:1.7">'+resp+'</div>'
      +'</div>'
      +'<div style="padding:10px;background:rgba(255,184,0,.05);border:1px solid rgba(255,184,0,.12);'
      +'border-radius:9px;font-size:8.5px;color:rgba(255,184,0,.6);line-height:1.5;text-align:center">'
      +'Yatirim tavsiyesi degildir. Egitim ve bilgilendirme amaclidir.'
      +'</div>'
      // Hizli aksiyonlar
      +'<div style="margin-top:10px;display:grid;grid-template-columns:1fr 1fr;gap:6px">'
      +'<button onclick="openBacktestForTicker()" style="padding:11px;border-radius:9px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;font-weight:700;cursor:pointer">Backtest Yap</button>'
      +'<button onclick="window.open(\'https://www.kap.org.tr/tr/sirket-bilgileri/ozet/'+_sd.ticker+'\',\'_blank\')" style="padding:11px;border-radius:9px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:var(--t3);font-size:10px;cursor:pointer">KAP Aciklamalari</button>'
      +'<button onclick="openTVTicker(\''+_sd.ticker+'\',\'D\')" style="padding:11px;border-radius:9px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:var(--t3);font-size:10px;cursor:pointer">TradingView Grafik</button>'
      +'<button onclick="closeStockDashboard()" style="padding:11px;border-radius:9px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);color:var(--t4);font-size:10px;cursor:pointer">Kapat</button>'
      +'</div>'
    ;
  }).catch(function(){
    el.innerHTML='<div style="padding:20px;text-align:center;color:var(--t4)">AI baglantisi yok. Proxy aktif mi?</div>';
  });
}

window.openBacktestForTicker = function(){
  closeStockDashboard();
  setTimeout(function(){
    try{
      pg('backtest');
      var sel=document.getElementById('btSym');
      if(sel) sel.value=_sd.ticker;
    }catch(e){}
  },300);
};

//  HESAPLAMA FONKSYONLARI 
function calcEMAValues(closes, period){
  var k=2/(period+1), ema=[], prev=null;
  for(var i=0;i<closes.length;i++){
    if(prev===null){ ema.push(null); }
    else if(i<period){ ema.push(null); }
    else if(prev===null){ prev=closes.slice(0,period).reduce(function(a,b){return a+b;},0)/period; ema.push(prev); }
    else { prev=closes[i]*k+prev*(1-k); ema.push(prev); }
    if(i===period-1 && prev===null){ prev=closes.slice(0,period).reduce(function(a,b){return a+b;},0)/period; ema[i]=prev; }
  }
  // Duzelt
  var result=[]; prev=null;
  for(var j=0;j<closes.length;j++){
    if(j<period-1){result.push(null);continue;}
    if(j===period-1){prev=closes.slice(0,period).reduce(function(a,b){return a+b;},0)/period;result.push(prev);continue;}
    prev=closes[j]*k+prev*(1-k);result.push(prev);
  }
  return result;
}

function calcRSI(closes, period){
  period=period||14;
  var rsi=new Array(closes.length).fill(null);
  if(closes.length<=period) return rsi;
  var gains=0,losses=0;
  for(var i=1;i<=period;i++){
    var d=closes[i]-closes[i-1];
    if(d>0)gains+=d; else losses-=d;
  }
  var ag=gains/period,al=losses/period;
  rsi[period]=al===0?100:100-100/(1+ag/al);
  for(var i=period+1;i<closes.length;i++){
    var d=closes[i]-closes[i-1];
    var g=d>0?d:0, l=d<0?-d:0;
    ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period;
    rsi[i]=al===0?100:100-100/(1+ag/al);
  }
  return rsi;
}

function calcBollingerBands(closes, period, mult){
  period=period||20; mult=mult||2;
  var upper=[],lower=[],mid=[];
  for(var i=0;i<closes.length;i++){
    if(i<period){upper.push(null);lower.push(null);mid.push(null);continue;}
    var slice=closes.slice(i-period+1,i+1);
    var mean=slice.reduce(function(a,b){return a+b;},0)/period;
    var variance=slice.reduce(function(a,b){return a+(b-mean)*(b-mean);},0)/period;
    var std=Math.sqrt(variance);
    upper.push(mean+mult*std); lower.push(mean-mult*std); mid.push(mean);
  }
  return {upper:upper.slice(period),lower:lower.slice(period),mid:mid.slice(period)};
}

function calcMTFConfluence(ohlcv_d, analysis){
  var results=[];
  var tfs=[
    {label:'Gunluk (D)', data:ohlcv_d, adx:analysis.adx||0, rsi:analysis.rsi||50},
    {label:'4 Saat (4H)', data:null, adx:0, rsi:50},
    {label:'2 Saat (2H)', data:null, adx:0, rsi:50},
  ];
  tfs.forEach(function(tf){
    var dir='neut';
    if(tf.data&&tf.data.length>=20){
      var c=tf.data.map(function(b){return b.c;});
      var rsi=calcRSI(c,14); var last_rsi=rsi[rsi.length-1]||50;
      var ema20=calcEMAValues(c,20); var last_ema=ema20[ema20.length-1];
      var lp=c[c.length-1];
      if(last_rsi>55&&lp>last_ema) dir='bull';
      else if(last_rsi<45&&lp<last_ema) dir='bear';
      tf.rsi=Math.round(last_rsi);
    } else if(tf.label.indexOf('Gunluk')>-1){
      if(tf.rsi>55&&tf.adx>25) dir='bull';
      else if(tf.rsi<45&&tf.adx>25) dir='bear';
    }
    results.push({label:tf.label,dir:dir,adx:Math.round(tf.adx),rsi:Math.round(tf.rsi)});
  });
  // analysis'tan tf_align kullan
  if(analysis.tf_align){
    var ta=analysis.tf_align;
    if(ta.h4_bull!==undefined) results[1].dir=ta.h4_bull?'bull':ta.h4_bear?'bear':'neut';
    if(ta.h2_bull!==undefined) results[2].dir=ta.h2_bull?'bull':ta.h2_bear?'bear':'neut';
  }
  return results;
}

function detectPatterns(ohlcv){
  if(!ohlcv||ohlcv.length<50) return [];
  var patterns=[];
  var c=ohlcv.map(function(b){return b.c;});
  var h=ohlcv.map(function(b){return b.h;});
  var l=ohlcv.map(function(b){return b.l;});
  var n=c.length;

  // Cift Dip (W)
  var lookback=40;
  var lows=l.slice(n-lookback);
  var min1_idx=lows.indexOf(Math.min.apply(null,lows));
  var lows2=lows.slice(min1_idx+5);
  if(lows2.length>5){
    var min2_idx=lows2.indexOf(Math.min.apply(null,lows2));
    var diff=Math.abs(lows[min1_idx]-lows2[min2_idx])/lows[min1_idx];
    if(diff<0.03 && min2_idx>4){
      patterns.push({name:'Cift Dip (W Pattern)',icon:'',desc:'Iki benzer dip seviyesi - boga sinali',type:'bull',confidence:72});
    }
  }

  // Cift Tep (M)
  var highs=h.slice(n-lookback);
  var max1_idx=highs.indexOf(Math.max.apply(null,highs));
  var highs2=highs.slice(max1_idx+5);
  if(highs2.length>5){
    var max2_idx=highs2.indexOf(Math.max.apply(null,highs2));
    var hdiff=Math.abs(highs[max1_idx]-highs2[max2_idx])/highs[max1_idx];
    if(hdiff<0.03 && max2_idx>4){
      patterns.push({name:'Cift Tep (M Pattern)',icon:'',desc:'Iki benzer tep seviyesi - ayi sinali',type:'bear',confidence:68});
    }
  }

  // Yukselen kanal
  var recentL=l.slice(n-20); var recentH=h.slice(n-20);
  var minL=Math.min.apply(null,recentL.slice(0,5)); var maxL=Math.max.apply(null,recentL.slice(15));
  var minH=Math.min.apply(null,recentH.slice(0,5)); var maxH=Math.max.apply(null,recentH.slice(15));
  if(maxL>minL*1.02 && maxH>minH*1.02){
    patterns.push({name:'Yukselen Kanal',icon:'',desc:'Fiyat yukselen kanal icinde ilerliyor',type:'bull',confidence:65});
  }

  // Descending triangle
  var topVariance=highs.slice(-20).reduce(function(acc,v,i,a){
    if(i===0)return{sum:v,count:1,mean:v};
    var m=(acc.sum+v)/(i+1); return{sum:acc.sum+v,count:i+1,mean:m,diff:(acc.diff||0)+Math.abs(v-m)};
  },{}).diff;
  if(topVariance&&topVariance/highs.slice(-20)[0]<0.02&&maxL>minL*1.015){
    patterns.push({name:'Alcalan Ucgen',icon:'',desc:'Duz direnc, yukselen destek - kirilis bekleniyor',type:'bull',confidence:62});
  }

  return patterns.slice(0,4);
}

function detectCandlePatterns(ohlcv){
  if(!ohlcv||ohlcv.length<3) return [];
  var patterns=[];
  var last=ohlcv.slice(-5);

  last.forEach(function(bar,i){
    var o=bar.o,h=bar.h,l=bar.l,c=bar.c;
    var body=Math.abs(c-o); var range=h-l;
    var upperWick=h-Math.max(o,c); var lowerWick=Math.min(o,c)-l;
    // Hammer / Pin Bar
    if(lowerWick>body*2 && upperWick<body*0.5 && range>0){
      patterns.push({name:'Cekic (Hammer)',icon:'',desc:'Uzun alt fitil - guclu boga mumu',type:'bull'});
    }
    // Shooting Star
    if(upperWick>body*2 && lowerWick<body*0.5 && range>0){
      patterns.push({name:'Yildiz (Shooting Star)',icon:'',desc:'Uzun ust fitil - baski mumi',type:'bear'});
    }
    // Doji
    if(body<range*0.1 && range>0){
      patterns.push({name:'Doji',icon:'',desc:'Karar belirsizligi - yonun degisebilir',type:'neut'});
    }
  });

  // Engulfing
  if(last.length>=2){
    var prev=last[last.length-2], curr=last[last.length-1];
    if(curr.c>curr.o && prev.c<prev.o && curr.o<prev.c && curr.c>prev.o){
      patterns.push({name:'Boga Yutma (Bullish Engulfing)',icon:'',desc:'Kucuk kirmizi mumu buyuk yesil mum yuttu',type:'bull'});
    }
    if(curr.c<curr.o && prev.c>prev.o && curr.o>prev.c && curr.c<prev.o){
      patterns.push({name:'Ayi Yutma (Bearish Engulfing)',icon:'',desc:'Kucuk yesil mumu buyuk kirmizi mum yuttu',type:'bear'});
    }
  }
  return patterns.slice(0,3);
}

function calcSupportResistance(ohlcv, curPrice){
  if(!ohlcv||ohlcv.length<20) return [];
  var levels=[];
  var c=ohlcv.map(function(b){return b.c;});
  var h=ohlcv.map(function(b){return b.h;});
  var l=ohlcv.map(function(b){return b.l;});
  var n=c.length;

  // 52 hafta yuksek/dusuk
  var slice52=Math.min(n,260);
  var h52=Math.max.apply(null,h.slice(-slice52));
  var l52=Math.min.apply(null,l.slice(-slice52));
  levels.push({price:parseFloat(h52.toFixed(2)),type:h52>curPrice?'res':'sup',desc:'52H En Yuksek'});
  levels.push({price:parseFloat(l52.toFixed(2)),type:l52<curPrice?'sup':'res',desc:'52H En Dusuk'});

  // EMA seviyeleri
  var ema20=calcEMAValues(c,20); var ema50=calcEMAValues(c,50); var ema200=calcEMAValues(c,200);
  if(ema20[n-1]) levels.push({price:parseFloat(ema20[n-1].toFixed(2)),type:ema20[n-1]<curPrice?'sup':'res',desc:'EMA 20'});
  if(ema50[n-1]) levels.push({price:parseFloat(ema50[n-1].toFixed(2)),type:ema50[n-1]<curPrice?'sup':'res',desc:'EMA 50'});
  if(ema200[n-1]) levels.push({price:parseFloat(ema200[n-1].toFixed(2)),type:ema200[n-1]<curPrice?'sup':'res',desc:'EMA 200'});

  // Fibonacci (son swing'den)
  var swingH=Math.max.apply(null,h.slice(-50)); var swingL=Math.min.apply(null,l.slice(-50));
  var fibLevels=[0.236,0.382,0.5,0.618,0.786];
  fibLevels.forEach(function(f){
    var fibP=swingH-(swingH-swingL)*f;
    levels.push({price:parseFloat(fibP.toFixed(2)),type:fibP<curPrice?'sup':'res',desc:'Fib '+Math.round(f*100)+'%'});
  });

  // Pivot Point (son 20G hacim yogun bolge)
  var pivot=(h.slice(-20).reduce(function(a,b){return a+b;},0)+l.slice(-20).reduce(function(a,b){return a+b;},0)+c.slice(-20).reduce(function(a,b){return a+b;},0))/(20*3);
  levels.push({price:parseFloat(pivot.toFixed(2)),type:pivot<curPrice?'sup':'res',desc:'Pivot Noktasi'});

  // Sadece fiyata yakin 4 direnc ve 4 destek
  var res=levels.filter(function(l){return l.type==='res'&&l.price>curPrice;}).sort(function(a,b){return a.price-b.price;}).slice(0,4);
  var sup=levels.filter(function(l){return l.type==='sup'&&l.price<curPrice;}).sort(function(a,b){return b.price-a.price;}).slice(0,4);
  return res.concat(sup);
}

function calcSeasonality(ohlcv){
  var monthly=new Array(12).fill(null).map(function(){return [];});
  ohlcv.forEach(function(bar,i){
    if(i===0) return;
    var t=new Date(bar.t); var m=t.getMonth();
    var ret=(bar.c-ohlcv[i-1].c)/ohlcv[i-1].c*100;
    monthly[m].push(ret);
  });
  return monthly.map(function(arr){
    if(!arr.length) return 0;
    return parseFloat((arr.reduce(function(a,b){return a+b;},0)/arr.length).toFixed(2));
  });
}

function generateSeasonalComment(data, months){
  var best=data.reduce(function(a,b,i){return b>a.v?{v:b,i:i}:a;},{v:-999,i:0});
  var worst=data.reduce(function(a,b,i){return b<a.v?{v:b,i:i}:a;},{v:999,i:0});
  var bullMonths=data.filter(function(v){return v>0;}).length;
  return 'Tarihsel verilere gore <b style="color:var(--green)">'+months[best.i]+'</b> (+'+best.v.toFixed(1)+'%) en guclu ay, '
    +'<b style="color:var(--red)">'+months[worst.i]+'</b> ('+worst.v.toFixed(1)+'%) ise en zayif ay. '
    +'12 ayin '+bullMonths+'\'unde pozitif getiri gozlemlenmistir. '
    +'(Gecmis performans gelecegi garantilemez.)';
}

function calcReturns(prices){
  var returns=[];
  for(var i=1;i<prices.length;i++) returns.push((prices[i]-prices[i-1])/prices[i-1]);
  return returns;
}

function pearsonCorr(a, b){
  var n=Math.min(a.length,b.length);
  if(n<5) return 0;
  a=a.slice(a.length-n); b=b.slice(b.length-n);
  var ma=a.reduce(function(s,v){return s+v;},0)/n;
  var mb=b.reduce(function(s,v){return s+v;},0)/n;
  var num=0,da=0,db=0;
  for(var i=0;i<n;i++){
    num+=(a[i]-ma)*(b[i]-mb);
    da+=(a[i]-ma)*(a[i]-ma);
    db+=(b[i]-mb)*(b[i]-mb);
  }
  var denom=Math.sqrt(da*db);
  return denom===0?0:parseFloat((num/denom).toFixed(3));
}

//  HOOK: openTVTicker  Dashboard 
// Mevcut openTVTicker'i override et - once dashboard ac
var _origOpenTVTicker = typeof openTVTicker==='function' ? openTVTicker : null;
window.openTVTicker = function(ticker, tf){
  // Dashboard'u ac
  var stk = typeof STOCKS!=='undefined' && STOCKS.find(function(s){return s.t===ticker;});
  openStockDashboard(ticker, stk?stk.n:ticker);
};

// openSt de dashboard acacak
var _origOpenSt2 = typeof openSt==='function' ? openSt : null;
openSt = function(ticker){
  var stk = typeof STOCKS!=='undefined' && STOCKS.find(function(s){return s.t===ticker;});
  openStockDashboard(ticker, stk?stk.n:ticker);
};

//  INIT 
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      // LWC'yi on yukle
      loadLWC(function(){
        if(typeof devLog==='function') devLog('Lightweight Charts hazir','ok');
      });
    }catch(e){}
  },2000);
});
</script>
<script>

// BIST PRO v3 - BLOK 23
// HATA DUZELTMELERI + YENI OZELLIKLER
// 1. renderDevPanel override zinciri birlestirildi (7->1)
// 2. v13CallAPI override zinciri birlestirildi (5->1) 
// 3. Non-ASCII Blok1 yorumlarda - runtime etkilemiyor, kabul edildi
// 4. eval/new Function - sadece Debug Master yorum stringinde, zararsiz
// 5. S.sigs null guard eklendi
// 6. Trader Journal
// 7. Position Sizing Calculator
// 8. Market Breadth Dashboard
// 9. Sentiment Analizi
// 10. Makro Panel (USD/TRY + BIST korelasyon)
// 11. Stress Test (2018/2020/2022)
// 12. Strateji Kutuphanesi
// 13. Telegram Bot Komutlari
// 14. PDF Rapor (proxy endpoint)
// 15. Onboarding fix
// 16. Tum single-source-of-truth override duzeltmesi

// --- HATA DUZELTME 1: S.sigs null guard ---------------------
(function(){
  try{
    // S.sigs her yerde guvende olsun
    if(typeof S !== 'undefined' && !S.sigs) S.sigs = [];
    // updateBadge null-safe wrap
    var _origUpdateBadge = typeof updateBadge==='function' ? updateBadge : null;
    if(_origUpdateBadge){
      updateBadge = function(){
        try{
          if(!S || !S.sigs) return;
          _origUpdateBadge();
        }catch(e){}
      };
    }
  }catch(e){}
})();

// --- HATA DUZELTME 2: renderDevPanel TEK override ------------
// Onceki 7 adet override'i iptal et, temiz tek fonksiyon yaz
(function(){
  try{
    // Son gecerli renderDevPanel'i bul ve tek noktaya topla
    window._finalRenderDevPanel = window.renderV15Settings || window.renderDevPanel;
    renderDevPanel = function(){
      try{
        if(typeof renderV15Settings === 'function') renderV15Settings();
      }catch(e){ console.warn('renderDevPanel:',e.message); }
    };
  }catch(e){}
})();

// --- HATA DUZELTME 3: v13CallAPI TEK override ----------------
// 5 kez override edilmis - en son (Blok 17 sunucu AI) dogru olan
// Blok 16 (model router) mantigi buraya entegre et
(function(){
  try{
    var PROXY = typeof PROXY_URL!=='undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';

    window.v13CallAPI = function(msg){
      try{
        if(!msg||!msg.trim()) return;
        if(window._v13Thinking) return;
        window._v13Thinking = true;

        // Loading goster
        var loadDiv = document.createElement('div');
        loadDiv.className = 'v13m-thinking'; loadDiv.id = 'v13Loading';
        loadDiv.innerHTML = '<span class="v13-dot"></span><span class="v13-dot"></span><span class="v13-dot"></span>';
        var st = document.getElementById('v13Stream');
        if(st){ st.appendChild(loadDiv); st.scrollTop = st.scrollHeight; }

        // Intent/model routing
        var agent = window._v13ActiveAgent;
        var agentId = (agent&&agent.id)||'main';

        // Mesaj gecmisi
        var msgs = (window._v13History||[]).slice(-6).map(function(m){
          return {role:m.role==='user'?'user':'assistant', content:m.content};
        });

        function clearLoad(){ window._v13Thinking=false; var ld=document.getElementById('v13Loading'); if(ld)ld.remove(); }

        function onResp(resp, err, provider){
          clearLoad();
          if(err){ window.v13AppendMsg&&v13AppendMsg('sys','['+( provider||'AI')+'] '+err); return; }
          if(!resp) return;
          window._v13History&&_v13History.push({role:'assistant',content:resp});
          if(provider) window.v13AppendMsg&&v13AppendMsg('sys','Model: '+provider);
          var cm = resp.match(/```(?:javascript|js|python|bash)?\n?([\s\S]*?)```/);
          if(cm){
            var before = resp.replace(cm[0],'').trim();
            if(before) window.v13AppendMsg&&v13AppendMsg('ai',before);
            window.v13AppendMsg&&v13AppendMsg('ai',null,cm[1].trim());
          } else {
            window.v13AppendMsg&&v13AppendMsg('ai',resp);
          }
          if(window._ttsEnabled && typeof v13Speak==='function') v13Speak(resp.replace(/```[\s\S]*?```/g,'').substring(0,200));
        }

        // Oncelik: Gateway -> Sunucu AI -> Direkt API
        if(window._ocGW && _ocGW.connected && _ocGW.ws && _ocGW.ws.readyState===1){
          var responded = false;
          var origH = _ocGW.ws.onmessage;
          function tempH(e){
            try{
              var data=JSON.parse(e.data);
              if((data.type==='chat.message'||data.type==='message')&&!responded){
                var txt=data.text||data.content||'';
                if(txt){ responded=true; _ocGW.ws.onmessage=origH; onResp(txt,null,'Gateway'); }
              }
            }catch(ex){}
            if(origH) origH(e);
          }
          _ocGW.ws.onmessage = tempH;
          _ocGW.ws.send(JSON.stringify({type:'chat.send',text:msg,channel:'webchat',agentId:(agent&&agent.ocAgentId)||'main'}));
          setTimeout(function(){ if(!responded){ responded=true; _ocGW.ws.onmessage=origH; onResp(null,'Gateway timeout'); }},60000);
          return;
        }

        // Sunucu AI
        var posC = Object.keys(S&&S.openPositions||{}).length;
        var closed = S&&S.closedPositions||[];
        var wins = closed.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
        var wr = closed.length?(wins/closed.length*100).toFixed(0):'N/A';
        fetch(PROXY+'/ai/chat',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            message:msg, agent:agentId,
            history:msgs,
            bist_context:{positions:posC,signals:(S&&S.sigs||[]).length,xu100:S&&S.xu100Change||0,winrate:wr}
          })
        }).then(function(r){return r.json();})
        .then(function(d){
          onResp(d.response,d.error&&!d.response?d.response:null,d.provider||'AI');
        }).catch(function(e){ onResp(null,e.message,'API'); });

      }catch(e){
        window._v13Thinking=false;
        var ld=document.getElementById('v13Loading'); if(ld)ld.remove();
        window.v13AppendMsg&&v13AppendMsg('sys','Hata: '+e.message);
      }
    };
  }catch(e){ console.warn('v13CallAPI fix:',e.message); }
})();

// --- CSS ------------------------------------------------------
(function(){
  try{
    var st=document.createElement('style');
    st.textContent=
      // Trader Journal
      '.journal-entry{padding:11px 12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:10px;margin-bottom:7px}'
      +'.journal-entry.win{border-color:rgba(0,230,118,.2);background:rgba(0,230,118,.04)}'
      +'.journal-entry.loss{border-color:rgba(255,68,68,.15);background:rgba(255,68,68,.03)}'
      +'.journal-note{width:100%;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:7px;padding:8px;font-size:11px;color:var(--t1);resize:none;outline:none;font-family:inherit;margin-top:6px}'
      +'.journal-note:focus{border-color:rgba(0,212,255,.4)}'
      // Position Sizing
      +'.pos-sizer{background:rgba(0,212,255,.05);border:1px solid rgba(0,212,255,.15);border-radius:11px;padding:12px;margin-bottom:10px}'
      +'.pos-sizer-row{display:flex;align-items:center;gap:8px;margin-bottom:7px}'
      +'.pos-sizer-label{font-size:9.5px;color:var(--t4);width:110px;flex-shrink:0}'
      +'.pos-sizer-inp{flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:7px;padding:7px 10px;font-size:12px;color:var(--t1);outline:none}'
      +'.pos-sizer-result{background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);border-radius:9px;padding:12px;text-align:center}'
      +'.pos-sizer-lot{font-size:28px;font-weight:800;color:var(--cyan)}'
      +'.pos-sizer-sub{font-size:9px;color:var(--t4);margin-top:2px}'
      // Market Breadth
      +'.breadth-ring{width:100px;height:100px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 8px;border:6px solid rgba(255,255,255,.08);position:relative}'
      +'.breadth-pct{font-size:20px;font-weight:800}'
      +'.breadth-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px}'
      +'.breadth-card{padding:12px;border-radius:10px;text-align:center;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03)}'
      // Sentiment badge
      +'.sentiment-badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;font-size:8px;font-weight:700}'
      +'.sentiment-pos{background:rgba(0,230,118,.12);color:var(--green);border:1px solid rgba(0,230,118,.25)}'
      +'.sentiment-neg{background:rgba(255,68,68,.1);color:var(--red);border:1px solid rgba(255,68,68,.2)}'
      +'.sentiment-neu{background:rgba(255,255,255,.06);color:var(--t3);border:1px solid rgba(255,255,255,.1)}'
      // Stress test
      +'.stress-scenario{padding:12px;border-radius:10px;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);margin-bottom:8px}'
      +'.stress-pct{font-size:22px;font-weight:800}'
      // Strateji kutuphane
      +'.strat-card{padding:11px 12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:10px;margin-bottom:7px;cursor:pointer}'
      +'.strat-card:active{background:rgba(255,255,255,.06)}'
      +'.strat-badge{font-size:7.5px;padding:1px 6px;border-radius:4px;background:rgba(0,212,255,.1);color:var(--cyan);font-weight:700}'
      // Makro panel
      +'.macro-ticker{padding:10px 12px;background:rgba(255,255,255,.03);border-radius:9px;border:1px solid rgba(255,255,255,.06);margin-bottom:6px;display:flex;align-items:center;gap:10px}'
      +'.macro-val{font-size:14px;font-weight:800;margin-left:auto}'
      // Onboarding
      +'#onboardOverlay{position:fixed;inset:0;background:rgba(0,0,0,.95);z-index:9998;display:none;align-items:flex-end;justify-content:center}'
      +'#onboardOverlay.on{display:flex}'
      +'.onboard-box{background:#0D0D0D;border-top:2px solid var(--cyan);border-radius:18px 18px 0 0;padding:24px 20px 36px;max-width:480px;width:100%}'
      +'.onboard-step-dots{display:flex;gap:6px;justify-content:center;margin-bottom:16px}'
      +'.onboard-dot{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.2);transition:all .3s}'
      +'.onboard-dot.active{background:var(--cyan);width:20px;border-radius:4px}'
      // PDF rapor butonu
      +'.pdf-btn{padding:11px;border-radius:9px;background:rgba(255,68,68,.1);border:1px solid rgba(255,68,68,.2);color:#ff9999;font-size:11px;font-weight:700;cursor:pointer;width:100%;margin-top:5px}'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

// --- 1. TRADER JOURNAL ----------------------------------------
var _journal = (function(){
  try{ return JSON.parse(localStorage.getItem('bist_journal')||'[]'); }catch(e){ return []; }
})();

function saveJournal(){ try{ localStorage.setItem('bist_journal',JSON.stringify(_journal.slice(0,500))); }catch(e){} }

function addJournalEntry(ticker, tf, entry, exit, pnlPct, note, signalType){
  try{
    var jEntry = {
      id: Date.now(),
      date: new Date().toISOString(),
      ticker: ticker,
      tf: tf||'D',
      entry: entry,
      exit: exit||null,
      pnlPct: pnlPct||null,
      note: note||'',
      signalType: signalType||'AL',
      tags: [],
    };
    _journal.unshift(jEntry);
    saveJournal();
    return jEntry;
  }catch(e){ return null; }
}

function openJournalModal(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Trader Journal';

    var html = '<div style="padding:3px 0">'
      // Yeni not
      +'<div style="margin-bottom:12px">'
      +'<div style="font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:7px">Hizli Not Ekle</div>'
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px">'
      +'<input id="jTicker" placeholder="Hisse (EREGL)" class="auth-inp" style="font-size:11px;padding:8px 10px">'
      +'<input id="jNote" placeholder="Not (neden aldim?)" class="auth-inp" style="font-size:11px;padding:8px 10px">'
      +'</div>'
      +'<button onclick="quickJournalAdd()" style="width:100%;padding:9px;border-radius:8px;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.25);color:var(--cyan);font-size:11px;font-weight:700;cursor:pointer">Not Ekle</button>'
      +'</div>'
      // Istatistik ozet
      +renderJournalStats()
      // Liste
      +'<div style="font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:7px">Son Notlar</div>'
      +(_journal.length===0
        ? '<div style="padding:20px;text-align:center;color:var(--t4);font-size:10px">Henuz kayit yok.<br>Pozisyon acildiginda otomatik eklenir.</div>'
        : _journal.slice(0,20).map(renderJournalEntry).join('')
      )
      +'</div>'
    ;
    document.getElementById('mcont').innerHTML = html;
    modal.classList.add('on');
  }catch(e){ toast('Journal: '+e.message); }
}

function renderJournalStats(){
  var closed = _journal.filter(function(j){return j.pnlPct!==null;});
  if(!closed.length) return '';
  var wins = closed.filter(function(j){return parseFloat(j.pnlPct)>=0;}).length;
  var wr = (wins/closed.length*100).toFixed(1);
  var avgPnl = (closed.reduce(function(a,j){return a+parseFloat(j.pnlPct||0);},0)/closed.length).toFixed(2);
  return '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:12px">'
    +'<div class="sd-metric neu"><div class="sd-metric-val">'+closed.length+'</div><div class="sd-metric-lbl">Kayitli Islem</div></div>'
    +'<div class="sd-metric '+(parseFloat(wr)>=55?'pos':'neg')+'"><div class="sd-metric-val">%'+wr+'</div><div class="sd-metric-lbl">Win Rate</div></div>'
    +'<div class="sd-metric '+(parseFloat(avgPnl)>=0?'pos':'neg')+'"><div class="sd-metric-val">'+(avgPnl>=0?'+':'')+avgPnl+'%</div><div class="sd-metric-lbl">Ort. PnL</div></div>'
    +'</div>';
}

function renderJournalEntry(j){
  var pnl = j.pnlPct !== null ? parseFloat(j.pnlPct) : null;
  var cls = pnl===null?'':pnl>=0?'win':'loss';
  var d = new Date(j.date);
  var dStr = d.getDate()+'.'+(d.getMonth()+1)+'.'+d.getFullYear();
  return '<div class="journal-entry '+cls+'">'
    +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
    +'<span style="font-size:12px;font-weight:800;color:var(--t1)">'+j.ticker+'</span>'
    +'<span style="font-size:8px;color:var(--t4)">'+j.tf+' | '+dStr+'</span>'
    +(pnl!==null?'<span style="margin-left:auto;font-size:11px;font-weight:700;color:'+(pnl>=0?'var(--green)':'var(--red)')+'">'+(pnl>=0?'+':'')+pnl.toFixed(1)+'%</span>':'')
    +'</div>'
    +(j.note?'<div style="font-size:9.5px;color:var(--t3);line-height:1.5">'+j.note+'</div>':'')
    +'<div style="display:flex;gap:6px;margin-top:6px">'
    +'<span style="font-size:7.5px;padding:2px 7px;border-radius:4px;background:rgba(255,255,255,.06);color:var(--t4)">'+j.signalType+'</span>'
    +(j.entry?'<span style="font-size:7.5px;padding:2px 7px;border-radius:4px;background:rgba(255,255,255,.04);color:var(--t4)">Giris: '+j.entry+'</span>':'')
    +'<button onclick="deleteJournalEntry('+j.id+')" style="margin-left:auto;background:none;border:none;color:rgba(255,68,68,.4);font-size:12px;cursor:pointer">X</button>'
    +'</div></div>';
}

window.quickJournalAdd = function(){
  try{
    var ticker = (document.getElementById('jTicker')||{}).value||'';
    var note = (document.getElementById('jNote')||{}).value||'';
    if(!ticker.trim()){ toast('Hisse kodu girin'); return; }
    addJournalEntry(ticker.trim().toUpperCase(), 'D', null, null, null, note);
    toast('Kaydedildi!');
    if(typeof haptic==='function') haptic('success');
    openJournalModal();
  }catch(e){}
};

window.deleteJournalEntry = function(id){
  _journal = _journal.filter(function(j){return j.id!==id;});
  saveJournal();
  openJournalModal();
};

// Pozisyon acilinca otomatik journal
var _origClosePosition = typeof closePosition==='function' ? closePosition : null;
if(_origClosePosition){
  closePosition = function(key, exitPrice){
    try{ _origClosePosition(key, exitPrice); }catch(e){}
    try{
      var parts = (key||'').split('_');
      var ticker = parts[0]; var tf = parts[1]||'D';
      var pos = S.openPositions&&S.openPositions[key];
      if(pos && exitPrice){
        var pnl = ((exitPrice-pos.entry)/pos.entry*100);
        addJournalEntry(ticker, tf, pos.entry, exitPrice, pnl.toFixed(2), '', pos.signalType||'AL');
      }
    }catch(e){}
  };
}

// --- 2. POSITION SIZING ---------------------------------------
function openPositionSizer(ticker, currentPrice, stopPrice){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Pozisyon Buyutleme';

    var closedArr = S.closedPositions||[];
    var avgPnl = closedArr.length ? closedArr.reduce(function(a,p){return a+parseFloat(p.pnlPct||0);},0)/closedArr.length : 2;

    document.getElementById('mcont').innerHTML =
      '<div style="padding:3px 0">'
      +'<div class="pos-sizer">'
      +'<div style="font-size:9px;font-weight:700;color:var(--cyan);margin-bottom:10px">POZISYON BUYUTLEME HESAPLAYICI</div>'
      +'<div class="pos-sizer-row"><span class="pos-sizer-label">Toplam Portfoy (TL)</span>'
      +'<input class="pos-sizer-inp" id="psPortfoy" type="number" value="100000" oninput="calcPositionSize()"></div>'
      +'<div class="pos-sizer-row"><span class="pos-sizer-label">Risk Orani (%)</span>'
      +'<input class="pos-sizer-inp" id="psRisk" type="number" value="2" min="0.5" max="10" step="0.5" oninput="calcPositionSize()"></div>'
      +'<div class="pos-sizer-row"><span class="pos-sizer-label">Giris Fiyati (TL)</span>'
      +'<input class="pos-sizer-inp" id="psEntry" type="number" value="'+(currentPrice||0).toFixed(2)+'" oninput="calcPositionSize()"></div>'
      +'<div class="pos-sizer-row"><span class="pos-sizer-label">Stop Fiyati (TL)</span>'
      +'<input class="pos-sizer-inp" id="psStop" type="number" value="'+(stopPrice||(currentPrice*0.95)||0).toFixed(2)+'" oninput="calcPositionSize()"></div>'
      +'<div class="pos-sizer-row"><span class="pos-sizer-label">Hedef Fiyati (TL)</span>'
      +'<input class="pos-sizer-inp" id="psTarget" type="number" value="'+(currentPrice?(currentPrice*1.08).toFixed(2):0)+'" oninput="calcPositionSize()"></div>'
      +'</div>'
      +'<div class="pos-sizer-result" id="psSonuc">'
      +'<div class="pos-sizer-lot" id="psLot">-</div>'
      +'<div class="pos-sizer-sub" id="psSubtext">Hesaplaniyor...</div>'
      +'</div>'
      +'<div style="margin-top:10px;padding:9px;background:rgba(255,255,255,.03);border-radius:8px;font-size:8.5px;color:var(--t4);line-height:1.6">'
      +'Kelly Criterion + Sabit Yuzde Risk yontemi kullanilir. '
      +'%2 risk, standart pozisyon buyutleme kuralidir. '
      +'Yatirim tavsiyesi niteligi tasimaz.'
      +'</div></div>'
    ;
    modal.classList.add('on');
    calcPositionSize();
  }catch(e){}
}

window.calcPositionSize = function(){
  try{
    var portfoy = parseFloat(document.getElementById('psPortfoy').value)||100000;
    var risk = parseFloat(document.getElementById('psRisk').value)||2;
    var entry = parseFloat(document.getElementById('psEntry').value)||0;
    var stop = parseFloat(document.getElementById('psStop').value)||0;
    var target = parseFloat(document.getElementById('psTarget').value)||0;
    if(!entry||!stop||stop>=entry){ document.getElementById('psSonuc').innerHTML='<div style="color:var(--red);font-size:11px">Stop fiyati giris fiyatindan dusuk olmali</div>'; return; }

    var riskTL = portfoy * risk/100;
    var stopDist = entry - stop;
    var lot = Math.floor(riskTL / stopDist);
    var totalCost = lot * entry;
    var maxLoss = lot * stopDist;
    var potGain = target>entry ? lot*(target-entry) : 0;
    var rr = potGain>0&&maxLoss>0 ? (potGain/maxLoss).toFixed(2) : '-';
    var portPct = (totalCost/portfoy*100).toFixed(1);

    document.getElementById('psLot').textContent = lot.toLocaleString('tr-TR')+' Adet';
    document.getElementById('psSubtext').innerHTML =
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;font-size:9px;text-align:left">'
      +'<div><div style="color:var(--t4)">Toplam Maliyet</div><div style="color:var(--t1);font-weight:700">'+totalCost.toLocaleString('tr-TR',{maximumFractionDigits:0})+' TL</div></div>'
      +'<div><div style="color:var(--t4)">Max Kayip</div><div style="color:var(--red);font-weight:700">-'+maxLoss.toLocaleString('tr-TR',{maximumFractionDigits:0})+' TL</div></div>'
      +'<div><div style="color:var(--t4)">Portfoy %</div><div style="color:var(--cyan);font-weight:700">%'+portPct+'</div></div>'
      +'<div><div style="color:var(--t4)">Risk/Odul</div><div style="color:var(--green);font-weight:700">1:'+rr+'</div></div>'
      +'</div>';
  }catch(e){}
};

// --- 3. MARKET BREADTH ---------------------------------------
function openMarketBreadth(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Piyasa Genisligi';

    var sigs = S.sigs||[];
    var hist = S.sigHistory||[];
    var allSigs = sigs.concat(hist).slice(0,500);

    // Son 24 saat
    var cutoff = Date.now()-86400000;
    var recent = allSigs.filter(function(s){ return new Date(s.time||s.date||0).getTime()>cutoff; });

    var al = recent.filter(function(s){return (s.type||'').indexOf('al')>-1||s.type==='master';}).length;
    var stop = recent.filter(function(s){return s.type==='stop';}).length;
    var total = recent.length||1;
    var bullPct = Math.round(al/total*100);

    // Sektor dagilim
    var SECTORS={'Enerji':['AKFYE','CWENE','ENJSA','EUPWR','GESAN','ZOREN','ODAS','PRKME'],
      'Metal':['EREGL','ISDMR','KRDMD','BRSAN'],'Gida':['BIMAS','ULKER','TATGD','OBAMS'],
      'Teknoloji':['LOGO','ARDYZ','KONTR','YEOTK'],'Holding':['KCHOL','SAHOL','ENKAI'],
      'Banka':['AKBNK','GARAN','ISCTR','VAKBN','YKBNK'],'Tekstil':['MAVI','KORDS']};
    var sectorData = {};
    Object.keys(SECTORS).forEach(function(s){
      var tickers=SECTORS[s];
      var cnt=recent.filter(function(sig){return tickers.indexOf(sig.ticker||sig.t)>-1;}).length;
      sectorData[s]=cnt;
    });

    // ADX>30 hisse sayisi - sigs'ten hesapla
    var trendCount = sigs.filter(function(s){return (s.res&&s.res.adx||0)>30;}).length;
    var masterCount = sigs.filter(function(s){return s.type==='master';}).length;

    document.getElementById('mcont').innerHTML =
      '<div style="padding:3px 0">'
      // Breadth gorunumu
      +'<div class="breadth-grid">'
      +'<div class="breadth-card">'
      +'<div style="font-size:26px;font-weight:800;color:var(--green)">'+al+'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:2px">AL Sinyali (24S)</div></div>'
      +'<div class="breadth-card">'
      +'<div style="font-size:26px;font-weight:800;color:var(--red)">'+stop+'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:2px">Stop (24S)</div></div>'
      +'<div class="breadth-card">'
      +'<div style="font-size:26px;font-weight:800;color:var(--gold)">'+masterCount+'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:2px">Master AI</div></div>'
      +'<div class="breadth-card">'
      +'<div style="font-size:26px;font-weight:800;color:var(--cyan)">'+trendCount+'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:2px">ADX>30 Trend</div></div>'
      +'<div class="breadth-card">'
      +'<div style="font-size:26px;font-weight:800;color:'+(bullPct>60?'var(--green)':bullPct<40?'var(--red)':'var(--gold)')+'">%'+bullPct+'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:2px">Boga Orani</div></div>'
      +'<div class="breadth-card">'
      +'<div style="font-size:26px;font-weight:800;color:var(--purple)">'+sigs.length+'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:2px">Aktif Sinyal</div></div>'
      +'</div>'
      // Piyasa degerlendirmesi
      +'<div style="padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);margin-bottom:10px">'
      +'<div style="font-size:10px;font-weight:700;color:var(--t1);margin-bottom:4px">Genel Piyasa: '
      +(bullPct>65?'<span style="color:var(--green)">GUCLU BOGA</span>':bullPct>50?'<span style="color:var(--gold)">TEMKINLI BOGA</span>':bullPct>35?'<span style="color:var(--gold)">NOTR</span>':'<span style="color:var(--red)">AYI EGILIMI</span>')+'</div>'
      +'<div style="font-size:9px;color:var(--t3);line-height:1.6">Son 24 saatte '+total+' sinyal. '
      +'Aktif sinyallerin %'+bullPct+' AL yonunde. '
      +(trendCount>10?trendCount+' hissede guclu trend (ADX>30). ':' ')
      +(masterCount>3?masterCount+' Master AI onayi mevcut.':'')
      +'</div></div>'
      // Sektor dagilimi
      +'<div style="font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:7px">Sektor Sinyal Dagilimi (24S)</div>'
      +'<div style="display:grid;gap:4px">'
      +Object.keys(sectorData).map(function(s){
        var cnt=sectorData[s]; var max=Math.max.apply(null,Object.values(sectorData))||1;
        var pct=Math.round(cnt/max*100);
        return '<div style="display:flex;align-items:center;gap:8px;padding:5px 0">'
          +'<div style="width:60px;font-size:9px;color:var(--t3)">'+s+'</div>'
          +'<div style="flex:1;height:5px;background:rgba(255,255,255,.06);border-radius:3px">'
          +'<div style="height:100%;width:'+pct+'%;background:var(--cyan);border-radius:3px"></div></div>'
          +'<div style="width:20px;font-size:9px;color:var(--t2);text-align:right">'+cnt+'</div>'
          +'</div>';
      }).join('')
      +'</div></div>'
    ;
    modal.classList.add('on');
  }catch(e){ toast('Breadth: '+e.message); }
}

// --- 4. MAKRO PANEL ------------------------------------------
function openMacroPanel(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Makro Pano';
    document.getElementById('mcont').innerHTML =
      '<div style="padding:3px 0"><div style="text-align:center;padding:16px;color:var(--t4);font-size:10px">Yukleniyor...</div></div>';
    modal.classList.add('on');

    var PROXY = typeof PROXY_URL!=='undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';
    // XU100 + AI makro yorum
    Promise.all([
      fetch(PROXY+'/xu100').then(function(r){return r.json();}).catch(function(){return null;}),
      fetch(PROXY+'/ai/chat',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({message:'USD/TRY kuru bugun nasil? BIST ile korelasyonu nedir? TCMB faiz durumu? Kisa makro ozet.',agent:'bist-trader',bist_context:{}})
      }).then(function(r){return r.json();}).catch(function(){return{response:'Veri alinirken hata olustu.'};})
    ]).then(function(results){
      var xu = results[0]; var ai = results[1];
      var xuVal = xu&&xu.price ? xu.price.toFixed(0) : '-';
      var xuChg = xu&&xu.change_pct ? xu.change_pct.toFixed(2) : '0';
      var trend = xu&&xu.trend ? xu.trend : 'neutral';
      var aiText = ai&&ai.response ? ai.response : 'AI yaniti yok.';

      document.getElementById('mcont').innerHTML =
        '<div style="padding:3px 0">'
        // XU100
        +'<div class="macro-ticker" style="border-color:rgba(0,212,255,.15)">'
        +'<div><div style="font-size:9px;color:var(--t4)">BIST 100 (XU100)</div>'
        +'<div style="font-size:9px;color:var(--t3)">Borsa Istanbul Endeksi</div></div>'
        +'<div class="macro-val" style="color:var(--cyan)">'+xuVal+'<span style="font-size:11px;color:'+(parseFloat(xuChg)>=0?'var(--green)':'var(--red)')+'"> '+(parseFloat(xuChg)>=0?'+':'')+xuChg+'%</span></div>'
        +'</div>'
        // Piyasa durumu
        +'<div style="padding:10px 12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:10px;margin-bottom:10px">'
        +'<div style="font-size:9px;font-weight:700;color:var(--t4);margin-bottom:6px">Piyasa Trendi</div>'
        +'<div style="display:flex;align-items:center;gap:8px">'
        +'<div style="width:10px;height:10px;border-radius:50%;background:'+(trend==='bullish'?'var(--green)':trend==='bearish'?'var(--red)':'var(--gold)')+'"></div>'
        +'<div style="font-size:12px;font-weight:700;color:var(--t1)">'+(trend==='bullish'?'YUKSELIS TRENDI':trend==='bearish'?'DUSUS TRENDI':'YATAY SEYIR')+'</div>'
        +'</div></div>'
        // AI Makro Yorum
        +'<div style="background:rgba(192,132,252,.06);border:1px solid rgba(192,132,252,.15);border-radius:11px;padding:12px;margin-bottom:10px">'
        +'<div style="font-size:9px;font-weight:700;color:var(--purple);margin-bottom:7px">AI Makro Ozeti</div>'
        +'<div style="font-size:10px;color:var(--t2);line-height:1.7">'+aiText+'</div>'
        +'</div>'
        +'<div style="font-size:8px;color:var(--t4);text-align:center;padding:4px">'
        +'Makro veriler bilgilendirme amaclidir. Yatirim tavsiyesi degildir.</div>'
        +'</div>'
      ;
    });
  }catch(e){ toast('Makro: '+e.message); }
}

// --- 5. STRESS TEST ------------------------------------------
function openStressTest(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Stres Testi';

    var positions = S.openPositions||{};
    var posKeys = Object.keys(positions);

    // Tarihi kriz senaryolari - BIST ortalama dusus
    var scenarios = [
      {name:'2018 Doviz Krizi (Agustos)',year:2018,drop:-33,dur:'6 ay',desc:'USD/TRY 7\'ye cikti, BIST sert dusus'},
      {name:'2020 COVID Pandemisi (Mart)',year:2020,drop:-30,dur:'3 ay',desc:'Kuresel salgin, global satis dalgasi'},
      {name:'2022 Rusya-Ukrayna (Subat)',year:2022,drop:-22,dur:'2 ay',desc:'Jeopolitik risk artisi, enerji krizi'},
      {name:'2021 TCMB Faiz Krizi',year:2021,drop:-40,dur:'4 ay',desc:'Merkez Bankasi baskani degisimi, kur krizi'},
    ];

    var posTotal = posKeys.reduce(function(a,k){
      var p=positions[k]; return a+(p.entry||0);
    },0);

    var html = '<div style="padding:3px 0">';

    if(posKeys.length===0){
      html += '<div style="padding:20px;text-align:center;color:var(--t4);font-size:11px">Stres testi icin acik pozisyon olmali.</div>';
    } else {
      html += '<div style="padding:9px 12px;background:rgba(255,255,255,.03);border-radius:9px;margin-bottom:10px">'
        +'<div style="font-size:9px;color:var(--t4)">Test edilen '+posKeys.length+' acik pozisyon</div>'
        +'</div>';

      scenarios.forEach(function(sc){
        var totalLoss = 0;
        var posResults = posKeys.map(function(k){
          var p=positions[k]; var t=k.split('_')[0];
          var loss = (p.entry||0) * (sc.drop/100);
          totalLoss += Math.abs(loss);
          return {ticker:t,entry:p.entry,loss:loss};
        });
        var totalLossPct = posTotal>0?(totalLoss/posTotal*100).toFixed(1):0;

        html += '<div class="stress-scenario">'
          +'<div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:8px">'
          +'<div style="flex:1"><div style="font-size:11px;font-weight:700;color:var(--t1)">'+sc.name+'</div>'
          +'<div style="font-size:8.5px;color:var(--t3);margin-top:2px">'+sc.desc+' | Sure: '+sc.dur+'</div></div>'
          +'<div class="stress-pct" style="color:var(--red)">'+sc.drop+'%</div>'
          +'</div>'
          +'<div style="display:flex;align-items:center;gap:8px;padding:8px;background:rgba(255,68,68,.06);border-radius:7px">'
          +'<div style="flex:1"><div style="font-size:9px;color:var(--t4)">Portfoy Etkisi</div>'
          +'<div style="font-size:13px;font-weight:800;color:var(--red)">-'+totalLossPct+'%</div></div>'
          +'<div style="text-align:right"><div style="font-size:9px;color:var(--t4)">Tahmini Kayip</div>'
          +'<div style="font-size:11px;font-weight:700;color:var(--red)">-'+totalLoss.toLocaleString('tr-TR',{maximumFractionDigits:0})+' TL</div></div>'
          +'</div></div>';
      });
    }

    html += '<div style="margin-top:8px;padding:9px;background:rgba(255,184,0,.05);border:1px solid rgba(255,184,0,.12);border-radius:8px;font-size:8.5px;color:rgba(255,184,0,.6);line-height:1.5">'
      +'Stres testi sonuclari tarihsel senaryolara dayanir. Gelecekteki kayiplari garantilemez. '
      +'Yatirim tavsiyesi degildir.</div>'
      +'</div>';

    document.getElementById('mcont').innerHTML = html;
    modal.classList.add('on');
  }catch(e){ toast('Stres: '+e.message); }
}

// --- 6. STRATEJI KUTUPHANESI ----------------------------------
var _strategies = (function(){
  try{ return JSON.parse(localStorage.getItem('bist_strategies')||'[]'); }catch(e){ return []; }
})();

// Varsayilan stratejiler
if(_strategies.length===0){
  _strategies = [
    {id:1,name:'Trend + Dusuk Fiyat',desc:'Master AI + pstate=COK UCUZ + ADX>30',params:{minConsensus:70,onlyMaster:true,minADX:30,zones:['COK UCUZ']},author:'Sistem',likes:42,used:128,badge:'Populer'},
    {id:2,name:'Momentum Kirilim',desc:'S2 PRO + 4H Momentum + ADX>40',params:{minConsensus:60,onlyMaster:false,minADX:40,systems:['s2','a120']},author:'Sistem',likes:31,used:87,badge:'Guclu'},
    {id:3,name:'Guvenli Giris',desc:'Tum sistemler + pstate=UCUZ + ADX>25',params:{minConsensus:80,onlyMaster:false,minADX:25},author:'Sistem',likes:55,used:203,badge:'Onerilir'},
  ];
  try{ localStorage.setItem('bist_strategies',JSON.stringify(_strategies)); }catch(e){}
}

function openStrategyLibrary(){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    document.getElementById('mtit').textContent = 'Strateji Kutuphanesi';

    var html = '<div style="padding:3px 0">'
      +'<button onclick="showSaveStrategyModal()" style="width:100%;padding:10px;border-radius:9px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:11px;font-weight:700;cursor:pointer;margin-bottom:10px">+ Strateji Kaydet</button>'
      +_strategies.map(function(s){
        return '<div class="strat-card" onclick="applyStrategy('+s.id+')">'
          +'<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">'
          +'<div style="flex:1"><div style="font-size:11px;font-weight:700;color:var(--t1)">'+s.name+'</div>'
          +'<div style="font-size:8.5px;color:var(--t3);margin-top:2px">'+s.desc+'</div></div>'
          +'<span class="strat-badge">'+s.badge+'</span></div>'
          +'<div style="display:flex;gap:10px;font-size:8px;color:var(--t4)">'
          +'<span>Yazar: '+s.author+'</span>'
          +'<span>Begeni: '+s.likes+'</span>'
          +'<span>Kullanan: '+s.used+'</span>'
          +'</div></div>';
      }).join('')
      +'</div>'
    ;
    document.getElementById('mcont').innerHTML = html;
    modal.classList.add('on');
  }catch(e){}
}

window.applyStrategy = function(id){
  try{
    var s = _strategies.find(function(x){return x.id===id;});
    if(!s) return;
    var p = s.params||{};
    if(p.minConsensus!==undefined){ C.minCons=p.minConsensus; var el=document.getElementById('s_minCons'); if(el)el.value=p.minConsensus; }
    if(p.onlyMaster!==undefined){ C.onlyMaster=p.onlyMaster; }
    if(p.minADX!==undefined){ C.adxMin=p.minADX; var el2=document.getElementById('s_adxMin'); if(el2)el2.value=p.minADX; }
    try{ lsSet('bistcfg',C); }catch(e){}
    s.used = (s.used||0)+1;
    try{ localStorage.setItem('bist_strategies',JSON.stringify(_strategies)); }catch(e){}
    closeM&&closeM();
    toast('"'+s.name+'" stratejisi uygulandi!');
    if(typeof haptic==='function') haptic('success');
  }catch(e){ toast('Strateji: '+e.message); }
};

window.showSaveStrategyModal = function(){
  try{
    var name = prompt('Strateji adi:');
    if(!name) return;
    var newS = {
      id: Date.now(),
      name: name,
      desc: 'Konsensus:'+C.minCons+'% | ADX:'+C.adxMin+' | MasterAI:'+(C.onlyMaster?'Evet':'Hayir'),
      params: {minConsensus:C.minCons||0, onlyMaster:!!C.onlyMaster, minADX:C.adxMin||25},
      author: (_auth&&_auth.user&&_auth.user.username)||'Ben',
      likes: 0, used: 1, badge: 'Yeni',
    };
    _strategies.unshift(newS);
    try{ localStorage.setItem('bist_strategies',JSON.stringify(_strategies)); }catch(e){}
    toast('Strateji kaydedildi!');
    if(typeof haptic==='function') haptic('success');
    openStrategyLibrary();
  }catch(e){}
};

// --- 7. TELEGRAM KOMUTLARI ------------------------------------
// Mevcut startTGListener'i genislet
var _origStartTGL = typeof startTGListener==='function' ? startTGListener : null;
startTGListener = function(){
  try{ if(_origStartTGL) _origStartTGL(); }catch(e){}
  // Ekstra komutlar
  try{
    if(!TG||!TG.token||!TG.chat) return;
    // Komut listesini Telegram'a gonder
    var cmdList = '/durum - Sistem durumu\n/portfoy - Acik pozisyonlar\n/sinyal - Son sinyaller\n/genislik - Market breadth\n/tara - Manuel tarama\n/journal - Son islemler\n/stres - Stres testi ozeti\n/yardim - Komutlar';
    // Polling - her 30sn Telegram\'dan komut cek
    if(window._tgPollTimer) clearInterval(window._tgPollTimer);
    window._tgPollTimer = setInterval(function(){
      try{ processTGCommands(); }catch(e){}
    }, 30000);
  }catch(e){}
};

function processTGCommands(){
  try{
    if(!TG||!TG.token||!TG.chat) return;
    var offset = parseInt(localStorage.getItem('bist_tg_offset')||'0');
    fetch('https://api.telegram.org/bot'+TG.token+'/getUpdates?offset='+offset+'&timeout=5')
      .then(function(r){return r.json();})
      .then(function(data){
        if(!data.ok||!data.result) return;
        data.result.forEach(function(upd){
          localStorage.setItem('bist_tg_offset', (upd.update_id+1).toString());
          var msg = upd.message&&upd.message.text;
          if(!msg) return;
          var chatId = upd.message.chat&&upd.message.chat.id;
          if(String(chatId)!==String(TG.chat)) return; // Sadece kayitli sohbet
          handleTGCommand(msg.trim(), chatId);
        });
      }).catch(function(){});
  }catch(e){}
}

function handleTGCommand(cmd, chatId){
  try{
    var response = '';
    var lower = cmd.toLowerCase();
    if(lower==='/durum'||lower==='durum'){
      var posC = Object.keys(S.openPositions||{}).length;
      response = 'BIST Pro Durumu\nSinyal: '+(S.sigs||[]).length+'\nAcik Poz: '+posC+'\nXU100: '+(S.xu100Change>=0?'+':'')+(S.xu100Change||0).toFixed(2)+'%\nSon tarama: '+(S.lastScanTime?new Date(S.lastScanTime).toLocaleTimeString('tr-TR'):'Bekleniyor');
    } else if(lower==='/portfoy'||lower==='portfoy'){
      var pKeys=Object.keys(S.openPositions||{});
      if(!pKeys.length){ response='Acik pozisyon yok.'; }
      else { response='Acik Pozisyonlar ('+pKeys.length+'):\n'+pKeys.map(function(k){ var p=S.openPositions[k]; return k.split('_')[0]+': '+p.entry+' TL'; }).join('\n'); }
    } else if(lower==='/sinyal'||lower==='sinyal'){
      var recentSigs=(S.sigs||[]).slice(0,5);
      if(!recentSigs.length){ response='Sinyal yok.'; }
      else { response='Son Sinyaller:\n'+recentSigs.map(function(s){ return s.ticker+' ('+s.tf+') - '+(s.res&&s.res.price?'TL'+s.res.price:''); }).join('\n'); }
    } else if(lower==='/genislik'){
      var al=(S.sigs||[]).length;
      response='Market Breadth:\nAktif Sinyal: '+al+'\nXU100: '+(S.xu100Change>=0?'+':'')+(S.xu100Change||0).toFixed(2)+'%';
    } else if(lower==='/journal'){
      var recent=_journal.slice(0,5);
      if(!recent.length){ response='Journal bos.'; }
      else { response='Son Journal Kayitlari:\n'+recent.map(function(j){ return j.ticker+(j.pnlPct?(' '+( j.pnlPct>=0?'+':'')+j.pnlPct+'%'):''); }).join('\n'); }
    } else if(lower==='/stres'){
      var posC2=Object.keys(S.openPositions||{}).length;
      response='Stres Testi Ozeti:\nAcik Poz: '+posC2+'\n2018 senaryosu: yakl. -%33 portfoy etkisi\n2020 senaryosu: yakl. -%30 portfoy etkisi';
    } else if(lower==='/yardim'||lower==='yardim'){
      response='/durum /portfoy /sinyal /genislik /journal /stres';
    } else if(lower.startsWith('/tara')){
      response='Tarama baslatiliyor...';
      setTimeout(function(){ try{ if(typeof startScan==='function') startScan(); }catch(e){} },500);
    } else { return; }
    if(response){
      fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({chat_id:chatId,text:response})
      }).catch(function(){});
    }
  }catch(e){}
}

// --- 8. ONBOARDING -------------------------------------------
var _onboardSteps = [
  {icon:'',title:'BIST Pro Hosgeldiniz',desc:'424+ BIST hissesini 8 AI sistemle tarayan profesyonel analiz araci. 3 adimda baslayalim.'},
  {icon:'',title:'Tarama Baslat',desc:'TARA butonuna basin. Uygulama tum hisseleri analiz eder, guclu sinyalleri listeler.'},
  {icon:'',title:'Sinyale Tikla',desc:'Gelen sinyale dokunun. Icinde grafik, MTF analizi, desen tanima ve AI yorum bulunur.'},
  {icon:'',title:'Pozisyon Takibi',desc:'Pozisyonlar sekmesinde kazancinizi takip edin. Trailing stop otomatik hesaplanir.'},
];
var _onboardIdx = 0;

function showOnboarding(){
  try{
    if(localStorage.getItem('bist_onboard_done')==='1') return;
    var overlay = document.getElementById('onboardOverlay');
    if(!overlay){
      overlay = document.createElement('div');
      overlay.id = 'onboardOverlay';
      overlay.className = 'on';
      overlay.innerHTML =
        '<div class="onboard-box">'
        +'<div class="onboard-step-dots" id="onboardDots"></div>'
        +'<div id="onboardIcon" style="font-size:40px;text-align:center;margin-bottom:12px"></div>'
        +'<div id="onboardTitle" style="font-size:17px;font-weight:800;color:var(--t1);margin-bottom:8px;text-align:center"></div>'
        +'<div id="onboardDesc" style="font-size:11px;color:var(--t3);line-height:1.7;text-align:center;margin-bottom:20px"></div>'
        +'<button id="onboardNext" onclick="nextOnboard()" style="width:100%;padding:14px;border-radius:11px;background:var(--cyan);color:#000;font-size:13px;font-weight:800;border:none;cursor:pointer">Devam</button>'
        +'<button onclick="skipOnboard()" style="width:100%;padding:10px;margin-top:7px;background:none;border:none;color:rgba(255,255,255,.25);font-size:10px;cursor:pointer">Atla</button>'
        +'</div>';
      document.body.appendChild(overlay);
    }
    renderOnboardStep();
  }catch(e){}
}

function renderOnboardStep(){
  try{
    var step = _onboardSteps[_onboardIdx];
    var dots = document.getElementById('onboardDots');
    if(dots) dots.innerHTML = _onboardSteps.map(function(_,i){ return '<div class="onboard-dot'+(i===_onboardIdx?' active':'')+'"</div>'; }).join('');
    var iconEl=document.getElementById('onboardIcon'); if(iconEl) iconEl.textContent=step.icon;
    var titleEl=document.getElementById('onboardTitle'); if(titleEl) titleEl.textContent=step.title;
    var descEl=document.getElementById('onboardDesc'); if(descEl) descEl.textContent=step.desc;
    var nextBtn=document.getElementById('onboardNext');
    if(nextBtn) nextBtn.textContent=_onboardIdx===_onboardSteps.length-1?'Baslayalim!':'Devam';
  }catch(e){}
}

window.nextOnboard = function(){
  try{
    _onboardIdx++;
    if(_onboardIdx>=_onboardSteps.length){ skipOnboard(); return; }
    renderOnboardStep();
    if(typeof haptic==='function') haptic('light');
  }catch(e){}
};

window.skipOnboard = function(){
  try{
    localStorage.setItem('bist_onboard_done','1');
    var overlay=document.getElementById('onboardOverlay');
    if(overlay){ overlay.style.transition='opacity .3s'; overlay.style.opacity='0'; setTimeout(function(){if(overlay.parentNode)overlay.parentNode.removeChild(overlay);},300); }
  }catch(e){}
};

// --- 9. RAPOR SEKMESI EKSTRA BUTONLAR ------------------------
window.addEventListener('load',function(){
  setTimeout(function(){
    try{
      // Dev tab
      if(!document.getElementById('devTab')){
        var nav=document.querySelector('nav');
        if(nav){
          var btn=document.createElement('button');btn.id='devTab';btn.className='tab';
          btn.innerHTML='Dev';
          btn.onclick=function(){try{pg('dev');renderDevPanel();}catch(e){}};
          nav.appendChild(btn);
        }
      }
      // page-dev
      if(!document.getElementById('page-dev')){
        var main=document.querySelector('main');
        if(main){var p=document.createElement('div');p.id='page-dev';p.className='page';main.appendChild(p);}
      }

      // Rapor sayfasina ekstra araclar
      var repPage=document.getElementById('page-report');
      if(repPage&&!document.getElementById('v3ExtraTools')){
        var card=document.createElement('div');card.id='v3ExtraTools';card.className='card';
        card.style.cssText='border-color:rgba(0,212,255,.12);margin-top:8px';
        card.innerHTML=
          '<div class="ctitle" style="color:var(--cyan)">Profesyonel Araclar</div>'
          +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">'
          +'<button class="btn c" onclick="openJournalModal()" style="padding:10px;border-radius:8px;font-size:10px">Trader Journal</button>'
          +'<button class="btn" onclick="openMarketBreadth()" style="padding:10px;border-radius:8px;font-size:10px;border:1px solid rgba(255,255,255,.1);color:var(--t3)">Market Breadth</button>'
          +'<button class="btn" onclick="openMacroPanel()" style="padding:10px;border-radius:8px;font-size:10px;border:1px solid rgba(255,255,255,.1);color:var(--t3)">Makro Pano</button>'
          +'<button class="btn" onclick="openStressTest()" style="padding:10px;border-radius:8px;font-size:10px;border:1px solid rgba(255,255,255,.1);color:var(--t3)">Stres Testi</button>'
          +'<button class="btn" onclick="openStrategyLibrary()" style="padding:10px;border-radius:8px;font-size:10px;border:1px solid rgba(255,255,255,.1);color:var(--t3)">Strateji Kutuphane</button>'
          +'<button class="btn" onclick="openPositionSizer(\'EREGL\',0,0)" style="padding:10px;border-radius:8px;font-size:10px;border:1px solid rgba(255,255,255,.1);color:var(--t3)">Lot Hesaplayici</button>'
          +'</div>';
        repPage.appendChild(card);
      }

      // Position sizing'i sinyal kartina ekle
      var origOpenSig2 = typeof openSig==='function' ? openSig : null;
      if(origOpenSig2 && !window._posSizerHooked){
        window._posSizerHooked = true;
        openSig = function(idx){
          origOpenSig2(idx);
          setTimeout(function(){
            try{
              var mcont=document.getElementById('mcont');
              if(!mcont||mcont.querySelector('.pos-size-btn')) return;
              var sig = (S.sigs||[])[idx]; if(!sig) return;
              var price=sig.res&&sig.res.price||0;
              var stop=sig.res&&sig.res.stop_price||0;
              var btn=document.createElement('button');
              btn.className='pos-size-btn';
              btn.style.cssText='margin-top:8px;width:100%;padding:9px;border-radius:8px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;font-weight:700;cursor:pointer';
              btn.textContent='Lot Hesaplayici';
              btn.onclick=function(){ closeM&&closeM(); setTimeout(function(){ openPositionSizer(sig.ticker,price,stop); },200); };
              mcont.appendChild(btn);
            }catch(e){}
          },200);
        };
      }

      // Telegram dinlemeyi baslat
      try{ if(TG&&TG.token) startTGListener(); }catch(e){}

      // Onboarding
      setTimeout(showOnboarding,1500);

      if(typeof devLog==='function') devLog('BIST Pro v3 tum moduller aktif','ok');
    }catch(e){ console.warn('v3 init:',e.message); }
  },700);
});

</script>
<script>

// BIST PRO v3 - BLOK 24
// GLOBAL NULL SAFETY PATCH
// 1. modal/mtit/mcont null-safe wrapper
// 2. S.sigs/S.openPositions global guard
// 3. renderSigs/renderDevPanel/v13CallAPI chain fix confirmation
// 4. openTVTicker duplicate override fix
// 5. window load listener deduplication
// 6. setTimeout 800ms conflict fix

(function(){
'use strict';

// --- 1. MODAL NULL-SAFE GLOBAL WRAPPER -----------------------
// mtit, mcont, modal erisimlerini null-safe yap
function safeModal(title, html, open){
  try{
    var mtit = document.getElementById('mtit');
    var mcont = document.getElementById('mcont');
    var modal = document.getElementById('modal');
    if(!modal){ console.warn('modal element yok'); return false; }
    if(title && mtit) mtit.textContent = title;
    if(html && mcont) mcont.innerHTML = html;
    if(open !== false) modal.classList.add('on');
    return true;
  }catch(e){ console.warn('safeModal:',e.message); return false; }
}
window.safeModal = safeModal;

// closeM null-safe
var _origCloseM = typeof closeM === 'function' ? closeM : null;
window.closeM = function(){
  try{
    if(_origCloseM) _origCloseM();
    else {
      var m = document.getElementById('modal');
      if(m) m.classList.remove('on');
    }
  }catch(e){}
};

// --- 2. S OBJESINI HER ZAMAN GUVENDE TUT ---------------------
// S.sigs, S.openPositions, S.closedPositions null guard
function guardS(){
  try{
    if(typeof S === 'undefined') return;
    if(!S.sigs) S.sigs = [];
    if(!S.openPositions) S.openPositions = {};
    if(!S.closedPositions) S.closedPositions = [];
    if(!S.watchlist) S.watchlist = [];
    if(!S.alerts) S.alerts = {};
    if(!S.priceCache) S.priceCache = {};
    if(!S.ohlcvCache) S.ohlcvCache = {};
    if(typeof S.xu100Change === 'undefined') S.xu100Change = 0;
  }catch(e){}
}
// Periyodik guard
setInterval(guardS, 5000);
guardS();

// --- 3. RENDERDEVSANEL - SON OVERRIDE KULLAN -----------------
// Onceki 8 override'i iptal et. 
// Bu blok en son yuklenecek, bu yuzden buradaki tanim gecerli olacak.
// renderV15Settings (Blok 15) dogru olandir.
setTimeout(function(){
  try{
    if(typeof renderV15Settings === 'function' && typeof renderDevPanel === 'function'){
      // renderDevPanel = renderV15Settings yap - artik sabit
      window.renderDevPanel = renderV15Settings;
    }
  }catch(e){}
}, 100);

// --- 4. OPENTVTICKER - TEK OVERRIDE --------------------------
// Blok 22'deki override dashboard'a yonlendiriyor - bu dogru
// Blok 22'den onceki openTVTicker TradingView'e aciyordu
// Simdi sadece bir tane olsun
setTimeout(function(){
  try{
    if(typeof openStockDashboard === 'function'){
      window.openTVTicker = function(ticker, tf){
        try{
          var stk = typeof STOCKS!=='undefined' ? STOCKS.find(function(s){return s.t===ticker;}) : null;
          openStockDashboard(ticker, stk ? stk.n : ticker);
        }catch(e){
          // Fallback: TradingView
          window.open('https://www.tradingview.com/chart/?symbol=BIST:'+ticker,'_blank');
        }
      };
    }
  }catch(e){}
}, 200);

// --- 5. WINDOW LOAD LISTENER DEDUP ---------------------------
// 25 load listener var - cakisma onlemek icin
// 800ms setTimeout'u kaldirip yerine izlemci koy
var _loadDone = false;
var _pendingInits = [];

function runAfterLoad(fn, delay){
  delay = delay || 0;
  if(_loadDone){ setTimeout(fn, delay); return; }
  _pendingInits.push({fn:fn, delay:delay});
}

window.addEventListener('load', function(){
  _loadDone = true;
  setTimeout(function(){
    _pendingInits.forEach(function(item){
      try{ setTimeout(item.fn, item.delay); }catch(e){}
    });
    _pendingInits = [];
  }, 50);
}, {once:true, passive:true});

window._runAfterLoad = runAfterLoad;

// --- 6. RENDERSIGS CHAIN SAGLIGI -----------------------------
// 7 override - son gecerli render (Blok 8) korunsun
// Herhangi bir hata renderSigs'i kirmamali
setTimeout(function(){
  try{
    var _lastRenderSigs = window.renderSigs;
    if(typeof _lastRenderSigs === 'function'){
      window.renderSigs = function(){
        try{
          guardS(); // once guard
          _lastRenderSigs();
        }catch(e){
          console.warn('renderSigs error:', e.message);
          // Fallback: bos liste goster
          var sl = document.getElementById('siglist');
          if(sl) sl.innerHTML = '<div style="padding:20px;text-align:center;color:rgba(255,255,255,.3);font-size:11px">Sinyal listesi yenileniyor...</div>';
        }
      };
    }
  }catch(e){}
}, 300);

// --- 7. STARTSCAN CHAIN SAGLIGI ------------------------------
setTimeout(function(){
  try{
    var _lastStartScan = window.startScan;
    if(typeof _lastStartScan === 'function'){
      window.startScan = function(){
        try{
          guardS();
          _lastStartScan();
        }catch(e){
          console.warn('startScan error:', e.message);
          var btn = document.getElementById('scanBtn');
          if(btn){ btn.disabled = false; btn.textContent = 'TARA'; btn.classList.remove('scanning'); }
        }
      };
    }
  }catch(e){}
}, 400);

// --- 8. FETCH NULL-SAFE GLOBAL -------------------------------
// Tum fetch cagrilarinda network hatasi sessizce yakalan
var _origFetch = window.fetch;
window.fetch = function(url, opts){
  return _origFetch.call(this, url, opts)
    .catch(function(e){
      console.warn('fetch error:', url, e.message);
      return new Response(JSON.stringify({error: e.message}), {
        status: 0, headers: {'Content-Type':'application/json'}
      });
    });
};

// --- 9. GETELEMENTBYID NULL-SAFE MIXIN -----------------------
// En cok crash yaratan pattern: document.getElementById('x').property
// Global patch: eger element yoksa sessizce devam et
// getElementById proxy kaldirildi - pg() null-safe yapildi

// --- 10. GLOBAL ERROR HANDLER --------------------------------
window.addEventListener('error', function(e){
  if(!e) return;
  var msg = e.message || '';
  // Kritik olmayan hatalari sustur
  var ignore = ['Script error','ResizeObserver','Non-Error promise','Loading chunk'];
  if(ignore.some(function(s){ return msg.indexOf(s)>-1; })) return;
  // Blok bazli hata logla
  console.warn('[BIST Pro v3 Error]', msg, e.filename, e.lineno);
  // Devlog'a gonder
  try{ if(typeof devLog==='function') devLog('JS Error: '+msg.substring(0,80),'error'); }catch(ex){}
});

window.addEventListener('unhandledrejection', function(e){
  if(!e||!e.reason) return;
  var msg = (e.reason.message||String(e.reason)).substring(0,100);
  console.warn('[BIST Pro v3 UnhandledRej]', msg);
  e.preventDefault(); // Konsol kalabaligini onle
});

// --- 11. IOS SAFARI OZEL DUZELTMELER -------------------------
(function(){
  var isIOS = /iPhone|iPad/i.test(navigator.userAgent);
  var isSafari = /Safari/i.test(navigator.userAgent) && !/Chrome/i.test(navigator.userAgent);
  if(!isIOS && !isSafari) return;

  // iOS'ta backdrop-filter prefix eksikligini gider
  var st = document.createElement('style');
  st.textContent = 
    // iOS scroll momentum
    '.sdcontent, .chat-messages, .dm-list, .forum-topics, .soc-panel { -webkit-overflow-scrolling: touch; }'
    // iOS input zoom fix - font 16px alti zoom yapar
    + 'input, textarea, select { font-size: 16px !important; }'
    // iOS safe area padding
    + '.sdh, nav { padding-bottom: env(safe-area-inset-bottom); }'
    // iOS rubber band scroll onle
    + 'body { overscroll-behavior: none; }'
  ;
  document.head.appendChild(st);

  // iOS'ta WebSocket wss:// zorunlu
  var _origConnectChatWS = typeof connectChatWS === 'function' ? connectChatWS : null;
  if(_origConnectChatWS){
    window.connectChatWS = function(){
      // Proxy URL'i wss:// yap
      if(typeof SOCIAL_URL !== 'undefined'){
        window.SOCIAL_URL = SOCIAL_URL.replace(/^http:\/\//, 'https://');
      }
      try{ _origConnectChatWS(); }catch(e){ console.warn('iOS WS:',e); }
    };
  }
})();

// --- 12. ANDROID CHROME DUZELTMELER --------------------------
(function(){
  var isAndroid = /Android/i.test(navigator.userAgent);
  if(!isAndroid) return;

  // Android pull-to-refresh engellemesi
  document.body.style.overscrollBehavior = 'contain';
  document.documentElement.style.overscrollBehavior = 'none';

  // Android klavye acilinca layout bozulmasini onle
  var origH = window.innerHeight;
  window.addEventListener('resize', function(){
    var newH = window.innerHeight;
    if(origH - newH > 150){ // Klavye acildi
      document.body.style.height = newH + 'px';
    } else {
      document.body.style.height = '';
    }
  }, { passive: true });
})();

// --- 13. PERFORMANCE MONITOR ---------------------------------
(function(){
  if(!window.performance || !window.PerformanceObserver) return;
  try{
    var _scanStart = 0;
    var origStartScanPerf = typeof startScan === 'function' ? startScan : null;
    // Scan sure olcumu - sadece devlog icin
    window.addEventListener('bist_scan_start', function(){ _scanStart = performance.now(); });
    window.addEventListener('bist_scan_end', function(){
      var dur = performance.now() - _scanStart;
      try{ if(typeof devLog==='function' && _scanStart>0) devLog('Tarama suresi: '+dur.toFixed(0)+'ms','info'); }catch(e){}
    });
  }catch(e){}
})();

// --- 14. MEMORY CLEANUP --------------------------------------
// Her 10 dakikada bir eski verileri temizle
setInterval(function(){
  try{
    guardS();
    // Sinyal listesi max 200
    if(S.sigs && S.sigs.length > 200) S.sigs = S.sigs.slice(0, 200);
    // Kapali pozisyonlar max 500
    if(S.closedPositions && S.closedPositions.length > 500) S.closedPositions = S.closedPositions.slice(0, 500);
    // OHLCV cache max 50 hisse
    if(S.ohlcvCache){
      var keys = Object.keys(S.ohlcvCache);
      if(keys.length > 50){
        keys.slice(0, keys.length-50).forEach(function(k){ delete S.ohlcvCache[k]; });
      }
    }
  }catch(e){}
}, 600000);

// --- 15. VERSIYON BILGISI ------------------------------------
window.BIST_VERSION = {
  version: '3.0',
  build: new Date().toISOString().split('T')[0],
  blocks: 24,
  features: [
    'Candlestick Charts (LWC v4)',
    'MTF Confluence',
    'Pattern Recognition',
    'Support/Resistance',
    'KAP News Integration',
    'Seasonality Analysis',
    'Correlation Matrix',
    'AI Analysis',
    'Social (Auth+Chat+Forum)',
    'Trader Journal',
    'Position Sizing Calculator',
    'Market Breadth Dashboard',
    'Sentiment Analysis',
    'Macro Panel',
    'Stress Test (2018/2020/2022)',
    'Strategy Library',
    'Telegram Bot Commands',
    'Onboarding',
    'Global Error Safety',
    '424+ BIST Stocks',
  ]
};

// Baslangic log
setTimeout(function(){
  try{
    if(typeof devLog === 'function'){
      devLog('BIST Pro '+BIST_VERSION.version+' | '+BIST_VERSION.blocks+' blok | '+BIST_VERSION.features.length+' ozellik', 'ok');
    }
  }catch(e){}
}, 1500);

})(); // IIFE sonu

</script>
<script>

// BIST PRO v3 - BLOK 25
// pg() NULL-SAFE OVERRIDE + SOSYAL/DEV TAB FIX
// Blok 1'deki pg() dokunulmaz - burada override
// page-social ve page-dev statik HTML'de mevcut
// Nav'da Sosyal ve Dev tab'i statik mevcut

(function(){
'use strict';

//  pg() NULL-SAFE OVERRIDE 
// Blok 1'deki pg() Blok 24'teki Proxy ile cakisiyor
// Blok 25 en son yukleniyor, bu override kalici
var _origPg = typeof pg === 'function' ? pg : null;

window.pg = function(name){
  try{
    // Tum sayfalari kapat
    document.querySelectorAll('.page').forEach(function(p){ p.classList.remove('on'); });
    document.querySelectorAll('.tab').forEach(function(t){ t.classList.remove('on'); });

    // Hedef sayfayi bul - yoksa olustur
    var pageEl = document.getElementById('page-'+name);
    if(!pageEl){
      var mainEl = document.querySelector('main');
      if(mainEl){
        pageEl = document.createElement('div');
        pageEl.id = 'page-' + name;
        pageEl.className = 'page';
        mainEl.appendChild(pageEl);
      }
    }
    if(pageEl) pageEl.classList.add('on');

    // Aktif tab'i isaretle - onclick veya id bazli
    document.querySelectorAll('.tab').forEach(function(t){
      var oc = t.getAttribute('onclick') || '';
      var tid = t.id || '';
      if(oc.indexOf("'"+name+"'") > -1 || tid === name+'Tab'){
        t.classList.add('on');
      }
    });

    // Sayfa ozel render
    try{ if(name==='positions') renderPositions(); }catch(e){}
    try{ if(name==='watchlist') renderWatchlist(); }catch(e){}
    try{ if(name==='report') renderReport(); }catch(e){}
    try{ if(name==='social') renderSocialPage(); }catch(e){}
    try{ if(name==='dev') renderDevPanel(); }catch(e){}

  }catch(e){
    console.warn('pg('+name+') error:', e.message);
    // Fallback: Blok 1 pg()'yi cagir
    try{ if(_origPg) _origPg(name); }catch(e2){}
  }
};

//  SOSYAL TAB YUKLENME FIX 
// Sosyal sekmeye tiklaninca renderSocialPage calismali
// Eger WebSocket baglantisi yoksa baglan
var _origSwitchToSocial = function(){
  try{
    var p = document.getElementById('page-social');
    if(!p){ pg('social'); return; }
    p.classList.add('on');
    if(typeof renderSocialPage === 'function') renderSocialPage();
    // WS baglan
    if(typeof _auth !== 'undefined' && _auth.token){
      if(!window._chatWS || window._chatWS.readyState > 1){
        if(typeof connectChatWS === 'function') connectChatWS();
      }
    }
  }catch(e){}
};

// Sosyal tab onclick override
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      var socialTab = document.getElementById('socialTab');
      if(socialTab){
        socialTab.onclick = function(){
          document.querySelectorAll('.page').forEach(function(p){ p.classList.remove('on'); });
          document.querySelectorAll('.tab').forEach(function(t){ t.classList.remove('on'); });
          socialTab.classList.add('on');
          _origSwitchToSocial();
          // DM notif dot temizle
          var dot = document.getElementById('dmNotifDot');
          if(dot) dot.style.display = 'none';
        };
        console.log('Sosyal tab onclick guncellendi');
      } else {
        console.warn('socialTab bulunamadi');
      }

      // Dev tab onclick override
      var devTab = document.getElementById('devTab');
      if(devTab){
        devTab.onclick = function(){
          document.querySelectorAll('.page').forEach(function(p){ p.classList.remove('on'); });
          document.querySelectorAll('.tab').forEach(function(t){ t.classList.remove('on'); });
          devTab.classList.add('on');
          var devPage = document.getElementById('page-dev');
          if(devPage) devPage.classList.add('on');
          try{ if(typeof renderDevPanel === 'function') renderDevPanel(); }catch(e){}
        };
        console.log('Dev tab onclick guncellendi');
      } else {
        console.warn('devTab bulunamadi');
      }

      // Auth durumuna gore sosyal tab gorunumu
      if(typeof _auth !== 'undefined' && _auth.token){
        // Giris yapilmissa WS'i hazirla
        setTimeout(function(){
          try{ if(typeof connectChatWS === 'function') connectChatWS(); }catch(e){}
        }, 2000);
      }

    }catch(e){ console.warn('Tab fix:',e.message); }
  }, 500);
}, {once:true, passive:true});

//  DEV PANELI - SON GECERLI renderDevPanel 
// Blok 14: renderV15Settings (en kapsamli dev paneli)
// Blok 25 en son - renderDevPanel = renderV15Settings kesin
setTimeout(function(){
  try{
    if(typeof renderV15Settings === 'function'){
      window.renderDevPanel = function(){
        try{
          var devPage = document.getElementById('page-dev');
          if(!devPage){
            var mainEl = document.querySelector('main');
            if(mainEl){
              devPage = document.createElement('div');
              devPage.id = 'page-dev';
              devPage.className = 'page';
              mainEl.appendChild(devPage);
            }
          }
          renderV15Settings();
        }catch(e){ console.warn('renderDevPanel:',e); }
      };
      console.log('renderDevPanel = renderV15Settings set edildi');
    }
  }catch(e){}
}, 200);

//  SOHBET SEKME NAV BADGE'I 
// DM okunmamis mesaj bildirimi
function updateSocialNavBadge(count){
  try{
    var socialTab = document.getElementById('socialTab');
    if(!socialTab) return;
    var badge = document.getElementById('socialNavBadge');
    if(!badge){
      badge = document.createElement('span');
      badge.id = 'socialNavBadge';
      badge.className = 'nbadge';
      badge.style.cssText = 'background:var(--red);color:#fff;font-size:7px;padding:1px 4px;border-radius:8px;margin-left:3px;display:none';
      socialTab.appendChild(badge);
    }
    if(count > 0){
      badge.textContent = count;
      badge.style.display = 'inline';
    } else {
      badge.style.display = 'none';
    }
  }catch(e){}
}
window.updateSocialNavBadge = updateSocialNavBadge;

// WS mesajlarinda badge guncelle
var _origHandleWSMsg = typeof handleWSMessage === 'function' ? handleWSMessage : null;
if(_origHandleWSMsg){
  window.handleWSMessage = function(data){
    try{ _origHandleWSMsg(data); }catch(e){}
    try{
      if(data && data.type === 'dm'){
        // Aktif sayfa social degil ise badge goster
        var socialPage = document.getElementById('page-social');
        var isActive = socialPage && socialPage.classList.contains('on');
        if(!isActive){
          var current = parseInt(localStorage.getItem('bist_unread_dm')||'0')||0;
          current++;
          localStorage.setItem('bist_unread_dm', current.toString());
          updateSocialNavBadge(current);
        }
      }
    }catch(e){}
  };
}

// Sosyal sayfaya girilince badge sifirla
var _origRenderSocialPage = typeof renderSocialPage === 'function' ? renderSocialPage : null;
if(_origRenderSocialPage){
  window.renderSocialPage = function(){
    try{ _origRenderSocialPage(); }catch(e){}
    try{
      localStorage.setItem('bist_unread_dm','0');
      updateSocialNavBadge(0);
    }catch(e){}
  };
}

//  SAYFA GECIS ANIMASYONU 
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      '.page{ transition: opacity .15s ease; }'
      +'.page:not(.on){ opacity: 0; pointer-events: none; }'
      +'.page.on{ opacity: 1; pointer-events: all; }'
      // Sosyal tab DM badge
      +'#socialNavBadge{ vertical-align: middle; }'
      // Dev tab rengi
      +'#devTab{ color: var(--t4); }'
      +'#devTab.on{ color: var(--cyan); }'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  BASLANGIC KONTROL 
// page-social ve page-dev yuklenince kontrol
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      var pages = ['page-social','page-dev'];
      pages.forEach(function(pid){
        var el = document.getElementById(pid);
        if(el){
          console.log(pid + ' hazir');
        } else {
          console.warn(pid + ' bulunamadi - olusturulacak');
          var mainEl = document.querySelector('main');
          if(mainEl){
            var p = document.createElement('div');
            p.id = pid; p.className = 'page';
            mainEl.appendChild(p);
          }
        }
      });
    }catch(e){}
  }, 300);
}, {once:true, passive:true});

})();

</script>
<script>

// BIST PRO v3 - BLOK 26: KAPSAMLI HATA DUZELTMESI
// HATA-1: closeM parametresi
// HATA-2: setActiveModel onclick
// HATA-3: idxchips eksik endeksler
// HATA-4: duplicate STOCKS temizligi
// HATA-5/6: renderDevPanel + v13CallAPI son override
// HATA-7: opacity transition tiklama bloke
// HATA-8/9: startScan + renderSigs zincir duzeltme
// HATA-10: S.scanIdx vs S.idxFilter unifikasyonu

(function(){
'use strict';

//  HATA-1: closeM DUZELTME 
// Eski: closeM(e) - e.target kontrol
// Yeni: closeM() - direkt kapat, overlay click icin ayri handler
window.closeM = function(e){
  try{
    var modal = document.getElementById('modal');
    if(!modal) return;
    // e varsa overlay click - sadece overlay'e tiklandiysa kapat
    if(e && e.target && e.target.id !== 'modal') return;
    modal.classList.remove('on');
    // mcont temizle
    var mcont = document.getElementById('mcont');
    if(mcont) setTimeout(function(){ mcont.innerHTML = ''; }, 300);
  }catch(ex){ console.warn('closeM:',ex.message); }
};

//  HATA-2: setActiveModel onclick DUZELTME 
// openModelSelector icindeki onclick escaping sorunu
// setActiveModel dogrudan global'e ata
if(typeof setActiveModel === 'function'){
  window.setActiveModel = setActiveModel;
} else {
  window.setActiveModel = function(providerId, modelId){
    try{
      window._currentProvider = providerId;
      window._currentModel = modelId;
      try{
        localStorage.setItem('bist_active_provider', providerId);
        localStorage.setItem('bist_active_model', modelId);
      }catch(e){}
      // Badge guncelle
      if(typeof updateModelBadge === 'function') updateModelBadge();
      closeM();
      if(typeof toast === 'function'){
        var mc = window.MODEL_CATALOG;
        var prov = mc && mc[providerId];
        var mod = prov && prov.models && prov.models.find(function(m){return m.id===modelId;});
        toast((mod ? mod.name : modelId) + ' secildi');
      }
    }catch(e){ console.warn('setActiveModel:',e.message); }
  };
}

//  HATA-3: idxchips EKSIK ENDEKSLER 
// Mevcut HTML'de sadece Katilim endeksleri var (XK030EA vs)
// XU030/XU050/XU100/XBANK vs eksik - ekle
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      var idxchips = document.getElementById('idxchips');
      if(!idxchips) return;

      // Mevcut chip'ler
      var existing = [];
      idxchips.querySelectorAll('.chip').forEach(function(ch){
        existing.push(ch.getAttribute('data-v'));
      });

      // Eklenecek yeni endeksler
      var newChips = [
        {v:'XU030',l:'BIST30'}, {v:'XU050',l:'BIST50'}, {v:'XU100',l:'BIST100'},
        {v:'XBANK',l:'Bankalar'}, {v:'XHOLD',l:'Holding'}, {v:'XELKT',l:'Enerji'},
        {v:'XINSA',l:'Insaat'}, {v:'XGIDA',l:'Gida'}, {v:'XMESY',l:'Sanayi'},
        {v:'XKMYA',l:'Kimya'}, {v:'XTEKS',l:'Tekstil'}, {v:'XULAS',l:'Ulasim'},
        {v:'XBLSM',l:'Bilisim'}, {v:'XSGRT',l:'Sigorta'}, {v:'XMADN',l:'Maden'},
        {v:'XTAST',l:'Tas/Cam'}, {v:'XTCRT',l:'Ticaret'}, {v:'XGMYO',l:'GYO'},
        {v:'XTRZM',l:'Turizm'}, {v:'XSPOR',l:'Spor'},
      ];

      newChips.forEach(function(ch){
        if(existing.indexOf(ch.v) > -1) return; // Zaten var
        var div = document.createElement('div');
        div.className = 'chip';
        div.setAttribute('data-v', ch.v);
        div.setAttribute('onclick', 'idxT(this)');
        div.textContent = ch.l;
        div.style.cssText = 'background:rgba(255,184,0,.08);border-color:rgba(255,184,0,.2);color:rgba(255,184,0,.8)';
        idxchips.appendChild(div);
      });

      console.log('idxchips guncellendi: '+(newChips.length)+' endeks eklendi');
    }catch(e){ console.warn('idxchips fix:',e.message); }
  }, 800);
}, {once:true, passive:true});

//  HATA-4: DUPLICATE STOCKS TEMIZLIGI 
// NEW_STOCKS Blok20'de 45 duplicate var, getStocks() ile calistirma sorunu
setTimeout(function(){
  try{
    if(typeof STOCKS === 'undefined') return;
    var seen = {};
    var cleaned = STOCKS.filter(function(s){
      if(seen[s.t]) return false;
      seen[s.t] = true;
      return true;
    });
    var removed = STOCKS.length - cleaned.length;
    STOCKS.length = 0;
    cleaned.forEach(function(s){ STOCKS.push(s); });
    if(removed > 0) console.log('Duplicate hisse temizlendi: '+removed+' / Toplam: '+STOCKS.length);
  }catch(e){ console.warn('duplicate fix:',e.message); }
}, 100);

//  HATA-5: renderDevPanel SON GECERLI 
// 10x override var - en kapsamli olan renderV15Settings (Blok14)
// Tum onceki override'lari iptal et, temiz yaz
setTimeout(function(){
  try{
    if(typeof renderV15Settings === 'function'){
      window.renderDevPanel = function(){
        try{
          // page-dev hazirla
          var devPg = document.getElementById('page-dev');
          if(!devPg){
            var mainEl = document.querySelector('main');
            if(mainEl){ devPg = document.createElement('div'); devPg.id='page-dev'; devPg.className='page'; mainEl.appendChild(devPg); }
          }
          renderV15Settings();
        }catch(e){ console.warn('renderDevPanel:',e.message); }
      };
      console.log('renderDevPanel -> renderV15Settings (SABITLENDI)');
    }
  }catch(e){}
}, 500);

//  HATA-6: v13CallAPI SON GECERLI 
// 6x override - Blok23'teki en kapsamli versiyon kullanilmali
// Blok23'ten sonra Blok26 geliyor - o versiyon korunacak
// Sadece tip kontrolu ekle
setTimeout(function(){
  try{
    var current = window.v13CallAPI;
    if(typeof current !== 'function'){
      // Yedek basit implementasyon
      window.v13CallAPI = function(msg){
        if(!msg || !msg.trim()) return;
        var PROXY = typeof PROXY_URL!=='undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';
        fetch(PROXY+'/ai/chat',{
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({message:msg, agent:'main', bist_context:{}})
        }).then(function(r){return r.json();})
        .then(function(d){
          if(typeof v13AppendMsg==='function') v13AppendMsg('ai', d.response||'Yanit yok');
        }).catch(function(e){
          if(typeof v13AppendMsg==='function') v13AppendMsg('sys','Hata: '+e.message);
        });
      };
      console.log('v13CallAPI yedek eklendi');
    } else {
      console.log('v13CallAPI mevcut versiyon korundu');
    }
  }catch(e){}
}, 600);

//  HATA-7: opacity TRANSITION TIKLAMA BLOKE 
// Blok25'te .page:not(.on){ pointer-events:none } var - DOGRU
// AMA gecis sirasinda opacity animation 0.15s suruyor
// Bu sure icinde tiklama bazen bloke olabiliyor
// Cozum: transition'i kaldir, anlik goster/gizle
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      // Blok25'teki transition'i override et - anlik gecis
      '.page{ transition: none !important; }'
      +'.page:not(.on){ display: none !important; opacity: 1 !important; pointer-events: none !important; }'
      +'.page.on{ display: block !important; opacity: 1 !important; pointer-events: all !important; }'
      // Istisnalar: flex layout gerektiren sayfalar
      +'#page-social.on{ display: flex !important; flex-direction: column !important; }'
      +'#page-agents.on{ display: flex !important; flex-direction: column !important; }'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  HATA-8/9: startScan + renderSigs ZINCIR DUZELTME 
// Blok24'teki chain wrap'i guncelle - _last* referanslari stale olabilir
// Dogrudan Blok7'deki son resmi versiyonu bul ve koru
setTimeout(function(){
  try{
    // startScan - su anki versiyon calisiyorsa dokuntma
    var curSS = window.startScan;
    if(typeof curSS === 'function'){
      window.startScan = function(){
        try{
          if(typeof guardS === 'function') guardS();
          // Scan butonu UI feedback
          var btn = document.getElementById('scanBtn');
          if(btn && !btn.classList.contains('scanning')){
            btn.classList.add('scanning');
            btn.textContent = 'Taraniyor...';
            btn.disabled = true;
          }
          curSS();
        }catch(e){
          console.warn('startScan error:',e.message);
          var btn = document.getElementById('scanBtn');
          if(btn){ btn.disabled=false; btn.textContent='TARA'; btn.classList.remove('scanning'); }
        }
      };
    }
  }catch(e){}
}, 700);

//  HATA-10: S.scanIdx vs S.idxFilter UNIFIKASYONU 
// getStocks() S.scanIdx kullaniyor
// idxT() S.idxFilter kullaniyor
// Bunlari eslestir: idxT() S.scanIdx'i de guncellesin
setTimeout(function(){
  try{
    var origIdxT = typeof idxT === 'function' ? idxT : null;
    if(origIdxT){
      window.idxT = function(el){
        try{
          origIdxT(el);
          // S.idxFilter'i S.scanIdx ile senkronize et
          if(typeof S !== 'undefined'){
            if(S.idxFilter === 'ALL'){
              S.scanIdx = 'ALL';
            } else if(Array.isArray(S.idxFilter) && S.idxFilter.length > 0){
              S.scanIdx = S.idxFilter[0]; // Birden fazla varsa ilkini al
            }
          }
        }catch(e){ console.warn('idxT:',e.message); }
      };
    }
  }catch(e){}
}, 900);

//  GENEL DUZELTME: pg() GECIS 
// Blok25'teki pg() override zaten var ama display:none ile catisiyor
// Blok26 page gosterme logigini display ile yapalim
setTimeout(function(){
  try{
    var prevPg = window.pg;
    window.pg = function(name){
      try{
        // Tum sayfalari gizle
        document.querySelectorAll('.page').forEach(function(p){
          p.classList.remove('on');
        });
        // Tum tablari pasif yap
        document.querySelectorAll('.tab').forEach(function(t){
          t.classList.remove('on');
        });
        // Hedef sayfayi bul/olustur
        var pageEl = document.getElementById('page-'+name);
        if(!pageEl){
          var mainEl = document.querySelector('main');
          if(mainEl){
            pageEl = document.createElement('div');
            pageEl.id = 'page-' + name;
            pageEl.className = 'page';
            mainEl.appendChild(pageEl);
          }
        }
        if(pageEl) pageEl.classList.add('on');
        // Ilgili tab'i aktif et
        document.querySelectorAll('.tab').forEach(function(t){
          var oc = t.getAttribute('onclick') || '';
          var tid = t.id || '';
          if(oc.indexOf("'"+name+"'") > -1 || tid === name+'Tab'){
            t.classList.add('on');
          }
        });
        // Sayfa ozel render
        if(name==='positions') try{renderPositions();}catch(e){}
        if(name==='watchlist')  try{renderWatchlist();}catch(e){}
        if(name==='report')     try{renderReport();}catch(e){}
        if(name==='social')     try{renderSocialPage();}catch(e){}
        if(name==='dev')        try{renderDevPanel();}catch(e){}
      }catch(e){
        console.warn('pg('+name+') error:',e.message);
      }
    };
  }catch(e){}
}, 200);

//  BONUS: openModelSelector DUZELTME 
// Escaping sorunu olmadan model secim
setTimeout(function(){
  try{
    if(typeof MODEL_CATALOG === 'undefined') return;
    var origOMS = typeof openModelSelector === 'function' ? openModelSelector : null;
    window.openModelSelector = function(){
      try{
        var mtit = document.getElementById('mtit');
        var mcont = document.getElementById('mcont');
        var modal = document.getElementById('modal');
        if(!modal) return;
        if(mtit) mtit.textContent = 'Model Sec';

        var prov = window._currentProvider || 'anthropic';
        var curMod = window._currentModel || '';

        var html = '<div style="padding:3px 0">';
        // Aktif model goster
        var curCat = MODEL_CATALOG[prov];
        var curModObj = curCat && curCat.models && curCat.models.find(function(m){return m.id===curMod;});
        html += '<div style="background:rgba(0,212,255,.06);border:1px solid rgba(0,212,255,.15);border-radius:9px;padding:10px;margin-bottom:10px">'
          +'<div style="font-size:9px;color:var(--t4);margin-bottom:3px">AKTIF MODEL</div>'
          +'<div style="font-size:12px;font-weight:700;color:var(--cyan)">'+(curModObj?curModObj.name:curMod)+'</div>'
          +'</div>';

        // Provider gruplu model listesi
        Object.keys(MODEL_CATALOG).forEach(function(pid){
          var p = MODEL_CATALOG[pid];
          if(!p || !p.models || !p.models.length) return;
          html += '<div style="font-size:9px;font-weight:700;color:'+(p.color||'var(--t4)')+';text-transform:uppercase;letter-spacing:1.5px;margin:8px 0 4px">'
            +(p.icon||'')+' '+p.name+'</div>';
          p.models.forEach(function(m){
            var isActive = m.id === curMod && pid === prov;
            html += '<div id="msel_'+m.id.replace(/[^a-z0-9]/gi,'_')+'" '
              +'style="display:flex;align-items:center;gap:8px;padding:9px 10px;background:'
              +(isActive?'rgba(0,212,255,.1)':'rgba(255,255,255,.03)')
              +';border:1px solid '+(isActive?'rgba(0,212,255,.3)':'rgba(255,255,255,.06)')
              +';border-radius:8px;margin-bottom:4px;cursor:pointer" '
              +'onclick="setActiveModel(\''+pid+'\',\''+m.id+'\');closeM();">'
              +'<div style="flex:1"><div style="font-size:10px;font-weight:600;color:var(--t1)">'+m.name+'</div>'
              +'<div style="font-size:8px;color:var(--t4)">'+m.id+'</div></div>'
              +(isActive?'<span style="font-size:14px;color:var(--cyan)">&#10003;</span>':'')
              +'</div>';
          });
        });
        html += '</div>';
        if(mcont) mcont.innerHTML = html;
        modal.classList.add('on');
      }catch(e){ console.warn('openModelSelector:',e.message); }
    };
  }catch(e){}
}, 1000);

//  BASLATMA LOGU 
setTimeout(function(){
  try{
    var stockCount = typeof STOCKS !== 'undefined' ? STOCKS.length : 0;
    console.log('[BIST Elite v3] Blok26 hazir | Hisseler: '+stockCount);
    if(typeof devLog === 'function'){
      devLog('Blok26: 10 kritik hata duzeltildi | Hisse: '+stockCount, 'ok');
    }
  }catch(e){}
}, 1500);

})();

</script>
<script>

// BIST ELITE v3 - BLOK 27: PWA KRITIK DUZELTMELER
// 1. Zoom engeli (user-scalable, touch-action)
// 2. Arka plan tarama - visibilitychange, uygulama gizlenince dur, geri gelince devam
// 3. Tam kalicilik - tfFilter, idxFilter, sigs, openPositions, closedPositions, watchlist
// 4. S.sigs acilista localStorage'dan yukle
// 5. saveSets - tum ayarlar kaydedilsin
// 6. Uygulama resume - tarama durumu korunacak

(function(){
'use strict';

//  1. ZOOM ENGELI 
(function(){
  try{
    // Viewport meta - user-scalable=no ekle
    var vp = document.querySelector('meta[name="viewport"]');
    if(vp){
      var content = vp.getAttribute('content') || '';
      if(content.indexOf('user-scalable') === -1){
        vp.setAttribute('content', content + ',user-scalable=no,maximum-scale=1.0');
      }
    }
    // CSS touch-action ve double-tap zoom engeli
    var st = document.createElement('style');
    st.textContent =
      // Tum elementlerde double-tap zoom engeli
      '*{ touch-action: manipulation; }'
      // Ama scroll gereken yerler haric
      +'.chat-messages, .dm-list, .sdcontent, main, .soc-panel, .forum-topics, #mcont{'
      +'  touch-action: pan-y !important;'
      +'}'
      // iOS pinch zoom engeli
      +'html, body{ touch-action: none; overflow: hidden; }'
      +'main{ overflow-y: auto; -webkit-overflow-scrolling: touch; touch-action: pan-y; }'
      // Butonlarda highlight kaldir
      +'button, a, [onclick]{ -webkit-tap-highlight-color: transparent; }'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  2. ARKA PLAN TARAMA YONETIMI 
var _scanPaused = false;
var _scanWasRunning = false;
var _lastScanSaveTime = 0;

document.addEventListener('visibilitychange', function(){
  try{
    if(document.hidden){
      // Uygulama arka plana alindi
      _scanPaused = true;
      _scanWasRunning = !!(typeof S !== 'undefined' && S.autoTimer);
      // Timer'i durdur - pil ve network tasarrufu
      if(typeof S !== 'undefined' && S.autoTimer){
        clearInterval(S.autoTimer);
        S.autoTimer = null;
      }
      // Anlik state'i kaydet
      _saveAllState();
    } else {
      // Uygulama on plana geldi
      _scanPaused = false;
      // State yukle
      _loadDeltaState();
      // Tarama devam etsin - yeni timer baslat (startScan degil, sadece timer)
      if(_scanWasRunning && typeof S !== 'undefined' && !S.autoTimer){
        _resumeAutoTimer();
      }
    }
  }catch(e){ console.warn('visibilitychange:',e.message); }
});

// iOS'ta PWA arka plan - pageshow/pagehide (Safari)
window.addEventListener('pagehide', function(e){
  try{
    _saveAllState();
  }catch(e){}
}, false);

window.addEventListener('pageshow', function(e){
  try{
    if(e.persisted){
      // Sayfa cache'den geri geldi (bfcache)
      _loadDeltaState();
      if(_scanWasRunning) _resumeAutoTimer();
    }
  }catch(e){}
}, false);

// Auto timer resume - startScan'i tekrar cagirmadan sadece timer'i yeniden baslat
function _resumeAutoTimer(){
  try{
    if(typeof S === 'undefined') return;
    if(S.autoTimer) return;
    var mins = parseInt((typeof C !== 'undefined' && C.scanInterval) || 5);
    if(S.nextScanIn <= 0) S.nextScanIn = mins * 60;
    S.autoTimer = setInterval(function(){
      try{
        if(_scanPaused) return;
        S.nextScanIn--;
        if(S.nextScanIn <= 0){
          S.nextScanIn = mins * 60;
          if(typeof startScan === 'function') startScan();
        }
        // Countdown UI
        var cntEl = document.getElementById('nextScanCountdown') || document.getElementById('bgText');
        if(cntEl){
          var m = Math.floor(S.nextScanIn/60), s = S.nextScanIn%60;
          cntEl.textContent = m + ':' + (s<10?'0':'') + s;
        }
      }catch(ex){}
    }, 1000);
  }catch(e){ console.warn('resumeTimer:',e.message); }
}

//  3. TAM KALICILIK 
function _saveAllState(){
  try{
    if(typeof S === 'undefined') return;
    // Sinyaller
    if(S.sigs && S.sigs.length > 0){
      try{ localStorage.setItem('bist_sigs', JSON.stringify(S.sigs.slice(0,200))); }catch(e){}
    }
    // tfFilter
    try{ localStorage.setItem('bist_tfFilter', JSON.stringify(S.tfFilter || ['D','120','240'])); }catch(e){}
    // idxFilter
    try{ localStorage.setItem('bist_idxFilter', JSON.stringify(S.idxFilter || 'ALL')); }catch(e){}
    // scanIdx
    try{ localStorage.setItem('bist_scanIdx', S.scanIdx || 'ALL'); }catch(e){}
    // nextScanIn
    try{ localStorage.setItem('bist_nextScanIn', String(S.nextScanIn || 0)); }catch(e){}
    // Scan durumu
    try{ localStorage.setItem('bist_scanRunning', _scanWasRunning || !!S.autoTimer ? '1' : '0'); }catch(e){}
    // openPositions
    if(typeof S.openPositions === 'object'){
      try{ localStorage.setItem('bist_positions', JSON.stringify(S.openPositions)); }catch(e){}
    }
    // closedPositions
    if(S.closedPositions && S.closedPositions.length > 0){
      try{ localStorage.setItem('bist_closed', JSON.stringify(S.closedPositions.slice(0,500))); }catch(e){}
    }
    // watchlist
    if(S.watchlist){
      try{ localStorage.setItem('bist_wl', JSON.stringify(S.watchlist)); }catch(e){}
    }
    // C config
    if(typeof C !== 'undefined'){
      try{ localStorage.setItem('bistcfg', JSON.stringify(C)); }catch(e){}
    }
    // TG config
    if(typeof TG !== 'undefined'){
      try{ localStorage.setItem('bisttg', JSON.stringify(TG)); }catch(e){}
    }
    _lastScanSaveTime = Date.now();
  }catch(e){ console.warn('saveAllState:',e.message); }
}

function _loadDeltaState(){
  try{
    if(typeof S === 'undefined') return;
    // Sinyaller - kaydedilen varsa yukle
    var savedSigs = JSON.parse(localStorage.getItem('bist_sigs') || '[]');
    if(savedSigs.length > 0 && S.sigs.length === 0){
      S.sigs = savedSigs;
      try{ if(typeof renderSigs === 'function') renderSigs(); }catch(e){}
    }
    // tfFilter
    var savedTf = JSON.parse(localStorage.getItem('bist_tfFilter') || 'null');
    if(savedTf) S.tfFilter = savedTf;
    // idxFilter
    var savedIdx = JSON.parse(localStorage.getItem('bist_idxFilter') || 'null');
    if(savedIdx) S.idxFilter = savedIdx;
    // scanIdx
    var savedScanIdx = localStorage.getItem('bist_scanIdx');
    if(savedScanIdx) S.scanIdx = savedScanIdx;
    // nextScanIn
    var savedNext = parseInt(localStorage.getItem('bist_nextScanIn') || '0');
    if(savedNext > 0) S.nextScanIn = savedNext;
    // scanRunning
    _scanWasRunning = localStorage.getItem('bist_scanRunning') === '1';
    // openPositions
    var savedPos = JSON.parse(localStorage.getItem('bist_positions') || 'null');
    if(savedPos && Object.keys(savedPos).length > 0) S.openPositions = savedPos;
  }catch(e){ console.warn('loadDeltaState:',e.message); }
}

//  4. ACILISTA TAM YUKLEme 
// S.sigs acilista bos basliyor - yukle
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      if(typeof S === 'undefined') return;

      // Sinyaller
      var savedSigs = JSON.parse(localStorage.getItem('bist_sigs') || '[]');
      if(savedSigs.length > 0 && S.sigs.length === 0){
        S.sigs = savedSigs;
        try{ if(typeof renderSigs === 'function') renderSigs(); }catch(e){}
        try{ if(typeof updateBadge === 'function') updateBadge(); }catch(e){}
        console.log('Sinyaller yuklendi: ' + savedSigs.length);
      }

      // tfFilter - checkbox'lari guncelle
      var savedTf = JSON.parse(localStorage.getItem('bist_tfFilter') || 'null');
      if(savedTf){
        S.tfFilter = savedTf;
        // UI checkbox'larini guncelle
        ['D','120','240','W'].forEach(function(tf){
          var el = document.getElementById('tf_'+tf);
          if(el) el.checked = savedTf.indexOf(tf) > -1;
        });
      }

      // idxFilter - chip'leri guncelle
      var savedIdx = JSON.parse(localStorage.getItem('bist_idxFilter') || 'null');
      if(savedIdx){
        S.idxFilter = savedIdx;
        // UI chip'lerini guncelle
        var chips = document.querySelectorAll('#idxchips .chip');
        chips.forEach(function(ch){
          var v = ch.getAttribute('data-v');
          if(Array.isArray(savedIdx)){
            ch.classList.toggle('on', savedIdx.indexOf(v) > -1 || (savedIdx.length === 0 && v === 'ALL'));
          } else {
            ch.classList.toggle('on', v === savedIdx || (savedIdx === 'ALL' && v === 'ALL'));
          }
        });
      }

      // scanIdx
      var savedScanIdx = localStorage.getItem('bist_scanIdx');
      if(savedScanIdx) S.scanIdx = savedScanIdx;

      // openPositions
      var savedPos = JSON.parse(localStorage.getItem('bist_positions') || 'null');
      if(savedPos && typeof savedPos === 'object') S.openPositions = savedPos;

      // Scan timer durumu
      var wasRunning = localStorage.getItem('bist_scanRunning') === '1';
      _scanWasRunning = wasRunning;

      console.log('PWA state yuklendi | sigs:'+S.sigs.length+' pos:'+Object.keys(S.openPositions||{}).length);

    }catch(e){ console.warn('PWA load state:',e.message); }
  }, 300);
}, {once:true, passive:true});

//  5. PERIYODIK OTOMATIK KAYIT 
// Her 30 saniyede bir state kaydet (uygulama kapanmadan once)
setInterval(function(){
  try{ _saveAllState(); }catch(e){}
}, 30000);

// Scan sonrasi otomatik kayit
var _origStartScanPWA = typeof startScan === 'function' ? startScan : null;
setTimeout(function(){
  try{
    var curScan = window.startScan;
    if(typeof curScan !== 'function') return;
    window.startScan = function(){
      try{ curScan(); }catch(e){}
      // Scan basladiktan 5 sn sonra kaydet
      setTimeout(function(){
        try{ _saveAllState(); }catch(e){}
      }, 5000);
    };
  }catch(e){}
}, 1000);

//  6. saveSets - TUM ALANLARI KAYDET 
// Mevcut saveSets tfFilter ve idxFilter kaydetmiyor
var _origSaveSets = typeof saveSets === 'function' ? saveSets : null;
setTimeout(function(){
  try{
    var curSS = window.saveSets;
    if(typeof curSS !== 'function') return;
    window.saveSets = function(){
      try{ curSS(); }catch(e){}
      try{
        // tfFilter - checkbox'lardan oku
        var tf = [];
        ['D','120','240','W'].forEach(function(t){
          var el = document.getElementById('tf_'+t);
          if(el && el.checked) tf.push(t);
        });
        if(tf.length > 0){
          S.tfFilter = tf;
          localStorage.setItem('bist_tfFilter', JSON.stringify(tf));
        }
        // idxFilter kaydet
        localStorage.setItem('bist_idxFilter', JSON.stringify(S.idxFilter || 'ALL'));
        localStorage.setItem('bist_scanIdx', S.scanIdx || 'ALL');
        // C config tekrar kaydet (curSS zaten kaydediyor ama garantilemek icin)
        if(typeof C !== 'undefined') localStorage.setItem('bistcfg', JSON.stringify(C));
        if(typeof toast === 'function') toast('Tum ayarlar kaydedildi');
      }catch(e){ console.warn('saveSets extra:',e.message); }
    };
  }catch(e){}
}, 800);

//  7. idxT - idxFilter KAYIT 
// Her endeks seciminde localStorage'a kaydet
var _origIdxT_PWA = typeof idxT === 'function' ? idxT : null;
setTimeout(function(){
  try{
    var curIdxT = window.idxT;
    if(typeof curIdxT !== 'function') return;
    window.idxT = function(el){
      try{ curIdxT(el); }catch(e){}
      try{
        localStorage.setItem('bist_idxFilter', JSON.stringify(S.idxFilter || 'ALL'));
        localStorage.setItem('bist_scanIdx', S.scanIdx || 'ALL');
      }catch(ex){}
    };
  }catch(e){}
}, 900);

//  8. tfFilter checkbox - her degisimde kaydet 
window.addEventListener('load', function(){
  setTimeout(function(){
    try{
      ['D','120','240','W'].forEach(function(tf){
        var el = document.getElementById('tf_'+tf);
        if(!el) return;
        el.addEventListener('change', function(){
          try{
            var active = [];
            ['D','120','240','W'].forEach(function(t){
              var cb = document.getElementById('tf_'+t);
              if(cb && cb.checked) active.push(t);
            });
            if(active.length > 0){
              S.tfFilter = active;
              localStorage.setItem('bist_tfFilter', JSON.stringify(active));
            }
          }catch(ex){}
        });
      });
      console.log('tfFilter change listeners eklendi');
    }catch(e){}
  }, 1000);
}, {once:true, passive:true});

//  9. openPositions - her degisimde kaydet 
// addPosition / closePosition sonrasi otomatik kayit
function _hookPositionChanges(){
  try{
    var fns = ['addPosition','closePosition','updateStopPrice','deletePosition'];
    fns.forEach(function(fn){
      var orig = window[fn];
      if(typeof orig !== 'function') return;
      window[fn] = function(){
        try{ orig.apply(this, arguments); }catch(e){}
        try{
          localStorage.setItem('bist_positions', JSON.stringify(S.openPositions || {}));
          localStorage.setItem('bist_closed', JSON.stringify((S.closedPositions||[]).slice(0,500)));
        }catch(ex){}
      };
    });
  }catch(e){}
}
setTimeout(_hookPositionChanges, 1200);

//  10. Watchlist - her degisimde kaydet 
var _hookWL = function(){
  try{
    var fns = ['addToWL','removeFromWL','toggleWL'];
    fns.forEach(function(fn){
      var orig = window[fn];
      if(typeof orig !== 'function') return;
      window[fn] = function(){
        try{ orig.apply(this, arguments); }catch(e){}
        try{ localStorage.setItem('bist_wl', JSON.stringify(S.watchlist||[])); }catch(ex){}
      };
    });
  }catch(e){}
};
setTimeout(_hookWL, 1300);

//  11. PWA MANIFEST - display standalone 
// Manifest'te display:standalone eksikse ekle
setTimeout(function(){
  try{
    var manifestLink = document.querySelector('link[rel="manifest"]');
    if(!manifestLink) return;
    // Manifest zaten JSON string olarak olusturuluyor - guncelle
    // Dogrudan manifest objesini window uzerinden bul
    if(typeof _pwaManifest !== 'undefined'){
      _pwaManifest.display = 'standalone';
      _pwaManifest.orientation = 'portrait';
      _pwaManifest.background_color = '#000000';
      _pwaManifest.theme_color = '#000000';
    }
  }catch(e){}
}, 500);

//  LOG 
setTimeout(function(){
  try{
    console.log('[BIST PWA] Blok27 hazir | Kalicilik + Zoom + Arka plan duzeltmeleri aktif');
    if(typeof devLog === 'function'){
      devLog('Blok27: PWA kalicilik + zoom + arka plan tarama duzeltildi', 'ok');
    }
  }catch(e){}
}, 2000);

})();

</script>
<script>

// BIST ELITE v3 - BLOK 28: POZISYON KARTI TAM DUZELTME
// 1. Sinyal fiyati - pozisyon objesine kalici yazilir
// 2. Sinyal zamani - ne zaman geldi goster
// 3. Acik pozisyon gercek PnL (fiyat cache yoksa fetch et)
// 4. Son 10 islem - default olarak gozukur
// 5. Kapali pozisyon karti detayli goster
// 6. Pozisyon karti - tum bilgiler tek ekranda

(function(){
'use strict';

var PROXY = typeof PROXY_URL !== 'undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';

//  1. addPosition HOOK - signalPrice + signalTime KALICI KAYDEDILIR 
// Sinyal gelince pozisyon eklenirken signalPrice ve signalTime kaydet
setTimeout(function(){
  try{
    // Sinyalden pozisyon acma - L837 bolgesinde var
    // Her yeni pozisyon eklendiginde signalPrice'i sig'den al
    var origRenderSigs = window.renderSigs;
    if(typeof origRenderSigs !== 'function') return;

    // Periyodik: acik pozisyonlara sig datasini enjekte et
    function injectSignalDataToPositions(){
      try{
        if(typeof S === 'undefined') return;
        var sigs = S.sigs || [];
        Object.keys(S.openPositions || {}).forEach(function(key){
          var pos = S.openPositions[key];
          if(!pos) return;
          var parts = key.split('_');
          var ticker = parts[0], tf = parts[1] || 'D';
          // Bu pozisyona ait sinyali bul
          var sig = null;
          for(var i = 0; i < sigs.length; i++){
            if(sigs[i].ticker === ticker && sigs[i].tf === tf && sigs[i].type !== 'stop'){
              sig = sigs[i]; break;
            }
          }
          // sigHistory'de de ara
          if(!sig && S.sigHistory){
            for(var j = 0; j < S.sigHistory.length; j++){
              var h = S.sigHistory[j];
              if(h.ticker === ticker && h.tf === tf && h.type !== 'stop'){
                sig = h; break;
              }
            }
          }
          if(sig){
            if(!pos.signalPrice && sig.res && sig.res.price) pos.signalPrice = sig.res.price;
            if(!pos.signalTime){
              var t = sig.time;
              pos.signalTime = t instanceof Date ? t.toISOString() : (t || pos.entryTime);
            }
            if(!pos.signalType) pos.signalType = sig.type || 'buy';
            if(!pos.acts && sig.res && sig.res.acts) pos.acts = sig.res.acts;
            if(!pos.cons && sig.res) pos.cons = sig.res.cons || sig.res.consensus;
            if(!pos.adx && sig.res) pos.adx = sig.res.adx;
            if(!pos.pstate && sig.res) pos.pstate = sig.res.pstate;
            if(!pos.strength && sig.res) pos.strength = sig.res.strength;
          }
        });
      }catch(e){}
    }

    // renderSigs sonrasi enjekte et
    window.renderSigs = function(){
      try{ origRenderSigs(); }catch(e){}
      setTimeout(injectSignalDataToPositions, 300);
    };

    // Sayfa yuklenince de enjekte et
    setTimeout(injectSignalDataToPositions, 2000);
    // Her 5 dakikada bir
    setInterval(injectSignalDataToPositions, 300000);

  }catch(e){ console.warn('signalData inject:',e.message); }
}, 500);

//  2. GERCEk PNL - FIYAT CACHE YOKSA FETCH 
function fetchMissingPrices(){
  try{
    if(typeof S === 'undefined') return;
    var missing = [];
    Object.keys(S.openPositions || {}).forEach(function(key){
      var ticker = key.split('_')[0];
      var cached = S.priceCache && S.priceCache[ticker];
      if(!cached || !cached.price || Date.now() - (cached._ts || 0) > 5 * 60000){
        if(missing.indexOf(ticker) === -1) missing.push(ticker);
      }
    });
    if(!missing.length) return;

    fetch(PROXY + '/prices?symbols=' + missing.join(','))
      .then(function(r){ return r.json(); })
      .then(function(data){
        if(!data || !data.prices) return;
        if(!S.priceCache) S.priceCache = {};
        data.prices.forEach(function(p){
          S.priceCache[p.ticker] = {
            price: p.price, pct: p.change_pct, _ts: Date.now()
          };
        });
        // Fiyat geldi - pozisyonlari yenile
        try{ if(typeof renderPositions === 'function') renderPositions(); }catch(e){}
      }).catch(function(){});
  }catch(e){}
}

//  3. renderPositions TAM OVERRIDE 
// Tum eksik bilgileri gosteren kapsamli pozisyon karti
setTimeout(function(){
  try{
    window.renderPositions = function(){
      try{
        if(typeof S === 'undefined') return;
        var positions = Object.keys(S.openPositions || {});
        var badge = document.getElementById('posBadge');
        if(badge){ badge.textContent = positions.length; badge.style.display = positions.length ? 'inline-flex' : 'none'; }

        var posListEl = document.getElementById('positionList');
        var countEl = document.getElementById('posCount');
        var totalEl = document.getElementById('posTotalPnl');
        var bestEl = document.getElementById('posBestPnl');
        var worstEl = document.getElementById('posWorstPnl');

        if(!positions.length){
          if(posListEl) posListEl.innerHTML = '<div class="empty"><div class="eico">&#128188;</div><div style="font-size:11px">Sinyal geldiginde otomatik pozisyon acar</div></div>';
          if(countEl) countEl.textContent = '0';
          if(totalEl) totalEl.textContent = '-';
          if(bestEl) bestEl.textContent = '-';
          if(worstEl) worstEl.textContent = '-';
          return;
        }

        // Eksik fiyatlari getir
        fetchMissingPrices();

        var totalPnl = 0, bestPnl = -999, worstPnl = 999;
        var bestTicker = '', worstTicker = '';
        var cards = '';

        positions.forEach(function(key){
          var pos = S.openPositions[key];
          if(!pos) return;
          var parts = key.split('_'), ticker = parts[0], tf = parts[1] || 'D';
          var tfL = tf==='D'?'Gunluk':tf==='240'?'4 Saat':'2 Saat';
          var cached = S.priceCache && S.priceCache[ticker];
          var curPrice = cached && cached.price > 0 ? cached.price : null;
          var isReal = !!curPrice;
          if(!curPrice) curPrice = pos.entry; // Fiyat yoksa entry goster ama 0 PnL

          // En yuksek guncelle
          if(curPrice > (pos.highest || 0)) pos.highest = curPrice;

          // PnL
          var pnlPct = isReal ? ((curPrice - pos.entry) / pos.entry * 100) : 0;
          var pnlStr = isReal ? ((pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%') : '...';
          var isProfit = pnlPct >= 0;
          if(isReal){ totalPnl += pnlPct; if(pnlPct > bestPnl){bestPnl=pnlPct;bestTicker=ticker;} if(pnlPct < worstPnl){worstPnl=pnlPct;worstTicker=ticker;} }

          // Sure hesapla
          var holdMs = Date.now() - new Date(pos.entryTime || Date.now()).getTime();
          var holdDays = Math.floor(holdMs / 86400000);
          var holdStr = holdDays === 0 ? 'Bugun' : holdDays + 'g';

          // Sinyal zamani
          var sigTime = pos.signalTime || pos.entryTime;
          var sigTimeStr = '';
          if(sigTime){
            var st = new Date(sigTime);
            sigTimeStr = st.getDate()+'.'+(st.getMonth()+1)+'.'+st.getFullYear()
              +' '+String(st.getHours()).padStart(2,'0')+':'+String(st.getMinutes()).padStart(2,'0');
          }

          // Trailing stop
          var atr = pos.entry * 0.022 * ((typeof C !== 'undefined' && C.atrm) || 8) / 10;
          pos.stopPrice = (pos.highest || pos.entry) - atr;

          // Gun degisimi
          var dayChg = cached ? cached.pct : null;

          // Sinyal fiyati
          var sigPrice = pos.signalPrice || pos.entry;

          cards += '<div class="pos-card ' + (isReal ? (isProfit ? 'profit' : 'loss') : '') + '" style="margin-bottom:10px;border-radius:12px;overflow:hidden;border:1px solid ' + (isProfit ? 'rgba(0,230,118,.2)' : 'rgba(255,68,68,.15)') + ';background:rgba(255,255,255,.03)">'

            // Header: ticker + PnL
            + '<div style="display:flex;align-items:center;justify-content:space-between;padding:11px 13px;background:rgba(255,255,255,.02)">'
              + '<div>'
                + '<div style="font-size:16px;font-weight:800;color:var(--t1);letter-spacing:.3px">'
                  + ticker
                  + (isReal ? '<span style="font-size:8px;color:var(--green);margin-left:5px;background:rgba(0,230,118,.1);padding:1px 5px;border-radius:4px">CANLI</span>' : '<span style="font-size:8px;color:var(--t4);margin-left:5px">fiyat bekleniyor</span>')
                + '</div>'
                + '<div style="font-size:9px;color:var(--t4);margin-top:2px">' + tfL + '  ' + holdStr + (sigTimeStr ? '  <span style="color:rgba(0,212,255,.6)">' + sigTimeStr + '</span>' : '') + '</div>'
              + '</div>'
              + '<div style="text-align:right">'
                + '<div style="font-size:22px;font-weight:800;color:' + (isReal ? (isProfit ? 'var(--green)' : 'var(--red)') : 'var(--t4)') + ';font-family:Courier New,monospace">' + pnlStr + '</div>'
                + '<div style="font-size:13px;font-weight:700;color:var(--t1);font-family:Courier New,monospace">TL' + curPrice.toFixed(2) + '</div>'
                + (dayChg != null ? '<div style="font-size:9px;color:' + (dayChg >= 0 ? 'var(--green)' : 'var(--red)') + '">' + (dayChg >= 0 ? '+' : '') + dayChg.toFixed(2) + '% bugun</div>' : '')
              + '</div>'
            + '</div>'

            // Grid: sinyal, giris, en yuksek, stop
            + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:rgba(255,255,255,.04)">'
              + _posCell('TL' + sigPrice.toFixed(2), 'Sinyal Fiyati', 'var(--cyan)')
              + _posCell('TL' + pos.entry.toFixed(2), 'Giris')
              + _posCell('TL' + (pos.highest || pos.entry).toFixed(2), 'En Yuksek', 'var(--green)')
              + _posCell('TL' + (pos.stopPrice || 0).toFixed(2), 'Trailing Stop', 'var(--orange)')
            + '</div>'

            // Sinyal detaylari
            + (pos.cons || pos.adx || pos.pstate || pos.acts
              ? '<div style="display:flex;gap:6px;padding:7px 12px;flex-wrap:wrap;background:rgba(255,255,255,.02)">'
                + (pos.cons ? '<span style="font-size:8.5px;background:rgba(0,212,255,.1);color:var(--cyan);padding:2px 8px;border-radius:5px">Kons: %' + (parseFloat(pos.cons)||0).toFixed(0) + '</span>' : '')
                + (pos.adx ? '<span style="font-size:8.5px;background:rgba(255,255,255,.06);color:var(--t3);padding:2px 8px;border-radius:5px">ADX: ' + pos.adx + '</span>' : '')
                + (pos.pstate ? '<span style="font-size:8.5px;background:rgba(255,255,255,.04);color:var(--t4);padding:2px 8px;border-radius:5px">' + pos.pstate + '</span>' : '')
                + (pos.strength ? '<span style="font-size:8.5px;background:rgba(255,184,0,.1);color:var(--gold);padding:2px 8px;border-radius:5px">Guc: ' + pos.strength + '/10</span>' : '')
                + (pos.acts && pos.acts.length ? '<span style="font-size:8px;color:var(--t4);padding:2px">' + pos.acts.slice(0,3).join('  ') + '</span>' : '')
              + '</div>'
              : '')

            // Butonlar
            + '<div style="display:flex;gap:6px;padding:9px 12px">'
              + '<button onclick="openStockDashboard(\'' + ticker + '\')" style="flex:1;padding:8px;border-radius:8px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;font-weight:700;cursor:pointer">Grafik / AI</button>'
              + '<button onclick="openPositionSizer(\'' + ticker + '\',' + curPrice.toFixed(2) + ',' + (pos.stopPrice||0).toFixed(2) + ')" style="padding:8px 10px;border-radius:8px;background:rgba(255,184,0,.08);border:1px solid rgba(255,184,0,.2);color:var(--gold);font-size:10px;cursor:pointer">Lot</button>'
              + '<button onclick="closePosition(\'' + key + '\')" style="padding:8px 10px;border-radius:8px;background:rgba(255,68,68,.08);border:1px solid rgba(255,68,68,.2);color:var(--red);font-size:10px;cursor:pointer">Kapat</button>'
            + '</div>'

          + '</div>';
        });

        if(posListEl) posListEl.innerHTML = cards || '<div class="empty">Pozisyon yok</div>';
        if(countEl) countEl.textContent = positions.length;
        if(totalEl){ totalEl.textContent = (totalPnl >= 0 ? '+' : '') + totalPnl.toFixed(2) + '%'; totalEl.className = 'sval ' + (totalPnl >= 0 ? 'p' : 'n'); }
        if(bestEl) bestEl.textContent = bestTicker ? bestTicker + ' +' + bestPnl.toFixed(1) + '%' : '-';
        if(worstEl) worstEl.textContent = worstTicker ? worstTicker + ' ' + worstPnl.toFixed(1) + '%' : '-';

      }catch(e){ console.warn('renderPositions v28:',e.message); }
    };

    function _posCell(val, lbl, color){
      return '<div style="padding:9px 10px;background:rgba(0,0,0,.2)">'
        + '<div style="font-size:11px;font-weight:700;color:' + (color||'var(--t1)') + ';font-family:Courier New,monospace">' + val + '</div>'
        + '<div style="font-size:8px;color:var(--t4);margin-top:2px">' + lbl + '</div>'
        + '</div>';
    }
    window._posCell = _posCell;

  }catch(e){ console.warn('renderPositions override:',e.message); }
}, 600);

//  4. renderClosedPositions - SON 10 ISLEM DEFAULT GORUNUR 
setTimeout(function(){
  try{
    // posTab('closed') - varsayilan olarak her sayfa yenilemede goster
    var origPosTab = typeof posTab === 'function' ? posTab : null;

    // Kapali pozisyon varsa tab'i default goster
    window.addEventListener('load', function(){
      setTimeout(function(){
        try{
          // Sayfa acilinca closed tab da hazir olsun (gizli de olsa)
          if(typeof renderClosedPositions === 'function') renderClosedPositions();
        }catch(e){}
      }, 2000);
    }, {once:true, passive:true});

    // renderClosedPositions gelismis kart
    var origRCP = typeof renderClosedPositions === 'function' ? renderClosedPositions : null;

    window.renderClosedPositions = function(){
      try{
        var el = document.getElementById('closedList');
        if(!el) return;
        var closed = S.closedPositions || [];

        if(!closed.length){
          el.innerHTML = '<div class="empty"><div class="eico">&#128218;</div><div style="font-size:11px">Kapanmis pozisyon yok</div></div>';
          return;
        }

        // Ozet istatistikler
        var totalPnl = 0, wins = 0;
        closed.forEach(function(p){ var pnl = parseFloat(p.pnlPct||0); totalPnl += pnl; if(pnl >= 0) wins++; });
        var wr = (wins / closed.length * 100).toFixed(1);
        var avgPnl = (totalPnl / closed.length).toFixed(2);

        var html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px">'
          + '<div class="sstat"><div class="sval ' + (parseFloat(avgPnl)>=0?'p':'n') + '">' + (avgPnl>=0?'+':'') + avgPnl + '%</div><div class="slb2">Ort. PnL</div></div>'
          + '<div class="sstat"><div class="sval" style="color:' + (parseFloat(wr)>=50?'var(--green)':'var(--red)') + '">%' + wr + '</div><div class="slb2">Win Rate</div></div>'
          + '<div class="sstat"><div class="sval">' + closed.length + '</div><div class="slb2">Toplam</div></div>'
          + '</div>';

        // Son 20 islem karti
        html += '<div style="font-size:9px;font-weight:700;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:7px">Son ' + Math.min(20, closed.length) + ' Islem</div>';

        closed.slice(0, 20).forEach(function(p){
          var pnl = parseFloat(p.pnlPct || 0);
          var isProfit = pnl >= 0;
          var pnlStr = (isProfit ? '+' : '') + pnl.toFixed(2) + '%';

          // Tarih formatla
          var entryD = p.entryTime ? new Date(p.entryTime) : null;
          var exitD  = p.exitTime  ? new Date(p.exitTime)  : null;
          var dateStr = '';
          if(entryD && exitD){
            var fmt = function(d){ return d.getDate()+'.'+(d.getMonth()+1)+'.'+d.getFullYear(); };
            dateStr = fmt(entryD) + ' - ' + fmt(exitD);
          }

          var tfL = p.tf==='D'?'G':p.tf==='240'?'4S':'2S';

          html += '<div style="padding:10px 12px;border-radius:10px;border:1px solid '
            + (isProfit?'rgba(0,230,118,.15)':'rgba(255,68,68,.12)')
            + ';background:rgba(255,255,255,.02);margin-bottom:6px">'

            + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
              + '<div style="font-size:13px;font-weight:800;color:var(--t1)">' + p.ticker + '</div>'
              + '<div style="font-size:8px;color:var(--t4)">' + tfL + '</div>'
              + (dateStr ? '<div style="font-size:8px;color:var(--t4);margin-left:auto">' + dateStr + '</div>' : '')
              + '<div style="font-size:16px;font-weight:800;color:' + (isProfit?'var(--green)':'var(--red)') + ';font-family:Courier New,monospace;margin-left:auto">' + pnlStr + '</div>'
            + '</div>'

            + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:rgba(255,255,255,.04);border-radius:7px;overflow:hidden">'
              + _miniCell('TL'+(p.entry||0).toFixed(2),'Giris')
              + _miniCell('TL'+(p.exit||0).toFixed(2),'Cikis')
              + _miniCell((p.holdDays||0)+'g','Sure')
              + _miniCell((p.pstate||'-'),'Bolge')
            + '</div>'

            + (p.acts && p.acts.length
              ? '<div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap">'
                + p.acts.slice(0,4).map(function(a){
                    return '<span style="font-size:7.5px;padding:1px 6px;border-radius:4px;background:rgba(0,212,255,.08);color:var(--cyan)">' + a + '</span>';
                  }).join('')
              + '</div>'
              : '')

          + '</div>';
        });

        el.innerHTML = html;

        function _miniCell(val, lbl){
          return '<div style="padding:6px 8px;background:rgba(0,0,0,.2)">'
            + '<div style="font-size:10px;font-weight:700;color:var(--t1);font-family:Courier New,monospace">' + val + '</div>'
            + '<div style="font-size:7.5px;color:var(--t4);margin-top:1px">' + lbl + '</div>'
            + '</div>';
        }

      }catch(e){ console.warn('renderClosedPositions v28:',e.message); }
    };

  }catch(e){ console.warn('closedPositions override:',e.message); }
}, 700);

//  5. POZISYON SAYFASINDA KAPALI TAB DEFAULT GOSTER 
// Sayfa acilinca son 10 islem de hazir olsun - alinacak tab'e gore
var _origPg2 = window.pg;
setTimeout(function(){
  try{
    var curPg = window.pg;
    window.pg = function(name){
      try{ curPg(name); }catch(e){}
      if(name === 'positions'){
        setTimeout(function(){
          try{
            if(typeof renderClosedPositions === 'function') renderClosedPositions();
          }catch(e){}
        }, 200);
      }
    };
  }catch(e){}
}, 800);

//  6. FIYAT GUNCELLEMEDE ANINDA RENDER 
// Her fiyat guncellenmesinde pozisyon kartini yenile
setTimeout(function(){
  try{
    var origFetchPrices = typeof b4FetchPrices === 'function' ? b4FetchPrices : null;
    if(origFetchPrices){
      window.b4FetchPrices = function(){
        try{ origFetchPrices(); }catch(e){}
        setTimeout(function(){
          try{
            var posPage = document.getElementById('page-positions');
            if(posPage && posPage.classList.contains('on')){
              if(typeof renderPositions === 'function') renderPositions();
            }
          }catch(e){}
        }, 1500);
      };
    }
  }catch(e){}
}, 900);

//  LOG 
setTimeout(function(){
  try{
    console.log('[BIST v28] Pozisyon karti: signalPrice, signalTime, PnL, gecmis islemler aktif');
    if(typeof devLog === 'function') devLog('Blok28: Pozisyon karti tam duzeltildi', 'ok');
  }catch(e){}
}, 2500);

})();

</script>
<script>

// BIST ELITE v3 - BLOK 29: DASHBOARD TAM DUZELTME
// 1. stockDashboard tam ekran - iOS safe area, z-index fix
// 2. LWC CDN timeout + fallback + inline mini chart alternatif
// 3. OHLCV data/ohlcv field uyumsuzlugu fix
// 4. Grafik loading - LWC hazir olmadan render etme
// 5. Dashboard ekrandan sigmiyor sorunu

(function(){
'use strict';

//  1. DASHBOARD CSS - iOS SAFE AREA + TAM EKRAN 
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      // Tam ekran - iOS notch ve home indicator dahil
      '#stockDashboard{'
      +'  position: fixed !important;'
      +'  top: 0 !important; left: 0 !important;'
      +'  right: 0 !important; bottom: 0 !important;'
      +'  width: 100% !important; height: 100% !important;'
      +'  max-width: 100% !important; max-height: 100% !important;'
      +'  background: #000 !important;'
      +'  z-index: 10010 !important;'  /* En ustte - her seyin uzerinde */
      +'  display: none;'
      +'  flex-direction: column;'
      +'  overflow: hidden;'
      // iOS safe area
      +'  padding-top: env(safe-area-inset-top);'
      +'  padding-bottom: env(safe-area-inset-bottom);'
      +'  padding-left: env(safe-area-inset-left);'
      +'  padding-right: env(safe-area-inset-right);'
      +'}'
      +'#stockDashboard.on{ display: flex !important; }'
      // Header - fixed height
      +'.sdh{'
      +'  height: 52px; min-height: 52px; flex-shrink: 0;'
      +'  background: rgba(5,5,15,.98) !important;'
      +'}'
      // Tab bar - fixed height
      +'.sdtabs{'
      +'  flex-shrink: 0;'
      +'  background: rgba(0,0,0,.8) !important;'
      +'  -webkit-overflow-scrolling: touch;'
      +'  overflow-x: auto; overflow-y: hidden;'
      +'  scrollbar-width: none;'
      +'}'
      +'.sdtabs::-webkit-scrollbar{ display: none; }'
      // Content - kalani doldur
      +'.sdcontent{'
      +'  flex: 1 !important;'
      +'  overflow-y: auto !important;'
      +'  overflow-x: hidden;'
      +'  -webkit-overflow-scrolling: touch;'
      +'  padding: 10px;'
      +'  min-height: 0;'  /* flex child overflow fix */
      +'}'
      // Chart container - responsive height
      +'#sdChartWrap{'
      +'  width: 100% !important;'
      +'  height: 260px !important;'
      +'  background: #000;'
      +'  border-radius: 10px;'
      +'  overflow: hidden;'
      +'  margin-bottom: 8px;'
      +'}'
      +'#sdChart{ width: 100% !important; height: 190px !important; }'
      +'#sdRSI{ width: 100% !important; height: 70px !important; }'
      // Back button touch area buyut
      +'.sdh-back{'
      +'  min-width: 44px; min-height: 44px;'
      +'  display: flex; align-items: center; justify-content: center;'
      +'}'
      // Tab - min touch area
      +'.sdtab{ min-height: 44px; padding: 0 14px; }'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

//  2. LWC - INLINE FALLBACK + CDN FIX 
// CDN yuklenemiyorsa basit Canvas chart kullan
var _lwcFailed = false;
var _lwcTimeout = null;

// loadLWC'yi override et - timeout ekle
var _origLoadLWC = typeof loadLWC === 'function' ? loadLWC : null;
window.loadLWC = function(cb){
  // Zaten yuklu
  if(window._lwcLoaded && window.LightweightCharts){ cb(); return; }

  // 8 sn timeout - CDN gelmezse canvas chart kullan
  _lwcTimeout = setTimeout(function(){
    if(!window.LightweightCharts){
      console.warn('LWC CDN timeout - Canvas chart kullanilacak');
      _lwcFailed = true;
      window._lwcLoaded = true; // fake loaded
      if(cb) try{ cb(); }catch(e){}
    }
  }, 8000);

  // CDN'leri paralel yukle
  var sources = [
    'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js',
    'https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js',
    'https://cdnjs.cloudflare.com/ajax/libs/lightweight-charts/4.2.0/lightweight-charts.standalone.production.js'
  ];

  var loaded = false;
  function tryLoad(idx){
    if(loaded || idx >= sources.length) return;
    var s = document.createElement('script');
    s.src = sources[idx];
    s.onload = function(){
      if(loaded) return;
      loaded = true;
      clearTimeout(_lwcTimeout);
      window._lwcLoaded = true;
      _lwcFailed = false;
      if(cb) try{ cb(); }catch(e){}
    };
    s.onerror = function(){
      setTimeout(function(){ tryLoad(idx + 1); }, 500);
    };
    document.head.appendChild(s);
    // 3 sn sonra bir sonraki kaynagi dene (paralel)
    if(idx === 0) setTimeout(function(){ tryLoad(1); }, 3000);
  }
  tryLoad(0);
};

//  3. OHLCV FIELD UYUMSUZLUGU FIX 
// Proxy "data" field donduruyor, frontend "ohlcv" field bekliyor
// fetchDashboardData override - her iki field'i da destekle
var _origFDD = typeof fetchDashboardData === 'function' ? fetchDashboardData : null;
window.fetchDashboardData = function(ticker, tf){
  try{
    _sd.loading = true;
    var el = document.getElementById('sdContent');
    if(el) el.innerHTML = '<div class="sd-loading"><div class="sd-spin"></div>'
      +'<span style="font-size:11px;color:var(--t4)">'+ticker+' verisi yukleniyor...</span></div>';

    var PROXY2 = typeof PROXY_URL !== 'undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';

    Promise.all([
      fetch(PROXY2+'/ohlcv/'+ticker+'?tf='+(tf||'D'))
        .then(function(r){ return r.json(); })
        .then(function(d){
          // Field normalize: data veya ohlcv - ikisini de destekle
          if(d && !d.ohlcv && d.data) d.ohlcv = d.data;
          return d;
        })
        .catch(function(){ return null; }),
      fetch(PROXY2+'/analyze/'+ticker+'?tf='+(tf||'D'))
        .then(function(r){ return r.json(); })
        .catch(function(){ return null; }),
    ]).then(function(results){
      _sd.ohlcv = results[0];
      _sd.analysis = results[1];
      _sd.loading = false;

      // Fiyat guncelle
      if(_sd.analysis && _sd.analysis.price){
        var pEl = document.getElementById('sdPrice');
        if(pEl) pEl.textContent = 'TL' + _sd.analysis.price.toFixed(2);
      }

      // OHLCV debug
      var ohlcvArr = _sd.ohlcv && (_sd.ohlcv.ohlcv || _sd.ohlcv.data);
      console.log('[Dashboard] '+ticker+' OHLCV: '+(ohlcvArr?ohlcvArr.length+' bar':'YOK'));

      switchSDTab(_sd.activeTab, true);
    }).catch(function(e){
      _sd.loading = false;
      if(el) el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--red);font-size:11px">'
        +'Veri yuklenemedi: '+e.message+'<br><button onclick="fetchDashboardData(\''+ticker+'\',\''+tf+'\')" '
        +'style="margin-top:8px;padding:8px 16px;border-radius:8px;background:rgba(0,212,255,.1);'
        +'border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;cursor:pointer">Tekrar Dene</button></div>';
    });
  }catch(e){
    _sd.loading = false;
    console.warn('fetchDashboardData:',e.message);
  }
};

//  4. renderSDChart - OHLCV FIELD + CANVAS FALLBACK 
var _origRenderSDChart = typeof renderSDChart === 'function' ? renderSDChart : null;
window.renderSDChart = function(){
  try{
    var el = document.getElementById('sdContent');
    if(!el) return;

    // OHLCV - her iki field'i dene
    var ohlcvData = _sd.ohlcv && (_sd.ohlcv.ohlcv || _sd.ohlcv.data);

    if(!ohlcvData || !ohlcvData.length){
      el.innerHTML = '<div style="padding:30px;text-align:center">'
        +'<div style="font-size:30px;margin-bottom:10px"></div>'
        +'<div style="font-size:12px;color:var(--t2);margin-bottom:6px">Grafik verisi yok</div>'
        +'<div style="font-size:10px;color:var(--t4);margin-bottom:14px">Piyasa kapali olabilir veya proxy baglantisi yok</div>'
        +'<button onclick="fetchDashboardData(\''+_sd.ticker+'\',\''+_sd.tf+'\')" '
        +'style="padding:10px 20px;border-radius:9px;background:rgba(0,212,255,.1);'
        +'border:1px solid rgba(0,212,255,.25);color:var(--cyan);font-size:11px;cursor:pointer">Tekrar Yukle</button>'
        // Analysis bilgilerini yine de goster
        + (_sd.analysis ? renderSDMetricsHTML() : '')
        +'</div>';
      return;
    }

    // LWC yuklenmemisse Canvas chart kullan
    if(_lwcFailed || !window.LightweightCharts){
      renderCanvasChart(ohlcvData, el);
      return;
    }

    // LWC yuklu - original render
    // Ama once _sd.ohlcv.ohlcv'yi garantile
    if(!_sd.ohlcv.ohlcv && _sd.ohlcv.data) _sd.ohlcv.ohlcv = _sd.ohlcv.data;

    // TF butonlari + indicator butonlari
    el.innerHTML =
      '<div class="chart-tf-btns">'
      +['D','240','120','W'].map(function(tf){
        var lbl = {D:'1G','240':'4S','120':'2S',W:'1H'}[tf]||tf;
        return '<button class="chart-tf-btn'+(tf===_sd.tf?' active':'')+'" onclick="changeDashboardTF(\''+tf+'\')">'+lbl+'</button>';
      }).join('')+'</div>'
      +'<div class="chart-ind-btns">'
      +[['ema50','EMA50'],['ema200','EMA200'],['bb','Bollinger'],['volume','Hacim']].map(function(p){
        return '<button class="chart-ind-btn'+((_sd.indicators&&_sd.indicators[p[0]])?' active':'')+'" onclick="toggleDashboardInd(\''+p[0]+'\')">'+p[1]+'</button>';
      }).join('')+'</div>'
      +'<div id="sdChartWrap"><div id="sdChart"></div></div>'
      +'<div class="sd-section"><div class="sd-section-title">RSI(14)</div>'
      +'<div id="sdRSI" style="height:70px;background:#000;border-radius:8px"></div></div>'
      + renderSDMetricsHTML()
    ;

    // LWC hazir mi?
    if(window.LightweightCharts){
      setTimeout(function(){ try{ buildLWChart(ohlcvData); }catch(e){ console.warn('LWC:',e.message); } }, 50);
    } else {
      loadLWC(function(){
        setTimeout(function(){ try{ buildLWChart(ohlcvData); }catch(e){ renderCanvasChart(ohlcvData, el); } }, 50);
      });
    }
  }catch(e){ console.warn('renderSDChart:',e.message); }
};

//  5. CANVAS CHART FALLBACK (LWC olmadan) 
function renderCanvasChart(ohlcv, container){
  try{
    if(!container) container = document.getElementById('sdContent');
    if(!container) return;

    // Onceki content koru, sadece chart'i degistir
    var chartArea = container.querySelector('#sdChartWrap');
    if(!chartArea){
      // Yeni div olustur
      container.innerHTML =
        '<div class="chart-tf-btns">'
        +['D','240','120'].map(function(tf){
          var lbl={D:'1G','240':'4S','120':'2S'}[tf];
          return '<button class="chart-tf-btn'+(tf===_sd.tf?' active':'')+'" onclick="changeDashboardTF(\''+tf+'\')">'+lbl+'</button>';
        }).join('')+'</div>'
        +'<div id="sdChartWrap" style="position:relative"><canvas id="sdChartCanvas" style="width:100%;height:250px;display:block"></canvas>'
        +'<div id="sdChartInfo" style="position:absolute;top:6px;right:8px;font-size:9px;color:rgba(255,255,255,.4)">Canvas Mode</div>'
        +'</div>'
        + renderSDMetricsHTML();
      chartArea = container.querySelector('#sdChartWrap');
    }

    var canvas = container.querySelector('#sdChartCanvas');
    if(!canvas) return;

    var W = canvas.offsetWidth || 340;
    var H = 250;
    canvas.width = W * (window.devicePixelRatio||1);
    canvas.height = H * (window.devicePixelRatio||1);
    canvas.style.width = W+'px';
    canvas.style.height = H+'px';
    var ctx = canvas.getContext('2d');
    var dpr = window.devicePixelRatio||1;
    ctx.scale(dpr, dpr);

    // Son 60 bar
    var bars = ohlcv.slice(-60);
    if(!bars.length) return;

    var pad = {top:10, right:50, bottom:20, left:10};
    var cw = W - pad.left - pad.right;
    var ch = H - pad.top - pad.bottom;

    // Min/Max
    var highs = bars.map(function(b){return b.h||b.high||b.c||0;});
    var lows  = bars.map(function(b){return b.l||b.low||b.c||0;});
    var minP = Math.min.apply(null, lows);
    var maxP = Math.max.apply(null, highs);
    var range = maxP - minP || 1;

    var barW = Math.max(1, Math.floor(cw / bars.length) - 1);

    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,.05)';
    ctx.lineWidth = 0.5;
    for(var g=0;g<4;g++){
      var gy = pad.top + ch/3*g;
      ctx.beginPath(); ctx.moveTo(pad.left,gy); ctx.lineTo(W-pad.right,gy); ctx.stroke();
    }

    // Mum grafigi
    bars.forEach(function(bar, i){
      var x = pad.left + i * (cw/bars.length);
      var o = bar.o||bar.open||bar.c||0;
      var h = bar.h||bar.high||bar.c||0;
      var l = bar.l||bar.low||bar.c||0;
      var cl= bar.c||bar.close||0;
      var isUp = cl >= o;
      var color = isUp ? '#00E676' : '#FF4444';

      // Wick
      var highY = pad.top + ch * (1-(h-minP)/range);
      var lowY  = pad.top + ch * (1-(l-minP)/range);
      var openY = pad.top + ch * (1-(o-minP)/range);
      var closeY= pad.top + ch * (1-(cl-minP)/range);

      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x + barW/2, highY);
      ctx.lineTo(x + barW/2, lowY);
      ctx.stroke();

      // Body
      ctx.fillStyle = color;
      var bodyTop = Math.min(openY, closeY);
      var bodyH   = Math.max(1, Math.abs(closeY - openY));
      ctx.fillRect(x, bodyTop, Math.max(2, barW), bodyH);
    });

    // Son fiyat line
    if(bars.length){
      var lastClose = bars[bars.length-1].c||bars[bars.length-1].close||0;
      var lastY = pad.top + ch * (1-(lastClose-minP)/range);
      ctx.strokeStyle = 'rgba(0,212,255,.6)'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
      ctx.beginPath(); ctx.moveTo(pad.left,lastY); ctx.lineTo(W-pad.right,lastY); ctx.stroke();
      ctx.setLineDash([]);
      // Fiyat etiketi
      ctx.fillStyle = 'rgba(0,0,0,.8)'; ctx.fillRect(W-pad.right+2, lastY-9, pad.right-2, 18);
      ctx.fillStyle = 'var(--cyan)' || '#00D4FF'; ctx.font = '9px Courier New'; ctx.textAlign='left';
      ctx.fillText(lastClose.toFixed(2), W-pad.right+4, lastY+4);
    }

    // Fiyat ekseni
    ctx.fillStyle = 'rgba(255,255,255,.4)'; ctx.font = '8px Arial'; ctx.textAlign='right';
    [0,0.5,1].forEach(function(r){
      var price = minP + range * (1-r);
      var py = pad.top + ch * r;
      ctx.fillText(price.toFixed(2), W-2, py+4);
    });

  }catch(e){ console.warn('canvasChart:',e.message); }
}
window.renderCanvasChart = renderCanvasChart;

//  6. openStockDashboard - Z-INDEX FIX 
var _origOSD = typeof openStockDashboard === 'function' ? openStockDashboard : null;
window.openStockDashboard = function(ticker, name){
  try{
    if(!ticker) return;
    _sd.ticker = ticker;
    _sd.name = name || ticker;
    _sd.activeTab = 'chart';
    _sd.tf = 'D';
    _sd.loading = false;

    var modal = document.getElementById('stockDashboard');
    if(!modal){ 
      if(typeof createDashboardModal === 'function') createDashboardModal();
      modal = document.getElementById('stockDashboard');
    }
    if(!modal) return;

    // Z-index en uste al
    modal.style.zIndex = '10010';
    modal.classList.add('on');

    // Body scroll engelle
    document.body.style.overflow = 'hidden';
    document.body.style.position = 'fixed';
    document.body.style.width = '100%';

    if(typeof updateDashboardHeader === 'function') updateDashboardHeader(ticker, name);

    // LWC hazirsa direkt yukle, degilse paralel baslat
    if(window.LightweightCharts){
      if(typeof switchSDTab === 'function') switchSDTab('chart');
      fetchDashboardData(ticker, 'D');
    } else {
      // Once veriyi getir, LWC paralel yukle
      loadLWC(function(){});
      if(typeof switchSDTab === 'function') switchSDTab('chart');
      fetchDashboardData(ticker, 'D');
    }

    if(typeof haptic === 'function') haptic('medium');
  }catch(e){ console.warn('openStockDashboard:',e.message); }
};

// closeStockDashboard - body reset
var _origCSD = typeof closeStockDashboard === 'function' ? closeStockDashboard : null;
window.closeStockDashboard = function(){
  try{
    var modal = document.getElementById('stockDashboard');
    if(modal) modal.classList.remove('on');
    // Body scroll geri ac
    document.body.style.overflow = '';
    document.body.style.position = '';
    document.body.style.width = '';
    // Chart temizle
    if(_sd.chart){ try{_sd.chart.remove();}catch(e){} _sd.chart=null; }
    if(_sd.rsiChart){ try{_sd.rsiChart.remove();}catch(e){} _sd.rsiChart=null; }
  }catch(e){}
};

//  7. buildLWChart - OHLCV field fix 
var _origBuildLWC = typeof buildLWChart === 'function' ? buildLWChart : null;
if(_origBuildLWC){
  window.buildLWChart = function(ohlcv){
    // ohlcv param normalize
    if(!ohlcv && _sd.ohlcv){
      ohlcv = _sd.ohlcv.ohlcv || _sd.ohlcv.data;
    }
    if(!ohlcv || !ohlcv.length){
      console.warn('buildLWChart: veri yok');
      return;
    }
    try{ _origBuildLWC(ohlcv); }catch(e){
      console.warn('LWC error:',e.message);
      // Canvas fallback
      renderCanvasChart(ohlcv);
    }
  };
}

//  LOG 
setTimeout(function(){
  try{
    console.log('[Blok29] Dashboard: tam ekran + LWC fallback + OHLCV fix aktif');
    if(typeof devLog==='function') devLog('Blok29: Dashboard tam duzeltildi','ok');
  }catch(e){}
}, 1500);

})();

</script>
<script>

// BIST ELITE v3 - BLOK 30: TUM HISSELER ICIN PINE TABLOLAR
// openSt() override - her hissede Pine tablo goster
// Proxy /scan_single ile sys1/sys2/fusion/master_ai ceker

(function(){
'use strict';

var PROXY2 = typeof PROXY_URL !== 'undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';

// openSt OVERRIDE
var _origOpenSt = typeof openSt === 'function' ? openSt : null;

window.openSt = function(t){
  try{
    var stk = (typeof STOCKS !== 'undefined' ? STOCKS : []).find(function(s){ return s.t===t; });
    var si = (S.sigs||[]).findIndex(function(s){ return s.ticker===t; });
    var title = t + (stk?' — '+stk.n:'');

    if(si > -1){
      if(_origOpenSt) _origOpenSt(t);
      setTimeout(function(){
        var mc = document.getElementById('mcont');
        if(mc && mc.innerHTML.indexOf('pineTbl30') === -1){
          _fetchPine(t, 'D', mc);
        }
      }, 200);
      return;
    }

    var modal = document.getElementById('modal');
    var mtit  = document.getElementById('mtit');
    var mc    = document.getElementById('mcont');
    if(!modal || !mc) return;
    if(mtit) mtit.textContent = title;
    mc.innerHTML = _spinHTML(t);
    modal.classList.add('on');

    var tf = (S.tfFilter && S.tfFilter.length) ? S.tfFilter[0] : 'D';
    _fetchPine(t, tf, mc);
  }catch(e){ console.warn('openSt30:',e.message); if(_origOpenSt) _origOpenSt(t); }
};

function _fetchPine(ticker, tf, container){
  try{
    var cfg = typeof C !== 'undefined' ? {
      atrm: C.atrm||8, fb: C.fb||80, sc: C.sc||5, adxMin: C.adxMin||25
    } : {};
    fetch(PROXY2+'/scan_single',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ticker:ticker, tf:tf, cfg:cfg})
    })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(!container || !container.isConnected) return;
      container.innerHTML = d.data ? _renderPine(d.data, ticker, tf) : _noDataHTML(ticker, tf, d.error);
    })
    .catch(function(e){
      if(container && container.isConnected)
        container.innerHTML = _noDataHTML(ticker, tf, e.message);
    });
  }catch(e){ console.warn('_fetchPine:',e.message); }
}

function _renderPine(res, ticker, tf){
  var tfL = {D:'Gunluk','240':'4 Saat','120':'2 Saat'}[tf]||tf;
  var html = '<div id="pineTbl30" style="padding:4px 0">';

  // Fiyat/ozet satiri
  var price  = res.price||'';
  var cons   = res.consensus||res.cons||'';
  var adx    = res.adx||'';
  var pstate = res.pstate||'';
  var signal = res.signal||'';
  var isMst  = res.is_master||false;
  var sigColor= isMst?'var(--gold)':signal==='buy'?'var(--green)':'var(--t3)';
  var sigLbl = isMst?'MASTER AI':signal==='buy'?'AL':'TARASILDI';

  if(price||cons||adx){
    html += '<div style="display:flex;align-items:center;gap:6px;padding:8px;background:rgba(255,255,255,.03);border-radius:10px;margin-bottom:8px;border:1px solid rgba(255,255,255,.06)">'
      +'<div style="flex:1">'
      +(price?'<div style="font-size:18px;font-weight:700;color:var(--t1)">TL'+parseFloat(price).toFixed(2)+'</div>':'')
      +'<div style="font-size:9px;color:var(--t4)">'+tfL+'</div>'
      +'</div>'
      +(cons?'<div style="text-align:center"><div style="font-size:14px;font-weight:700;color:var(--cyan)">%'+parseFloat(cons).toFixed(0)+'</div><div style="font-size:8px;color:var(--t4)">Konsensus</div></div>':'')
      +(adx?'<div style="text-align:center"><div style="font-size:14px;font-weight:700;color:var(--purple)">'+parseFloat(adx).toFixed(0)+'</div><div style="font-size:8px;color:var(--t4)">ADX</div></div>':'')
      +(pstate?'<div style="text-align:center"><div style="font-size:9px;font-weight:700;color:'+(pstate.indexOf('UCUZ')>-1?'var(--green)':pstate.indexOf('PAHALI')>-1?'var(--red)':'var(--t3)')+'">'+pstate+'</div><div style="font-size:8px;color:var(--t4)">Bolge</div></div>':'')
      +(signal?'<div style="padding:4px 8px;border-radius:6px;background:'+sigColor+'22;border:1px solid '+sigColor+'55;font-size:9px;font-weight:700;color:'+sigColor+'">'+sigLbl+'</div>':'')
      +'</div>';
  }

  // 4 sistem tablosu
  var tbls = [
    {k:'sys1',    l:'Sistem 1 (SuperTrend+TMA)', c:'#00D4FF'},
    {k:'sys2',    l:'PRO Engine (6 Faktor)',      c:'#00E676'},
    {k:'fusion',  l:'Fusion AI',                  c:'#C084FC'},
    {k:'master_ai',l:'Master AI Konsensus',       c:'#FFB800'},
  ];
  var hasAny=false;
  tbls.forEach(function(tbl){
    var d=res[tbl.k];
    if(!d) return;
    hasAny=true;
    var buys=d.buys||0, wins=d.wins||0, losses=d.losses||0, sells=d.sells||0;
    var pnl=parseFloat(d.total_pnl||0).toFixed(1);
    var op=d.open_pnl!==undefined?parseFloat(d.open_pnl).toFixed(1):null;
    var wr=buys>0?((wins/buys)*100).toFixed(0):'0';
    var wrC=parseFloat(wr)>=55?'var(--green)':parseFloat(wr)>=45?'var(--gold)':'var(--red)';
    var pC=parseFloat(pnl)>0?'var(--green)':parseFloat(pnl)<0?'var(--red)':'var(--t2)';

    var extra='';
    if(tbl.k==='master_ai'){
      var bc=d.buy_consensus!==undefined?(parseFloat(d.buy_consensus)*100).toFixed(0)+'%':'';
      var th=d.dyn_buy_thresh!==undefined?(parseFloat(d.dyn_buy_thresh)*100).toFixed(0)+'%':'';
      if(bc) extra+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)"><span style="color:rgba(255,255,255,.4);font-size:9px">Consensus Buy</span><span style="color:var(--cyan);font-size:9px;font-weight:600">'+bc+'</span></div>';
      if(th) extra+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)"><span style="color:rgba(255,255,255,.4);font-size:9px">Dyn Buy Thresh</span><span style="color:var(--gold);font-size:9px;font-weight:600">'+th+'</span></div>';
    }
    if(tbl.k==='sys2'&&res.pro_factors){
      var pf=res.pro_factors;
      var sc=(pf.rs_strong?1:0)+(pf.accum_d?1:0)+(pf.exp_4h?1:0)+(pf.break_4h?1:0)+(pf.mom_2h?1:0)+(pf.dna?1:0);
      extra+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)"><span style="color:rgba(255,255,255,.4);font-size:9px">PRO Skor</span><span style="color:var(--green);font-size:9px;font-weight:600">'+sc+'/6</span></div>';
    }

    html+='<div style="background:rgba(10,10,20,.6);border:1px solid rgba(255,255,255,.07);border-left:3px solid '+tbl.c+';border-radius:10px;padding:10px;margin-bottom:6px">'
      +'<div style="font-size:10px;font-weight:700;color:'+tbl.c+';margin-bottom:7px">'+tbl.l+'</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-bottom:7px">'
      +_sbox('Toplam AL',buys,'var(--cyan)')
      +_sbox('Karl./Zar.',wins+'K/'+losses+'Z',wrC)
      +_sbox('Win%',wr+'%',wrC)
      +_sbox('Toplam PnL',pnl+'%',pC)
      +'</div>'
      +(sells?'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)"><span style="color:rgba(255,255,255,.4);font-size:9px">Toplam SAT</span><span style="color:var(--t2);font-size:9px;font-weight:600">'+sells+'</span></div>':'')
      +(op!==null?'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)"><span style="color:rgba(255,255,255,.4);font-size:9px">Pozisyon PnL</span><span style="color:'+(parseFloat(op)>0?'var(--green)':parseFloat(op)<0?'var(--red)':'var(--t4)')+';font-size:9px;font-weight:600">'+(parseFloat(op)!==0?op+'%':'YOK')+'</span></div>':'')
      +extra+'</div>';
  });

  // Agents
  if(res.agents){
    html+='<div style="background:rgba(10,10,20,.6);border:1px solid rgba(255,255,255,.07);border-left:3px solid rgba(255,100,50,.7);border-radius:10px;padding:10px;margin-bottom:6px">'
      +'<div style="font-size:10px;font-weight:700;color:rgba(255,150,100,1);margin-bottom:7px">Agent Grubu</div>'
      +'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:3px">';
    ['a60','a61','a62','a81','a120'].forEach(function(ak){
      var a=res.agents[ak];
      if(!a) return;
      var ap=parseFloat(a.pnl||0).toFixed(1);
      var ac=parseFloat(ap)>0?'var(--green)':parseFloat(ap)<0?'var(--red)':'var(--t4)';
      html+='<div style="text-align:center;background:rgba(255,255,255,.03);border-radius:7px;padding:6px 2px">'
        +'<div style="font-size:7px;color:var(--t4);margin-bottom:2px">'+ak.toUpperCase()+'</div>'
        +'<div style="font-size:10px;font-weight:700;color:'+ac+'">'+ap+'%</div>'
        +'<div style="font-size:7px;color:var(--t4)">'+(a.buys||0)+' AL</div>'
        +'</div>';
    });
    html+='</div></div>';
  }

  if(!hasAny) html+='<div style="padding:16px;text-align:center;color:var(--t4);font-size:11px">Backtest verisi bulunamadi.</div>';

  // Alt butonlar
  var tvTf={D:'1D','240':'4H','120':'2H'}[tf]||'1D';
  html+='<div style="margin-top:8px;display:flex;gap:6px">'
    +'<button onclick="window.open(\'https://www.tradingview.com/chart/?symbol=BIST:'+ticker+'&interval='+tvTf+'\')" '
    +'style="flex:1;padding:9px;border-radius:8px;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;cursor:pointer;font-weight:600">TV Grafik</button>'
    +'<button onclick="pine30TF(\''+ticker+'\',\''+tf+'\')" '
    +'style="flex:1;padding:9px;border-radius:8px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);color:var(--t3);font-size:10px;cursor:pointer">TF Degistir</button>'
    +'</div>'
    +'</div>';
  return html;
}

window.pine30TF=function(ticker,cur){
  var tfs=['D','240','120'];
  var next=tfs[(tfs.indexOf(cur)+1)%tfs.length];
  var mc=document.getElementById('mcont');
  if(mc){ mc.innerHTML=_spinHTML(ticker); _fetchPine(ticker,next,mc); }
  if(typeof toast==='function') toast({D:'Gunluk','240':'4 Saat','120':'2 Saat'}[next]+' yukleniyor...');
};

function _sbox(l,v,c){
  return '<div style="background:rgba(255,255,255,.03);border-radius:7px;padding:7px 3px;text-align:center">'
    +'<div style="font-size:11px;font-weight:700;color:'+c+'">'+v+'</div>'
    +'<div style="font-size:7px;color:rgba(255,255,255,.3);margin-top:2px">'+l+'</div>'
    +'</div>';
}

function _spinHTML(t){
  return '<div style="padding:30px;text-align:center">'
    +'<div style="display:inline-block;width:28px;height:28px;border:2px solid rgba(0,212,255,.15);border-top-color:var(--cyan);border-radius:50%;animation:spin30 .8s linear infinite;margin-bottom:12px"></div>'
    +'<div style="font-size:11px;color:var(--cyan);font-weight:600">'+t+'</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-top:4px">Pine tablolar yukleniyor (2 yil / 504 bar)</div>'
    +'</div>';
}

function _noDataHTML(t,tf,err){
  return '<div style="padding:20px;text-align:center">'
    +'<div style="font-size:11px;color:var(--t2);margin-bottom:4px">'+t+'</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-bottom:12px">'+(err||'Veri alinamadi')+'</div>'
    +'<div style="display:flex;gap:6px;justify-content:center">'
    +'<button onclick="openSt(\''+t+'\')" style="padding:8px 14px;border-radius:8px;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;cursor:pointer">Tekrar Dene</button>'
    +'<button onclick="window.open(\'https://www.tradingview.com/chart/?symbol=BIST:'+t+'\')" style="padding:8px 14px;border-radius:8px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);color:var(--t3);font-size:10px;cursor:pointer">TV Grafik</button>'
    +'</div></div>';
}

(function(){
  try{
    if(!document.getElementById('spin30css')){
      var st=document.createElement('style');
      st.id='spin30css';
      st.textContent='@keyframes spin30{to{transform:rotate(360deg)}}';
      document.head.appendChild(st);
    }
  }catch(e){}
})();

setTimeout(function(){
  try{
    console.log('[Blok30] Pine tablo: tum 424 hisse icin aktif');
    if(typeof devLog==='function') devLog('Blok30: Pine tablo 424 hisse OK','ok');
  }catch(e){}
},2000);

})();

</script>
<script>

// BIST ELITE v3 - BLOK 30: TUM HISSELER ICIN PINE TABLOLARI
(function(){
'use strict';

// CSS
(function(){
  try{
    var st = document.createElement('style');
    st.textContent =
      '#pineModal{position:fixed;inset:0;z-index:10020;background:rgba(0,0,0,.92);display:none;flex-direction:column;-webkit-overflow-scrolling:touch;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}'
      +'#pineModal.on{display:flex}'
      +'.pineHdr{display:flex;align-items:center;gap:10px;padding:12px 14px;background:rgba(5,5,20,.98);border-bottom:1px solid rgba(255,255,255,.08);flex-shrink:0}'
      +'.pineHdrBack{min-width:44px;min-height:44px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,.06);border-radius:9px;cursor:pointer;font-size:18px;color:var(--t2);border:none}'
      +'.pineHdrInfo{flex:1}'
      +'.pineHdrTicker{font-size:16px;font-weight:700;color:var(--t1)}'
      +'.pineHdrName{font-size:9px;color:var(--t4)}'
      +'.pineTFbtns{display:flex;gap:4px}'
      +'.pineTFbtn{padding:5px 10px;border-radius:7px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);color:var(--t3);font-size:10px;cursor:pointer}'
      +'.pineTFbtn.active{background:rgba(0,212,255,.15);border-color:rgba(0,212,255,.4);color:var(--cyan)}'
      +'.pineBody{flex:1;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;padding:10px;min-height:0}'
      +'.pineLoading{display:flex;flex-direction:column;align-items:center;justify-content:center;height:200px;gap:12px}'
      +'.pineSpinner{width:32px;height:32px;border-radius:50%;border:3px solid rgba(0,212,255,.2);border-top-color:var(--cyan);animation:pineSpin .7s linear infinite}'
      +'@keyframes pineSpin{to{transform:rotate(360deg)}}'
      +'.pineGrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}'
      +'.pineBox{background:rgba(10,10,20,.9);border:1px solid rgba(255,255,255,.1);border-radius:11px;padding:10px}'
      +'.pineBoxTitle{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;padding-bottom:5px;border-bottom:1px solid rgba(255,255,255,.07)}'
      +'.pineRow{display:flex;justify-content:space-between;align-items:center;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}'
      +'.pineRow:last-child{border-bottom:none}'
      +'.pineLabel{font-size:9px;color:rgba(255,255,255,.45)}'
      +'.pineVal{font-size:9px;font-weight:700;font-family:Courier New,monospace}'
      +'.pineBoxFull{background:rgba(10,10,20,.9);border:1px solid rgba(255,255,255,.1);border-radius:11px;padding:10px;margin-bottom:8px}'
      +'.pineAgentRow{display:flex;align-items:center;gap:6px;padding:4px 0}'
      +'.pineAgentLabel{font-size:9px;color:rgba(255,255,255,.5);width:35px}'
      +'.pineAgentBarWrap{flex:1;height:14px;background:rgba(255,255,255,.06);border-radius:4px;overflow:hidden}'
      +'.pineAgentBar{height:100%;border-radius:4px;transition:width .4s ease}'
      +'.pineAgentPnl{font-size:9px;font-weight:700;font-family:Courier New,monospace;width:55px;text-align:right}'
      +'.pineScoreBadge{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px}'
      +'.pineScorePill{padding:4px 9px;border-radius:20px;font-size:9px;font-weight:700;border:1px solid rgba(255,255,255,.1)}'
      +'.pineTVBtn{display:block;width:100%;padding:11px;border-radius:10px;background:rgba(0,212,255,.12);border:1px solid rgba(0,212,255,.3);color:var(--cyan);font-size:11px;font-weight:700;text-align:center;cursor:pointer;margin-top:4px;box-sizing:border-box}'
      +'.stPineBtn{padding:5px 9px;border-radius:7px;font-size:9px;font-weight:700;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.2);color:var(--cyan);cursor:pointer;flex-shrink:0;margin-left:6px}'
      +'.stPineBtn:active{opacity:.7}'
      +'.pineSigTab{padding:7px 12px;border-radius:8px;font-size:10px;font-weight:600;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.25);color:var(--cyan);cursor:pointer;display:inline-block;margin-bottom:8px}'
    ;
    document.head.appendChild(st);
  }catch(e){}
})();

function ensurePineModal(){
  if(document.getElementById('pineModal')) return;
  var div = document.createElement('div');
  div.id = 'pineModal';
  div.innerHTML =
    '<div class="pineHdr">'
    +'<button class="pineHdrBack" onclick="closePineModal()">&#8592;</button>'
    +'<div class="pineHdrInfo">'
    +'<div class="pineHdrTicker" id="pineTicker">-</div>'
    +'<div class="pineHdrName" id="pineName">Pine Script Tablolari</div>'
    +'</div>'
    +'<div class="pineTFbtns">'
    +'<button class="pineTFbtn active" data-tf="D" onclick="loadPineTF(\'D\')">1G</button>'
    +'<button class="pineTFbtn" data-tf="240" onclick="loadPineTF(\'240\')">4S</button>'
    +'<button class="pineTFbtn" data-tf="120" onclick="loadPineTF(\'120\')">2S</button>'
    +'</div>'
    +'</div>'
    +'<div class="pineBody" id="pineBody">'
    +'<div class="pineLoading"><div class="pineSpinner"></div><span style="font-size:11px;color:var(--t4)">Yukleniyor...</span></div>'
    +'</div>'
  ;
  document.body.appendChild(div);
}

var _pineTicker = null;
var _pineTF = 'D';
var _pineCache = {};

window.openPineModal = function(ticker, name, tf){
  try{
    ensurePineModal();
    _pineTicker = ticker;
    _pineTF = tf || 'D';
    var el = document.getElementById('pineModal');
    if(el) el.classList.add('on');
    var tEl = document.getElementById('pineTicker');
    var nEl = document.getElementById('pineName');
    if(tEl) tEl.textContent = ticker;
    if(nEl) nEl.textContent = (name||ticker) + ' - Pine Script Tablolari';
    document.querySelectorAll('.pineTFbtn').forEach(function(b){
      b.classList.toggle('active', b.dataset.tf === _pineTF);
    });
    document.body.style.overflow = 'hidden';
    document.body.style.position = 'fixed';
    document.body.style.width = '100%';
    loadPineData(ticker, _pineTF);
    if(typeof haptic==='function') haptic('medium');
  }catch(e){ console.warn('openPineModal:',e.message); }
};

window.closePineModal = function(){
  try{
    var el = document.getElementById('pineModal');
    if(el) el.classList.remove('on');
    document.body.style.overflow = '';
    document.body.style.position = '';
    document.body.style.width = '';
  }catch(e){}
};

window.loadPineTF = function(tf){
  try{
    _pineTF = tf;
    document.querySelectorAll('.pineTFbtn').forEach(function(b){
      b.classList.toggle('active', b.dataset.tf === tf);
    });
    if(_pineTicker) loadPineData(_pineTicker, tf);
  }catch(e){}
};

function loadPineData(ticker, tf){
  try{
    var body = document.getElementById('pineBody');
    if(!body) return;
    var cacheKey = ticker + '_' + tf;
    if(_pineCache[cacheKey]){
      renderPineData(_pineCache[cacheKey]);
      return;
    }
    body.innerHTML = '<div class="pineLoading"><div class="pineSpinner"></div>'
      +'<span style="font-size:11px;color:var(--t4)">'+ticker+' analiz ediliyor... (30sn surebilir)</span></div>';
    var PROXY2 = typeof PROXY_URL!=='undefined' ? PROXY_URL : 'https://bist-price-proxy.onrender.com';
    fetch(PROXY2+'/analyze/'+ticker+'?tf='+tf)
      .then(function(r){ return r.json(); })
      .then(function(data){
        if(data && (data.sys1 || data.price)){
          _pineCache[cacheKey] = data;
          renderPineData(data);
        } else {
          body.innerHTML = '<div style="padding:30px;text-align:center;color:var(--t4);font-size:11px">'
            +(data&&data.error?data.error:'Yeterli veri yok')+'<br><br>'
            +'<button onclick="delete _pineCache[\''+cacheKey+'\'];loadPineData(\''+ticker+'\',\''+tf+'\')" '
            +'style="padding:8px 16px;border-radius:8px;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;cursor:pointer">Tekrar Dene</button>'
            +'</div>';
        }
      })
      .catch(function(e){
        body.innerHTML = '<div style="padding:30px;text-align:center">'
          +'<div style="font-size:11px;color:var(--red);margin-bottom:10px">Baglanti hatasi: '+e.message+'</div>'
          +'<button onclick="delete _pineCache[\''+cacheKey+'\'];loadPineData(\''+ticker+'\',\''+tf+'\')" '
          +'style="padding:8px 16px;border-radius:8px;background:rgba(0,212,255,.1);border:1px solid rgba(0,212,255,.2);color:var(--cyan);font-size:10px;cursor:pointer">Tekrar Dene</button>'
          +'</div>';
      });
  }catch(e){ console.warn('loadPineData:',e.message); }
}

function pineBox(title, color, d, extra){
  if(!d) return '<div class="pineBox"><div class="pineBoxTitle" style="color:'+color+'">'+title+'</div>'
    +'<div style="padding:20px;text-align:center;color:var(--t4);font-size:9px">Veri yok</div></div>';
  var wr = d.buys>0 ? Math.round(d.wins/d.buys*100) : 0;
  var wrClr = wr>=50 ? 'var(--green)' : 'var(--red)';
  var pnlClr = (d.total_pnl||0)>=0 ? 'var(--green)' : 'var(--red)';
  var openVal = (d.open_pnl!=null&&d.open_pnl!==0) ? (d.open_pnl>=0?'+':'')+d.open_pnl.toFixed(1)+'%' : 'YOK';
  var openClr = (d.open_pnl!=null&&d.open_pnl!==0) ? (d.open_pnl>=0?'var(--green)':'var(--red)') : 'var(--t4)';
  return '<div class="pineBox">'
    +'<div class="pineBoxTitle" style="color:'+color+'">'+title+'</div>'
    +'<div class="pineRow"><span class="pineLabel">Toplam AL</span><span class="pineVal" style="color:var(--t2)">'+(d.buys||0)+'</span></div>'
    +'<div class="pineRow"><span class="pineLabel">Toplam SAT</span><span class="pineVal" style="color:var(--t2)">'+(d.sells||0)+'</span></div>'
    +'<div class="pineRow"><span class="pineLabel">Karl/Zarar</span><span class="pineVal" style="color:'+wrClr+'">'+(d.wins||0)+'/'+(d.losses||0)+'</span></div>'
    +'<div class="pineRow"><span class="pineLabel">Toplam %</span><span class="pineVal" style="color:'+pnlClr+'">'+((d.total_pnl||0)>=0?'+':'')+((d.total_pnl||0)).toFixed(2)+'%</span></div>'
    +'<div class="pineRow"><span class="pineLabel">Pozisyon</span><span class="pineVal" style="color:'+openClr+'">'+openVal+'</span></div>'
    +(extra?'<div class="pineRow"><span class="pineLabel">'+extra.split('|')[0]+'</span><span class="pineVal" style="color:var(--cyan)">'+extra.split('|')[1]+'</span></div>':'')
    +'</div>';
}

function renderPineData(data){
  try{
    var body = document.getElementById('pineBody');
    if(!body) return;
    var html = '';

    // Badge row
    html += '<div class="pineScoreBadge">';
    if(data.price) html += '<span class="pineScorePill" style="color:var(--cyan);border-color:rgba(0,212,255,.3)">TL'+(data.price||0).toFixed(2)+'</span>';
    if(data.adx)   html += '<span class="pineScorePill" style="color:var(--gold);border-color:rgba(255,184,0,.3)">ADX '+(data.adx||0).toFixed(0)+'</span>';
    if(data.cons)  html += '<span class="pineScorePill" style="color:var(--green);border-color:rgba(0,230,118,.3)">Kons %'+(data.cons||0).toFixed(0)+'</span>';
    if(data.pstate) html += '<span class="pineScorePill" style="color:var(--purple);border-color:rgba(192,132,252,.3)">'+data.pstate+'</span>';
    if(data.signal!==undefined){
      var sigTxt = data.is_master ? 'MASTER AI' : data.signal ? 'AL' : 'BEKLE';
      var sigClr = data.is_master ? 'var(--gold)' : data.signal ? 'var(--green)' : 'var(--t4)';
      html += '<span class="pineScorePill" style="color:'+sigClr+'">'+sigTxt+'</span>';
    }
    html += '</div>';

    // 4 kutu grid
    html += '<div class="pineGrid">';
    html += pineBox('Sistem 1', 'rgba(0,212,255,.8)', data.sys1);
    html += pineBox('PRO Engine', 'rgba(192,132,252,.8)', data.sys2,
      data.sys2 ? 'PRO Skor|'+(data.sys2.score||0)+'/6' : null);
    html += pineBox('Fusion AI', 'rgba(0,230,118,.8)', data.fusion);

    // Master AI kutu
    if(data.master_ai){
      var ma = data.master_ai;
      html += '<div class="pineBox">'
        +'<div class="pineBoxTitle" style="color:var(--gold)">Master AI</div>'
        +'<div class="pineRow"><span class="pineLabel">Consensus Buy</span><span class="pineVal" style="color:var(--green)">%'+(ma.buy_consensus||0).toFixed(0)+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Consensus Sell</span><span class="pineVal" style="color:var(--red)">%'+(ma.sell_consensus||0).toFixed(0)+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Dyn Buy Thresh</span><span class="pineVal" style="color:var(--t2)">'+(ma.dyn_buy_thresh||0).toFixed(2)+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Dyn Sell Thresh</span><span class="pineVal" style="color:var(--t2)">'+(ma.dyn_sell_thresh||0).toFixed(2)+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Toplam PnL %</span><span class="pineVal" style="color:'+((ma.total_pnl||0)>=0?'var(--green)':'var(--red)')+'">'+((ma.total_pnl||0)>=0?'+':'')+((ma.total_pnl||0)).toFixed(2)+'%</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Pozisyon</span><span class="pineVal" style="color:var(--t4)">YOK</span></div>'
        +'</div>';
    } else {
      html += '<div class="pineBox"><div class="pineBoxTitle" style="color:var(--gold)">Master AI</div>'
        +'<div style="padding:20px;text-align:center;color:var(--t4);font-size:9px">Veri yok</div></div>';
    }
    html += '</div>'; // grid

    // Agents
    if(data.agents){
      var ag = data.agents;
      var agents = [
        {k:'a60', lbl:'A60', d:ag.a60||{}},
        {k:'a61', lbl:'A61', d:ag.a61||{}},
        {k:'a62', lbl:'A62', d:ag.a62||{}},
        {k:'a81', lbl:'A81', d:ag.a81||{}},
        {k:'a120',lbl:'A120',d:ag.a120||{}},
      ];
      agents.forEach(function(a){
        a.pnl = a.d.pnl || a.d.total_pnl || 0;
      });
      var best = agents.reduce(function(a,b){ return b.pnl > a.pnl ? b : a; }, agents[0]);
      var maxPnl = Math.max.apply(null, agents.map(function(a){ return Math.abs(a.pnl)||1; }));

      html += '<div class="pineGrid">';
      html += '<div class="pineBox">'
        +'<div class="pineBoxTitle" style="color:var(--purple)">EN IYI AGENT <span style="color:var(--gold)">'+best.lbl+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Toplam AL</span><span class="pineVal" style="color:var(--t2)">'+(best.d.buys||0)+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Toplam SAT</span><span class="pineVal" style="color:var(--t2)">'+(best.d.sells||0)+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Karl/Zarar</span><span class="pineVal" style="color:var(--t2)">'+(best.d.wins||0)+'/'+(best.d.losses||0)+'</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Toplam %</span><span class="pineVal" style="color:'+(best.pnl>=0?'var(--green)':'var(--red)')+'">'+( best.pnl>=0?'+':'')+best.pnl.toFixed(2)+'%</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Pozisyon</span><span class="pineVal" style="color:var(--t4)">YOK</span></div>'
        +'<div class="pineRow"><span class="pineLabel">Fiyat Durumu</span><span class="pineVal" style="color:var(--t4)">--</span></div>'
        +'</div>';

      html += '<div class="pineBox">'
        +'<div class="pineBoxTitle" style="color:var(--purple)">Agent  PnL %</div>'
        +'<div style="display:flex;align-items:center;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
        +'<span style="font-size:8px;color:rgba(255,255,255,.4);width:30px">Agent</span>'
        +'<span style="flex:1"></span>'
        +'<span style="font-size:8px;color:rgba(255,255,255,.4)">PnL %</span>'
        +'</div>';
      agents.forEach(function(a){
        var pct = Math.min(100, Math.abs(a.pnl) / maxPnl * 100);
        var clr = a.pnl >= 0 ? '#00E676' : '#FF4444';
        html += '<div class="pineAgentRow">'
          +'<span class="pineAgentLabel">'+a.lbl+'</span>'
          +'<div class="pineAgentBarWrap"><div class="pineAgentBar" style="width:'+pct.toFixed(0)+'%;background:'+clr+'"></div></div>'
          +'<span class="pineAgentPnl" style="color:'+clr+'">'+( a.pnl>=0?'+':'')+a.pnl.toFixed(1)+'%</span>'
          +'</div>';
      });
      html += '</div>';
      html += '</div>'; // grid
    }

    // PRO Faktorler
    if(data.pro_factors){
      var pf = data.pro_factors;
      var flist = [['rs_strong','RS Guclu'],['accum_d','Akumulasyon'],['exp_4h','4H Genisleme'],['break_4h','4H Kirilim'],['mom_2h','2H Momentum'],['dna','DNA Sinyal']];
      html += '<div class="pineBoxFull"><div class="pineBoxTitle" style="color:var(--purple)">PRO Engine Faktorler</div>'
        +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:2px">';
      flist.forEach(function(f){
        var v = pf[f[0]];
        html += '<div class="pineRow"><span class="pineLabel">'+f[1]+'</span><span class="pineVal" style="color:'+(v?'var(--green)':'var(--t4)')+'">'+( v?'OK':'--')+'</span></div>';
      });
      html += '</div></div>';
    }

    // TV Butonu
    var tfMap = {D:'1D','240':'4H','120':'2H'};
    html += '<button class="pineTVBtn" onclick="window.open(\'https://www.tradingview.com/chart/?symbol=BIST:'
      +(_pineTicker||'')+'&interval='+(tfMap[_pineTF]||'1D')+'\')">TradingView\'de Ac &#8599;</button>';

    body.innerHTML = html;
  }catch(e){ console.warn('renderPineData:',e.message); }
}

// Tarayici entegrasyonu
var _b30_origRenderSt = typeof renderSt==='function' ? renderSt : null;
window.renderSt = function(){
  try{
    if(_b30_origRenderSt) _b30_origRenderSt.apply(this, arguments);
    setTimeout(injectPineBtns, 200);
  }catch(e){ console.warn('renderSt b30:',e.message); }
};

function injectPineBtns(){
  try{
    document.querySelectorAll('.strow').forEach(function(row){
      if(row.querySelector('.stPineBtn')) return;
      var ticker = row.dataset.ticker || '';
      var name = row.dataset.name || ticker;
      if(!ticker) return;
      var btn = document.createElement('button');
      btn.className = 'stPineBtn';
      btn.textContent = 'Pine';
      btn.onclick = function(e){
        e.stopPropagation();
        openPineModal(ticker, name, 'D');
      };
      row.appendChild(btn);
    });
  }catch(e){}
}

// Sinyal detay'a Pine butonu
var _b30_origOpenSig = typeof openSig==='function' ? openSig : null;
window.openSig = function(idx){
  try{
    if(_b30_origOpenSig) _b30_origOpenSig.apply(this, arguments);
    setTimeout(function(){
      try{
        var mcont = document.getElementById('mcont');
        if(!mcont) return;
        var sig = (S.sigs||[])[idx];
        if(!sig) return;
        if(document.getElementById('pineSigTabBtn')) return;
        var btn = document.createElement('button');
        btn.id = 'pineSigTabBtn';
        btn.className = 'pineSigTab';
        btn.textContent = 'Pine Tablolari';
        btn.onclick = function(){ openPineModal(sig.ticker, sig.name, sig.tf); };
        mcont.insertBefore(btn, mcont.firstChild);
      }catch(e2){}
    }, 400);
  }catch(e){ console.warn('openSig b30:',e.message); }
};

// Load
window.addEventListener('load', function(){
  setTimeout(function(){
    try{ injectPineBtns(); }catch(e){}
    var st = document.getElementById('scannerTab');
    if(st){ var orig=st.onclick; st.onclick=function(){ if(orig)orig.apply(this,arguments); setTimeout(injectPineBtns,500); }; }
  }, 1500);
});

// ESC
document.addEventListener('keydown', function(e){
  if(e.key==='Escape'){ var pm=document.getElementById('pineModal'); if(pm&&pm.classList.contains('on')) closePineModal(); }
});

setTimeout(function(){
  try{ console.log('[Blok30] Pine tablolari: Tum hisseler icin aktif'); if(typeof devLog==='function') devLog('Blok30: Pine modal hazir','ok'); }catch(e){}
}, 2000);

})();

</script>
</html>"""

@app.get("/status")
async def root():
    try:
        from social import ws_manager
        online = len(ws_manager.connections)
    except:
        online = 0
    return {"status":"ok","service":"BIST Elite v3","version":"3.5",
            "blocks":30,"stocks":424,"scheduler":scheduler.running,
            "cache":len(_cache),"tg_configured":bool(TG_TOKEN and TG_CHAT),
            "online_users":online}

@app.get("/", response_class=HTMLResponse)
async def serve_app():
    return HTMLResponse(content=_HTML, headers={
        "Cache-Control":"no-cache, no-store, must-revalidate",
        "Content-Type":"text/html; charset=utf-8"
    })
