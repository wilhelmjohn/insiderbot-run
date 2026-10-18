"""Hämtar dagskurser (stängning, volym) för bolag som saknas i kurser.csv men förekommer i analytikerlistorna.
Skriver kurser_extra.csv (Datum,Ticker,Stang,Volym) från 2020-06-01. Används av riktkurs_test.py."""
import sys, time
import pandas as pd, yfinance as yf
T = """ATCO-A ATCO-B ALIV-SDB AZN NDA-SE EPI-B FNOX HPOL-B LUMI NCC-B NYF SAGA-B SSAB-A STE-R FING-B SHB-B TIETOO CINT COIC RESURS TFBANK BFG N24 TIGO-SDB BIOT CALTX NPAPER VIT-B OX2 COLL CARY TDVOX PROB IMPL-SDB TOBII NOBINA THUNDR SAS VESTUM READ MAG KARO VIVA ARISE SEMC IPCO ADDV-B AWRD OFFHUS PAGERO LUMENR GHP ASPIRE PERM SIGNUP NILAR AZELIO ZORDIX SBB-D BICO SWMA ICA KIND-SDB BILL-B VOLV-A SKF-A INDU-A SAND SWED-A EVO""".split()
ut, ok, fel = [], [], []
for t in T:
    tk = t + ".ST"
    try:
        h = yf.Ticker(tk).history(start="2020-06-01", auto_adjust=False)
        if h is None or len(h) < 50: fel.append(tk); continue
        h = h.reset_index()
        d = pd.DataFrame({"Datum": pd.to_datetime(h["Date"]).dt.tz_localize(None).dt.strftime("%Y-%m-%d"), "Ticker": tk, "Stang": h["Close"], "Volym": h["Volume"]})
        ut.append(d); ok.append(f"{tk}:{len(d)}")
    except Exception as e:
        fel.append(tk)
    time.sleep(0.3)
if ut: pd.concat(ut).to_csv("kurser_extra.csv", index=False)
print("HÄMTADE:", " ".join(ok)); print("SAKNAS:", " ".join(fel)); print("KLAR")
