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
| `sudo bash docker/run.sh --admin` | Spustí servisný ADMIN režim, ktorý obchádza výzvu na heslo pri zápise receptov. Heslo nemení a audit zostáva aktívny. Režim skončí ukončením aplikácie. |

Spúšťajte z grafickej pracovnej plochy Jetsonu. Nespúšťajte súčasne druhú
aplikáciu nad rovnakou kamerou/Pico. Skript spracuje aj požiadavku aplikácie
na vypnutie alebo reštart hostiteľského Jetsonu.

Podrobnosti: [Docker / Jetson](../README.md#docker--jetson),
[ochrana receptov](../README.md#recipe-security), [skript run.sh](../docker/run.sh).

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

## Heslo a ochrana receptov

Aplikácia nemusí byť spustená. Heslá sa zadávajú až do interaktívnej výzvy,
nie ako súčasť príkazu.

| Príkaz | Význam |
|---|---|
| `bash docker/security.sh status` | Zobrazí, či je ochrana receptov zapnutá. |
| `bash docker/security.sh set-password` | Nastaví prvé heslo. Ak už existuje, použite `change-password`. |
| `bash docker/security.sh change-password` | Zmení heslo po overení aktuálneho hesla. |
| `bash docker/security.sh verify` | Overí zadané heslo bez zmeny nastavení. |
| `bash docker/security.sh remove-password` | Po overení hesla a potvrdení vypne ochranu receptov. |
| `sudo bash docker/security.sh reset-password` | Nastaví nové heslo bez znalosti pôvodného; vyžaduje oprávnenia správcu Linuxu. Heslo neodstraňuje. |
| `bash docker/security.sh --help` | Zobrazí dostupné príkazy; funguje aj bez zostaveného obrazu. |

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

### Hardvérová validácia Pico PIO

Nasledujúci príkaz patrí **dovnútra pripraveného aplikačného Docker prostredia**,
v ktorom je projekt dostupný v pracovnom priečinku, importovateľný modul `app`
a sprístupnená kamera/HID/Pico:

```bash
python3 tools/validate_pico_pio.py --output /tmp/hdf-pio-validation-01 --samples 20 --switches 3
```

| Voľba | Význam |
|---|---|
| `--output ADRESAR` | Povinný nový, ešte neexistujúci priečinok pre snímky, stav a logy. Použite pripojený priečinok hostiteľa, ak majú výsledky prežiť odstránenie kontajnera. |
| `--samples 20` | Počet snímok na kombináciu rozlíšenia a expozície; predvolene 20, najmenej 1. |
| `--switches 3` | Počet prepnutí MASTER/TRIGGER pre rozlíšenie; predvolene 3, najmenej 0. |
| `--help` | Pomoc nástroja; vyžaduje prostredie s jeho Python závislosťami. |

Je to servisný nástroj pre zastavenú výrobu, nie bežná kontrola stavu.
Mení expozíciu, rozlíšenie, mapovanie a dočasné Pico profily; neposiela `SAVE`
ani neprepisuje firmvér. Má pevne zadaný port `/dev/ttyACM0`, preto pred použitím
overte pripojené zariadenie. Nesľubuje obnovu všetkých pôvodných nastavení.
Aktuálnu kompatibilitu s pripojenou kamerou a verziou aplikácie treba overiť;
historický validačný report nie je dôkazom nového testu.

Podrobnosti: [validačný report](PIO_VALIDATION_20260911_SK.md),
[zdroj nástroja](../tools/validate_pico_pio.py),
[stav implementácie PIO](PIO_IMPLEMENTATION_CHECKPOINT.md).

### Automatizované testy aplikácie

V pripravenom testovacom Docker prostredí s nainštalovaným `pytest`,
z koreňového priečinka projektu:

```bash
python3 -m pytest tests -q -p no:cacheprovider
```

Testy GUI používajú `QT_QPA_PLATFORM=offscreen`. Používajte oddelené testovacie
dáta, nie produkčné `/data`. Produkčný obraz nemusí mať `pytest` nainštalovaný.
Kompletné príkazy na vytvorenie testovacieho kontajnera a podmienky jednotlivých
kontrol sú v [overení polohy kamery](CAMERA_POSITION_VERIFICATION_SK.md)
a [reporte Empty Mold V2](EMPTY_MOLD_V2_REPORT_SK.md).
Cloudový pomocný príkaz `/workspace/hdf-runtime/run-python` zo starších reportov
nie je štandardný príkaz tohto Jetsonu.

Pre vývojára: `git diff --check` skontroluje formátovanie zmien a
`bash -n tools/setup-desktop.sh` skontroluje syntax skriptu bez jeho vykonania.
Rovnako možno skontrolovať jednotlivé `docker/*.sh` súbory.

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
[integrácia PIO](PIO_APP_INTEGRATION_SK.md),
[starší protokol 3.3](../README.md#serial-command-reference).
Staršie `MODE/TRIG/GAP/COUNT` nastavenia nemožno automaticky považovať za
riadenie nového PIO režimu. Historické laboratórne skripty mimo tohto repozitára
(napr. `cu55_trigger_lab/pico_ram.py`) nie sú súčasťou distribuovaných nástrojov HDF Vision.

## Čo zatiaľ nemá nastavovací príkaz

Automatické spustenie HDF Vision po boote, kiosk režim a vlastné štartovacie logo
zatiaľ nemajú v projekte pripravený inštalačný skript.
