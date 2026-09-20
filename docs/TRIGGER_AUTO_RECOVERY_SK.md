# Automatická obnova po neúplnej PIO dvojici

V RUN/TRIGGER sa automaticky obnovuje iba typovaná chyba IncompletePioPair
(0/2 alebo 1/2 snímok). Iné chyby ani bežný NOK z nástroja obnovu nespúšťajú.
Chybná kontrola zostáva failed; existujúca NOK cesta Modbus sa nemení.
Obnova neposiela nový OK a neprehráva požiadavku pôvodného dielu.

Počas obnovy je kontrolér PREPARING, nové vstupy sa odmietajú bez fronty.
Pracuje existujúci pracovník konkrétnej stanice, ostatné kamery majú vlastný
kontrolér a limit. Najviac dva pokusy bez intervenujúcej úspešnej kontroly.
Úspešná overovacia dvojica limit nevynuluje, dokončená kontrola áno.
Ručný vstup operátora do novej RUN session po príprave limit resetuje.

Každý pokus: Pico IDLE, kamera MASTER, overenie20 snímok, kamera TRIGGER,
ustálenie a pôvodné prípravné impulzy1+2. Identita streamu a počet otvorení
sa musia zachovať. GET MODE pri presnom neplatnom echo0x00 má najviac tri
opakovania po20ms; SET sa neopakuje naslepo. Neúspech/cancel ponechá Pico IDLE.
Nefunkčný stream, zmena streamu, nepotvrdený režim alebo nekompletná overovacia
dvojica nemôžu viesť k READY. Pri vyčerpaní pokusov zostane ERROR.

RUN karta ukazuje samostatné upozornenie, číslo pokusu a konečný výsledok.
Upozornenie sa neprepisuje normálnym výsledkom ďalšieho dielu. Žiadosť o SETUP
alebo zatvorenie zruší obnovu medzi ohraničenými hardvérovými operáciami.

Audit je v data_root/logs/trigger_recovery_YYYYMMDD.jsonl (dátum a čas UTC),
s identitou kamery/Pico, pôvodnej požiadavky a chyby, pokusom, trvaním,
HID read retry a výsledkom. Zápis nezávisí od zapnutia histórie receptu.
Ak zápis auditu zlyhá, obnova neprejde do READY a zostane aj aplikačný error log.

Overenie automatizovanými regresiami nepredstavuje akceptáciu zapojenia lisu.
Laboratórne merania preukázali možnosť obnovy, nie odstránenie príčiny výpadkov.
