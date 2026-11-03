# Live náhľad v TRIGGER režime – Pico 4.2.1

RUN aj Golden Wizard používajú spoločnú službu pre Live. Kamera zostáva
v TRIGGER režime. Pico generuje impulzy hardvérovo cez PIO pri 1 MHz;
USB komunikácia ani hlavná slučka MicroPython neurčujú ich rozostup.

Aplikácia posiela periódu z aktívneho overeného kamerového profilu:

| Rozlíšenie | Maximálne fps | Perióda nábežných hrán |
|---|---:|---:|
| 2592 × 1944 | 30 | 33 340 µs |
| 1920 × 1080 | 60 | 16 670 µs |
| 1280 × 720 | 60 | 16 670 µs |
| 640 × 480 | 112 | 8 930 µs |

HIGH trvá 100 µs. Ak expozícia presiahne základnú periódu, použije sa
rovnaké predĺženie ako pri produkčnom trigger páre (`expozícia + 100 µs`).
Obnovovanie UI má vlastný limit; nemusí zobraziť každú prijatú snímku.

## Obraz a prechody

Prvá snímka po spustení Live je PRIME a nezobrazuje sa. Pri krátkom teste
so svetlom mala práve prvá snímka vo všetkých rozlíšeniach svetlý horný pás.
Aplikácia vyčistí starý náhľad a počká na ďalšiu snímku pred zapnutím UI časovača.

Pri LIVE OFF Pico zastaví PIO a nastaví trigger na LOW. Aplikácia nechá
rozbehnuté snímky dobehnúť ako preview, až potom zruší príznak Live a vynúti
nové pripravenie produkčného páru. Preview snímky sa nepoužijú ako výsledok
kontroly ani ako Golden snímka. Golden capture najprv vypne Live.

Svetlo ovláda samostatné tlačidlo Svetlo. Pri jeho prepnutí sa dva prechodové
zábery nezobrazia a náhľad podrží posledný čistý obraz. Ak pripravenie kamery
zruší manuálne svetlo, služba ho pred Live obnoví. LIVE ON/OFF nemení jeho stav;
PIO ovláda iba GP16. Odchod z RUN a zatvorenie Golden Wizard vypnú Live aj
svetlo. Zmena SESSION takisto zastaví Live a vypne svetlo. Počas Live sa
fyzické vstupy nespracúvajú a PIO FIRE vracia BUSY LIVE.

## Protokol a kompatibilita

STATUS obsahuje schopnosť `LIVE_PIO_V1` a stav `LIVE_PREVIEW` s periódou
aj šírkou impulzu. Aplikácia vyžaduje túto schopnosť, aby sa so starším firmvérom
nespustilo pôvodné softvérové časovanie s pevnou periódou.

```text
LIVE ON <period_us> <pulse_us>
OK LIVE ON <period_us> <pulse_us>
LIVE OFF
OK LIVE OFF
```

Samostatné `LIVE ON` vo firmvéri 4.2.1 zostáva kompatibilné a používa
33 340/100 µs; aktuálna aplikácia vždy posiela konkrétny kamerový profil.

## Aktualizácia

Aktualizujte aplikáciu aj Pico. Zastavte výrobu a zatvorte aplikáciu
aj sériové terminály. Z koreňa projektu:

```bash
bash docker/update-pico.sh --check
bash docker/update-pico.sh
```

Pri viacerých Pico pridajte `--port /dev/ttyACM0`. Aktualizátor používa
`firmware/pico/main.py` (4.2.1-pio-pair), zálohuje pôvodný obsah a zachováva
konfiguráciu. Verzionovaná kópia je `firmware/pico/main_v4.2.1.py`;
kontrolné súčty sú v `SHA256SUMS`. Rovnakú alebo novšiu verziu neprepisuje.

## Overenie

V RUN a Golden Wizard pri každom rozlíšení overte spustenie a zastavenie
Live, zachovanie TRIGGER režimu a čistý obraz po PRIME. Overte aj trvalé
svetlo, zatvorenie wizardu vrátane Escape a odchod z RUN. Po LIVE OFF overte
bežný produkčný pár bez prime/preview snímky vo výsledku.

### Overenie na zariadení 3. 11. 2026

Verzia 4.2.1 bola nahratá a overená na Pico s pripojenou CU55. V skutočných
oknách RUN a Golden Wizard prešli všetky štyri rozlíšenia pri expozícii
1 000 µs vrátane prepínania svetla, LIVE OFF, následného produkčného/Golden
snímania a ukončenia wizardu. Z 2 823 prijatých Live snímok bolo do náhľadu
publikovaných 2 783; 40 boli zámerne vynechané PRIME/prechodové snímky.
Sekvencie zostali súvislé, USB diagnostika bez chyby. Riadková analýza
publikovaných snímok a vizuálna kontrola prvého/posledného svetlého záberu
v každej kombinácii UI/rozlíšenia nezistili vodorovný pás. Ide o krátke
funkčné overenie, nie dlhodobý test Live pri každej expozícii.
