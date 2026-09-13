# Aktuálny checkpoint architektúry

Aktualizované: 2026-09-13. Stav: A1–A6 HOTOVÉ; podpora nezávislých kamerových staníc implementovaná, A7 ODLOŽENÁ.
Primárny checklist: CHECKLIST_SK.md v tomto priečinku.

## Aktuálny rozsah a ďalší krok

Používateľ žiadal dokončiť A6, A7 vynechať a implementovať druhú kameru s druhým Pico.
Potvrdil vlastné nezávislé kontroly; potom sa pýtal na tretiu kameru. Implementácia
preto umožňuje pridávať ďalšie stanice. Fyzické párovanie urobí používateľ v aplikácii;
žiadne serial páry nehádame. Kontrakt a postup: MULTI_CAMERA_SK.md.

1. Zachovať všetky lokálne A1–A6 a multi-camera zmeny na codex/remove-jetson-gpio.
2. Skontrolovať najnovšie výsledky testov na konci tohto checkpointu.
3. A7 nezačať bez nového pokynu. Fyzické kamery/Pico neboli v tomto posedení testované.
4. Používateľ pri spustení vyberie a overí dvojice v okne Kamery a Pico. Existujúce
   dáta prvej stanice zostávajú /data, ďalšie stanice majú /data/stations/camera_N.
5. Používateľ následne schválil push a merge. Publikácia: PR #358 do dev;
   stav merge overiť na GitHube. Aplikáciu ani hardvér nereštartovať.

## Východiskový stav

Repo: /home/hdf-jetson1/HDF_Vision
Vetva pri príprave: dev
HEAD: e6cfe625e5e2ecda03c221f75060da9318f9230e (merge PR #357).
Pracovný strom bol pred prípravou čistý. Toto posedenie vytvorilo iba dokumentáciu.
Všetky skoršie funkčné zmeny až po katalóg a opravu učenia sú mergnuté do dev.
Historické formulácie „lokálne/bez merge“ v starších checkpointoch opisujú vtedajší
stav a NEMAJÚ znamenať, že treba znovu publikovať PR #352–357.

## Pôvodné nálezy inventúry (GPIO položky vyriešené v A1)

- docker/run.sh má configure_gpio_runtime, import Jetson.GPIO, nastavovanie
  BOARD vstupov/výstupov, configure-gpio argument a automatické volanie pred kontajnerom.
  Nejde iba o nepoužívaný Python súbor – launcher má aktívnu GPIO inicializáciu.
- requirements.txt obsahuje Jetson.GPIO; Dockerfile a README majú súvisiace texty.
- app/services/gpio_service.py (711 riadkov), app/ui/gpio_wizard.py,
  tests/test_gpio_service.py. V main_window.py je aj ochranný zvyšok pre staré integrácie.
- Golden Wizard importuje ToolEditDialog, vytvára _btn_legacy_edit a _edit_tool stále
  otvára druhý editor. Najprv preveriť všetky volania, nielen odstrániť tlačidlo.
- Veľkosti pri príprave: golden_wizard.py 4661, roi_mask_editor.py 4020,
  main_window.py 3475, tool_service.py 2349, tool_edit_dialog.py 2348,
  camera_service.py 1606, draw_view.py 1421, tool_registry.py 1257, schema.py 1154.

## Chránené správanie a známe medzery

- Výsledky otvorené z RUN nesmú meniť runtime režim/sekvenciu/pripravenosť kamery.
- SETUP vyžaduje potvrdenie pozastavenia; Pico riadi svetlo aj fyzické impulzy.
- Overené PIO časovanie a výnimka 1080p streamu sú v docs/PIO_APP_INTEGRATION_SK.md
  a docs/PIO_VALIDATION_20260911_SK.md. Nepokaziť ich náhodným zjednotením reopen toku.
- Zber učenia teraz rešpektuje rotáciu pohľadu; staré zle zachytené vzorky sa samy
  neopravia. Dáta zostali zachované. Podrobnosti docs/CATALOG_AND_LEARNING_ROI_SK.md.
- Filtrované ROI je iba náhľad; nezapisuje originál ani nemení výsledok kontroly.
- Otvorené kandidáty: duplicitná rotácia snímky prevzatej z iného pohľadu,
  RUN nastavený pred dokončením prípravy kamery, platnosť modelu po zmene geometrie.
- Štyri locator testy v tests/test_tool_pipeline.py zlyhávali aj na pôvodnej verzii.
  Záznam /tmp/hdf_filtered_baseline.log; /tmp môže zmiznúť, preto výsledok zopakovať podľa potreby.
- Známy test masky očakával transparentný pixel [9,9] napriek hodnote 1 vo vstupnej
  maske. Nedávať úspešný stav testov len tým, že sa taký prípad vynechá bez vysvetlenia.
- Záťaž histórie + produkcie zatiaľ nebola hardvérovo overená.

## Testovacie prostredie

Docker image hdf_vision:dev, pytest v .docker-cache/pytest (ignorované súbory).
Príklad (hardware nie je potrebný):

```sh
docker run --rm --network none --user 1000:1000 \
  -v /home/hdf-jetson1/HDF_Vision:/workspace -w /workspace \
  -e PYTHONPATH=/workspace/.docker-cache/pytest:/workspace \
  -e QT_QPA_PLATFORM=offscreen --entrypoint python3 hdf_vision:dev \
  -m pytest tests/test_catalog_learning_update.py tests/test_run_page_navigation.py -q
```

Docker/Git mutácie môžu vyžadovať sandbox escalation. Nežiadať používateľa znovu
schvaľovať bežné autorizované úpravy; pri odmietnutí automatickým reviewerom uviesť
konkrétny dôvod. Testové /data zabezpečiť v dočasnom priestore, nemontovať produkčné
recepty na zápis kvôli testom. Niektoré staršie sady vytvárajú modulové stuby;
spoločné spustenie môže zlyhať pri importe, aj keď oddelené sady prejdú.

Posledné funkčné overenie pred plánom: 65 testov katalógu/učenia/formulárov prešlo,
log /tmp/hdf_catalog_learning_final3.log. Príprava dokumentácie testy neopakovala.

## Hardvér v tomto posedení

Žiadne snímanie, inicializácia GPIO, nahrávanie FW ani reštart zariadení neboli
vykonané. Aktuálny stav bežiacej aplikácie/kamery/Pico sa neoveroval.

## Šablóna ďalšieho checkpointu

- Dátum, etapa, vetva a HEAD:
- Dokončené a presné súbory:
- Rozpracované (čo už zmenené, čo ešte nefunguje):
- Testy: príkaz/sada, výsledok, baseline zlyhania, uložené logy:
- Hardvér: aktívny test/proces, kamera, svetlo, Pico RAM/flash stav, ak sa overoval:
- Git: lokálne/commit/push/merge, PR:
- Rozhodnutia a prípadné blokery:
- PRESNÝ NAJBLIŽŠÍ KROK:

Checkpoint aktualizovať po každej etape, významnom náleze a pred prerušením.
Ak beží dlhý test, zapísať jeho proces/session a priečinok výsledkov ešte pred čakaním.

## Checkpoint A1 – 2026-09-12

- Vetva: codex/remove-jetson-gpio, základ HEAD e6cfe625e5e2ecda03c221f75060da9318f9230e.
- Stav: lokálne zmeny, bez commitu/push/merge. Dokumentácia prípravy je súčasťou stromu.
- Odstránené gpio_service.py (711 riadkov), gpio_wizard.py (472), ich test a
  nepoužívaná _send_run_trigger_gpio_pulse. Žiadny volajúci týchto modulov nezostal.
- docker/run.sh už neinicializuje piny ani neakceptuje configure-gpio.
  requirements.txt/Dockerfile bez závislosti; README a repo-tree aktualizované.
- Diagnostický label manual_gpio premenovaný na manual_trigger v kamere/testoch;
  Modbus komentár už neodkazuje na odstránený GPIO pipeline.
- Vyhľadanie GPIO v app/, docker/, requirements.txt a tests/ bez zdrojových nálezov.
  Zvyšné odkazy v dokumentácii opisujú históriu alebo rozhodnutie o odstránení.
- 85 testov prešlo: test_pico_service, test_pico_pio_protocol, test_pico_firmware_v4,
  test_external_trigger_routing, test_main_window_pico_trigger, test_run_page_navigation.
- V samostatných procesoch prešlo 19 test_camera_service_trigger_timing a
  9 test_catalog_learning_update. Spoločný zber týchto dvoch súborov zlyhá na
  globálnom cv2 stube bez MORPH_ELLIPSE; existujúcu izoláciu rieši etapa A6.
- Spolu 113 úspešných testov v Docker hdf_vision:dev bez pripojeného hardvéru.
- bash -n a git diff --check prešli. Simulovaný launcher (dočasné náhrady docker,
  xhost, python3) zachoval USB/NVIDIA argumenty, nespustil host Python a odmietol
  configure-gpio kódom 2 pred volaním Docker. Nespúšťal sa produkčný kontajner.
- Pico FW/protokol ani snímací algoritmus sa nemenili; používateľské dáta zachované.
- Existujúci Docker image nebol rebuildnutý: môže stále obsahovať nainštalovaný
  Jetson.GPIO, aplikácia ho však neimportuje. Nový build už závislosť nepožaduje.
- Fyzický štart, MASTER/TRIGGER a cold boot neboli teraz testované; ostávajú A7.
- PRESNÝ ĎALŠÍ KROK: A2, inventúra schopností druhého editora pred jeho odstránením.

## Checkpoint A2 – 2026-09-12

- Lokálne na codex/remove-jetson-gpio spolu s A1; bez commitu/push/merge.
- Odstránený app/ui/golden_wizard/tool_edit_dialog.py (2348 riadkov) vrátane
  TemplateRoiEditor, AngleRoiEditor a EdgeAnchorEditor. Nikto ďalší ich neimportoval.
- Golden Wizard nemá tlačidlo Rozšírené nastavenie. Riadková akcia _edit_tool
  vyberie spoločný panel/plátno; hrana prepne kontext A–B. Tabuľka má NoEditTriggers,
  nemala samostatný signál dvojkliku otvárajúci dialóg.
- Inventúra schopností: parametre/prahy/defaulty/test už ToolConfigPanel;
  ROI/maska/search/template/A–B už LocatorROIEditor a workspace callbacky;
  učenie/validácia/tuning už panel + Golden callbacky. Zapnutie zostáva v tabuľke.
- Chýbajúce premenovanie doplnené do ToolConfigPanel ako Názov nástroja.
  Ukladá až editingFinished, ignoruje prázdny názov, kopíruje Tool pred zápisom,
  smeruje update_tool na vybraný view, obnoví panel pri chybe.
- Staré angle_roi/angle_enabled ovládanie z dialógu sa neprenáša: aktuálny locator
  používa alignment_mode a referenčnú hranu v plátne. Legacy výpočet/parser sa
  odstraňuje až v A3 po zavedení jasného odmietania starých receptov.
- Test dialógu v test_image_canvas nahradený roundtrip testom skutočného
  LocatorROIEditor (používaného vo wizardovi), ROI + maska + pan/zoom + JSON.
- Nový tests/test_single_tool_editor.py: 7 testov premenovania a smerovania pre
  SSIM, locator, hranu, ochranu formy a prítomnosť V2.
- Finálna cielená Docker sada 23 passed: test_single_tool_editor,
  test_image_canvas::test_workspace_roi_mask_json_roundtrip,
  test_catalog_learning_update, test_filtered_roi_canvas, test_golden_wizard_trigger_path.
- Širší beh identifikoval 4 existujúce zlyhania test_image_canvas: zaškrtnutie DRAW,
  redo po nakreslení ROI, escape počas kreslenia pre existujúcu/prázdnu ROI.
  Reprodukované pôvodným testom z git show HEAD:tests/test_image_canvas.py
  (/tmp/test_image_canvas_baseline_a2.py) na nezmenenom ROIEditor/image_canvas kóde:
  4 failed, 2 passed, 16 deselected. Nesmú sa vydávať za prechádzajúcu regresnú sadu.
  Riešiť pri A6 (história/interakcie plátna); neodstránili sa testy ani assertiony.
- git diff --check prešiel; žiadny odkaz na ToolEditDialog/tool_edit_dialog/legacy_edit
  v app/tests/README/repo-tree. Historické dokumenty zachované.
- Bez fyzického snímania, FW zmien alebo produkčného štartu. Vizuálna kontrola
  na fyzickom displeji ešte neprebehla.
- PRESNÝ ĎALŠÍ KROK: A3, kontrakt receptu a validácia pred zmenou aktívneho stavu.

## Checkpoint A3a – 2026-09-12

- Lokálne na codex/remove-jetson-gpio spolu s A1/A2, bez commitu/push/merge.
- Nový modul recipe_format.py; formát 3 v schema.py; diskové draft/published
  vstupy aj zápisy validované. Chýbajúci súbor už nie je default recept a service
  neperzistuje fallback. Podrobnosti RECIPE_FORMAT_SK.md.
- RecipeService.load validuje pred DB zmenou; ToolService.load_recipe validuje
  pred priradením aktívneho stavu a dekóduje golden pred priradením. Prázdny nový
  koncept môže existovať bez golden. regions.json/thresholds.json sa už nenačítavajú.
- save_regions ukladá do recipe.json; nepíše duplicitný regions.json. DB threshold
  integrácia zatiaľ zostáva, jej širšie odstránenie nie je súčasť tohto kroku.
- Odstránený MainWindow._run_legacy_trigger a jeho fallback, ToolService.evaluate,
  nepoužívané DbService JSON get/set/save helpers a ich osamotený backup helper.
- _prepare_run_trigger overuje formát pred pause kamery. UI pri odmietnutí
  ponechá aktívny ToolService, vráti combo výber a zobrazí dôvod. Startup chybu
  zapisuje aj do status labelu. Pri výbere sa sekvencie resetujú až po úspechu load.
- create odmieta existujúci názov/priečinok pred zmenou DB, neprepisuje starý recept.
- tests/test_recipe_format.py pokrýva verzie, poškodené štruktúry/JSON, chýbajúci
  súbor, odkazy, NaN, zachovanie aktívneho stavu/DB/súboru, roundtrip publikovania,
  chybnú golden a odmietnutie pred pause kamery.
- 28 RUN/external/Pico/navigation/Golden trigger testov prešlo v oddelenom Dockeri.
- Zostáva A3b: fyzicky odstrániť absdiff/ssd/light_presence implementácie a aliasy,
  legacy_angle výpočet a modelové fallbacky. Zatiaľ sú odmietané na hranici súboru,
  ale nie všetky interné konštruktory ich zakazujú. Neoznačiť celú A3 za dokončenú.
- Nebol build, hardvérové snímanie ani modifikácia používateľských receptov.
- PRESNÝ ĎALŠÍ KROK: A3b, inventúra registrácií/volaní zakázaných typov a parserov.
- Finálna A3a sada: 44 passed (recipe_format, recipe_audit, recipe_aggregation,
  single_tool_editor, catalog_learning_update), jedna ImageIO deprecation pri
  úmyselne poškodenej golden. S oddelenou RUN sadou spolu 72 passed. Diff check OK.

## Checkpoint A3 dokončená – 2026-09-12

- Vetva codex/remove-jetson-gpio, základ e6cfe625e5e2ecda03c221f75060da9318f9230e.
  A1/A2/A3 ostávajú lokálne spolu, bez commitu/push/merge; pracovný strom obsahuje
  aj doteraz nesledované architektonické dokumenty, modelové kontrakty a nové testy.
- Autoritatívny kontrakt: RECIPE_FORMAT_SK.md. Staršie sekcie A3a sú historický stav.
- Odstránené SSD/AbsDiff/LightPresence, alias template_match a mechanizmus aliasov.
  Kontrola otvoru je samostatný katalógový preset.bright_opening → presence_absence.
- Odstránený legacy_angle a prevod rotation_enabled; ponechaný aktuálny translation,
  template_rotation/guided_edge. Testovacie fixture používajú alignment_mode.
- Model Tool a runner odmietajú zrušené typy/parametre aj pri internom volaní.
  Oba orchestrátory vopred validujú typy celej pipeline. Priama locator funkcia
  má tiež guard; nezostáva vykonateľná stará vetva.
- RecipeV2.from_dict vyžaduje verziu/validnú štruktúru, odstránené from_recipe_data,
  staré view aliasy/modbus_input, parser ndarray masiek, params ROI/mask fallback,
  run_tool_isolated a serializácia starého RecipeData. RecipeData ostáva malý
  interný objekt pre ukladanie regiónov Golden Wizardom.
- Export má iba views[].tools; vrchné tools je odmietnuté. UI zapisuje ROI/masku
  priamo do nástroja. Template ROI používa aktuálny existujúci modelový kontrakt.
- Odstránená synchronizácia globálnych prahov cez DB a thresholds.json.
  Prahy aktuálnych nástrojov ostávajú v recipe.json; existujúce historické dáta nemažeme.
- Validácia pred golden/učiacim snímaním a pred kamerovým profilom zabraňuje
  použitiu starého profilu z cache pri poškodenom recepte. Pri importe masky
  poškodený blob vyvolá RecipeFormatError namiesto zrušenia masky.
- Kontrakt oddelený do modelov recipe_contract.py a recipe_document.py;
  services/recipe_format.py pridáva I/O a kontrolu registrovaných typov.
  Modelová vrstva neimportuje služby pri parsovaní.
- Hlavná cielená Docker sada: 106 passed (recipe_format, recipe_audit,
  catalog_learning_update, view_configuration, recipe_aggregation, single_tool_editor,
  pair_tool_cache, presence_absence, filtered_roi, filtered_roi_canvas,
  golden_wizard_trigger_path, slovak_tool_labels).
- Oddelená Pico/RUN sada: 85 passed (external_trigger_routing, main_window_pico_trigger,
  run_page_navigation, pico_service, pico_pio_protocol, pico_firmware_v4).
- Doplnková sada po pridaní roundtrip testu všetkých registrovaných typov a kontroly
  absencie starých úložísk: 68 passed (recipe_format, recipe_audit,
  db_service_today_filters, manual_light_ui, view_config_pio). Sady sa prekrývajú;
  čísla NESČÍTAVAŤ ako počet unikátnych testov. ImageIO deprecation pri úmyselne
  poškodenej golden je jediný warning týchto sád.
- Baseline z git archive HEAD do /tmp/hdf-a3-baseline: test_locator_template_match
  + test_tool_pipeline = 6 failed, 9 passed. Rovnaký výsledok aj na aktuálnom kóde
  (aktuálny beh s izolovaným tmpfs /data). Názvy zlyhaní:
  test_locator_template_match_detects_rotation; test_locator_template_cache_reuses_downsample;
  test_locator_updates_context_with_alignment; test_ssim_benefits_from_aligned_frame;
  test_pipeline_alignment_modes_produce_consistent_ssim; test_pipeline_handles_locator_rotation.
  Štyri pipeline už známe; ďalšie dva locator testy teraz reprodukované na baseline.
  Tieto testy neboli vypnuté ani zmäkčené; A4 začne ich vyšetrením. Baseline kontajner
  nemal zapisovateľné /data (chyby loggera), aktuálny má tmpfs; assertiony sú zhodné.
- Zvyšné výskyty absdiff v imaging/compare sú matematická operácia aktuálnych
  nástrojov, nie odstránený nástroj. Historické zobrazovacie názvy/nok metriky a
  vyšetrovania sa neprepisujú. Obranné výpočtové fallbacky nie sú staré parsery.
- Bez hardvérových impulzov, FW zásahov, buildu alebo zápisov do produkčných dát.
- PRESNÝ ĎALŠÍ KROK: A4, reprodukovať uvedené locator chyby a zmapovať raw/rotated/
  aligned súradnice medzi RUN, učením a náhľadom; zmeny testovať na známom obraze.

## Checkpoint A4a – 2026-09-13

- Rovnaká vetva codex/remove-jetson-gpio; A1–A4a lokálne, bez commitu/push/merge.
- Nový services/frame_coordinates.py: InspectionFrame(image, rotation) a
  reused_view_frame. Matica obrazu je kontrolná, pred locatorom/anotáciami.
- MainWindow._execute_view_trigger ukladá záznamy v captured_frames a pri vetvení
  odovzdá injected_capture. Transformácia medzi pohľadmi je rozdiel uhlov.
- Odstránená náhrada chýbajúceho zdroja starým preview/capture. Zdroj musí patriť
  aktuálnemu trigger_state; chyba ide do existujúceho error/NOK handlera.
- test_frame_coordinates: 16 kombinácií rotácií na nesymetrickom obraze, kópie,
  reťazenie vetiev v RGB, chýbajúci zdroj a záznam bez orientácie.
- Všetkých 15 testov locator/pipeline prešlo po oprave zastaraných fixture a
  znamienka/stredu rotácie. Algoritmus locatora sa nemenil. Podrobné odôvodnenie
  každej opravy v FRAME_COORDINATES_SK.md; žiadne zlyhanie nie je len vynechané.
- Súvisiaca sada 52 passed: frame_coordinates, locator_template_match,
  tool_pipeline, filtered_roi, catalog_learning_update. Docker, tmpfs /data.
- Bez hardvérových testov alebo zmien FW. Používateľské dáta zachované.
- A4 NIE JE CELÁ DOKONČENÁ: učenie s predchádzajúcim locatorom, invalidácia modelov
  a oddelenie prípravy snímok od UI zostávajú. Pri manuálnych/external viacvstupových
  cykloch znovupoužitie zdrojovej snímky musí dostať explicitnú životnosť v A5.
- PRESNÝ ĎALŠÍ KROK: porovnať _capture_presence_learning_frame/_on_presence_v2_learning
  s prípravou pipeline v RUN; zaviesť spoločnú cestu a podpis platnosti modelu.
- Záverečná oddelená regresia RUN/Pico/ROI: 40 passed (run_roi_rotation,
  main_window_pico_trigger, external_trigger_routing, run_page_navigation,
  golden_wizard_trigger_path). git diff --check prešiel.

## Checkpoint A4b – 2026-09-13

- Lokálne na codex/remove-jetson-gpio spolu s A1–A4a; bez commitu/push/merge.
- LearningSamplePreparer v services/statistical_learning.py: spustí iba aktuálne
  povolené locatory cez RUN pipeline, následne PairTool prípravu ROI a masky.
  Neplatná ROI/rozmery/locator bez zhody/žiadne platné pixely → odmietnutie vzorky.
- Golden Wizard crop callback teraz používa túto službu namiesto obyčajného výrezu.
  Capture callback už vracia otočenú snímku, služba ju znova neotáča.
- PresenceV2SampleCaptureDialog zachytáva chyby prípravy, zastaví auto timer,
  zobrazí dôvod a nezaradí chybnú vzorku. Platí pre ochranu formy aj V2.
- PairTool._prepare_pair opravený pri virtuálnom zarovnaní: ignoruje zdanlivo
  zarovnanú cache, ak frame_is_aligned=False; aplikuje inverznú T_total.
- models/learning_contract.py SAMPLE_PREPARATION_VERSION=2. Nové vzorky nesú
  verziu v parametroch, model stats tiež. Staré modely sa odmietajú, staré vzorky
  nemožno prepočítať novou cestou. Reset ostáva explicitný s potvrdením používateľa.
- Runtime V2 konečne rešpektuje reference_model_invalidated aj pri ready=True.
- tests/test_statistical_learning porovnáva skutočný vstup evaluate_sample v RUN
  s uloženou vzorkou a golden výrezom pre obidva typy a fyzické/virtuálne zarovnanie.
  Ďalšie prípady: zlyhaný/zakázaný locator, invalidovaný/starý model a chyba auto zberu.
- Hlavná sada 85 passed (statistical_learning pred doplnením testu dialógu,
  pair_tool_cache, filtered_roi, catalog_learning_update, tool_pipeline,
  locator_template_match, recipe_format), ImageIO deprecation pri poškodenom PNG.
- Bez snímania na hardvéri, FW zmien, buildu alebo zásahu do produkčných modelov.
- ZOSTÁVA A4c: verzia prípravy neodhalí zmenu profilu/rotácie/golden/locator parametrov.
  Potrebný deterministický podpis vzoriek a modelu a jeho kontrola v RUN/učení.
  Podpis nesmie zahŕňať dynamické počítadlá/ready flagy ani iba zobrazovacie zmeny.
  Nutné odmietnuť miešanie nekompatibilných dávok aj rebuild starých vzoriek.
- PRESNÝ ĎALŠÍ KROK: navrhnúť learning context manifest uložený pri vzorkách,
  rovnaký výpočet pri rebuild aj v runtime; kontrolovať relevantné view nastavenia
  a predchádzajúce locatory bez závislosti služby na Qt.
- Finálna doplnková sada 39 passed: statistical_learning vrátane chyby dialógu,
  single_tool_editor, golden_wizard_trigger_path, frame_coordinates. Diff check OK.


## Checkpoint A4 dokončená – 2026-09-13

- Vetva codex/remove-jetson-gpio, základ HEAD e6cfe625e5e2ecda03c221f75060da9318f9230e.
  A1–A4 sú lokálne, bez commitu/push/merge. Neresetovať predchádzajúce zmeny.
- Nový services/learning_context.py: deterministický SHA-256 podpis golden pixelov,
  capture profilu/rotácie/Pico časovania, ROI/masky a usporiadaných aktívnych locatorov.
  Verzia prípravy 2 + verzia kontextu 1. Žiadne globálne cache/identity zariadení.
- sample_context.json pri vzorkách, rovnaký learning_signature v stats.json modelu.
  Bez manifestu alebo pri nezhode nemožno miešať dávky ani prepočítať staré vzorky.
  Reset dát zostáva explicitný s potvrdením. Nové PNG majú jedinečné názvy aj pri
  uložení celej dávky v jednej milisekunde (pôvodné názvy sa mohli prepísať).
- Oba orchestrátory (RUN/test) počítajú podpis z reálneho golden a receptu a
  odovzdávajú ho runneru; hash golden sa počíta iba raz na volanie, ak sú aktívne
  štatistické nástroje. Uložený ready flag sám nestačí na spustenie modelu.
- Po zlyhaní locatora sa štatistický model nevyhodnotí ani pri politike pokračovania
  bez zarovnania. V2 vráti WARN, ochrana formy NOK s pôvodným dôvodom chyby.
- Golden Wizard kontroluje podpis pri obnove panelu, zbere, rebuild, validácii aj
  aplikovaní odporúčaní. Nová dávka vyžaduje rebuild, zachováva staré súbory modelu.
  Premenovanie zachová existujúcu cestu vzoriek v rámci pohľadu. Rozhodovacie prahy,
  názvy a počítadlá nie sú súčasťou podpisu prípravy pixelov.
- Golden test používa aktuálny view profil a všetky locatory tak ako RUN.
  UI wrapper view_utils už otáča cez InspectionFrame, rovnaký algoritmus používa
  prevzatie snímky medzi pohľadmi. LearningSamplePreparer + PairTool z A4b zachované.
- Nové tests/test_learning_context.py a rozšírené test_statistical_learning.py:
  zmeny golden/profilu/rotácie/Pico/ROI/masky/locatorov, zachovanie po premenovaní
  a prahoch, obe runtime cesty, starý manifest, rebuild a zachovanie dát, 20 rýchlo
  uložených snímok a zlyhanie zarovnania pre oba štatistické nástroje.
- Záverečná Docker sada: 174 passed, 1 ImageIO deprecation warning pri úmyselne
  poškodenom PNG. Súbory: learning_context, statistical_learning, view_configuration,
  run_roi_rotation, recipe_format, tool_pipeline, locator_template_match,
  frame_coordinates, pair_tool_cache, filtered_roi, filtered_roi_canvas,
  single_tool_editor, catalog_learning_update, golden_wizard_trigger_path.
  Spustené v hdf_vision:dev s tmpfs /data, bez siete/hardvéru. git diff --check OK.
- Žiadne snímanie, FW zmeny, build, reštart aplikácie ani zápisy do produkčných dát.
  Nové kontextové podpisy znamenajú nové učenie starých modelov; žiadna migrácia.
- Podrobnosti: FRAME_COORDINATES_SK.md, LEARNING_CONTEXT_SK.md.
- PRESNÝ ĎALŠÍ KROK: A5, najprv malý kontrolér prípravy/pripravenosti RUN mimo UI.
  Zmapovať existujúce volania pred presunom; nezmeniť overené PIO časovanie.
  Budúce viac zariadení a zdrojové pohľady potrebujú explicitné identity/cykly.
  Hardvérová záťažová validácia patrí A7; staré canvas baseline problémy ostávajú A6.
- Finálna drobná optimalizácia A4c: podpis číta iba capture polia pohľadu, nie
  view.to_dict() so serializáciou všetkých nástrojov/masiek pri každej kontrole.
  Následná cielená sada learning_context + statistical_learning: 36 passed.

## Rozpracovaná A5 – 2026-09-13

- Používateľ požiadal vykonať A5. Zatiaľ NIE JE CELÁ HOTOVÁ.
- services/inspection_controller.py: instance-owned stavový automat, thread-safe
  admission jednej požiadavky bez fronty, vlastníctvo tokenu, počítadlá, konečné
  vetvenie sekvencie s detekciou cyklu. Bez Qt a bez globálnej kamery.
- MainWindow integrácia: startup/SETUP→RUN až po úspešnej príprave a striktnom
  profile; prijatie externého eventu rezervuje token pred Qt emit. manual_trigger
  používa controller.run_sequence a finish v finally. Chýbajúci frame vyvolá NOK
  chybu, výsledkové metadáta dostávajú request_id/source/input.
- Cielené testy controller + navigation + Pico trigger + routing: 29 passed.
- Práve zostáva preveriť zlyhanie arm_master_frame pri rezervovanom tokene,
  ostatné capture-mode prechody a testy nových chybových ciest.
- A5 hlavný zostávajúci rozsah: oddeliť capture/výpočet/úložisko od widgetov
  v _execute_view_trigger a presunúť ich do workeru, doručenie dát cez Qt signály.
  Aktuálnu _execute_view_trigger NESPUSTIŤ v pracovnom vlákne: stále volá widgety!
  Agregácia/persistencia ešte čiastočne v MainWindow; sekvenčné stavy tiež.
- Nevykonaný hardware test/build/push/merge. A1–A5 zmeny na rovnakej lokálnej vetve.


## Checkpoint A5a dokončená – 2026-09-13

- Základný stavový kontrolér a admission sú integrované a otestované. A5 ako celok
  stále NIE JE HOTOVÁ; zostávajú štyri checkboxy v checkliste.
- Startup používa SETUP, RUN až po úspešnej príprave a striktnom profile.
  PIO príprava po profile je cez existujúce idempotentné prepare_trigger API,
  žiadny nový low-level mode príkaz alebo timing algoritmus.
- Vstupy rezervujú controller token ešte pred external_triggered.emit. Počas BUSY
  sa nefrontujú: počítadlo odmietnutí a log, obmedzené UI oznámenie. Aktuálne
  počítadlá sú v inspection.snapshot(); nejde ešte o hotový operátorský dashboard.
- Metadáta výsledku obsahujú request_id, trigger_source a trigger_input_index.
  run_sequence vlastní poradie/vetvenie, opakovaný pohľad v tej istej vetve odmietne
  ako cyklus. Chýbajúci frame už nefinalizuje čiastočné per-view výsledky ako úspech.
- Zlyhanie arm_master_frame uvoľní rezervovaný token do ERROR, vyšle NOK,
  skúsi Pico IDLE, nikdy nezaradí capture event. Controller po chybe neprijíma
  ďalšie kontroly, kým operátor neobnoví RUN cez SETUP/prípravu.
- SETUP/close zatvárajú admission pred IDLE. BUSY/PREPARING zatiaľ neumožnia
  pozastavenie/close; nevykonávajú konkurenčný zásah do rozpracovaného capture.
- MainWindow._apply_capture_mode obaluje RUN prekonfiguráciu stavovou prípravou,
  _configure_capture_mode ostáva UI adaptér staršieho nastavenia tlačidiel.
- 79 passed: inspection_controller, inspection_window_lifecycle, run_page_navigation,
  main_window_pico_trigger, external_trigger_routing, golden_wizard_trigger_path,
  recipe_format. ImageIO deprecation pri úmyselne poškodenom PNG.
- Nové testy importujú controller a lifecycle UI adaptér normálne. Dve existujúce
  AST sady upravené na reálny admission token; ich úplné odstránenie patrí A6.
- A5b musí riešiť: _prepare_run_trigger (recept/routing), _execute_view_trigger
  (capture/pipeline/persistencia premiešané s widgetmi), _finalize_run_trigger
  (agregácia/Modbus/UI), DB spojenie vlastní worker (aktuálna DbService má SQLite
  connection vytvorenú v UI vlákne, nesmie sa len preposlať workeru!).
- Nepreniesť MainWindow alebo jeho widgety do workeru. Oddeliť vstupný snapshot
  konfigurácie a návratové dátové udalosti; filtrové náhľady čítať zo snapshotu.
  Pri asynchrónnom cykle doplniť blokovanie zmien profilu cez výber pohľadu/Live,
  koordinované pause/close, explicitnú životnosť viacvstupového cyklu a stavov.
- Hardvér/build/FW/produkčné dáta/push/merge: bez zásahov. Lokálne A1–A5a.
- Záverečná cielená sada po doplnení NOK/IDLE chyby rezervácie: 33 passed.
  Syntaktická kontrola a git diff --check prešli. Žiadny test/proces nezostal bežať.

## Rozpracovaná A5b – 2026-09-13, priebežný checkpoint

- InspectionRuntime v services/inspection_runtime.py teraz vlastní routing,
  capture, pipeline, agregáciu, výsledkové súbory a vlastné SQLite spojenie.
  MainWindow dostáva records/pending_overlays/status, renderuje ich na UI vlákne.
- InspectionWorker v ui/inspection_worker.py: jedna kapacita, jeden executor,
  queued doručenie na vlastnícke Qt vlákno. RUN príprava a stop/close sú worker jobs.
- Odstránené produkčné metódy z MainWindow; Golden SETUP capture adaptér zatiaľ
  zostáva samostatný (A6), nespúšťa sa počas runtime worker busy.
- Sequence context uchováva cycle_id/captured_frames/frame_ids pre konkrétny zdroj
  a krokovanie. request_id je zvlášť na trigger. Pri chybe/pause sa kontexty resetujú.
- Zatiaľ 79 testov prešlo po presune, pribudli ďalšie testy waiting pause/SQLite
  ownership, ktoré treba teraz spustiť. Nefinalizovať A5 bez týchto a širšej regresie.
- Najbližšie: regresia všetkých nových/runtime/navigation testov, overiť startup a
  Qt pause/close error cleanup, odstrániť nepoužívané runtime importy z MainWindow,
  aktualizovať dokumentáciu a checklist A5 na dokončené až po úspešnom overení.


## Checkpoint A5 dokončená – 2026-09-13

- A5 je dokončená; predchádzajúce A5a/A5b odseky sú historické priebežné stavy.
- Rovnaká vetva codex/remove-jetson-gpio, základ e6cfe625e5e2ecda03c221f75060da9318f9230e.
  A1–A5 lokálne, bez commitu/push/merge. Predchádzajúce zmeny zachované.
- services/inspection_runtime.py (1057 riadkov) vlastní produkčné vykonanie:
  profily/capture, routing vstupov, sekvencie, vetvy, pipeline, serializáciu správ,
  súbory výsledkov, SQLite zápisy, agregáciu a Modbus výsledok.
  Je bez widgetov/Qt; aktuálne ešte importuje čisté helpery z ui/camera_profile_utils,
  ui/view_utils a ui/branching_utils. Presun týchto modulov z UI namespace patrí A6.
- ui/inspection_worker.py (44 riadkov): executor s jedným pracovníkom, bez fronty
  za aktívnou úlohou; queued Qt doručenie do UI. submit volá iba vlastnícke UI vlákno.
- MainWindow má 2640 riadkov. Odstránené produkčné prepare/execute, routing,
  serializácia/persistencia aj staré sekvenčné slovníky. Renderuje records/status/
  pending_overlays. _finalize_run_trigger je už iba zobrazenie hotového výsledku.
- Startup kamera/Pico príprava je vo workeri; runtime číta počiatočný režim kamery.
  _start_production urobí UI snapshot a až po prepare úspechu publikuje RUN.
  Výber pohľadu v RUN už neprekonfiguruje kameru; profil vlastní capture runtime.
- SQLite: DbService vznikne v runtime.run na worker vlákne, zatvorí sa v finally
  cez novú close metódu. UI spojenie sa neposúva medzi vláknami. Chyby uloženia
  už nie sú len print/return: kontrola skončí ERROR/NOK a pokusom o Pico IDLE.
- SETUP/close čakajú na prijatú kontrolu, blokujú nové vstupy hneď, potom worker
  vykoná quiesce/close. Ošetrené aj okno medzi admission a doručením Qt eventu.
  Kontrolu neprerušujú konkurenčným close kamery. Výsledky nemenia runtime režim.
- Pri busy sú zablokované editovacie ovládače, výber pohľadu, Live/manuálne svetlo
  a trigger tlačidlo; výsledky a požiadavka pozastavenia sú naďalej dostupné.
  Chyba vykreslenia výsledku nezablokuje controller.finish ani pending stop.
- cycle_id/run_id patrí explicitnej sekvencii; request_id konkrétnemu prijatému
  vstupu; frame_id identifikuje pôvodné pixely. Prevzatý pohľad dedí frame_id.
  Viacvstupové zdrojové kontexty majú samostatné captured_frames/frame_ids, prvý
  krok vytvorí nový cyklus, chyba/pause/zmena receptu ich resetujú.
- Software trigger si aktuálny explicitný Pico pohľad vyberá v runtime, nie v UI.
  Zachované existujúce prepare_trigger/prepare_master/capture_trigger/capture_master
  API a profilové časovanie; žiadna zmena firmware ani low-level mode algoritmu.
- Nové testy: samostatný runtime so skutočnou MSE pipeline, sekvencia/reuse,
  chyba snímky/úložiska, nový recept, worker SQLite vlastník, queued UI doručenie,
  čakanie pause/close, admission pred Qt doručením a chyba renderovania.
  Routing a navigation testy už používajú normálne importy namiesto AST extrakcie.
- Záverečná sada 174 passed (inspection_runtime, inspection_controller,
  inspection_window_lifecycle, run_page_navigation, main_window_pico_trigger,
  external_trigger_routing, recipe_format, run_roi_rotation, golden_wizard_trigger_path,
  camera_profile_utils, learning_context, statistical_learning, tool_pipeline,
  locator_template_match, pico_pio_protocol, pico_firmware_v4).
  Jediný warning: ImageIO deprecated backend pri úmyselne poškodenom PNG.
  Docker hdf_vision:dev, bez siete/hardvéru, dočasné /data. Diff check a AST syntax OK.
- Žiadne aktívne testové procesy. Bez buildu, reštartu, snímania, FW zásahu alebo
  zápisov do produkčných dát. Reálne USB/Pico/line timing/záťaž zatiaľ NEOVERENÉ (A7).
- A6: rozdeliť ďalšie veľké moduly (najprv tool_service alebo Golden podľa checklistu),
  zdieľať SETUP capture helper s runtime a vyčistiť čisté helpery z UI namespace.
  SETUP/Golden servisné dialógy majú vlastné synchrónne operácie; neboli presúvané
  do produkčného workeru ani označené za asynchrónne. Počas produkčnej úlohy sú blokované.
- Autoritatívny kontrakt: INSPECTION_CONTROLLER_SK.md. Hardvérová regresia A7 musí
  overiť najmä MASTER externú rezerváciu pri worker odovzdaní a pause/close pod záťažou.
- Posledná UI drobnosť: návrat do RUN obnoví vypnutý Live label a sprístupní
  filtrované ROI aj po predchádzajúcom Live. Následná navigation sada: 11 passed.


## Checkpoint A6a – 2026-09-13

- Vetva codex/remove-jetson-gpio, základ HEAD e6cfe625e5e2ecda03c221f75060da9318f9230e.
  Všetky A1–A6a zmeny lokálne, bez commitu/push/merge; nič z predchádzajúcich etáp nevrátené.
- tool_service.py: z ~2000 na 78 riadkov, ostáva katalóg a golden/recept loading.
  Nové tool_contracts (119), tool_pipeline (684), tool_image_helpers, tool_status,
  tool_latency a tools/ssim.py + tools/locator_template.py. Existujúce telá
  implementácií prenesené bez zmeny algoritmov. Registry aj všetci app/tests
  volajúci importujú konkrétne moduly, bez facade spätných aliasov.
- Odstránený nepoužívaný validate_tool_params a jeho súkromné coercion helpery.
  Repozitár nemal žiadne volania; aktívna validácia formulárov zostáva v
  golden_wizard_logic a kontrakt receptu/modelu/pipeline zostáva zachovaný.
- ToolConfigPanel + CollapsibleSection v golden_wizard/tool_config_panel.py (1601),
  ToolsTableWidget v tools_table.py. GoldenWizard má 3019 riadkov. Ďalšie delenie
  jeho učenia, správy pohľadov a náhľadov ZOSTÁVA; neoznačiť celú A6 za hotovú.
  thresholds_panel používa priamy import panelu (pôvodný package export neexistoval).
- Čisté helpery presunuté a všetci volajúci aktualizovaní:
  ui/camera_profile_utils → services/camera_profiles,
  ui/view_utils → services/view_images, ui/branching_utils → services/view_aggregation.
  InspectionRuntime už neimportuje UI moduly.
- Odstránené globálne náhrady cv2/imaging/compare_service v troch testových moduloch.
  UI testy run_roi_rotation/main_window_pico_trigger/main_window_pio importujú
  reálne adaptéry; nepoužívajú AST extrakciu. Firmware AST zostáva zámerný test
  MicroPython skriptu s vynechaným nekonečným main loop; sys.modules používa
  iba monkeypatch s automatickým obnovením po teste.
- Prvý spoločný beh: 461 passed, 5 failed. Sú to známe canvas/overlay baseline
  problémy; teraz došetrené, nie vynechané. ROI redo bola skutočná chyba:
  _restore_snapshot držal referencie na zoznamy, ktoré set_roi_data vymazal in-place.
  Oprava uloží nezávislé kópie zoznamov; nový test overuje viac undo/redo dvakrát.
- Ostatné štyri assertiony boli zastarané: test klikal skryté generické Draw,
  namiesto viditeľného Obdĺžnik; dva Escape testy klikali do letterbox okolia
  namiesto obrazových súradníc a očakávali QGraphicsRectItem namiesto aktuálneho
  QGraphicsPathItem; maskový test očakával transparentný [9,9] napriek jednotke.
  Fixture teraz definuje skutočný vonkajší nulový okraj, vnútorný otvor zachovaný.
- Cielená ROI/overlay sada po opravách: 27 passed. Predtým po splitoch prešlo
  77 pipeline/learning/runtime testov a 165 panel/profile/pipeline testov.
- Zmapovanie editora: _ROIView (obdĺžnik/handles), _ShapeROIView (elipsa/polygón/
  rotácia/história), _MaskView (samostatné masky), _SharedCanvasView (ROI+masky+edge),
  ROIEditor, LocatorROIEditor a MaskEditor. Duplicitné maskové správanie a delenie
  histórie/geometrie ešte nie sú vyriešené. Nemigrovať ich do ďalších mixinov bez kontraktu.
- Bez hardvéru, FW, buildu, reštartu alebo zásahu do produkčných dát.
- PRESNÝ ĎALŠÍ KROK: A6b, vyčleniť správu datasetu/rebuild/validácie z Golden Wizardu
  do samostatnej služby, UI nech vlastní iba dialógy/správy. Potom správa pohľadov/
  náhľadov, kamera stream vs PIO, ROI história/geometria/masky podľa checklistu.
- Poznámka: tool_latency zachováva pôvodný globálny tracker, aby presun nemenil
  správanie. Pred multi-camera rozšírením zadefinovať vlastníctvo/identitu jeho metrík.

### Záverečné overenie A6a

- Celá sada: **467 passed, 1 warning in 10.37s**. Warning je ImageIO deprecated
  TIFF backend pri teste úmyselne poškodeného golden súboru.
- Príkaz: `docker run --rm --network none --user 1000:1000 -v /home/hdf-jetson1/HDF_Vision:/workspace -w /workspace -e PYTHONPATH=/workspace/.docker-cache/pytest:/workspace -e QT_QPA_PLATFORM=offscreen --tmpfs /data:rw,mode=1777 --entrypoint python3 hdf_vision:dev -m pytest tests -q --tb=short`.
- Testový proces skončil, žiadne testy nezostali spustené. Overenie nepoužilo
  hardvér ani produkčné dáta; bez buildu aplikácie a bez publikovania Git zmien.

## Checkpoint A6 dokončená – 2026-09-13

- A6b: LearningDataset vlastní kontrolu kompatibility, rebuild a validáciu;
  GoldenViews spravuje pohľady a draft tracking, GoldenPreview obrazy a náhľady.
- Kamera používa kompozíciu CameraStream + PioCapture. Stream vlastní backend,
  buffery a MASTER rezervácie; PIO vlastní generáciu, požiadavku, pripravenosť a zámky.
  CameraService vlastní profil/HID a prepínanie režimov, bez ďalšieho mixinu.
- Odstránený starý capture_trigger_frame a súvisiaci trojimpulzový tok: žiadny
  app/script volajúci, iba vlastné zastarané testy. Odstránených 19 testov tohto toku;
  aktuálne PIO profily/protokol/firmware/lifecycle naďalej pokryté testami.
- Kamera nikdy nezamieňa neotvoriteľné zariadenie za inú /dev/video kameru.
- ViewCapture je spoločný SETUP/Golden/RUN tok vrátane MASTER rezervácie a PIO.
- ROI widgety ostali v roi_mask_editor (1226 riadkov), kreslenie v ui/roi:
  rectangle_view, shape_view, mask_view, shared_canvas; geometry a EditHistory
  sú samostatné komponenty, mask_painting zdieľa rasterizáciu štetca.
- Nepoužívaný globálny latency tracker aj jeho dva nevolané ToolService accessory
  odstránené. Časy jednotlivých nástrojov zostávajú vo výsledkoch a UI.
- Nové testy izolácie rezervácií/generácií dvoch kamier, odmietnutia náhradného
  zariadenia, histórie a neplatného capture módu. Celá Docker sada **452 passed,
  1 ImageIO deprecation warning, 10.01s**. Testy ukončené, hardvér nepoužitý.
- A7 sa NESPUSTÍ podľa výslovného pokynu používateľa. Ďalej implementovať
  dve nezávislé kontrolné stanice kamera/Pico. Používateľ potvrdil vlastné
  nezávislé kontroly z každého Pico, nie jeden spoločný cyklus.
- Zmeny stále lokálne, bez buildu/pushu/merge/firmware zásahov.

## Rozpracovaná podpora viacerých kamier (po A6, nie A7)

- Používateľ potvrdil nezávislé kontroly z každého Pico. Následne sa pýtal na
  tretiu kameru: párovanie a shell preto nemajú limit dvoch staníc. Výkon N kamier
  nie je hardvérovo overený; pripojené sú iba dve.
- Read-only sysfs inventúra: CU55 2420020C (pôvodná, teraz video2), 162A5806
  (nová, teraz video0); Pico fdfb463297dcf38c (ttyACM0), 7e44fd2ac0cff44c (ttyACM1).
  Fyzické páry NEZNÁME. Používateľ výslovne chce párovanie až v aplikácii.
- WorkstationDevices + dialóg ukladá explicitné USB serial priradenia, odmieta
  duplicitné zariadenia. Žiadny odhad párov ani automatické nasadenie configu.
- WorkstationWindow obsahuje nezávislé MainWindow pracovné panely. Prepnutie tabu
  nemení RUN druhého panelu. Každý má controller/runtime/worker/DB/recipe služby.
- Dáta stanice 1 zostávajú /data; ďalšie /data/stations/camera_N. Rozšírené storage
  funkcie a caller paths používajú explicitný base_dir/data_root. Modbus/Pico config
  je per-station; nový Modbus je predvolene vypnutý.
- Kamera aj Pico majú resolver USB serial; chýbajúce zariadenie nezamenia za iné.
  RUN request/InspectionFrame/meta nesú camera_id; prevzatá snímka inej kamery sa odmieta.
- Launcher mapuje /dev a /sys pre hotplug namiesto povinného CAM_DEV video0.
- Rozpracované: regresné testy, UI smoke, diagnostika párovania, dokumentácia.
  App ani hardvér nespustené, nič flashnuté/buildnuté/pushnuté.

## Záverečný checkpoint – A6 + nezávislé kamerové stanice

- Implementácia dokončená, **472 passed, 1 warning in 10.77s**. Warning iba ImageIO
  deprecated TIFF backend pri úmyselne poškodenom golden. Docker hdf_vision:dev,
  bez siete/USB, offscreen Qt, dočasné /data; rovnaký plný príkaz ako v A6a vyššie.
- Nové regresie: tri paralelné runtime (druhý zlyhá, ostatné skončia), prepínanie
  troch kariet bez pause, vlastné recepty/DB/golden/meta, zamietnuté cudzie snímky
  a požiadavky, serial renumber/disconnect, nejednoznačné a duplicitné priradenia.
- Reálny widget tree MainWindow a GoldenWizard otestovaný so simulovanou kamerou.
  Golden LIVE už používa priamo CameraService svojej stanice; LivePreviewService
  odstránený, žiadny globálny CAM_DEV výber v Golden.
- Dva skutočné GStreamer videotestsrc streamy bežali bez USB: vlastné GLib kontexty,
  zastavenie jedného nezastaví druhý. Starý appsink/bus nemôže ovplyvniť nový stream.
- Každá stanica vlastní SessionSettingsStore; loggery sú oddelené podľa cesty
  a synchronizované. Zmena ukladania/overlay na jednej nemení druhú.
- Globálne zatvorenie/reštart/vypnutie koordinuje WorkstationWindow, počká na
  dokončenie pracovníkov všetkých staníc a IDLE, až potom ukončí aplikáciu.
- Okno párovania vie pridať tretiu a ďalšie stanice, overiť zvolenú dvojicu cez
  existujúce PIO (1080p/1 ms), zobraziť snímku a vždy zavrieť testovacie USB služby.
  Na párovanie musia byť všetci pracovníci voľní a stanice zastavené; heslo podľa
  existujúceho SecurityService. Dáta odobratých staníc sa nemažú.
- Fyzické páry používateľ nepozná podľa serial a výslovne ich chce vybrať v app.
  Žiadny workstation_devices.json v produkcii nebol vytvorený. Fyzické kamery
  ani Pico sa neotvárali, neflashovali, neresetovali ani nesnímali.
- A7 ODLOŽENÁ. Žiadny nový Docker build, spustenie produkčnej app, reštart, commit,
  push alebo merge. Vetva codex/remove-jetson-gpio; všetky A1–A6/multi-camera zmeny
  zostávajú lokálne vrátane nových súborov. Diff check a syntax launcheru bez chyby.
- Návod a presný rozsah: MULTI_CAMERA_SK.md. Nasledujúca akcia používateľa je
  spustenie aktualizovaným launcherom a párovanie. Výkon troch fyzických kamier
  nie je testovaný ani garantovaný softvérovou regresiou.
- Žiadne testové procesy nezostali spustené.

## Publikovanie – PR #358

- Používateľ výslovne schválil push a merge všetkých pripravených zmien.
- Implementačný commit: f681983 (A1–A6 a nezávislé kamerové stanice).
- PR: https://github.com/TpekSVK/HDF_Vision/pull/358, cieľ dev.
- Záverečné overenie kódu: 472 passed; od tohto behu sa menila iba dokumentácia
  a komentár. Merge nadväzuje po kontrolách GitHubu. Staršie lokálne stavy vyššie
  sú historické checkpointy, nie pokyn opätovne publikovať implementáciu.
- A7, fyzické párovanie, build, nasadenie a reštart zostávajú nevykonané.

## Správa receptov a nastavenie pohľadu – 2026-09-13

- Odstránené nefunkčné pole Zariadenie kamery z Upraviť pohľad. Fyzické
  priradenie kamery zostáva v Kamery a Pico; uloženie pohľadu už device_id nevytvára.
- Zmazať recept… ponúka vlastný zoznam aj nepodporovaných receptov aktuálnej
  stanice. Neaktívny recept sa nemusí načítať; aktívny recept zostáva zachovaný.
- Potvrdenie zmazania má predvolené Nie a zachovanú autorizáciu. Default je
  chránený; pri mazaní aktívneho receptu sa najprv overuje dostupný default.
- Overenie: 479 testov prešlo, 1 existujúce upozornenie ImageIO. Regresie pokrývajú
  odmietnutý formát, zrušenie, autorizáciu, oddelenie staníc aj aktívny recept.
- Používateľ schválil push a merge. A7 ani hardvérové testy sa nevykonávali.
