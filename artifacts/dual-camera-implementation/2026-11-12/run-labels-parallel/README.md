# Názvy RUN a súbežná príprava

Nad obrazom je názov pohľadu z receptu, v SETUP je názov skrytý, tlačidlo má
text Trigger a hlavný výsledok OK/NOK bez interného group_id. Golden Wizard
pripraví konkrétny pohľad sám, bez predbežnej a následnej prípravy v MainWindow.

WorkcellRuntime pripravuje aj zastavuje kamery v dvoch samostatných úlohách.
Pred čistením po chybe počká na obe úlohy. Automatické testy používajú bariéry
na potvrdenie prekrytia prípravy/zastavenia a kontrolujú chybu počas prípravy.
UI skúška kontroluje premenovanie pohľadov celust/diel a prázdny názov v SETUP.

Na pripojených kamerách prešlo 10 MASTER + 10 TRIGGER cyklov pri720p;
40 uložených snímok, bez chyby snímania a ukončenia, PLC vypnuté.
Príprava TRIGGER trvala celkovo2262ms, jednotlivé kamery2258/2256ms,
pričom2256ms prípravy prebiehalo súčasne. Súčet samostatných príprav by bol4514ms.
Nejde o porovnanie dvoch nových meraní studeného štartu pred/po zmene;
porovnávajú sa intervaly tej istej skúšky. MASTER sa pripravoval na už otvorených
streamoch, preto jeho krátky čas nemožno použiť ako čas studeného štartu.

`hardware-report.json` obsahuje intervaly a výsledky. Prípadné MSE NOK sú
verdikty nástroja podľa snímky/scény, nie zlyhané snímanie. Pôvodné nastavenia
kamier boli obnovené. Potom bola používateľská aplikácia znovu spustená
s existujúcim receptom test2 a názvami celust/diel (`run-view-name.png`).

Záverečná cielená regresia: 669 passed,8 izolovaných dávok.
