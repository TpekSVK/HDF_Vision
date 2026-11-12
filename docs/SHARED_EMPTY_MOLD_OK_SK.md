# Spoločné OK snímky – Kontrola prázdnej formy

Platí pre nástroj **Kontrola prázdnej formy** (`mold.protection_v1`).

1. Pri jednom nástroji zozbierajte OK snímky ako doteraz. Potvrdením zberu sa
   okrem jeho ROI výrezov uložia aj celé snímky do spoločného úložiska receptu.
2. Vyberte ďalší rovnaký nástroj v tom istom pohľade a nastavte jeho vlastné ROI.
3. V sekcii Učenie kliknite **Použiť spoločné OK snímky**.
4. Kliknite **Prepočítať model** daného nástroja. Model, nastavenia aj validácia
   zostávajú samostatné pre každý nástroj.

Import zachováva pôvodný postup prípravy snímky: locator, zarovnanie, ROI tvar
aj ignore masku cieľového nástroja. Každá spoločná snímka sa do nástroja importuje
najviac raz. Po ďalšom zbere možno rovnakým tlačidlom pridať iba nové snímky.
Pridanie vzoriek vyžaduje ručný prepočet; hotové modely ostatných nástrojov sa
automaticky nemenia. NOK snímky, modely, prahy a odporúčania sa nezdieľajú.

Snímky sú oddelené podľa receptu, pohľadu a kompatibilného nastavenia Golden,
kamery, osvetlenia a locatorov. Po zmene týchto nastavení nie sú staré spoločné
snímky automaticky dostupné. ROI a maska cieľového nástroja spoločné úložisko
nemenia, ale zmena jeho geometrie naďalej vyžaduje reset nekompatibilného lokálneho
učenia a nový prepočet modelu.

Reset učenia vymaže iba výrezy OK/NOK a model daného nástroja. Spoločné celé
snímky ani učenie ostatných nástrojov nevymaže. Po resete možno spoločné snímky
znovu použiť s novou ROI.

**Staré OK vzorky zozbierané pred touto úpravou sú iba ROI výrezy.** Chýbajúce
pixely mimo pôvodnej ROI nemožno obnoviť, preto sa nedajú zdieľať pre iné oblasti.
Existujúce vzorky a modely zostávajú zachované; pre zdieľanie treba vykonať nový
zber celých OK snímok.

Spoločné snímky sú uložené v dátovom adresári:
`recipes/<recept>/shared_mold_ok/<view>/<kontext>/`. Nevkladajú sa do Git repozitára.
Samostatný nástroj **Kontrola prázdnej formy V2** ani ostatné nástroje sa nemenia.
