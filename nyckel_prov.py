"""Prov: hur långt bak ger Yahoo kvartals- och årssiffror (resultat, balans) för svenska bolag?"""
import pandas as pd, yfinance as yf, time
T = ["VOLV-B.ST","ERIC-B.ST","HM-B.ST","ATCO-A.ST","EVO.ST","SBB-B.ST","NCC-B.ST","BILL.ST","SINCH.ST","KINV-B.ST","ELUX-B.ST","AZN.ST"]
ut = []
for t in T:
    k = yf.Ticker(t)
    rad = {"Ticker": t}
    for namn, f in (("kv_resultat", lambda: k.quarterly_income_stmt), ("kv_balans", lambda: k.quarterly_balance_sheet),
                    ("ar_resultat", lambda: k.income_stmt), ("ar_balans", lambda: k.balance_sheet)):
        try:
            d = f()
            rad[namn] = f"{d.shape[1]} perioder {d.columns.min().date()}..{d.columns.max().date()} rader={d.shape[0]}" if d is not None and not d.empty else "tom"
        except Exception as e:
            rad[namn] = "fel:" + str(e)[:60]
    try:
        i = k.info
        rad["info"] = {x: i.get(x) for x in ("trailingPE","forwardPE","priceToBook","debtToEquity","returnOnEquity","profitMargins","marketCap","sector")}
    except Exception as e:
        rad["info"] = "fel:" + str(e)[:60]
    try:
        rad["eps_hist"] = str(k.get_earnings_dates(limit=40).shape)
    except Exception as e:
        rad["eps_hist"] = "fel:" + str(e)[:60]
    ut.append(rad); print(rad, flush=True); time.sleep(0.5)
try:
    k = yf.Ticker("VOLV-B.ST")
    k.quarterly_income_stmt.to_csv("nyckel_prov_exempel_resultat.csv"); k.quarterly_balance_sheet.to_csv("nyckel_prov_exempel_balans.csv")
except Exception as e:
    print("exempel fel", e)
pd.DataFrame(ut).to_csv("nyckel_prov.csv", index=False)
print("KLAR")
