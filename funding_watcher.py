#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
funding_watcher.py — automaattinen hankerahoitushakujen seuranta
=================================================================

Seuraa määriteltyjä rahoituslähteitä (RSS/Atom-syötteet, JSON-rajapinnat,
EU:n Funding & Tenders -portaali, HTML-sivut), vertaa löydöksiä paikalliseen
funding_data.json-tiedostoon ja:

  1. lisää uudet rahoitushaut funding_data.json-tiedostoon,
  2. lähettää ilmoituksen otsikolla
     "Uusi rahoitushaku löydetty: [Hankkeen nimi]"
     (Discord / Telegram / Slack / sähköposti).

RIIPPUVUUDET: ei mitään pakollisia — pelkkä Python 3.9+ vakiokirjasto.
              (BeautifulSoup4 vain jos käytät "html"-tyyppisiä lähteitä.)

KÄYTTÖ:
    python3 funding_watcher.py                 # normaali ajo
    python3 funding_watcher.py --dry-run       # ei kirjoita eikä ilmoita
    python3 funding_watcher.py --test-sources  # testaa mitkä lähteet vastaavat
    python3 funding_watcher.py --test-notify   # lähettää testi-ilmoituksen
    python3 funding_watcher.py --list          # tulostaa nykyisen datan tilan
    python3 funding_watcher.py -v              # verbose-loki

Katso README.md ajastusohjeet (crontab / GitHub Actions).
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import gzip
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Vakioasetukset
# --------------------------------------------------------------------------

APP_NAME = "hankerahoituskalenteri"
VERSION = "1.2.0"
USER_AGENT = (
    f"{APP_NAME}/{VERSION} (funding-watcher; +https://example.org/funding-watcher)"
)
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
HTTP_BACKOFF = 2.0

def _base_dir() -> Path:
    """Kansio, josta sources.json, funding_data.json ja .env luetaan.

    PyInstallerilla paketoituna (--onefile) skripti puretaan väliaikaiseen
    kansioon, joten __file__ osoittaa väärään paikkaan. Silloin käytetään
    exen omaa sijaintia, jolloin datatiedostot ovat exen vieressä.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
DEFAULT_DATA = BASE_DIR / "funding_data.json"
DEFAULT_CONFIG = BASE_DIR / "sources.json"
DEFAULT_STATE = BASE_DIR / ".watcher_state.json"
DEFAULT_ENV = BASE_DIR / ".env"

RAHOITTAJATYYPIT = ("Julkinen", "Säätiöt", "EU")

log = logging.getLogger("watcher")


# --------------------------------------------------------------------------
# Apurit: .env, merkistö, slugit
# --------------------------------------------------------------------------

def load_dotenv(path: Path = DEFAULT_ENV) -> None:
    """Lukee yksinkertaisen KEY=VALUE .env-tiedoston ympäristömuuttujiin.

    Olemassa olevia ympäristömuuttujia EI ylikirjoiteta, jotta CI:n
    salaisuudet voittavat aina paikallisen tiedoston.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    val = env(name).lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on", "kylla", "kyllä")


def slugify(text: str, maxlen: int = 60) -> str:
    """Muuntaa tekstin URL-ystävälliseksi tunnisteeksi (ä→a, ö→o)."""
    text = (text or "").lower()
    text = text.replace("ä", "a").replace("ö", "o").replace("å", "a")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:maxlen].strip("-") or "haku"


def normalize_title(text: str) -> str:
    """Normalisoi otsikon duplikaattivertailua varten."""
    text = (text or "").lower()
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^\wäöå ]+", " ", text)
    text = re.sub(r"\b(20\d\d|haku|hakuaika|rahoitushaku|avoinna|auki)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(url: str) -> str:
    """Poistaa seurantaparametrit ja normalisoi URLin vertailua varten."""
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query)
        if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_"))
    ]
    return urllib.parse.urlunsplit(
        (
            parts.scheme.lower() or "https",
            parts.netloc.lower().removeprefix("www."),
            parts.path.rstrip("/"),
            urllib.parse.urlencode(query),
            "",
        )
    )


def strip_html(text: str) -> str:
    """Riisuu HTML-tagit ja purkaa yleisimmät entiteetit."""
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    import html as _html

    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n", text)
    return text.strip()


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


# --------------------------------------------------------------------------
# HTTP-kerros
# --------------------------------------------------------------------------

class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = HTTP_TIMEOUT,
    retries: int = HTTP_RETRIES,
) -> tuple[int, dict[str, str], bytes]:
    """HTTP-kutsu uudelleenyrityksillä, gzip-purulla ja järkevillä otsikoilla."""
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept-Language": "fi,en;q=0.8",
    }
    hdrs.update(headers or {})

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                if resp_headers.get("content-encoding", "").lower() == "gzip":
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        pass
                return resp.status, resp_headers, body
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001
                pass
            # 4xx (paitsi 408/429) ei parane uusimalla
            if exc.code not in (408, 429) and 400 <= exc.code < 500:
                raise HttpError(
                    f"HTTP {exc.code} {exc.reason}: {truncate(body.decode('utf-8', 'replace'), 300)}",
                    exc.code,
                ) from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            last_error = exc

        if attempt < retries:
            delay = HTTP_BACKOFF ** attempt
            log.debug("  yritys %d/%d epäonnistui (%s), odotetaan %.1fs",
                      attempt, retries, last_error, delay)
            time.sleep(delay)

    raise HttpError(f"Yhteys epäonnistui {retries} yrityksen jälkeen: {last_error}")


def http_json(url: str, **kwargs: Any) -> Any:
    _status, _headers, body = http_request(url, **kwargs)
    return json.loads(body.decode("utf-8", "replace"))


def encode_multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    """Koodaa multipart/form-data -rungon ilman ulkoisia kirjastoja."""
    boundary = f"----watcher{uuid.uuid4().hex}"
    buf = io.BytesIO()
    for name, value in fields.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        buf.write(str(value).encode("utf-8"))
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


# --------------------------------------------------------------------------
# Päivämäärien tunnistus suomenkielisestä tekstistä
# --------------------------------------------------------------------------

FI_MONTHS = {
    "tammi": 1, "helmi": 2, "maalis": 3, "huhti": 4, "touko": 5, "kesä": 6,
    "kesa": 6, "heinä": 7, "heina": 7, "elo": 8, "syys": 9, "loka": 10,
    "marras": 11, "joulu": 12,
}
EN_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DASH = r"[–—\-−]"

# 1.10.2026 tai 01.10.2026
RE_FI_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
# 1.–31.10.2026  |  1.10.–31.10.2026  |  1.10.2026–31.10.2026
RE_FI_RANGE_SHORT = re.compile(
    rf"\b(\d{{1,2}})\.\s*{DASH}\s*(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}})\b"
)
RE_FI_RANGE_FULL = re.compile(
    rf"\b(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}})?\s*{DASH}\s*(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}})\b"
)
# 2026-10-01
RE_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# 1. lokakuuta 2026
RE_FI_MONTHNAME = re.compile(
    r"\b(\d{1,2})\.?\s+(tammi|helmi|maalis|huhti|touko|kes[äa]|hein[äa]|elo|syys|loka|marras|joulu)"
    r"kuuta\s+(\d{4})\b",
    re.IGNORECASE,
)
# 1 October 2026 / October 1, 2026
RE_EN_DATE = re.compile(
    r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b",
    re.IGNORECASE,
)

OPEN_HINTS = (
    "haku aukeaa", "hakuaika alkaa", "haku alkaa", "haku avautuu", "avautuu",
    "aukeaa", "hakuaika:", "hakuaika", "opening date", "opens", "call opens",
    "avoinna alkaen", "start date", "startdate", "starts", "alkaen", "from",
)
CLOSE_HINTS = (
    "haku päättyy", "hakuaika päättyy", "viimeinen hakupäivä", "määräaika",
    "deadline", "closes", "closing date", "viimeistään", "mennessä",
    "haettava viimeistään", "cut-off",
)


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[int, dt.date]]:
    """Palauttaa kaikki tekstistä löytyvät päivämäärät (sijainti, pvm)."""
    found: list[tuple[int, dt.date]] = []
    if not text:
        return found

    for m in RE_ISO_DATE.finditer(text):
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    for m in RE_FI_DATE.finditer(text):
        d = _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            found.append((m.start(), d))
    for m in RE_FI_MONTHNAME.finditer(text):
        key = m.group(2).lower().replace("ä", "ä")
        month = FI_MONTHS.get(key) or FI_MONTHS.get(key.replace("ä", "a"))
        if month:
            d = _safe_date(int(m.group(3)), month, int(m.group(1)))
            if d:
                found.append((m.start(), d))
    for m in RE_EN_DATE.finditer(text):
        month = EN_MONTHS.get(m.group(2).lower()[:3])
        if month:
            d = _safe_date(int(m.group(3)), month, int(m.group(1)))
            if d:
                found.append((m.start(), d))

    found.sort(key=lambda x: x[0])
    return found


def find_date_range(text: str) -> tuple[dt.date | None, dt.date | None]:
    """Etsii hakuajan alku- ja loppupäivän tekstistä.

    Strategia:
      1. eksplisiittinen väli (1.10.–31.10.2026)
      2. vihjesanan jälkeen tuleva lähin päivämäärä
      3. kaksi ensimmäistä päivämäärää järjestyksessä
    """
    if not text:
        return None, None

    # 1. Eksplisiittiset välit
    m = RE_FI_RANGE_FULL.search(text)
    if m:
        end_year = int(m.group(6))
        start_year = int(m.group(3)) if m.group(3) else end_year
        start = _safe_date(start_year, int(m.group(2)), int(m.group(1)))
        end = _safe_date(end_year, int(m.group(5)), int(m.group(4)))
        if start and end and start <= end:
            return start, end

    m = RE_FI_RANGE_SHORT.search(text)
    if m:
        year, month = int(m.group(4)), int(m.group(3))
        start = _safe_date(year, month, int(m.group(1)))
        end = _safe_date(year, month, int(m.group(2)))
        if start and end and start <= end:
            return start, end

    dates = find_dates(text)
    if not dates:
        return None, None

    lowered = text.lower()
    start = _nearest_after_hint(lowered, dates, OPEN_HINTS)
    end = _nearest_after_hint(lowered, dates, CLOSE_HINTS)

    if start and end and start > end:
        start, end = None, end
    if not start and not end:
        uniq = sorted({d for _, d in dates})
        if len(uniq) >= 2:
            return uniq[0], uniq[-1]
        return None, uniq[0]
    if not end and start:
        later = [d for _, d in dates if d > start]
        end = min(later) if later else None
    if not start and end:
        # Loppupäivä tunnistettiin vihjesanasta; alkupäiväksi aiempi päivämäärä
        earlier = [d for _, d in dates if d < end]
        start = min(earlier) if earlier else None
    return start, end


def _nearest_after_hint(
    lowered: str, dates: list[tuple[int, dt.date]], hints: Iterable[str]
) -> dt.date | None:
    """Etsii lähimmän päivämäärän, joka seuraa jotain vihjesanaa (≤120 merkkiä)."""
    best: tuple[int, dt.date] | None = None
    for hint in hints:
        idx = lowered.find(hint)
        while idx != -1:
            for pos, date in dates:
                distance = pos - idx
                if 0 <= distance <= 120 and (best is None or distance < best[0]):
                    best = (distance, date)
            idx = lowered.find(hint, idx + 1)
    return best[1] if best else None


# --------------------------------------------------------------------------
# "Mihin rahaa voi saada" -päättely
# --------------------------------------------------------------------------

PURPOSE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("palkka", "palkkaus", "henkilöstökulu", "salary", "personnel"),
     "Henkilöstö- ja palkkakulut"),
    (("apuraha", "työskentelyapuraha", "grant", "stipendi"),
     "Työskentelyapurahat"),
    (("investoin", "laite", "kone", "equipment", "infrastruktuuri"),
     "Laite- ja investointikulut"),
    (("matka", "liikkuvuus", "travel", "mobility", "konferens"),
     "Matka- ja liikkuvuuskulut"),
    (("tuotekehity", "tki", "t&k", "innovaatio", "prototyyp", "demonstraat", "pilot"),
     "Tuotekehitys, pilotit ja demonstraatiot"),
    (("kansainvälist", "vienti", "international", "export"),
     "Kansainvälistyminen ja markkinaselvitykset"),
    (("koulutus", "osaamis", "oppimi", "training", "education"),
     "Koulutus ja osaamisen kehittäminen"),
    (("työllisyy", "työllistä", "osallisuu", "employment", "inclusion"),
     "Työllisyys- ja osallisuustoimet"),
    (("ilmasto", "hiilineutraal", "vihreä siirtym", "energia", "climate", "energy"),
     "Ilmasto-, energia- ja vihreän siirtymän toimet"),
    (("digitaali", "tekoäly", "data", "digital", "tekoälyn"),
     "Digitalisaatio ja datan hyödyntäminen"),
    (("taide", "kulttuuri", "näyttely", "produktio", "culture", "art"),
     "Taiteellinen työ ja kulttuurituotannot"),
    (("julkais", "avoin tiede", "publication", "open access"),
     "Julkaisu- ja avoimen tieteen kulut"),
    (("viestin", "levittä", "dissemination", "communication"),
     "Hankeviestintä ja tulosten levittäminen"),
    (("ostopalvelu", "asiantuntijapalvelu", "alihankinta", "subcontract", "consult"),
     "Ostetut asiantuntija- ja alihankintapalvelut"),
]

TARGET_RULES: list[tuple[tuple[str, ...], str]] = [
    (("pk-yrity", "pk yrity", "sme", "mikroyrity", "startup", "yritykse", "yrityksil"),
     "Yritykset ja pk-yritykset"),
    (("yliopisto", "korkeakoulu", "ammattikorkea", "university", "tutkimuslaito"),
     "Korkeakoulut ja tutkimuslaitokset"),
    (("tutkija", "väitöskirja", "tohtori", "postdoc", "researcher"),
     "Tutkijat ja tutkimusryhmät"),
    (("kunta", "kaupunki", "hyvinvointialue", "municipalit", "viranomai"),
     "Kunnat, kaupungit ja hyvinvointialueet"),
    (("yhdisty", "järjestö", "säätiö", "ngo", "association", "kolmannen sektorin"),
     "Yhdistykset, järjestöt ja säätiöt"),
    (("oppilaito", "lukio", "opisto", "school", "vocational", "ammatillinen"),
     "Oppilaitokset"),
    (("taiteilij", "työryhm", "artist"),
     "Taiteilijat ja työryhmät"),
    (("konsortio", "kumppanuu", "consortium", "partner"),
     "Konsortiot ja kumppanuushankkeet"),
]


_KEYWORD_CACHE: dict[str, re.Pattern[str]] = {}


def has_keyword(blob: str, keyword: str) -> bool:
    """Sanan alkuun ankkuroitu osumatarkistus.

    Suomen taivutuksen vuoksi loppu jätetään vapaaksi ("palkka" osuu sanaan
    "palkkakuluihin"), mutta alku ankkuroidaan sananrajaan, jotta lyhyet
    avainsanat eivät osu sanan keskelle ("tki" ei osu sanaan "tutkimus"
    eikä "koulu" sanaan "korkeakoulu").
    """
    pattern = _KEYWORD_CACHE.get(keyword)
    if pattern is None:
        pattern = re.compile(r"\b" + re.escape(keyword), re.IGNORECASE)
        _KEYWORD_CACHE[keyword] = pattern
    return pattern.search(blob) is not None


def infer_list(text: str, rules: list[tuple[tuple[str, ...], str]], limit: int = 6) -> list[str]:
    lowered = (text or "").lower()
    out: list[str] = []
    for keywords, label in rules:
        if any(has_keyword(lowered, kw) for kw in keywords) and label not in out:
            out.append(label)
        if len(out) >= limit:
            break
    return out


# Järjestys ratkaisee: nimetyt ohjelmat ja organisaatiot ennen yleissanoja,
# jotta esim. "Suomen Kulttuurirahasto" ei mene EU:ksi eikä
# "Taiken apurahat" säätiöksi.
FUNDER_TYPE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("eakr", "esr", "jtf", "interreg", "horizon", "erasmus", "life-ohjelma",
      "euroopan", "european", "eu:n", "eic", "cordis", "rakennerahast", "leader",
      "maaseuturahasto", "creative europe", "cef", "digital europe"),
     "EU"),
    (("akatemia", "business finland", "ely-keskus", "ely keskus", "ministeriö",
      "taike", "opetushallitus", "valtio", "kela", "tem", "okm", "stea",
      "maakuntaliitto", "liitto", "kunta", "kaupunki"),
     "Julkinen"),
    (("säätiö", "saatio", "rahasto", "foundation", "kulttuurirahasto", "apuraha"),
     "Säätiöt"),
]


def infer_funder_type(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).lower()
    for keywords, label in FUNDER_TYPE_RULES:
        if any(has_keyword(blob, kw) for kw in keywords):
            return label
    return "Julkinen"


# --------------------------------------------------------------------------
# Lähdeadapterit
# --------------------------------------------------------------------------

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1]


def _text(parent: ET.Element, *names: str) -> str:
    for name in names:
        for child in parent:
            if _tag(child).lower() == name.lower():
                if child.text and child.text.strip():
                    return child.text.strip()
                # Atom-linkit: <link href="..."/>
                href = child.attrib.get("href")
                if href:
                    return href.strip()
    return ""


def fetch_rss(source: dict) -> list[dict]:
    """RSS 2.0 / Atom -syöte vakiokirjastolla."""
    _status, _headers, body = http_request(
        source["url"], headers=source.get("headers") or {}
    )
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise HttpError(f"Syötteen XML-jäsennys epäonnistui: {exc}") from exc

    entries: list[ET.Element] = []
    for path in ("./channel/item", ".//{http://www.w3.org/2005/Atom}entry", ".//item", ".//entry"):
        entries = root.findall(path)
        if entries:
            break

    raw_items: list[dict] = []
    for entry in entries[: source.get("max_items", 60)]:
        title = _text(entry, "title")
        link = _text(entry, "link", "id")
        summary = _text(entry, "description", "summary", "content", "encoded")
        published = _text(entry, "pubDate", "published", "updated", "date")
        raw_items.append(
            {
                "nimi": strip_html(title),
                "linkki": link,
                "kuvaus": strip_html(summary),
                "julkaistu": published,
            }
        )
    return raw_items


def dig(obj: Any, path: str) -> Any:
    """Hakee arvon pisteellä erotetulla polulla, esim. 'metadata.title.0'."""
    current = obj
    if not path:
        return current
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            if part.isdigit():
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                current = current[0] if current else None
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    if isinstance(current, list) and current and not isinstance(current[0], (dict, list)):
        return ", ".join(str(x) for x in current)
    return current


def fetch_json_api(source: dict) -> list[dict]:
    """Yleinen JSON-rajapinta-adapteri kenttäkartalla."""
    method = (source.get("method") or "GET").upper()
    headers = dict(source.get("headers") or {})
    data = None
    if source.get("body") is not None:
        headers.setdefault("Content-Type", "application/json")
        data = json.dumps(source["body"]).encode("utf-8")

    payload = http_json(source["url"], method=method, headers=headers, data=data)
    items = dig(payload, source.get("items_path", "")) or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise HttpError(f"items_path '{source.get('items_path')}' ei osoita listaan")

    field_map: dict[str, str] = source.get("field_map") or {}
    raw_items: list[dict] = []
    for item in items[: source.get("max_items", 100)]:
        record: dict[str, Any] = {}
        for target, path in field_map.items():
            value = dig(item, path)
            if value is not None:
                record[target] = value
        if not record.get("nimi"):
            continue
        record["nimi"] = strip_html(str(record["nimi"]))
        record["kuvaus"] = strip_html(str(record.get("kuvaus", "")))
        base = source.get("link_prefix", "")
        if base and record.get("linkki") and not str(record["linkki"]).startswith("http"):
            record["linkki"] = base.rstrip("/") + "/" + str(record["linkki"]).lstrip("/")
        raw_items.append(record)
    return raw_items


EU_STATUS = {"forthcoming": "31094501", "open": "31094502", "closed": "31094503"}


def fetch_eu_sedia(source: dict) -> list[dict]:
    """EU:n Funding & Tenders -portaalin hakurajapinta (SEDIA).

    HUOM: rajapinnan sopimus voi muuttua ilman ennakkoilmoitusta.
    Aja `--test-sources` varmistaaksesi, että päätepiste vastaa.
    """
    statuses = [EU_STATUS.get(s, s) for s in source.get("statuses", ["forthcoming", "open"])]
    query = source.get("query") or {
        "bool": {
            "must": [
                {"terms": {"type": source.get("types", ["1", "2", "8"])}},
                {"terms": {"status": statuses}},
            ]
        }
    }
    fields = {
        "query": json.dumps(query),
        "languages": json.dumps(source.get("languages", ["en"])),
        "sort": json.dumps(source.get("sort", {"field": "sortStatus", "order": "ASC"})),
    }
    body, content_type = encode_multipart(fields)

    params = {
        "apiKey": source.get("api_key", "SEDIA"),
        "text": source.get("text", "***"),
        "pageSize": str(source.get("page_size", 50)),
        "pageNumber": "1",
    }
    url = source["url"] + ("&" if "?" in source["url"] else "?") + urllib.parse.urlencode(params)

    payload = http_json(
        url, method="POST", headers={"Content-Type": content_type}, data=body
    )

    results = payload.get("results") or dig(payload, "response.results") or []
    raw_items: list[dict] = []
    for item in results[: source.get("max_items", 60)]:
        meta = item.get("metadata") or {}

        def first(key: str) -> str:
            value = meta.get(key)
            if isinstance(value, list):
                return str(value[0]) if value else ""
            return str(value or "")

        title = first("title") or item.get("title") or ""
        if not title:
            continue
        raw_items.append(
            {
                "nimi": strip_html(title),
                "linkki": item.get("url") or "",
                "kuvaus": strip_html(
                    item.get("summary") or first("description") or first("descriptionByte")
                ),
                "haku_alkaa": (first("startDate") or "")[:10],
                "haku_paattyy": (first("deadlineDate") or "")[:10],
                "ohjelma": first("frameworkProgramme") or first("programmeDivision"),
                "rahoittaja": source.get("rahoittaja", "Euroopan komissio"),
                "summa": first("budgetOverview"),
            }
        )
    return raw_items


def fetch_html(source: dict) -> list[dict]:
    """HTML-sivun raaputus CSS-valitsimilla (vaatii beautifulsoup4)."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise HttpError(
            "Lähdetyyppi 'html' vaatii beautifulsoup4:n. "
            "Asenna: pip install beautifulsoup4"
        ) from exc

    _status, _headers, body = http_request(source["url"], headers=source.get("headers") or {})
    soup = BeautifulSoup(body.decode("utf-8", "replace"), "html.parser")
    selectors = source.get("selectors") or {}
    rows = soup.select(selectors.get("item", "article"))

    raw_items: list[dict] = []
    for row in rows[: source.get("max_items", 60)]:
        def pick(key: str) -> str:
            sel = selectors.get(key)
            if not sel:
                return ""
            node = row.select_one(sel)
            return node.get_text(" ", strip=True) if node else ""

        title = pick("nimi")
        if not title:
            continue
        link = ""
        link_sel = selectors.get("linkki", "a")
        anchor = row.select_one(link_sel)
        if anchor is not None:
            link = anchor.get("href") or ""
            if link and not link.startswith("http"):
                link = urllib.parse.urljoin(source["url"], link)
        raw_items.append(
            {
                "nimi": title,
                "linkki": link,
                "kuvaus": pick("kuvaus") or row.get_text(" ", strip=True)[:600],
            }
        )
    return raw_items


ADAPTERS = {
    "rss": fetch_rss,
    "atom": fetch_rss,
    "json_api": fetch_json_api,
    "eu_sedia": fetch_eu_sedia,
    "html": fetch_html,
}


# --------------------------------------------------------------------------
# Normalisointi hankkeeksi
# --------------------------------------------------------------------------

def normalize_record(raw: dict, source: dict, today: dt.date) -> dict | None:
    """Muuntaa adapterin raakatuloksen funding_data.json-muotoon."""
    name = truncate(strip_html(str(raw.get("nimi", ""))), 200)
    if not name or len(name) < 6:
        return None

    link = str(raw.get("linkki") or source.get("fallback_link") or source.get("url") or "")
    description = truncate(strip_html(str(raw.get("kuvaus", ""))), 900)
    haystack = f"{name}\n{description}\n{raw.get('julkaistu', '')}"

    # Päivämäärät: adapterin antamat voittavat, muuten päätellään tekstistä
    start = _parse_iso(raw.get("haku_alkaa"))
    end = _parse_iso(raw.get("haku_paattyy"))
    if not (start and end):
        guess_start, guess_end = find_date_range(haystack)
        start = start or guess_start
        end = end or guess_end

    # Ei yhtään päivämäärää → ei voida sijoittaa kalenteriin
    if not start and not end:
        if source.get("require_dates", True):
            log.debug("    ohitetaan (ei päivämääriä): %s", truncate(name, 60))
            return None
        start = today
        end = today + dt.timedelta(days=int(source.get("default_window_days", 30)))
    if start and not end:
        end = start + dt.timedelta(days=int(source.get("default_window_days", 30)))
    if end and not start:
        start = min(today, end)
    if start and end and start > end:
        start, end = end, start

    # Menneet haut ohitetaan (konfiguroitava)
    max_age = int(source.get("skip_if_closed_days", 0))
    if end < today - dt.timedelta(days=max_age):
        log.debug("    ohitetaan (päättynyt %s): %s", end, truncate(name, 60))
        return None

    funder = str(raw.get("rahoittaja") or source.get("rahoittaja") or source.get("nimi", "Tuntematon"))
    funder_type = str(
        raw.get("rahoittajatyyppi")
        or source.get("rahoittajatyyppi")
        or infer_funder_type(funder, name, description, source.get("nimi", ""))
    )
    if funder_type not in RAHOITTAJATYYPIT:
        funder_type = infer_funder_type(funder_type, funder, name, description)

    purposes = infer_list(f"{name} {description}", PURPOSE_RULES) or [
        "Tarkista tarkka käyttötarkoitus rahoittajan hakusivulta"
    ]
    targets = infer_list(f"{name} {description}", TARGET_RULES) or [
        "Tarkista hakukelpoisuus rahoittajan hakusivulta"
    ]

    call_id = raw.get("id") or f"{slugify(funder, 24)}-{slugify(name, 40)}"
    if not raw.get("id"):
        digest = hashlib.sha1(
            (canonical_url(link) or name).encode("utf-8")
        ).hexdigest()[:6]
        call_id = f"{call_id}-{digest}"

    return {
        "id": call_id,
        "nimi": name,
        "rahoittaja": funder,
        "rahoittajatyyppi": funder_type,
        "ohjelma": str(raw.get("ohjelma") or source.get("ohjelma") or ""),
        "haku_alkaa": start.isoformat(),
        "haku_paattyy": end.isoformat(),
        "summa": truncate(str(raw.get("summa") or source.get("summa") or "Ei ilmoitettu"), 120),
        "alue": str(raw.get("alue") or source.get("alue") or ""),
        "kuvaus": description or "Ei kuvausta. Katso tarkemmat tiedot hakusivulta.",
        "kayttotarkoitus": purposes,
        "kohderyhmat": targets,
        "linkki": link,
        "lahde": source.get("id", "tuntematon"),
        "loydetty": today.isoformat(),
    }


def _parse_iso(value: Any) -> dt.date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        dates = find_dates(str(value))
        return dates[0][1] if dates else None


# --------------------------------------------------------------------------
# Duplikaattien tunnistus
# --------------------------------------------------------------------------

def is_duplicate(candidate: dict, existing: list[dict], threshold: float = 0.90) -> dict | None:
    """Palauttaa olemassa olevan hankkeen jos kyseessä on duplikaatti."""
    cand_id = candidate["id"]
    cand_url = canonical_url(candidate.get("linkki", ""))
    cand_title = normalize_title(candidate["nimi"])

    for item in existing:
        if item.get("id") == cand_id:
            return item
        if cand_url and canonical_url(item.get("linkki", "")) == cand_url:
            return item
        other_title = normalize_title(item.get("nimi", ""))
        if not other_title or not cand_title:
            continue
        same_funder = slugify(item.get("rahoittaja", "")) == slugify(candidate.get("rahoittaja", ""))
        ratio = difflib.SequenceMatcher(None, cand_title, other_title).ratio()
        if ratio >= threshold and (same_funder or ratio >= 0.97):
            # Sama nimi mutta selvästi eri hakukierros → ei duplikaatti
            if item.get("haku_alkaa") and candidate.get("haku_alkaa"):
                delta = abs(
                    (dt.date.fromisoformat(item["haku_alkaa"])
                     - dt.date.fromisoformat(candidate["haku_alkaa"])).days
                )
                if delta > 60:
                    continue
            return item
    return None


# --------------------------------------------------------------------------
# Ilmoitukset
# --------------------------------------------------------------------------

TYPE_COLORS = {"Julkinen": 0x1D4ED8, "Säätiöt": 0x9333EA, "EU": 0x0E7490}


def format_fi_date(iso: str) -> str:
    try:
        d = dt.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso or "?"
    return f"{d.day}.{d.month}.{d.year}"


def build_message_text(call: dict, today: dt.date) -> str:
    """Yhteinen tekstimuotoinen ilmoitusrunko (Telegram / sähköposti / Slack)."""
    start = dt.date.fromisoformat(call["haku_alkaa"])
    days = (start - today).days
    if days > 0:
        timing = f"Haku aukeaa {days} päivän kuluttua ({format_fi_date(call['haku_alkaa'])})"
    elif days == 0:
        timing = f"Haku aukeaa tänään ({format_fi_date(call['haku_alkaa'])})"
    else:
        timing = f"Haku on jo auki (alkoi {format_fi_date(call['haku_alkaa'])})"

    purposes = "\n".join(f"  • {p}" for p in call.get("kayttotarkoitus", [])[:6])
    targets = ", ".join(call.get("kohderyhmat", [])[:5])

    return (
        f"Uusi rahoitushaku löydetty: {call['nimi']}\n"
        f"\n"
        f"Rahoittaja: {call['rahoittaja']} ({call['rahoittajatyyppi']})\n"
        f"{timing}\n"
        f"Haku päättyy: {format_fi_date(call['haku_paattyy'])}\n"
        f"Rahoituksen määrä: {call.get('summa') or 'Ei ilmoitettu'}\n"
        f"\n"
        f"Mihin rahaa voi hakea:\n{purposes or '  • Katso hakusivu'}\n"
        f"\n"
        f"Kenelle: {targets or 'Katso hakusivu'}\n"
        f"\n"
        f"Hakusivu: {call.get('linkki') or '-'}"
    )


def notify_discord(calls: list[dict], today: dt.date, webhook: str) -> bool:
    """Lähettää Discord-webhookiin enintään 10 embediä per viesti."""
    ok = True
    for chunk_start in range(0, len(calls), 10):
        chunk = calls[chunk_start: chunk_start + 10]
        embeds = []
        for call in chunk:
            start = dt.date.fromisoformat(call["haku_alkaa"])
            days = (start - today).days
            if days > 0:
                timing = f"**{days} päivää** hakuun\n{format_fi_date(call['haku_alkaa'])}"
            elif days == 0:
                timing = f"**Aukeaa tänään**\n{format_fi_date(call['haku_alkaa'])}"
            else:
                timing = f"**Haku on auki**\nalkoi {format_fi_date(call['haku_alkaa'])}"

            purposes = "\n".join(f"• {p}" for p in call.get("kayttotarkoitus", [])[:6])
            embed = {
                "title": truncate(f"Uusi rahoitushaku löydetty: {call['nimi']}", 250),
                "description": truncate(call.get("kuvaus", ""), 500),
                "color": TYPE_COLORS.get(call["rahoittajatyyppi"], 0x334155),
                "fields": [
                    {"name": "Rahoittaja", "value": truncate(
                        f"{call['rahoittaja']}\n_{call['rahoittajatyyppi']}_", 1024), "inline": True},
                    {"name": "Haku alkaa", "value": truncate(timing, 1024), "inline": True},
                    {"name": "Haku päättyy",
                     "value": format_fi_date(call["haku_paattyy"]), "inline": True},
                    {"name": "Mihin rahaa voi hakea",
                     "value": truncate(purposes or "Katso hakusivu", 1024), "inline": False},
                    {"name": "Kenelle",
                     "value": truncate(", ".join(call.get("kohderyhmat", [])[:5]) or "-", 1024),
                     "inline": True},
                    {"name": "Rahoituksen määrä",
                     "value": truncate(str(call.get("summa") or "Ei ilmoitettu"), 1024),
                     "inline": True},
                ],
                "footer": {"text": f"Lähde: {call.get('lahde', '?')} · hankerahoituskalenteri"},
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            if call.get("linkki", "").startswith("http"):
                embed["url"] = call["linkki"]
            embeds.append(embed)

        payload = {
            "username": "Hankerahoituskalenteri",
            "content": (
                f"**{len(calls)} uutta rahoitushakua löydetty**"
                if chunk_start == 0 and len(calls) > 1
                else None
            ),
            "embeds": embeds,
            "allowed_mentions": {"parse": []},
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        for attempt in range(4):
            try:
                http_request(
                    webhook,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload).encode("utf-8"),
                    retries=1,
                )
                break
            except HttpError as exc:
                if exc.status == 429 and attempt < 3:
                    time.sleep(2 ** attempt + 1)
                    continue
                log.error("Discord-ilmoitus epäonnistui: %s", exc)
                ok = False
                break
        time.sleep(1.0)  # Discordin rate limit
    return ok


def notify_telegram(calls: list[dict], today: dt.date, token: str, chat_id: str) -> bool:
    ok = True
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for call in calls:
        text = build_message_text(call, today)
        try:
            http_request(
                url,
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps(
                    {"chat_id": chat_id, "text": truncate(text, 4000),
                     "disable_web_page_preview": False}
                ).encode("utf-8"),
                retries=2,
            )
        except HttpError as exc:
            log.error("Telegram-ilmoitus epäonnistui: %s", exc)
            ok = False
        time.sleep(0.4)
    return ok


def notify_slack(calls: list[dict], today: dt.date, webhook: str) -> bool:
    ok = True
    for call in calls:
        blocks = [
            {"type": "header", "text": {"type": "plain_text",
                                        "text": truncate(f"Uusi rahoitushaku: {call['nimi']}", 150)}},
            {"type": "section", "text": {"type": "mrkdwn",
                                         "text": truncate(build_message_text(call, today), 2900)}},
        ]
        try:
            http_request(
                webhook,
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"text": f"Uusi rahoitushaku löydetty: {call['nimi']}",
                                 "blocks": blocks}).encode("utf-8"),
                retries=2,
            )
        except HttpError as exc:
            log.error("Slack-ilmoitus epäonnistui: %s", exc)
            ok = False
        time.sleep(0.4)
    return ok


def notify_email(calls: list[dict], today: dt.date) -> bool:
    host = env("SMTP_HOST")
    port = int(env("SMTP_PORT", "587") or 587)
    user = env("SMTP_USER")
    password = env("SMTP_PASS")
    sender = env("SMTP_FROM") or user
    recipients = [r.strip() for r in env("SMTP_TO").split(",") if r.strip()]
    if not (host and sender and recipients):
        log.error("SMTP-asetukset puutteelliset (SMTP_HOST/SMTP_FROM/SMTP_TO)")
        return False

    subject_name = calls[0]["nimi"] if len(calls) == 1 else f"{len(calls)} uutta hakua"
    msg = EmailMessage()
    msg["Subject"] = f"Uusi rahoitushaku löydetty: {subject_name}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content("\n\n" + ("\n\n" + "-" * 60 + "\n\n").join(
        build_message_text(c, today) for c in calls
    ))

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                except smtplib.SMTPNotSupportedError:
                    log.warning("Palvelin ei tue STARTTLS:ää — lähetetään salaamattomana")
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.error("Sähköposti-ilmoitus epäonnistui: %s", exc)
        return False


def send_notifications(calls: list[dict], today: dt.date) -> None:
    """Lähettää ilmoitukset kaikkiin konfiguroituihin kanaviin."""
    if not calls:
        return
    channels_used = []

    webhook = env("DISCORD_WEBHOOK_URL")
    if webhook:
        channels_used.append("Discord" if notify_discord(calls, today, webhook) else "Discord(virhe)")

    tg_token, tg_chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        channels_used.append("Telegram" if notify_telegram(calls, today, tg_token, tg_chat)
                             else "Telegram(virhe)")

    slack = env("SLACK_WEBHOOK_URL")
    if slack:
        channels_used.append("Slack" if notify_slack(calls, today, slack) else "Slack(virhe)")

    if env("SMTP_HOST"):
        channels_used.append("Sähköposti" if notify_email(calls, today) else "Sähköposti(virhe)")

    if channels_used:
        log.info("Ilmoitukset lähetetty: %s", ", ".join(channels_used))
    else:
        log.warning(
            "Yhtään ilmoituskanavaa ei ole konfiguroitu — aseta esim. "
            "DISCORD_WEBHOOK_URL .env-tiedostoon tai ympäristömuuttujaksi."
        )


# --------------------------------------------------------------------------
# Tiedosto-I/O
# --------------------------------------------------------------------------

def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Tiedoston %s luku epäonnistui: %s", path.name, exc)
        return default


def save_json_atomic(path: Path, payload: Any, backup: bool = True) -> None:
    """Kirjoittaa tilapäistiedoston kautta, jotta data ei korruptoidu."""
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


EMBED_RE = re.compile(
    r'(<script type="application/json" id="varadata">)(.*?)(</script>)',
    re.DOTALL,
)


def embed_into_html(html_path: Path, data_path: Path) -> bool:
    """Päivittää index.html:n varadata-lohkon funding_data.json:in sisällöllä.

    Varadataa käytetään, kun sivu avataan suoraan levyltä (file://), jolloin
    selain estää fetch-kutsun sisartiedostoon.
    """
    if not html_path.exists():
        log.warning("HTML-tiedostoa ei löytynyt: %s", html_path)
        return False
    if not data_path.exists():
        log.warning("Datatiedostoa ei löytynyt: %s", data_path)
        return False

    html = html_path.read_text(encoding="utf-8")
    if not EMBED_RE.search(html):
        log.warning("Varadata-lohkoa ei löytynyt tiedostosta %s", html_path.name)
        return False

    payload = json.dumps(
        json.loads(data_path.read_text(encoding="utf-8")), ensure_ascii=False, indent=1
    )
    updated = EMBED_RE.sub(lambda m: m.group(1) + "\n" + payload + "\n" + m.group(3), html, count=1)
    if updated != html:
        html_path.write_text(updated, encoding="utf-8")
        log.info("Päivitettiin varadata tiedostoon %s", html_path.name)
    return True


def compute_status(call: dict, today: dt.date) -> str:
    try:
        start = dt.date.fromisoformat(call["haku_alkaa"])
        end = dt.date.fromisoformat(call["haku_paattyy"])
    except (KeyError, ValueError):
        return "Tuntematon"
    if today < start:
        return "Aukeamassa"
    if today <= end:
        return "Haku auki"
    return "Päättynyt"


# --------------------------------------------------------------------------
# Pääajo
# --------------------------------------------------------------------------

def collect_from_sources(
    config: dict,
    today: dt.date,
    only: str | None = None,
    progress=None,
) -> list[dict]:
    """Hakee ja normalisoi kaikki käytössä olevat lähteet.

    progress: valinnainen kutsuttava progress(valmis, yhteensa, id, tila, maara)
              jota kutsutaan jokaisen lähteen jälkeen. Tilat: "ok" | "virhe".
              Käytetään käyttöliittymän edistymispalkkiin.
    """
    found: list[dict] = []
    sources = [
        s for s in config.get("sources", [])
        if s.get("enabled", True) and (not only or s.get("id") == only)
    ]
    yhteensa = len(sources)
    valmis = 0

    def kerro(sid: str, tila: str, maara: int) -> None:
        if progress:
            try:
                progress(valmis, yhteensa, sid, tila, maara)
            except Exception:  # noqa: BLE001 — käyttöliittymä ei saa kaataa hakua
                pass

    for source in sources:
        sid = source.get("id", "?")
        adapter = ADAPTERS.get(source.get("tyyppi", "rss"))
        if adapter is None:
            log.warning("Tuntematon lähdetyyppi '%s' lähteessä %s", source.get("tyyppi"), sid)
            valmis += 1
            kerro(sid, "virhe", 0)
            continue

        log.info("→ Haetaan lähde: %s (%s)", source.get("nimi", sid), source.get("tyyppi"))
        try:
            raw_items = adapter(source)
        except HttpError as exc:
            log.warning("   Lähde %s epäonnistui: %s", sid, exc)
            valmis += 1
            kerro(sid, "virhe", 0)
            continue
        except Exception as exc:  # noqa: BLE001 — yksi lähde ei saa kaataa ajoa
            log.warning("   Lähde %s heitti odottamattoman virheen: %s", sid, exc)
            valmis += 1
            kerro(sid, "virhe", 0)
            continue

        log.info("   %d riviä syötteestä", len(raw_items))
        kept = 0
        for raw in raw_items:
            blob = f"{raw.get('nimi', '')} {raw.get('kuvaus', '')}".lower()
            if source.get("include_keywords") and not any(
                has_keyword(blob, k.lower()) for k in source["include_keywords"]
            ):
                continue
            if source.get("exclude_keywords") and any(
                has_keyword(blob, k.lower()) for k in source["exclude_keywords"]
            ):
                continue
            normalized = normalize_record(raw, source, today)
            if normalized:
                found.append(normalized)
                kept += 1
        log.info("   %d hakua läpäisi suodatuksen", kept)
        valmis += 1
        kerro(sid, "ok", kept)
    return found


def merge_candidates(
    existing: list[dict], candidates: list[dict]
) -> tuple[list[dict], int]:
    """Erottelee ehdokkaista aidosti uudet ja päivittää olemassa olevien päivämäärät.

    Palauttaa (uudet_haut, paivitettyjen_lukumaara). Muokkaa `existing`-listan
    alkioita paikallaan, kun päivämäärä on tarkentunut.
    """
    new_calls: list[dict] = []
    updated = 0

    for candidate in candidates:
        match = is_duplicate(candidate, existing + new_calls)
        if match is not None:
            changed = False
            for field in ("haku_alkaa", "haku_paattyy"):
                if (candidate.get(field)
                        and match.get(field) != candidate[field]
                        and match.get("lahde") != "seed"):
                    match[field] = candidate[field]
                    changed = True
            if changed:
                updated += 1
                log.debug("   Päivitettiin päivämäärät: %s", truncate(match["nimi"], 60))
            continue
        new_calls.append(candidate)
        log.info("   UUSI: %s (%s, aukeaa %s)",
                 truncate(candidate["nimi"], 70), candidate["rahoittaja"],
                 format_fi_date(candidate["haku_alkaa"]))

    return new_calls, updated


def run_watch(args: argparse.Namespace) -> int:
    today = dt.date.today()
    data_path = Path(args.data)
    config_path = Path(args.config)
    state_path = Path(args.state)

    config = load_json_file(config_path, {"sources": []})
    if not config.get("sources"):
        log.error("Lähdekonfiguraatiota ei löytynyt: %s", config_path)
        return 2

    data = load_json_file(data_path, {"versio": 1, "hankkeet": []})
    existing: list[dict] = data.get("hankkeet", [])
    state = load_json_file(state_path, {"notified_ids": [], "runs": 0})
    notified: set[str] = set(state.get("notified_ids", []))

    log.info("Nykyisessä datassa %d hanketta", len(existing))
    candidates = collect_from_sources(config, today, only=args.source)
    log.info("Lähteistä löytyi yhteensä %d ehdokasta", len(candidates))

    new_calls, updated = merge_candidates(existing, candidates)

    if args.dry_run:
        print(f"\n[DRY RUN] {len(new_calls)} uutta hakua, {updated} päivitystä. "
              f"Mitään ei kirjoitettu eikä ilmoitettu.\n")
        for call in new_calls:
            print(f"  • {call['nimi']}")
            print(f"    {call['rahoittaja']} · {call['rahoittajatyyppi']} · "
                  f"{format_fi_date(call['haku_alkaa'])}–{format_fi_date(call['haku_paattyy'])}")
            print(f"    {call['linkki']}")
        return 0

    if new_calls or updated:
        data["hankkeet"] = existing + new_calls
        data["paivitetty"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        data.setdefault("versio", 1)
        save_json_atomic(data_path, data)
        log.info("Kirjoitettiin %s (%d hanketta, %d uutta, %d päivitettyä)",
                 data_path.name, len(data["hankkeet"]), len(new_calls), updated)
        if args.embed:
            embed_into_html(Path(args.html), data_path)
    else:
        log.info("Ei uusia hakuja.")

    to_notify = [c for c in new_calls if c["id"] not in notified]
    if to_notify and not args.no_notify:
        send_notifications(to_notify, today)
        notified.update(c["id"] for c in to_notify)
    elif to_notify:
        log.info("--no-notify: ohitettiin %d ilmoitusta", len(to_notify))

    state["notified_ids"] = sorted(notified)[-2000:]
    state["runs"] = int(state.get("runs", 0)) + 1
    state["last_run"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    state["last_new_count"] = len(new_calls)
    save_json_atomic(state_path, state, backup=False)

    # GitHub Actions -yhteensopiva ulostulo
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"new_count={len(new_calls)}\n")
            fh.write(f"changed={'true' if (new_calls or updated) else 'false'}\n")

    return 0


def run_test_sources(args: argparse.Namespace) -> int:
    """Testaa jokaisen lähteen erikseen ja raportoi tuloksen."""
    config = load_json_file(Path(args.config), {"sources": []})
    sources = config.get("sources", [])
    if not sources:
        print("Ei lähteitä konfiguraatiossa.")
        return 2

    today = dt.date.today()
    print(f"\nTestataan {len(sources)} lähdettä\n" + "=" * 72)
    ok_count = 0
    for source in sources:
        sid = source.get("id", "?")
        label = f"{sid:<26} {source.get('tyyppi', '?'):<9}"
        if not source.get("enabled", True):
            print(f"  ⏸  {label} pois käytöstä")
            continue
        adapter = ADAPTERS.get(source.get("tyyppi", "rss"))
        if adapter is None:
            print(f"  ✗  {label} tuntematon tyyppi")
            continue
        started = time.time()
        try:
            raw_items = adapter(source)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗  {label} VIRHE: {truncate(str(exc), 90)}")
            continue
        elapsed = time.time() - started
        usable = sum(1 for r in raw_items if normalize_record(r, source, today))
        mark = "✓" if raw_items else "⚠"
        if raw_items:
            ok_count += 1
        print(f"  {mark}  {label} {len(raw_items):>3} riviä, "
              f"{usable:>3} käyttökelpoista ({elapsed:.1f}s)")
        if raw_items and args.verbose:
            for r in raw_items[:3]:
                print(f"        · {truncate(r.get('nimi', ''), 66)}")
    print("=" * 72)
    print(f"{ok_count}/{len([s for s in sources if s.get('enabled', True)])} "
          f"käytössä olevaa lähdettä vastasi.\n")
    print("Vinkki: korjaa toimimattomat URLit sources.json-tiedostoon tai "
          "aseta \"enabled\": false.\n")
    return 0


def run_test_notify(args: argparse.Namespace) -> int:
    today = dt.date.today()
    sample = {
        "id": "testi-ilmoitus",
        "nimi": "Testihaku – watcherin asennustesti",
        "rahoittaja": "Hankerahoituskalenteri",
        "rahoittajatyyppi": "Julkinen",
        "haku_alkaa": (today + dt.timedelta(days=14)).isoformat(),
        "haku_paattyy": (today + dt.timedelta(days=45)).isoformat(),
        "summa": "0 € (testi)",
        "kuvaus": "Tämä on testi-ilmoitus. Jos näet tämän, ilmoituskanava toimii.",
        "kayttotarkoitus": ["Asennuksen varmistaminen", "Webhookin testaus"],
        "kohderyhmat": ["Sinä"],
        "linkki": "https://example.org/",
        "lahde": "testi",
    }
    print("Lähetetään testi-ilmoitus konfiguroituihin kanaviin…")
    send_notifications([sample], today)
    return 0


def run_list(args: argparse.Namespace) -> int:
    today = dt.date.today()
    data = load_json_file(Path(args.data), {"hankkeet": []})
    calls = sorted(data.get("hankkeet", []), key=lambda c: c.get("haku_alkaa", ""))
    if not calls:
        print("Ei hankkeita datassa.")
        return 0

    buckets = {"Aukeamassa": [], "Haku auki": [], "Päättynyt": [], "Tuntematon": []}
    for call in calls:
        buckets[compute_status(call, today)].append(call)

    print(f"\nfunding_data.json — {len(calls)} hanketta "
          f"(päivitetty {data.get('paivitetty', '?')})\n")
    for status in ("Aukeamassa", "Haku auki", "Päättynyt", "Tuntematon"):
        items = buckets[status]
        if not items:
            continue
        print(f"── {status} ({len(items)}) " + "─" * (48 - len(status)))
        for call in items:
            start = dt.date.fromisoformat(call["haku_alkaa"])
            days = (start - today).days
            countdown = f"  ⏳ {days} pv" if 0 <= days <= 30 else ""
            span = (f"{format_fi_date(call['haku_alkaa'])}–"
                    f"{format_fi_date(call['haku_paattyy'])}")
            print(f"   {span:<23}"
                  f"[{call['rahoittajatyyppi'][:8]:<8}] "
                  f"{truncate(call['nimi'], 52):<54}{countdown}")
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="funding_watcher.py",
        description="Seuraa hankerahoitushakuja ja päivittää funding_data.json:in.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Esimerkkejä:\n"
            "  python3 funding_watcher.py --test-sources -v\n"
            "  python3 funding_watcher.py --dry-run\n"
            "  python3 funding_watcher.py --source rakennerahastot_rss\n"
        ),
    )
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="funding_data.json polku")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="sources.json polku")
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="tilatiedoston polku")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV), help=".env-tiedoston polku")
    parser.add_argument("--source", help="aja vain tämä lähde-id")
    parser.add_argument("--dry-run", action="store_true",
                        help="näytä mitä tapahtuisi, älä kirjoita äläkä ilmoita")
    parser.add_argument("--no-notify", action="store_true", help="päivitä data, älä ilmoita")
    parser.add_argument("--html", default=str(BASE_DIR / "index.html"),
                        help="index.html polku (--embed / --sync-html)")
    parser.add_argument("--embed", action="store_true",
                        help="päivitä index.html:n varadata datan kirjoituksen jälkeen")
    parser.add_argument("--sync-html", action="store_true", dest="sync_html",
                        help="päivitä vain index.html:n varadata ja lopeta")
    parser.add_argument("--test-sources", action="store_true", help="testaa lähteiden toimivuus")
    parser.add_argument("--test-notify", action="store_true", help="lähetä testi-ilmoitus")
    parser.add_argument("--list", action="store_true", dest="list_calls",
                        help="tulosta nykyinen data tilan mukaan ryhmiteltynä")
    parser.add_argument("-v", "--verbose", action="store_true", help="yksityiskohtainen loki")
    parser.add_argument("-q", "--quiet", action="store_true", help="vain virheet")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    level = logging.INFO
    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.ERROR
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    load_dotenv(Path(args.env_file))

    try:
        if args.sync_html:
            ok = embed_into_html(Path(args.html), Path(args.data))
            return 0 if ok else 1
        if args.test_sources:
            return run_test_sources(args)
        if args.test_notify:
            return run_test_notify(args)
        if args.list_calls:
            return run_list(args)
        return run_watch(args)
    except KeyboardInterrupt:
        log.warning("Keskeytetty käyttäjän toimesta.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
