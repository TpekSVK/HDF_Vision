# Úvodné logo a spúšťanie bez terminálu

Pri spustení cez desktopovú ikonu sa najskôr zobrazí GTK prekrytie s existujúcim
logom a farbou pozadia aplikácie #14181d. Obrázok obsahuje chybný polopriehľadný
prvý stĺpec; pri vykreslení sa oreže, pôvodný PNG zostáva zachovaný.

Docker a Python sa načítavajú pod prekrytím. Qt po zobrazení okna a dokončení
prípravy kamier zapíše súkromný readiness marker; potom sa prekrytie plynulo
skryje za0,5s. Overené na skutočnom GNOME/X11 a oboch kamerách s receptom test2.
Prvý zaznamenaný štart bol pripravený približne8,3s od spustenia ikony.
`report.json` obsahuje skutočne namerané postupné opacity od plnej nepriehľadnosti
po0, zoznam okien pred/po a overenie, že druhé kliknutie nevytvorilo ďalšie okno.
`final-check.json` potvrdzuje aj následný beh s rotovaním výpisov na pozadí.

Terminál sa neotvára. Desktopová ikona aj GNOME autostart používajú nový launcher;
predchádzajúce súbory sú zálohované mimo Gitu v
~/.local/state/hdf-vision/startup-launchers/original. Výpisy sa uchovávajú
v application.log s5MB rotáciou a dvoma archívmi. Pri zlyhaní sa zobrazí správa
s možnosťou zavrieť úvodnú obrazovku; pri dlhom štarte možno aplikáciu odkryť.

Overenie:7 testov prešlo (readiness, zálohovanie/obnova launcherov a existujúce
pracovné okná), kontrola syntaxe Python/Bash a dva skutočné štarty.
Celý reštart/nové prihlásenie OS sa nevykonával; nastavila sa skorá fáza GNOME
autostartu. Systémové zobrazenie pred grafickým prihlásením sa nemení.
