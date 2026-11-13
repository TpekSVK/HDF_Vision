# Opravy z auditu spoľahlivosti — 13. 11. 2026

Implementované body 1, 2 a 7 auditu:

- Pico: asynchrónne chyby fyzického vstupu `ERR SESSION_IDLE` a
  `ERR CAPTURE_AFTER_LIGHT` sa nepriraďujú odpovediam STATUS, SESSION ani PIO.
  Pri priamom FIRE/TRIGGER ostávajú platnými chybovými odpoveďami.
- Pri úspešnej zmene relácie Pica sa zahodí výsledok predchádzajúceho MASTER
  snímania. Kontrola MASTER dokončenia nevyhodnocuje starú chybu v TRIGGER.
  Chyba aktuálneho MASTER cyklu sa naďalej oznamuje.
- Premenovanie receptu aktualizuje jeho lokálne `reference_assets_dir`
  v rozpracovanom aj publikovanom JSON a v pamäťových návrhoch nástrojov.
  Obsah vzoriek a modelov sa nemení. Pri chybe zápisu alebo DB premenovania
  sa vykoná návrat pôvodných dokumentov a priečinka. Cudzie cesty sa nemenia.

Experimentálny nástroj **mold.protection_v2** bol odstránený z aplikácie:
registrácia, výpočtové služby, dialóg učenia, zber spätnej väzby a súvisiace
obslužné vetvy v RUN. Existujúce experimentálne tabuľky a používateľské dáta
sa automaticky nemažú. Recept s odstráneným typom sa odmietne ako nepodporovaný;
nástroj sa pri načítaní receptu potichu nevynechá.

Produkčná **Kontrola prázdnej formy (mold.protection_v1)** zostáva zachovaná.
Jej používané interné služby s názvom `presence_absence_v2` sa neodstraňujú.

## Upresnenie bodov 3 a 4 pre aktuálne zapojenie lisu

Bod 3 opisuje výpadok doručenia výsledku **z aplikácie do riadenia lisu**.
Aplikácia môže mať snímku vyhodnotenú, ale lis nemusí dostať výstup.
Podľa používateľa má lis v programe limit **2 sekundy od odoslania žiadosti
o snímku po prijatie výsledku**. Ak výsledok nepríde v tomto čase alebo je NOK,
lis zastaví proces a vyžiada zásah operátora. Limit zahŕňa prijatie požiadavky,
snímanie, vyhodnotenie aj doručenie výsledku; nejde iba o čas algoritmu.
Výsledok sa má odovzdať hneď po dokončení kontroly; 2 sekundy sú iba
maximálna lehota. Pre produktivitu je cieľom čo najkratší celkový čas
od impulzu lisu po odovzdanie výsledku.
Praktickým dopadom straty výsledného signálu je zastavenie výroby.

Bod 4 sa týka iba spúšťania kontrol **cez Modbus TCP vstupy**. Nie je to
problém cesty **relé lisu → vstup Pico → snímanie kamery**, ktorú používateľ
používa. Pre túto cestu ide o neaktívne riziko, nie prioritu aktuálnej zostavy.

Kód bodov 3 a 4 sa v tejto zmene neupravoval. Nevykonávali sa výpadkové
skúšky s fyzickým lisom, Picom ani kamerami. Opravy sa načítajú pri novom
spustení aplikácie; nevyžadujú aktualizáciu firmvéru Pica.
