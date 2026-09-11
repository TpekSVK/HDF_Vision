# Integrácia CU55M / Pico PIO 4.0

## Stav

Implementácia a overenie sú dokončené vo vetve `codex/cu55-pio-trigger-v4`.
Výsledky: 560/560 PNG, 12 prechodových cyklov, externé požiadavky/reconnect/obnova,
173 automatizovaných testov. Detaily a rozsah overenia: `PIO_VALIDATION_20260911_SK.md`.
Firmvér je v `firmware/pico/main_v4.0.py` a identicky v `main.py`. Testoval sa iba
v RAM; trvalá flash Pico sa nemenila. Zmeny nie sú commitnuté ani pushnuté.

## Čo používa aplikácia

`PicoService.capture_trigger(camera)` vlastní snímaciu transakciu. RUN aj Golden
používajú túto cestu. Mikrosekundové hrany riadi Pico PIO, nie časovač alebo GPIO
Jetsona. `CameraPioMixin` prijíma obe snímky priamo z GStreamer callbacku, nezávisle
od preview fronty. Dvojica musí obsahovať dva rámce správnych rozmerov s nadväzujúcim
poradím. Výsledkom je iba druhý rámec. Chýbajúce, duplicitné, prebytočné a oneskorené
rámce znamenajú chybu; nevracia sa fallback zo starého preview.

Sériový výsledok musí obsahovať zhodné ID transakcie, počet impulzov a konečné ACK.
Rámce môžu prísť pred ACK. Staré ACK s iným ID neukončí novú transakciu. Jediný RX
thread triedi odpovede a udalosti; port má exkluzívne otvorenie.

## Profily

Všetky časy nižšie sú µs. Formát je Y8/GREY/GRAY8.

| Rozlíšenie | fps | Základný period |
|---|---:|---:|
| 2592×1944 | 30 | 33340 |
| 1920×1080 | 60 | 16670 |
| 1280×720 | 60 | 16670 |
| 640×480 | 112 | 8930 |

Pokiaľ expozícia presahuje základný period, použije sa expozícia + 100 µs.
Pre VGA pri 10/15/16 ms teda 10100/15100/16100 µs. Impulz má 100 µs.
Svetlo: náskok L = max(2000, expozícia + 1000), dĺžka D = L + period + 2000,
parameter pre = L − period. Program riadi jedno svetelné okno cez celú dvojicu.

Overená laboratórna matica obsahuje 28 profilov (7 expozícií × 4 rozlíšenia).
Kód povoľuje 0,5–16 ms v krokoch 0,1 ms rovnakým vzorcom; medzihodnoty nemajú
samostatné historické obrazové overenie. Netestované formáty/rozlíšenia/fps a
expozície mimo rozsahu sa odmietnu. Staré recipe `trigger_gap_ms` ani Pico V1/V2
GAP neprepisujú tento period.

Aplikačná expozícia je vždy v µs. CU55 `exposure_time_absolute` používa jednotky
100 µs: 1000 µs sa zapisuje ako 10. Hodnota sa číta späť; nesúhlas je chyba.
Opakované nastavenie už potvrdenej rovnakej expozície nevykoná zápis do senzora.

## Prepínanie a pripravenosť

Po štarte Pico je SESSION IDLE. Aplikácia počas prípravy potlačí externé udalosti,
GET-om zistí režim kamery a SET pošle iba pri potrebnej zmene. Neznámy stav sa
nepovažuje za MASTER a nevyvolá slepý opakovaný SET TRIGGER.

Pri novej konfigurácii sa najprv potvrdí MASTER a prijme 20 skutočných rámcov.
Pri 1080p sa potom stream pri prechode do TRIGGER **zachová**: jeho reopen v TRIGGER
preukázateľne spôsoboval nulový príjem. Ostatné rozlíšenia používajú po nastavení
expozície reopen v TRIGGER podľa opravnej laboratórnej matice. Nasleduje arming impulz
(prípadná snímka sa zahodí), settling dvojica (tiež sa zahodí) a SESSION TRIGGER. Pri settling kamera môže
vrátiť doprednú medzeru v číslovaní 0,2 po chýbajúcej arming snímke; táto dvojica
sa nikdy nevracia používateľovi. Produkčné dvojice vyžadujú nadväzujúce čísla.
Pripravenosť je viazaná na konfiguráciu a generáciu streamu. Rovnaký ďalší capture
neopakuje SET ani reopen. Zmena konfigurácie, stop, neúplná dvojica alebo neočakávaná
snímka pripravenosť zrušia. Ďalší pokus vyžaduje novú prípravu; žiadne nekonečné
skryté opakovanie produkčnej snímky.

MASTER prechod najprv nastaví Pico IDLE, potvrdí režim kamery, obnoví príjem
čerstvých kontinuálnych snímok a až potom aktivuje SESSION MASTER. UI pri chybe
hlási dôvod a skúsi obnoviť predchádzajúci režim. Pri neúspešnej obnove nemožno
považovať samotný text vo výbere režimu za potvrdenie pripraveného hardvéru.

V TRIGGER externý vstup vysiela REQUEST; app vyberie view a až potom spustí PIO.
V MASTER zostáva CAPTURE počas osvetlenia a rezervácia čerstvej MASTER snímky.
Dočasné odovzdanie kamery sprievodcovi deaktivuje SESSION; po návrate sa obnoví.

## Podklady a reprodukcia

- `CU55_PIO_LAB_REPORT_SK.md`: pôvodný podrobný report; opisuje laboratórium pred integráciou.
- `cu55_pio_validated_profiles.json`: pôvodné profily, vrátane pôvodu snímok; nový test kontroluje zhodu časovania.
- `tests/test_cu55_pio.py`, `test_pico_pio_protocol.py`, `test_pico_firmware_v4.py`, `test_main_window_pio.py`: nové testy.
- `firmware/pico/README.md`: nasadenie, kompatibilita a protokol.

Hardvérové overenie oboch režimov, opakovaných prechodov a konfigurácií je uvedené
v záverečnom validačnom reporte. Simulácia PIO FIFO overuje naprogramované
cykly; nenahrádza meranie napätia na TRIG kamery ani úspešný príjem snímok.

## Nastavovanie pohľadov v Golden Wizard

Dialógy Pridať pohľad a Upraviť pohľad používajú spoločnú expozíciu snímača pre
MASTER aj TRIGGER. Ponuka obsahuje 0,5, 1, 2, 5, 10, 15 a 16 ms; ručný vstup nie je
povolený. Pôvodný recept s inou expozíciou sa automaticky nekonvertuje: pri úprave
pohľadu treba vybrať novú hodnotu. Samotné otvorenie receptu hodnoty nemení.

Rozlíšenie uvádza rozmery, fps streamu a formát. TRIGGER podporuje štyri overené Y8
profily; fps streamu nevyjadruje počet kontrol za sekundu. Režim kamery sa prepína
v hlavnom okne. V TRIGGER aplikácia vypočíta časovanie Pico z rozlíšenia a expozície,
preto formulár neobsahuje ručné nastavenie medzery medzi impulzmi ani výber V1/V2.
Voľby časovač/externý vstup naďalej určujú, kedy aplikácia požiada o snímku.
