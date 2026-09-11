# See3CAM_CU55M + Pico PIO: implementačný report pre HDF_Vision

**Dátum:** 11. 9. 2026  
**Východiskový commit aplikácie:** `acaf59c67c607c94eee08e1fe5be498e405873e5`  
**Výsledok:** overený laboratórny spôsob snímania jednej kamery cez Pico PIO, pripravený ako podklad pre implementáciu. Používateľ vizuálne prijal získané snímky. Aplikácia ani pôvodný firmvér na Pico flash neboli počas vyšetrovania upravené. Tento dokument je návrh implementácie opretý o merania; neoznačuje budúcu integráciu za už otestovanú.

## 1. Záver pre implementátora

Použiť jednu atomickú sekvenciu na Pico: **PRIME impulz → produkčný impulz**, s presným odstupom nábežných hrán podľa rozlíšenia a expozície. Pico PIO riadi súčasne trigger aj svetlo. Jetson odošle jeden príkaz, priebežne prijme dve snímky, prvú zahodí a druhú odovzdá zobrazovaniu aj inšpekcii.

Kritické pravidlá:

1. Odstup impulzov generuje PIO, nie `sleep()` na Jetsonovi ani postupné USB príkazy.
2. Odstup je **rising-edge → rising-edge period**, nie LOW medzera za koncom prvého impulzu.
3. Pred každou produkčnou snímkou je jeden PRIME. Úvodné jednorazové pripravenie kamery ho nenahrádza.
4. Zachovať požadovanú expozíciu receptu. Na tomto CU55M je hodnota ovládania expozície v jednotkách **100 µs**.
5. Pri zmene rozlíšenia/expozície zabezpečiť finálne otvorenie streamu **v už aktívnom TRIGGER režime**, potom arming/settling a vyprázdnenie fronty. Toto odstránilo nulový príjem pri 720p/VGA.
6. Neposielať redundantný `SET TRIGGER`. Režim najprv prečítať a zmeniť iba ak je iný; stream možno znovu otvoriť aj bez zmeny režimu.
7. Prijať produkčný obraz iba z úplnej a jednoznačne priradenej dvojice. Jednu prijatú snímku z dvoch nemožno automaticky označiť za produkčnú.
8. Táto integračná cesta používa výhradne Pico. **Žiadny fallback na Jetson GPIO.**

## 2. Overené prostredie a rozsah

- Jetson Orin Nano 8 GB, JetPack 6.2; Linux 5.15.148-tegra.
- See3CAM_CU55M, USB VID:PID `2560:c155`, UID `2420020C`, firmvér `1.5.131.1648`.
- USB 3; Y8 = V4L2 `GREY` = GStreamer `GRAY8`.
- Raspberry Pi Pico; GP16 = trigger active HIGH, GP17 = svetlo active HIGH.
- Používateľ potvrdil pripravené prispôsobenie napäťových úrovní. Pri prenose na inú zostavu zachovať toto zapojenie; nepripájať predpokladaných 3,3 V priamo na kamerový vstup s limitom 2,1 V podľa použitého datasheetu.
- Zisk/ovládanie `brightness` v testoch: 0. Svetlo aktívne iba cez Pico, scéna izolovaná od okolitého svetla.
- Finálna matica: 4 rozlíšenia × 7 expozícií × 20 produkčných snímok = **560 vybraných pôvodných PNG**.
- Nie je overené: Y12, iný exemplár/firmvér kamery, iný hub, viac kamier naraz, ľubovoľné expozície alebo sériová produkčná záťaž externých vstupov.

## 3. Definitívna tabuľka testovaných časov

Čísla v tabuľke sú **perióda medzi nábežnými hranami PRIME a produkčného impulzu v µs**. Každá bunka má 20 prijatých snímok; používateľ posudzuje ich obrazovú kvalitu.

| Rozlíšenie / stream fps | 0,5 ms | 1 ms | 2 ms | 5 ms | 10 ms | 15 ms | 16 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2592 × 1944 / 30 | 33340 | 33340 | 33340 | 33340 | 33340 | 33340 | 33340 |
| 1920 × 1080 / 60 | 16670 | 16670 | 16670 | 16670 | 16670 | 16670 | 16670 |
| 1280 × 720 / 60 | 16670 | 16670 | 16670 | 16670 | 16670 | 16670 | 16670 |
| 640 × 480 / 112 | 8930 | 8930 | 8930 | 8930 | 10100 | 15100 | 16100 |

**Dôležité odchýlky od zaokrúhlenej tabuľky výrobcu:**

- Výrobca uvádza pre 5 MP 33,33 ms. Pri PIO 33330 µs vznikali neúplné dvojice; **33340 µs fungovalo**. Pre implementáciu použiť overených 33340, nie znovu skúšať 33330 pri každom spustení. Nejde o dôkaz presnej vnútornej periódy snímača.
- VGA má nominálne minimum 8930 µs, ale pri expozíciách 10/15/16 ms sa muselo predĺžiť na expozíciu + 100 µs. Tieto tri konkrétne hodnoty sú overené; všeobecný vzorec pre netestované expozície ešte nie je validovaný.
- Stream fps je nastavenie UVC formátu. Pri dlhej VGA expozícii a dlhšom trigger intervale **neznamená 112 produkčných snímok/s**. Rýchlosť inšpekcií je ešte nižšia, pretože každá používa dve fyzické snímky a má režijný čas.

Šírka každého trigger impulzu: **PIO naprogramovaných 100 µs HIGH**. Dve nábežné hrany na cyklus. Hodnoty nemožno uložiť do existujúceho celočíselného poľa milisekúnd bez straty presnosti.

Strojovo čitateľné profily so všetkými časmi a zdrojovými priečinkami: [validated_profiles.json](validated_profiles.json).

## 4. Expozícia: jednotky a readback

Laboratórne nastavenie používalo:

```text
v4l2-ctl -d <camera> --set-ctrl=exposure_time_absolute=<hodnota>
v4l2-ctl -d <camera> -C exposure_time_absolute
```

| Požadovaná expozícia | µs v aplikačnom modeli | Hodnota CU55M ovládania |
|---|---:|---:|
| 0,5 ms | 500 | 5 |
| 1 ms | 1000 | 10 |
| 2 ms | 2000 | 20 |
| 5 ms | 5000 | 50 |
| 10 ms | 10000 | 100 |
| 15 ms | 15000 | 150 |
| 16 ms | 16000 | 160 |

V každej vybranej kombinácii bol readback zhodný s požiadavkou. To potvrdzuje hodnotu ovládania, nie nezávislé optické zmeranie integračnej doby.

Odporúčaný kontrakt: aplikačný model vždy `exposure_us`; adaptér CU55M explicitne prevedie na jednotky 100 µs a overí readback. Zaokrúhlenie netestovaných hodnôt musí byť definované, nesmie byť tiché. Názov V4L2 ovládania sám osebe nestačí na určenie jednotiek pre všetky modely kamier.

V aktuálnej aplikácii `set_manual_exposure_us()` najprv posiela vstupnú hodnotu priamo do `exposure_time_absolute`, ale až alternatívnu vetvu `exposure_absolute` delí 100. **Pri tomto CU55M sa to nezhoduje s overenou cestou.** Súčasne `_apply_safe_trigger_exposure()` berie hodnoty označené ako absolute units a posiela ich do metódy pomenovanej v µs. Pri integrácii zjednotiť jednotky, interný stav aj skutočný readback; nepreberať pôvodné čísla automaticky.

## 5. Svetlo a presná definícia časovania

Pre finálnu maticu rozlíšení/expozícií sa používalo:

```python
E = exposure_us
P = validated_period_us
W = 100
L = max(2000, E + 1000)       # náskok svetla pred produkčnou hranou
D = L + P + 2000             # celková dĺžka osvetlenia
pre = L - P                 # argument laboratórneho protokolu
```

Voči PRIME na čase 0:

- PRIME HIGH: 0 až W;
- svetlo ON: P − L, môže byť záporné;
- produkcia HIGH: P až P + W;
- svetlo OFF: P − L + D.

Ak svetlo musí začať pred PRIME, PIO posunie celú sekvenciu tak, aby sa vykonala s nezápornými časmi. `pre > 0` znamená svetlo pred prvým impulzom; `pre < 0` znamená svetlo až po prvom impulze. Do produkčného API je čitateľnejšie uložiť `light_lead_us` voči produkcii a signed `pre` odvodiť iba v adaptéri.

Príklad finálnej matice, 1080p / 1 ms:

```text
P = 16670 µs; W = 100 µs
L = 2000 µs; D = 20670 µs; pre = -14670 µs
PRIME: 0; svetlo ON: 14670; produkcia: 16670; svetlo OFF: 35340 µs
```

Príklad 1080p / 16 ms: L = 17000, pre = +330, D = 35670 µs. Svetlo začína 330 µs pred PRIME. Pri tejto expozícii neponechať pevný náskok 2 ms, ktorý bol použitý v skorších testoch expozície 1 ms.

**Rozlíšiť dve experimentálne nastavenia:** 500-cyklový test pri 1080p/1 ms používal D = 20000 µs. Finálna matica používala vyššie uvedený vzorec a D = 20670 µs. Obidve majú príslušné výsledky; netvrdiť, že úplne identický profil z finálnej matice absolvoval 500 cyklov.

## 6. Inicializácia a zmena nastavení kamery

### Presne použitý laboratórny postup

1. Exkluzívne prevziať kameru aj sériový port Pico. Identifikovať kameru podľa USB identity/UID, nie pevného `/dev/video0`.
2. Uložiť pôvodný režim, rozlíšenie, fps, expozíciu, gain a Pico konfiguráciu.
3. GET režimu; podľa potreby jeden prechod do MASTER. Nastaviť Y8, rozlíšenie a jeho fps, expozíciu a gain. Overiť ovládania a príjem približne 20 MASTER snímok. Tieto snímky slúžia iba na overenie streamu; finálna matica ich neporovnávala ani neukladala ako obrazové referencie.
4. Jeden SET TRIGGER iba ak potrebný, GET readback = TRIGGER. Odstrániť prechodné snímky a počkať na 500 ms ticha, najviac 3 s.
5. Pre konkrétnu expozíciu zapísať a overiť hodnotu.
6. **Zavrieť a znovu otvoriť stream, pričom kamera zostáva TRIGGER.** Znovu počkať na ticho. Pri opravnej dávke sa toto vykonalo po každej zmene expozície.
7. Samostatný arming impulz: prípadnú snímku zahodiť. Chýbajúca arming snímka je zaznamenaná a nie je produkčný výsledok.
8. Jedna settling dvojica: výsledky zahodiť. Potom začať produkčné dvojice. Čiastočné dvojice sa nesmú použiť ako produkčné.
9. Počas stabilnej série nemeníť režim ani neotvárať stream pri každom cykle.

### Dôkaz potreby znovuotvorenia

Pri prvej dávke fungoval úvodný MASTER stream pre 720p/VGA, ale po prepnutí režimu neprišla žiadna trigger snímka. Pri 720p nepomohli intervaly až 80 ms ani zmena PIO na softvérové impulzy Pico. Po opätovnom otvorení streamu v TRIGGER režime prvá dvojica priniesla jednu snímku a ďalšie kompletné dvojice fungovali aj pri nominálnych 16670/8930 µs. Opravná dávka následne dosiahla všetkých 300/300 snímok.

To dokazuje účinnosť tohto postupu na testovanej zostave. Nepotvrdzuje konkrétnu internú príčinu vo firmvéri kamery.

### Návrh aplikačného stavu

```text
CLOSED -> CONFIGURING -> TRIGGER_ARMING -> READY
READY -> CAPTURING_PAIR -> READY
zmena expozície/rozlíšenia -> CONFIGURING
neúplný burst -> RECOVERING -> TRIGGER_ARMING alebo ERROR
preview MASTER -> INVALIDATE_TRIGGER_SESSION
```

Pripravenosť viazať na generáciu konfigurácie: `(camera_uid, format, size, stream_fps, exposure, gain, pipeline_generation, timing_profile)`. Zmena ktoréhokoľvek relevantného parametra zneplatní pripravenosť. Existujúci `set_stream_mode()` už stream zastavuje a otvára; integračný kód musí overiť, že finálne otvorenie nastalo až po všetkých nastaveniach. Samotný no-op `set_stream_mode(TRIGGER)` pripravenosť neopraví, pretože správne preskakuje redundantný SET.

## 7. Príjem jednej produkčnej snímky

Odporúčaný kontrakt `capture_pio_pair(profile, request_id)`:

1. Zamknúť capture transakciu; overiť generáciu konfigurácie a READY.
2. Aktivovať príjem celej dvojice pred odoslaním príkazu. Vyprázdniť a zaznamenať prípadné staré/predčasné buffery.
3. Poslať **jeden** príkaz Pico pre dve hrany a svetlo.
4. Prijímať snímky hneď. Nečakať najprv na koncový ACK a až potom zháňať snímku z latest-frame ring buffera.
5. Súčasne zbierať odpoveď Pico s rovnakým request ID; vyžadovať výsledok aj úspešné dokončenie príkazu.
6. Počet = 2, správna generácia streamu, rozmery/formát a monotónne poradie. Prvá snímka PRIME sa zahodí, druhá je produkčná.
7. Jedna snímka alebo timeout znamenajú neúspešný/nejednoznačný cyklus. Nepoužiť posledný známy obraz. Obnovu logovať ako nový pokus, nie ako neviditeľné dokončenie pôvodného cyklu.
8. Tú istú produkčnú snímku odovzdať UI a vyhodnoteniu. Nevykonať druhé snímanie iba pre náhľad.

GStreamer v laboratóriu: `v4l2src io-mode=2 do-timestamp=false`, presný GRAY8 caps, appsink `emit-signals=true sync=false drop=true max-buffers=1 enable-last-sample=false`. Callback okamžite kopíruje buffer do numpy a odovzdá ho do hostiteľskej fronty, nepočíta SSIM ani neukladá PNG. Preto `max-buffers=1` samo osebe neznamenalo stratu PRIME; callback musí obe snímky prevziať okamžite. Pri integrácii nestačí pollovať iba najnovší frame.

Laboratórny buffer offset je poradie GStreamer/V4L2 bufferov, nie nezávislý hardvérový identifikátor expozície. Hostiteľská časová značka, rastúce poradie ani rozdielny CRC samy osebe nedokazujú optickú čerstvosť. Produkčné request ID koreluje príkaz a jeho časové okno; kamera toto ID nevkladá do obrazu.

## 8. Pico PIO: funkčný referenčný protokol

Laboratórny príkaz:

```text
LAB PIO <id> <count> <period_us> <pulse_us> <pre_us> <duration_us> <light_enable> <tail_us>
LAB PIO 42 2 16670 100 -14670 20670 1 50000
```

Odpovede:

```text
LAB RESULT { ... "id":42, "count":2, "period_us":16670, ... }
OK LAB FIRED 42
```

`ERR ...` a `BUSY ...` musia ukončiť príslušný pokus ako chybu, nie sa považovať za potvrdenie.

PIO program pracuje na 1 MHz, `out_base=Pin(16)`, dva výstupné bity:

- bit 0: GP16 trigger;
- bit 1: GP17 svetlo;
- FIFO slovo: `((duration_us - 4) << 2) | pin_state`;
- `pull; out(pins,2); out(x,30); jmp(x_dec,hold)`;
- medzi zmenami pinov uplynie N + 4 cyklov podľa tohto programu.

Udalosti v rovnakom čase sa zlúčia do jedného stavu pinov. Najmenší odstup udalostí v tejto implementácii je 4 µs. TX FIFO je spojené na 8 slov. Sekvencia končí LOW a čaká na ďalšie slovo; potom sa state machine deaktivuje a výstupy sa vrátia LOW. Hostiteľský začiatok príkazu nie je fyzický začiatok sekvencie. Interný úvodný posun 2 ms a tail 50 ms nie sú perióda kamery ani dĺžka jej expozície.

**Časové značky PIO v JSON sú naprogramované cykly, nie osciloskopické meranie.** V staršom PIO probe boli polia ešte pomenované `actual_*`; určujúci je uvedený `timing_basis`. Finálne logy rozlišujú naprogramované hodnoty. PIO hodiny a kamera majú nezávislé hodinové zdroje; prenos na iný exemplár si vyžaduje overenie minimálneho intervalu.

RAM5 limity referenčného LAB PIO: count 1–3, period 8930–100000 µs, HIGH 10 µs až menej než period, pre ±100000 µs, svetlo 0–200000 µs, tail 20000–100000 µs. Nie je to hotový všeobecný viackamerový plánovač.

### Čo nepreberať z laboratórneho runtime do produkcie

Laboratórne rozšírenie beží iba v RAM, zakazuje SAVE a vypína mapovanie externých vstupov/pollovanie INx počas testu. Jeho hlavná slučka ani diagnostické príkazy nie sú náhrada kompletného produkčného firmvéru. Preniesť PIO engine do riadenej novej verzie pôvodného firmvéru, zachovať existujúce profily, MASTER flow, vstupné mapovania a sériový protokol. Novú schopnosť ohlásiť verziou/capability a odmietnuť PIO režim pri starom firmvéri.

## 9. Konkrétne miesta budúcich zmien v HDF_Vision

Odkazy sú voči vyššie uvedenému commitu. Ide o návrh, nie o vykonané úpravy.

| Súbor / symbol | Súčasný stav | Potrebná zmena |
|---|---|---|
| `app/utils/trigger_timing.py` | ms tabuľky, default LOW gap 40/20/20/12 ms, margin 3 ms | Zaviesť explicitný CU55 PIO profil v integer µs; testované periódy z JSON; rozlíšiť period od gap; nerozširovať Y8 hodnoty automaticky na Y12. |
| `camera_service.py:_compute_trigger_timing` | period = pulse + gap, margin a hostiteľské pauzy | Nová vetva pre PIO neodvodzuje period zo starého gap; bez pripočítania 10 ms HIGH alebo 3 ms rezervy. |
| `camera_service.py:_perform_trigger_sequence` | Tri impulzy; medzi nimi čaká na USB snímku a následne `sleep()` | Jeden PIO burst s dvoma impulzmi, dve okamžite odoberané snímky, žiadne hostiteľské časovanie vnútri páru. |
| `camera_service.py:enter_trigger_session`, `_apply_safe_trigger_exposure` | Môže prepísať expozíciu na internú safe hodnotu | Zachovať recept, explicitné jednotky/readback; po nastaveniach znovuotvoriť stream a arming/settling. |
| `camera_service.py:set_manual_exposure_us` | Priamy zápis do primárneho ovládania, delenie 100 iba vo fallback vetve | Modelovo špecifický prevod CU55M µs ↔ 100 µs, jednotný interný stav. |
| `camera_service.py:set_stream_mode` | Už preskakuje redundantný SET a reštartuje otvorený stream | Toto zachovať; oddeliť znovuotvorenie streamu od zmeny režimu; pripravenosť viazať na konfiguráciu. |
| `camera_service.py:_attempt_trigger_pipeline_reopen` | Pomocná metóda existuje | Zapojiť do explicitného recovery stavu; samotná existencia funkcie nedokazuje, že ju všetky capture cesty volajú. |
| `app/services/pico_service.py` | `fire()` a parser pre existujúce OK FIRED/CAPTURE | Verziovaná PIO transakcia, request ID, výsledok + ACK, timeouty, BUSY, prijímač aktivovaný pred príkazom. |
| `pico_service.py:_rx_loop`, `_line_matches_command` | Neznáme riadky ignoruje; `LAB RESULT` nemá kontrakt | Nové typované správy nezamieňať s `CAPTURE INx` ani ich nestratiť medzi nesúvisiacimi odpoveďami. |
| `app/ui/main_window.py:_send_run_trigger_gpio_pulse` | Pulzuje fyzický GPIO pin 7 na Jetsonovi, 10 ms | CU55 PIO capture nesmie volať túto funkciu. Nahradiť príslušné callbacky transakciou cez Pico; bez GPIO fallbacku. |
| `main_window.py` capture volania okolo 1211, 1236, 1353 a odovzdanie callbacku okolo 1962 | Používajú pôvodnú trigger cestu | Audit všetkých vstupov: manuálny, časovaný, externý, diagnostika/učenie. Zachovať tú istú snímku pre UI aj inšpekciu. |
| `pico_config_service.py`, `pico_wizard.py`, ukladanie view/receptov | Existujúce ms konfigurácie | Verziovanie schémy, explicitné µs profily, migrácia bez reinterpretovania starej hodnoty GAP ako novej periódy. |
| `firmware/pico/main.py`, `main_v3.3.py`, README | Produkčný firmware s existujúcimi MASTER/TRIGGER flow | Verziovaný PIO engine; zachovať pôvodný režim a otestovať existujúce vstupy. Neprepisovať flash laboratórnym runtime. |

Najväčšia integračná pasca: ponechať tri volania pôvodného capture algoritmu a pod každé vložiť dvojimpulzový PIO burst. Výsledkom by bolo šesť hrán a zlé priradenie snímok. Nahradiť transakciu ako celok.

## 10. Chyby, obnova a preview

- Pri chybe/odpojení ihneď zneplatniť READY a konfiguráciu; úspešný starší readback už nestačí.
- Pri neúplnom páre nespúšťať inšpekciu z dostupnej jednej snímky. Reportovať capture error odlišne od vizuálneho NOK dielu.
- Navrhovaná obnova: ukončiť transakciu → stream reopen pri nezmenenom TRIGGER → ticho → arming/settling → nový capture pokus. Obnovu limitovať; nevytvárať nekonečný sled módov a impulzov.
- Pri nevyriešenej chybe môže byť ďalší riadený krok MASTER → konfigurácia → TRIGGER → reopen. Nie je dovolené považovať opakované slepé SET TRIGGER za bezpečný reset. Historicky sa kamera pri niektorých módových sekvenciách zablokovala.
- Dynamické USB zariadenia zisťovať podľa identity pri novom otvorení. Neskočiť počas prebiehajúceho cyklu potichu na nové `/dev/videoN`.
- Preview má jedného vlastníka streamu. Prechod do MASTER preview zneplatní trigger pripravenosť. Ukončenie preview musí pripraviť TRIGGER znovu pred akceptovaním externého triggeru.
- Pri externom vstupe a BUSY treba definovať, či sa požiadavka odmietne alebo zaradí do obmedzenej fronty. Toto laboratórna dávka neoverovala. Pripravenosť hostiteľa/Pico a request ID sa nesmú nahradiť starým samotným `CAPTURE` textom.

V matici bol frame timeout 1 s a čakanie na sériovú odpoveď 2 s. Tieto hodnoty boli diagnostické. Produkčné timeouty navrhnúť podľa profilu a overenej latencie s rezervou, nezamieňať ich s periódou medzi impulzmi.

## 11. Súhrn dôkazov a hranice záverov

### 1080p skúmanie pásu

Pás okolo 47. riadku zostával aj pri súvislom svetle a rástol s odstupom PRIME → produkcia. Posúvanie blesku pri 18 ms ho len čiastočne kompenzovalo. Skrátenie na 16,670 ms s PIO ho výrazne potlačilo. Ide o experimentálnu súvislosť, nie priame zmeranie vnútorného reset/readout mechanizmu snímača.

### Dlhá séria

`results/20260911_181014_band_validation`: 500/500 úplných dvojíc, 1000 fyzických snímok, žiadna chyba poradia ani evikcia hostiteľskej fronty; úvodný arming impulz bez snímky bol samostatne evidovaný. Náhodné pauzy 0,222–4,994 s. Čas hostiteľský príkaz → produkčná snímka: priemer 37,68 ms, p95 38,15 ms, maximum 54,81 ms.

Staršie obrazové kritériá prešli 500/500, prísne top200 kritérium 117/500 a globálne riadkové kritérium 20/500. Počas série sa obraz posunul približne o pixel; podobný posun má aj záverečný MASTER. Používateľ výsledok prijal ako validný a posun hodnotil ako malý otras. Namerené neúspechy zostávajú v reportoch; používateľské prijatie nie je prepis meraní.

### Finálna matica bez MASTER porovnávania

- Pôvodná dávka `review_batches/20260911_193419_resolution_exposure`: 262 PNG. Plné rozlíšenie 0,5–15 ms po 20; pri 16 ms iba 2. 1080p všetkých 7 × 20. Nižšie rozlíšenia bez snímok.
- Opravná dávka `review_batches/20260911_200903_missing_recovery`: **300/300 PNG**, bez cleanup chýb. Celá séria fullres/16 ms sa zopakovala na 20; ďalej 720p a VGA po 140.
- Výber pre implementáciu: pôvodných 120 fullres + 140 1080p + opravných 300 = **560**. Pôvodné 2 fullres/16 ms zostávajú navyše ako história, nepočítajú sa do vybraných 560.
- Overené: 28 úplných priečinkov po 20 PNG, rozmery z hlavičiek, metadata úplných dvojíc, readback expozície, SHA256 všetkých vybraných obrazov.
- Používateľ oznámil, že snímky vyzerajú správne, konzistentne a bez artefaktov/pásov. V tejto fáze sa podľa jeho požiadavky nevykonávalo porovnanie s MASTER.

### Čo ešte nezamieňať za overené

- Starší test optického kódu 100/100 aj idle do 120 s platili pre predchádzajúce časovanie 18 ms, nie automaticky pre nový PIO profil.
- 20 snímok na kombináciu nie je dlhodobá štatistika spoľahlivosti každej kombinácie.
- Nebola overená elektrická hrana na kamere osciloskopom ani skutočný optický priebeh blesku.
- Viackamerové počty 4/8 boli iba predchádzajúce odhady, nie výsledok tohto testovania. Tento dokument validuje jednu kameru.
- Gain 0 a tento svetelný zdroj sú súčasťou skúšky. Presýtenie pri vyššej expozícii neposudzoval automatický test; pri implementácii zostáva predmetom používateľského nastavenia.

## 12. Navrhované kroky implementácie a akceptácia

1. **Model a prevody:** profily integer µs, CU55M expozícia, readback, odmietnutie nepodporovaných profilov. Testy prevodov 500→5, 1000→10, 16000→160; perióda nesmie dostať pripočítanú HIGH šírku.
2. **Pico firmware:** verziovaná PIO schopnosť pri zachovaní MASTER a vstupov. Testy počtu hrán, oboch výstupov LOW po chybe/ukončení, BUSY a hraníc FIFO/parametrov. Na cieľovej zostave overiť časovanie meraním, ak bude dostupné.
3. **Sériová transakcia a capture fronta:** jeden request ID, výsledok/ACK, dve snímky, žiadna strata pri skoršom príchode snímok než ACK. Testy timeoutu, jednej snímky, odpojenia a oneskorenej odpovede zo starého requestu.
4. **Životný cyklus kamery:** konfigurácia pred finálnym reopen, arming, invalidácia READY pri zmene view/expozície/preview. Reprodukovať doterajší 720p/VGA problém a potvrdiť jeho odstránenie v aplikácii.
5. **Zapojenie všetkých spúšťačov:** manuálny, časovaný, fyzický INx, softvérový ekvivalent vstupu. Odstrániť CU55 PIO závislosť od Jetson GPIO callbacku. Zachovať MASTER capture bez regresie.
6. **Integračná matica:** zopakovať 28 × 20 cez aplikáciu; porovnať s prijatými PNG používateľom. Potom 500 náhodných cyklov pri cieľovom profile, optický kód a idle/reconnect pre nový PIO režim, externé BUSY situácie.
7. **Nasadenie:** samostatná vetva a review; záloha pôvodného firmware/config, jasná verzia protokolu a návrat k predchádzajúcej verzii. Tento report sám firmware ani aplikáciu nenasadzuje.

Produkčná akceptácia musí explicitne rozlišovať: príjem/čerstvosť obrazu, správnu konfiguráciu a obrazovú kvalitu. Nesmie považovať vysoké SSIM za náhradu úplnosti páru ani za dôkaz čerstvosti.

## 13. Odovzdávacie súbory a obnovenie laboratória

V tomto balíku:

- `validated_profiles.json`: 28 konkrétnych profilov vrátane svetla a vstupných hodnôt expozície;
- `evidence_manifest.json`: 560 pôvodných PNG, ich metadata a SHA256; absolútne cesty k dátam;
- `source_sha256.json`, `lab_source/`: snímka laboratórneho zdrojového kódu. Zdrojové snapshoty jednotlivých meraní zostávajú tiež v `results/<run>/source`;
- tento report.

Súvisiace reporty v nadradenom laboratóriu: `REPORT_500_SK.md`, `BAND_REPORT.md`, `PROGRESS.md`, `START_HERE.md`, `RESUME_STATE.json`. Lokálna dokumentácia výrobcu je v `docs/`; tabuľka Y8 USB3 bola v `04_See3CAM_CU55M_Trigger_Mode_Application_Note_1.0.txt`, strana 5 pôvodného PDF. PIO inštrukčný model: https://docs.micropython.org/en/latest/rp2/tutorial/pio.html .

Posledný známy stav: dávky skončené, cleanup bez chýb, Pico laboratórny RAM5 runtime; pôvodný flash nezmenený. Stav sa po reštarte môže líšiť, vždy čítať STATUS. Laboratórny runtime má vstupy OFF, nie bežnú produkčnú prevádzku.

Obnova pôvodného runtime až keď kamera/test nie je obsadený:

```bash
python3 /home/hdf-jetson1/cu55_trigger_lab/pico_ram.py restore
```

Loader overuje zhodu pôvodného `main.py` so zálohou; neprepisuje flash. Po obnove overiť STATUS, profily/mapovania a svetlo OFF. Ak treba obnoviť experimentálny režim, `pico_ram.py start` načíta aktuálny laboratórny zdroj do RAM, preto pred reprodukciou overiť jeho verziu/hash.

Pôvodný main.py SHA256: `d8dfe35c6a2e2f2348d280a873fb11bf755dfd2b1ea2c07ac044e8f76580755d`.

**Dáta sú lokálne mimo Git repozitára HDF_Vision. GitHub ich automaticky nezálohuje.** Archív odovzdávacieho balíka obsahuje report, profily, manifest a kód; surové PNG zostávajú v uvedených priečinkoch a nie sú súčasťou malého archívu.
