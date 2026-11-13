# Zachovanie prípravy samostatnej kamery

Dátum: 13. 11. 2026.

Jednokamerová vetva zatvárala trigger stream v `InspectionRuntime.quiesce`,
`MainWindow._exit_run_trigger_session` a pri normálnom odchode z diagnostickej
`CameraPositionService.close_session`. Dvojkamerová zostava už používala
odlišnú cestu so zachovaním streamu.

Bežný prechod teraz len pozastaví požiadavky a svetlo cez Pico.
Zdravý stream zostáva otvorený aj pre jednu kameru. Zmena profilu naďalej
vyvolá potrebnú prípravu cez existujúce overenie profilu a PIO signatúry.
Chyba prípravy/snímania v samostatnej Diagnostike naďalej uvoľní trigger
stream. Úplné zatvorenie aplikácie naďalej stream zastaví.

Regresia: 699 testov v 10 izolovaných dávkach, bez zlyhania. Testy overujú
bežnú pauzu samostatného runtime, úplné zatvorenie, prechody diagnostického
rozhrania, zachovanie streamu pri odchode z diagnostiky a cleanup po chybe.
Dočasný pytest bol nainštalovaný iba do /tmp; závislosti aplikácie sa nemenili.

Ide o softvérové a Qt regresné testy v kontajneri. Jednokamerová zostava
na pracovnom Jetsone nebola počas tejto opravy priamo dostupná a hardvérová
skúška na nej neprebehla. Po aktualizácii vetvy dev treba aplikáciu znovu
spustiť a potvrdiť prechody s nezmeneným kamerovým profilom. Krátky stav
prípravy pri návrate do RUN sám osebe neznamená reštart streamu.
