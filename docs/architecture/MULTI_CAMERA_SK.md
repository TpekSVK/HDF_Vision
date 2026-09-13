# Nezávislé kamerové stanice

Implementácia 2026-09-13, po A6. A7 je na pokyn používateľa odložená.
Táto verzia podporuje pridávanie staníc; počet nie je pevne obmedzený na dve.
Tri stanice boli overené simuláciou, dve samostatné GStreamer slučky syntetickým
videom. Výkon troch fyzických USB kamier ani aktuálne fyzické párovanie neboli overené.

## Ovládanie

1. Spustite aplikáciu aktualizovaným `docker/run.sh`.
2. Otvorte **Kamery a Pico**. Pri prvom spustení bez priradenia sa nespustí žiadna kamera.
3. Vyberte kameru a Pico pre **Kameru 1**; pridajte ďalšie stanice tlačidlom
   **Pridať kameru**. Zadajte aj vlastné zrozumiteľné názvy, napríklad „Horná kontrola“.
4. **Overiť dvojicu a zobraziť snímku** pripraví vybranú dvojicu overeným PIO tokom
   pri 1920×1080 a expozícii 1 ms. Zobrazí produkčnú snímku alebo chybu. Overenie
   prebieha na pozadí; pred zavretím sa dokončí snímanie a uvoľnia USB zariadenia.
   Výber iného Pico umožní overiť druhé fyzické zapojenie; aplikácia páry nehádá.
5. Uložte priradenie. V hornej časti aplikácie pribudnú samostatné karty staníc.
   Každá má vlastný recept, pohľady, MASTER/TRIGGER, Pico nastavenia, RUN a výsledky.
6. Zapnite potrebné Pico vstupy a nastavte pohľady v príslušnej stanici rovnako
   ako pri jednej kamere. Nová stanica začína vlastným prázdnym receptom `default`.

Prepnutie medzi kamerami alebo na **VÝSLEDKY** nezastavuje kontroly na pozadí.
**SETUP** po potvrdení zastaví iba príslušnú stanicu, po dokončení prijatej kontroly.
Na zmenu párovania musia byť všetky stanice v SETUP alebo chybe a pracovníci voľní.
Priradenie používa existujúce overenie hesla aplikácie. Odobratie stanice nevymaže jej dáta.
Vypnutie/reštart z ľubovoľnej stanice najprv dokončí a zastaví všetky stanice.

## Dáta

| Obsah | Kamera 1 | Kamera N, N > 1 |
| --- | --- | --- |
| Základný priečinok | `/data` | `/data/stations/camera_N` |
| Recepty/golden/učenie | `recipes/` | `recipes/` vo vlastnom základe |
| Výsledky | `runs/`, `HDF_Vision.db` | `runs/`, `HDF_Vision.db` vo vlastnom základe |
| Pico/Modbus | `pico_config.json`, `modbus_config.json` | Vlastné súbory v základe stanice |
| Zvolený recept | `config.json` | Vlastný `config.json` |
| Diagnostické logy | `logs/` | Vlastný `logs/` predvolene |

Spoločné priradenie je `/data/workstation_devices.json`, formát verzie 1.
Rovnaký názov receptu či pohľadu v dvoch staniciach neznamená spoločné dáta.
Prvá stanica zachová doterajšie dáta bez migrácie; priraďte jej pôvodnú kameru.
Zistená pôvodná kamera je **2420020C**, nová **162A5806**. Pico identifikátory
sú **fdfb463297dcf38c** a **7e44fd2ac0cff44c**. Fyzické páry musí potvrdiť operátor.

## Vlastníctvo a izolácia

- WorkstationWindow skladá rovnaké pracovné panely MainWindow s explicitnými
  závislosťami; nepridáva alternatívnu vykonávaciu cestu pre ďalšie kamery.
- Každá stanica má vlastný InspectionController, InspectionRuntime, InspectionWorker,
  kamerový profil, CameraStream, PioCapture, PicoService, konfigurácie a databázu.
- Každý stream má vlastnú GLib.MainContext. Starý appsink/bus po zatvorení streamu
  nemôže dodať snímku ani zneplatniť nový PIO request. Každý Pico číta jediný RX thread.
- Párovanie odmieta duplicitnú kameru alebo Pico. V tejto implementácii nezávislých
  kontrol je Pico vyhradené jednej kamere; zdieľané Pico/kanály nie sú implementované.
- USB zariadenia sa hľadajú podľa jedinečného serial. Zmena videoN/ttyACMN po reštarte
  nemení logické priradenie. Chýbajúce alebo nejednoznačné zariadenie vyvolá chybu,
  nevyberie sa prvé iné dostupné zariadenie. Druhý UVC metadata uzol sa nepočíta ako kamera.
- Požiadavky, prevzaté snímky a výsledky majú camera_id; metadata navyše pico_id.
  Snaha použiť požiadavku či snímku inej stanice sa odmietne.
- Každá stanica prijme najviac jednu prebiehajúcu kontrolu. Zaneprázdnenie či výpadok
  jednej neblokuje prijímanie ostatných. Po chybe sa príslušná stanica znova pripraví cez RUN.
- Golden LIVE používa priamo stream priradenej kamery. Zrušený samostatný
  LivePreviewService už neotvára inú kameru cez globálne CAM_DEV ani nemení rozlíšenie.
- Reálne hardvérové časovanie je rovnaký CU55 PIO profil a existujúci Pico firmware;
  nový firmware sa kvôli viacerým staniciam nevyžaduje a nebol nahrávaný.

Každé ďalšie zariadenie spotrebuje ďalšiu USB priepustnosť, pamäť na obrazy a čas
výpočtu. Softvérová podpora tretej stanice nie je meranie výrobného taktu troch kamier.
Aktuálne overenie je softvérové; párovanie a následné fyzické merania zostávajú operátorovi
alebo ďalšiemu výslovne dohodnutému testovaniu. A7 sa týmto nespustila.

## Záverečné overenie

Celá regresná sada v Docker hdf_vision:dev: **472 passed**, jediný ImageIO
deprecation warning. Bez USB prístupu a bez produkčných dát. Zahŕňa kontroléry,
PIO protokol a firmware simuláciu, tri runtime, vlastníctvo dát, Qt rozhranie,
Golden Wizard a dva skutočné syntetické GStreamer zdroje.
Zmeny sú publikované v PR #358 do dev na výslovný pokyn používateľa.
Neboli buildnuté ani nasadené; hardvérové overenie zostáva odložené.
