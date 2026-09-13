# Formát receptov – verzia 3

Aktuálny export obsahuje `format_version: 3`. Python trieda sa zatiaľ volá RecipeV2;
číslo formátu súboru je samostatný kontrakt. Recepty bez verzie, s inou verziou,
chýbajúce a nečitateľné súbory sa odmietajú. Používateľské súbory sa nemažú ani
neprevádzajú. Nový recept treba vytvoriť pod novým názvom.

## Jedna cesta načítania

- models/recipe_contract.py: verzia, výnimka a pravidlá odstránených typov/parametrov.
- models/recipe_document.py: kontrola štruktúry bez závislosti na UI či službách.
- services/recipe_format.py: čítanie JSON a kontrola voči aktuálnemu registru nástrojov.
- RecipeV2.from_dict tiež vyžaduje platný formát; prázdny nový recept sa vytvára
  explicitne cez RecipeV2(), nie parsovaním chýbajúceho súboru.
- Draft aj published prechádzajú rovnakou kontrolou. Pred zápisom prebehne validácia.

Nástroje sa ukladajú výhradne v `views[].tools`; koreňové `tools` je odmietnuté.
Interná vlastnosť RecipeV2.tools je pracovný zoznam pre pipeline, nie druhé úložisko.
ROI a ignore_mask sú polia nástroja. Ich duplikáty v params sú odmietnuté a runtime
ich už nečíta. Maska podporuje aktuálny PNG/base64 export; starý ndarray JSON či
poškodený blob vyvolajú chybu, nikdy sa potichu nezmenia na chýbajúcu masku.

Kontrolujú sa verzie, základná štruktúra, jedinečné ID, odkazy, režim snímania,
rotácia, externý zdroj/vstup, agregácia, číselné hodnoty a registrované typy.
Úplná sémantická validácia všetkých nástrojových parametrov, geometrie/modelov,
cyklov vetvenia a dostupnosti hardvéru zostáva samostatná zodpovednosť.

## Odstránené vetvy

- absdiff, ssd, light_presence: odstránené implementácie a registrácie.
- template_match: odstránený alias aj register aliasov; používa sa locator.template_match.
- Kontrola otvoru zostáva ako preset.bright_opening v katalógu; vytvára presence_absence.
- Locator prijíma translation, template_rotation alebo guided_edge. Staré angle_*
  nastavenia (okrem aktuálnych angle_range_deg/angle_step_deg), rotation_enabled
  a legacy_angle sa odmietajú; starý výpočet uhla je odstránený.
- Preklady starých režimov ako External Trigger/manual a pole modbus_input sú odstránené.
- regions.json, thresholds.json a databázová tabuľka prahov už nie sú zdrojom konfigurácie.
  Nová databáza tabuľku prahov nevytvára; existujúca tabuľka/súbory zostávajú nedotknuté.
- Odstránené legacy RUN, ToolService.evaluate, run_tool_isolated, from_recipe_data,
  serializácia starého RecipeData a alternatívne JSON úložisko v DbService.

Interné typové konštruktory stále podporujú tvorbu nového konceptu s predvolenými
hodnotami a prácu s numpy snímkami/ROI. Nie je to migrácia uložených starých receptov.
Výpočtové pomocné funkcie ako imaging.absdiff_u8 sú potrebné pre aktuálne MSE/rozdiely
obrazu. Historické názvy/metriky ostávajú čitateľné v prehliadači výsledkov.

## Ochrana aktívneho stavu

Recept sa kontroluje pred zmenou ToolService, pred prípravou RUN, pred golden/učiacou
snímkou a pred aplikovaním profilu kamery. Chyba profilu/receptu nepoužije starý
pohľad z cache. Pipeline aj test nástroja preveria všetky typy pred vykonaním prvého
nástroja; priame volanie locatora odmietne staré parametre.

Nový koncept môže existovať bez golden; existujúca golden sa dekóduje pred zmenou
aktívneho ToolService. RUN kontroluje podklady jednotlivých pohľadov.
Ak published neexistuje, zostáva dnešné správanie: použije sa validovaný koncept.
Ak published existuje a je poškodený, neprejde sa potichu na koncept.
Historické výsledky sa načítavajú nezávisle od importu receptu.

Viac kamier/Pico: zatiaľ nepridávame nefunkčné identifikačné polia. Budúci kontrakt
pohľadu odkáže na logickú kameru, konfigurácia pracoviska na fyzické zariadenia.
Zmena povinného kontraktu bude znamenať novú verziu formátu.
