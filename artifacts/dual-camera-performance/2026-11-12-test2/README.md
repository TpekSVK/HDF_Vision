# Súbežné snímanie aktuálneho receptu test2

Jetson, dve See3CAM CU55, spoločné Pico4.3.1, TRIGGER,2592×1944 Y8/30fps.
Nezmenená kópia publikovaného receptu test2: celust/kamera1/expozícia500µs,
diel/kamera2/expozícia1000µs, oba nástroje SSIM a pôvodné ROI/práhy.
SHA256 publikovaného receptu je uložené v report.json.

## Výsledok

Priemerné časy od spustenia kamerovej úlohy:

| Pohľad | Snímanie + výpočet samostatne | Snímanie + výpočet pri súbehu | Vrátane uloženia samostatne | Vrátane uloženia pri súbehu |
|---|---:|---:|---:|---:|
| celust |157,1ms |176,4ms |183,7ms |201,4ms |
| diel |140,6ms |149,4ms |165,7ms |175,2ms |

Oba výsledky pri súbehu: priemer176,8ms do skončenia výpočtu,
202,1ms do dokončenia uloženia a publikácie v teste. P95 je184,0ms, resp.206,7ms.
Samotný čas pipeline podľa jej interného merania: celust25,45→25,64ms,
diel8,93→10,03ms. Čas úseku snímania: celust121,77→136,07ms,
diel121,82→125,83ms. Väčšina navýšenia preto pripadla na snímanie/prenos,
nie na samotný výpočet SSIM. Nemožno z toho oddelene určiť presný podiel USB,
drivera a ostatného spracovania v úseku snímania.

## Metodika a hranice

- Tri scenáre: iba celust, iba diel, oba súčasne.20 meraných opakovaní každého.
- Pred meraním tri zahrievacie opakovania každého scenára; v štatistike nie sú.
  Celkovo69 behov/92 obrázkov, z toho60 behov/80 obrázkov v štatistike.
- Poradie scenárov premiešané v každom kole s pevným seed1211.
- Reálne snímanie, pôvodný produkčný runtime a ukladanie obrázkov/SQLite,
  dva kamerové pracovné thready. Pri súbehu majú samostatné vstupné požiadavky
  bariéru štartu; maximálny rozdiel štartov úloh0,42ms.
- Ide o súčasné vyžiadanie fyzických snímok, nie umelé vynútenie rovnakého
  okamihu doručenia obrazov z USB. Výpočty sa prekrývali v priemere3,50ms.
- Logging True, export_artifacts False podľa receptu. OpenCV6 threadov,
  bez úprav počtu threadov, frekvencií alebo výkonového režimu hostiteľa.
- Výstupy Modbus vypnuté, žiadne fyzické PLC zápisy. Časy nezahŕňajú vykreslenie
  Qt ani sieťovú komunikáciu so skutočným PLC. Sú použiteľné pre porovnanie
  scenárov tejto skúšky; nejde o záťažovú kvalifikáciu ani garanciu maxima.
- Všetky merané kontroly skončili OK, bez chyby snímania a bez dodatočných
  štartov GStreamer. Pôvodný recept ani jeho produkčná história sa nemenili.
- Prvá úvodná príprava ešte pred meraním skončila známou chybou
  `Invalid HID command echo: 0x00`; jej protokol je zachovaný ako setup-first-attempt.
  Opakovaný celý štart a všetky zahrievacie/merané kontroly prešli.
  Časy prípravy nie sú súčasťou štatistiky.
- Po skúške sa obnovili kamerové nastavenia, kanály sa vypli do IDLE a aplikácia
  bola znovu spustená s pôvodnými dátami. Meranie bežalo v oddelenom dočasnom
  dátovom priečinku, bez produkčných zásahov do receptu a výsledkov.

`measurements.csv` obsahuje jednotlivé výsledky; `report.json` aj absolútne
časové značky jednotlivých fáz, pôvodné nastavenia a stav ukončenia.
`comparison.json` obsahuje priemery pre tabuľku. `benchmark.py` je použitá sonda
viazaná na túto zostavu a skopírované dáta v /out/data; nepúšťať popri aplikácii.
