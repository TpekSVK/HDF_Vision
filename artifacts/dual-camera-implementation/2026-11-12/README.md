# Overenie dvojkamerovej implementácie, 12. 11. 2026

- `report.json`: skutočné kamery 162A5806/1D3A5806, spoločné Pico s FW4.3.1,
  10 MASTER + 10 TRIGGER spoločných AND cyklov, po 20 uložených snímok na kameru.
  Všetky snímania prešli; prípadné MSE NOK sú výsledky nástroja podľa scény.
  Po skončení sú kanály IDLE, Live a svetlá vypnuté, pôvodné nastavenia kamier obnovené.
- `gui-report.json`: skutočné rozhranie v offscreen Qt, RUN, zachovanie vybraného
  pohľadu, filtrovanie/otvorenie obrázkov, Live/svetlo/golden pre oba kanály.
- `run-dual.png`, `results-camera_2.png`, `golden-camera_2.png`: zábery tejto skúšky.
- `probe.py`, `gui_probe.py`: presné skúšobné skripty použité na tejto zostave.
  Majú pevné identity hardvéru a dočasné cesty; nejde o bežné spúšťanie aplikácie.
  GUI skript očakáva dáta vytvorené prvým skriptom.
- `regression/`: záverečné výsledky automatických skúšok, izolovaných po Qt súboroch.

Fyzické skúšky používali dočasné recepty/SQLite a vypnutý Modbus, nemenili
produkčné recepty ani PLC. Spúšťanie bolo softvérové cez reálny Pico/camera
capture flow; nové vonkajšie impulzy na 24 V vstupoch sa tu negenerovali.
Výkon je len krátka funkčná skúška s MSE pri 720p, nie záťažová kvalifikácia.

Skripty bežali v `hdf_vision:dev`, s `--runtime nvidia --network none`, prístupom
k ttyACM0/video0/video2/hidraw0/hidraw1, `/sys:/sys:ro`, repozitárom `/workspace:ro`,
skriptmi `/probe:ro`, novým výstupným priečinkom `/out` a `PYTHONPATH=/workspace`.
Pred ich prípadným opakovaním musí byť aplikácia zatvorená; overte identity a voľné zariadenia.

Používateľský postup: [Dve kamery](../../../docs/DUAL_CAMERA_SK.md).

Záverečný výsledok: **871 passed**. `regression/verified-summary.json`
spája výsledky34 dávok. GoldenTouch samostatne prešiel28/28 za318,42s;
pôvodný dávkový limit240s bol príliš krátky. Pôvodný timeout aj samostatný
úspešný beh zostali uložené; priložený runner má pre tento súbor900s limit.
