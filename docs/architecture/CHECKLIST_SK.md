# Upratanie architektúry HDF_Vision

Začiatok: 2026-09-12. Aktuálny postup vždy čítaj v CHECKPOINT_SK.md v tomto priečinku.
Tento súbor je pracovný plán na viac posedení, nie potvrdenie vykonanej migrácie.

## Záväzné rozhodnutia používateľa

- Systematicky zjednodušiť architektúru, nie iba rozdeliť veľké súbory mechanicky.
- Rozšírené nastavenie nástroja možno odstrániť. Nemusíme udržiavať druhý editor.
- Staré recepty nemusia fungovať. Aplikácia ich musí vedieť jasne odmietnuť;
  nemusíme budovať prevodníky ani paralelnú legacy vykonávaciu cestu.
- Jetson GPIO sa definitívne nepoužíva. Odstrániť celý jeho kód, UI, závislosti
  a inicializáciu pri štarte. Toto NEZNAMENÁ odstrániť GP16/GP17 či vstupy na Pico.
- Priebežne zapisovať checkpointy pre obnovu práce po vyčerpaní limitu.
- Budúca aplikácia bude podporovať viac kamier a viac Pico na jednom Jetsone.
  Upratovanie má pripraviť rozhrania a vlastníctvo zdrojov; kompletná podpora
  viacerých zariadení nie je podmienkou dokončenia prvej etapy A1.
- Aktuálna požiadavka pri 21 % zostávajúceho limitu je pripraviť plán a checkpoint.
  V prípravnom posedení nemeníme runtime ani hardvér.

## Pravidlá realizácie

- Jedna ucelená etapa na pracovnú vetvu codex/…; každý výsledok musí byť reviewovateľný.
- Pred úpravou skontrolovať git stav, AGENTS.md a aktuálny checkpoint. Zachovať cudzie zmeny.
- Nezamieňať označenie legacy s dôkazom nepoužívania. Overiť importy, signály,
  továrne registrovaných nástrojov, skripty, testy a priame/alternatívne vstupy.
- Nemažeme používateľské recepty, vzorky/modely, /data ani historické výsledky.
  Odmietnutie starého formátu nie je povolenie vymazať súbory.
- Aktuálne funkčné recepty a ich formát najprv zmapovať. Definovať podporovaný
  kontrakt a až potom odstraňovať fallbacky. Podporu starých formátov netreba znova schvaľovať.
- Zachovať správanie pri čistom presune. Zmeny správania a opravy evidovať osobitne.
- Nenasadzovať nový Pico firmware ani nereštartovať hardvér len kvôli presunu Python kódu.
- Pred push/merge overiť konkrétne oprávnenie v aktuálnej konverzácii; samotný plán
  nepublikuje budúce etapy. Minulé schválenia sa týkali dokončených PR.
- Testy Python/Qt bežia v Docker hdf_vision:dev, nie v náhodnom hostiteľskom prostredí.
- Výsledky testov označiť presne; baseline zlyhanie nie je úspešný test.

## A0 – Inventúra a východiskový stav

- [x] Uložiť rozhodnutia, známe problémy a východiskový commit.
- [x] Zmerať najväčšie súbory a overiť konkrétne zvyšky Jetson GPIO.
- [ ] Spísať hlavné toky: spustenie, import/otvorenie receptu, RUN, externý trigger,
      výsledky, SETUP, golden, učenie, publikovanie receptu, ukončenie aplikácie.
- [ ] Zostaviť minimálnu opakovateľnú regresnú sadu a zaznamenať jej baseline.
- [ ] Overiť, či build/runtime nepoužíva iný checkout alebo obraz než pracovný projekt.
- [ ] Pred návrhom A3/A5/A6 zmapovať singletony, globálny stav a predpoklady jednej
      kamery/Pico; zdokumentovať rozhrania podľa pravidiel nižšie.

### Architektonické pravidlá pre viac zariadení

- Rozlíšiť logickú identitu kamery/Pico od aktuálneho USB portu či /dev/videoN,
  /dev/ttyACMN. Spôsob fyzickej identifikácie overiť na zariadeniach; ak chýba
  unikátny identifikátor, vyžadovať explicitné priradenie, nie prvé nájdené zariadenie.
- Pohľad receptu odkazuje na logickú kameru; konfigurácia pracoviska mapuje kamery,
  Pico a trigger/svetelné kanály. Nezakódovať povinne vzťah jedna kamera = jedno Pico.
  Konkrétne povolené zapojenia a možnosti FW sa určia pri implementácii.
- Každá kamera vlastní svoj stream, profil, stav MASTER/TRIGGER a pripravenosť.
  Každé Pico vlastní svoje spojenie, jediného čitateľa protokolu, čakajúce príkazy
  a koreláciu odpovedí. Zámky viazať na fyzický zdroj, nie globálne na aplikáciu.
- Koordinátor plánuje snímanie a rezervuje zdieľané zdroje (Pico, kanál, svetlo).
  Súbežnosť povoľovať iba tam, kde je bezpečná; sekvenčné snímanie musí byť možné.
  Nezamieňať pripravené rozhrania s prísľubom USB priepustnosti či počtu kamier.
- Požiadavka, snímka, výsledok aj chyba nesú identitu cyklu, pohľadu a kamery;
  hardvérové operácie navyše identitu Pico/kanála a relácie spojenia. Odpoveď zo
  starej relácie po reconnecte nesmie dokončiť nový príkaz.
- Strata zariadenia zneplatní jeho pripravenosť; koordinátor podľa závislostí
  rozhodne o zastavení kontroly. Neúplná kontrola nesmie skončiť ako OK.
  UI výber kamery nesmie sám prepínať fyzické režimy ostatných zariadení.
- Rozhrania prijímajú konkrétne služby/identity ako závislosti. Jedno zariadenie
  je bežný prípad tejto štruktúry, nie osobitná paralelná implementácia.
- Pri extrakcii služieb overiť dvojicou simulovaných kamier a Pico nezávislé stavy,
  správne smerovanie, kolíziu lokálnych čísel požiadaviek, reconnect a výpadok
  jedného zariadenia. Hardvérový výkon viacerých zariadení overiť v budúcej etape.

Dokončenie: každý ďalší krok má známy vstup, zachovávané správanie a overenie.
Nevyžaduje sa dlhotrvajúci úplný audit pred odstránením jednoznačného GPIO zvyšku.

## A1 – Úplne odstrániť Jetson GPIO (prvá implementačná etapa)

- [x] Odstrániť app/services/gpio_service.py a app/ui/gpio_wizard.py po kontrole odkazov.
- [x] Odstrániť Jetson.GPIO z requirements.txt a neaktuálne časti Dockerfile.
- [x] Z docker/run.sh odstrániť configure_gpio_runtime, configure-gpio argument
      a automatickú inicializáciu hostiteľských pinov pred kontajnerom.
- [x] Preveriť zvyšné aliasy/ochranné metódy v main_window.py, konfiguráciu,
      dokumentáciu, inštalačné skripty a ďalšie súbory mimo app/.
- [x] Odstrániť testy zrušenej služby; zachovať testy, že snímanie ide cez Pico.
- [x] Opraviť README; odstrániť iba oprávnenia/pripojenia potrebné výlučne pre GPIO.
      Kamera USB/HID, Pico USB CDC a NVIDIA runtime naďalej zostávajú potrebné.
- [x] Overiť syntax a simulovaný štart skriptu, import UI a Pico routing testami.
      Fyzické spustenie aplikácie a snímanie zostáva na produkčné overenie A7.
- [x] Vyhľadať zvyšné Jetson.GPIO/GPIOService/configure-gpio odkazy a každý vysvetliť.

Dokončenie: žiadna vykonateľná cesta aplikácie ani launcher neinicializuje Jetson piny;
Pico protokol, jeho piny a svetlo sú zachované. Historické vyšetrovacie správy sa neprepisujú.

## A2 – Jeden editor nástrojov

- [x] Zmapovať volania _edit_tool a ToolEditDialog vrátane dvojkliku na tabuľku.
- [x] Spísať funkcie dostupné iba v rozšírenom dialógu. Potrebné aktuálne schopnosti
      sprístupniť v bočnom paneli/canvase; nepresúvať automaticky zastarané možnosti.
- [x] Odstrániť tlačidlo Rozšírené nastavenie, druhú editačnú cestu a tool_edit_dialog.py.
- [x] Skontrolovať špecializované nastavovanie locatora, bodov A–B, ROI a masky,
      učenie OK/NOK, test nástroja a uloženie/znovuotvorenie nastavení.
- [x] Dvojklik alebo edit akciu smerovať na výber nástroja a jeho jediný editor.

Dokončenie: neexistujú dva nezávislé formuláre meniacie tú istú konfiguráciu nástroja.

## A3 – Jednoznačný podporovaný formát receptu

- [x] Zmapovať aktuálny import/export, RecipeV2, published/draft a otvorenie receptu.
- [x] Navrhnúť explicitnú verziu/kontrakt podporovaného receptu; názov triedy RecipeV2
      sám osebe nepreukazuje existenciu vynucovanej verzie súboru.
- [x] Rozhodnúť, čo tvorí aktuálny platný formát na základe dnešného exportu,
      nie podľa prítomnosti jediného voliteľného poľa.
- [x] Zaviesť kontrolu pred zmenou aktívneho receptu, kamerového profilu a pred učením.
- [x] Starý, novší neznámy alebo poškodený formát odmietnuť zrozumiteľným dôvodom.
      Nevytvárať potichu prázdny/default recept namiesto neplatného.
- [x] Otestovať odmietnutie bez poškodenia posledného funkčného aktívneho receptu.
- [x] Až následne odstrániť legacy parsing, aliasy a _run_legacy_trigger s testami.
- [x] Definovať odmietnutie zrušených typov (napr. absdiff) a odstrániť ich implementácie
      až keď už nie je možné nimi obísť validáciu vstupu.

Dokončenie: jedna cesta kontroly pre podporované recepty; staré recepty sa nespúšťajú
čiastočne. Prehliadanie historických výsledkov ostáva nezávislé od importu receptu.

A3 dokončená: kontrakt, odstránené implementácie a overenie sú v RECIPE_FORMAT_SK.md
a aktuálnom checkpointe. Známe baseline locator zlyhania rieši A4.

## A4 – Jednotné súradnice snímky a platnosť učenia

- [x] Zdokumentovať surovú, otočenú a zarovnanú snímku aj súradnice ROI/masiek.
- [x] Presunúť otáčanie/prípravu do jednej služby s explicitným kontraktom výsledku.
- [x] Overiť všetky rotácie, prevzatie snímky z iného pohľadu a rozdielne rozlíšenia.
- [x] Zjednotiť učenie a RUN vrátane locatora; určiť, kedy je referencia zarovnaná.
- [x] Naviazať model na relevantný profil/rotáciu/ROI/predspracovanie a odmietnuť
      nekompatibilný model s pokynom na nové učenie. Dáta automaticky nemažeme.
- [x] Udržať zobrazovanie ROI a filtrov bez ďalšieho otáčania a bez zmeny originálu.
- [x] Vyriešiť štyri baseline zlyhania locator testov: reprodukcia, koreňová príčina,
      oprava implementácie alebo odôvodnená oprava zastaraného testového kontraktu.
      Hotovo aj ďalšie dva locator testy; dôvody v FRAME_COORDINATES_SK.md.

Dokončenie: každý krok vie, v akých súradniciach dostáva/vracia obraz; syntetické
scény so známou polohou dokazujú zhodu učenia, RUN a náhľadu.

A4 dokončená 2026-09-13. Pravidlá modelov: LEARNING_CONTEXT_SK.md.
Záverečná Docker regresia: 174 passed; bez hardvérového snímania (A7).

## A5 – Riadenie kontroly mimo MainWindow

Dokončené 2026-09-13: stavový kontrolér, worker a samostatný produkčný runtime.
Kontrakt a presný rozsah: INSPECTION_CONTROLLER_SK.md.

- [x] Vytvoriť samostatný kontrolér pre prípravu, pripravenosť, pozastavenie a chybu.
- [x] RUN označiť pripraveným až po úspechu kamery/Pico; ošetriť zlyhanie prípravy.
- [x] Presunúť sekvencie, prijímanie triggerov, agregáciu a ukladanie výsledkov mimo UI.
- [x] Určiť správanie pri preťažení: neprijatie/čakanie musí byť viditeľné a merateľné;
      žiadne tiché strácanie ani neobmedzená fronta.
- [x] Výpočty a čakanie na hardvér nedržať v UI vlákne. Qt widgety meniť iba v UI vlákne.
- [x] Výsledky iba prehliadajú; SETUP pozastaví po potvrdení; ukončenie korektne uvedie
      Pico do IDLE. Výsledok patrí konkrétnemu triggeru a snímke.

Dokončenie: UI je klient kontroléra; testy vedia vykonať výrobný cyklus bez MainWindow.

Záverečná regresia A5/A4/PIO: 174 passed. Fyzické merania a záťaž patria A7.

## A6 – Rozdeliť služby a Golden Wizard

Dokončená A6 (2026-09-13). Podrobnosti A6_MODULES_SK.md. Záverečná sada: 452 passed.

- [x] tool_service.py: oddeliť dátové typy/protokoly, orchestrátor a implementácie SSIM/locator.
- [x] Golden Wizard: vytiahnuť ToolConfigPanel, správu pohľadov, učenie a náhľady.
- [x] Kamera: oddeliť stream, riadenie režimov a získanie produkčnej snímky;
      ponechať jeden aktuálny MASTER a jeden PIO TRIGGER tok.
- [x] ROI editor: najprv zmapovať zdieľané správanie, potom oddeliť kreslenie,
      geometriu, masky a históriu. Nezavádzať ďalšie kópie editorov.
- [x] Nahrádzať AST-extrakčné testovacie náhrady normálnymi importmi malých tried.
      UI harnessy teraz bindujú importované adaptéry; samostatné runtime triedy sa testujú priamo.
      AST v teste firmware ostáva zámerne iba na vynechanie nekonečného main loop.
- [x] Odstrániť modulové testové stuby, ktoré znečisťujú sys.modules a bránia spoločnému zberu testov.
      Firmware MicroPython náhrady sú iba fixture-scoped monkeypatch, nie globálny import shim.

Dokončenie: moduly majú jednu zodpovednosť a testovateľné rozhrania. Počet riadkov
je indikátor, nie cieľ; nepremiestňovať len rovnaký monolit do viacerých mixinov.

## A7 – Produkčné overenie a uzavretie

ODLOŽENÉ na výslovný pokyn používateľa 2026-09-13. Po A6 nasleduje podpora
druhej kamery a druhého Pico, každá dvojica spúšťa vlastnú nezávislú kontrolu.

- [ ] MASTER/TRIGGER prepínanie, externé Pico/Modbus vstupy, viac pohľadov, učenie.
- [ ] Výsledky počas záťaže, export, návrat do RUN bez prípravných pulzov.
- [ ] Počítať vstupy, prijaté/odmietnuté požiadavky, dokončené kontroly a chyby snímania.
- [ ] Zmerať odozvu UI, pamäť a časy kontrol s náhľadom filtrov aj bez neho.
- [ ] Overiť studený štart; skontrolovať skutočný Pico firmware po reštarte
      (laboratórne testy pôvodne používali FW4 iba v RAM, aktuálny flash stav treba zistiť).
- [ ] Dokumentácia zodpovedá implementácii, žiadne nevysvetlené runtime fallbacky.
- [ ] Finálny checkpoint obsahuje commity, PR, testy, známe zvyšky a stav hardvéru.
