"""Pure recipe routing rules, shared by editing, publication and acquisition."""
from collections import defaultdict


CAMERA_IDS = ('camera_1', 'camera_2')
OUTPUT_GROUPS = ('group_1', 'group_2')


def validate_view_routing(views, *, complete=False, available_cameras=None):
    if complete and len({view.camera_id for view in views}) > 1 and any(view.output_group is None for view in views):
        raise ValueError('Pri recepte s dvoma kamerami priraďte každému pohľadu výstupnú skupinu.')
    by_id = {view.id: view for view in views}
    groups = defaultdict(list)
    for view in views:
        if view.camera_id not in CAMERA_IDS:
            raise ValueError(f'{view.name}: neplatná kamera.')
        if available_cameras is not None and view.camera_id not in available_cameras:
            raise ValueError(f'{view.name}: {view.camera_id} nie je priradená.')
        if view.output_group not in (None, *OUTPUT_GROUPS):
            raise ValueError(f'{view.name}: neplatná výstupná skupina.')
        if view.output_mode not in ('independent', 'AND'):
            raise ValueError(f'{view.name}: neplatné správanie výstupu.')
        if type(view.and_timeout_ms) is not int or not 10 <= view.and_timeout_ms <= 120000:
            raise ValueError(f'{view.name}: limit AND musí byť 10 až 120 000 ms.')
        if view.output_mode == 'AND':
            if view.output_group is None:
                raise ValueError(f'{view.name}: AND potrebuje výstupnú skupinu.')
            if view.branch_enabled:
                raise ValueError('AND zatiaľ nemožno kombinovať s vetvením snímky.')
        if view.output_group:
            groups[view.output_group].append(view)
        references = [view.frame_source_view_id, view.branch_default_view_id, *view.branch_targets.values()]
        for ref in references:
            target = by_id.get(ref)
            if target and target.camera_id != view.camera_id:
                raise ValueError('Snímku ani vetvenie nemožno preniesť na inú kameru.')
            if target and view.branch_enabled and target.output_mode == 'AND':
                raise ValueError('Vetvenie nemôže smerovať do skupiny AND.')
            if (target and view.branch_enabled and ref in (view.branch_default_view_id, *view.branch_targets.values())
                    and target.output_group != view.output_group):
                raise ValueError('Vetvené pohľady musia používať rovnakú výstupnú skupinu.')
        source = by_id.get(view.frame_source_view_id)
        if view.output_mode == 'AND' and source and source.output_group != view.output_group:
            raise ValueError('Zdroj snímky pohľadu AND musí patriť do rovnakej skupiny.')
    for name, members in groups.items():
        modes = {view.output_mode for view in members}
        if len(modes) != 1:
            raise ValueError(f'{name}: nemožno miešať samostatné a AND vyhodnotenie.')
        if 'AND' in modes:
            if complete and len(members) < 2:
                raise ValueError(f'{name}: AND potrebuje aspoň dva pohľady.')
            if len({view.and_timeout_ms for view in members}) != 1:
                raise ValueError(f'{name}: všetky pohľady AND musia mať rovnaký limit.')
        elif len({view.camera_id for view in members}) > 1:
            raise ValueError('Nezávislé kamery musia mať rozdielne výstupné skupiny.')
    return dict(groups)
