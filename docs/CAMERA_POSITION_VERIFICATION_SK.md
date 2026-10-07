# TROUBLESHOOTING – Overenie polohy kamery

## Implementácia a súbory

Nové súbory:

- `app/services/camera_position.py`: read-only referencia, DTO, tolerancie, diagnostický locator, fyzické pokyny a spoločný capture.
- `app/ui/troubleshooting_page.py`: rozšíriteľná stránka s výberom diagnostického modulu a View, RAW obrázkami, stavom, ručným/automatickým capture a prepínaním obrazov.
- `tests/test_camera_position.py`: transformácie, tolerancie, pokyny, nemennosť receptu a capture/cleanup.
- `tests/test_troubleshooting_page.py`: UI, timery, súbežnosť, viewport, navigácia a oddelenie od RUN.
- `docs/CAMERA_POSITION_VERIFICATION_SK.md`: tento report a Jetson testovací postup.

Zmenené súbory:

- `app/ui/main_window.py`: navigácia TROUBLESHOOTING v pôvodnom hornom riadku, pause/resume a zdieľanie existujúceho pracovníka.
- `app/services/view_capture.py`: voliteľné `hardware_prepared` pre diagnostickú session; ostatné existujúce volania zachovávajú pôvodné správanie.
- `README.md`: odkaz na diagnostiku.

RUN, produkčný locator, Golden Wizard, recipe modely, Modbus, storage a retention algoritmy sa nemenia. Modul sa otvára pre konkrétnu kamerovú stanicu; ostatné stanice majú svoje vlastné pracovníky a môžu zostať v RUN.

## Referencia a súradnice

Diagnostika číta rovnaký `recipe.json` cez `load_recipe_config` ako `InspectionRuntime`, vyberie aktívny View a jeho `golden_path`. Nevolá `RecipeService.load`, save ani publish a nepoužíva aktuálny RUN/aligned preview ako referenciu.

Golden Wizard ukladá **nezarovnaný** golden po bezstratovom otočení `image_rotation` View. Nástroj toto uložené otočenie obráti pre RAW golden zobrazenie. Nový RAW current získa cez `ViewCapture.capture(image_rotation_override=0)`. Locator pracuje v existujúcich súradniciach receptu: iba otočenie View sa aplikuje na private current kópiu; žiadna locator kompenzácia sa pred meraním nepoužije. RAW obrázky zostávajú nezarovnané.

## Tolerancie a chýbajúce údaje

Používajú sa iba uložené hodnoty aktívneho `locator.template_match`:

| Os | Zdroj | Dostupnosť |
| --- | --- | --- |
| X | `tool.thresholds.values.max_shift_x` | Ak je hodnota uložená, konečná a nezáporná. |
| Y | `tool.thresholds.values.max_shift_y` | Rovnaké pravidlo. |
| R | `tool.params.values.reference_max_angle_deg` | Ak je uložená v režime `guided_edge`. |

`template_rotation.angle_range_deg` je **rozsah vyhľadávania**, nie explicitná rotačná akceptačná tolerancia. Nástroj ho nenahrádza toleranciou. `translation` rotáciu nemeria; nenahrádza ju nulou. Chýbajúce limity sú v UI označené `chýba` a príslušná os `NEVYHODNOTENÉ`. Ak niektorá známa os prekročí limit, zobrazí sa MIMO TOLERANCIE; ak všetky známe osi prejdú, ale niektorá os/limit chýba, zobrazí sa neúplné vyhodnotenie, nikdy zelené celkové POLOHA KAMERY OK. DTO používa `None`, takže chýbajúce údaje možno v budúcnosti doplniť bez náhradných defaultov.

Vyhodnotenie je inkluzívne: `abs(dx) <= X`, `abs(dy) <= Y`, `abs(theta) <= R`; celkové OK vyžaduje všetky tri platné výsledky. X/Y sú existujúce affine translácie okolo počiatku súradníc, presne ako v produkčnom locatore, nie nový posun meraný v strede obrázka. Pri kombinácii translácie a rotácie je vhodné najprv upraviť roll a znova odmerať posun.

## Algoritmus a fyzické pokyny

Používa sa existujúca čistá funkcia `run_locator_template_match` vrátane `match_shape` a režimov `translation`, `template_rotation`, `guided_edge`. Produkcia používa jej `T` ako **golden → current** a na kompenzáciu obrazu aplikuje `T⁻¹`.

Diagnostika rozšíri iba svoju search ROI na celý obraz. Zachová existujúcu template oblasť vrátane legacy `use_golden_crop`. Na runtime kópii odstráni X/Y odmietnutie nálezu podľa produkčných max_shift limitov, aby mohla zobraziť aj prekročenie limitu. Pri guided_edge rozšíri detekčný akceptačný uhol na podporovaných 90°, ale vyhodnotí ho voči pôvodnému uloženému limitu. Nič z toho nezapisuje do receptu. U template_rotation zachová pôvodný rozsah/rozlíšenie vyhľadávania; nález na jeho hranici nie je spoľahlivou diagnózou.

Spoľahlivosť používa existujúci `found`, `threshold_corr`, guided-edge coverage a existujúce quality warnings. Nepridáva nový confidence prah. Pri nenájdenej/nejednoznačnej referencii alebo rozdielnych rozmeroch obrázkov sa nezobrazia smerové pokyny. Confidence zostáva technická hodnota; nie je percento istoty.

Vrstva `camera_correction_from_alignment` explicitne oddeľuje matematiku od fyzického pokynu:

- Obrazové X je doprava, Y dole, kladné theta je CW.
- Pri pevnej forme sa obraz pohybuje **opačne** než fyzická kamera. Kamera posunutá doprava vytvorí záporné obrazové X, preto korekcia znie DOĽAVA. Kamera doľava → DOPRAVA; hore → DOLE; dole → HORE.
- Camera roll CW vytvorí obrazový roll CCW; korekcia znie CCW. Pri opačnom roll je korekcia CW.
- Korekcia kamery má teda rovnaké znamienko ako nameraný posun obrazu; automatická inverzia affine matice by dala nesprávny fyzický pokyn. Pozitívne OpenCV `getRotationMatrix2D` je obrazové CCW, locator theta má opačné znamienko.
- Pred pomenovaním fyzických X/Y sa odstráni `image_rotation` View. Os v tolerancii nepridá pokyn.

Pokyny predpokladajú pevnú formu, pohyb v obrazovej rovine kamery a pohľad zozadu kamery smerom na formu. Nástroj nemôže zo samotnej snímky rozlíšiť pohyb kamery od pohybu formy; nepodporuje fyzické mm, perspektívnu kalibráciu, zrkadlený obraz ani zmenu scale. Správnu orientáciu konkrétnej montáže treba overiť testami B–G.

## Bezpečnosť a asynchrónny capture

Vstup z RUN používa existujúce potvrdenie pozastavenia. Mode sa ihneď prepne do SETUP, RUN timer sa zastaví, externé vstupy sa neprijímajú a aktuálna kontrola sa dokončí. Diagnostika sa otvorí až po úspešnom `InspectionController.pause(runtime.quiesce)`.

Referenciu aj capture/alignment vykonáva existujúci `InspectionWorker` s jedným pracovníkom a bez fronty za rozpracovanou operáciou. Host povoľuje diagnostické operácie iba v PAUSED. Service používa rovnaké CameraService/PicoService, profil View a `ViewCapture`, vrátane podporovanej PRIME + produkčnej PIO dvojice v trigger režime. Nepoužíva druhý GStreamer stack.

Kamerový profil a trigger session sa pripravia už pri vstupe do sekcie, pred sprístupnením capture. Session zostáva pripravená medzi manuálnymi aj automatickými snímkami. Diagnostický `ViewCapture` používa `hardware_prepared=True`: nemení znova profil ani explicitne nepripravuje trigger. Existujúci PIO capture stále kontroluje pripravenosť, ale jeho cached vetva nereštartuje pipeline a neopakuje arming/settling. Každá snímka je naďalej nová PRIME + produkčná dvojica. Iný View s rovnakým profilom session zachová; odlišný kamerový profil vyžaduje riadené ukončenie a novú prípravu. Pri chybe capture sa session bezpečne ukončí a automatika zastaví; nový explicitný pokus ju znovu pripraví.

Diagnostika nemá Modbus ani DB handle a nevolá `InspectionRuntime.run`, produkčný pipeline, `signal_result`, insert_result alebo save_production_result. Nemení OK/NOK/yield ani produkčnú históriu. Recept je čerstvo načítaná lokálna kópia; všetky runtime zmeny locatora sú ďalšie kópie. Výber receptu sa počas diagnostiky zablokuje, View možno zmeniť medzi operáciami.

Automatika je single-shot timer: ďalší interval 0.5/1/2/5 s začne až po dokončení predchádzajúcej operácie. Default je manuálny režim, interval 1 s. Busy guard odmietne opakovaný capture; Qt UI nikdy nečaká na kameru/alignment. Hardvérová chyba zastaví automatické opakovanie.

Pri odchode sa oba timery zastavia, automatika sa vypne a výsledok pre opustenú generáciu stránky sa ignoruje. `finally` pri každom capture uvoľní iba per-capture guard; úspešná snímka nevolá Pico quiesce ani exit_trigger_session. Prebiehajúci hardvérový capture sa bezpečne dokončí a až potom odchod cez spoločný worker zavolá `close_session`, Pico quiesce a ukončenie trigger session. Cleanup je idempotentný; pri chybe sa vlastníctvo zachová na ďalší pokus a RUN sa neobnoví pred úspešnou pauzou. Prechod do RUN/SETUP/VÝSLEDKY, skrytie pri prepnutí kamerovej stanice aj zatvorenie aplikácie čakajú na cleanup. RUN sa znovu pripraví iba po explicitnom návrate do RUN. Existujúci master stream môže zostať otvorený ako zdieľaný prostriedok; nový stream sa nevytvára.

## Testy a výsledky

Nových **60 testov prešlo**. Session testy overujú opakovaný capture bez reštartu, zmenu profilu, cleanup/retry po chybe a odchod do RUN/SETUP/VÝSLEDKY, skrytie stránky i zatvorenie počas capture. Regresné kontroly overujú funkčný locator aj pri vypnutom checkboxe „Zapnúť zarovnanie pozície“ a pôvodný horný riadok navigácie. Zahŕňajú hraničné X/Y/R tolerancie, všetky fyzické smery, rotáciu View, syntetický posun mimo pôvodnej search oblasti, znamienko skutočného rotačného matchingu, chýbajúce tolerancie, nejednoznačnú referenciu, nízku confidence, RAW golden orientáciu, nemennosť JSON/recipe/snímok, shared capture v MASTER/TRIGGER, cleanup pri chybe, UI vytvorenie, automatiku bez paralelných requestov, zastavenie timerov, neskorý výsledok, stabilný switching viewport a pause/deferred RUN navigáciu bez Modbus/produkčných počítadiel.

Celá sada: **587 passed, 1 failed, 0 skipped**. Existujúce zlyhanie `tests/test_run_page_navigation.py::test_recovery_notice_disappears_after_next_ok_result_only` bolo prítomné pred implementáciou: jeho `SimpleNamespace` nemá `_resume_live_preview_after_trigger`, ktorú existujúci callback volá. Test ani produkčné recovery správanie sa kvôli tomuto problému nemenili. Testy bežali na x86_64 bez fyzického Jetson/USB; produkčné časovanie a mechanické pokyny na skutočnej montáži ešte neboli overené. GUI bolo vizuálne skontrolované pri 960 × 600; stránka používa pružný obrazový priestor a scroll na menších displejoch.

Cloud, z koreňa repozitára:

```bash
/workspace/hdf-runtime/run-python -m pytest -q -p no:cacheprovider tests/test_camera_position.py tests/test_troubleshooting_page.py
/workspace/hdf-runtime/run-python -m pytest -q -ra -p no:cacheprovider tests
git diff --check
```

Jetson, existujúci produkčný image a samostatné testovacie dáta:

```bash
bash docker/build.sh
mkdir -p /tmp/hdf-position-test-data
docker run --rm --runtime nvidia --privileged \
  -e QT_QPA_PLATFORM=offscreen -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/workspace -v /tmp/hdf-position-test-data:/data \
  -w /workspace hdf_vision:dev bash -lc \
  'python3 -m pip install pytest==8.3.5 && python3 -m pytest -q -p no:cacheprovider tests/test_camera_position.py tests/test_troubleshooting_page.py'
```

Tieto Jetson príkazy sú manuálny postup, neboli vykonané v x86_64 cloude.

## Presný manuálny test na Jetson

Pred testom zastavte výrobu príslušnej stanice. Zaznamenajte aktívny recept/View, OK/NOK/yield, počet riadkov SQLite `results`, zoznam run_id a snímok RUN, hash `recipe.json` a goldenu. Nastavte monitoring PLC/Modbus OK/NOK výstupov. Na viacerých staniciach porovnávajte iba testovanú stanicu; ostatné môžu legitímne pridávať výsledky. Vplyv bežného retention odlíšte od diagnostiky. Použite správnu pevnú formu, stabilné svetlo a priradenú kameru/Pico. Spustite `bash docker/run.sh`, vyberte recept a View, otvorte TROUBLESHOOTING a potvrďte pauzu.

| Test | Kroky | Očakávanie |
| --- | --- | --- |
| A – pôvodná poloha | Vráťte kameru do mechanickej polohy goldenu. Zvoľte manuálne a ZHOTOVIŤ SNÍMKU. | Pri dostupných troch limitoch a platnom guided_edge: POLOHA KAMERY OK, current zelený. Pri chýbajúcom R: neúplné vyhodnotenie, nie falošné OK. Golden vždy zelený. |
| B – kamera doprava | Posuňte kameru v jej obrazovej rovine doprava nad uložený limit X; zhotovte snímku. | Obrazový posun opačný než kamera, fyzický pokyn DOĽAVA. Po korekcii a novej snímke sa odchýlka zmenší. |
| C – kamera doľava | Z pôvodnej polohy posuňte kameru doľava a zhotovte snímku. | Pokyn DOPRAVA; návrat zmenší odchýlku. |
| D – kamera hore | Z pôvodnej polohy posuňte kameru hore a zhotovte snímku. | Pokyn DOLE; návrat zmenší odchýlku. |
| E – kamera dole | Z pôvodnej polohy posuňte kameru dole a zhotovte snímku. | Pokyn HORE; návrat zmenší odchýlku. |
| F – CW roll | Z pôvodnej polohy otočte kameru CW pri pohľade zozadu smerom na formu, nad uložený R limit a v merateľnom rozsahu referencie. | Obraz theta CCW, pokyn OTOČ PROTI SMERU HODINOVÝCH RUČIČIEK. Po návrate znovu odmerajte X/Y. |
| G – CCW roll | Z pôvodnej polohy otočte kameru opačne. | Pokyn OTOČ V SMERE HODINOVÝCH RUČIČIEK; po návrate odchýlka klesne. |
| H – automatika | Vyberte Automaticky, 1 s. Pomalými pohybmi nastavujte kameru; skúste stlačiť capture počas operácie. Skúste aj 0.5/2/5 s. Opustite stránku počas capture. | Výsledok sa aktualizuje po každom novom skutočnom capture, UI reaguje, nevzniknú paralelné capture. Timer sa zastaví; rozpracovaný capture/cleanup sa dokončí pred RUN. |
| I – prepínanie | S malým viditeľným posunom vyberte PREPÍNANIE. Sledujte Golden/Current označenie, rám a stred/detail obrazu. | Striedanie každých 400 ms, rovnaký viewport/scale/centrovanie; mechanický posun zostáva viditeľný. |
| J – produkčné dáta | Bez nového RUN triggera porovnajte stav z úvodu. | OK/NOK/yield aj riadky results ostali rovnaké, žiadny nový produktový run/snímka, rovnaké recipe/golden hashe, žiadny Modbus OK/NOK pulz z diagnostiky. |

Testy A–J zopakujte v MASTER aj hardvérovom TRIGGER režime. Pri TRIGGER zhotovte aspoň päť manuálnych a päť automatických snímok: kamera má držať stream mode 1 počas celej návštevy, každá snímka používa novú PRIME + produkčnú dvojicu a log nesmie opakovať `pio_configure`, reštart pipeline ani úvodný arming/settling medzi úspešnými snímkami. Cleanup SESSION/IDLE má prísť až po odchode; počas rozpracovanej snímky počká na jej dokončenie. Overte odchod do RUN, SETUP, VÝSLEDKY, prepnutie inej kamerovej stanice aj zatvorenie aplikácie. Zopakujte vstup do Troubleshooting: nová návšteva pripraví jednu novú session. Vyvolajte capture chybu: automatika sa zastaví a ďalší explicitný pokus znovu pripraví session. V MASTER overte nový frame podľa Pico eventu, nie opakovaný cached preview. Po návrate do RUN vykonajte jednu normálnu kontrolu a overte štandardný výsledok, počítadlá, históriu a Modbus. Overte aj VÝSLEDKY → RUN a otvorenie Golden Wizard zo SETUP.

Dodatočne zakryte referenciu/zmeňte osvetlenie: očakáva sa POLOHU SA NEPODARILO SPOĽAHLIVO VYHODNOTIŤ, neutrálne current orámovanie a žiadne smery. Vyskúšajte View orientácie 90/180/270°; fyzické pokyny musia zostať konzistentné s montážou.

## Známe obmedzenia

- Bez explicitnej rotačnej tolerancie alebo rotačného merania nemožno deklarovať všetky tri osi OK. Model sa kvôli diagnostike nemení.
- Diagnostika používa locator receptu nezávisle od checkboxu „Zapnúť zarovnanie pozície“ (`pose_enabled`). Pri chýbajúcom locatore alebo viacerých aktívnych locatoroch sa nezvolí svojvoľná referenčná transformácia; UI vypíše nedostupné spoľahlivé vyhodnotenie.
- Rotational template search zostáva vo svojom uloženom uhlovom rozsahu a rozlíšení; guided_edge zachováva svoju uloženú šírku referenčného pásu. Pri väčšej odchýlke môže správne odmietnuť hodnotenie.
- Nálezy s existujúcim upozornením na slabý kontrast/nejednoznačnosť sa nepoužijú na fyzické pokyny.
- Poloha kamery versus poloha formy a orientácia konkrétnej montáže vyžadujú fyzické overenie; nie je implementovaná perspektívna ani rozmerová kalibrácia.
- Reálne Jetson capture/USB/PIO, mechanická presnosť a dotykový displej vyžadujú uvedené manuálne overenie.

### Ponuka diagnostiky a vizuálne pokyny

Tab TROUBLESHOOTING otvorí ponuku diagnostických činností. Kamera sa pripraví až
po výbere **Overenie polohy kamery**; otvorenie samotnej ponuky nepreruší RUN.
Tlačidlo **Späť na Troubleshooting** vráti používateľa do ponuky. Pri prebiehajúcej
snímke počká aplikácia na jej dokončenie a potom ukončí trigger session.

Informácie o recepte, View a toleranciách sú vľavo nad RAW GOLDEN. Merania,
stav a pokyny na pohyb kamery sú vpravo nad RAW AKTUÁLNA. Žlté šípky ukazujú
iba potrebné korekcie, v rovnakých fyzických smeroch ako textové pokyny.
Prepínač **Šípky korekcie** umožňuje vrstvu skryť. Pri prepínaní snímok sa šípky
zobrazujú iba na aktuálnej snímke. Pri nespoľahlivom výsledku sa nezobrazujú.
Vrstva nemení RAW pixely, locator, recept ani výsledky RUN.
