# Aktualizácia HDF firmvéru Pico

## Bežné použitie

Zastavte výrobu, zatvorte HDF Vision a ostatné programy používajúce USB serial
(napr. Thonny). V termináli Jetsonu spustite:

```bash
cd /home/hdf-jetson1/HDF_Vision
bash docker/update-pico.sh
```

Skript automaticky vyberie jediné dostupné Raspberry Pi USB sériové zariadenie
a cez `STATUS` musí potvrdiť identitu `pico_hdf_controller`. Ak je zariadení viac,
vypíše porty aj USB sériové čísla a skončí bez aktualizácie. Vyberte konkrétne:

```bash
bash docker/update-pico.sh --port /dev/ttyACM0
```

Číslo `ttyACM0` nie je trvalá identita. Skontrolujte vypísané USB sériové číslo,
aby ste vybrali správne Pico; možno použiť aj jeho `/dev/serial/by-id/...` cestu.
Skript nikdy automaticky neaktualizuje všetky Pico naraz.

Iba kontrola verzie, bez prerušenia programu, zápisu alebo reštartu:

```bash
bash docker/update-pico.sh --check
```

Aj kontrola potrebuje voľný sériový port. Voľby `--check` a `--port` možno kombinovať.
Pomoc: `bash docker/update-pico.sh --help`.

## Čo znamená aktuálna verzia

Cieľom je `firmware/pico/main.py` **v lokálnej kópii projektu**. Aktuálne je to
`4.1-pio-pair`. Skript automaticky nesťahuje zdroje z internetu. Ak chcete novší
firmvér z projektu, najprv aktualizujte príslušnú lokálnu vetvu projektu.

Číselné verzie porovnáva ako čísla (napr. 4.10 je novšia než 4.9). Rovnakú
alebo novšiu číselnú verziu nenahráva; zmena prípony pri rovnakom čísle tiež
nespôsobí prepis. Obsahové zmeny firmvéru preto musia mať zvýšenú verziu.
Neznámu identitu/verziu alebo neodpovedajúce Pico odmietne.

Aktualizuje **aplikačný súbor `main.py`**, nie samotný MicroPython ani UF2.
Predpokladá už fungujúci MicroPython s HDF firmvérom a USB sériovým protokolom.
Pico v BOOTSEL alebo prázdne Pico týmto skriptom prvotne nenainštalujete.

## Postup aktualizácie

1. Overí voľný port, identitu a verziu. Obsadený port odmietne.
2. Iba pri staršej verzii preruší program a vstúpi do MicroPython raw REPL.
   Vypne výstupy svetla a triggeru GP17/GP16.
3. Overí, že verzia `main.py` vo flash zodpovedá bežiacej verzii.
4. Uloží na Jetson pôvodný `main.py`, konfiguráciu (ak existuje), pôvodný STATUS
   a manifest s USB identitou, verziami a SHA-256 záložných súborov.
5. Nahrá nový kód do dočasného súboru, prečíta ho späť a porovná všetky bajty.
   Overí aj kompiláciu priamo na MicroPython zariadení bez spustenia kódu.
6. Až potom premenuje overený súbor na `main.py`, zosynchronizuje flash
   a opäť overí obsah. Pôvodný `main.py` sa počas prenosu neprepisuje po častiach.
7. Urobí softvérový reštart, počká na READY a overí presnú verziu cez STATUS
   aj štart v `SESSION_MODE IDLE`.

Uložený `hdf_pico_config.json` nemení ani neposiela `SAVE`. Neuložené nastavenia
v RAM a manuálne zapnuté svetlo sa reštartom nezachovajú. Po úspešnom dokončení
spustite HDF Vision, ktoré znovu nastaví prevádzkový režim Pico.

Prenos používa [oficiálny MicroPython raw REPL protokol](https://docs.micropython.org/en/latest/reference/repl.html).
Reštart sa vyvoláva z bežného REPL, pretože [soft reset v raw režime preskakuje main.py](https://docs.micropython.org/en/latest/reference/reset_boot.html).

## Zálohy a chyba aktualizácie

Každý pokus o aktualizáciu vytvorí samostatný priečinok:

```text
artifacts/pico-backups/<UTC-čas>-<unikátny-identifikátor>/
  main.py
  hdf_pico_config.json   # iba ak existoval na Pico
  status-before.txt
  manifest.json
```

V termináli kontajnera je ten istý priečinok viditeľný pod `/backups/`.
Zálohy pretrvajú odstránenie kontajnera a sú ignorované Gitom. Vytvára ich
kontajner ako root; sú dostupné na čítanie, ich mazanie môže vyžadovať sudo.
Ak uloženie zálohy zlyhá, nový firmvér sa nenahráva.

Pri chybe počas aktualizácie sa skript pokúsi znovu nahrať pôvodný `main.py`
a overiť jeho štart. Aj po úspešnej obnove skončí s chybovým kódom, pretože
požadovaná aktualizácia sa nepodarila. Pôvodný firmvér môže mať odlišné
správanie po boote (napr. verzia 3.3 nemá SESSION IDLE); výroba musí zostať zastavená.

Pri odpojenom USB, výpadku napájania alebo poškodenej flash nemusí byť automatická
obnova možná. Skript to oznámi. Vtedy pred spustením výroby obnovte zo zálohy
`main.py` cez MicroPython nástroj, napr. Thonny, a overte verziu cez STATUS.
Záložnú konfiguráciu obnovujte iba na rovnaké Pico podľa `usb_serial` v manifeste.
Ak súbor konfigurácie v zálohe nie je, pôvodne na zariadení neexistoval.

## Prostredie a overenie

Spúšťač používa existujúci obraz `hdf_vision:dev` a jeho `pyserial`; netreba
inštalovať ďalšie knižnice na Jetson ani zostavovať nový obraz. Názov obrazu
možno zmeniť cez `IMAGE_NAME`. Skript funguje aj pri spustení z iného priečinka.

Kontajner má prístup k USB zariadeniam a hostiteľským procesom, aby vedel zistiť
obsadený port; sieť je vypnutá a projekt je pripojený iba na čítanie. Priame
spúšťanie Python nástroja mimo tohto spúšťača nie je určené na bežnú obsluhu.

Automatizované testy simulujú porovnanie verzií, viac zariadení, zálohy,
zlyhanie zápisu/štartu, obnovu, chybu kompilácie a raw REPL komunikáciu.
**Nahrávanie na skutočné Pico zatiaľ týmto novým skriptom nebolo overené.**

Zdroj: [spúšťač](../docker/update-pico.sh), [aktualizátor](../tools/update_pico.py),
[testy](../tests/test_update_pico.py), [Pico firmvér](../firmware/pico/README.md).
