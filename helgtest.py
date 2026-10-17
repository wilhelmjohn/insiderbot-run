#!/usr/bin/env python3
"""Engångstest: finns det publiceringar daterade på lördag/söndag hos FI? Frågar alla helgdagar de senaste 26 veckorna
(och 2 veckodagar som kontroll) och skriver helg_logg.txt. Sparar ingenting i insynsdatan."""
import time
from datetime import date, timedelta

import requests

import insider_bot as ib


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                      "Accept": "text/html,application/xhtml+xml", "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"})
    rader = []
    idag = date.today()
    dagar = [idag - timedelta(days=i) for i in range(1, 26 * 7 + 1)]
    helg = [d for d in dagar if d.weekday() >= 5]
    kontroll = [d for d in dagar if d.weekday() < 5][:2]
    for d in sorted(helg + kontroll):
        try:
            df, komplett = ib.hamta_dag(s, d)
            rader.append(f"{d} {['mån','tis','ons','tor','fre','lör','sön'][d.weekday()]} rader={len(df)} komplett={komplett}")
        except Exception as e:
            rader.append(f"{d} fel {e.__class__.__name__}")
        time.sleep(0.5)
    helgrader = sum(int(r.split("rader=")[1].split()[0]) for r in rader if (" lör " in r or " sön " in r) and "rader=" in r)
    rader.append(f"SUMMA helgrader: {helgrader}")
    open("helg_logg.txt", "w").write("\n".join(rader) + "\n")
    print("\n".join(rader))


if __name__ == "__main__":
    main()
