# Spúšťanie HDF Vision s logom a bez terminálu

Tento návod nastaví ikonu na pracovnej ploche a automatické spustenie aplikácie
po grafickom prihlásení. Počas načítavania sa zobrazí logo HDF Vision na pozadí
vo farbe aplikácie. Po dokončení úvodnej prípravy kamier sa logo počas približne
0,5 sekundy plynulo skryje. Terminál sa pri spustení z ikony neotvára.

## Čo musí byť pripravené

- HDF Vision stiahnuté v priečinku, z ktorého sa bude bežne používať.
- Funkčné bežné spustenie cez `bash docker/run.sh`, vrátane Docker obrazu
  `hdf_vision:dev`, prístupu k Dockeru a potrebných zariadení.
- Grafická pracovná plocha GNOME/X11, Python 3 a GTK3/PyGObject na hostiteľskom
  Jetsone. Na overenom Jetsone už boli dostupné; pre logo sa nič neinštalovalo.
  Pri minimálnom systéme bez pracovnej plochy nemusia byť prítomné.

Nastavenie vykonajte priamo na pracovnej ploche Jetsonu ako používateľ, ktorý
bude aplikáciu používať. **Bez sudo a mimo Dockeru.** Kvôli tejto funkcii netreba
znovu zostavovať existujúci Docker obraz.

## Jednorazové nastavenie

Otvorte terminál v koreňovom priečinku stiahnutej aplikácie. Na tomto Jetsone:

```bash
cd /home/hdf-jetson1/HDF_Vision
python3 tools/setup-launcher.py
```

Na inom Jetsone použite jeho vlastnú cestu k projektu. Skript si túto cestu
uloží do spúšťacích súborov automaticky.

Upravia sa dva súbory aktuálneho používateľa:

- `~/Desktop/HDF_Vision.desktop` — ikona na pracovnej ploche.
- `~/.config/autostart/HDF_Vision.desktop` — automatické spustenie po prihlásení.

Oba spúšťajú `tools/launch-app.py` bez terminálu a bez pôvodného pevného
osemsekundového čakania. Automatický štart používa skorú fázu GNOME.
Nastavenie sa dá zopakovať; prvá záloha pôvodných súborov sa neprepíše.
Ak priečinok projektu neskôr presuniete, spustite nastavenie z jeho novej cesty.

## Používanie a náhľad

Na spustenie kliknite na ikonu **HDF Vision**. Pri nasledujúcom grafickom
prihlásení sa aplikácia spustí automaticky. Nastavenie samo nemení automatické
prihlasovanie používateľa do systému.

Spustenie s logom možno vyvolať aj priamo:

```bash
python3 tools/launch-app.py
```

Samostatný približne päťsekundový náhľad loga, ktorý nespúšťa Docker ani kamery:

```bash
python3 tools/launch-app.py --preview
```

Logo sa pri skutočnom štarte skryje až po zobrazení rozhrania a dokončení úvodnej
prípravy. Zobrazí sa aj rozhranie s chybovým stavom, aby bolo možné problém
riešiť v aplikácii. Ak samotné spustenie zlyhá, úvodná obrazovka ukáže správu
a tlačidlo na zavretie. Pri načítavaní dlhšom než 90 sekúnd možno aplikáciu odkryť.
Opakované kliknutie na ikonu počas behu nevytvorí druhú kópiu spustenú týmto
launcherom. Ručné spúšťanie cez iné príkazy týmto zámkom chránené nie je.

Pre servis zostáva dostupný pôvodný príkaz s výpismi v termináli:

```bash
bash docker/run.sh
```

Nespúšťajte ho súčasne s už bežiacou aplikáciou nad rovnakými kamerami a Picom.

## Výpisy a zálohy

Výpisy zo skrytého spustenia sú štandardne v súbore:

```text
~/.local/state/hdf-vision/logs/application.log
```

Ak je nastavené `XDG_STATE_HOME`, výpisy sa ukladajú pod tento priečinok namiesto
`~/.local/state`. Súbor sa rotuje pri veľkosti 5 MB; ponechávajú sa dva archívy.
Aktuálne výpisy si môžete prečítať napríklad takto:

```bash
tail -n 100 "${XDG_STATE_HOME:-$HOME/.local/state}/hdf-vision/logs/application.log"
```

Prvé pôvodné spúšťacie súbory sú zálohované v:

```text
~/.local/state/hdf-vision/startup-launchers/original
```

## Obnova pôvodného spúšťania

Z koreňa projektu spustite ako ten istý používateľ:

```bash
python3 tools/setup-launcher.py --restore
```

Obnovia sa pôvodné súbory ikony a automatického spúšťania. Ak niektorý pred
nastavením neexistoval, odstráni sa jeho nová kópia. Po úspešnej obnove sa táto
záloha odstráni; nasledujúce nastavenie vytvorí novú. Už bežiaca aplikácia sa
obnovou neukončí. Recepty, výsledky, priradenie kamier ani firmvér Pica sa nemenia.

## Rozsah overenia

Na tomto Jetsone sa overilo spustenie z ikony bez terminálu, čakanie na obe
kamery, plynulé skrytie loga a ochrana pred opätovným spustením. Celý reštart
Jetsonu sa pri overovaní nevykonával. Logo prekrýva plochu počas spúšťania
aplikácie v grafickej relácii; nemení systémové zobrazenie pred prihlásením.

Ďalšie príkazy sú v [prehľade príkazov](PRIKAZY_SK.md).
