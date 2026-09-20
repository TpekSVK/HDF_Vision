# Kontrola prázdnej formy V2 – implementačný report

Dátum: 20. 9. 2026. Interný typ: `mold.protection_v2`.

Nový experimentálny tool je samostatne dostupný v katalógu v kategórii **Špecializované**. Pôvodný `mold.protection_v1` zostáva registrovaný. Jeho implementačné súbory ani matematika sa nemenili. Existujúce recepty sa na V2 nemigrujú.

## 1. Pridané súbory

- `app/services/empty_mold_v2/__init__.py` – samostatný balík V2.
- `app/services/empty_mold_v2/regions.py` – hlavná ROI, kavity, ignore, automatický OUTSIDE, geometrický podpis, NOK polygóny.
- `app/services/empty_mold_v2/alignment.py` – použitie existujúceho Locatora; celý zarovnaný frame pri zbere aj RUN.
- `app/services/empty_mold_v2/model.py` – výpočet median/MAD modelu po blokoch riadkov.
- `app/services/empty_mold_v2/evaluator.py` – polarita, prahy zón, morfológia, spoločné bloby a metriky.
- `app/services/empty_mold_v2/storage.py` – SQLite katalóg, bezstratové snímky, nemenné modelové artefakty, audit.
- `app/services/empty_mold_v2/workflow.py` – autorizovaný tréning, kontrola kandidátov, odporúčania, validácia a explicitná aktivácia.
- `app/services/empty_mold_v2/validation.py` – polygonové ground truth, nezávislé testovanie, deterministický návrh parametrov.
- `app/services/empty_mold_v2/feedback.py` – operátorské False NOK / False OK bez zmeny modelu či receptu.
- `app/services/empty_mold_v2/overlays.py` – kavity a bloby v zarovnaných aj pôvodných súradniciach.
- `app/services/empty_mold_v2/export.py` – ZIP export datasetu pre ďalšie nástroje.
- `app/services/tools/empty_mold_v2.py` – implementácia `BaseTool`, runtime a metadáta registrácie.
- `app/ui/golden_wizard/empty_mold_v2_dialog.py` – V2 konfigurácia, kreslenie kavit a anotácií, dataset, kandidáti, heatmap, ladenie, výsledky a modelové verzie.
- `tests/test_empty_mold_v2.py` – unit/integration testy služieb, RUN, receptov a spätnej kompatibility.
- `tests/test_empty_mold_v2_ui.py` – headless Qt testy katalógu, konfigurácie, kavit a anotácií.
- `docs/EMPTY_MOLD_V2_REPORT_SK.md` – tento report.
- `artifacts/empty-mold-v2/benchmark.json` – namerané výsledky syntetického benchmarku.
- `artifacts/empty-mold-v2/regions.png`, `model.png` – headless náhľady UI so syntetickým obrazom.

## 2. Upravené súbory

- `app/services/tool_registry.py` – pridaná registrácia V2; registrácia V1 zostáva.
- `app/services/learning_context.py` – výpočet existujúceho podpisu prípravy obrazu aj pre V2; V2 nie je zaradená do pôvodného UI/tréningového workflow V1.
- `app/services/db_service.py` – aditívne vytvorenie V2 tabuliek a indexov existujúcim inicializačným mechanizmom.
- `app/services/inspection_runtime.py` – iba pre V2: audit vykonanej verzie receptu, pôvodná transformačná matica, správne historické overlaye a obmedzený pamäťový snapshot pre feedback.
- `app/services/storage_service.py` – full frame výsledkov obsahujúcich V2 sa zapisuje bezstratovo. Pravidlá, či sa full frame vôbec uloží, disk guard a retencia zostávajú pôvodné. Ostatné výsledky používajú pôvodné nastavenie kvality.
- `app/ui/golden_wizard/golden_wizard.py` – otvorenie V2 konfigurácie, existujúca autorizácia, zber a uloženie toolu do draftu.
- `app/ui/golden_wizard/tool_config_panel.py` – tlačidlo „V2: Kavity, vzorky a model“ iba pre nový typ.
- `app/ui/golden_wizard/tool_catalog_dialog.py` – zaradenie V2 do špecializovaných toolov.
- `app/ui/main_window.py` – operátorský V2 feedback a V2 overlaye v RUN.
- `app/ui/results_page.py` – spätné označenie historického V2 výsledku ako kandidáta.

Pred začatím práce už boli zmenené `docs/architecture/CHECKPOINT_SK.md` a prítomné necommitnuté A7 diagnostické skripty. Tieto používateľské zmeny neboli súčasťou implementácie ani upravované.

## 3. Architektúra a integrácia

Tool používa existujúci `Tool`, `ToolParams`, `ToolRoi`, `ToolMask`, `BaseTool`, `ToolRunResult`, `ToolRegistry` a pipeline. Nastavenia, kavity a ukazovateľ na aktívnu verziu sa serializujú existujúcou cestou receptov. Nie je pridaný nový autentifikačný systém.

Hlavná ROI a ignore sa kreslia priamo v existujúcom Golden Wizarde. Tlačidlo V2 otvára tri záložky: **Kavity**, **Vzorky a kandidáti**, **Model, ladenie a validácia**. Kavity používajú existujúci `ROIEditor`: obdĺžnik, kruh/elipsa a polygón. Názvy sa generujú K1, K2, …; vybranú kavitu možno nahradiť alebo odstrániť. Prekrývajúce sa kavity sú odmietnuté. Kavity sú orezané platnou hlavnou ROI a ignore maskou.

Pre kontrolu sa vytvorí:

```
valid = main_roi - ignore
K_i = cavity_i ∩ valid
OUTSIDE_CAVITIES = valid - union(K_i)
```

Prázdna hlavná ROI, nesprávna maska, neplatný model, zmena prípravy obrazu alebo zlyhanie Locatora vedú k NOK s `inspection_fault`, nie k tichému OK.

V2 neodhaduje posun ani otočenie. Zber spúšťa existujúce Locator tooly a spotrebuje ich výslednú transformáciu; RUN spotrebuje ten istý `ToolRunnerContext`. Podporuje fyzicky aj virtuálne zarovnaný pipeline frame. Neúspešné zarovnanie nevpustí frame do datasetu.

Podpis modelu viaže Golden, kamerový profil, nastavenie pohľadu, Locator a geometriu. Nekompatibilné staré vzorky sa zobrazia ako iný kontext a nemožno ich potichu použiť na nový model.

**Zistenie o existujúcej architektúre:** produkčný `InspectionRuntime` dnes načítava `recipe.json`, hoci projekt obsahuje aj publikovanú kópiu receptu. Toto správanie nebolo menené. Pre V2 je rozhodujúca explicitná aktivácia a následné uloženie receptu. Samotný prepočet, validácia či návrh parametrov ukazovateľ aktívneho modelu nemenia. Publikovanie ostáva dostupnou existujúcou akciou, nie novou podmienkou runtime V2.

## 4. Matematika variation modelu

Model sa vytvára výhradne z kompatibilných `training_ok` snímok. Golden, NOK, kandidáti a validačné snímky netvoria normálny vzhľad.

Pre každý platný pixel `p`:

```
center[p] = median_i(I_i[p])
MAD[p] = median_i(abs(I_i[p] - center[p]))
variability[p] = clip(1.4826 * MAD[p], variability_floor, variability_cap)
signed[p] = (I_current[p] - center[p]) / variability[p]
score[p] = abs(signed[p])
```

Default `variability_floor = 3` jasové úrovne. Floor musí byť kladný, čo zabraňuje deleniu nulou. Default cap je 40; nula vypína horný cap. Cap menší než floor je neplatný. Model a score používajú `float32`; originály `uint8`, masky `uint8`/`bool`.

Fyzicky sa ukladajú dve obrazové polia pre celý referenčný rozmer. Pixely každej kavity a OUTSIDE majú vlastný center/variability; zóny nezdieľajú odhad variability. Toto je ekvivalent samostatných pixelových modelov zón bez duplikovania originálnych snímok.

Pri tréningu sa používa dočasný diskový `uint8` memmap a menšie float32 bloky riadkov. Nevzniká zásobník desiatok full-resolution float64 obrazov.

Voliteľná **globálna normalizácia jasu** odčíta medián platnej ROI z každého frame pred tréningom aj vyhodnotením. Je defaultne vypnutá. Nastavenie je zmrazené v modelovej verzii; jeho zmena vyžaduje nový model. Lokálna normalizácia okien nie je implementovaná.

## 5. Citlivosť a polarita

Citlivosť je 1–100, v konfigurácii V2 s posuvníkom aj číselnou hodnotou:

```
threshold(s) = 8.0 - 6.5 * (s - 1) / 99
```

Citlivosť 1 znamená prah 8; citlivosť 100 prah 1,5. Vyššia citlivosť označí viac odchýlok voči lokálnej variabilite. Nejde o absolútny globálny prah jasu.

Kladný advanced `anomaly_multiplier` nahrádza mapovanie citlivosti; nula používa posuvník. Per-zone threshold môže nahradiť spoločný prah pre konkrétnu kavitu alebo OUTSIDE.

- Svetlejší: `score > threshold` a `signed > 0`.
- Tmavší: `score > threshold` a `signed < 0`.
- Oboje: `score > threshold` bez znamienkového filtra.

## 6. Morfológia, bloby a výsledok

Lokálne masky zón sa najskôr spoja. Až potom nasledujú OPEN, CLOSE, erode, dilate a **jedna spoločná 8-connected-components analýza**. Defekt na rozhraní K2 → OUTSIDE ostáva jedným blobom.

Morfológia používa štvorcové jadro s rozmerom `2 * radius + 1`; nula danú operáciu vypína. Po každej operácii sa maska znovu oreže platnou oblasťou. Voliteľný ignore border eroduje platnú kontrolovanú oblasť a vyžaduje konzistentný prepočet modelu.

Relevantné bloby prechádzajú filtrami minimálnej/maximálnej plochy, minimálneho priemerného a maximálneho anomaly score. `max_blob_area=0` je bez horného limitu. Kladný max area je **filter**: väčšie objekty sa nezapočítajú, preto je default neobmedzený. Default min area je 20 px, povolený počet 0.

NOK nastane, ak počet relevantných blobov prekročí povolený počet. Report obsahuje stav zón, počet blobov, najväčšiu plochu, bounding box, centroid, dotknuté zóny, mean/max anomaly, width, height, aspect ratio, fill ratio, perimeter a circularity. Tvarové metriky sa zatiaľ nevyužívajú ako filtre. Blob môže mať označenie `K2 + OUTSIDE_CAVITIES`.

## 7. Tréning, validácia, odporúčania a diagnostika

Stavy sú `training_ok`, `training_nok`, `validation_ok`, `validation_nok`, `candidate_ok`, `candidate_nok`, `rejected`.

Jeden capture ukladá jeden celý zarovnaný grayscale frame v lossless WebP pre všetky zóny. UI odporúča aspoň 30 OK, ideálne 50+. Technické minimum na zostavenie modelu je 2. Duplicita rovnakých potvrdených pixelov je zakázaná aj medzi tréningom a validáciou.

OK zber používa existujúci manuálny/automatický zberový dialóg. NOK zber otvorí existujúci polygonový editor v samostatnom anotačnom dialógu. Jeden frame môže mať viac polygónov aj polygóny cez hranice zón. Anotácia uloží body a prieniky so zónami.

**Navrhnúť nastavenia** používa iba potvrdené tréningové OK/NOK. Grid prehľadáva citlivosť, minimálnu plochu a CLOSE polomer; ostatné parametre zostávajú technikove. NOK nesmie meniť center ani MAD. Optimalizačné poradie je False OK, počet nezachytených polygónov, False NOK a falošné bloby mimo anotácií. Pri zhodnom výsledku sa preferuje vnútro úspešného rozsahu citlivosti, nie jeho krajná hodnota. Ide o deterministickú heuristiku, nie dôkaz generalizácie.

Každý GT polygón musí byť pokrytý aspoň 10 % svojich platných pixelov a aspoň jedným pixelom. Ak frame obsahuje viac GT defektov, nájdenie iba jedného nestačí. Samostatne sa počítajú detekované komponenty bez prieniku s GT. Zásah GT je hodnotený až po filtrovaní blobov; celkový NOK musí zároveň vyhovieť povolenému počtu blobov.

Odporúčanie zobrazí **Aktuálne → Odporúčané** a výsledky. Mení sa až po **Použiť odporúčané**. **Vrátiť predchádzajúce** obnoví nastavenia pred posledným použitím odporúčania.

**Otestovať vzorky** používa nezávislé `validation_ok`/`validation_nok`, nič netrénuje a nemení nastavenia. Výsledky obsahujú OK correct, NOK correct, False NOK, False OK, zóny, chýbajúce GT a falošné bloby. Dvojklik otvorí problémovú snímku. Pri kandidátskom modeli sa zobrazí originál, Golden, anomaly mapa, binary maska, bloby, hranice a GT. Mapa variability používa farebný overlay na Golden.

Výpočty modelu, odporúčaní a validácie bežia v pomocnom Qt workerovi. Okno sa počas výpočtu nedá zavrieť ani meniť nastavenia. Logy zahŕňajú počty tréningových OK/NOK, štatistiky variability, odporúčanie, validáciu, uloženie/aktiváciu verzie a feedback; RUN neloguje pixely.

## 8. False NOK / False OK a oprávnenia

V RUN je tlačidlo **V2: False NOK / False OK**. Uchováva sa posledný vhodný frame každého pohľadu v pamäti, aj keď produkčné logovanie neukladá plné OK snímky. Pri otvorení feedbacku sa zachytí referencia na konkrétny výsledok, aby neskorší cyklus nezamenil označovaný obraz.

- Pôvodný NOK + fyzicky prázdna forma → `candidate_ok`.
- Pôvodný OK + fyzicky zostal diel → `candidate_nok`.
- Chyba modelu/Locatora sa neponúka ako platná tréningová vzorka.
- Bez zásahu operátora nevzniká žiadna vzorka ani zmena modelu.

Vo **VÝSLEDKOCH** možno vybrať konkrétny V2 tool a použiť rovnaký feedback dodatočne. Vyžaduje sa zachovaný full frame a pôvodný záznam zarovnania. Thumbnail sa na tréning nepoužije. Pôvodný transform sa aplikuje na originálny uložený frame; nikdy sa neodhaduje nový Locator nad historickou snímkou.

Kandidát sa zachová v oddelenom dataset úložisku pred produkčnou retenciou. Opakované označenie identických pixelov vytvorí samostatný auditný záznam, ale môže zdieľať jeden fyzický WebP. Rovnaký frame nemožno schváliť ako dva tréningové/validačné záznamy.

Technik otvorí konfiguráciu existujúcou ochranou receptu. Môže kandidáta otvoriť, zamietnuť alebo prijať do zvoleného tréningového/validačného stavu. Pri NOK musí doplniť polygóny. Pri zmene kamerového/geometrického kontextu sa prijatie odmietne. Operátorské akcie nikdy nevolajú aktiváciu ani tréning.

Audit obsahuje čas, recept a dostupné recipe ID, odtlačok vykonaného receptu, tool ID, model ID, systémový výsledok, výsledky zón, label/source feedbacku, approval status, anotáciu, referenciu na zdrojový výsledok/full frame a zarovnanie. Osobné údaje sa nepridávajú.

## 9. Verzie modelu a aktivácia

Každý model dostane UUID, UTC čas, parametre, zoznam použitých tréningových ID, kontext/geometriu, NPZ artefakt center/variability a metadata JSON. Kandidátsky build sa uloží ako samostatná verzia; predchádzajúce verzie sa nemažú.

Aktivácia vyžaduje aktuálnu validáciu s aspoň jedným správnym OK a NOK a **žiadnym False OK**. Zmena parametrov, anotácií či validačného datasetu zneplatní možnosť aktivácie bez nového testu. False NOK zostávajú viditeľné a samotné aktiváciu neblokujú.

**Explicitne aktivovať validovanú verziu** vytvorí nemennú validovanú verziu s rozhodovacími parametrami a validačnými výsledkami. Zmení iba ukazovateľ `v2_active_model` v rozpracovanom toole; uchová aj `v2_previous_model`. Potom treba uložiť recept. Runtime berie parametre z modelovej verzie, nie z rozpracovaných posuvníkov.

Aktívny/neaktívny stav sa odvodzuje z ukazovateľa v recepte a zobrazuje v zozname verzií. SQLite nie je druhým nezávislým zdrojom pravdy o aktívnom modeli. Staršiu uloženú verziu možno vybrať na testovanie a po novej validácii explicitne aktivovať. Modelové artefakty sa pri RUN cacheujú.

## 10. SQLite, súbory a export

Existujúce tabuľky a stĺpce sa nemenia. `_init_schema()` navyše vykoná idempotentné `CREATE TABLE/INDEX IF NOT EXISTS` pre:

- `empty_mold_v2_samples` – owner, stav, čas, frame path/hash, metadata, anotácie.
- `empty_mold_v2_models` – verzia, owner, čas, artifact path, metadata.
- `empty_mold_v2_audit` – akcia, čas, owner, detaily.

Indexy vyhľadávajú vzorky podľa owner/stavu a modely podľa owner. Čiastočný unikátny index `(owner, frame_hash)` pre schválené stavy chráni pred identickými dátami v tréningu aj validácii.

Súbory sú v `/data/recipes/.empty_mold_v2/<owner>/`:

```
frames/<sample-id>.webp
contexts/<context-hash>/masks.npz
contexts/<context-hash>/zones.json
models/<version-id>/variation.npz
models/<version-id>/metadata.json
```

Umiestnenie podľa nemenného owner ID prežije premenovanie receptu. Produkčná retencia sa týka `/data/runs`, nie tohto datasetu. Dataset nemá automatické mazanie ani po zamietnutí; jeho životný cyklus treba spravovať vedome. Modelové artefakty sú oddelené od originálov.

ZIP export obsahuje lossless aligned frames, stavy OK/NOK/kandidát/rejected, GT polygóny, feedback a tool/model metadata, hlavnú ROI, ignore a masky zón. NPZ masky sú pomenované `main_roi`, `ignore`, `zone_0`, …; poradie mien je v `zones.json`. Je to dataset export pripravený pre budúci AI nástroj, nie export spustiteľného neurónového modelu.

## 11. Testy a meranie

Automatizované testy boli spustené v existujúcom ARM64 Docker image `hdf_vision:dev`, nad aktuálnym workspace, s `QT_QPA_PLATFORM=offscreen`. Kamera ani produkčné recepty neboli pre testy používané. Testovacie dáta a SQLite databázy boli dočasné.

**Finálny výsledok: 521 testov prešlo, 1 existujúce deprekačné upozornenie ImageIO/tifffile, 14,54 s.** `git diff --check` prešiel. Pokrytie zahŕňa:

- rectangle/polygon/ellipse ROI, kavity, ignore a OUTSIDE;
- stabilné a variabilné oblasti, defekty a obe polarity;
- jeden blob cez K2 → OUTSIDE, morfológiu a per-zone override;
- konzistentnú normalizáciu;
- jednotlivé GT polygóny a falošné detekcie;
- oddelenie kandidátov, tréningu a nezávislej validácie;
- read-only odporúčanie/test, apply/undo a explicitnú aktiváciu;
- zmenu anotácie po validácii;
- oba feedback labely, nedostupný full frame, deduplikáciu a nezmenený aktívny model;
- runtime snapshot pre feedback aj bez produkčného logovania;
- fyzické/virtuálne zarovnanie existujúcim Locatorom a zmenu jeho nastavení;
- overlaye v pôvodných súradniciach;
- serializáciu a existujúcu cestu uloženia/publikovania receptov;
- pôvodný tool a aditívne SQLite zmeny;
- Qt katalóg, konfiguráciu, kreslenie kavity a NOK anotácie.

Syntetický benchmark 1920×1080, 50 OK snímok, 4 kavity + OUTSIDE:

| Meranie | Výsledok |
|---|---:|
| Zostavenie variation modelu | 4,91 s |
| Medián vyhodnotenia, 20 opakovaní | 28,91 ms |
| p95 vyhodnotenia | 34,42 ms |
| Peak RSS celého benchmark procesu | 399,59 MiB |
| Defekt cez hranicu kavity | 1 blob, 1 200 px, NOK |

Ide o meranie CPU evaluátora so syntetickými dátami. Nezahŕňa capture, Locator, SQLite/WebP, kreslenie UI ani celý produkčný cyklus. Výkonový režim a clocks neboli menené. Čas na reálnom recepte treba zmerať samostatne.

## 12. Známe obmedzenia

- Nebola vykonaná fyzická akceptácia na reálnej vstrekovacej forme. Potrebné sú reprezentatívne OK/NOK snímky z konkrétneho pracoviska.
- Normalizácia je globálna; pri zapnutí môže potlačiť veľkoplošnú rovnomernú zmenu. Preto je defaultne vypnutá.
- Odporúčanie prehľadáva citlivosť, min area a CLOSE. OPEN, ďalšie morfológie, mean/max a zónové prahy technik ladí ručne.
- 10 % pokrytie GT je pevné validačné kritérium; nejde o plnohodnotné IoU hodnotenie segmentácie. Bloby s aspoň jedným zásahom GT sa nepočítajú ako úplne falošné komponenty.
- Minimom na výpočet sú 2 OK, no to nestačí na reprezentatívny produkčný model; UI odporúča 30/50+ a vyžaduje nezávislú validáciu pred aktiváciou.
- Byte-identické potvrdené vzorky sa neduplikujú. Detekcia podobných, ale nie identických snímok nie je implementovaná.
- Bez uloženého full frame nemožno spätne označiť historický výsledok. Pri defaultnom ukladaní plných NOK sa False OK dá zachytiť priamo z posledného RUN snapshotu, prípadne treba v existujúcej storage konfigurácii zvoliť ukladanie full OK.
- Rozpracovaná zmena ROI, ignore, Golden alebo Locatora môže spôsobiť odmietnutie starého aktívneho modelu ako nekompatibilného. Nedôjde k tichému použitiu kandidáta.
- Cesty aktívnych artefaktov v recepte sú absolútne. Presun celého runtime úložiska na iný mount vyžaduje aktualizáciu referencií; ZIP export rieši prenos datasetu, nie automatický import modelu do inštalácie.
- Schválené aj zamietnuté dataset dáta a staršie modely sa automaticky nepremazávajú; treba sledovať kapacitu úložiska.
- Tréning je offline; automatický OK zber v existujúcom dialógu do potvrdenia drží uint8 snímky v pamäti. Samotný model builder používa diskový memmap.

## 13. Čo sa neimplementovalo

Nie je pridaná neurónová sieť, ONNX/TensorRT inferencia, GPU akcelerácia, automatická segmentácia kavit, nový globálny Locator, perspektívna rektifikácia kavit, automatické učenie, automatická aktivácia ani nové prihlasovanie. Lokálna normalizácia po oknách, automatické ladenie všetkých advanced parametrov, špecifický AI plugin a automatický import dataset ZIP sú mimo tejto verzie.

## 14. Presný manuálny test na Jetson Orin Nano v Dockeri

1. Na Jetson pracovisku používajte existujúci JetPack 6.2 a Docker image projektu. Z koreňa repozitára spustite `bash docker/build.sh`, ak image ešte nie je pripravený, a potom `bash docker/run.sh`. Nepoužívajte `--admin` na test heslových oprávnení. Nespúšťajte súbežnú druhú aplikáciu nad tou istou kamerou.
2. Overte, že starý recept sa načíta a starý tool **Kontrola prázdnej formy** ostal v katalógu aj v RUN funkčný. Zmeny skúšajte v samostatnom testovacom recepte.
3. V SETUP odomknite existujúcu editáciu receptu. Vyberte kameru/pohľad a nastavte grayscale Y8, stabilnú expozíciu a osvetlenie existujúcim postupom.
4. Pridajte existujúci Locator a zaň nový **Kontrola prázdnej formy V2** z kategórie Špecializované. Vytvorte Golden.
5. Nakreslite jednu hlavnú ROI tak, aby obsahovala štyri kavity aj priestor medzi nimi. Pridajte ignore zóny existujúcim editorom.
6. Otvorte **V2: Kavity, vzorky a model**. V záložke Kavity nakreslite polygón a stlačte **Pridať kavitu**. Opakujte pre K1–K4. OUTSIDE nekreslite.
7. Vo Vzorkách zvoľte **Tréning**, **Zber OK vzoriek** a zozbierajte približne 50 reálne prázdnych foriem. Zahrňte prirodzené prípustné otlačky, škvrny a mierne odlesky. Potvrďte zber.
8. V záložke Model zvoľte **Vytvoriť variation model**. Po dokončení otvorte **Mapa variability** a skontrolujte stabilné aj variabilné časti.
9. V tréningovom zbere vytvorte NOK prípady v K3, medzi kavitami a cez hranicu K2/OUTSIDE. Pri každej snímke obkreslite skutočné defekty polygónmi, každý pridajte a anotáciu uložte. Pre viac defektov pridajte viac polygónov.
10. Kliknite **Navrhnúť nastavenia**. Overte, že posuvníky sa samé nezmenili, a prečítajte Aktuálne → Odporúčané aj výsledky. Použite **Použiť odporúčané**; vyskúšajte **Vrátiť predchádzajúce** a znovu použite odporúčanie.
11. Prepnite zber na **Nezávislá validácia**. Získajte nové OK aj NOK snímky, ktoré neboli použité v tréningu. NOK opäť anotujte.
12. Kliknite **Otestovať vzorky**. Overte súhrn aj zóny. Dvojklikom otvorte problémové vzorky a prepnite originál/Golden/anomaly/binary/bloby. Skontrolujte jeden spoločný blob cez hranicu kavity.
13. Dolaďte citlivosť, min area, polaritu a prípadne pokročilé parametre. Po každej zmene znovu otestujte vzorky. Po zmene floor/cap/normalizácie/ignore border zostavte nový model.
14. Kliknite **Explicitne aktivovať validovanú verziu**. Aktivácia sa musí odmietnuť bez aktuálnej validácie, bez oboch validačných tried alebo pri False OK. Po úspechu zavrite V2 konfiguráciu a uložte recept existujúcim tlačidlom. Publikovanie možno vykonať existujúcim postupom; RUN dnes číta uložený `recipe.json`.
15. Prejdite do RUN. Skontrolujte prázdnu formu, zvyšok v kavite, zvyšok mimo kavít a zvyšok cez K2/OUTSIDE. Overte celkový výsledok, zóny, blob count, najväčšiu plochu a overlay. Skontrolujte aj oblasť mimo hlavnej ROI a ignore: tieto pixely nesmú samy spôsobiť detekciu.
16. Pri potvrdenej False NOK použite **V2: False NOK / False OK**, vyberte V2 výsledok a potvrďte fyzické zistenie. V SETUP musí pribudnúť iba `candidate_ok`. Overte nezmenené ID aktívneho modelu.
17. Analogicky pri False OK uložte `candidate_nok`. V RUN sa nevyžaduje anotácia. Overte, že ďalšie kontroly stále používajú rovnaký aktívny model.
18. Vo VÝSLEDKOCH vyberte starší výsledok a konkrétny V2 tool. Vyskúšajte kandidáta z full frame. Pri zázname bez full frame musí aplikácia akciu odmietnuť a nesmie použiť thumbnail. Pre historické OK vyžaduje existujúca `/data/config.json` politika ukladanie full OK (`store_full_nok: false`); táto implementácia toto nastavenie sama nemení.
19. Odomknite SETUP, otvorte kandidáta, pri NOK doplňte polygóny a prijmite ho do tréningu alebo validácie. Vyskúšajte aj zamietnutie.
20. Zostavte nový kandidátsky model. Bez explicitnej aktivácie sa `v2_active_model` nesmie zmeniť ani po uložení receptu. Potom validujte, explicitne aktivujte a uložte; zmení sa model ID a staršia verzia ostane v zozname.
21. Skúste zmenu kamery/Locatora alebo ROI bez kompatibilného modelu: kontrola má skončiť NOK s diagnostikou. Nevytvárajte z takejto technickej chyby tréningový feedback.
22. Exportujte dataset a skontrolujte `manifest.json`, lossless frames, polygóny a `contexts/*/masks.npz`. Overte, že produkčné mazanie starých `/data/runs` neodstraňuje schválený dataset.
23. Na konkrétnom recepte zmerajte latenciu celého cyklu a dlhší beh. Syntetický benchmark v tomto reporte nenahrádza tento test.

Reprodukcia automatizovaných testov bez kamery a bez produkčného `/data`:

```bash
cd /home/hdf-jetson1/HDF_Vision
docker run --rm --network none \
  -e QT_QPA_PLATFORM=offscreen \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/workspace:/workspace/.docker-cache/pytest \
  -v "$PWD":/workspace:ro -w /workspace \
  hdf_vision:dev python3 -m pytest tests -q -p no:cacheprovider
```

Príkaz využíva existujúci lokálny pytest cache projektu. V produkčnom image samotný pytest predinštalovaný nebol.

## Následná oprava UI podľa screenshotov z 20. 9. 2026

- Opravený `AttributeError` pri prepnutí na ignore masku: `ROIEditor` používa nové `mask_can_undo()` / `mask_can_redo()` nad skutočnou `EditHistory`, nie odstránené `_mask_undo` / `_mask_redo` polia. Zmenené `app/ui/roi_mask_editor.py` a `app/ui/roi/shared_canvas.py`.
- Obrysy V2 oblastí a NOK anotácií sa kreslia ako samostatné vektorové prvky scény so stálou hrúbkou 2 obrazovkové pixely. Už sa nezapisujú do náhľadového rastra hrúbkou 1 pixel, ktorá pri zmenšení mizla po úsekoch. Modrá označuje hlavnú ROI, žltá kavity, červená ignore a fialová GT. Doplnená legenda v editore aj diagnostike.
- Zmenené `app/ui/golden_wizard/empty_mold_v2_dialog.py` a doplnené regresné testy v `tests/test_empty_mold_v2_ui.py`: história ignore vrátane Undo/Redo, obrysy pri 31 % a ďalších zoomoch, zachovanie pôvodných obrazových pixelov a odstránenie starých overlayov pri obnovení náhľadu.
- Výsledok celej sady po oprave: **523 passed, 1 existujúce ImageIO/tifffile upozornenie, 15,06 s**. Testy bežali v izolovanom Dockeri bez kamery a produkčných dát. `git diff --check` bez chýb.

### Overenie vetvy určenej na merge do dev

V2 a opravy UI boli prenesené na čistý základ `origin/dev` bez nesúvisiacich A7 commitov. Celá sada na tejto vetve: **520 passed, 1 existujúce upozornenie, 14,70 s**. Rozdiel oproti 523 testom v pracovnom adresári tvoria tri testy z oddelených A7 zmien.
