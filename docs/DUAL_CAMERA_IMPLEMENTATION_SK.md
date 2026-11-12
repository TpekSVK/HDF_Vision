# Dvojkamerová implementácia — experimentálna vetva

Implementácia vo vetve `codex/experimental-dual-camera`, základ `1a3b0cf`
(main po PR378). Nezlučuje sa do main/dev. Používateľský postup je
v [DUAL_CAMERA_SK.md](DUAL_CAMERA_SK.md), výsledky overenia v
`artifacts/dual-camera-implementation/2026-11-12/`.

## Architektúra

- `CameraRegistry`: pevné USB identity a fyzické kamerové služby; routery iba
  pre existujúce UI. Produkčné úlohy dostávajú konkrétnu kameru a kanál.
- `DualPicoTransport`: jedno USB spojenie, samostatné kanálové odpovede,
  časovanie, rezervácie snímok, Live a svetlo. Kanály GP16/17 a GP18/19.
- `RecipeView`: camera_id, output_group, output_mode, and_timeout_ms,
  golden_camera. Kontrakt v4 číta v3 a pred prvým zápisom zálohuje presné v3
  bajty. Bindings v2 majú obdobnú zálohu pôvodnej v1 konfigurácie.
- `WorkcellRuntime` a `WorkcellSession`: dve obmedzené produkčné vetvy, prijatie
  pred zaradením do Qt, žiadna fronta požiadaviek. Snímanie, pipeline, PLC aj
  ukladanie mimo vlákna UI. Nezávislé obnovy TRIGGER, zrušenie pri stop,
  detekcia odpojenia/reštartu Pica. Zastavenie čaká aj na publikovanie skupiny.
- `InspectionGroups`: AND epochy a čas od prvého prijatia po kompletný výsledok,
  držanie už prijatej kamery, rezervácia skupiny počas publikovania, timeout
  NOK, ignorovanie starých výsledkov. AND s vetvením je zakázané; nezávislé
  kamery musia mať samostatné výstupné skupiny.
- Modbus: dve trojice OK/NOK/voliteľný heartbeat, role len vo vlastnej trojici,
  relé7/8 voľné. Generácie pulzov chránia novší impulz pred starým časovačom.
- Golden Wizard, zber/učenie, test nástroja a diagnostika používajú kameru
  pohľadu. Kopírovanie pohľadu na druhú kameru neprenesie referenčné podklady.
  Chýbajúca kamera nezatvorí editor. Pico Wizard uchováva koncepty oboch kanálov.
- História má konkrétnu kameru pri obrázku, spoločný AND cycle_id a samostatný
  finálny skupinový verdikt v SQLite/JSONL. Filtre aj CSV rozlišujú kamery,
  skupinu a dôvod. Chyby snímania možno uložiť aj bez obrázka.
- Existujúci štýl a rozloženie ostali zachované; nové ovládače majú dotykovú
  výšku44px. Dialógy sa overovali aj pri dostupnej výške704px.

## Firmvér a fyzické overenie

Na skutočnom Picu je 4.3.1-dual-channel. main.py je totožný s main_v4.3.1.py;
SHA256SUMS zodpovedá zdrojom. Kópia starého 4.2.1 a konfigurácie ostala mimo
Gitu v `artifacts/pico-backups/20261112T183754Z-154abb02`.

Oprava4.3.1 umožňuje arming PIO v IDLE, pričom fyzické vstupy ostávajú blokované.
MASTER/TRIGGER kanálové fyzické vstupy posielajú REQUEST; hostiteľ najskôr
pripraví príslušný profil a až potom vyžiada snímanie. Starý neprefixovaný
kanál1 protokol ostáva pre starších klientov/updater.

Reálny test: 10 MASTER + 10 TRIGGER spoločných AND cyklov, dve kamery720p,
MSE,40 uložených snímok, po20 na kameru. MASTER326–524ms,
TRIGGER384–430ms vrátane uloženia. Bez chyby snímania/ukončenia.
GUI test: RUN a zachovanie výberu pohľadu, obrázky/filtre, TRIGGER Live,
samostatné svetlo, získanie golden z oboch kamier. Kamerové nastavenia obnovené,
oba kanály IDLE, Live/svetlá vypnuté.

PLC bolo počas fyzických testov vypnuté. Relé, nové vonkajšie24V impulzy a
výkon pri dlhodobej záťaži/plnom rozlíšení neboli v tejto implementačnej skúške
fyzicky kvalifikované. Tieto skúšky patria k následnému testovaniu zostavy.

## Automatické overenie

Záverečné protokoly sú priložené pri artefaktoch. Základná sada630 testov
prešla; UI súbory bežia izolovane, aby sa nededil globálny Qt štýl medzi sadami.
Overuje sa aj vytvorenie/publikovanie dvojkamerového receptu cez reálne dialógy,
zber z vybranej kamery, timeout/odmietanie vstupov, súbežné pipeline, chyby,
obnova/zastavenie, Modbus pulzy, migrácia a filtre. Kontrola syntaxe, hashov
firmvéru a diffu je súčasťou záverečnej kontroly.

Konečný výsledok: **871 passed**, všetkých34 dávok overených. GoldenTouch
prekročil240s limit dávkového spúšťača; samostatný úplný beh prešiel
28/28 za318,42s. Pôvodný timeout aj úspešný protokol sú zachované.


## Následná úprava názvov a prípravy

RUN používa názov pohľadu, SETUP ho skrýva, tlačidlo má text Trigger,
výsledok nepoužíva interné group_id. Príprava aj zastavenie oboch kamier
prebiehajú súbežne, chyby čakajú na dokončenie prípravy druhej kamery pred
čistením. Golden Wizard neopakuje prípravu z hlavného okna.

Cielená regresia669 passed. Opakovaný fyzický test10 MASTER +10 TRIGGER
bez chyby snímania; príprava TRIGGER oboch kamier2262ms oproti súčtu
samostatných intervalov4514ms. Dôkazy v podpriečinku artefaktov
`run-labels-parallel/`. Aplikácia znovu spustená s pôvodným receptom test2.

## Zachovanie pripravenosti

Bežná pauza, navigácia, diagnostika a úspešné vypnutie Live nezatvárajú kamerové
spojenie zdieľanej zostavy. V SETUP zostávajú vstupy a osvetlenie vypnuté.
Zmena profilu/režimu, chýbajúca platná príprava alebo chyba vyvolá potrebnú
obnovu. Zatvorenie aplikácie naďalej uvoľní všetky zariadenia.

Regresia672 passed. Opakované fyzické prechody Live/pauza → kontrola pri720p
bez reštartu oboch spojení a bez chyby snímania. Návrat TRIGGER RUN15–19ms.
Skutočný MainWindow.open_wizard aj návrat do RUN overené bez nových štartov
GStreamer. Dôkazy v `warm-camera-lifecycle/`.
