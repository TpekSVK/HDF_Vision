# Katalóg nástrojov a učenie ROI – 2026-09-12

## Nové kontroly

Základné: vyhľadanie a zarovnanie vzoru, podobnosť vzhľadu SSIM, prítomnosť podľa
svetlej/tmavej plochy, naučená kontrola odchýlok V2, plocha rozdielov oproti
referencii a odchýlka profilu hrany. Špecializované: kontrola prázdnej formy a
priepustnosť svetla. Pokročilé: MSE a NCC. Každý má praktickejší popis použitia.

SSD a nedokončený Absolútny rozdiel sú skryté pri pridávaní. Ich implementácie
ani uložené recepty nie sú odstránené; starý absdiff naďalej vracia WARN a jeho
popis výslovne odporúča náhradu. Existujúce identifikátory nástrojov ostávajú.

Otvor je prednastavenie, ktoré vytvára presence_absence s jasnou polaritou,
prahom 200, bez vyhladenia a plochou 100–10000 pixelov. Sú to východiskové
hodnoty na doladenie podľa dielu, nie univerzálne výrobné limity. Staré nástroje
light_presence sa nemigrujú a fungujú pôvodným spôsobom.

## Plocha rozdielov oproti referencii (interné edge_change)

Pôvodný výpočet absdiff/prah/voliteľné morfologické čistenie zostáva. Limit
edge_ratio_max sa zobrazuje v percentách, ale uložená hodnota ostáva 0–1.
Pridané metriky changed_area_pct a largest_change_px. Nový limit
largest_change_max_px je predvolene 0 = vypnutý, takže staré recepty nemenia
rozhodovanie. Súvislé oblasti sa počítajú s 8-susednosťou iba v platnej maske ROI.
NOK môže vyvolať celková plocha alebo najväčšia oblasť; popis NOK vie uviesť oba limity.
Náhľad filtrovaného ROI ukazuje skutočnú výslednú masku rozdielov, nie iba vyhladenie.
Metadáta formulárov teraz zachovávajú jednotku, mierku zobrazenia a počet desatinných
miest, ktoré sa predtým pri prevode ToolSchemaField strácali.

## Oprava učenia ochrany formy a V2

Zber OK aj NOK vzoriek vynucoval image_rotation_override=0, hoci ROI je kreslená
na otočenom golden. Nový spoločný helper odovzdáva rotáciu aktuálneho pohľadu
pred výrezom. Neotáča už otočený výrez. Rozdielne rozmery snímky voči golden sa
odmietnu; ROI mimo snímky sa odmietne namiesto tichého orezania.

Staré nesprávne vzorky/modely sa automaticky nemažú ani neopravujú. Pri nástroji,
ktorého vzorky zobrazovali nesprávny objekt, treba použiť Reset učenia, zozbierať
nové OK/NOK vzorky a prepočítať model. Oprava sa vzťahuje na oba štatistické nástroje.
Pri pohybujúcom sa diele zostáva potrebné overiť aj zarovnanie medzi referenciou
 a učením; táto oprava rieši konkrétne vynútenú nulovú rotáciu a rozmery.

## Overenie

65 testov prešlo v Docker/offscreen Qt. Pokryté: kategórie a skryté položky,
legacy implementácie, prednastavenie otvoru, stabilné identifikátory, učenie pri
0/90/180/270 stupňoch, odmietnutie nesprávnych rozmerov/ROI, maska rozdielov,
najväčšia oblasť a deaktivácia limitu, ignorovaná maska, prepočet percent na pôvodnú
hodnotu receptu a existujúce testy prítomnosti/hrany/formulárov.
Log: /tmp/hdf_catalog_learning_final3.log. Následne doplnené iba zalamovanie názvu
karty a tooltip plného popisu. Hardvérový zber ani záťažové meranie novej analýzy
súvislých oblastí sa v tejto fáze nevykonali. Známe staršie locator testy sa tu neriešili.
