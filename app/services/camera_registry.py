"""Stable camera roles and selected-view routing for existing setup widgets.

Production workers receive concrete camera/channel objects, never these mutable
UI selectors. Switching the selected view therefore cannot reroute a capture.
"""
from contextlib import contextmanager


class CameraRegistry:
    def __init__(self, cameras, picos, *, names=None, serials=None, transport=None):
        if not cameras or set(cameras) != set(picos):
            raise ValueError('Každá kamera potrebuje vlastný kanál Pica.')
        self.cameras, self.picos = dict(cameras), dict(picos)
        self.names = names or {key: key.replace('camera_', 'Kamera ') for key in cameras}
        self.serials = dict(serials or {})
        self.transport = transport
        self.selected = next(iter(cameras))

    def select(self, camera_id):
        if camera_id not in self.cameras:
            raise ValueError(f'Kamera {camera_id} nie je priradená. Otvorte Kamery a Pico.')
        self.selected = camera_id

    @contextmanager
    def selected_view(self, view):
        previous = self.selected
        self.select(view.camera_id)
        try:
            yield self.cameras[self.selected], self.picos[self.selected]
        finally:
            self.selected = previous

    def choices(self):
        return [(key, self.names[key]) for key in self.cameras]

    def close(self):
        for camera in self.cameras.values():
            camera.stop(caller='workcell_close')
        if self.transport:
            self.transport.close()


class CameraRouter:
    def __init__(self, registry):
        object.__setattr__(self, 'registry', registry)

    def select_view(self, view):
        self.registry.select(view.camera_id)

    def __getattr__(self, name):
        return getattr(self.registry.cameras[self.registry.selected], name)

    def __setattr__(self, name, value):
        setattr(self.registry.cameras[self.registry.selected], name, value)


class PicoRouter:
    def __init__(self, registry):
        self.registry = registry
        self._callbacks = {}

    def __getattr__(self, name):
        return getattr(self.registry.picos[self.registry.selected], name)

    def _camera(self, camera):
        return self.registry.cameras[self.registry.selected] if isinstance(camera, CameraRouter) else camera

    def set_live_preview(self, enabled, camera=None):
        return self.registry.picos[self.registry.selected].set_live_preview(enabled, self._camera(camera))

    def prepare_trigger(self, camera):
        return self.registry.picos[self.registry.selected].prepare_trigger(self._camera(camera))

    def prepare_master(self, camera):
        return self.registry.picos[self.registry.selected].prepare_master(self._camera(camera))

    def capture_trigger(self, camera, **kwargs):
        return self.registry.picos[self.registry.selected].capture_trigger(self._camera(camera), **kwargs)

    def capture_master(self, source, camera):
        return self.registry.picos[self.registry.selected].capture_master(source, self._camera(camera))

    def register_trigger_callback(self, callback):
        wrappers = []
        for camera_id, pico in self.registry.picos.items():
            def forward(source, ident=camera_id):
                if self.registry.selected == ident:
                    callback(source)
            pico.register_trigger_callback(forward)
            wrappers.append((pico, forward))
        self._callbacks[callback] = wrappers

    def unregister_trigger_callback(self, callback):
        for pico, forward in self._callbacks.pop(callback, []):
            pico.unregister_trigger_callback(forward)

    def register_camera_trigger_callback(self, callback):
        for camera_id, pico in self.registry.picos.items():
            pico.register_trigger_callback(lambda source, ident=camera_id: callback(source, camera_id=ident))

    def close(self):
        for pico in self.registry.picos.values():
            pico.close()
        if self.registry.transport:
            self.registry.transport.close()
