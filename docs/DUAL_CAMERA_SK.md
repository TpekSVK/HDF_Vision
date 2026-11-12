# Dve kamery s jedným Picom

Táto podpora je súčasťou vývojovej verzie `dev`; vznikla vo vetve
`codex/experimental-dual-camera`. Používa jeden recept s pohľadmi z dvoch kamier
a jedno spoločné Pico. Rozloženie obrazoviek, farby a dotykové ovládanie zostávajú
zachované. Prenos do `main` sa rieši samostatne.

## Spustenie a priradenie zariadení

Na pracovnej ploche Jetsonu spustite z priečinka projektu:

```bash
bash docker/run.sh
```

Pri existujúcom obraze `hdf_vision:dev` netreba kvôli tejto zmene znovu zostavovať
Docker obraz. Aplikácia načíta aktuálny kód z pracovného priečinka.

V **NASTAVENIA → Kamery a Pico** vyberte spoločné Pico a pri každej kamere
jej USB identitu a kanál dosky. Najskôr musia byť kontroly zastavené v SETUP.

| Rola v aplikácii | Trigger | Svetlo | USB kamera overená 12. 11. 2026 |
|---|---|---|---|
| Kamera 1 | GP16 | GP17 | `162A5806` |
| Kamera 2 | GP18 | GP19 | `1D3A5806` |

Spoločné Pico pri overení malo USB identitu `6f7618ff197a649c`.
Po prípadnej výmene káblov použite **Overiť dvojicu a zobraziť snímku**;
nespoliehajte sa na poradie `/dev/video0` a `/dev/video2`. Nakoniec uložte
priradenie. Obe kamery sa pri ďalšom spustení hľadajú podľa USB identity.
Jedna kamera nemôže patriť obom kanálom. Režim bez Pica sa nepridáva.

Na pripojenom Picu je už nahratý a fyzicky overený firmvér
**4.3.1-dual-channel**. Zdroj je `firmware/pico/main_v4.3.1.py`, nasadzovacia
kópia `main.py` je identická. Pre inú dosku použite
[postup aktualizácie](PICO_UPDATE_SK.md) pri zatvorenej aplikácii.

## Recept s dvoma kamerami

1. Vytvorte alebo otvorte recept v Golden Wizarde.
2. V **Upraviť pohľad** nastavte prvému pohľadu **Kamera 1**. Na prvé skúšky
   odporúčame rozlíšenie 1280 × 720, Y8 a primeranú expozíciu pre osvetlenie.
3. V správaní pohľadu vyberte výstupnú skupinu a samostatné alebo spoločné AND
   vyhodnotenie podľa nasledujúcej časti.
4. Získajte golden snímku a nastavte nástroje prvého pohľadu.
5. Cez **Pridať pohľad** vytvorte druhý pohľad, zvoľte **Kamera 2**, nastavte
   jeho snímanie a správanie. Získajte vlastnú golden snímku a nastavte nástroje.
6. Uložte a publikujte recept. Neúplné alebo protichodné priradenie sa odmietne
   s vysvetlením.

Po prepnutí pohľadu ovládajú Live, svetlo, golden, zber OK/NOK, učenie,
test nástroja a overenie polohy kamery práve kameru tohto pohľadu. Pri zmene
kamery sa golden a naučené podklady nekopírujú z druhej kamery. Golden si pamätá
USB identitu; po výmene fyzickej kamery treba získať nové podklady.
Pri nepriradenej kamere editor zostane otvorený, ale snímanie bude zablokované.

## Výstupné skupiny Modbus

V **Modbus Wizarde** zostáva nastavenie pripojenia a aktívnych výstupov.
V každej skupine priraďte odlišné relé pre OK a NOK; heartbeat je voliteľný.

| Skupina | Dostupné fyzické relé | Adresy protokolu |
|---|---|---|
| Skupina 1 | 1, 2, 3 | 0, 1, 2 |
| Skupina 2 | 4, 5, 6 | 3, 4, 5 |
| Rezerva | 7, 8 | 6, 7 |

Skupinu priraďujete pohľadu v Golden Wizarde. Nezávislé kamery musia mať
rôzne skupiny. Napríklad pohľad kamery 1 → skupina 1, pohľad kamery 2 → skupina 2.
Viac pohľadov tej istej kamery môže používať tú istú samostatnú skupinu.
Výstup sa určí z pohľadov spracovaných v danej kontrole vrátane povoleného vetvenia.

Pre spoločný výsledok vyberte pri oboch pohľadoch **Spoločné AND** a rovnakú
skupinu. Zobrazí sa **Limit AND**, ktorý nastavíte v milisekundách
(10 až 120 000 ms). Zmena limitu sa prenesie na ostatné pohľady tej istej
AND skupiny. Limit musí zahŕňať príchod snímok aj ich spracovanie.
Na úvod možno použiť napríklad 2000 ms a potom ho upraviť podľa merania.

AND vydá OK až po úspešnom vyhodnotení všetkých pohľadov skupiny. NOK,
chyba snímania, zastavenie neúplnej skupiny alebo vypršanie limitu znamenajú NOK
s uloženým dôvodom. Kamera, ktorá už do skupiny prispela, neprijme ďalšiu
požiadavku, kým sa skupina neuzavrie. Chýbajúca kamera môže svoju požiadavku
prijať. Požiadavky sa nehromadia vo fronte. Neskorý výsledok nemôže doplniť
nový cyklus starým OK. AND nemožno kombinovať s vetvením snímky.

Zatiaľ sa nepáruje identita výrobku. Dve snímky prijaté v otvorenom AND okne
sa považujú za ten istý diel/formu. Nastavte preto čas podľa skutočného pohybu linky.

## RUN a vstupy

Recept používa spoločný režim **MASTER** alebo **TRIGGER**. Aplikácia pripraví
obe potrebné kamery súbežne. Pri odchode do nastavení súbežne pozastaví oba
kanály; najskôr počká na prijaté kontroly a publikovanie výsledkov.
Golden Wizard pripravuje kameru vybraného pohľadu a pri otvorení neopakuje
predbežnú prípravu z hlavného okna.
Každá kamera má vlastné snímanie a spracovanie, pričom snímky
môžu prísť súčasne. Prepnutie zobrazovaného pohľadu nemení už prijatú kontrolu.
Po kontrole zostane vybraný pohľad zobrazený.

Nad obrazom sa zobrazuje názov vybraného pohľadu nastavený v Golden Wizarde;
v nastaveniach je toto miesto prázdne. Tlačidlo sa volá **Trigger**.
Hlavný výsledok zobrazuje **OK/NOK**, bez interného označenia skupiny.

**Pripravenosť kamier sa pri bežnej navigácii zachováva.** Prechod do nastavení
pozastaví kontroly, vypne externé vstupy, Live a svetlo, ale nezatvorí kamerové
spojenia. Návrat do RUN alebo otvorenie Golden Wizardu pri rovnakom profile
vykoná iba overenie pripravenosti a opätovné povolenie potrebného režimu.
Plná príprava sa vykoná pri spustení, zmene režimu MASTER/TRIGGER či relevantného
kamerového profilu alebo po strate pripravenosti/chybe. Nový recept ju potrebuje
len vtedy, keď mení takéto nastavenia. Zmena názvu, ROI alebo rozhodovacích
prahov sama osebe kameru znovu nepripravuje.

Vypnutie TRIGGER Live zastaví impulzy, nechá dobehnúť snímky a vyčistí zásobník
náhľadu. Pri úspešnom dobehnutí zostane pripravenosť zachovaná; následná kontrola
stále vyžaduje vlastnú úplnú dvojicu PRIME + CAPTURE. Chyba dobehnutia alebo
neočakávaná snímka pripravenosť zruší. Pri ukončení aplikácie alebo uvoľnení
kamier na overenie priradenia sa spojenia riadne zatvoria.

Manuálne tlačidlo **TRIGGER** spustí pri samostatnom vyhodnotení vybranú kameru;
pri vybranej AND skupine vyžiada oba kamerové príspevky. Fyzické vstupy nastavte
v **Pico Wizarde** pre vybranú kameru/kanál a povoľte potrebné vstupy. Každý kanál
má vlastné profily V1/V2 a mapovanie IN1–IN8. Rovnaký vstup môžete namapovať
obom kanálom, ak majú snímať na spoločný impulz. Dva rôzne vstupy umožnia
samostatné spúšťanie. Pri úprave oboch kanálov sa ich rozpracované nastavenia
uchovajú a spoločné uloženie uloží oba.

V pohľadoch nastavte zodpovedajúci externý zdroj/vstup alebo sekvenčné
spracovanie. Explicitná požiadavka na pohľad jednej kamery nevypne sekvenčný
príjem druhej kamery. Modbus vstupy sa smerujú cez rovnaké pravidlá pohľadov.
Pri obsadenej kamere sa nový impulz odmietne; nevytvorí sa oneskorená kontrola.
Fyzické vstupy v SETUP/IDLE nesnímajú. Zastavenie počká na prijaté spracovanie
a publikovanie výsledku; prípadná obnova TRIGGER je obmedzená a pri zastavení sa zruší.

## Výsledky a snímky

Vo **VÝSLEDKOCH** môžete vybrať Kamera 1, Kamera 2 alebo všetky kamery a použiť
existujúce filtre receptu, pohľadu, OK/NOK a dátumov. Každá snímka zostáva
priradená skutočnej kamere a pohľadu. AND členovia majú spoločné ID cyklu;
podrobnosti a CSV obsahujú skupinu, konečný výsledok skupiny a dôvod.
Aj pri chybe snímania vznikne záznam s vysvetlením, hoci snímka chýba,
ak je v recepte povolené zaznamenávanie výsledkov.

## Rozsah overenia a limity

Na pripojených kamerách a Picu prešlo 10 spoločných MASTER a 10 spoločných
TRIGGER cyklov pri 720p, spustených softvérovou požiadavkou na spoločnú skupinu.
Uložilo sa 40 snímok, 20 z každej kamery; filtre ich
správne rozdelili. Celý spoločný cyklus s jednoduchým MSE a ukladaním trval
približne 326–524 ms. Ide o krátku funkčnú skúšku, nie garanciu výkonu pre
plné rozlíšenie alebo náročné nástroje. Jednotlivé MSE výsledky môžu byť NOK
podľa scény; snímanie počas skúšky nezlyhalo.

Skúška skutočného rozhrania overila RUN, zachovanie zvoleného pohľadu,
otvorenie snímok a filtre, TRIGGER Live, samostatné svetlo a golden z oboch
kamier. Automatické skúšky navyše pokrývajú vytvorenie/publikovanie receptu,
AND timeout a odmietanie vstupov, paralelné spracovanie, obnovu po chybe,
ukladanie, skupiny Modbus a dotykové dialógy. Výstupy PLC boli pri fyzických
kamerových skúškach vypnuté; skutočné relé a dlhodobú záťaž treba ešte overiť
na zapojenej zostave. V tejto skúške sa neprivádzal samostatný vonkajší impulz
na 24 V vstupy; smerovanie fyzických vstupov overujú automatické testy firmvéru
a aplikácie. Protokoly sú v `artifacts/dual-camera-implementation/2026-11-12/`.

## Zálohy a návrat

Staré recepty v3 sa načítajú. Pred prvým uložením vo verzii4 vzniká presná
záloha s príponou `.pre-dual-v3.json`; pôvodné priradenie zariadení v1 sa pred
prechodom zálohuje ako `.pre-dual-v1.json`. Existujúca história sa nemaže.
Nová zostava používa spoločné recepty a históriu v koreňových dátach aplikácie;
staré podpriečinky nezávislých staníc sa automaticky nezlučujú.

Pred návratom na starú aplikáciu ju zatvorte a zálohujte aktuálny dátový priečinok.
Starý program nerozumie receptu v4; obnovte jeho zodpovedajúcu v3 zálohu aj
v1 priradenie zariadení. V3 záloha je stav pred prvým uložením, neobsahuje
neskoršie úpravy. Návrat firmvéru/config vykonajte podľa
[návodu na aktualizáciu](PICO_UPDATE_SK.md). Pôvodná fyzická záloha 4.2.1 je
v `artifacts/pico-backups/20261112T183754Z-154abb02`; nezdieľa sa cez Git.
Starý firmvér ovláda jednu kameru, takže návrat neumožní dvojkamerovú prevádzku.
