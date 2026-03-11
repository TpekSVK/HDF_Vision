sudo cp docker/99-hdf-uvc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

## Externý trigger

- Pin označený ako `Trigger (vstup)` v GPIO Wizard musí byť v režime vstupu s úrovňami 3.3 V.
- Test sa spustí na nábežnej hrane (prechod LOW → HIGH). Zariadenie musí vytvoriť krátky pulz alebo flanžu na túto hranu.
- Ak zariadenie používa opačnú polaritu (HIGH → LOW), je potrebné ho prispôsobiť alebo doplniť invertor – aplikácia reaguje len na nábežnú hranu.
- V konfigurácii receptu nastavte pre daný pohľad `Trigger Mode` na `External Trigger`.

