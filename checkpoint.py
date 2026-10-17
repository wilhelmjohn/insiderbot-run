"""Mellanlagring i git under långa körningar (GitHub Actions).

Tar en körning slut på tid (steggräns 240 min) eller dör, går inget förlorat: det som
hunnit hämtas har redan checkats in. Gör ingenting utanför GitHub Actions och kraschar
aldrig anroparen (misslyckas incheckningen skrivs bara en varning).
"""
import os
import subprocess
import time
from pathlib import Path


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=300)


def spara_i_git(filer, meddelande: str, forsok: int = 3) -> bool:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return False
    try:
        _git("config", "user.name", "insiderbot")
        _git("config", "user.email", "insiderbot@users.noreply.github.com")
        finns = [str(f) for f in filer if Path(f).exists()]
        if not finns:
            return False
        _git("add", *finns)
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return True                      # inget nytt att spara
        c = _git("commit", "-m", meddelande)
        if c.returncode != 0:
            print(f"  Varning: commit misslyckades: {c.stderr.strip()[:200]}")
            return False
        for i in range(forsok):
            p = _git("pull", "--rebase", "--autostash", "origin", "main")
            if p.returncode == 0 and _git("push", "origin", "HEAD:main").returncode == 0:
                print(f"  Mellanlagrat i repot: {meddelande}")
                return True
            time.sleep(5 + 7 * i)
        print("  Varning: kunde inte pusha mellanlagringen, försöker igen vid nästa.")
    except Exception as e:                    # får aldrig stoppa hämtningen
        print(f"  Varning: mellanlagring misslyckades ({e.__class__.__name__}: {str(e)[:120]})")
    return False
