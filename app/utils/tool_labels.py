"""Slovak display names; persisted names and tool identifiers stay unchanged."""
TOOL_NAMES = {
    'ssim': 'Štrukturálna podobnosť (SSIM)', 'mse': 'Stredná štvorcová chyba (MSE)',
    'ssd': 'Súčet štvorcov rozdielov (SSD)', 'ncc': 'Podobnosť vzoru (NCC)',
    'locator.template_match': 'Vyhľadanie a zarovnanie vzoru',
    'edge_change': 'Zmena hrán', 'edge_profile_deviation': 'Odchýlka profilu hrany',
    'light_transmission': 'Kontrola priepustnosti svetla', 'absdiff': 'Absolútny rozdiel',
}
OLD_NAMES = {'SSIM','MSE','SSD','NCC','Locator (Template Match)','Edge Change',
             'Edge Profile Deviation','Light Transmission Check','Abs Diff'}


def tool_display_name(tool):
    name = str(getattr(tool, 'name', '') or '').strip()
    kind = str(getattr(tool, 'type', '') or '').strip()
    if not name or name == kind or name in OLD_NAMES:
        return TOOL_NAMES.get(kind, name or kind or 'Nástroj')
    return name
