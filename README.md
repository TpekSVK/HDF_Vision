sudo cp docker/99-hdf-uvc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

## Externý trigger

- Pin označený ako `Trigger (vstup)` v GPIO Wizard musí byť v režime vstupu s úrovňami 3.3 V.
- Test sa spustí na nábežnej hrane (prechod LOW → HIGH). Zariadenie musí vytvoriť krátky pulz alebo flanžu na túto hranu.
- Ak zariadenie používa opačnú polaritu (HIGH → LOW), je potrebné ho prispôsobiť alebo doplniť invertor – aplikácia reaguje len na nábežnú hranu.
- V konfigurácii receptu nastavte pre daný pohľad `Trigger Mode` na `External Trigger`.


## CU55 Trigger Mode Architecture

Aplikácia používa pre See3CAM_CU55M **session-based trigger architektúru**:

- Trigger režim je **session-level** stav.
- Kamera sa prepne do **TRIGGER mode** iba pri vstupe do trigger session a ostáva v ňom počas celej session.
- Prepínanie **MASTER ↔ TRIGGER** medzi jednotlivými triggrami je zakázané, pretože v praxi spôsobovalo nestabilitu.
- Trigger pipeline sa počas session drží otvorená a neotvára sa/nezatvára pri každom trigri.
- Pre CU55 sa používa **HW GPIO trigger** (nie software trigger cez HID).

### Recovery pri timeoutoch triggeru

Ak production trigger neprinesie frame, aplikácia spúšťa recovery bez prepínania stream mode:

1. **Soft recovery pulzy** (až 3 pokusy)
2. **Re-prime session** (vyčistenie queue + 2x prime + production trigger)
3. **Pipeline reopen** (NULL → reopen → PLAYING → settle → 2x prime → production trigger)
4. **Fail** (zalogovanie chyby)

Táto architektúra reflektuje reálne správanie CU55: občas po trigri sample nepríde, ale kamera sa vie synchronizovať po ďalších pulzoch bez potreby MASTER/TRIGGER togglingu.
