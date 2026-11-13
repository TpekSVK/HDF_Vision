"""Acknowledge an early GNOME autostart without delaying the GTK cover."""
import os


def register_startup(Gio, GLib, log, on_stop):
    startup_id = os.environ.pop('DESKTOP_AUTOSTART_ID', '')
    if not startup_id:
        return

    def connected(_source, result, *_unused):
        try:
            bus = Gio.bus_get_finish(result)
        except GLib.Error as exc:
            log.warning('GNOME session connection failed: %s', exc)
            return

        def registered(connection, reply, *_unused):
            try:
                path, = connection.call_finish(reply).unpack()
            except GLib.Error as exc:
                log.warning('GNOME startup registration failed: %s', exc)
                return
            log.info('GNOME startup registered; cover is mapped.')

            def session_signal(connection, _sender, object_path, _interface, signal, _parameters, *_unused):
                if signal in ('QueryEndSession', 'EndSession'):
                    connection.call('org.gnome.SessionManager', object_path,
                        'org.gnome.SessionManager.ClientPrivate', 'EndSessionResponse',
                        GLib.Variant('(bs)', (True, '')), None,
                        Gio.DBusCallFlags.NONE, 2000, None, response_sent)
                elif signal == 'Stop':
                    on_stop()

            def response_sent(connection, reply, *_unused):
                try:
                    connection.call_finish(reply)
                except GLib.Error as exc:
                    log.warning('GNOME session response failed: %s', exc)

            connection.signal_subscribe('org.gnome.SessionManager',
                'org.gnome.SessionManager.ClientPrivate', None, path, None,
                Gio.DBusSignalFlags.NONE, session_signal)

        bus.call('org.gnome.SessionManager', '/org/gnome/SessionManager',
            'org.gnome.SessionManager', 'RegisterClient',
            GLib.Variant('(ss)', ('HDF_Vision', startup_id)), GLib.VariantType.new('(o)'),
            Gio.DBusCallFlags.NONE, 2000, None, registered)

    Gio.bus_get(Gio.BusType.SESSION, None, connected)
