"""Unprivileged IBus focus bridge; it never receives typed text or passwords."""
import json
import os
import sys


def main():
    uid = int(os.environ['HDF_DESKTOP_UID'])
    gid = int(os.environ['HDF_DESKTOP_GID'])
    if os.geteuid() == 0 and uid != 0:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    elif os.geteuid() != uid:
        raise PermissionError('Keyboard helper must use the desktop user')
    # PyGObject is supplied by the existing Ubuntu/Jetson image packages.
    sys.path.append('/usr/lib/python3/dist-packages')
    from gi.repository import Gio, GLib
    bus = Gio.DBusConnection.new_for_address_sync(
        os.environ['IBUS_ADDRESS'], Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
        Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

    def call(path, interface, method, args=None):
        return bus.call_sync('org.freedesktop.IBus', path, interface, method, args,
                             None, Gio.DBusCallFlags.NONE, 1000, None)

    context_path = call('/org/freedesktop/IBus', 'org.freedesktop.IBus',
                        'CreateInputContext', GLib.Variant('(s)', ('hdf-vision-keyboard',))).unpack()[0]
    interface = 'org.freedesktop.IBus.InputContext'
    call(context_path, interface, 'SetCapabilities', GLib.Variant('(u)', (8,)))
    loop = GLib.MainLoop()
    pending = b''

    def read_commands(fd, condition):
        nonlocal pending
        chunk = os.read(fd, 65536)
        if not chunk:
            loop.quit()
            return False
        pending += chunk
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            try:
                command = json.loads(line)
                if command.get('op') == 'show':
                    call(context_path, interface, 'FocusIn')
                    rect = tuple(int(value) for value in command['rect'])
                    call(context_path, interface, 'SetCursorLocation', GLib.Variant('(iiii)', rect))
                elif command.get('op') == 'hide':
                    call(context_path, interface, 'FocusOut')
            except (ValueError, KeyError, TypeError):
                continue
            except GLib.Error:
                loop.quit()
                return False
        return True

    GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP, read_commands)
    try:
        loop.run()
    finally:
        try:
            call(context_path, interface, 'FocusOut')
        except GLib.Error:
            pass
        bus.close_sync(None)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
