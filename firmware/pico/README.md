# Raspberry Pi Pico – HDF_Vision 4.0

Nový firmvér je **main_v4.0.py**, identifikácia `pico_hdf_controller 4.0-pio-pair`.
`main.py` je jeho identická nasadzovacia kópia. Na Pico s MicroPython pre RP2040
sa kopíruje ako `main.py`. Verzia `main_v3.3.py` zostáva ako predchádzajúca verzia.
Stav overenia a nasadenia uvádza `docs/PIO_IMPLEMENTATION_CHECKPOINT.md` v aplikácii.

## Zapojenie

- GP16: TRIG kamery, aktívny HIGH, cez existujúci prevodník úrovní.
- GP17: svetlo, aktívne HIGH.
- GP1–GP8: IN1–IN8, active LOW s pull-up.
- Jediný prijímač USB serial v aplikácii je PicoService. Súbežný terminál port nesmie otvoriť.

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
Manuálne svetlo ON je dostupné iba v MASTER; v TRIGGER ho vlastní PIO sekvencia.

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
