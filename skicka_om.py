"""Skickar om notiser för angivna tickers (test av notisutseendet). Rör inte notis_skickade.txt.

python skicka_om.py REJL-B.ST,CALVIK.ST
"""
import os
import sys
from pathlib import Path

import bevakning
import insider_bot as ib
import notis

MAPP = Path(__file__).parent


def main(tickers, topic):
    kop = ib.bygg_koplista(ib.las_historik())
    kop.to_csv(ib.KOPLISTA, index=False)
    res = bevakning.bevaka(ib.KOPLISTA, MAPP / "ticker_map.csv", MAPP / "kurser.csv",
                           nyheter_fil=MAPP / "nyheter.csv", diagram_mapp=MAPP / "diagram")
    val = res[res["Ticker"].isin(tickers)]
    print(f"{len(val)} av {len(tickers)} tickers hittades: {list(val['Ticker'])}")
    msgs = [notis.meddelande(r) for _, r in val.iterrows()]
    for m in msgs:
        m["title"] = "(Omsänd) " + m["title"]
        print("-", m["title"], "| bild:", bool(m.get("bild")))
    if topic and msgs:
        notis.skicka(msgs, topic, os.environ.get("NTFY_SERVER", "https://ntfy.sh"))
        print("Skickat.")
    else:
        print("Inget skickat (topic saknas eller inga fall).")


if __name__ == "__main__":
    main([t.strip() for t in sys.argv[1].split(",") if t.strip()], os.environ.get("NTFY_TOPIC", "").strip() or None)
