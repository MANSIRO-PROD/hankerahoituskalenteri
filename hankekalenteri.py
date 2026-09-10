#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hankekalenteri.py — Hankerahoituskalenteri yhtenä ohjelmana
============================================================

Avaa kalenterin selaimeen ja hakee tuoreimmat rahoitushaut taustalla.
Kalenteri aukeaa heti nykyisellä datalla; kun haku valmistuu, sivu
päivittää itsensä ilman että sitä tarvitsee ladata uudelleen.

    python3 hankekalenteri.py

Mitä ohjelma tekee käynnistyessään:

  1. Käynnistää paikallisen palvelimen (oletus portti 8777).
  2. Avaa kalenterin selaimeen välittömästi.
  3. Hakee taustalla kaikki sources.json:in lähteet, jos data on
     vanhentunut (oletus: yli 3 tuntia vanhaa).
  4. Lisää uudet haut funding_data.json:iin ja lähettää ilmoituksen,
     jos .env-tiedostossa on ilmoituskanava.
  5. Toistaa haun automaattisesti niin kauan kuin ohjelma on auki.

Sivulla on myös päivityspainike, joka hakee tiedot heti riippumatta
siitä milloin edellinen haku tehtiin.

Sulje ohjelma sulkemalla ikkuna tai painamalla Ctrl+C.

RIIPPUVUUDET: ei mitään — Pythonin vakiokirjasto riittää.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import funding_watcher as fw

VERSIO = "1.0.0"
OLETUSPORTTI = 8777
PORTTIHAARUKKA = 24          # montako perättäistä porttia kokeillaan

log = logging.getLogger("kalenteri")


# ══════════════════════════════════════════════════════════════════════
# Haun tila — jaettu palvelimen ja taustasäikeen kesken
# ══════════════════════════════════════════════════════════════════════

class HaunTila:
    """Yhden hakukierroksen tila, luettavissa /api/status-rajapinnasta."""

    def __init__(self) -> None:
        self._lukko = threading.Lock()
        self.tila = "odottaa"          # odottaa | haetaan | valmis | virhe
        self.valmis = 0
        self.yhteensa = 0
        self.nykyinen = ""
        self.uusia = 0
        self.paivitettyja = 0
        self.lahteet: list[dict] = []
        self.viesti = "Ei vielä haettu."
        self.alkoi: float | None = None
        self.kesto: float | None = None
        self.viimeksi: str | None = None
        self.kanavat: list[str] = []

    def aloita(self, yhteensa: int) -> None:
        with self._lukko:
            self.tila = "haetaan"
            self.valmis = 0
            self.yhteensa = yhteensa
            self.nykyinen = ""
            self.uusia = 0
            self.paivitettyja = 0
            self.lahteet = []
            self.viesti = "Haetaan rahoituslähteitä…"
            self.alkoi = time.time()
            self.kesto = None

    def edisty(self, valmis: int, yhteensa: int, sid: str, tila: str, maara: int) -> None:
        with self._lukko:
            self.valmis = valmis
            self.yhteensa = yhteensa
            self.nykyinen = sid
            self.lahteet.append({"id": sid, "tila": tila, "maara": maara})

    def lopeta(self, uusia: int, paivitettyja: int, viesti: str,
               kanavat: list[str] | None = None, virhe: bool = False) -> None:
        with self._lukko:
            self.tila = "virhe" if virhe else "valmis"
            self.uusia = uusia
            self.paivitettyja = paivitettyja
            self.viesti = viesti
            self.kanavat = kanavat or []
            self.kesto = round(time.time() - self.alkoi, 1) if self.alkoi else None
            self.viimeksi = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    def snapshot(self) -> dict:
        with self._lukko:
            epaonnistuneet = sum(1 for s in self.lahteet if s["tila"] == "virhe")
            return {
                "tila": self.tila,
                "valmis": self.valmis,
                "yhteensa": self.yhteensa,
                "nykyinen": self.nykyinen,
                "uusia": self.uusia,
                "paivitettyja": self.paivitettyja,
                "epaonnistuneet": epaonnistuneet,
                "onnistuneet": len(self.lahteet) - epaonnistuneet,
                "lahteet": list(self.lahteet),
                "viesti": self.viesti,
                "kesto": self.kesto,
                "viimeksi": self.viimeksi,
                "kanavat": self.kanavat,
                "versio": VERSIO,
            }


TILA = HaunTila()
_haku_lukko = threading.Lock()


# ══════════════════════════════════════════════════════════════════════
# Taustahaku
# ══════════════════════════════════════════════════════════════════════

def datan_ika_tunteina(data_polku: Path) -> float:
    """Kuinka vanhaa funding_data.json on tunteina. Puuttuva tiedosto = ikuisuus."""
    data = fw.load_json_file(data_polku, {})
    leima = data.get("paivitetty")
    if not leima:
        return float("inf")
    try:
        aika = dt.datetime.fromisoformat(str(leima))
    except ValueError:
        return float("inf")
    if aika.tzinfo is None:
        aika = aika.astimezone()
    return (dt.datetime.now().astimezone() - aika).total_seconds() / 3600


def hae_taustalla(asetukset: argparse.Namespace, ilmoita: bool = True) -> None:
    """Yksi hakukierros. Ei koskaan nosta poikkeusta ulos säikeestä."""
    if not _haku_lukko.acquire(blocking=False):
        log.debug("Haku on jo käynnissä — ohitetaan.")
        return

    try:
        today = dt.date.today()
        data_polku = Path(asetukset.data)
        config = fw.load_json_file(Path(asetukset.config), {"sources": []})
        kaytossa = [s for s in config.get("sources", []) if s.get("enabled", True)]

        if not kaytossa:
            TILA.aloita(0)
            TILA.lopeta(0, 0, "Yhtään lähdettä ei ole käytössä (sources.json).", virhe=True)
            return

        TILA.aloita(len(kaytossa))
        print(f"\n  Haetaan {len(kaytossa)} rahoituslähdettä…")

        def edisty(valmis, yhteensa, sid, tila, maara):
            TILA.edisty(valmis, yhteensa, sid, tila, maara)
            merkki = "OK " if tila == "ok" else "  -"
            lisa = f"{maara} hakua" if tila == "ok" else "ei vastausta"
            print(f"    [{valmis}/{yhteensa}] {merkki} {sid:<30} {lisa}")

        ehdokkaat = fw.collect_from_sources(config, today, progress=edisty)

        data = fw.load_json_file(data_polku, {"versio": 1, "hankkeet": []})
        olemassa = data.get("hankkeet", [])
        uudet, paivitetyt = fw.merge_candidates(olemassa, ehdokkaat)

        onnistui = sum(1 for s in TILA.snapshot()["lahteet"] if s["tila"] == "ok")

        if uudet or paivitetyt:
            data["hankkeet"] = olemassa + uudet
            data["paivitetty"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            data.setdefault("versio", 1)
            fw.save_json_atomic(data_polku, data)
            fw.embed_into_html(Path(asetukset.html), data_polku)
        elif onnistui:
            # Ei uutta, mutta merkitään että tarkistus tehtiin — muuten
            # ohjelma yrittäisi hakea uudelleen joka avauskerralla.
            data["paivitetty"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            fw.save_json_atomic(data_polku, data, backup=False)

        kanavat: list[str] = []
        if uudet and ilmoita and not asetukset.no_notify:
            tilamuistio = fw.load_json_file(Path(asetukset.state), {"notified_ids": []})
            jo_ilmoitetut = set(tilamuistio.get("notified_ids", []))
            ilmoitettavat = [u for u in uudet if u["id"] not in jo_ilmoitetut]
            if ilmoitettavat:
                fw.send_notifications(ilmoitettavat, today)
                jo_ilmoitetut.update(u["id"] for u in ilmoitettavat)
                tilamuistio["notified_ids"] = sorted(jo_ilmoitetut)[-2000:]
                tilamuistio["last_run"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
                fw.save_json_atomic(Path(asetukset.state), tilamuistio, backup=False)
                if fw.env("DISCORD_WEBHOOK_URL"):
                    kanavat.append("Discord")
                if fw.env("TELEGRAM_BOT_TOKEN"):
                    kanavat.append("Telegram")
                if fw.env("SLACK_WEBHOOK_URL"):
                    kanavat.append("Slack")
                if fw.env("SMTP_HOST"):
                    kanavat.append("Sähköposti")

        if not onnistui:
            viesti = ("Yksikään lähde ei vastannut. Tarkista verkkoyhteys, "
                      "tai aja: funding_watcher.py --test-sources -v")
            TILA.lopeta(0, 0, viesti, virhe=True)
            print(f"\n  {viesti}\n")
            return

        if uudet:
            viesti = f"{len(uudet)} uutta rahoitushakua löydetty."
        elif paivitetyt:
            viesti = f"Ei uusia hakuja. {paivitetyt} hakuajan päivitystä."
        else:
            viesti = "Ei uusia hakuja."

        TILA.lopeta(len(uudet), paivitetyt, viesti, kanavat)
        print(f"\n  {viesti}")
        for u in uudet:
            print(f"    + {fw.truncate(u['nimi'], 62)}")
            print(f"      {u['rahoittaja']} · aukeaa {fw.format_fi_date(u['haku_alkaa'])}")
        if kanavat:
            print(f"    Ilmoitus lähetetty: {', '.join(kanavat)}")
        print()

    except Exception as exc:  # noqa: BLE001 — säie ei saa kaatua koskaan
        log.exception("Haku epäonnistui")
        TILA.lopeta(0, 0, f"Haku epäonnistui: {exc}", virhe=True)
    finally:
        _haku_lukko.release()


def kaynnista_haku(asetukset: argparse.Namespace) -> bool:
    """Käynnistää haun omaan säikeeseensä. False jos haku oli jo käynnissä."""
    if _haku_lukko.locked():
        return False
    threading.Thread(
        target=hae_taustalla, args=(asetukset,), daemon=True, name="haku"
    ).start()
    return True


def ajastin(asetukset: argparse.Namespace, stop: threading.Event) -> None:
    """Toistaa haun niin kauan kuin ohjelma on auki."""
    valissa = max(0.25, asetukset.interval_hours) * 3600
    while not stop.wait(valissa):
        log.info("Ajastettu tarkistus.")
        kaynnista_haku(asetukset)


# ══════════════════════════════════════════════════════════════════════
# HTTP-palvelin
# ══════════════════════════════════════════════════════════════════════

def tee_kasittelija(kansio: Path, asetukset: argparse.Namespace):
    class Kasittelija(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(kansio), **kw)

        # Vaimennetaan oletusloki; palvelin puhuu vain kun on asiaa.
        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _json(self, data: dict, status: int = 200) -> None:
            runko = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(runko)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(runko)

        def end_headers(self) -> None:
            # funding_data.json ei saa jäädä selaimen välimuistiin, muuten
            # sivu näyttäisi vanhaa dataa haun jälkeen.
            if self.path.endswith((".json", ".html")) or self.path in ("/", ""):
                self.send_header("Cache-Control", "no-store, must-revalidate")
            super().end_headers()

        def do_GET(self) -> None:
            if self.path.split("?")[0] == "/api/status":
                self._json(TILA.snapshot())
                return
            super().do_GET()

        def do_POST(self) -> None:
            polku = self.path.split("?")[0]
            if polku == "/api/refresh":
                aloitettu = kaynnista_haku(asetukset)
                self._json({
                    "aloitettu": aloitettu,
                    "viesti": "Haku käynnistetty." if aloitettu else "Haku on jo käynnissä.",
                })
                return
            if polku == "/api/shutdown":
                self._json({"viesti": "Suljetaan."})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send_error(404, "Tuntematon rajapinta")

    return Kasittelija


def vapaa_portti(toive: int) -> int | None:
    for portti in range(toive, toive + PORTTIHAARUKKA):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", portti))
                return portti
            except OSError:
                continue
    return None


def portti_kaytossa_meidan(portti: int) -> bool:
    """Onko portissa jo tämä sama ohjelma? Silloin avataan vain selain."""
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"http://127.0.0.1:{portti}/api/status", timeout=1.5
        ) as vastaus:
            json.loads(vastaus.read().decode("utf-8"))
            return True
    except Exception:  # noqa: BLE001
        return False


# ══════════════════════════════════════════════════════════════════════
# Käynnistys
# ══════════════════════════════════════════════════════════════════════

def banneri(url: str, kansio: Path) -> None:
    print()
    print("  ┌────────────────────────────────────────────────────────┐")
    print("  │  HANKERAHOITUSKALENTERI                                │")
    print("  └────────────────────────────────────────────────────────┘")
    print()
    print(f"  Kalenteri:  {url}")
    print(f"  Kansio:     {kansio}")
    print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="hankekalenteri.py",
        description="Avaa hankerahoituskalenterin ja hakee tuoreimmat tiedot.",
    )
    p.add_argument("--port", type=int, default=OLETUSPORTTI, help="palvelimen portti")
    p.add_argument("--no-browser", action="store_true", help="älä avaa selainta")
    p.add_argument("--no-refresh", action="store_true", help="älä hae tietoja käynnistyessä")
    p.add_argument("--force", action="store_true", help="hae aina, vaikka data olisi tuoretta")
    p.add_argument("--no-notify", action="store_true", help="älä lähetä ilmoituksia")
    p.add_argument("--max-age-hours", type=float, default=3.0,
                   help="hae vain jos data on tätä vanhempaa (oletus 3 h)")
    p.add_argument("--interval-hours", type=float, default=6.0,
                   help="automaattisen tarkistuksen väli ohjelman ollessa auki (oletus 6 h)")
    p.add_argument("--data", default=str(fw.BASE_DIR / "funding_data.json"))
    p.add_argument("--config", default=str(fw.BASE_DIR / "sources.json"))
    p.add_argument("--state", default=str(fw.BASE_DIR / ".watcher_state.json"))
    p.add_argument("--html", default=str(fw.BASE_DIR / "index.html"))
    p.add_argument("--env-file", default=str(fw.BASE_DIR / ".env"))
    p.add_argument("-v", "--verbose", action="store_true")
    asetukset = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if asetukset.verbose else logging.WARNING,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # Watcherin oma loki on tarkoitettu komentoriviajoon. Täällä edistyminen
    # kerrotaan selkokielisinä riveinä, joten vaimennetaan päällekkäisyys.
    logging.getLogger("watcher").setLevel(
        logging.DEBUG if asetukset.verbose else logging.ERROR
    )

    fw.load_dotenv(Path(asetukset.env_file))

    kansio = fw.BASE_DIR
    if not (kansio / "index.html").exists():
        print(f"\n  index.html puuttuu kansiosta {kansio}\n"
              f"  Varmista, että kaikki tiedostot ovat samassa kansiossa.\n")
        input("  Paina Enter sulkeaksesi… ")
        return 1

    # Jo käynnissä? Avataan vain selain uudelleen.
    if portti_kaytossa_meidan(asetukset.port):
        url = f"http://localhost:{asetukset.port}/index.html"
        print(f"\n  Kalenteri on jo käynnissä. Avataan selain: {url}\n")
        if not asetukset.no_browser:
            webbrowser.open(url)
        return 0

    portti = vapaa_portti(asetukset.port)
    if portti is None:
        print(f"\n  Vapaata porttia ei löytynyt väliltä "
              f"{asetukset.port}–{asetukset.port + PORTTIHAARUKKA}.\n")
        input("  Paina Enter sulkeaksesi… ")
        return 1

    url = f"http://localhost:{portti}/index.html"
    palvelin = http.server.ThreadingHTTPServer(
        ("127.0.0.1", portti), tee_kasittelija(kansio, asetukset)
    )
    palvelin.daemon_threads = True

    banneri(url, kansio)

    if not asetukset.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    if not any(fw.env(k) for k in
               ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN",
                "SLACK_WEBHOOK_URL", "SMTP_HOST")):
        print("  Ilmoituskanavaa ei ole asetettu: uudet haut näkyvät")
        print("  kalenterissa, mutta erillistä ilmoitusta ei lähetetä.")
        print("  Lisää DISCORD_WEBHOOK_URL .env-tiedostoon ottaaksesi käyttöön.")
        print()

    ika = datan_ika_tunteina(Path(asetukset.data))
    if asetukset.no_refresh:
        print("  Tietojen haku ohitettu (--no-refresh).")
    elif asetukset.force or ika > asetukset.max_age_hours:
        if ika == float("inf"):
            print("  Dataa ei ole vielä haettu — haetaan nyt.")
        else:
            print(f"  Data on {ika:.1f} h vanhaa — haetaan tuoreet tiedot.")
        kaynnista_haku(asetukset)
    else:
        print(f"  Data on tuoretta ({ika:.1f} h) — ei haeta uudelleen.")
        print("  Voit hakea heti kalenterin päivityspainikkeesta.")
        TILA.lopeta(0, 0, f"Data tarkistettu {ika:.1f} tuntia sitten.")

    stop = threading.Event()
    threading.Thread(target=ajastin, args=(asetukset, stop), daemon=True,
                     name="ajastin").start()

    print("  Sulje ohjelma sulkemalla tämä ikkuna tai painamalla Ctrl+C.")
    print()

    try:
        palvelin.serve_forever()
    except KeyboardInterrupt:
        print("\n  Suljetaan…")
    finally:
        stop.set()
        palvelin.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
