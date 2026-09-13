# Platnosť učenia a modelov – A4

Dokončené 2026-09-13. Platí pre Prítomnosť/neprítomnosť V2 a Ochranu formy V1.

## Jedna príprava pixelov

Snímka pre učenie má už aplikovanú rotáciu pohľadu. LearningSamplePreparer
spustí povolené locatory v poradí RUN, potom spoločnú prípravu PairTool.
Výrez ROI a vylúčená maska preto zodpovedajú vstupu modelu v produkcii.
Platí to pre fyzické aj virtuálne zarovnanie. Nesprávne rozmery, ROI mimo obrazu,
prázdna platná oblasť alebo chybný locator znamenajú odmietnutie vzorky.

## Podpis kontextu

services/learning_context.py vypočíta learning_signature (SHA-256). Zahŕňa:

- verziu prípravy pixelov (2) a verziu podpisového kontraktu (1),
- obsah, rozmery a typ golden obrazu; samotná cesta alebo dátum súboru nestačia,
- camera_profile, image_rotation, pico_profile, flash_delay_ms, flash_pulse_ms,
  trigger_gap_ms a frame_source_view_id aktuálneho pohľadu,
- typ kontrolného nástroja, jeho ROI a obsah vylúčenej masky,
- povolené locatory v poradí vykonávania, ich typy, ROI, masky, parametre a prahy.

Podpis nezahŕňa názvy, umiestnenie golden súboru, počítadlá, ready flagy ani
rozhodovacie prahy analyzera. Zmena citlivosti/limitov objektov či polarity
nevyžaduje nový medián/MAD: nemení prípravu uložených pixelov. Prahy locatora
sa zahrnujú, pretože určujú prijatie zarovnania ešte pred vznikom vzorky.
Pri budúcej zmene prípravy pixelov treba zvýšiť SAMPLE_PREPARATION_VERSION;
nové konfigurovateľné predspracovanie musí pridať svoje parametre do podpisu.

## Uloženie a kontroly

Pri vzorkách je sample_context.json s learning_signature a verziou prípravy.
Pred pridaním dávky sa skontroluje existujúci manifest a nový sa uloží atomicky.
Existujúce PNG bez platného manifestu nie je možné označiť ako novú dávku.
Všetky vzorky majú jedinečné názvy aj pri rýchlom uložení z pamäte.

Rebuild kontroluje manifest a uloží totožný podpis do model/stats.json.
Pridanie ďalších vzoriek nastaví reference_model_needs_rebuild; staré súbory
modelu ostanú zachované, ale aktívny nástroj ho nepoužije pred novým prepočtom.
Reset vzoriek/modelu/manifestu vykoná iba existujúca potvrdená akcia používateľa.

RUN aj test nástroja počítajú očakávaný podpis z aktuálneho receptu a golden.
Hash golden sa zdieľa medzi štatistickými nástrojmi v rámci jedného volania.
Chýbajúci/starý podpis, nezhoda, invalidated alebo needs_rebuild znamenajú
nepripravený model. V2 hlási WARN, ochrana formy NOK. Zlyhanie locatora tiež
zabráni vyhodnoteniu modelu, aj keď iné nástroje smú pokračovať bez zarovnania.

Golden Wizard kontroluje rovnaký podpis pri zbere, obnove panelu, prepočte,
validácii a použití automatických odporúčaní. Staré dávky sa nemiešajú s novými.
Premenovanie/presun poradia nástroja zachová už uloženú cestu jeho vzoriek
v danom pohľade; názov preto nie je dôvodom na stratu učenia.

## Hranice etapy

Podpis dokazuje zhodu zaznamenanej konfigurácie; neoveruje fyzické zapojenie,
aktuálne napájanie, skutočné nastavenie cudzieho zariadenia ani zmenu scény.
Budúca podpora viacerých kamier/Pico musí doplniť stabilné identity a skutočný
zdrojový capture profil. Teraz sa nepoužívajú globálne modelové cache ani implicitné
poradie USB zariadení. Zdrojové snímky patria jednému explicitnému capture cyklu.

Staré modely ani vzorky sa automaticky nemigrujú a nemažú. Po nasadení týchto
zmien bude potrebné nové učenie. Overenie tejto etapy je syntetické v izolovanom
Docker prostredí; hardvérové prepínanie, záťaž a studený štart ostávajú A7.
