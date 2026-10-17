"""Hämtar FI:s blankningsregister och sparar:
  blankning.csv      aktuell summerad nettoblankning per bolag (alla positioner över 0,1 %) samt förändring de senaste
                     20 handelsdagarna för de stora innehaven (över 0,5 % per innehavare)
  blankning_dag.csv  daglig ögonblicksbild av summerad blankning (byggs på framåt för framtida tester)
Körs i GitHub Actions (FI nås inte från sessionen). Kräver odfpy."""
import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

MAPP = Path(__file__).resolve().parent
BAS = "https://www.fi.se/sv/vara-register/blankningsregistret"
FILER = {"agg": "GetBlankningsregisterAggregat", "akt": "GetAktuellFile", "hist": "GetHistFile"}


def hamta(namn):
    r = requests.get(f"{BAS}/{FILER[namn]}/", headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    r.raise_for_status()
    return pd.read_excel(io.BytesIO(r.content), engine="odf", header=5)


def main():
    agg = hamta("agg")
    agg.columns = ["Emittent", "LEI", "Niva", "Datum"][: len(agg.columns)]
    agg["Niva"] = pd.to_numeric(agg["Niva"].astype(str).str.replace(",", "."), errors="coerce")
    agg = agg.dropna(subset=["Emittent", "Niva"])
    agg["Emittent"] = agg["Emittent"].astype(str).str.strip()

    # Förändring 20 handelsdagar för stora innehav: summa av innehavarnas senast kända nivå per dag
    forandring = {}
    try:
        h, a = hamta("hist"), hamta("akt")
        for df in (h, a):
            df.columns = ["Innehavare", "Emittent", "ISIN", "Pos", "Datum", "Kommentar"][: len(df.columns)]
        x = pd.concat([h, a])
        x["d"] = pd.to_datetime(x["Datum"], errors="coerce")
        x["p"] = pd.to_numeric(x["Pos"].astype(str).str.replace(",", "."), errors="coerce").fillna(0)  # "<0,5" = avslutad
        x["Emittent"] = x["Emittent"].astype(str).str.strip()
        x = x.dropna(subset=["d", "Emittent"]).sort_values("d").drop_duplicates(["Innehavare", "Emittent", "d"], keep="last")
        dagar = pd.bdate_range(x["d"].min(), pd.Timestamp.today().normalize())
        for emi, g in x.groupby("Emittent"):
            w = g.pivot_table(index="d", columns="Innehavare", values="p", aggfunc="last")
            w = w.reindex(dagar.union(w.index)).ffill().fillna(0).reindex(dagar)
            s = w.sum(axis=1)
            if len(s) > 21:
                forandring[emi] = round(float(s.iloc[-1] - s.iloc[-21]), 2)
    except Exception as e:  # förändringen är bonus; nivån räcker
        print("förändring misslyckades:", e)

    agg["Forandring_20d"] = agg["Emittent"].map(forandring)
    agg.to_csv(MAPP / "blankning.csv", index=False)
    print(f"blankning.csv: {len(agg)} bolag, {agg['Forandring_20d'].notna().sum()} med förändring")

    dag = agg[["Emittent", "Niva"]].copy()
    dag.insert(0, "Dag", date.today().isoformat())
    f = MAPP / "blankning_dag.csv"
    if f.exists():
        gl = pd.read_csv(f)
        gl = gl[gl["Dag"] != date.today().isoformat()]
        dag = pd.concat([gl, dag])
    dag.to_csv(f, index=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("blankning misslyckades:", e)
        sys.exit(0)
