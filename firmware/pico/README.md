# Raspberry Pi Pico – HDF_Vision 4.3.1

Nový firmvér je **main_v4.3.1.py**, identifikácia `pico_hdf_controller 4.3.1-dual-channel`.
`main.py` je jeho identická nasadzovacia kópia. Na Pico s MicroPython pre RP2040
alebo RP2350/Pico2 sa kopíruje ako `main.py`. Predchádzajúce kópie vrátane `main_v4.2.1.py` zostávajú zachované.
Historické výsledky overenia sú v externom archíve uvedenom v hlavnom README projektu.

Na Jetson hoste možno starší HDF firmvér aktualizovať príkazom
`bash docker/update-pico.sh` po zatvorení aplikácie a zastavení výroby.
[Návod na aktualizáciu a zálohy](../../docs/PICO_UPDATE_SK.md).

## Zapojenie

- GP16: TRIG kamery, aktívny HIGH, cez existujúci prevodník úrovní.
- GP17: svetlo, aktívne HIGH.
- GP18: TRIG kamery 2, GP19: svetlo kamery 2, aktívne HIGH.
- GP1–GP8: IN1–IN8, active LOW s pull-up.
- Jediný prijímač USB serial v aplikácii je PicoService. Súbežný terminál port nesmie otvoriť.

## Dva kamerové kanály

Nová schopnosť `DUAL_CHANNEL_V1 HOST_INPUT_V1` dopĺňa existujúce `PIO_PAIR_V1 SESSION_V1 LIVE_PIO_V1`.
Príkaz `CH 1 STATUS` smeruje na GP16/17, `CH 2 STATUS` na GP18/19. Všetky odpovede
a udalosti sú potom označené rovnakým prefixom, napr. `CH 2 CAPTURE IN3` alebo
`CH 1 OK PIO 123`. Hostiteľ používa jedno exkluzívne USB spojenie, ale samostatné
odpovede, rezervácie, LIVE, svetlo, SESSION, profily V1/V2 a mapovanie vstupov pre každý kanál.

Fyzický vstup v kanálovom protokole odošle `REQUEST INx` **v MASTER aj TRIGGER**.
Aplikácia najprv vyberie pohľad a pripraví jeho profil. V MASTER potom pošle
`CH n TRIGGER INx`/`FIRE V1`, pri udalosti CAPTURE rezervuje čerstvú snímku a pri
ukončení svetla uzavrie rezerváciu. V TRIGGER rezervuje PRIME/CAPTURE a pošle
`CH n PIO FIRE ...`. Tým sa nespustí osvetlenie proti starému kamerovému profilu.
Vstup bez pripravenej SESSION nesníma. Spoločný fyzický vstup možno namapovať na oba kanály.

PIO používa dva nezávislé state machines. Počas čakania na dokončenie jedného
kanála môže druhý prijať príkaz a snímať. MASTER okná sa tiež obsluhujú bez
blokujúceho čakania na celé trvanie blesku. Každé svetlo a LIVE majú vlastný stav;
IDLE jedného kanála nesmie vypnúť druhý. Pri ukončení programu sa vypnú všetky štyri GPIO.

SAVE ukladá konfiguráciu verzie 2 s oddelenými sekciami `channels.1` a `channels.2`.
Starý plochý konfiguračný súbor sa načíta ako nastavenia kanála 1; druhý má predvolené hodnoty.
Načítanie samo nič neprepisuje. Pre návrat na 4.2.1 obnovte aj pôvodnú konfiguráciu zo zálohy,
ak už bola uložená nová dvojkanálová konfigurácia.

Neoznačené príkazy zostávajú kompatibilné s kamerou 1. Ich MASTER fyzické vstupy
zachovávajú pôvodné správanie popísané nižšie. Pri kanálovom klientovi platí vyššie
uvedená hostiteľom riadená požiadavka v oboch režimoch.

## Riadenie režimov

Po štarte je `SESSION IDLE`. Fyzické vstupy vtedy nesmú spustiť snímanie.
Aplikácia najprv pripraví kameru a potom potvrdí `SESSION MASTER` alebo `SESSION TRIGGER`.
Režim SESSION sa neukladá do konfigurácie. Prepnutie vypne manuálne svetlo.

V MASTER zostáva časovanie DELAY/PULSE/CAPTURE v milisekundách. Pico zapne svetlo,
odošle `CAPTURE INx` alebo `CAPTURE V1/V2` a po zhasnutí potvrdí `OK FIRED ...`.
Aplikácia rezervuje čerstvý frame pri udalosti CAPTURE.

V TRIGGER fyzický vstup odošle iba `REQUEST INx`. Aplikácia vyberie pohľad,
pripraví kameru, rezervuje prijímač dvojice a až potom odošle `PIO FIRE`.
Samotný vstup negeneruje impulzy ani svetlo. Takto sa rámce nestratia pred rezerváciou.
Od v4.1 je manuálne svetlo ON dostupné v MASTER aj TRIGGER, iba mimo aktívneho cyklu.
Prijatý FIRE/TRIGGER vstup alebo PIO FIRE automaticky zruší manuálne zapnutie.
Svetlo potom riadi snímací cyklus a po skončení zostáva vypnuté; manuálny režim
sa neobnoví ani po chybe PIO. LIGHT STATUS aj STATUS vracajú MANUAL_LIGHT OFF.
V IDLE sa LIGHT ON odmietne. Odmietnutý príkaz snímania (nesprávny režim,
parametre alebo obsadený radič) manuálne svetlo nemení.

## Protokol PIO_PAIR_V1 / SESSION_V1

`STATUS` obsahuje `CAPABILITIES PIO_PAIR_V1 SESSION_V1` a `SESSION_MODE ...` a končí `END`.

```
SESSION IDLE
SESSION MASTER
SESSION TRIGGER
PIO FIRE <id> <count> <period_us> <pulse_us> <pre_us> <light_duration_us> <enable> <tail_us>
```

SESSION odpovedá `OK SESSION <mode>`. PIO vracia `PIO RESULT <JSON>` a `OK PIO <id>`.
Aplikácia musí overiť ID, počet impulzov, konečné ACK aj dve skutočné snímky.
ACK samo nedokazuje príjem snímok ani elektricky zmerané hrany.

PIO časy sú v **mikrosekundách**. Period je vzdialenosť nábežných hrán, nie medzera LOW.
Kladné pre znamená svetlo pred PRIME, záporné svetlo po PRIME. Jeden hardvérový
PIO program pri 1 MHz riadi oba výstupy. Produkcia používa 2 impulzy po 100 µs,
prvá snímka sa zahodí, druhá sa spracuje. Samostatný arming používa count=1.
PIO FIRE je povolené v IDLE (príprava) a TRIGGER, odmietnuté v MASTER.

## Konfigurácia a kompatibilita

`hdf_pico_config.json`, `SET`, `MAP`, `SAVE`, `INPUTS`, `FIRE`, `TRIGGER INx`
zostávajú podporované. Mapovanie vstupov na V1/V2 vyberá MASTER timing profil;
mapovanie na Recipe View riadi aplikácia. Nastavenia sú v RAM až do `SAVE`.
Staré polia MODE/TRIG/GAP/COUNT sa načítajú, ale v4.0 ich nepoužíva na produkčné
TRIGGER impulzy. O režime rozhoduje SESSION a o presnom časovaní nový PIO príkaz.
Starší klient bez SESSION po štarte v4.0 nespustí snímanie. Nová aplikácia podporuje
starý firmvér v MASTER, pre TRIGGER vyžaduje v4.0.

Pred trvalým nasadením zálohovať pôvodný `main.py` aj konfiguráciu. Nahratie do RAM
pri vývoji nie je trvalé nasadenie; po reštarte sa spustí verzia uložená na flash.

## Trigger Live 4.2.1

`LIVE ON <period_us> <pulse_us>` spúšťa hardvérové PIO na GP16; GP17
(svetlo) zostáva nezmenené. Aplikácia posiela periódu kamerového profilu,
zahadzuje prvú PRIME snímku a po LIVE OFF vyčká na dobehnutie náhľadu.
STATUS obsahuje `LIVE_PIO_V1`. Podrobnosti: [Trigger Live](../../docs/TRIGGER_LIVE_SK.md).
