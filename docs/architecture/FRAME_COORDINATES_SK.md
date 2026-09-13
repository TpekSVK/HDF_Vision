# Súradnice snímok – A4 dokončená 2026-09-13

Aktuálne rozlišujeme:

1. Surová snímka kamery (pred otočením pohľadu).
2. Nezarovnaná kontrolná snímka (otočenie pohľadu už aplikované).
3. Locatorom zarovnaná snímka pre vyhodnotenie/náhľad.
4. Zobrazovacia snímka s ROI, filtrami alebo anotáciami.

Do iného pohľadu sa prenáša iba bod 2. InspectionFrame v services/frame_coordinates.py
nesie pixely a uhol, o ktorý sú otočené voči kamere. Cieľové pixely vzniknú otočením
o rozdiel cieľového a zdrojového uhla. Zachová sa tak rovnaký výsledok, ako pri
priamom otočení surovej snímky pre cieľový pohľad. Vráti sa nezávislá kópia.

RUN ukladá tieto záznamy do captured_frames v aktuálnom trigger_state a používa
ich pri frame_source_view_id aj vetvení (injected_capture). Neprenáša locatorom
zarovnaný alebo anotovaný náhľad. Chýbajúci zdroj vyvolá chybu; runtime ju zachytí,
aktualizuje stav kontroly a vyšle NOK. Neberie sa _get_last_frame_for_view ani
nová snímka z kamery ako tichá náhrada chýbajúceho zdroja.

A5 rozšírila životnosť zdrojových snímok aj na explicitné krokované sekvencie.
Runtime drží oddelený kontext pre zdroj sekvencie: cycle_id, captured_frames a
frame_ids. Nový prvý krok vytvorí nový cyklus; zmena receptu, chyba a pozastavenie
kontexty resetujú. Prevzatý pohľad dedí frame_id, nový trigger má vlastné request_id.
Nie je to cache posledného preview. Podrobnosti v INSPECTION_CONTROLLER_SK.md.

## Zistenia z pôvodných locator testov

- Šablóna veľká ako oblasť hľadania nemá priestor na posun. Testy posunu teraz
  explicitne vyberajú menšiu šablónu v širšej oblasti, presnosť posunu/SSIM zostala.
- Pre rotáciu nestačí niekoľkopixelový gradient so slabo rozlíšiteľným uhlom.
  Použitá je deterministická textúra 24 × 24, šablóna 32 × 32, obraz 96 × 96.
  Tolerancia uhla zostáva ±1°, kontroluje sa aj matica známej transformácie.
- OpenCV kladný uhol rotácie obrazu zodpovedá zápornému theta v používanom
  obrazovom súradnicovom systéme. Translácia pri otočení okolo stredu nie je nula.
- Jednoliata šablóna sa správne odmieta pred downsamplingom. Pôvodný test
  požadoval resize/cache pre konštantnú šablónu; teraz kontroluje odmietnutie.
- Overlay export je v recepte opt-in; pôvodný test ho očakával pri vypnutom exporte.
- Po odblokovaní rotácie sa ukázal nesprávne zaradený blok assertionov s neexistujúcimi
  premennými results/diagnostics/context. Presunutý späť do testu chýbajúcej zhody.
- Locator algoritmus sa v tomto posedení nemenil. Zmenili sa chybné testovacie
  predpoklady; 15 testov locatora/pipeline teraz prechádza bez vypínania testov.

## Historicky zostávalo po A4a (dokončené v A4b/A4c)

- Zjednotiť prípravu učenia s RUN vrátane predchádzajúceho locatora.
- Určiť a implementovať podpis platnosti modelu: relevantný profil, rotácia, ROI,
  maska a predspracovanie; zmena musí vyžadovať nové učenie bez mazania dát.
- Doplniť integráciu prípravy snímky do menšej služby mimo UI.
- Overiť všetky editorové cesty na uloženom recepte a až potom hardvérové snímanie.

## A4b – učenie používa prípravu RUN (2026-09-13)

services/statistical_learning.py spustí aktuálne povolené locatory v rovnakom
poradí ako RUN a následne použije PairTool prípravu ROI/masiek. Analyzer nástroje
sa pri zbere nespúšťajú. Snímka musí mať rozmery golden a ROI musí ležať v obraze.
Pri zlyhaní zarovnania sa vzorka neuloží; automatický zber sa zastaví.

Opravená spoločná PairTool cesta: ak frame_is_aligned=False, prítomnosť
frame_aligned_gray nie je dôkazom zarovnaných pixelov (môže to byť pôvodná snímka).
Pri nenulovej T_total sa pripraví virtuálne zarovnaný obraz inverznou transformáciou.
Vzorky a modely tak používajú rovnaké pixely pri apply_alignment=True aj False.

SAMPLE_PREPARATION_VERSION=2 je uložená pri zbere v parametroch a pri učení do
štatistík modelu. Staré modely bez verzie sa v RUN odmietajú. Staré vzorky nemožno
prepočítať a označiť za nový model; vyžaduje sa nový zber. Existujúci používateľský
reset vyžaduje potvrdenie; tieto zmeny samotné žiadne modely ani vzorky nemažú.
Runtime tiež rešpektuje reference_model_invalidated, aj keď je ready=True.

Po A4b zostával podpis konkrétnej konfigurácie. Ten doplnila A4c nižšie. Príprava virtuálne zarovnaného obrazu je momentálne per-tool;
prípadné zdieľanie cache patrí až po testoch správnosti do optimalizačnej etapy.


## A4c – uzavretie

Podpis vzoriek a modelov je implementovaný v services/learning_context.py.
Presný kontrakt a správanie po zmenách uvádza LEARNING_CONTEXT_SK.md.
RUN aj Golden test odmietnu model pri zmene jeho vstupov; zlyhaný locator
nikdy nevedie k vyhodnoteniu štatistického modelu na nezarovnanom výreze.

apply_view_rotation v UI je len wrapper nad InspectionFrame; nemá druhú
OpenCV/fallback implementáciu otáčania. Súradnice ROI/masiek ostávajú v otočenom
golden priestore, filtre/anotácie menia iba zobrazovanú kópiu.

Syntetická regresia uzavrela A4 (174 testov v konečnej sade). Fyzické kamery/Pico,
reálne recepty a záťaž budú overené samostatne v A7; ich overenie sa tu nepredstiera.
