# HDF_Vision

HDF_Vision je QC vision aplikácia pre **NVIDIA Jetson Orin Nano**. Produkčný stack je JetPack, Docker, Python, PySide6, OpenCV, GStreamer/V4L2, kamera See3CAM_CU55M, Raspberry Pi Pico a SQLite. Windows sa používa iba na vývoj a editáciu; runtime, testovanie s hardvérom aj produkcia sú Jetson-only a bežia v Dockeri.

## Aktuálne implementované

- RUN obrazovka s výberom receptu, manuálnym `TRIGGER`, Live preview, pásom View, OK/NOK výsledkom, metrikami a dennými štatistikami.
- Golden Wizard pre Golden snímku, ROI/masku, vision tools, konfiguráciu View, validáciu a publish.
- CU55 MASTER flow s kontinuálnym streamom a routovaním asynchrónnych Pico eventov `CAPTURE IN1` až `CAPTURE IN8`.
- Pico Wizard pre V1/V2 timing profily, mapovanie vstupov a HDF whitelist.
- Manual light v RUN aj Golden Wizard, nezávislé od Live preview.

## Produkčná architektúra kamery

Preferovaný režim je **CU55 MASTER**. Kamera streamuje kontinuálne; hardvérový trigger CU55 nie je primárny produkčný spôsob snímania.

```text
physical external input INx
  → Pico: input detection, light and timing
  → Pico USB: CAPTURE INx
  → PicoService permanent RX reader
  → HDF_Vision resolver selects View
  → frame from continuous CU55 stream
  → vision pipeline
  → OK/NOK
```

Pico režim `TRIGGER`, v ktorom GP16 generuje hardvérové trigger pulzy kamery, zostáva legacy/test režim.

## Raspberry Pi Pico firmware 3.2.2

Zdrojom pravdy pre túto sekciu je firmware `pico_hdf_controller 3.2.2-master-capture-sim-manual-light` dodaný k dokumentácii.

### Hardware

| Pico pin | Funkcia | Polarita |
|---|---|---|
| GP17 | LED / light output | active HIGH |
| GP16 | CU55 camera trigger output | active HIGH |
| GP1..GP8 | external input IN1..IN8 | active LOW, interný pull-up |

### V1 / V2 timing profiles

**V1 a V2 nie sú Recipe View 1 a View 2.** Sú to dva hardvérové timing profily Pico. Recipe View vyberá HDF_Vision podľa fyzického externého vstupu; timing profil vyberá Pico podľa svojho `INPUT_MAP`.

| Jednotný názov | Firmware názov | Význam | Rozsah |
|---|---|---|---:|
| Mode | `MODE` | `MASTER` alebo `TRIGGER` | — |
| Input delay | `DELAY` | oneskorenie od potvrdeného vstupu po začiatok sekvencie | 0..60000 ms |
| Light duration | `PULSE` | minimálny čas svetla ON od jeho zapnutia | 1..60000 ms |
| Capture delay | `CAPTURE` | v MASTER čas od LIGHT ON po `CAPTURE INx` | 0..60000 ms |
| Trigger pulse | `TRIG` | HIGH čas jedného GP16 pulzu v TRIGGER | 1..1000 ms |
| Trigger gap | `GAP` | prestávka medzi GP16 pulzmi v TRIGGER | 1..60000 ms |
| Trigger count | `COUNT` | počet GP16 pulzov v TRIGGER | 1..10 |

`DEBOUNCE_MS` a `LOCKOUT_MS` sú perzistentné konfiguračné hodnoty, ale firmware 3.2.2 pre ne neposkytuje serial `SET` command.

### MASTER timing

```text
external input edge
  → debounce confirmation
  → Input delay (DELAY)
  → LIGHT ON
  → Capture delay (CAPTURE)
  → CAPTURE INx
  → light remains ON for at least Light duration (PULSE)
  → LIGHT OFF
  → lockout
```

Ak `CAPTURE > PULSE`, firmware predĺži reálny svetelný interval po `CAPTURE`; event preto nikdy nenastane po vypnutí svetla. Pri zapnutom Manual light sa timing aj `CAPTURE INx` vykonajú normálne, ale svetlo po cykle zostane ON.

### IN1..IN8 mapping

Každý fyzický vstup má mapovanie `OFF`, `V1` alebo `V2`:

```text
MAP IN1 V1
MAP IN7 V2
MAP IN8 OFF
```

- IN1 spustí V1 timing profile.
- IN7 spustí V2 timing profile.
- OFF nespustí timing cyklus.

Mapovanie nemení identitu vstupu. HDF_Vision dostane a routuje `CAPTURE IN1` alebo `CAPTURE IN7`, nie `CAPTURE V1`/`V2`.

### MASTER vs TRIGGER

Pre produkciu nastavte oba používané profily podľa potreby na MASTER:

```text
SET V1 MODE MASTER
SET V2 MODE MASTER
```

- **MASTER:** CU55 kontinuálne streamuje, Pico riadi svetlo a pri fyzickom alebo simulovanom vstupe odošle `CAPTURE INx`.
- **TRIGGER:** Pico riadi svetlo a generuje `COUNT` hardvérových pulzov na GP16; `TRIG` určuje dĺžku pulzu a `GAP` medzeru. Je to legacy/test režim.

### CAPTURE INx eventy a serial reader

`CAPTURE IN1` až `CAPTURE IN8` sú asynchrónne eventy, nie odpovede na command. `PicoService` používa jeden permanentný serial RX reader, ktorý rozdeľuje eventy a command responses. Nepridávajte druhý thread/UI reader volajúci `readline()` nad tým istým portom.

### Fyzický input, FIRE a SIM

- Fyzický active-LOW edge prejde debounce, použije `INx → V1/V2` mapping a v MASTER odošle príslušné `CAPTURE INx`.
- `FIRE V1` / `FIRE V2` manuálne vykoná profil. V MASTER nemá fyzický `source_input`, preto **neodošle** `CAPTURE INx`.
- `SIM IN1` až `SIM IN8` simulujú fyzický input: použijú rovnaký mapping aj timing a v MASTER odošlú napr. `CAPTURE IN1`. Ak je vstup `OFF`, odpoveď je `ERR INPUT INx NOT_MAPPED`.

Na servisný test routovania používajte `SIM INx`, nie `FIRE V1/V2`.

### Manual light

```text
LIGHT ON
LIGHT OFF
LIGHT STATUS
```

`LIGHT ON` zapne runtime manual override, `LIGHT OFF` ho uvoľní a `LIGHT STATUS` vráti `MANUAL_LIGHT ON` alebo `MANUAL_LIGHT OFF`.

- Manual light je runtime-only; `SAVE` ho neukladá a po reštarte je OFF.
- Nie je súčasťou Recipe View a nie je naviazané na Live preview.
- Pri `MANUAL_LIGHT ON` bežný fyzický/FIRE/SIM cyklus stále prebehne; po cykle ostane svetlo ON.

### Serial command reference

ASCII commandy sú case-insensitive a ukončené novým riadkom. `STATUS` a `INPUTS` sú viacriadkové a končia `END`.

```text
STATUS
SAVE
INPUTS

FIRE V1
FIRE V2
SIM IN1 ... SIM IN8

SET V1 MODE MASTER|TRIGGER
SET V1 DELAY <ms>
SET V1 PULSE <ms>
SET V1 CAPTURE <ms>
SET V1 TRIG <ms>
SET V1 GAP <ms>
SET V1 COUNT <n>
SET V1 SINGLE 1
SET V1 DOUBLE 1

SET V2 MODE MASTER|TRIGGER
SET V2 DELAY <ms>
SET V2 PULSE <ms>
SET V2 CAPTURE <ms>
SET V2 TRIG <ms>
SET V2 GAP <ms>
SET V2 COUNT <n>
SET V2 SINGLE 1
SET V2 DOUBLE 1

MAP ALL OFF
MAP IN1 V1|V2|OFF
...
MAP IN8 V1|V2|OFF

LIGHT ON
LIGHT OFF
LIGHT STATUS
```

Podporované `SET` aliasy sú `CAPTURE_DELAY`; `TRIGGER`, `TRIGGER_PULSE`; `TRIG_GAP`, `TRIGGER_GAP`; `PULSES`, `TRIGGER_COUNT`. `<view>` prijíma aj `VIEW1`/`1` a `VIEW2`/`2`. `SINGLE`/`DOUBLE` vyžadujú štvrtý token, ktorého hodnota sa nevyhodnocuje. `SET` a `MAP` menia RAM konfiguráciu; `SAVE` uloží timing, mode, input mapping, debounce a lockout do `hdf_pico_config.json`, nie Manual light.

### STATUS príklad

Presná syntax a default hodnoty firmware 3.2.2:

```text
FIRMWARE pico_hdf_controller 3.2.2-master-capture-sim-manual-light
PINS LED=GP17 TRIG=GP16
V1_MODE MASTER
V1_DELAY 0
V1_PULSE 200
V1_CAPTURE 5
V1_TRIG 10
V1_GAP 300
V1_COUNT 2
V2_MODE MASTER
V2_DELAY 0
V2_PULSE 200
V2_CAPTURE 5
V2_TRIG 10
V2_GAP 300
V2_COUNT 2
INPUT_MAP IN1=V1 IN2=OFF IN3=OFF IN4=OFF IN5=OFF IN6=OFF IN7=V2 IN8=OFF
DEBOUNCE_MS 30
LOCKOUT_MS 100
MANUAL_LIGHT OFF
NOTE MASTER physical input emits CAPTURE INx
NOTE SIM IN1..IN8 simulates a physical input
NOTE COUNT=2 means trigger #1 dummy, trigger #2 capture
END
```

## Pico Wizard

Otvorte **SETUP → Sprievodca Raspberry Pi Pico**. Wizard je UI zdroj pravdy pre hardvérovú Pico konfiguráciu a HDF whitelist:

- stav pripojenia, port, firmware, read-only `STATUS` a živý stav IN1..IN8,
- V1/V2 `MODE`, Input delay (`DELAY`), Light duration (`PULSE`) a Capture delay (`CAPTURE`),
- `IN1..IN8 → OFF/V1/V2` mapping,
- checkbox **Povolený** pre HDF input whitelist.

Pri **Uložiť** odošle hodnoty do Pico, vykoná `SAVE` a lokálne uloží whitelist do `/data/pico_config.json`. Wizard v aktuálnom UI nemá ovládanie ani samostatný informatívny indikátor Manual light; celý raw `STATUS` však môže obsahovať `MANUAL_LIGHT`.

### Pico mapping vs HDF input whitelist

- Pico mapping `IN7 → V2` určuje, čo fyzicky vykoná Pico.
- HDF checkbox `IN7 [Povolený]` určuje, či RUN smie spracovať prijatý `CAPTURE IN7`.

Pre produkčný event sú potrebné obe podmienky: input musí byť namapovaný na profil v MASTER a povolený v HDF. Whitelist nemení Pico firmware mapping.

## Golden Wizard a View routing

Recipe View neurčuje V1/V2 timing profile. V **View config** možno zvoliť napr.:

```text
Spôsob snímania: Externý signál
Externý režim: Explicitný
Externý zdroj: Pico USB
Externý vstup: IN7
```

Výsledok je `CAPTURE IN7 → View s Pico + IN7`. Dialog zobrazuje `Pico IN7 → V2` a hodnoty Delay/Pulse/Capture iba ako read-only snapshot hardvérovej konfigurácie.

### Explicitný režim

```text
View A → Pico IN1
View B → Pico IN7

CAPTURE IN1 → View A
CAPTURE IN7 → View B
```

Duplicitná explicitná kombinácia zdroj + input (napr. `Pico + IN7`) pre dva View nie je povolená. RecipeService ju pri uložení odmietne; resolver z bezpečnostných dôvodov ignoruje aj prípadné duplicitné staré dáta.

### Sekvenčný režim

Pri `Sekvenčný` vyberá HDF_Vision View podľa poradia sekvencie osobitne pre daný externý zdroj. Input nevyberá konkrétny View. Pico nemá sekvenčný View state machine: stále použije fyzické `INx → V1/V2` mapovanie a jeho timing.

### Golden workflow

1. vytvoriť alebo zvoliť View a Golden snímku,
2. nastaviť ROI/masku,
3. pridať a nastaviť vision tools,
4. upraviť View config a routing,
5. validovať konfiguráciu a výsledky,
6. publish receptu.

Golden Wizard má vedľa Live samostatné tlačidlo Manual light. Live ON/OFF nemení Manual light a Manual light ON/OFF nemení Live preview.

## RUN UI

- **Recept:** výber aktívneho receptu; View strip zobrazuje a umožňuje aktivovať jeho Views.
- **TRIGGER:** samostatná manuálna akcia, ktorá spracuje aktuálny/požadovaný View.
- **Live preview:** v MASTER zapína alebo zastavuje pravidelnú obnovu obrazu zo streamu.
- **Výsledok:** vision pipeline zobrazí OK/NOK, metriky a posledný záber; sidebar obsahuje celkové/OK/NOK/yield a časové štatistiky.
- **Manual light:** samostatné tlačidlo `Svetlo zapnuté/vypnuté`, ktoré používa Pico `LIGHT ON/OFF`.

`Live ON/OFF` nikdy implicitne nemení `Light ON/OFF` a opačne. Stav svetla sa číta z Pico cez `STATUS`; pri staršom firmware sa zobrazí neznámy stav alebo chyba commandu.

## Docker / Jetson

Na Jetson hoste z koreňa repozitára:

```bash
bash docker/build.sh
bash docker/run.sh
```

`docker/build.sh` používa Buildx, default image `hdf_vision:dev` a platformu `linux/arm64`. `docker/run.sh` inicializuje Jetson GPIO, nastaví X11 (`DISPLAY`, `/tmp/.X11-unix`, `QT_QPA_PLATFORM=xcb`), používa NVIDIA runtime a privileged USB/device access. Mountuje `/data:/data`, `/dev/bus/usb`, zvolené `CAM_DEV` (default `/dev/video0`) a povoľuje video/hidraw device cgroups. Kamera je dostupná cez `/dev/video*`; Pico USB CDC musí byť hostiteľovi dostupné ako `/dev/ttyACM*` a do kontajnera sa prenáša cez privileged USB access.

Voliteľne:

```bash
CAM_DEV=/dev/video1 bash docker/run.sh
bash docker/run.sh configure-gpio
```

Pred použitím CU55 možno nainštalovať existujúce udev pravidlá:

```bash
sudo cp docker/99-hdf-uvc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## Raspberry Pi Pico firmware update

Repozitárový firmware je v `firmware/pico/main.py`. Nahrajte požadovanú verziu na Pico pod presným názvom `main.py`, Pico reštartujte a cez serial odošlite:

```text
STATUS
```

Pred produkciou musí prvý riadok potvrdiť `FIRMWARE pico_hdf_controller 3.2.2-master-capture-sim-manual-light`.

## Troubleshooting

### INx sa ukáže ACTIVE, ale RUN neurobí snímku

Skontrolujte `INPUT_MAP`, `V1/V2 MODE MASTER`, HDF checkbox **Povolený** a View `Externý zdroj`/`Externý vstup`.

### STATUS ukazuje IN7=OFF

```text
MAP IN7 V1
```

alebo `MAP IN7 V2`, potom `SAVE`.

### CAPTURE INx neprichádza

Overte mapping, MASTER mode, `DEBOUNCE_MS`, active-LOW fyzický vstup a správanie pomocou `SIM INx`. `FIRE V1/V2` nie je test `CAPTURE INx`.

### LIGHT ON vracia ERR UNKNOWN

Pico pravdepodobne používa firmware starší než 3.2.2. Overte prvý riadok `STATUS` a aktualizujte `main.py`.

### Svetlo zostalo zapnuté

Overte `LIGHT STATUS`; ak vráti `MANUAL_LIGHT ON`, odošlite `LIGHT OFF`.

## Plánované / future

AI/TensorRT, PLC/I/O rozšírenia, multi-camera a ďalšie integrácie nie sú v tejto dokumentácii prezentované ako aktuálny produkčný flow.

## Známe nesúlady v repozitári

- `firmware/pico/main.py` v aktuálnom checkout-e sa identifikuje ako 3.2.0 a nepodporuje `SIM` ani `LIGHT`; pred použitím Manual light/SIM musí byť nahradený dodaným firmware 3.2.2.
- Recipe model a niektoré legacy metódy stále obsahujú `flash_delay_ms`/`flash_pulse_ms` a cestu na publish timingov do Pico. Pico Wizard je však aktuálne určené miesto pre V1/V2 hardware timing; View config ich iba informatívne zobrazuje pre zvolený INx.
