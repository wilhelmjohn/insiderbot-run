#!/usr/bin/env python3
"""
Notiser: skickar nya fall från bevakning.csv till telefonen via ntfy (https://ntfy.sh).

Ett fall skickas en gång. Får det fler personer (t.ex. en tredje köper) räknas det som
ett nytt fall och skickas igen. Vad som redan skickats sparas i notis_skickade.txt.
Första körningen skickar inget gammalt: alla befintliga fall markeras som skickade
och du får bara ett bekräftelsemeddelande.

Sätt upp:
  1. Installera appen ntfy (iOS/Android) och prenumerera på ett eget, svårgissat ämne,
     t.ex. insiderbot-<slumpmässig text> (ämnesnamnet fungerar som lösenord: alla som känner det
     kan läsa notiserna, så skriv det inte i öppna kanaler).
  2. GitHub: Settings > Secrets and variables > Actions > New repository secret:
     Name NTFY_TOPIC, värde = ämnesnamnet.
  Workflowen skickar sedan automatiskt. Utan NTFY_TOPIC skickas ingenting.

Kör lokalt:
  NTFY_TOPIC=ditt-amne python notis.py --test      # skickar ett testmeddelande
  python notis.py --prov                           # visar vad som skulle skickas
  python notis.py --endast-fall                    # bara fall efter kursfall
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import requests

MAPP = Path(__file__).resolve().parent
BEVAKNING = MAPP / "bevakning.csv"
SKICKADE = MAPP / "notis_skickade.txt"
AKTIVERAD = "AKTIVERAD|ntfy"
MAX_BEVAKA_FULL = 5
MAX_PER_KORNING = 6   # fler än så samlas i ett meddelande


def fall_id(r) -> str:
    """Ett fall är bolaget, första publiceringsdatum och antal personer."""
    return f"{r['Ticker']}|{r['Forsta_publ']}|{int(r['Antal_personer'])}"


def nya_fall(b: pd.DataFrame, kanda: set) -> pd.DataFrame:
    """Fall som inte redan skickats. Ett fall är ett bolag (episod), inte en exakt kombination av
    första publicering och antal personer: bevakningsfönstret är rullande, så när gamla köp faller ur
    fönstret ändras antal personer och första publicering för ett gammalt fall utan att något nytt hänt.
    Därför skickas ett bolag igen bara om fler personer köpt än tidigare skickats, eller om första
    publicering ligger mer än 45 dagar efter den senast skickade (ny episod)."""
    sedda = {}
    for x in kanda:
        p = x.split("|")
        if len(p) == 3 and p[2].isdigit():
            try:
                f = pd.Timestamp(p[1])
            except ValueError:
                continue
            m0, f0 = sedda.get(p[0], (0, f))
            sedda[p[0]] = (max(m0, int(p[2])), max(f0, f))
    ut = []
    for i, r in b.iterrows():
        s = sedda.get(r["Ticker"])
        if s is None:
            ut.append(i)
            continue
        if int(r["Antal_personer"]) > s[0] or (pd.Timestamp(r["Forsta_publ"]) - s[1]).days > 45:
            ut.append(i)
    return b.loc[ut]


def _tal(x, fmt="{:,.0f}"):
    return "" if pd.isna(x) else fmt.format(x).replace(",", " ")


def _namn(r) -> str:
    """Bolagsnamn med ticker (FI använder ibland gamla bolagsnamn, t.ex. Job Solution = Calviks)."""
    tk = str(r.get("Ticker", "")).replace(".ST", "")
    return f"{r['Bolag']} ({tk})" if tk and tk != "nan" else str(r["Bolag"])


def meddelande(r) -> dict:
    """ntfy-meddelande (JSON) för ett fall."""
    rader = [f"{int(r['Antal_personer'])} personer, {_tal(r['Belopp'])} kr",
             str(r.get("Personer", ""))[:160]]
    if str(r.get("Serieskopare", "")) not in ("", "nan"):
        rader.append("Serieköpare: " + str(r["Serieskopare"]) + " (info, påverkar inte poängen; i backtestet inte sämre utfall)")
    lage = {"snabbt_fall": "rasat de senaste veckorna", "fall": "långt under 52v-högsta",
            "ej_fall": "ej efter kursfall"}.get(r["Tillstand"], r["Tillstand"])
    under = r.get("Under_52v_hogsta_%")
    lage += "" if pd.isna(under) else f" ({under:.0f} % från toppen)"
    rader.append(f"Läge: {lage}")
    if pd.notna(r.get("Kopkurs")) and pd.notna(r.get("Kurs_nu")):
        rader.append(f"Köpkurs {r['Kopkurs']:.2f}, nu {r['Kurs_nu']:.2f} ({r['Utv_sedan_kop_%']:+.0f} %)")
    if str(r.get("Nyhet_fore", "")) not in ("", "nan", "ingen_nyhet", "okand"):
        rader.append(f"Före köpet: {r['Nyhet_fore']}. {str(r.get('Nyhet_rubrik', ''))[:100]}")
    if str(r.get("Kontroll", "")) == "kurs avviker":
        rader.append("Kontrollera: köpkursen avviker från marknadskursen (split/emission/uppköp?)")
    regel = str(r.get("Regel", "")) == "ja"
    if pd.notna(r.get("Poang")):
        rader.insert(0, f"POÄNG {r['Poang']:.0f}/100 ({r.get('Poang_skal', '')})")
    if regel:
        rader.insert(0, f"REGELTRÄFF: över MA200, ej efter fall, omsättning {_tal(r.get('Oms_MSEK_dag'), '{:.1f}')} MSEK/dag")
    else:
        why = str(r.get("Varfor_ej_regel", ""))
        rader.insert(0, "BEVAKA (ingen regelträff, ingen testad edge): " + (why if why not in ("", "nan") else "uppfyller inte regeln"))
    if pd.notna(r.get("Dagar_sedan_rapport")) or pd.notna(r.get("Dagar_till_rapport")):
        rp = []
        if pd.notna(r.get("Dagar_sedan_rapport")):
            rp.append(f"senaste rapport för {int(r['Dagar_sedan_rapport'])} dagar sedan ({r['Rapport_senast']})")
        if pd.notna(r.get("Dagar_till_rapport")):
            rp.append(f"nästa om ca {int(r['Dagar_till_rapport'])} dagar ({r['Rapport_nasta_uppsk']}, {r.get('Rapport_kalla') if str(r.get('Rapport_kalla', '')) not in ('', 'nan') else 'uppskattad'})")
        txt = "Rapport: " + "; ".join(rp)
        if pd.notna(r.get("Dagar_sedan_rapport")) and r["Dagar_sedan_rapport"] <= 5:
            txt += ". Precis efter rapport: historiskt svagaste läget (+5 % mot +8 % på 6 mån)"
        rader.append(txt)
    if str(r.get("Rod_flagga", "")) not in ("", "nan"):
        rader.append(f"RÖD FLAGGA: {r['Rod_flagga']}")
    prio = 4 if regel else 2
    tagg = "white_check_mark" if regel else ("chart_with_downwards_trend" if r["Tillstand"] != "ej_fall" else "moneybag")
    if regel and str(r.get("Rod_flagga", "")) not in ("", "nan"):
        tagg = "warning"
    if str(r.get("Kommentar", "")) not in ("", "nan"):
        rader.append(str(r["Kommentar"]))
    if str(r.get("Teknik", "")) not in ("", "nan"):
        rader.append("Teknik: " + str(r["Teknik"]).replace(" | ", "\n"))
    bild = MAPP / "diagram" / str(r["Diagram"]) if str(r.get("Diagram", "")) not in ("", "nan") else None
    return {"bild": str(bild) if bild and bild.exists() else None,
            "title": f"{'Regelträff' if regel else 'Insiderköp'}{'' if pd.isna(r.get('Poang')) else f' {r[chr(80)+chr(111)+chr(97)+chr(110)+chr(103)]:.0f}'}: {_namn(r)}", "message": "\n".join(x for x in rader if x),
            "priority": prio, "tags": [tagg, "insiderbot"]}


# Knappen "Öppna bevakningen" i notisen öppnar bevakningssidan (privat artifakt, kräver inloggning på claude.ai).
# Ett tryck på själva notisen öppnar den i ntfy (text och diagram), så ingen click-länk sätts.
KLICK_URL = os.environ.get("NTFY_KLICK", "https://claude.ai/artifact/4hi1JrjeYgkbTPsVWXfJQq")


def _hdr(s: str) -> str:
    """HTTP-header med svenska tecken/radbrytningar som RFC 2047 (ntfy avkodar det)."""
    import base64
    if s.isascii() and "\n" not in s:
        return s
    return "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode() + "?="


def skicka(meddelanden: list, topic: str, server: str) -> None:
    """Ett meddelande per fall. Med diagram skickas bilden som bilaga i SAMMA notis (PUT), annars JSON. Båda har
    en knapp som öppnar bevakningssidan."""
    bas = server.rstrip("/")
    for m in meddelanden:
        bild = m.get("bild")
        text = {k: v for k, v in m.items() if k != "bild"}
        if bild and Path(bild).exists():
            h = {"Filename": Path(bild).name, "Title": _hdr(text.get("title", "")), "Message": _hdr(text.get("message", "")),
                 "Priority": str(text.get("priority", 3)), "Tags": ",".join(text.get("tags", []))}
            if KLICK_URL:
                h["Actions"] = _hdr(f"view, Öppna bevakningen, {KLICK_URL}")
            requests.put(f"{bas}/{topic}", data=Path(bild).read_bytes(), headers=h, timeout=60).raise_for_status()
            continue
        if KLICK_URL:
            text.setdefault("actions", [{"action": "view", "label": "Öppna bevakningen", "url": KLICK_URL}])
        requests.post(bas + "/", json={"topic": topic, **text}, timeout=30).raise_for_status()


def marknad_notis(topic, server="https://ntfy.sh", prov=False, skickade=SKICKADE, marknad_csv=MAPP / "marknad.csv"):
    """Dagens marknadsstatus (-10..+10) med diagram, en gång per dag."""
    if not Path(marknad_csv).exists():
        return
    import analys
    import marknad as mk
    d = pd.read_csv(marknad_csv, index_col=0, parse_dates=True)
    d["Vandning"] = d["Vandning"].fillna("")
    dag = d.index[-1].date().isoformat()
    id_ = f"MARKNAD|{dag}"
    kanda = set(Path(skickade).read_text().split()) if Path(skickade).exists() else set()
    if id_ in kanda:
        return
    t = d.iloc[-1]
    forra = d["Trend"].iloc[-6] if len(d) > 6 else t["Trend"]
    msg = {"title": f"Marknad {t['Trend']:+.1f}/10", "priority": 3, "tags": ["bar_chart"],
           "message": f"{mk.status(d)}\nFörändring senaste veckan: {t['Trend'] - forra:+.1f}\n(-10 superbear, 0 sidlängs, +10 superbull)"}
    png = MAPP / "diagram" / "marknad.png"
    png.parent.mkdir(exist_ok=True)
    try:
        analys.rita_marknad(marknad_csv, png)
        msg["bild"] = str(png)
    except Exception as e:
        print(f"Marknadsdiagram misslyckades: {e}")
    print("MARKNAD:", msg["title"], msg["message"].replace("\n", " | "))
    if prov or not topic:
        return
    skicka([msg], topic, server)
    with open(skickade, "a") as f:
        f.write(id_ + "\n")


def morgon_sammanfattning(topic, server="https://ntfy.sh", prov=False, idag=None, skicka_fn=None):
    """Morgonnotis: vad som publicerats av FI sedan förra handelsdagens kvällskörning, även när inget nytt hänt.
    Fönstret är förra vardagen till idag (på måndagen alltså fredag-måndag, så helgen ingår)."""
    skicka_fn = skicka_fn or skicka
    idag = pd.Timestamp(idag or pd.Timestamp.now(tz="Europe/Stockholm").date()).normalize()
    start = idag - pd.Timedelta(days=1)
    while start.weekday() >= 5:             # förra vardagen (kvällskörningen gäller handelsdagar)
        start -= pd.Timedelta(days=1)
    k = pd.read_csv(MAPP / "insiderkop.csv", usecols=["Publiceringsdatum", "Bolag_id", "Emittent", "Belopp"], parse_dates=["Publiceringsdatum"])
    nya = k[k["Publiceringsdatum"] >= start]
    dagar = {0: "mån", 1: "tis", 2: "ons", 3: "tors", 4: "fre", 5: "lör", 6: "sön"}
    per_dag = ", ".join(f"{dagar[d.weekday()]} {int(n)}" for d, n in nya.groupby("Publiceringsdatum").size().items())
    rader = [f"Insynsköp publicerade sedan {dagar[start.weekday()]} {start:%d/%m}: {len(nya)}" + (f" ({per_dag})" if per_dag else "")]
    helg = nya[nya["Publiceringsdatum"].dt.weekday >= 5]
    if start.weekday() == 4 and idag.weekday() == 0:
        rader.append("Lördag och söndag: " + (f"{len(helg)} publicerade" if len(helg) else "FI publicerade inget över helgen"))
    tm = pd.read_csv(MAPP / "ticker_map.csv")
    bolag_tk = dict(zip(tm["Bolag_id"], tm["Ticker"]))
    b = pd.read_csv(BEVAKNING, keep_default_na=False, na_values=[""], dtype={"Ticker": str}) if BEVAKNING.exists() else pd.DataFrame()
    if not b.empty:
        bnu = b[pd.to_datetime(b["Forsta_publ"], errors="coerce") >= start]
        if len(bnu):
            rader.append(f"Nya fall på bevakningslistan ({len(bnu)}): " + ", ".join(
                f"{_namn(r)} ({int(r['Antal_personer'])} pers, {_tal(r['Belopp'] / 1e6, '{:.1f}').replace('.', ',')} Mkr)" for _, r in bnu.head(6).iterrows()))
        else:
            rader.append("Inga nya fall på bevakningslistan.")
        ticks = set(b["Ticker"])
        mer = nya.assign(Ticker=nya["Bolag_id"].map(bolag_tk))
        mer = mer[mer["Ticker"].isin(ticks) & ~mer["Ticker"].isin(set(bnu["Ticker"]))]
        if len(mer):
            g = mer.groupby("Ticker")["Belopp"].agg(["count", "sum"]).sort_values("sum", ascending=False)
            namn = dict(zip(b["Ticker"], b["Bolag"]))
            rader.append("Fler köp i bolag som redan bevakas: " + ", ".join(
                f"{str(namn.get(t, t))[:26]} ({int(r['count'])} köp, {_tal(r['sum'] / 1e3)} tkr)" for t, r in g.head(6).iterrows()))
    msg = {"title": "God morgon: insyn sedan " + f"{dagar[start.weekday()]}", "message": "\n".join(rader), "priority": 2, "tags": ["sunrise", "insiderbot"]}
    print(msg["title"] + "\n" + msg["message"])
    if prov or not topic:
        return msg
    skicka_fn([msg], topic, server)
    return msg


def kor(bevakning=BEVAKNING, skickade=SKICKADE, topic=None, server="https://ntfy.sh",
        prov=False, endast_fall=False, skicka_fn=skicka) -> dict:
    if not Path(bevakning).exists():
        print("bevakning.csv saknas.")
        return {"nya": 0, "skickade": 0}
    b = pd.read_csv(bevakning, keep_default_na=False, na_values=[""], dtype={"Ticker": str})
    if endast_fall:
        b = b[b["Tillstand"] != "ej_fall"]
    b = b.assign(_id=b.apply(fall_id, axis=1))
    kanda = set(Path(skickade).read_text().split()) if Path(skickade).exists() else set()
    # Aktivering: sker första gången en notis faktiskt kan skickas (NTFY_TOPIC satt). Då markeras alla befintliga
    # fall som skickade (ingen bunt gamla notiser) och ett bekräftelsemeddelande skickas. Utan NTFY_TOPIC
    # markeras ingenting. Filen kan också innehålla MARKNAD|-rader från marknadsnotisen.
    aktiverad = AKTIVERAD in kanda
    nya = nya_fall(b, kanda)

    if not aktiverad:
        if not topic:
            print("NTFY_TOPIC saknas: ingen aktivering, inget skickas.")
            return {"nya": 0, "skickade": 0, "aktiverad": False}
        print(f"Aktiverar notiser: markerar {len(b)} befintliga fall som skickade.")
        if not prov:
            skicka_fn([{"title": "Insiderbot aktiverad",
                        "message": f"Notiser på. {len(b)} befintliga fall ignoreras, bara nya skickas.",
                        "priority": 2, "tags": ["white_check_mark"]}], topic, server)
            with open(skickade, "a") as f:     # sparas först när bekräftelsen gått iväg
                f.write("\n".join(list(b["_id"]) + [AKTIVERAD]) + "\n")
        return {"nya": 0, "skickade": 0, "aktiverad": True}

    print(f"{len(nya)} nya fall")
    if nya.empty:
        return {"nya": 0, "skickade": 0}
    # snabbt_fall först, sedan flest personer
    ordning = {"snabbt_fall": 1, "fall": 2, "ej_fall": 3}
    nya = nya.assign(_o=nya["Tillstand"].map(ordning).fillna(4).where(nya.get("Regel", "nej") != "ja", 0)).sort_values(
        ["_o", "Antal_personer", "Belopp"], ascending=[True, False, False])
    regelfall = nya[nya.get("Regel", "nej") == "ja"] if "Regel" in nya else nya.iloc[0:0]
    bevaka = nya.drop(regelfall.index)
    att_skicka = [meddelande(r) for _, r in regelfall.head(MAX_PER_KORNING).iterrows()]
    if len(regelfall) > MAX_PER_KORNING:
        rest = regelfall.iloc[MAX_PER_KORNING:]
        att_skicka.append({"title": f"{len(rest)} fler regelträffar",
                           "message": ", ".join(rest["Bolag"].astype(str)), "priority": 3, "tags": ["insiderbot"]})
    if not bevaka.empty:
        # Bevaka (ingen regelträff, ingen testad edge): de MAX_BEVAKA_FULL med högst poäng får eget
        # lågprioriterat meddelande med poäng, teknisk analys och graf; resten samlas i en sammanfattning.
        bevaka = bevaka.sort_values(["Poang", "Belopp"], ascending=False)
        for _, r in bevaka.head(MAX_BEVAKA_FULL).iterrows():
            att_skicka.append(meddelande(r))
        rest = bevaka.iloc[MAX_BEVAKA_FULL:]
        if not rest.empty:
            rader = [f"{_namn(r)[:34]}: {int(r['Antal_personer'])} pers, {_tal(r['Belopp'] / 1e6, '{:.1f}')} Mkr, "
                     f"poäng {_tal(r.get('Poang'), '{:.0f}')}/100" for _, r in rest.head(12).iterrows()]
            if len(rest) > 12:
                rader.append(f"... och {len(rest) - 12} till (se bevakning.csv)")
            att_skicka.append({"title": f"Bevaka: {len(rest)} till utan regelträff",
                               "message": "\n".join(rader) + "\nIngen testad edge: håll koll, köp inte utan egen bedömning.",
                               "priority": 2, "tags": ["eyes", "insiderbot"]})
    for m in att_skicka:
        print(f"- {m['title']}\n  " + m["message"].replace("\n", "\n  "))
    if prov or not topic:
        if not topic:
            print("NTFY_TOPIC saknas: inget skickades (och fallen markeras inte som skickade).")
        return {"nya": len(nya), "skickade": 0}
    skicka_fn(att_skicka, topic, server)
    with open(skickade, "a") as f:          # sparas först när det lyckats skickas
        f.write("\n".join(nya["_id"]) + "\n")
    return {"nya": len(nya), "skickade": len(att_skicka)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="skicka ett testmeddelande")
    ap.add_argument("--prov", action="store_true", help="visa utan att skicka eller spara")
    ap.add_argument("--endast-fall", action="store_true")
    ap.add_argument("--morgon", action="store_true", help="skicka morgonsammanfattningen")
    ap.add_argument("--sida", default=None, help="skicka kort notis att bevakningssidan är uppdaterad (text)")
    a = ap.parse_args()
    topic = os.environ.get("NTFY_TOPIC", "").strip() or None
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    if a.test:
        if not topic:
            raise SystemExit("Sätt NTFY_TOPIC först.")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        f = MAPP / "test_diagram.png"
        fig, ax = plt.subplots(figsize=(6, 3)); ax.plot([1, 3, 2, 4, 5]); ax.set_title("Exempeldiagram"); fig.savefig(f); plt.close(fig)
        skicka([{"bild": str(f), "title": "Insiderbot test: åäö", "message": "REGELTRÄFF (test)\n2 personer, 1,5 Mkr\nTryck på knappen för att öppna bevakningssidan.",
                 "priority": 3, "tags": ["white_check_mark"]}], topic, server)
        print("Testmeddelande skickat.")
        return
    if a.sida is not None:
        if not topic:
            raise SystemExit("NTFY_TOPIC saknas.")
        skicka([{"title": "Bevakningssidan uppdaterad", "message": a.sida or "Kvällsuppdateringen är klar.",
                 "priority": 2, "tags": ["bar_chart", "insiderbot"]}], topic, server)
        print("Sidnotis skickad.")
        return
    if a.morgon:
        morgon_sammanfattning(topic, server, prov=a.prov)
        return
    marknad_notis(topic, server, prov=a.prov)
    kor(topic=topic, server=server, prov=a.prov, endast_fall=a.endast_fall)


if __name__ == "__main__":
    main()
