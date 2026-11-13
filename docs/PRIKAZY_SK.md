# Prehľad príkazov HDF Vision

Praktický rozcestník terminálových príkazov a skriptov projektu. Stav podľa
repozitára k 22. 10. 2026. Pri každej skupine je odkaz na podrobnosti alebo
priamo na príslušný skript.

## Odkiaľ príkazy spúšťať

Pokiaľ nie je uvedené inak, príkazy spúšťajte **na Jetsone v termináli,
z koreňového priečinka projektu**:

```bash
cd /home/hdf-jetson1/HDF_Vision
```

Docker skripty samy vytvárajú kontajner; netreba ich spúšťať vnútri Dockeru.
Vyžadujú dostupný Docker a zostavený obraz, štandardne `hdf_vision:dev`.
`sudo` používajte tam, kde je výslovne uvedené. Príkazy v tabuľkách sú
samostatné možnosti, nie postup, ktorý treba celý vykonať.

## Spustenie aplikácie

| Príkaz | Význam |
|---|---|
| `bash docker/run.sh` | Spustí HDF Vision v bežnom režime s kamerami, Pico a grafickým rozhraním. |
| `python3 tools/setup-startup.py` | Jednorazovo nastaví celý overený Linux štart s logom aj spúšťač bez terminálu (Jetson Linux/UEFI 36.4.7, GNOME/X11). Vyžiada sudo pre systémové kroky, nereštartuje. |
| `python3 tools/setup-startup.py --check` | Overí predpoklady tohto nastavenia bez inštalácie. |
| `python3 tools/setup-launcher.py` | Nastaví ikonu a automatický štart s logom, plynulým prechodom a bez terminálu. |
| `python3 tools/launch-app.py` | Spustí aplikáciu s úvodným logom a výpismi do súboru. |
| `python3 tools/setup-launcher.py --restore` | Obnoví pôvodnú ikonu a automatické spúšťanie zo zálohy. |
| `sudo bash docker/run.sh --admin` | Spustí servisný ADMIN režim, ktorý obchádza výzvy na spoločné heslo pri zápise receptov aj otvorení ponuky Napájanie. Heslo nemení a audit zostáva aktívny. Režim skončí ukončením aplikácie. |

Spúšťajte z grafickej pracovnej plochy Jetsonu. Nespúšťajte súčasne druhú
aplikáciu nad rovnakou kamerou/Pico. Skript spracuje aj požiadavku aplikácie
na vypnutie alebo reštart hostiteľského Jetsonu.

Podrobnosti: [Docker / Jetson](../README.md#docker--jetson),
[spúšťanie s logom a bez terminálu](setup_launcher_SK.md),
[ochrana receptov](../README.md#recipe-security), [skript run.sh](../docker/run.sh).

Pre zostavu s dvoma kamerami a jedným Picom platí rovnaký
spúšťací príkaz. [Priradenie kanálov, recepty a AND skupiny](DUAL_CAMERA_SK.md).

## Zostavenie Docker obrazu

| Príkaz | Význam |
|---|---|
| `bash docker/build.sh` | Zostaví obraz a načíta ho do lokálneho Dockeru. Predvolená platforma je Jetson `linux/arm64`. |
| `bash docker/build.sh --no-cache` | Zostaví obraz bez použitia existujúcej zostavovacej cache; môže trvať výrazne dlhšie. |
| `bash docker/build.sh --no-load` | Zostaví obraz bez načítania do lokálneho Dockeru; určené pre vývojový build/cache. |
| `bash docker/build.sh --platform=linux/arm64` | Explicitne vyberie platformu obrazu. Iná platforma sama osebe nezaručuje kompatibilitu Jetson závislostí. |
| `IMAGE_NAME=registry.example.com/hdf/vision:verzia bash docker/build.sh --push` | Zostaví a odošle obraz do registra; nenačíta ho lokálne. Ukážkový názov nahraďte vlastným registrom a tagom, ku ktorému máte prístup. |

Voľby možno kombinovať. Skript `build.sh` nemá voľbu `--help`.

Premenné prostredia pre zostavenie:

| Premenná | Predvolené | Význam |
|---|---|---|
| `IMAGE_NAME` | `hdf_vision:dev` | Názov a tag obrazu. Podporujú ho aj `run.sh`, `security.sh` a `contact.sh`. |
| `DOCKERFILE` | `docker/Dockerfile` | Súbor s postupom zostavenia. |
| `CONTEXT_DIR` | `.` | Priečinok so vstupmi pre zostavenie. |
| `CACHE_DIR` | `.docker-cache` | Lokálna cache zostavenia. |
| `PLATFORM` | `linux/arm64` | Cieľová platforma; voľba `--platform=...` má prednosť. |

Napríklad vlastný lokálny tag použite pri zostavení aj spustení:

```bash
IMAGE_NAME=hdf_vision:servis bash docker/build.sh
IMAGE_NAME=hdf_vision:servis bash docker/run.sh
```

Podrobnosti: [Docker / Jetson](../README.md#docker--jetson),
[skript build.sh](../docker/build.sh).

## Spoločné heslo pre recepty a napájanie

Aplikácia nemusí byť spustená. Heslá sa zadávajú až do interaktívnej výzvy,
nie ako súčasť príkazu.

| Príkaz | Význam |
|---|---|
| `bash docker/security.sh status` | Zobrazí, či je ochrana receptov a Napájania zapnutá. |
| `bash docker/security.sh set-password` | Nastaví prvé heslo. Ak už existuje, použite `change-password`. |
| `bash docker/security.sh change-password` | Zmení heslo po overení aktuálneho hesla. |
| `bash docker/security.sh verify` | Overí zadané heslo bez zmeny nastavení. |
| `bash docker/security.sh remove-password` | Po overení hesla a potvrdení vypne ochranu receptov aj ponuky Napájanie. |
| `sudo bash docker/security.sh reset-password` | Nastaví nové heslo bez znalosti pôvodného; vyžaduje oprávnenia správcu Linuxu. Heslo neodstraňuje. |
| `bash docker/security.sh --help` | Zobrazí dostupné príkazy; funguje aj bez zostaveného obrazu. |

Rovnaké heslo chráni zápis receptov aj ponuku **NASTAVENIA → Systém → Napájanie**.
Pri existujúcom hesle sa nič ďalšie nenastavuje. Bez hesla fungujú obe oblasti
bez výzvy. V ponuke sú **Zavrieť aplikáciu · pracovná plocha**, **Reštartovať počítač**
a **Vypnúť počítač**. Zatvorenie aplikácie ponechá počítač zapnutý; pri každej
akcii sa najprv bezpečne ukončia všetky kamerové stanice. Heslo sa zadáva cez
systémovú klávesnicu GNOME, ktorá sa vyvolá dotykom textového poľa. Odomknutie platí len do zatvorenia
ponuky. Servisný režim `sudo bash docker/run.sh --admin` obíde obe výzvy.

Namiesto `--help` možno použiť `-h` alebo `help`; pomoc zobrazí aj spustenie
bez argumentov. Nastavenie sa ukladá do `/data/security.json`.

Podrobnosti: [ochrana receptov](../README.md#recipe-security),
[skript security.sh](../docker/security.sh).

## Kontaktné údaje v aplikácii

| Príkaz | Význam |
|---|---|
| `bash docker/contact.sh set-info` | Interaktívne nastaví alebo zmení meno, telefón a e-mail zobrazované v okne Kontakt. |
| `bash docker/contact.sh --help` | Zobrazí použitie. Funguje aj `-h`, `help` alebo spustenie bez argumentov. |

Údaje sa ukladajú do `/data/contact.json`. Pri ďalšom otvorení okna Kontakt
sa načítajú bez reštartu aplikácie. Prázdne zadanie alebo zrušenie nemení
pôvodný kontakt.

Podrobnosti: [lokálny kontakt](CONTACT_SK.md).

## Nerušiť a upozornenia na aktualizácie

Tieto príkazy spúšťajte **bez sudo, mimo Dockeru, v termináli prihlásenej
pracovnej plochy Jetsonu**. Platia pre používateľa, ktorý ich spustí.

| Príkaz | Význam |
|---|---|
| `bash tools/setup-desktop.sh` | Jednorazovo zapne Nerušiť, potlačí oznámenia aktualizácií a zakáže automatické spúšťanie Ubuntu Update Notifier. |
| `bash tools/setup-desktop.sh --restore` | Obnoví pôvodné uložené nastavenia vrátane predchádzajúceho stavu Nerušiť. |
| `bash tools/setup-desktop.sh --help` | Zobrazí použitie; funguje aj `-h`. |

Po nastavení aj obnove sa odhláste a prihláste alebo reštartujte Jetson.
Nerušiť sa prepne ihneď, už otvorené okná sa nezatvárajú. Samotné aktualizácie
sa nevypínajú. Opakované spustenie zachová prvú zálohu; nastavenia pretrvajú reštart.

Podrobnosti: [tichá pracovná plocha](../README.md#tichá-pracovná-plocha-jetsonu),
[skript setup-desktop.sh](../tools/setup-desktop.sh).

## Oprávnenia kamery – jednorazová inštalácia

Voliteľný postup z README pre prístup k UVC kamere:

```bash
sudo cp docker/99-hdf-uvc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Prvý príkaz nainštaluje pravidlá zariadení, druhý ich načíta a tretí znova
vyvolá spracovanie zariadení. Ide o systémové nastavenie pre celý Jetson.
Pravidlá nastavujú skupinu `video` a pre zodpovedajúce zariadenia See3CAM
povoľujú čítanie aj zápis všetkým používateľom (`0666`).

Podrobnosti: [Docker / Jetson](../README.md#docker--jetson),
[presné pravidlá](../docker/99-hdf-uvc.rules).

## Servisné overovanie a testy

### Historické hardvérové merania

Meracie skripty a ich výsledky sú v externom archíve uvedenom v hlavnom README
projektu. Historické skripty obsahujú pôvodné cesty; pred ďalším meraním ich
treba pripraviť pre samostatný testovací priečinok mimo aplikácie.

### Automatizované testy aplikácie

V pripravenom testovacom Docker prostredí s nainštalovaným `pytest`,
z koreňového priečinka projektu:

```bash
python3 -m pytest tests -q -p no:cacheprovider
```

Testy GUI používajú `QT_QPA_PLATFORM=offscreen`. Používajte oddelené testovacie
dáta, nie produkčné `/data`. Produkčný obraz nemusí mať `pytest` nainštalovaný.
Kompletné príkazy na vytvorenie testovacieho kontajnera a podmienky jednotlivých
kontrol sú v [overení polohy kamery](CAMERA_POSITION_VERIFICATION_SK.md).
Historický implementačný report Empty Mold V2 je v externom archíve.
Cloudový pomocný príkaz `/workspace/hdf-runtime/run-python` zo starších reportov
nie je štandardný príkaz tohto Jetsonu.

Pre vývojára: `git diff --check` skontroluje formátovanie zmien a
`bash -n tools/setup-desktop.sh` skontroluje syntax skriptu bez jeho vykonania.
Rovnako možno skontrolovať jednotlivé `docker/*.sh` súbory.

## Aktualizácia firmvéru Pico

Na Jetson hoste, mimo Dockeru, po zastavení výroby a zatvorení HDF Vision:

| Príkaz | Význam |
|---|---|
| `bash docker/update-pico.sh` | Zistí verziu jediného pripojeného Pico a starší HDF firmvér aktualizuje podľa lokálneho `firmware/pico/main.py`. Pred zápisom vytvorí zálohu. |
| `bash docker/update-pico.sh --check` | Iba porovná verzie, bez nahrávania a reštartu Pico. |
| `bash docker/update-pico.sh --port /dev/ttyACM0` | Vyberie konkrétne Pico; pri viacerých zariadeniach je výber povinný. |
| `bash docker/update-pico.sh --check --port /dev/ttyACM0` | Skontroluje konkrétne Pico bez aktualizácie. |
| `bash docker/update-pico.sh --help` | Zobrazí pomoc; funguje aj `-h`, bez potreby Docker obrazu. |

Skript používa rovnaký obraz `hdf_vision:dev` ako aplikácia (možno zmeniť cez
`IMAGE_NAME`). Nový Docker build nie je potrebný, ak tento obraz už existuje.
Najnovšia verzia znamená verziu v lokálnom projekte; skript nesťahuje GitHub.
Rovnakú ani novšiu verziu neprepisuje. Aktualizuje iba HDF `main.py`, nie MicroPython.
Zálohy sú v `artifacts/pico-backups/`, mimo Gitu.

Podrobnosti: [aktualizácia Pico, zálohy a obnova](PICO_UPDATE_SK.md).

## Sériové príkazy Pico

**Nie sú to príkazy pre linuxový terminál.** Posielajú sa Pico cez USB serial.
Port nesmie súčasne používať aplikácia a ďalší sériový terminál.

| Príkaz alebo skupina | Význam a podrobnosti |
|---|---|
| `STATUS`, `INPUTS` | Verzia firmvéru, konfigurácia a stav vstupov. Viacriadkové odpovede končia `END`. |
| `SET ...`, `MAP ...`, `SAVE` | Nastavenie časovania, mapovanie vstupov a uloženie do flash. `SET` a `MAP` menia RAM, až `SAVE` nastavenie uloží. |
| `FIRE V1`, `FIRE V2`, `TRIGGER IN1` až `TRIGGER IN8` | Vyvolanie cyklu podľa režimu a mapovania; nejde iba o čítanie stavu. |
| `LIGHT ON`, `LIGHT OFF`, `LIGHT STATUS` | Manuálne svetlo a jeho stav; povolenie závisí od SESSION a verzie firmvéru. |
| `SESSION IDLE`, `SESSION MASTER`, `SESSION TRIGGER` | Režim riadený aplikáciou vo firmvéri 4.x. |
| `PIO FIRE ...` | Nízkoúrovňový časovaný cyklus kamery a svetla; syntax a jednotky určuje protokol PIO. |

Úplná syntax a verzované pravidlá: [aktuálny Pico firmvér 4.1](../firmware/pico/README.md),
historická integrácia PIO v externom archíve uvedenom v hlavnom README,
[starší protokol 3.3](../README.md#serial-command-reference).
Staršie `MODE/TRIG/GAP/COUNT` nastavenia nemožno automaticky považovať za
riadenie nového PIO režimu. Historické laboratórne skripty mimo tohto repozitára
(napr. `cu55_trigger_lab/pico_ram.py`) nie sú súčasťou distribuovaných nástrojov HDF Vision.

## Čo zatiaľ nemá nastavovací príkaz

Automatické spustenie HDF Vision po boote, kiosk režim a vlastné štartovacie logo
zatiaľ nemajú v projekte pripravený inštalačný skript.
