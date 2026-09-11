# Náhľad filtrovaného ROI

Checkbox „Zobraziť filtrované ROI“ je v RUN a Golden Wizard. Je to iba vizualizácia;
nezapisuje parametre/recept ani golden a nemení rozhodovanie nástroja.

## Použitie

RUN: zapnúť checkbox, vybrať konkrétny nástroj v existujúcej ponuke ROI a vykonať
kontrolu. Zobrazí sa jeho medzivýsledok z poslednej kontroly. Ak bol checkbox pri
snímaní vypnutý, dáta sa spätne nedopočítavajú: treba novú kontrolu. Bez vybraného
nástroja (Všetky nástroje) sa zobrazí výzva na výber, aby sa viaceré filtre v
prekrývajúcich ROI neprepisovali. Obrysy ROI možno zapínať nezávisle.

Golden Wizard: vybrať nástroj a zapnúť checkbox. Ten istý nástroj sa vykoná nad
kópiou jeho konfigurácie a golden ako referenciou aj vstupom. Výsledok kontroly sa
neukladá. Aktualizácie po zmene ROI alebo parametrov sa zlúčia časovačom 180 ms.
Modelové učenie ani locator sa na účely náhľadu nespúšťajú. Náhľad patrí golden,
nie živému obrazu; ovládanie je pri Live vypnuté. Originálne pixely zostávajú
v current_img a po vypnutí checkboxu sa obnovia.

## Zobrazené fázy

- MSE, SSD, NCC: ROI po internom Gaussian blur; pri sigma=0 explicitne Bez filtrovania.
- Edge Change: ROI po Gaussian blur pred rozdielom voči golden.
- Edge Profile Deviation: skutočný normálový Sobel gradient; vizualizácia absolútnej
  hodnoty do 8 bitov (hodnoty nad 255 sa orežú, výpočet pracuje s pôvodným gradientom).
- Light Presence a Presence/Absence: binárny obraz používaný na počítanie plochy.
- SSIM: skutočný vstup metriky, označený Bez predbežného filtrovania; nejde o mapu SSIM.
- Ostatné nástroje zatiaľ medzivýsledok neposkytujú; UI to výslovne uvedie.

## Implementácia a limity

PairTool publikuje snímku ROI cez _publish_filtered_roi, SSIM cez voliteľný callback.
PipelineToolReport drží dočasný filtered_roi mimo diagnostík/exportu receptu.
Zber je v produkčnej pipeline predvolene vypnutý. Zapnutie uchová len ROI nástrojov
aktuálnej kontroly; nevykonáva druhý filter. Kompozícia prebieha až pri zobrazovaní.
Platí maska tvaru ROI aj ignorované oblasti. Pri virtuálnom zarovnaní sa ROI premietne
späť do zobrazovaných súradníc. Okolie ostáva pôvodné. Rotácia pohľadu sa neopakuje.

Aktualizácia pixmapy editora zachová scénu, ROI položky, zoom a históriu undo;
nepoužíva resetujúce set_background. Zobrazenie nenahrádza zdrojové dáta nástroja.
Náhľad je určený na ladenie; réžia jeho kopírovania/zobrazenia pri plnom produkčnom
zaťažení nebola meraná. Veľká ROI v Golden môže predĺžiť odozvu jeho prepočtu.

## Overenie 2026-09-11

63 testov prešlo v Dockeri: skutočný blur, binarizácia a gradient, parita metriky
s náhľadom/bez neho, maska/ellipse, virtuálna translácia, prepínanie RUN pri všetkých
rotáciách, Golden checkbox, zachovanie geometrie a zoomu v Qt, navigácia a recepty.
Log /tmp/hdf_filtered_final2.log. Následná úprava iba rozmiestnila RUN checkbox
s popisom do samostatného riadku, aby nerozširovali hlavný panel akcií.

Širšia sada tests/test_tool_pipeline.py má štyri existujúce zlyhania locatora.
Rovnaké štyri zlyhania potvrdené aj na archíve pôvodného HEAD (pred touto zmenou);
logy /tmp/hdf_filtered_full.log a /tmp/hdf_filtered_baseline.log.
Nové testy nemenia očakávania týchto starších testov.
Hardvérový produkčný test ani push/merge tejto funkcie zatiaľ neprebehli.
