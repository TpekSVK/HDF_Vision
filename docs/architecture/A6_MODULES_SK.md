# A6 – priebežné rozdelenie modulov

Stav 2026-09-13: A6a dokončená, celá A6 zostáva rozpracovaná.

## Nástroje

| Modul | Zodpovednosť |
| --- | --- |
| tool_service.py | Katalóg, aktívny recept a golden referencia |
| tool_contracts.py | Typy výsledkov, kontextu, protokol a základ runnera |
| tool_pipeline.py | Poradie nástrojov, vykonanie, zarovnanie a reporty |
| tools/locator_template.py | Template locator |
| tools/ssim.py | SSIM |
| tool_image_helpers.py | Geometria výrezov a kľúče obrazovej cache |
| tool_status.py | Normalizácia výsledkov podľa metrík/prahov |
| Časy nástrojov | Zostávajú vo výsledkoch; nepoužívaný globálny tracker odstránený |

Implementácie importujú kontrakty a obrazové helpery; nemusia importovať
orchestrátor. Registry vytvára konkrétne implementácie. UI/runtime volajú
run_pipeline/run_tool_test z tool_pipeline. ToolService nie je kompatibilná facade
pre staré importy. Nepoužívaný druhý validátor parametrov bol odstránený; aktívna
validácia formulárov a kontrakt receptu zostali zachované.

## Golden Wizard a služby

ToolConfigPanel a CollapsibleSection majú samostatný modul tool_config_panel.
ToolsTableWidget je v tools_table. Dialog GoldenWizard ostáva koordinátorom;
vyčlenenie jeho učenia, správy pohľadov a náhľadov bude ďalší krok.

Čisté camera_profiles, view_images a view_aggregation sú medzi službami.
Produkčný InspectionRuntime už nemusí importovať UI namespace.

## Testy a história ROI

Globálne importové náhrady obrazových knižníc boli odstránené. Testy aplikácie
používajú reálne importy v Dockeri. Firmware emulácia je odlišný prípad: zámerne
vynecháva nekonečný loop a dočasne nahrádza MicroPython moduly cez fixture.

Obnovenie ROI snapshotu už nestráca redo/undo zoznamy: pred set_roi_data sa
uchovajú ich kópie, pretože setter vymazáva aktuálne zoznamy. Viackrokový test
preukazuje zachovanie celej histórie pri opakovanom návrate tam aj späť.
Zastarané canvas testy používajú viditeľné kresliace tlačidlo, obrazové súradnice
namiesto letterbox okolia a aktuálny path item. Overlay test má explicitne nulový
vonkajší okraj; nepožaduje priehľadnosť pixela označeného ako vyplnený.

## Zostáva

- Správa učenia/vzoriek/modelov ako služba, UI dialógy ako jej klient.
- Správa pohľadov a náhľadov mimo hlavného Golden dialogu.
- Stream kamery, riadenie módov a spoločný aktuálny capture tok.
- Spoločná geometria, maskové operácie a história ROI bez ďalších kópií editorov.
- Vlastníctvo metrík pre viac zariadení pred rozšírením súčasného latency trackeru.

Hardvérové merania a produkčná záťaž zostávajú A7.

## Overenie A6a

Celá sada v Docker obraze hdf_vision:dev: **467 testov prešlo**, bez zlyhania.
Jediné upozornenie je zastaraný ImageIO TIFF backend pri poškodenom golden súbore.
Beh bol bez siete a hardvéru, s dočasným /data a offscreen Qt.

## Dokončenie A6

Zostávajúce zodpovednosti vyčlenené do LearningDataset, GoldenViews, GoldenPreview,
CameraStream, PioCapture, ViewCapture a ui/roi komponentov. GoldenWizard má
2349 riadkov, CameraService 574, CameraStream 649, roi_mask_editor 1226.
A6 nevyžaduje odstrániť každý dlhší widget: ui/roi/shared_canvas riadi spoločné
interakcie ROI/masky/edge, ktoré používajú jednu scénu. Nie sú zavedené nové mixiny.
Globálny latency tracker bol bez konzumenta a bol odstránený; per-tool časy zachované.
Celá finálna sada: **452 passed** (19 testov odstráneného trojimpulzového toku zrušených,
4 nové testy izolácie zdrojov/histórie/capture; zvyšné testy prešli).
A7 odložená. Nasleduje implementácia dvoch nezávislých kamerových staníc.
