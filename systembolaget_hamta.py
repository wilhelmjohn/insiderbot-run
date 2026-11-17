"""Hämtar Systembolagets släpplistor (vin) för ett datumintervall med Playwright.

Använder sidans egna svar (productsearch) när de går att fånga, annars läses
produktkorten i sidan. Skriver JSON till katalogen i argv[1].
Användning: python systembolaget_hamta.py UTKATALOG FRAN TILL kategori1,kategori2,...
"""
import json, os, re, sys, time
from playwright.sync_api import sync_playwright

ut, fran, till = sys.argv[1], sys.argv[2], sys.argv[3]
kategorier = sys.argv[4].split(",")
os.makedirs(ut, exist_ok=True)


def url_for(kat):
    return (f"https://www.systembolaget.se/sortiment/{kat}/?kategori=Vin"
            f"&saljstart-fran={fran}&saljstart-till={till}")


def parse_cards(text):
    """Reservväg: läs produktkort ur sidtexten."""
    out = []
    blocks = re.split(r"\n(?=Säljstart [a-zåäö]+ \d+ \w+ kl )", text)
    for b in blocks:
        m = re.search(r"Nr (\d+)", b)
        if not m:
            continue
        lines = [l.strip() for l in b.split("\n") if l.strip()]
        d = {"artikelnr": m.group(1), "saljstart": lines[0]}
        for l in lines:
            if re.match(r"^(RÖTT|VITT|ROSÉ|MOUSSERANDE|STARKVIN|APERITIF|SMAKRIKT|ÖVRIGT)", l):
                d["stil"] = l
            mm = re.match(r"^(.+), (\d{4}|[A-Za-z]{0,3}), (Glasflaska|Plastflaska|Box|Burk|Flaska)", l)
        vol = re.search(r"([\d ]+) ml · ([\d,]+) % vol", b)
        if vol:
            d["volym_ml"] = vol.group(1).replace(" ", "")
            d["alkohol"] = vol.group(2)
        pris = re.search(r"(\d[\d ]*):-", b)
        if pris:
            d["pris_kr"] = pris.group(1).replace(" ", "")
        namn = [l for l in lines if "Glasflaska" in l or "ml" in l and "," in l and "vol" not in l]
        if namn:
            d["rubrik"] = namn[0]
        d["eko"] = "EKOLOGISKT" in b
        d["text"] = " | ".join(lines[1:12])
        out.append(d)
    return out


def expected_count(text):
    m = re.search(r"Hittade (\d+) varor", text)
    return int(m.group(1)) if m else None


result = {"fran": fran, "till": till, "hamtad": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "kategorier": {}}
with sync_playwright() as p:
    b = p.chromium.launch()
    for kat in kategorier:
        ctx = b.new_context(locale="sv-SE", viewport={"width": 1400, "height": 2200})
        pg = ctx.new_page()
        api = {}

        def on_resp(r):
            if "productsearch" in r.url and r.request.method in ("GET", "POST"):
                try:
                    j = r.json()
                    for pr in j.get("products", []) or []:
                        api[str(pr.get("productNumber") or pr.get("productId"))] = pr
                except Exception:
                    pass
        pg.on("response", on_resp)
        info = {"url": url_for(kat)}
        try:
            resp = pg.goto(url_for(kat), wait_until="domcontentloaded", timeout=60000)
            info["status"] = resp.status if resp else None
            pg.wait_for_timeout(8000)
            try:
                pg.get_by_role("button", name=re.compile("Godkänn|Acceptera", re.I)).first.click(timeout=3000)
            except Exception:
                pass
            total = expected_count(pg.inner_text("body"))
            info["forvantat"] = total
            for _ in range(40):
                antal = len(re.findall(r"\bNr \d+", pg.inner_text("body")))
                if total is None or antal >= total:
                    break
                knapp = pg.get_by_role("button", name=re.compile("Visa fler|Ladda fler|Visa mer", re.I)).first
                try:
                    knapp.scroll_into_view_if_needed(timeout=3000)
                    knapp.click(timeout=3000)
                except Exception:
                    pg.mouse.wheel(0, 6000)
                pg.wait_for_timeout(2500)
            text = pg.inner_text("body")
            if os.environ.get("DEBUG_SB"):
                print("KNAPPAR:", [x for x in pg.eval_on_selector_all("button, a[role=button]", "els => els.map(e => (e.innerText||e.getAttribute('aria-label')||'').trim())") if x][-25:])
                print("SLUT:", text[-700:].replace("\n", " | "))
            info["produkter_dom"] = parse_cards(text)
            info["produkter_api"] = list(api.values())
            info["antal_dom"] = len(info["produkter_dom"])
            info["antal_api"] = len(api)
        except Exception as e:
            info["fel"] = repr(e)[:300]
        result["kategorier"][kat] = info
        print(kat, info.get("status"), "förväntat", info.get("forvantat"), "dom", info.get("antal_dom"), "api", info.get("antal_api"), info.get("fel", ""))
        ctx.close()
    b.close()

fn = os.path.join(ut, f"släpp_{fran}_{till}.json".replace("ä", "a"))
json.dump(result, open(fn, "w"), ensure_ascii=False, indent=1)
print("sparat", fn)
