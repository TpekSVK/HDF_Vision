# Produkčný runtime – A5 dokončená 2026-09-13

## Rozdelenie zodpovednosti

- InspectionController: stav prípravy/pripravenosti/chyby, výhradné prijatie
  požiadavky, počítadlá a konečné vykonanie vetiev bez cyklického opakovania pohľadu.
- InspectionRuntime: profil a snímanie, routing a krokovanie pohľadov, výpočtová
  pipeline, uloženie výsledku, agregácia a výstup do Modbus.
- InspectionWorker: jedno pracovné vlákno, najviac jedna odovzdaná úloha,
  queued doručenie hotových dát na vlastnícke Qt vlákno.
- MainWindow: snapshot UI volieb, navigácia, potvrdenie pauzy a vykreslenie výsledkov.

Runtime nemá widgety ani globálnu kameru/Pico. Dostáva konkrétne inštancie služieb.
SQLite spojenie vzniká, používa sa a zatvára v pracovnom vlákne; UI si ponecháva
vlastné spojenie. Čisté pomocné funkcie ešte sídlia aj v ui namespace; ich presun
spolu s ďalším delením modulov je súčasť A6.

## Životný cyklus

PAUSED → PREPARING → READY → BUSY → READY. Chyba prípravy alebo kontroly vedie
na ERROR; ďalší trigger nemôže sám obnovovať kameru. Obnova prebehne novou prípravou.

Startup a návrat do RUN pripravia kameru/Pico na pracovníkovi. Až úspešná odpoveď
publikuje RUN. Výber náhľadového pohľadu už neprekonfiguruje produkčnú kameru.
Aktuálny profil uplatňuje runtime pri príprave a pred snímaním.

SETUP vyžaduje potvrdenie. Nové vstupy sa hneď prestanú prijímať, prijatá kontrola
sa dokončí, pracovník uvedie Pico do IDLE a až potom sa sprístupní nastavovanie.
Rovnaký postup používa ukončenie; kamera, Pico a Modbus sa zatvoria na pracovníkovi.
Ošetrená je aj požiadavka už prijatá z callbacku, ktorej Qt signál ešte čaká na UI.
Nevykonáva sa násilné súbežné zatvorenie zariadenia počas capture.

Výsledky sú iba stránka: otvorenie ani návrat nevyvoláva prípravu či pauzu.
GUI dostáva hotové records, status a podklady overlay; filtre/ROI nemenia originál.
Počas práce sú blokované ovládače schopné meniť používaný profil, Live a svetlo.

## Prijímanie a preťaženie

Callback rezervuje token ešte pred Qt emit. Prijatá je najviac jedna kontrola,
ďalšie sa neodkladajú. Worker navyše odmietne druhú úlohu, kým prvá nie je doručená.
Počítadlá received/accepted/completed/failed/rejected_<stav> poskytuje snapshot().
Odmietnutia sa logujú s počtami; UI oznámenie je obmedzené, aby nevznikla nová
neobmedzená fronta samotných hlásení. Vstupy mimo RUN sú zámerne ignorované po
vstupnom logu; počítadlá pokrývajú admission cestu RUN, nie všetky elektrické hrany
počas nastavovania. Kompletný operátorský prehľad týchto čísel nie je nový dashboard.

Chyba snímky, uloženia alebo výpočtu ukončí kontrolu ako ERROR/NOK a vykoná pokus
o IDLE. Chyba vykreslenia nezadrží token ani pozastavenie; už vypočítaný fyzický
výsledok sa kvôli vykresleniu nemení.

## Identita a životnosť dát

request_id patrí jednému prijatému triggeru; nesie zdroj a čas prijatia.
cycle_id (tiež run_id pri ukladaní) patrí celej krokovanej sekvencii. frame_id
identifikuje pôvodnú snímku; pohľady, ktoré ju prevezmú, dedia rovnaké frame_id.
Tieto údaje sú uložené v metadátach výsledku spolu so zdrojom a vstupom triggera.

Kontexty krokovaných sekvencií sú oddelené podľa zdroja; obsahujú captured_frames
s explicitnou orientáciou. Prvý krok vytvorí nový cyklus, chyba, pauza alebo zmena
receptu kontext resetuje. Starý náhľad sa nikdy nepoužije ako náhrada zdroja.
Vetva, ktorá by v rámci požiadavky opakovala pohľad, sa odmietne namiesto slučky.

Budúce viac kamier/Pico rozšíri konkrétne identity a koordináciu zdieľaných zdrojov.
Táto etapa nezavádza USB index ako identitu ani globálny runtime singleton.

## Overenie a hranice

174 testov v záverečnej sade vrátane skutočnej pipeline bez MainWindow, worker
SQLite vlastníctva, Qt doručenia, input burst, chýb, krokovania, pause/close, ROI,
učenia a PIO protokolu. Testy nepoužívali fyzický hardvér ani produkčné /data.

Časovacie algoritmy a Pico firmware sa nemenili. Fyzický studený štart, USB,
externá MASTER rezervácia pri odovzdaní do workeru a záťaž sa overia v A7.
SETUP/Golden servisné dialógy majú naďalej vlastné synchrónne operácie; ich
zjednotenie s capture helpermi runtime patrí A6. Počas produkčnej úlohy sú blokované.
