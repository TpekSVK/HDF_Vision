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

Druhá skúška pridala skoré načítanie grafiky. Ukázala konflikt pri prevzatí
riadenia displeja: GDM/X server hlásil `Failed to acquire modesetting permission`
a grafické prihlásenie sa opakovalo. Skoré načítanie aj diagnostický režim sa
odstránili, ovládač sa uvoľnil a GDM sa obnovil bez ďalšieho reštartu Jetsonu.
Aplikácia sa potom úspešne pripravila do RUN. Pôvodný príkaz `early-display`
je v inštalátore vypnutý a odmietne meniť systém.

## Experimentálne odovzdanie displeja

Krátka samostatná hardvérová skúška bez reštartu overila túto sekvenciu:
Plymouth zobrazil naše logo (potvrdené používateľom), ukončil sa cez
`quit --retain-splash`, modul `nvidia_drm` sa bez násilia odobral a následne
sa spustilo grafické prihlásenie aj aplikácia. Celý štart od zapnutia ešte
čaká na overenie. Dĺžka viditeľnosti loga nebola používateľom meraná.

Na tomto testovacom Jetsone je pre ďalšiu skúšku pripravená nová voľba:

```bash
sudo python3 tools/setup-boot-branding.py display-handoff --experimental
```

Použiť iba na overovanej zostave, po inštalácii základnej Linux témy a so
zálohou. Vytvorí štyri vlastnené súbory:

- `/etc/modules-load.d/hdf-boot-display.conf`
- `/etc/systemd/system/plymouth-start.service.d/hdf-display.conf`
- `/etc/systemd/system/gdm.service.d/zz-hdf-display-handoff.conf`
- `/usr/local/libexec/hdf-vision-display-handoff`

Plymouth počká na načítanie existujúceho grafického modulu. Pred spustením
GDM/X sa cez jeho `ExecStartPre` ukončí Plymouth a uvoľní `nvidia_drm`.
X server potom načíta ovládač svojou bežnou cestou. Pomocník používa časové
limity, modul neodoberá násilím a nemení jeho binárny súbor ani parametre.
Samostatné skoré načítanie bez tohto odovzdania sa nepoužíva.
`restore` odstráni aj všetky tieto vlastnené súbory; po reštarte sa obnoví
pôvodné poradie načítania. UEFI, jadro, initrd aj GPIO overlay sa nemenia.

Launcher pred importom GTK vyfarbí koreňové X okno farbou aplikácie
pomocou dostupného `xsetroot`, aby sa pri prechode do grafickej relácie
nepresvital starý framebuffer s NVIDIA obrázkom. Ak nástroj nie je dostupný,
launcher pokračuje cez GTK bez prerušenia spúšťania aplikácie.

## Tretia skúška a doplnené čakanie

Pri ďalšom celom štarte bolo logo viditeľné približne na 22. sekunde videa,
po 26. sekunde zostal obraz čierny. Boot log zachytil konkrétny dôvod:
`modprobe: FATAL: Module nvidia_drm is in use.` Pôvodný pomocník po
neúspešnom uvoľnení pokračoval do GDM. Konfigurácia sa odstránila a grafika
aj RUN sa obnovili bez reštartu systému.

Doplnená verzia pred spustením GDM:

1. Požiada Plymouth o ukončenie.
2. Ukončí zostávajúce procesy iba jeho systemd služby.
3. S obmedzeným počtom pokusov počká na uvoľnenie modulu. Modul nikdy
   neodoberá násilím.
4. Vopred naplánuje jednorazovú kontrolu grafického štartu.

Kontrola sa spúšťa približne 25 sekúnd po predštartovom pomocníkovi.
Rozlišuje stabilný X server od opakovane padajúcich procesov podľa PID.
Ak nenájde stabilný X server, overí vlastníctvo a kontrolné súčty svojich
konfiguračných súborov, odstráni iba experimentálnu grafickú konfiguráciu,
uvoľní modul a obnoví GDM bežnou cestou. Jadrom, initrd, UEFI ani základnou
Linux témou nemanipuluje. Neskoršie ručné zmeny neprepisuje.

Kontrolný pomocník je vlastnený súbor
`/usr/local/libexec/hdf-vision-display-guard`; aj ten odstráni `restore`.
Výsledok je v `/var/lib/hdf-vision/boot-branding/display-guard-result.json`.
Kontrola potvrdzuje stabilitu X servera, nie pripravenosť kamier alebo
správnosť výsledku receptu. Je to ochrana tejto experimentálnej skúšky,
nie záruka obnovy pri ľubovoľnej hardvérovej alebo systémovej chybe.

Prešlo 23 testov. Upravená sekvencia sa následne overila priamo cez
nainštalovaný `ExecStartPre` GDM: odovzdanie prešlo, aplikácia sa spustila
a jednorazová kontrola potvrdila stabilný X server. Celý nový štart od
zapnutia tým ešte nebol overený.

## Overený celý štart a medzera pred aplikáciou

Záznam `VID_20261113_165817.mp4` už prešiel až do RUN. Log potvrdil
uvoľnenie displeja pred GDM, stabilný X server a úspešnú prípravu oboch
kamier po automatickom opakovaní prvého pokusu. Automatický návrat sa
nepoužil. UEFI sa nemenilo. V tomto zázname sa však stále objavuje medzera
približne medzi 26. a 36. sekundou videa: po Linux logu a pred aplikáciou.

Pre ďalšiu skúšku je doplnený voliteľný Xsession krok:

```bash
sudo python3 tools/setup-boot-branding.py x-background
```

Vytvorí iba `/usr/share/backgrounds/hdf-vision.png` a
`/etc/X11/Xsession.d/00hdf-vision-background`. Hneď na začiatku X relácie
vykreslí logo pomocou už dostupného `xsetbg`, ešte pred GNOME. Ovládač,
UEFI ani initrd nemení. Oba súbory sú zahrnuté v obnove a v automatickom
návrate tejto experimentálnej zostavy.

Skript je načítavaný existujúcim Xsession, preto nikdy nepoužíva `exit`.
Chyba vykreslenia ani nedostupný nástroj nesmú ukončiť prihlásenie; jednotlivé
pokusy majú časový limit. Úspešné aj úmyselne zlyhané vykreslenie sa overili
v bežiacej relácii bez prerušenia aplikácie. Skutočné skrátenie čiernej
medzery čaká na ďalší záznam štartu. Krátke zhasnutie pri opätovnej
inicializácii grafického výstupu môže zostať.
