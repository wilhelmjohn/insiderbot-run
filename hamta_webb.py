"""Hämtar webbsidor och PDF:er som sessionen inte når (öppet internet i Actions).
Indata: hamta_urls.txt (en https-URL per rad, rader med # ignoreras).
Utdata i datarepot: hamtat/<ticker-eller-namn>_<hash>.txt (ren text) och hamtat/index.csv."""
import csv, hashlib, io, os, re, sys, time, datetime as dt
from html.parser import HTMLParser
import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
MAX_BYTES = 15_000_000
MAX_TEXT = 400_000
UT = "hamtat"


class T(HTMLParser):
    def __init__(s):
        super().__init__(); s.o = []; s.skip = 0
    def handle_starttag(s, t, a):
        if t in ("script", "style", "noscript", "svg"): s.skip += 1
        if t in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "table"): s.o.append("\n")
    def handle_endtag(s, t):
        if t in ("script", "style", "noscript", "svg") and s.skip: s.skip -= 1
    def handle_data(s, d):
        if not s.skip: s.o.append(d)


def html_text(b, enc):
    t = T()
    t.feed(b.decode(enc or "utf-8", errors="replace"))
    x = re.sub(r"[ \t\r\f\v]+", " ", "".join(t.o))
    return re.sub(r"\n\s*\n+", "\n\n", x).strip()


def pdf_text(b):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    r = PdfReader(io.BytesIO(b))
    return "\n\n".join((p.extract_text() or "") for p in r.pages).strip()


def main():
    urls = [l.strip() for l in open("hamta_urls.txt", encoding="utf-8") if l.strip() and not l.startswith("#")]
    os.makedirs(UT, exist_ok=True)
    idx_fil = os.path.join(UT, "index.csv")
    rader = list(csv.reader(open(idx_fil, encoding="utf-8"))) if os.path.exists(idx_fil) else [["Datum", "Url", "Fil", "Status", "Tecken"]]
    nu = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    for u in urls:
        if not re.match(r"^https://", u):
            rader.append([nu, u, "", "hoppad: bara https", 0]); continue
        fil = ""; status = ""; n = 0
        try:
            r = requests.get(u, headers={"User-Agent": UA, "Accept-Language": "sv,en;q=0.8"}, timeout=40, stream=True)
            b = r.raw.read(MAX_BYTES + 1, decode_content=True)
            if len(b) > MAX_BYTES:
                status = "för stor"
            elif r.status_code != 200:
                status = f"HTTP {r.status_code}"
            else:
                ct = r.headers.get("content-type", "").lower()
                if "pdf" in ct or b[:4] == b"%PDF":
                    x = pdf_text(b)
                    if x is None:
                        status = "pypdf saknas"
                else:
                    x = html_text(b, r.encoding)
                if not status:
                    x = (x or "")[:MAX_TEXT]; n = len(x)
                    h = hashlib.sha1(u.encode()).hexdigest()[:8]
                    dom = re.sub(r"[^a-z0-9]+", "-", u.split("/")[2].lower())
                    fil = f"{dom}_{h}.txt"
                    open(os.path.join(UT, fil), "w", encoding="utf-8").write(f"URL: {u}\nHÄMTAD: {nu} UTC\n\n{x}\n")
                    status = "ok" if n > 200 else "nästan tom (dynamisk sida?)"
        except Exception as e:
            status = f"fel: {type(e).__name__}"
        print(status, n, u)
        rader.append([nu, u, fil, status, n]); time.sleep(1)
    with open(idx_fil, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rader)


main()
