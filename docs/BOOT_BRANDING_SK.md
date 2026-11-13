# Logo HDF Vision pri štarte Jetsonu

## Stav na tomto Jetsone, 13. 11. 2026

Zariadenie: Orin Nano Super, Jetson Linux a UEFI 36.4.7, systém na NVMe.
Nasadená je **Linux časť** vzhľadu štartu:

- Téma Plymouth `hdf-vision` používa existujúce logo a pozadie `#14181d`.
- Samostatná predvolená položka `HDF_VISION_BRANDED` preberá presne jadro,
  initrd, DTB aj GPIO overlay z pôvodnej predvolenej položky `HDF_CU55_HCPRIV`.
  Mení iba výpisy na obrazovke: tichý režim, splash a skrytý textový kurzor.
  Sériová konzola zostáva dostupná.
- Pôvodné boot položky aj `/boot/initrd` zostávajú zachované. Vlastný initrd
  NVIDIA sa neprestavuje; téma sa spúšťa cez už prítomný Plymouth v hlavnom systéme.
- Používateľské pozadie GNOME zobrazuje rovnaký motív pri prechode do aplikácie.
- Launcher nastaví plnú geometriu monitora ešte pred prvým zobrazením okna;
  žiadosť o fullscreen teda nie je závislá iba od už bežiaceho správcu okien.

**UEFI sa neaktualizovalo. Najskoršie NVIDIA logo zostáva.** Úplný priebeh
štartu a čas aktivácie Plymouth treba potvrdiť na pripojenom monitore po
reštarte. Táto fáza nezaručuje súvislý obraz od okamihu zapnutia napájania.
Pri inicializácii grafického ovládača môže byť obraz krátko čierny.

Pri kontrole doska hlásila oba A/B sloty v stave `normal`, aktívny slot B,
verziu 36.4.7 a minimálnu prijateľnú verziu 36.4.7. Verejný manifest NVIDIA
v čase prípravy neposkytoval kombináciu/tag r36.4.7; starší balík ani prepisovanie
čísel verzie na obídenie kontroly sa nepoužili. Úplná zmena UEFI loga vyžaduje
samostatne overený kompatibilný firmvér a plán obnovy cez recovery.

## Inštalácia Linux časti

Použiť na konkrétnom Jetsone s funkčným grafickým prostredím a existujúcim
Plymouth (`script.so`). Nie je to inštalácia UEFI firmvéru.

Z koreňa repozitára, ako bežný používateľ:

```bash
python3 tools/setup-boot-branding.py stage --output /tmp/hdf-linux-cover
```

Príkaz vyžaduje nový výstupný priečinok. Pred inštaláciou možno porovnať
vytvorený `extlinux.conf` s `/boot/extlinux/extlinux.conf`. Balík je viazaný na
aktuálnu boot konfiguráciu a verziu systému/UEFI; pri ich zmene sa inštalácia
zastaví. Existujúcu zálohu ani cieľovú tému neprepisuje.

```bash
sudo python3 tools/setup-boot-branding.py install --staged /tmp/hdf-linux-cover
python3 tools/setup-launcher.py --background hdf
```

Pri prvom použití aplikácie na inom zariadení treba samostatne nastaviť aj
jej automatické spúšťanie podľa [setup_launcher_SK.md](setup_launcher_SK.md).
Žiadny z týchto príkazov automaticky nereštartuje zariadenie.

Pred reštartom zatvorte aplikáciu a rozpracované úlohy. Po reštarte overte
výpis `cat /proc/cmdline`, tému a zachovanie kamier v RUN; ideálne natočte štart.
Skontrolujte aj chyby pomocou `journalctl -b -u plymouth-start.service`.

## Návrat

Systémová záloha je v `/var/lib/hdf-vision/boot-branding/`.

```bash
sudo python3 tools/setup-boot-branding.py restore
python3 tools/setup-launcher.py --background restore
```

Obnova kontroluje neskoršie ručné zmeny súborov a odmietne ich naslepo prepísať.
Zálohu po obnove archivuje. Zmena predvolenej boot položky sa prejaví pri
nasledujúcom reštarte. Ak by nová boot položka mala problém, pôvodná
`HDF_CU55_HCPRIV` je stále v boot menu a používa pôvodný príkazový riadok.
Aktualizáciu bootloadera ani zásah do QSPI tento inštalátor nevykonáva.

Záloha pôvodných používateľských nastavení pozadia je samostatne v
`~/.local/state/hdf-vision/startup-launchers/background.json`.

## Overenie pred reštartom

Prešlo 12 automatických testov vrátane zachovania pôvodných boot položiek,
USB jadra, initrd a overlayov; testy overujú aj odmietnutie zmeneného balíka,
obnovu pôvodnej konfigurácie a obnovu po čiastočne zlyhanej inštalácii.
Téma sa vykreslila v izolovanom Plymouth/X11 náhľade.
Launcher sa overil v Xvfb bez správcu okien s veľkosťou 1920 × 1080.
Tieto skúšky nenahrádzajú skúšku skutočného štartu na monitore.

## Podklady

- [NVIDIA: úprava UEFI loga](https://docs.nvidia.com/jetson/archives/r36.4/DeveloperGuide/SD/Bootloader/UEFI.html#customized-logo)
- [NVIDIA: kombinácie zdrojov UEFI](https://github.com/NVIDIA/edk2-edkrepo-manifest)
- [Ubuntu: Plymouth](https://manpages.ubuntu.com/manpages/jammy/man8/plymouth.8.html)

## Prvá skúška skutočného štartu

Po prvom reštarte sa potvrdilo tiché bootovanie s pôvodným USB jadrom,
ale logo počas Linux štartu ešte nebolo zobrazené dostatočne skoro.
Plymouth odštartoval v čase 7,73 s od štartu jadra, kým `nvidia_drm`
sa inicializoval až v čase 16,33 s pri spúšťaní X servera. Úvodné okno
aplikácie sa vo videu objavilo až približne na 36. sekunde.
Tieto časy videa sa nepočítajú od rovnakého okamihu ako časy jadra.

Pre ďalšiu skúšku sa doplnilo skoré načítanie už používaného modulu:

```bash
sudo python3 tools/setup-boot-branding.py early-display
```

Príkaz vyžaduje existujúcu inštaláciu vyššie. Vytvorí iba
`/etc/modules-load.d/hdf-boot-display.conf` a
`/etc/systemd/system/plymouth-start.service.d/hdf-display.conf`.
Plymouth počká na službu načítania modulov; modul sa teda nebude prvýkrát
načítavať až pri spustení X servera. Žiadny modul ani jeho parametre
sa nenahrádzajú. Pre túto skúšku sa zapisuje diagnostický log
`/var/log/hdf-boot-plymouth.log`. Načasovanie nového postupu čaká na overenie
ďalším reštartom. `restore` odstráni aj tieto dva vlastnené konfiguračné súbory.

Launcher zároveň pred importom GTK vyfarbí koreňové X okno farbou aplikácie
pomocou dostupného `xsetroot`, aby sa pri prechode do grafickej relácie
nepresvital starý framebuffer s NVIDIA obrázkom. Ak nástroj nie je dostupný,
launcher pokračuje cez GTK bez prerušenia spúšťania aplikácie.
