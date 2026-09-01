# Raspberry Pi Pico firmware

Tento adresár obsahuje produkčný MicroPython firmware HDF_Vision. Súbor `main.py` sa kopíruje priamo na Pico ako `main.py`; firmware sa identifikuje ako `pico_hdf_controller 3.2.0-master-capture`.

## Úloha Raspberry Pi Pico

Pico je I/O a timing controller medzi výrobnou linkou a HDF_Vision. Prijíma galvanicky oddelené IN1–IN8, ovláda svetlo, podporuje legacy hardvérový trigger See3CAM_CU55M a komunikuje s Jetsonom cez USB CDC serial (typicky `/dev/ttyACM*`). V MASTER architektúre hlási, z ktorého fyzického vstupu prišiel capture request.

Pico **nerozhoduje**, ktorý Recipe View patrí vstupu: pozná iba `IN1`…`IN8`. Mapovanie `Pico INx → Recipe View` patrí do HDF_Vision.

| Pico pin | Funkcia | Polarita |
|---|---|---|
| GP17 | svetlo/LED output | active HIGH |
| GP16 | CU55M hardware trigger output | active HIGH |
| GP1…GP8 | IN1…IN8 z galvanického oddelenia | active LOW, pull-up |

## MASTER režim – cieľová architektúra

```text
výrobná linka
    ↓
galvanické oddelenie
    ↓
Pico INx
    ↓
Pico riadi svetlo/timing
    ↓
USB event do Jetsonu
    ↓
HDF_Vision
    ↓
mapovanie INx → View
    ↓
frame z kontinuálneho CU55M MASTER streamu
    ↓
vision kontrola
```

CU55M kontinuálne streamuje v MASTER režime. Pico nespúšťa senzor hardvérovým triggerom; externý signál iba oznamuje aplikácii čas capture/inspection.

## Pico USB capture event

```text
CAPTURE IN1
CAPTURE IN2
...
CAPTURE IN8
```

Nepoužíva sa `CAPTURE V1`/`CAPTURE V2`, pretože View určuje recept. Event nemá ID ani poradové číslo. Vzniká iba z fyzického INx v MASTER režime; manuálne `FIRE` nemá fyzický zdroj, a preto event nevysiela. Po evente nasleduje výsledok cyklu `OK FIRED ...`.

## Timing svetla

Hodnoty sú v milisekundách a existujú osobitne pre legacy profily V1/V2:

- `DELAY` – čas po externom vstupe pred sekvenciou (0–60000).
- `PULSE` – doba zapnutia svetla (1–60000).
- `CAPTURE` / capture delay – čas LIGHT ON → `CAPTURE INx` (0–60000); alias `CAPTURE_DELAY`.
- `TRIG` – HIGH čas GP16 impulzu (1–1000).
- `GAP` – odstup medzi HW trigger impulzmi (1–60000).
- `COUNT` – počet HW impulzov (1–10, default 2).
- `DEBOUNCE_MS` (default 30) a `LOCKOUT_MS` (default 100) sú uložené v konfigurácii, ale nemajú sériový `SET` príkaz.

```text
external INx
    ↓
DELAY
    ↓
LIGHT ON
    ↓
CAPTURE delay
    ↓
CAPTURE INx cez USB
    ↓
LIGHT zostáva ON do skončenia PULSE
    ↓
LIGHT OFF
```

Ak `CAPTURE > PULSE`, aktívna sekvencia sa predĺži, aby event nevznikol po LIGHT OFF.

## Legacy TRIGGER režim

TRIGGER zapne svetlo a na GP16 vytvorí `COUNT` impulzov dĺžky `TRIG`, oddelených `GAP`. Svetlo zostane ON najmenej `PULSE`; sekvencia sa podľa potreby predĺži na dokončenie impulzov. Default `COUNT=2` zachováva „trigger #1 dummy, trigger #2 capture“. Režim ostáva pre spätnú kompatibilitu a experimenty. Preferovaný produkčný smer je **CU55M MASTER + kontinuálny stream + Pico USB capture event**.

## Serial protokol

ASCII príkazy sú ukončené novým riadkom a nie sú case-sensitive. `STATUS` a `INPUTS` sú viacriadkové a končia `END`. Neznámy príkaz vráti `ERR UNKNOWN`.

| Príkaz | Účel | Príklad | Typická odpoveď |
|---|---|---|---|
| `STATUS` | Firmware, piny, profily, mapovanie a globálne timingy. | `STATUS` | `FIRMWARE pico_hdf_controller 3.2.0-master-capture` … `END` |
| `SAVE` | Uloží aktuálnu konfiguráciu. | `SAVE` | `OK SAVED` |
| `INPUTS` | Active-low stav IN1–IN8. | `INPUTS` | `INPUTS IN1=ACTIVE IN2=OFF ...`, `END` |
| `FIRE <view>` | Manuálne spustí V1/V2 bez capture eventu. | `FIRE V1` | `OK FIRED V1 ... SOURCE=USB ...` alebo `BUSY V1` |
| `SET <view> MODE <mode>` | `MASTER` alebo `TRIGGER`. | `SET V1 MODE MASTER` | `OK SET V1 MODE MASTER` |
| `SET <view> DELAY <ms>` | Vstupné oneskorenie. | `SET V1 DELAY 20` | `OK SET V1 DELAY 20` |
| `SET <view> PULSE <ms>` | Dĺžka svetla. | `SET V1 PULSE 200` | `OK SET V1 PULSE 200` |
| `SET <view> CAPTURE <ms>` | MASTER capture delay. | `SET V1 CAPTURE 5` | `OK SET V1 CAPTURE 5` |
| `SET <view> TRIG <ms>` | Dĺžka HW impulzu. | `SET V1 TRIG 10` | `OK SET V1 TRIG 10` |
| `SET <view> GAP <ms>` | Medzera medzi impulzmi. | `SET V1 GAP 300` | `OK SET V1 GAP 300` |
| `SET <view> COUNT <n>` | Počet impulzov. | `SET V1 COUNT 2` | `OK SET V1 COUNT 2` |
| `SET <view> SINGLE <value>` | Skratka COUNT=1; štvrtý token je povinný, hodnota sa nevyhodnocuje. | `SET V1 SINGLE 1` | `OK SET V1 COUNT 1` |
| `SET <view> DOUBLE <value>` | Skratka COUNT=2; štvrtý token je povinný. | `SET V1 DOUBLE 1` | `OK SET V1 COUNT 2` |
| `MAP ALL OFF` | Zakáže všetky vstupy. | `MAP ALL OFF` | `OK MAP ALL OFF` |
| `MAP INx V1\|V2\|OFF` | Legacy priradenie INx k profilu. | `MAP IN3 V2` | `OK MAP IN3 V2` |

`<view>` prijíma V1/VIEW1/1 a V2/VIEW2/2. SET aliasy presne podporované implementáciou: `CAPTURE_DELAY`; `TRIGGER`, `TRIGGER_PULSE`; `TRIG_GAP`, `TRIGGER_GAP`; `PULSES`, `TRIGGER_COUNT`. Odpoveď používa kanonický názov. Čísla sa ohraničia rozsahom; nečíselná hodnota sa zmení na default. Chybné hodnoty vracajú `ERR SET`, `ERR VIEW`, `ERR MODE` alebo `ERR MAP`.

## Persistence

Konfigurácia sa načíta z `hdf_pico_config.json`. Chýbajúci/neplatný JSON nahradia defaults. Podporovaný je flat v3.x aj import staršieho v2 `views`/`input_map`. `SET` a `MAP` menia RAM; `SAVE` uloží aktuálnu konfiguráciu na Pico.

## Konfigurácia vstupov

IN1–IN8 sú active-low GP1–GP8 a predvolene `OFF`. Legacy `INx → V1/V2` vyberá timing profil a ostáva kompatibilné. Nový smer je:

```text
Pico → CAPTURE INx
HDF_Vision recipe → rozhodne View
```

## HDF_Vision integrácia

`app/services/pico_service.py` je aktuálna Jetson-side služba pre `/dev/ttyACM*`, pripojenie, command TX a synchrónnu odpoveď. Táto zmena službu ani async eventy neimplementuje.

```text
PicoService
├── USB CDC serial connection
├── command TX
├── command-response handling
├── permanent RX reader
└── async events
      └── CAPTURE IN1..IN8
```

**Nepoužívať dva nezávislé thready, ktoré oba volajú `readline()` na tom istom porte.** Budúca implementácia musí mať jeden permanentný RX reader, ktorý rozdelí riadky na command responses a async `CAPTURE` eventy.

## Pico vs Modbus

Pico a Modbus sa nevylučujú:

```text
VSTUP DO HDF_VISION:
výrobná linka → Pico → USB → HDF_Vision

VÝSTUP Z HDF_VISION:
HDF_Vision → Modbus TCP → I/O modul → výrobná linka
```

Modbus zostáva pre OK output, NOK output, heartbeat a prípadne externé DI. Pico možno používať ako samostatný externý trigger source.

## Plánovaná integrácia

V SETUP je plánovaný **Sprievodca Raspberry Pi Pico**, ktorý umožní:

- zobraziť stav pripojenia a `/dev/ttyACM*`,
- zobraziť firmware version,
- sledovať IN1–IN8,
- povoliť/zakázať vstupy použiteľné ako produkčný trigger,
- nastavovať potrebné timing hodnoty.

Wizard, async listener ani mapovanie INx na View zatiaľ nie sú implementované.
