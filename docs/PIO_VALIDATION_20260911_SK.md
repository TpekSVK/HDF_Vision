# CU55M / Pico PIO 4.0 – výsledok implementácie

Implementované a overené 11. 9. 2026 na Jetson Orin Nano, CU55M UID2420020C,
firmvér kamery 1.5.131.1648. Vetva `codex/cu55-pio-trigger-v4`.

## Výsledky

| Rozlíšenie Y8 | fps | Expozície | Produkčné snímky | MASTER→TRIGGER cykly |
|---|---:|---|---:|---:|
| 1920×1080 | 60 | 0,5 / 1 / 2 / 5 / 10 / 15 / 16 ms | 140/140 | 3/3 |
| 1280×720 | 60 | rovnakých 7 | 140/140 | 3/3 |
| 640×480 | 112 | rovnakých 7 | 140/140 | 3/3 |
| 2592×1944 | 30 | rovnakých 7 | 140/140 | 3/3 |

Každá bunka obsahuje 20 PNG. Spolu **560/560 produkčných snímok**, každá pochádza
z úplnej dvojice PRIME + produkcia; 1120 prijatých rámcov v produkčnej matici.
Prípravné rámce a snímky prechodových skúšok sa počítajú oddelene. Žiadna chyba
produkčnej dvojice, žiadna skrytá opakovaná produkcia a žiadny reopen počas stabilnej
20-snímkovej série. Kontrolné SHA256 všetkých 560 uložených PNG boli overené.
Obrazová kvalita sa automaticky neporovnávala s MASTER; PNG sú k dispozícii na kontrolu.

Ďalší test overil 20 požiadaviek `TRIGGER IN1` cez nový protokol REQUEST. Požiadavka
sama nespustila kameru; až rezervácia prijímača a PIO príkaz vytvorili dvojicu.
IDLE potlačilo požiadavky. Prešiel reconnect serial, opätovná inicializácia 1080p po
výmene streamu a následné MASTER snímanie. Opakované prepare MASTER zachovalo stream.
Po poslednej úprave FW (odmietnutý PIO príkaz nesmie meniť výstupy MASTER) prešiel
finálny kratší test s 3 externými požiadavkami, reconnectom a obnovou streamu.

Meranie dodatočného 1080p/1ms testu: volanie produkčnej capture funkcie po pripravení
kamery vracalo frame za medián 97,35 ms, rozsah 95,82–98,19 ms. To zahŕňa čakanie na
PIO ACK a neznamená fyzickú trigger latenciu senzora. Kompletný skúšobný cyklus
MASTER príprava + MASTER snímka + TRIGGER príprava + TRIGGER snímka trval 3,04–3,66 s;
nie je to čas jednej bežnej produkčnej snímky.

**173 automatizovaných testov prešlo** v `hdf_vision:dev` vrátane snímania, protokolu,
firmvéru, expozície, sprievodcu, Golden cesty a routovania externých vstupov. Staršie
neaktuálne testy boli opravené tak, aby používali existujúci INx kontrakt, rezervovaný
MASTER frame a inicializovaný stav sekvencií; produkčné routing pravidlá sa nemenili.
GUI cesty boli overené testami; hardvérová dávka používala priamo služby aplikácie.
Celá interaktívna RUN kontrola s konkrétnym používateľským receptom nebola vykonaná.

## Dôležitá oprava pôvodného odporúčania

V 1080p **zachovať stream pri prechode MASTER → TRIGGER**. Jeho znovuotvorenie v
TRIGGER reprodukovateľne viedlo k nulovému príjmu. Pôvodná úspešná 1080p matica
stream tiež zachovávala. Reopen z opravnej matice bol potrebný pri iných rozlíšeniach;
nesmie sa zovšeobecniť na 1080p.

Po samostatnom arming impulze môže prípravná dvojica obsahovať sekvencie 0,2. Obe sa
zahodia. Produkčné dvojice vyžadujú dve nadväzujúce sekvencie; chýbajúca snímka sa
nikdy nenahradí starým preview. Pri novom stream-e sa potvrdí MASTER a prijme 20
rámcov pred prechodom. Neznámy režim nevyvolá slepý SET TRIGGER.

## Uloženie a nasadenie

- `firmware/pico/main_v4.0.py`: označený nový MicroPython firmvér.
- `firmware/pico/main.py`: identická nasadzovacia kópia.
- `firmware/pico/main_v3.3.py`: predchádzajúca verzia ponechaná.
- `app/services/camera_pio.py`: príprava, dvojice, obnovovanie a režimy.
- `app/utils/cu55_pio.py`: overené per-resolution časovanie.
- `PIO_APP_INTEGRATION_SK.md`: implementačný kontrakt a obmedzenia profilov.

Nový firmvér bol testovaný **iba v RAM**. Pôvodná flash Pico zostala nezmenená a po
reštarte sa spustí pôvodný `main.py`. Pre trvalé nasadenie treba nový súbor skopírovať
na Pico ako `main.py`; postup a kompatibilita sú v `firmware/pico/README.md`.
Starší FW zostáva použiteľný pre MASTER, nový PIO TRIGGER vyžaduje FW4.0.
MASTER profily musia ponechať dostatočné osvetlenie po CAPTURE na prijatie čerstvého
rámca; hlavná matica používala V1 PULSE150 ms/CAPTURE30 ms. Finálny kratší test prešiel
aj s pôvodným V1 PULSE100 ms/CAPTURE0 ms. Ľubovoľné iné MASTER časovania sa netestovali.

## Dôkazy a reprodukcia

Základ výsledkov: `/home/hdf-jetson1/cu55_trigger_lab/implementation_20260911/`.

- `app_matrix_01/status.json`, `run.log`, podadresáre s PNG: úplná matica.
- `matrix01_source/`: jadro, ktoré absolvovalo maticu.
- `lifecycle.json`: 20 externých požiadaviek a kontrola obnovy.
- `final_lifecycle.json`: kontrola finálneho FW a služieb.
- `final_source/`, logy a finálny checkpoint: verzia odovzdaná po implementácii.

Reprodukčný nástroj je `tools/validate_pico_pio.py --output <nový_adresár> --samples 20 --switches 3`.
Spúšťa sa v aplikačnom Docker prostredí s prístupom ku kamere/HID/Pico; nevyužíva
Jetson GPIO. Mení nastavenia kamery a dočasné Pico profily, nevykonáva SAVE ani zápis
firmvéru. Na konci necháva kameru v MASTER, Pico v IDLE, svetlo vypnuté.
