# Käyttöönotto Windowsilla

Kaikki tarvittava on klikattavissa. Alla oleva järjestys on nopein.

---

## 1. Onko Python jo asennettu?

Avaa komentokehote (**Win + R** → `cmd` → Enter) ja kirjoita:

```
py --version
```

Jos vastauksena tulee esimerkiksi `Python 3.12.4`, olet valmis — **siirry
kohtaan 2**.

Jos tulee `'py' ei ole tunnistettu komennoksi`, asenna Python jommallakummalla
tavalla:

- **Microsoft Store** → hae *Python 3.12* → **Hae**. Tämä on helpoin, koska
  PATH-asetus tulee automaattisesti oikein.
- Tai komentokehotteessa: `winget install Python.Python.3.12`

> Jos asennat python.orgin lataamalla asennusohjelmalla, **rastita heti
> ensimmäisessä ikkunassa "Add python.exe to PATH"**. Ilman sitä `.bat`-
> tiedostot eivät löydä Pythonia.

---

## 2. Avaa kalenteri

Kaksoisklikkaa:

### `Avaa hankekalenteri.bat`

Tämä on ohjelma. Se tekee kaiken yhdellä klikkauksella:

1. avaa kalenterin selaimeen **heti** nykyisillä tiedoilla,
2. hakee taustalla kaikki rahoituslähteet,
3. päivittää sivun itsestään kun haku valmistuu — sivua ei tarvitse ladata
   uudelleen,
4. lähettää ilmoituksen, jos uusia hakuja löytyi ja `.env` on täytetty,
5. tarkistaa uudelleen 6 tunnin välein niin kauan kuin ohjelma on auki.

Työkalupalkkiin ilmestyy tilamerkki, joka kertoo mitä on menossa:

| Merkki | Tarkoittaa |
|---|---|
| 🟠 *Haetaan… 3/8 lähdettä* | Haku on käynnissä |
| 🔵 *2 uutta hakua · ilmoitettu* | Uusia löytyi, ilmoitus lähti |
| 🟢 *Tarkistettu 4 min sitten* | Kaikki ajan tasalla |
| 🔴 *…ei vastannut* | Osa lähteistä ei vastannut |

Vieressä oleva **↻-painike hakee tiedot heti**, milloin tahansa.

Tänään löytyneet haut on merkitty listassa **uusi**-merkinnällä.

**Sulje ohjelma sulkemalla musta komentoikkuna** tai painamalla siinä Ctrl+C.

> **Miksei haku käynnisty joka kerta?** Jos tiedot on haettu alle 3 tuntia
> sitten, ohjelma ei hae uudelleen — se vain avaa kalenterin. Näin lähteitä
> ei kuormiteta turhaan, jos avaat ohjelman useasti päivässä. ↻-painike
> ohittaa tämän aina.

> Voit myös vain kaksoisklikata `index.html`-tiedostoa ilman ohjelmaa.
> Silloin kalenteri näyttää viimeksi haetut tiedot, muttei päivitä niitä
> eikä tilamerkkiä näy.

---

## 3. Ota ilmoitukset käyttöön

1. Kopioi `.env.example` → nimeä kopio `.env`
   (File Explorerissa: valitse tiedosto, **Ctrl+C**, **Ctrl+V**, nimeä
   uudelleen `.env`)
2. Avaa `.env` Muistiossa ja liitä Discord-webhookin osoite riville
   `DISCORD_WEBHOOK_URL=`
3. Avaa `Avaa hankekalenteri.bat` uudelleen — se hakee tiedot ja lähettää
   ilmoituksen, jos uusia hakuja löytyi

> **Näytetäänkö tiedostopääte?** Jos Windows piilottaa päätteet, `.env`
> voi tallentua nimellä `.env.txt` eikä toimi. Explorerissa:
> **Näytä → Näytä → Tiedostotunnisteet**.

---

## 4. Ajastus (valinnainen)

**Tätä ei useimmiten tarvita.** Ohjelma hakee tuoreet tiedot aina
avattaessa ja 6 tunnin välein ollessaan auki. Ajastus on hyödyllinen vain,
jos haluat ilmoituksen Discordiin **myös silloin kun ohjelma ei ole auki**
— esimerkiksi jotta et missaa haun aukeamista lomalla.

Kaksoisklikkaa:

### `Asenna paivittainen ajastus.bat`

Se rekisteröi Windowsin Tehtävien ajoitukseen päivittäisen ajon klo 07:00.
Ajo tapahtuu taustalla ilman näkyvää ikkunaa.

| | |
|---|---|
| Testaa heti | `schtasks /run /tn "Hankerahoituskalenteri"` |
| Katso tila | `schtasks /query /tn "Hankerahoituskalenteri"` |
| Poista | `schtasks /delete /tn "Hankerahoituskalenteri" /f` |

Jos tehtävän luonti epäonnistuu oikeuksien takia: klikkaa `.bat`-tiedostoa
hiiren oikealla → **Suorita järjestelmänvalvojana**.

---

## 5. Exe (jos haluat toimivuuden ilman Pythonia)

Kaksoisklikkaa:

### `Rakenna exe.bat`

Se asentaa PyInstallerin ja kääntää kaksi itsenäistä ohjelmaa. Kestää pari
minuuttia ja tarvitsee verkkoyhteyden kerran.

| Ohjelma | Mitä tekee |
|---|---|
| **`Hankekalenteri.exe`** | **Tätä klikataan.** Avaa kalenterin ja hakee tuoreet tiedot. |
| `funding_watcher.exe` | Pelkkä haku ilman käyttöliittymää, ajastettua taustaajoa varten. |

Tämän jälkeen `.bat`-tiedostot käyttävät automaattisesti exejä Pythonin
sijaan — ja `Hankekalenteri.exe` toimii myös suoraan kaksoisklikkaamalla.

### Toiselle koneelle vieminen

**Kopioi koko kansio, ei pelkkää exeä.** Exe lukee vierestään tiedostot
`sources.json`, `funding_data.json` ja `.env`, eikä toimi ilman niitä.
Vastaanottavalla koneella ei tarvita Pythonia.

Kansiossa on tällöin:

```
Hankekalenteri.exe           ← KLIKKAA TÄTÄ. Ei tarvitse Pythonia.
index.html                   ← kalenterin käyttöliittymä
funding_data.json            ← data
sources.json                 ← seurattavat lähteet
.env                         ← webhook-osoite
funding_watcher.exe          ← valinnainen, ajastettua ajoa varten
```

Huomioitavaa:

- **Pelkkä kalenterin katselu ei tarvitse exeä eikä Pythonia.** `index.html`
  aukeaa millä tahansa koneella selaimessa ja näyttää viimeksi haetut tiedot.
  Vain automaattinen haku tarvitsee ohjelman.
- `Hankekalenteri.exe` kirjoittaa `funding_data.json`-tiedostoon, joten se
  tarvitsee kirjoitusoikeuden kansioon. Älä sijoita sitä `C:\Program Files`
  -kansioon.
- **Virustorjunta saattaa merkitä PyInstaller-exen epäilyttäväksi.** Tämä on
  tunnettu väärä hälytys, joka johtuu siitä miten PyInstaller pakkaa
  tulkin exen sisään. Jos exe poistetaan automaattisesti eikä sitä voi
  sallia, käytä `.bat`-tiedostoja — ne tekevät saman asian.

---

## Vaihtoehto: ei asennuksia mihinkään koneeseen

Jos tavoite on että seuranta toimii ilman että kukaan asentaa mitään, **GitHub
Actions on parempi ratkaisu kuin exe.** Silloin:

- watcher ajetaan GitHubin palvelimilla joka arkiaamu,
- kalenteri julkaistaan verkko-osoitteeseen, jonka voi avata millä tahansa
  laitteella — myös puhelimella,
- kenenkään koneelle ei asenneta mitään.

Ohje on `README.md`-tiedoston kohdassa **3.2 GitHub Actions**. Työnkulku on
valmiina tiedostossa `.github/workflows/funding-watcher.yml`.

---

## Vianetsintä

| Oire | Korjaus |
|---|---|
| `.bat` avautuu ja sulkeutuu heti | Avaa komentokehote, vedä `.bat`-tiedosto ikkunaan ja paina Enter — virheilmoitus jää näkyviin. |
| `'py' ei ole tunnistettu komennoksi` | Python puuttuu tai PATH-asetus jäi tekemättä. Asenna uudelleen Microsoft Storesta. |
| Selain sanoo "sivustoon ei saada yhteyttä" | Ohjelman ikkuna sulkeutui. Avaa `Avaa hankekalenteri.bat` uudelleen. |
| Portti varattu | Ohjelma etsii vapaan portin automaattisesti väliltä 8777–8801. Jos kaikki ovat varattuja: `Hankekalenteri.exe --port 9100`. |
| Tilamerkkiä ei näy työkalupalkissa | Avasit `index.html`-tiedoston suoraan, et ohjelman kautta. Tilamerkki näkyy vain ohjelman tarjoillessa sivun. |
| "Haetaan…" jumittuu | Jokin lähde vastaa hitaasti. Odota noin minuutti; jokaisella lähteellä on 30 s aikakatkaisu ja 3 yritystä. |
| Ääkköset näkyvät väärin komentoikkunassa | Kosmeettista. Toiminta ei häiriinny. |
| Ajastettu tehtävä ei tee mitään | Tarkista että `.env` on kansiossa ja että polut ovat oikein: `schtasks /query /tn "Hankerahoituskalenteri" /v /fo list` |
| Windows Defender esti `.bat`-tiedoston | Tiedoston ominaisuudet → rastita **Poista esto** (*Unblock*). Tämä leima tulee netistä ladatuille tiedostoille. |
