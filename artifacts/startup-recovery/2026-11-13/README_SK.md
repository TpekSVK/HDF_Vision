# Obnova prípravy kamier — 13. 11. 2026

Oprava po hlásení chyby TRIGGER po reštarte Jetsonu.

Overené na pripojených kamerách 162A5806 a 1D3A5806 a spoločnom Picu,
2592 × 1944, 30 fps, v dočasnej kópii receptu test2. Modbus bol vypnutý.
Pôvodné recepty a výsledky sa nemenili.

Hardvérová skúška skutočného Qt rozhrania prešla tromi scenármi:

- Senzory nastavené na MASTER, uložený režim aplikácie TRIGGER: úspešný RUN.
- Jednorazovo vyvolaná chyba prípravy kamery 1: druhý pokus uspel.
- Trvalá chyba prípravy kamery 1: presne tri pokusy, návrat do SETUP;
  po odstránení chyby manuálna voľba TRIGGER a návrat do RUN uspeli
  v tom istom procese bez reštartu aplikácie.

V každom scenári sa získali skutočné snímky z oboch pohľadov.
RUN → SETUP → RUN nezvýšil počet štartov streamov. Po zatvorení pracoviska
sa staré Pico spojenie neotvorilo znova a sériový port sa dal výlučne otvoriť.
`wrapped_prepare_calls` zahŕňa aj neskoršie volania počas snímania/navigácie;
asercia v skripte samostatne overuje dva úvodné pokusy pri prechodnej chybe.
Režim senzorov sa na konci obnovil, cleanup bez chyby.

Regresia: 685 testov, 10 izolovaných dávok, všetky úspešné.

Neuskutočnil sa ďalší reštart operačného systému ani vypnutie napájania.
MASTER po zapnutí sa simuloval nastavením senzorov a zatvorením streamov.
Skutočný štart po reštarte Jetsonu preto ešte treba potvrdiť pri ďalšom reštarte.

Skript je protokol pre túto konkrétnu zostavu; vyžaduje voľné kamery/Pico,
pripojené zariadenia v kontajneri a dočasné mounty `/source` (kópia dát po
čistom zatvorení aplikácie), `/out` a `/workspace`. Nespúšťať súčasne s aplikáciou.
