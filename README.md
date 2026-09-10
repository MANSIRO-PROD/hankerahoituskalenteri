# Hankerahoituskalenteri

Interaktiivinen kalenteri hankerahoitushauista + taustascripti, joka seuraa
rahoituslähteitä automaattisesti ja ilmoittaa uusista hauista.

> **Windows-käyttäjä?** Kaikki on klikattavissa — katso
> [`KAYTTOONOTTO_WINDOWS.md`](KAYTTOONOTTO_WINDOWS.md).

```
hankerahoituskalenteri/
├── hankekalenteri.py                   Ohjelma: avaa kalenterin + hakee tiedot
├── index.html                          Kalenteri- ja listanäkymä
├── funding_data.json                   Hankedata — päivittyy automaattisesti
├── funding_watcher.py                  Hakumoottori (ei riippuvuuksia)
├── sources.json                        Seurattavat rahoituslähteet
├── .env.example                        Ilmoituskanavien asetuspohja
├── requirements.txt                    Valinnaiset riippuvuudet (vain HTML-raaputus)
│
├── Avaa hankekalenteri.bat             Windows: käynnistää ohjelman
├── Tarkista rahoitushaut.bat           Windows: pelkkä haku ilman käyttöliittymää
├── Asenna paivittainen ajastus.bat     Windows: taustaajo (valinnainen)
├── Rakenna exe.bat                     Windows: kääntää itsenäiset exet
├── KAYTTOONOTTO_WINDOWS.md             Windows-ohje
│
└── .github/workflows/funding-watcher.yml   Valmis GitHub Actions -ajastus
```

## Nopein tapa: käynnistä ohjelma

```bash
python3 hankekalenteri.py
```

Kalenteri aukeaa selaimeen heti, ja tuoreimmat rahoitushaut haetaan taustalla.
Sivu päivittää itsensä kun haku valmistuu. Työkalupalkin tilamerkki kertoo
mitä on menossa, ja sen vieressä oleva ↻-painike hakee tiedot koska tahansa.

| Valitsin | Merkitys |
|---|---|
| `--force` | Hae aina, vaikka data olisi tuoretta |
| `--no-refresh` | Avaa kalenteri hakematta mitään |
| `--max-age-hours 3` | Hae vain jos data on tätä vanhempaa (oletus 3 h) |
| `--interval-hours 6` | Automaattisen tarkistuksen väli ohjelman ollessa auki |
| `--port 8777` | Portti (etsii vapaan automaattisesti jos varattu) |
| `--no-browser` | Älä avaa selainta |
| `--no-notify` | Älä lähetä ilmoituksia |

Loput tästä dokumentista käsittelee `funding_watcher.py`-hakumoottoria, jota
tarvitaan erikseen vain ajastettuun taustaajoon.

---

## 1. Kalenterin avaaminen

**Suositeltu tapa** — käynnistä kevyt paikallinen palvelin kansiossa:

```bash
cd hankerahoituskalenteri
python3 -m http.server 8000
```

Avaa selaimessa <http://localhost:8000>. Näin sivu lukee aina tuoreen
`funding_data.json`-tiedoston, jonka watcher on päivittänyt.

**Pelkän tiedoston avaaminen** (kaksoisklikkaus) toimii myös: selain estää
`file://`-tilassa sisartiedostojen lukemisen, joten sivu putoaa takaisin
HTML:ään upotettuun varadataan ja kertoo siitä huomautuksella.
`funding_watcher.py --embed` pitää upotetun varadatan ajan tasalla.

### Mitä sivulla on

| Ominaisuus | Kuvaus |
|---|---|
| **30 päivän laskurit** | Jokaiselle 30 päivän sisällä aukeavalle haulle sekunnilleen tikittävä laskuri. Alle 7 päivän päässä olevat merkitään punaisella. |
| **Kalenterinäkymä** | Kuukausiruudukko, viikko alkaa maanantaista, viikkonumerot vasemmassa reunassa. Palkin pituus = hakuaika. |
| **Jatkuva haku -rivi** | Yli 150 päivän hakuajat nostetaan ruudukon yläpuolelle, jottei vuoden mittainen palkki peitä varsinaisia hakuikkunoita. |
| **Listanäkymä** | Ryhmitelty tilan mukaan, päivämäärät ja jäljellä oleva aika näkyvissä. |
| **Suodattimet** | Rahoittajatyyppi (Julkinen / Säätiöt / EU) ja tila (Aukeamassa / Haku auki / Päättynyt) + vapaa tekstihaku. Valinnat säilyvät selaimessa. |
| **Hankemodaali** | Nimi ja rahoittaja, alkamis- ja päättymispäivä, "mihin rahaa voi saada", kohderyhmät, rahoituksen määrä ja suora linkki hakusivulle. |
| **.ics-vienti** | Lisää hakuajan omaan kalenteriin, muistutus 7 päivää ennen aukeamista. |
| **Näppäimistö** | `/` hakukenttään, `Esc` sulkee modaalin. |

Sivu toimii vaaleana ja tummana selaimen asetuksen mukaan; kuun kuvake
vaihtaa teeman käsin.

---

## 2. Watcherin käyttöönotto

### 2.1 Ilmoituskanava

```bash
cp .env.example .env
```

Avaa `.env` ja liitä Discord-webhookin URL:

> Discord → **Palvelimen asetukset** → **Integraatiot** → **Webhookit** →
> **Uusi webhook** → valitse kanava → **Kopioi webhookin URL**

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/…
```

Testaa, että ilmoitus menee perille:

```bash
python3 funding_watcher.py --test-notify
```

Telegram, Slack ja sähköposti toimivat samalla tavalla — täytä vain
vastaavat rivit `.env`-tiedostossa. Kaikki konfiguroidut kanavat saavat
ilmoituksen; mitään ei tarvitse valita erikseen.

### 2.2 Lähteiden tarkistus

```bash
python3 funding_watcher.py --test-sources -v
```

```
  ✓  eu_funding_tenders        eu_sedia   43 riviä,  38 käyttökelpoista (2.1s)
  ✓  koneen_saatio             rss        18 riviä,   4 käyttökelpoista (0.6s)
  ✗  business_finland_uutiset  rss        VIRHE: HTTP 404 Not Found
  ⏸  esimerkki_html_raaputus   html       pois käytöstä
```

**Tämä askel kannattaa tehdä ensin.** Mukana toimitetut lähde-URLit ovat
parhaita arvauksia: julkishallinnon syötepolut muuttuvat, eikä niitä ole
voitu varmentaa tätä pakettia koottaessa. Korjaa toimimattomat `url`-kentät
`sources.json`-tiedostoon tai aseta niille `"enabled": false`.
Yhden lähteen kaatuminen ei koskaan kaada koko ajoa.

### 2.3 Koeajo ilman muutoksia

```bash
python3 funding_watcher.py --dry-run
```

Näyttää mitä lisättäisiin, muttei kirjoita tiedostoja eikä lähetä ilmoituksia.

### 2.4 Oikea ajo

```bash
python3 funding_watcher.py --embed
```

`--embed` päivittää samalla `index.html`:n varadatan, jotta sivu näyttää
tuoreet tiedot myös suoraan tiedostona avattuna.

### Komennot

| Komento | Tekee |
|---|---|
| `python3 funding_watcher.py` | Normaali ajo: hakee, päivittää, ilmoittaa |
| `--dry-run` | Näyttää löydöt, ei kirjoita eikä ilmoita |
| `--no-notify` | Päivittää datan, ei lähetä ilmoituksia |
| `--test-sources -v` | Testaa jokaisen lähteen ja näyttää esimerkkirivit |
| `--test-notify` | Lähettää testi-ilmoituksen |
| `--list` | Tulostaa nykyisen datan tilan mukaan ryhmiteltynä |
| `--source <id>` | Ajaa vain yhden lähteen (kätevä uutta lähdettä säätäessä) |
| `--sync-html` | Päivittää vain `index.html`:n varadatan |
| `--embed` | Päivittää varadatan ajon päätteeksi |
| `-v` / `-q` | Yksityiskohtainen loki / vain virheet |

---

## 3. Ajastus

### 3.1 crontab (Linux, macOS)

```bash
crontab -e
```

Lisää rivi — päivittäinen ajo klo 07:00, loki tiedostoon:

```cron
0 7 * * * cd /polku/hankerahoituskalenteri && /usr/bin/python3 funding_watcher.py --embed --quiet >> watcher.log 2>&1
```

Huomioitavaa:

- **Käytä absoluuttisia polkuja.** Cronin `PATH` on hyvin suppea — `which python3`
  kertoo oikean polun.
- **`cd` kansioon ensin**, jotta script löytää `sources.json`- ja
  `.env`-tiedostot.
- Cron ei lue `~/.bashrc`:tä, joten ympäristömuuttujat tulevat `.env`-tiedostosta.
- Tarkista ajon jälkeen: `tail -20 watcher.log`.

Kahdesti päivässä (aamulla ja iltapäivällä):

```cron
0 7,15 * * 1-5 cd /polku/hankerahoituskalenteri && /usr/bin/python3 funding_watcher.py --embed --quiet >> watcher.log 2>&1
```

### 3.2 GitHub Actions

Valmis työnkulku on tiedostossa `.github/workflows/funding-watcher.yml`.
Se ajaa watcherin joka arkiaamu, commitoi löydetyt uudet haut takaisin
repositorioon ja julkaisee kalenterin GitHub Pagesiin.

Käyttöönotto:

1. Vie kansio GitHub-repositorioksi (`git init && git add . && git commit && git push`).
2. **Settings → Secrets and variables → Actions → New repository secret**
   → nimi `DISCORD_WEBHOOK_URL`, arvo webhookin URL.
3. **Settings → Pages → Source: GitHub Actions** (jos haluat kalenterin verkkoon).
4. **Actions**-välilehti → *Rahoitushakujen seuranta* → **Run workflow**
   testataksesi heti.

Ajastus on cronissa UTC-ajassa: `15 4 * * 1-5` = klo 06:15 Suomen talviaikaa,
07:15 kesäaikaa.

> **Huom.** Varmista ettei `.env` päädy repositorioon — mukana tuleva
> `.gitignore` estää sen. Webhookin URL on salaisuus: sillä voi kirjoittaa
> Discord-kanavallesi.

### 3.3 Windows

Tehtävien ajoitus (Task Scheduler) → **Luo tehtävä**:

- **Toiminto:** `python.exe`
- **Argumentit:** `funding_watcher.py --embed --quiet`
- **Aloita kohteesta:** kansion polku (tämä vastaa `cd`-komentoa)
- **Herätteet:** päivittäin klo 07:00

---

## 4. Lähteiden konfigurointi

`sources.json` ohjaa kaikkea. Uusi lähde on yksi olio `sources`-listassa.

### RSS/Atom-syöte

```json
{
  "id": "oma_rahoittaja",
  "nimi": "Oma rahoittaja – ajankohtaista",
  "tyyppi": "rss",
  "enabled": true,
  "url": "https://esimerkki.fi/feed/",
  "rahoittaja": "Oma rahoittaja",
  "rahoittajatyyppi": "Säätiöt",
  "fallback_link": "https://esimerkki.fi/haut",
  "include_keywords": ["haku", "apuraha"],
  "exclude_keywords": ["vuosikertomus"],
  "require_dates": true
}
```

### Kaikki kentät

| Kenttä | Oletus | Merkitys |
|---|---|---|
| `id` | — | Yksilöivä tunniste (pakollinen) |
| `tyyppi` | `rss` | `rss`, `json_api`, `eu_sedia` tai `html` |
| `url` | — | Syötteen tai rajapinnan osoite (pakollinen) |
| `enabled` | `true` | `false` ohittaa lähteen |
| `rahoittaja` | lähteen nimi | Rahoittajan nimi, jos syöte ei kerro sitä |
| `rahoittajatyyppi` | päätellään | `Julkinen`, `Säätiöt` tai `EU` |
| `include_keywords` | — | Rivi hyväksytään vain jos jokin sana löytyy |
| `exclude_keywords` | — | Rivi hylätään jos jokin sana löytyy |
| `require_dates` | `true` | `false` antaa päivämäärättömälle haulle oletusikkunan |
| `default_window_days` | `30` | Oletusikkunan pituus |
| `skip_if_closed_days` | `0` | Kuinka monta päivää päättyneitä hakuja vielä hyväksytään |
| `fallback_link` | `url` | Linkki, jos rivillä ei ole omaa |
| `max_items` | 40–100 | Käsiteltävien rivien yläraja |

### Miten watcher tulkitsee syötteen

1. **Päivämäärät** tunnistetaan otsikosta ja kuvauksesta: `1.10.2026`,
   `1.–31.10.2026`, `15.9.2026–14.11.2026`, `1. lokakuuta 2026`, `2026-10-05`
   sekä englanninkieliset muodot. Vihjesanat (*haku aukeaa*, *viimeinen
   hakupäivä*, *deadline*, *cut-off*) ratkaisevat kumpi päivä on kumpi.
2. **"Mihin rahaa voi saada"** ja **kohderyhmät** päätellään avainsanoista
   (palkkakulut, investoinnit, kansainvälistyminen, pk-yritykset,
   korkeakoulut …). Ne ovat aina karkeita — modaali kehottaa tarkistamaan
   ehdot hakusivulta.
3. **Duplikaatit** karsitaan kolmella tavalla: sama `id`, sama normalisoitu
   URL (seurantaparametrit poistettuna) tai yli 90 % samankaltainen otsikko
   samalta rahoittajalta. Sama nimi mutta yli 60 päivää eri hakuaika
   tulkitaan uudeksi hakukierrokseksi.
4. **Ilmoitus lähetetään kerran per haku.** `.watcher_state.json` muistaa
   jo ilmoitetut.

---

## 5. Datan rakenne

`funding_data.json` — voit myös lisätä hankkeita käsin samaan muotoon:

```json
{
  "versio": 1,
  "paivitetty": "2026-09-10T09:00:00+03:00",
  "hankkeet": [
    {
      "id": "yksilollinen-tunniste",
      "nimi": "Hankkeen tai haun nimi",
      "rahoittaja": "Rahoittajan nimi",
      "rahoittajatyyppi": "Julkinen",
      "ohjelma": "Ohjelman nimi",
      "haku_alkaa": "2026-10-01",
      "haku_paattyy": "2026-10-31",
      "summa": "150 000 – 700 000 €",
      "alue": "Koko Suomi",
      "kuvaus": "Lyhyt kuvaus.",
      "kayttotarkoitus": ["Palkkakulut", "Matkat"],
      "kohderyhmat": ["Yliopistot", "Pk-yritykset"],
      "linkki": "https://…"
    }
  ]
}
```

Pakollisia ovat `nimi`, `haku_alkaa` ja `haku_paattyy` (muodossa
`VVVV-KK-PP`). Muut kentät ovat valinnaisia — sivu jättää puuttuvat pois.
Tila (*Aukeamassa / Haku auki / Päättynyt*) lasketaan päivämääristä, joten
sitä ei tallenneta.

---

## 6. Vianetsintä

| Oire | Syy ja korjaus |
|---|---|
| Sivu näyttää huomautuksen upotetusta varadatasta | Sivu avattiin `file://`-osoitteesta. Käynnistä `python3 -m http.server 8000`. |
| "Dataa ei löytynyt" | `funding_data.json` puuttuu tai on viallista JSONia. Tarkista: `python3 -m json.tool funding_data.json`. |
| Lähde palauttaa `HTTP 404` | Syötteen osoite on muuttunut. Etsi uusi syöte rahoittajan sivulta ja päivitä `url`. |
| Lähde palauttaa rivejä, mutta 0 käyttökelpoista | Riveiltä ei löydy päivämääriä. Löysää `include_keywords`, tai aseta `"require_dates": false` ja `default_window_days`. |
| Ilmoitusta ei tule | Aja `--test-notify`. Tarkista että `.env` on samassa kansiossa ja webhookin URL on kokonainen. |
| Cron ei aja | Katso `grep CRON /var/log/syslog` (Linux). Yleisin syy: suhteellinen polku tai väärä `python3`-polku. |
| Sama haku ilmoitettiin kahdesti | Rahoittaja muutti otsikkoa tai URLia. Poista toinen käsin `funding_data.json`-tiedostosta. |
| Kalenteri on tyhjä | Kaikki tilasuodattimet pois päältä, tai hakukentässä on tekstiä. |

---

## 7. Huomioita

- **Mukana toimitettu data on esimerkkidataa.** Päivämäärät kuvaavat
  tyypillisiä hakukierroksia, mutta ne on tarkistettava rahoittajan omalta
  sivulta ennen hakemista. Watcher korvaa esimerkit oikeilla tiedoilla
  sitä mukaa kun lähteet tuottavat niitä.
- **Lähde-URLit vanhenevat.** Aja `--test-sources` säännöllisesti; se on
  nopein tapa huomata, että jokin syöte on siirtynyt.
- **Automaattinen tulkinta on apuväline, ei totuus.** Watcher lukee
  päivämäärät ja käyttötarkoitukset vapaasta tekstistä. Virhetulkinnat ovat
  mahdollisia, ja siksi jokaisessa hankkeessa on suora linkki lähteeseen.
- **Kunnioita lähteitä.** Päivittäinen ajo riittää; älä hae samaa syötettä
  minuutin välein.
- Tailwind CSS ladataan CDN:stä, mutta sivun kaikki kriittiset tyylit ovat
  tiedostossa itsessään — kalenteri toimii myös ilman verkkoyhteyttä.
