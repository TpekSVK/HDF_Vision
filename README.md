# HDF_Vision

HDF_Vision je QC vision aplikácia pre **NVIDIA Jetson Orin Nano**. Produkčný stack je JetPack, Docker, Python, PySide6, OpenCV, GStreamer/V4L2, kamera See3CAM_CU55M, Raspberry Pi Pico a SQLite. Windows sa používa iba na vývoj a editáciu; runtime, testovanie s hardvérom aj produkcia sú Jetson-only a bežia v Dockeri.

Príkazy na spustenie, nastavenie a servis nájdete v [prehľade príkazov](docs/PRIKAZY_SK.md).

## Aktuálne implementované

- Zostava dvoch kamier s jedným Picom: kamera pri pohľade, samostatné/AND výstupné skupiny, súbežný MASTER/TRIGGER a filtrované výsledky. [Spustenie a vytvorenie receptu](docs/DUAL_CAMERA_SK.md).

- Nezávislé kamerové stanice s vlastným Pico, receptmi a výsledkami; explicitné USB párovanie vrátane overenia snímkou. [Ovládanie a stav overenia](docs/architecture/MULTI_CAMERA_SK.md).

- RUN obrazovka s výberom receptu, manuálnym `TRIGGER`, Live preview, pásom View, OK/NOK výsledkom, metrikami a dennými štatistikami.
- Golden Wizard pre Golden snímku, ROI/masku, vision tools, konfiguráciu View, validáciu a publish.
- CU55 MASTER flow s kontinuálnym streamom a routovaním asynchrónnych Pico eventov `CAPTURE IN1` až `CAPTURE IN8` aj softvérových `CAPTURE V1/V2`.
- Pico Wizard pre V1/V2 timing profily, mapovanie vstupov a HDF whitelist.
- Manual light v RUN aj Golden Wizard, nezávislé od Live preview.

## Troubleshooting

Tab **TROUBLESHOOTING → Overenie polohy kamery** porovnáva RAW golden aktívneho
View s novou RAW snímkou bez produkčnej alignment kompenzácie. Používa uložené
tolerancie receptu, zobrazuje fyzické korekcie a podporuje ručné/automatické
snímkovanie aj automatické prepínanie Golden/Current každých 400 ms.
Dotykové rozloženie má veľké snímky, merania pod obrazom a tolerancie aj pokyny
v prekrytí **Podrobnosti**. Tlačidlo Späť sa nezobrazuje; na odchod slúži horná
navigácia. Toggle **Automatické snímanie** sprístupní výber **Rozostup**
(0,5 / 1 / 2 / 5 s), ktorý možno meniť aj počas snímania. Odstup sa počíta od
ukončenia predchádzajúcej kontroly; požiadavky sa neprekrývajú.
Diagnostika pozastaví RUN danej stanice
a nemení recept, produkčné výsledky ani Modbus OK/NOK.
[Implementácia, obmedzenia tolerancií a Jetson testy](docs/CAMERA_POSITION_VERIFICATION_SK.md).

## Výsledky kontrol

Stránka **VÝSLEDKY** má dotykové karty histórie vľavo a veľkú snímku vpravo.
Počet kariet na strane sa prispôsobí dostupnej výške (najviac 8), bez posuvného
zoznamu. Prepínač **Všetky / NOK / OK** filtruje okamžite. **Filtre** otvoria prekrytie
s receptom, pohľadom a dátumami **Od / Do**: dátum možno napísať alebo zvoliť deň
v slovenskom kalendári; koncový deň je zahrnutý. **Použiť filtre** potvrdí výber,
**Zrušiť**, Zavrieť alebo kliknutie mimo obnoví pôvodné hodnoty.

Prepínač kamery prehliada históriu jednej stanice alebo **Všetkých kamier**;
záznamy obsahujú názov kamery a pri spoločnom zozname sa radia podľa času.
ROI a Chyby sú toggle tlačidlá. Snímka podporuje priblíženie a posun dvoma prstami,
myšou aj tlačidlami. Pod snímkou je výsledok, zaokrúhlený čas a nástroje po trojiciach.
**Podrobnosti** alebo tlačidlo konkrétneho nástroja otvorí metriky a uložené prahy
v prekrytí. Akcie kandidátov False OK/NOK nie sú v operátorskej histórii zobrazené.
**Export CSV** exportuje všetky záznamy zodpovedajúce potvrdeným filtrom a zvoleným
kamerám do nového súboru vrátane názvu kamery. Čítanie SQLite, obrázkov a export
bežia na pozadí; otvorenie histórie nemení recepty ani riadenie kamier.

Nové záznamy obsahujú historickú geometriu a prahy; pri starších záznamoch sa
chýbajúca geometria nedopĺňa z aktuálneho receptu. Presná lokalizácia chyby sa
zobrazuje len ak ju nástroj uložil. Pri NOK bez lokalizovanej chyby (napríklad
nevyhovujúci SSIM alebo nepripravený model) **Chyby** červeno zvýraznia uloženú
kontrolovanú ROI a uvedú dôvod NOK; ide o oblasť kontroly, nie presnú polohu defektu.
Pri SSIM sa použije historická hodnota a uložený prah. Ak plná snímka chýba, použije sa uložený JPEG;
po odstránení oboch obrázkov zostanú dostupné metriky. Pravidlá uchovávania a
zapnutie/vypnutie ukladania kontrol zostávajú platné. Otvorenie stránky Výsledky z RUN ponechá kontroly a externé vstupy aktívne;
návrat do RUN nepripravuje kameru znovu. SETUP po potvrdení operátorom kontroly
pozastaví. Výsledky otvorené zo SETUP ich samy neobnovia.

## Produkčná architektúra kamery

Aplikácia podporuje **MASTER** s kontinuálnym streamom aj nový **Pico PIO TRIGGER** s dvojicou PRIME + produkcia. Firmvér a protokol sú v [firmware/pico](firmware/pico/README.md).

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

Nový TRIGGER používa GP16/GP17 a hardvérové PIO časovanie. Vstup vyšle `REQUEST INx`; aplikácia rezervuje dvojicu a odošle PIO príkaz. Nepoužíva Jetson GPIO. Neúplná dvojica sa odmietne.

## Starší Raspberry Pi Pico firmware 3.3

Nasledujúca sekcia opisuje staršiu kompatibilitu 3.3, nie nové SESSION/PIO riadenie 4.0. Zdrojom pre túto sekciu je `firmware/pico/main_v3.3.py` (`pico_hdf_controller 3.3-master-production-capture`).

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

`DEBOUNCE_MS` a `LOCKOUT_MS` sú perzistentné konfiguračné hodnoty, ale firmware 3.3 pre ne neposkytuje serial `SET` command.

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

Mapovanie nemení identitu fyzického vstupu. HDF_Vision dostane a routuje `CAPTURE IN1` alebo `CAPTURE IN7`; softvérová sekvencia cez `FIRE V1/V2` používa samostatný event `CAPTURE V1/V2`.

### MASTER vs TRIGGER

Pre produkciu nastavte oba používané profily podľa potreby na MASTER:

```text
SET V1 MODE MASTER
SET V2 MODE MASTER
```

- **MASTER:** CU55 kontinuálne streamuje, Pico riadi svetlo a pri fyzickom alebo explicitnom softvérovom vstupe odošle `CAPTURE INx`; pri softvérovej sekvencii odošle `CAPTURE V1/V2`.
- **TRIGGER:** Pico riadi svetlo a generuje `COUNT` hardvérových pulzov na GP16; `TRIG` určuje dĺžku pulzu a `GAP` medzeru. Je to legacy/test režim.

### CAPTURE INx eventy a serial reader

`CAPTURE IN1` až `CAPTURE IN8` sú asynchrónne eventy, nie odpovede na command. `PicoService` používa jeden permanentný serial RX reader, ktorý rozdeľuje eventy a command responses. Nepridávajte druhý thread/UI reader volajúci `readline()` nad tým istým portom.

### Fyzický input, FIRE a softvérový trigger

- Fyzický active-LOW edge prejde debounce, použije `INx → V1/V2` mapping a v MASTER odošle príslušné `CAPTURE INx`.
- `FIRE V1` / `FIRE V2` spustí produkčné časovanie sekvenčného profilu a v MASTER odošle `CAPTURE V1` / `CAPTURE V2`.
- `TRIGGER IN1` až `TRIGGER IN8` použijú rovnaký mapping aj timing ako fyzický explicitný vstup a v MASTER odošlú napr. `CAPTURE IN1`. Ak je vstup `OFF`, odpoveď je `ERR INPUT INx NOT_MAPPED`.

`SIM INx` zostáva iba kompatibilný alias `TRIGGER INx`.

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
TRIGGER IN1 ... TRIGGER IN8

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

Presná syntax a default hodnoty firmware 3.3:

```text
FIRMWARE pico_hdf_controller 3.3-master-production-capture
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
NOTE MASTER FIRE V1/V2 emits CAPTURE V1/V2
NOTE TRIGGER IN1..IN8 follows physical input mapping
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

### Dotykové ovládanie Golden Wizardu

Wizard využíva celú obrazovku. Horný riadok obsahuje výber pohľadu, **+ Pohľad**,
**Upraviť pohľad**, prepínače **Live** a **Svetlo** a tlačidlo **Ďalšie**.
Aktívne prepínače sú farebne zvýraznené. Ďalšie možnosti a získanie GOLDEN
sa otvárajú ako prekrytie nad obrazom bez posúvania pracovnej plochy.

Pravý panel prepína medzi **Nástroje** a **Parametre**. Na jednej stránke je
najviac päť nástrojov; ďalšie nástroje aj dlhšie formuláre sa stránkujú šípkami.
Jeden klik nástroj iba označí. Parametre sa otvoria dvojklikom na nástroj alebo
tlačidlom **Parametre**.
Výsledok testu zobrazuje stav, čas a metriky spolu na jednej stránke. Náhľady
a podrobné časovanie sa otvárajú v prekrytí nad obrazom.
Ak test zastaví predchádzajúci locator podľa politiky receptu, vybraný analyzátor
sa označí **NEOTESTOVANÉ** s dôvodom zlyhania locatora. Nejde o NOK meranie SSIM
ani iného vybraného analyzátora. Najprv skontrolujte šablónu, oblasť hľadania
a prípadnú referenčnú hranu A–B a otestujte samotný locator.
Na karte Nástroje sú priamo dostupné kopírovanie,
odstránenie s potvrdením, zapnutie/vypnutie a zmena poradia. Pôvodné pravidlo,
že locator musí predchádzať analyzátorom, zostáva zachované.

- **Jeden prst:** výber a úprava geometrie; po voľbe kresliaceho nástroja kreslenie.
- **Dva prsty:** roztiahnutím/stiahnutím priblíženie a pohybom oboch prstov posun snímky.
  Gesto nemení rotáciu ani uloženú ROI, masku či hranu. Po zdvihnutí jedného prsta
  sa kreslenie obnoví až po ukončení celého gesta a novom dotyku.
- **Celý obraz / − / +:** zostávajú dostupné aj bez gest; funguje aj pôvodné ovládanie myšou.
- **Upravovaná oblasť:** podľa nástroja ROI, hľadanie/šablóna, Ignore mask alebo hrana A–B.
  Referenčná hrana locatora je dostupná v príslušnom režime zarovnania.
  **Hľadanie** a **Šablóna** sa vyberajú výhradne tlačidlami; kliknutie alebo
  dotyk obrazu aktívnu oblasť nemení ani pri minutí úchopu.
- **Zarovnanie locatora:** bežné režimy majú jednu stránku. Pri referenčnej hrane A–B
  sú nastavenia zoskupené do troch stránok: režim a otočenie, hľadanie hrany a presnosť hrany.
  **Prijatie výsledku** zobrazuje minimálnu zhodu a maximálne posuny X/Y spolu na jednej stránke.
- **ROI:** osem úchopov obdĺžnika a odsadený úchop rotácie majú väčšiu dotykovú plochu.
  Rozmery sú zobrazené pod snímkou.
- **Polygón ROI aj masky:** dotyky pridávajú vrcholy; **Dokončiť polygón** je dostupné
  od troch bodov. **Späť o bod** opraví posledný bod a **Zrušiť** zruší rozkreslený tvar.
  Dvojklik myšou alebo Enter zostávajú funkčné.
- **Ignore mask:** štetec, guma, obdĺžnik, kruh a polygón; veľkosť štetca,
  priehľadnosť a zobrazenie masky sú pod **Maska ▴**.

Učené nástroje zachovávajú zber OK/NOK vzoriek, model a validáciu. Kontrola prázdnej
formy V2 má samostatný vstup **V2: Kavity, vzorky a model**.
Karta **Učenie** zobrazuje stav, počty vzoriek, zber a prepočet modelu na jednej
stránke. Odhad modelu a reset učenia sú v prekrytí **Podrobnosti modelu ▴**.
Pri kontrole prázdnej formy má **Citlivosť** dve stránky: základné nastavenia
detekcie a limity zvyškov; stručný výsledok posledného testu zostáva na oboch.
**Pokročilé** má dve stránky: zber a validácia vzoriek, potom profil modelu.
Karta **Validácia** má jednu stránku s testovaním vzoriek, návrhom nastavení,
stručným výsledkom a použitím odporúčania. Upozornenia na chýbajúce NOK vzorky,
malý dataset a prekrývanie zostávajú priamo v prehľade. Úplné reporty sú
v prekrytí **Podrobnosti validácie ▴**, kde sa prepína overenie vzoriek a návrh nastavení.

## RUN UI

- **Recept:** výber aktívneho receptu; View strip zobrazuje a umožňuje aktivovať jeho Views.
- **TRIGGER:** samostatná manuálna akcia, ktorá spracuje aktuálny/požadovaný View.
- **Live preview:** v MASTER zapína alebo zastavuje pravidelnú obnovu obrazu zo streamu.
- **Výsledok:** vision pipeline zobrazí OK/NOK, metriky a posledný záber; sidebar obsahuje celkové/OK/NOK/yield a časové štatistiky.
- **Manual light:** samostatné tlačidlo `Svetlo zapnuté/vypnuté`, ktoré používa Pico `LIGHT ON/OFF`.

`Live ON/OFF` nikdy implicitne nemení `Light ON/OFF` a opačne. Stav svetla sa číta z Pico cez `STATUS`; pri staršom firmware sa zobrazí neznámy stav alebo chyba commandu.

## Nastavenia a napájanie

**NASTAVENIA** zobrazujú kompaktné karty Recept, Kamera, Komunikácia a Systém
bez posúvania. Golden Setup sa otvára samostatne. Režim snímania aj tlačidlá
majú dotykovú veľkosť; servisný overlay sa zapína prepínacím tlačidlom.

V karte **Systém → Napájanie** sú tri akcie:

- **Zavrieť aplikáciu · pracovná plocha** bezpečne ukončí všetky kamerové
  stanice a aplikáciu; počítač zostane zapnutý.
- **Reštartovať počítač** ukončí stanice a po skončení aplikácie reštartuje hosta.
- **Vypnúť počítač** ukončí stanice a po skončení aplikácie vypne hosta.

Pred ukončením sa dokončí prijatá kontrola a uvoľnia zariadenia všetkých staníc.
Každá akcia vyžaduje záverečné potvrdenie. Ak je nastavené spoločné heslo ochrany
receptov, najprv sa heslom odomkne ponuka Napájanie. Odomknutie platí iba pre
aktuálne otvorenú ponuku; nemení oprávnenia na zápis receptov. Bez nastaveného
hesla je ponuka prístupná priamo. Heslo sa zadáva systémovou klávesnicou GNOME, ktorá sa vyvolá dotykom
textového poľa. Aplikácia nepoužíva druhú vlastnú klávesnicu.

Automatické vyvolávanie systémovej klávesnice platí aj pre ostatné upraviteľné
textové polia aplikácie. `docker/run.sh` z grafického terminálu načíta adresu
hostiteľského IBus a identitu používateľa pracovnej plochy. Pomocný proces sa
pripája ako tento používateľ; dostáva iba informáciu o zameraní poľa
a polohe kurzora, nikdy text hesla. Nenastavuje nové heslo ani nemení nastavenia
klávesnice GNOME. Pri ručnom spustení mimo `run.sh` treba odovzdať `IBUS_ADDRESS`,
`HDF_DESKTOP_UID` a `HDF_DESKTOP_GID`; samotné `QT_IM_MODULE=ibus` nestačí pre
aplikáciu bežiacu v kontajneri ako root. Použitá grafická relácia je GNOME/X11.

Heslo sa spravuje výhradne cez terminál, pozri [Recipe security](#recipe-security).
Ak už je heslo na ochranu receptov nastavené, automaticky platí aj pre Napájanie;
netreba nastavovať druhé heslo.

## Tichá pracovná plocha Jetsonu

Jednorazovo spustite **v termináli pracovnej plochy Jetsonu, bez sudo a mimo Dockeru**:

```bash
bash tools/setup-desktop.sh
```

Skript zapne režim **Nerušiť**, vypne oznámenia aktualizácií Ubuntu a GNOME
Software a zakáže automatické spustenie Ubuntu Update Notifier pre aktuálneho
používateľa. Nerušiť potlačí aj bežné oznámenia ostatných aplikácií; nejde o blokovanie
všetkých systémových dialógov. Samotné sťahovanie/inštalovanie aktualizácií,
APT služby a ručné otvorenie správcu aktualizácií zostávajú nezmenené.

Nerušiť sa zapne ihneď. **Po spustení sa odhláste a znova prihláste alebo
reštartujte**, aby prestal bežať aj pôvodný oznamovač. Už otvorené okná skript
nezatvára. Nastavenia pretrvajú reštart a skript možno spustiť opakovane;
prvú zálohu neprepíše. Iných používateľov neovplyvní.

Obnova pôvodného stavu (potom sa opäť odhláste/prihláste):

```bash
bash tools/setup-desktop.sh --restore
```

Záloha je v `${XDG_STATE_HOME:-~/.local/state}/hdf-vision/desktop/original`.
Obnova vráti iba zmenené nastavenia a pôvodný súbor automatického spúšťania;
úspešne obnovenú zálohu odstráni. Následné nové nastavenie uloží novú zálohu.
Tento skript upravuje oznámenia. Spúšťanie s logom nastavuje samostatný postup nižšie.

## Spúšťanie s logom a bez terminálu

Samostatný návod vrátane nastavenia ďalšieho Jetsonu, výpisov a obnovy:
[setup_launcher_SK.md](docs/setup_launcher_SK.md).

Na pracovnej ploche ako bežný používateľ spustite:

```bash
python3 tools/setup-launcher.py
```

Nastaví ikonu na ploche aj automatické spustenie po prihlásení. Hneď po spustení
prekryje plochu pôvodným logom na pozadí aplikácie (`#14181d`), bez terminálu
a bez pôvodného umelého8s čakania. Po zobrazení rozhrania a dokončení prípravy
kamier sa úvodná obrazovka počas0,5s plynulo skryje. Nezasahuje do firmvérového
ani prihlasovacieho zobrazenia operačného systému. Prechod po štarte grafickej
relácie závisí aj od GNOME; celý reštart hostiteľa sa pri skúške nevykonával.

Výpisy sú v `${XDG_STATE_HOME:-~/.local/state}/hdf-vision/logs/application.log`
s rotáciou5MB a dvoma archívmi. Chyba spustenia sa zobrazí na úvodnej obrazovke;
pri dlhom načítavaní možno aplikáciu odkryť. Opätovné kliknutie na ikonu počas
bežiacej aplikácie nevytvorí druhú inštanciu spustenú týmto launcherom.
Ručné spúšťanie cez `bash docker/run.sh` zostáva dostupné pre servis.

Náhľad loga bez kamier: `python3 tools/launch-app.py --preview`.
Obnova pôvodných súborov ikony a automatického spúšťania:

```bash
python3 tools/setup-launcher.py --restore
```

Prvá záloha pôvodných launcherov sa uchováva v
`~/.local/state/hdf-vision/startup-launchers/original`; opakované nastavenie ju
neprepíše. Potrebné hostiteľské GTK3/PyGObject sú na GNOME pracovnej ploche
Jetsonu dostupné; kvôli tejto úprave netreba znovu zostavovať Docker obraz.

## Docker / Jetson

Na Jetson hoste z koreňa repozitára:

```bash
bash docker/build.sh
bash docker/run.sh
```

`docker/build.sh` používa Buildx, default image `hdf_vision:dev` a platformu `linux/arm64`. `docker/run.sh` nastaví X11 (`DISPLAY`, `/tmp/.X11-unix`, `QT_QPA_PLATFORM=xcb`), používa NVIDIA runtime a privileged USB/device access. Mountuje `/data:/data`, `/dev:/dev` a `/sys:/sys:ro`, aby boli dostupné kamery, HID a Pico aj po USB reconnecte. Zariadenia sa explicitne priraďujú v aplikácii podľa USB serial; `videoN` a `ttyACMN` nie sú trvalé identity. Bez priradenia sa snímanie nespustí.

Voliteľne:

```bash
bash docker/run.sh  # Kamery a Pico priraďte v aplikácii.
```

Pred použitím CU55 možno nainštalovať existujúce udev pravidlá:

```bash
sudo cp docker/99-hdf-uvc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## Recipe security

Spoločné heslo pre zápis receptov a ponuku Napájanie sa spravuje výhradne
z terminálu v jednorazovom Docker kontajneri (aplikácia pritom nemusí bežať):

```bash
bash docker/security.sh status
bash docker/security.sh set-password
bash docker/security.sh change-password
bash docker/security.sh verify
bash docker/security.sh remove-password
sudo bash docker/security.sh reset-password
```

Normálny režim spustíte cez `bash docker/run.sh`. Ak je ochrana zapnutá, zápisy
receptu aj otvorenie ponuky Napájanie vyžiadajú rovnaké heslo.
`set-password` ho nastaví, `change-password` zmení po overení pôvodného hesla
a `remove-password` po overení a potvrdení odstráni ochranu oboch oblastí. Heslo
sa pri písaní v termináli nezobrazuje a nezadáva sa ako argument príkazu.
Servisný runtime režim sa spúšťa výhradne ako root:

```bash
sudo bash docker/run.sh --admin
```

ADMIN obíde password prompt pre recepty aj Napájanie; nemení ani neodstraňuje heslo a audit zmien
receptu zostáva aktívny. Po ukončení procesu ADMIN režim zanikne. Heslo sa nikdy
neukladá ako plaintext: persistentný `/data/security.json` obsahuje náhodný salt
a PBKDF2-HMAC-SHA256 hash a má oprávnenia `0600`. Pico Wizard a runtime akcie
(RUN, Live, trigger, manual light) nie sú recipe-password ochranou ovplyvnené.

## Raspberry Pi Pico firmware update

Po zastavení výroby a zatvorení HDF Vision spustite na Jetson hoste:

```bash
bash docker/update-pico.sh
```

Skript porovná bežiaci HDF firmvér s `firmware/pico/main.py` v lokálnom projekte
(aktuálne **4.1-pio-pair**). Staršiu verziu zálohuje a aktualizuje, rovnakú alebo
novšiu nemení. Pri viacerých Pico vyberte `--port /dev/ttyACM0`; samotnú kontrolu
bez nahrávania spustíte s `--check`. Uložená konfigurácia zostáva zachovaná.

[Použitie, zálohy a obnova](docs/PICO_UPDATE_SK.md),
[prehľad príkazov](docs/PRIKAZY_SK.md#aktualizácia-firmvéru-pico),
[aktuálny Pico protokol](firmware/pico/README.md).

## Troubleshooting

### INx sa ukáže ACTIVE, ale RUN neurobí snímku

Skontrolujte `INPUT_MAP`, `V1/V2 MODE MASTER`, HDF checkbox **Povolený** a View `Externý zdroj`/`Externý vstup`.

### STATUS ukazuje IN7=OFF

```text
MAP IN7 V1
```

alebo `MAP IN7 V2`, potom `SAVE`.

### CAPTURE INx neprichádza

Overte mapping, MASTER mode, `DEBOUNCE_MS`, active-LOW fyzický vstup a správanie pomocou `TRIGGER INx`.

### LIGHT ON vracia ERR UNKNOWN

Pico pravdepodobne používa firmware starší než 3.3. Overte prvý riadok `STATUS` a aktualizujte `main.py`.

### Svetlo zostalo zapnuté

Overte `LIGHT STATUS`; ak vráti `MANUAL_LIGHT ON`, odošlite `LIGHT OFF`.

## Plánované / future

AI/TensorRT a ďalšie PLC/I/O rozšírenia nie sú prezentované ako aktuálny produkčný flow. Viac kamerových staníc je implementovaných; fyzický výkon a produkčné overenie A7 sú zatiaľ neoverené.

## Známe nesúlady v repozitári

- `firmware/pico/main_v3.3.py` je aktuálny firmware pre MASTER production trigger routing.
- Recipe model a niektoré legacy metódy stále obsahujú `flash_delay_ms`/`flash_pulse_ms` a cestu na publish timingov do Pico. Pico Wizard je však aktuálne určené miesto pre V1/V2 hardware timing; View config ich iba informatívne zobrazuje pre zvolený INx.

### Podporovaný formát receptov

Aplikácia načítava recepty s `format_version: 3`. Staré recepty bez verzie sa
odmietajú bez automatického prevodu alebo vymazania. Nový recept vytvorte pod
novým názvom. Podrobnosti: [formát receptov](docs/architecture/RECIPE_FORMAT_SK.md).

## Archív meraní a pracovných plánov

Historické hardvérové testy, diagnostické záznamy, reporty a pracovné plány boli
31.10.2026 presunuté mimo aplikácie do lokálneho priečinka
`/home/hdf-jetson1/HDF_Vision_test_archive/20261031/`. Rozdelenie a spôsob čítania
komprimovaných záznamov uvádza jeho `README_SK.md`. Testovacie obrázky z pôvodného
`artifacts/` boli odstránené. Nové hardvérové merania ukladajte mimo aplikačného
priečinka. Automatizované regresné testy v `tests/` a servisné Pico zálohy zostávajú
súčasťou pracovného prostredia.
