# Oprava USB prenosu CU55 na Jetson Orin Nano Super

Stav: **experimentálne, dlhodobé overenie nie je dokončené**. Inštalátor bez
`--experimental` odmietne nasadenie. Úspešné `verify` potvrdzuje nasadenie,
nie bezchybnú prevádzku kamery. Pri overovaní po reštarte nastal aj timeout
prvej MASTER snímky pri príprave aplikačného testu; príčina zostáva otvorená.

Podporovaný je zatiaľ jediný presne overený profil: ARM64 Orin Nano,
L4T 36.4.7, jadro 5.15.148-tegra, pôvodný NVIDIA Image, UVC a konfigurácia
zodpovedajúce kontrolným súčtom v `tools/cu55_usb_fix_support/recipe.json`.
Rovnaký model Jetsonu môže mať odlišný JetPack/BSP. Pri nezhode sa postup
zastaví; túto kontrolu neobchádzajte kopírovaním modulov z inej dosky.

Úpravy hostiteľského systému:

- Oficiálna oprava xHCI zachováva `endpoint->hcpriv` pri zmene USB rozhrania.
  Bez nej môže reset sekvencie endpointu skončiť bez vykonania a ďalší prenos
  zlyhať s USB Transaction Error / EPROTO.
  [Upstream commit](https://github.com/torvalds/linux/commit/25e531b422dc2ac90cdae3b6e74b5cdeb081440d).
- UVC používa 64 prijímacích požiadaviek a rezervuje ich pamäť pred COMMIT,
  aby sa príjem spustil ihneď po COMMIT. Zvýšená rezerva pomáha preklenúť
  oneskorenie obsluhy USB na hostiteľovi. Zmena sa týka aj ostatných UVC kamier.

Firmvér kamery ani Pica sa nemení. Inštalátor nesníma, neposiela impulzy,
neukladá obrázky, nenačítava modul do bežiaceho jadra a nereštartuje zariadenie.

## Použitie z koreňa stiahnutého repozitára

Najprv ukončite aplikáciu a ostatné procesy používajúce kameru.
Zostavenie spustite ako bežný používateľ priamo na Jetson hostiteľovi.
Potrebujete aspoň 12 GiB voľného miesta mimo aplikačného priečinka,
prístup na GitLab a k balíkovým repozitárom Ubuntu/NVIDIA.
Príkaz `build` automaticky skontroluje a cez `apt-get` doinštaluje chýbajúce
balíky: `build-essential gcc-11 git bc flex bison libssl-dev libelf-dev
gcc-11-plugin-dev kmod patch initramfs-tools openssl ca-certificates`.
Pri tom môže `sudo` požiadať o heslo. Samotné zostavenie pokračuje pod bežným
používateľom; celý príkaz `build` nespúšťajte cez `sudo`. Už nainštalované
balíky preskočí. Pri chybe sťahovania alebo inštalácie sa zostavenie nezačne;
po vyriešení príčiny môžete zopakovať rovnaký príkaz. Kontrola `doctor` systém
nemení. Zostavenie trvá desiatky minút.

```bash
python3 tools/cu55_usb_fix.py doctor
mkdir -p "$HOME/cu55-builds"
python3 tools/cu55_usb_fix.py build --output "$HOME/cu55-builds/r36-fix-1"
sudo python3 tools/cu55_usb_fix.py install --artifacts "$HOME/cu55-builds/r36-fix-1" --experimental
```

Použite nový výstupný priečinok pre každý pokus. Zostavenie overí presný
oficiálny commit, vstupné aj výstupné súbory oboch patchov, funkčnú konfiguráciu
jadra a všetkých 18 031 exportovaných symbolov/CRC. Manifest obsahuje kontrolné
súčty výsledných lokálnych binárnych súborov. Používajte iba vlastný dôveryhodný
výstup zostavenia; manifest nie je podpis distribučného balíka.

Inštalácia vytvorí `/boot/Image.hdf-cu55-usb-fix`, modul v
`/lib/modules/5.15.148-tegra/updates/hdf-cu55/`, konfiguračný súbor
`/etc/modprobe.d/zz-hdf-cu55-usb-fix.conf` a obnovovací záznam
`/var/lib/hdf-vision/cu55-usb-fix/state.json`. Zachová aktuálne parametre UVC.
Samostatná položka `HDF_CU55_USB_FIX` preberá FDT, initrd, APPEND aj overlays
z aktuálnej predvolenej položky. Pôvodný Image, modul aj boot položky zostanú.
Ak initrd obsahuje vlastnú kópiu UVC, postup sa zastaví: tento inštalátor
vendor initrd neprestavuje. Zabezpečený boot so zapnutým Secure Boot nepodporuje.

Až keď je to vhodné, vykonajte reštart a kontrolu:

```bash
sudo reboot
# Po opätovnom prihlásení z koreňa repozitára:
sudo python3 tools/cu55_usb_fix.py verify
```

`verify` kontroluje nový boot, označenie jadra, vybratý modul, jeho načítanú
verziu a kontrolné súčty nasadených súborov. Nasleduje samostatný funkčný test
kamery vo všetkých používaných rozlíšeniach. Do jeho vyhodnotenia nepovažujte
nasadenie za potvrdenú opravu všetkých výpadkov.

## Návrat

Ukončite kamerové aplikácie. Použite tú istú verziu inštalátora:

```bash
sudo python3 tools/cu55_usb_fix.py rollback
sudo reboot
```

Rollback najprv overí všetky vlastnené súbory a obnoví pôvodnú konfiguráciu
bootovania. Pri ručne zmenenej konfigurácii alebo súbore nič naslepo neprepíše.
Pri zlyhaní bootovania vyberte pôvodnú položku v boot menu. Obnovovací záznam
zostáva na disku aj po zlyhaní inštalácie; čiastočnú inštaláciu vie `rollback`
upratať. Po úspešnom návrate záznam archivujte mimo `/var/lib/hdf-vision/cu55-usb-fix`
pred prípadnou novou inštaláciou. Bežiace jadro a modul sa zmenia až reštartom.
