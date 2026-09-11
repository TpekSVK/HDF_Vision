# Finálny checkpoint – Pico PIO 4.0, 2026-09-11

Používateľ povolil pokračovať po meraní; implementácia a autorizované overenie sú dokončené.
Vetva: codex/cu55-pio-trigger-v4. Zmeny zatiaľ bez commit/push/merge.

Výsledky: 560/560 PNG (28 profilov ×20), všetky SHA256 overené, 12 MASTER/TRIGGER
cyklov, 20 externých požiadaviek, reconnect a obnova streamu. Finálny FW aj služby
prešli dodatočným kratším hardvérovým testom. Finálna regresná sada: 173 passed.

Záverečný report: docs/PIO_VALIDATION_20260911_SK.md.
Implementačný kontrakt: docs/PIO_APP_INTEGRATION_SK.md.
Historický report: docs/CU55_PIO_LAB_REPORT_SK.md (jeho všeobecný reopen bol opravený).

Kľúčové pravidlá: 1080p zachováva MASTER-origin stream; ostatné rozlíšenia reopen
v TRIGGER po konfigurácii. Nový/neznámy stream sa pripraví cez MASTER +20 rámcov.
Settling môže zahodiť 0,2; produkcia vyžaduje dve nadväzujúce snímky. Žiadny Jetson GPIO.

Nový FW: firmware/pico/main_v4.0.py a identický main.py, SHA256SUMS vedľa nich.
Na Pico beží finálny FW4.0 iba v RAM. Flash ostala pôvodná (záloha laboratória overená
loaderom). Po reštarte Pico sa spustí pôvodný flash main.py; pre trvalé nasadenie treba
nahrať nový súbor. Žiadny SAVE v tejto validačnej fáze.

Aktuálny hardvér: kamera MASTER, stream zatvorený; Pico SESSION IDLE, svetlo vypnuté,
žiadne aktívne testy. Pôvodné uložené Pico profily sa pri poslednom zavedení FW načítali
z flash; finálny test použil MAP IN1 V1 iba v RAM (zodpovedá pôvodnému mapovaniu).

Dôkazy: /home/hdf-jetson1/cu55_trigger_lab/implementation_20260911/
app_matrix_01/status.json + PNG + run.log; lifecycle.json; final_lifecycle.json;
matrix01_source a final_source; finálne testovacie a hardvérové logy.

Trvalé nahratie FW ani publikovanie na GitHub nebolo súčasťou vykonaných zmien.

## Golden Wizard UI – doplnenie 2026-09-11

Dokončené aj formuláre Pridať/Upraviť pohľad, ktoré pri predchádzajúcom odovzdaní
stále obsahovali staré texty a vstupy. GoldenWizard odovzdáva aktuálny MASTER/TRIGGER
režim a aktuálnu expozíciu do ViewConfigDialog.

- Expozícia snímača sa vyberá iba z 0,5 / 1 / 2 / 5 / 10 / 15 / 16 ms, v oboch režimoch.
  Do receptu sa ukladajú mikrosekundy. Staré hodnoty mimo ponuky sa nezaokrúhľujú;
  pred uložením formulára musí používateľ vybrať jednu z ponúknutých hodnôt.
- Rozlíšenia majú aktuálne označenie rozmerov, fps a Y8 vrátane 640×480@112.
  Odstránené neaktuálne varovania o nefunkčnom 1080p a obmedzení 2592 iba na setup.
- V TRIGGER je Y12 nedostupné a uloženie nepodporovaného profilu zablokované.
  Aj zdedené nastavenie sa pri ukladaní overí voči aktuálnemu profilu kamery.
- Starý vstup nazvaný trigger expozícia, ktorý v skutočnosti menil trigger_gap_ms,
  je odstránený. Existujúca hodnota trigger_gap_ms zostáva zachovaná pre kompatibilitu;
  nové PIO časovanie ju nepoužíva. V TRIGGER sú V1/V2 skryté a text vysvetľuje
  automatické riadenie impulzov a blesku. MASTER si zachováva výber V1/V2.

Validácia: 30 testov formulára/ukladania receptov prešlo, samostatná existujúca PIO
regresná sada 173 testov prešla. Spoločný zber oboch sád koliduje so starými testovacími
náhradami app.utils.imaging (chýbajúci TimeBlockResult); sady boli preto spustené
v oddelených procesoch Docker, nie prezentované ako jeden spoločný úspešný beh.
Logy: /tmp/hdf_view_pio_tests.log a /tmp/hdf_view_pio_core_regression.log.
Rozloženie dialógu bolo vizuálne skontrolované pomocou offscreen Qt snímky.
Táto UI fáza nespúšťala hardvérové testy ani nemenila firmware na Pico.
Zmeny zostávajú lokálne, bez commitu/pushu.
