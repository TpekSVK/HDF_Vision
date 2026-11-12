# Zachovanie pripravenosti pri bežnej navigácii

Zostava so spoločným Picom pri pauze RUN ponechá kamerové spojenia otvorené,
ale oba kanály prepnú do IDLE, Live sa zastaví a svetlá vypnú. Výsledky sa pred
pauzou dokončia. Zatvorenie aplikácie a overovanie priradenia naďalej zariadenia
uvoľnia. Diagnostika pri návrate nezatvára kamerové spojenie zdieľanej zostavy.

Live OFF čaká na dobehnutie snímok ešte v režime náhľadu a vyčistí jeho zásobník.
Úspešný prechod nezruší pripravenosť. Kontrola naďalej vyžaduje vlastnú úplnú
PRIME + CAPTURE dvojicu; chyby dobehnutia a neočakávané snímky platnosť zrušia.
Odpojenie/reštart dosky zneplatní pripravenosť oboch kamier.

## Reálne overenie

- Obe kamery720p, Pico4.3.1, dočasné recepty a DB, Modbus vypnutý.
- 10 MASTER a10 TRIGGER kontrol,40 uložených obrázkov bez chyby snímania.
- Pred každou kontrolou sa pozastavil a obnovil RUN; pri TRIGGER predchádzal
  aj Live ON/OFF na oboch kamerách. Počet štartov GStreamer aj identity
  kamerových spojení zostali nezmenené. Preto sa overovala aj prvá kontrola
  bezprostredne po každom návrate z Live/pauzy.
- Obnovenie RUN TRIGGER15–19ms, MASTER32–48ms; tieto čísla platia pre pripravenú
  zostavu a rovnaký profil v krátkej skúške, nie studený štart.
- Skutočné Qt rozhranie: RUN → SETUP → MainWindow.open_wizard → Live/golden
  oboch kamier → RUN, bez dodatočných štartov GStreamer (`gui-report.json`).
- Pôvodné hardvérové nastavenia obnovené, kanály skončili IDLE, svetlá vypnuté.
  Potom bola používateľská aplikácia opäť spustená s pôvodným receptom test2.

Cielená regresia: **672 passed**,8 izolovaných dávok vrátane635 základných testov.
Nové skúšky kontrolujú zachovanie spojenia, odvod snímok po Live aj zneplatnenie
pripravenosti po chybe. MSE NOK podľa scény je normálny verdikt, nie chyba snímania.
Skripty majú pevné identity hardvéru a dočasné cesty rovnako ako pôvodné sondy;
nespúšťajú sa súčasne s používateľskou aplikáciou.
