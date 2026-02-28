sudo cp docker/99-hdf-uvc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

## Externý trigger

- Pin označený ako `Trigger (vstup)` v GPIO Wizard musí byť v režime vstupu s úrovňami 3.3 V.
- Test sa spustí na nábežnej hrane (prechod LOW → HIGH). Zariadenie musí vytvoriť krátky pulz alebo flanžu na túto hranu.
- Ak zariadenie používa opačnú polaritu (HIGH → LOW), je potrebné ho prispôsobiť alebo doplniť invertor – aplikácia reaguje len na nábežnú hranu.
- V konfigurácii receptu nastavte pre daný pohľad `Trigger Mode` na `External Trigger`.

## HID access (See3CAM_CU55M)

Pre reálne prepínanie Trigger/Master a Flash módov sa používa HID kanál (`/dev/hidrawX`).

Príklad spustenia kontajnera s HID zariadením:

```bash
docker run --rm -it \
  --device=/dev/video0 \
  --device=/dev/hidraw0 \
  --device=/dev/bus/usb \
  hdf_vision:dev
```

Poznámky:
- Ak v kontajneri vidíte `permission denied` na `/dev/hidrawX`, skontrolujte práva zariadenia na hoste.
- Produkčne odporúčame vyriešiť prístup cez udev pravidlo (udev group/mode pre hidraw).
- `docker/run.sh` sa pokúsi automaticky preposlať všetky `/dev/hidraw*` zariadenia.
- Ak sa index `hidraw` mení medzi rebootmi, nastavte explicitne `HDF_HIDRAW=/dev/hidraw2` (host env sa prenesie do kontajnera cez `docker/run.sh`).

