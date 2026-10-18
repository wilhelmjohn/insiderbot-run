"""Hämtar årsbokslut (resultat, balans, kassaflöde) från Yahoo för alla bolag i riktkurs_test_handelser.csv.
Skriver nyckel_ar.csv i långt format (Ticker,Period,Falt,Varde). Används av riktkurs_nyckel.py."""
import time, pandas as pd, yfinance as yf
tick = sorted(pd.read_csv("riktkurs_test_handelser.csv").Ticker.dropna().unique())
FALT = {"is": ["Total Revenue", "Operating Income", "Net Income Common Stockholders", "Net Income", "Diluted EPS", "Basic EPS", "EBITDA", "Diluted Average Shares"],
        "bs": ["Stockholders Equity", "Common Stock Equity", "Total Debt", "Cash And Cash Equivalents", "Total Assets", "Ordinary Shares Number", "Share Issued"],
        "cf": ["Free Cash Flow", "Operating Cash Flow"]}
ut, ok, fel = [], 0, []
for n, t in enumerate(tick, 1):
    try:
        k = yf.Ticker(t)
        for typ, df in (("is", k.income_stmt), ("bs", k.balance_sheet), ("cf", k.cashflow)):
            if df is None or df.empty: continue
            for f in FALT[typ]:
                if f in df.index:
                    for per, v in df.loc[f].items():
                        if pd.notna(v): ut.append((t, pd.Timestamp(per).strftime("%Y-%m-%d"), f, float(v)))
        ok += 1
    except Exception as e:
        fel.append(t)
    if n % 25 == 0: print(n, len(tick), flush=True)
    time.sleep(0.4)
pd.DataFrame(ut, columns=["Ticker", "Period", "Falt", "Varde"]).to_csv("nyckel_ar.csv", index=False)
print("OK", ok, "av", len(tick), "SAKNAS:", " ".join(fel)); print("KLAR")
