#!/usr/bin/env python3
"""Desktop launcher: show a cover before Docker/imports, log without a terminal."""
import argparse
import fcntl
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='Show only the startup cover for five seconds.')
    args = parser.parse_args()
    from app.ui.theme import COLOR_BG

    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'hdf-vision'
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / 'launcher.lock').open('a')
    if not args.preview:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
    logs = state / 'logs'
    logs.mkdir(exist_ok=True)
    log = logging.getLogger('hdf-desktop-launcher')
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(logs / 'application.log', maxBytes=5 * 1024 * 1024,
                                 backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    log.addHandler(handler)
    log.info('=== HDF Vision startup ===')
    # Paint the brand before importing GTK; this also matches the earlier
    # Xsession cover while the desktop and application are still loading.
    try:
        cover = ROOT / 'tools/boot_branding/wallpaper.png'
        painted = subprocess.run(['xsetbg', '-quiet', '-border', COLOR_BG, '-center', str(cover)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=2).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        painted = False
    if not painted:
        try:
            subprocess.run(['xsetroot', '-solid', COLOR_BG], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            log.info('Skoré prekrytie X pozadia nie je dostupné; pokračujem cez GTK.')

    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio
    from gnome_startup import register_startup

    window = Gtk.Window(title='HDF Vision — načítavanie')
    window.set_decorated(False)
    window.set_keep_above(True)
    window.set_accept_focus(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    css = Gtk.CssProvider()
    css.load_from_data(('''window { background: %s; color: #e6e8eb; }
        label { background: transparent; }
        #brand { font: bold 25px sans-serif; letter-spacing: 3px; }
        #message { font: 17px sans-serif; color: #9aa4af; }
        button { min-height: 44px; min-width: 160px; }''' % COLOR_BG).encode())
    Gtk.StyleContext.add_provider_for_screen(window.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    outer.set_halign(Gtk.Align.CENTER); outer.set_valign(Gtk.Align.CENTER)
    outer.set_spacing(18)
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    geometry = monitor.get_geometry() if monitor else None
    height = geometry.height if geometry else 768
    # Login autostart can run before the window manager. EWMH fullscreen is
    # only a request then; provide the real cover geometry before first map.
    if geometry:
        window.set_default_size(geometry.width, geometry.height)
        window.resize(geometry.width, geometry.height)
        window.move(geometry.x, geometry.y)
    size = min(420, int(height * .42))
    source = GdkPixbuf.Pixbuf.new_from_file(str(ROOT / 'app/assets/hdf_vision.png'))
    # The desktop icon has a stray translucent column at its left edge.
    # Clip that empty edge before scaling, leaving the original icon intact.
    logo = source.new_subpixbuf(1, 0, source.get_width() - 1, source.get_height())
    image = Gtk.Image.new_from_pixbuf(logo.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR))
    outer.pack_start(image, False, False, 0)
    brand = Gtk.Label(label='HDF VISION'); brand.set_name('brand')
    outer.pack_start(brand, False, False, 0)
    message = Gtk.Label(label='Spúšťam aplikáciu…'); message.set_name('message')
    outer.pack_start(message, False, False, 0)
    spinner = Gtk.Spinner(); spinner.start()
    outer.pack_start(spinner, False, False, 0)
    close = Gtk.Button(label='Zavrieť'); close.set_no_show_all(True)
    outer.pack_start(close, False, False, 12)
    window.add(outer); outer.set_opacity(0)
    window.fullscreen(); window.show_all()
    temporary = tempfile.TemporaryDirectory(prefix='hdf-vision-startup-')
    ready = Path(temporary.name) / 'ready'
    process = None
    output_thread = None
    fading = False
    timed_out = False
    visible_at = time.monotonic()

    def animate(setter, start, end, duration, finished=lambda: None):
        began = time.monotonic()
        def tick():
            amount = min(1, (time.monotonic() - began) / duration)
            smooth = amount * amount * (3 - 2 * amount)
            setter(start + (end - start) * smooth)
            if amount >= 1:
                finished()
                return False
            return True
        GLib.timeout_add(16, tick)

    def dismiss():
        nonlocal fading
        if fading:
            return
        fading = True
        spinner.stop()
        if window.get_screen().is_composited():
            animate(lambda value: Gtk.Widget.set_opacity(window, value), 1, 0, .5, window.hide)
        else:
            window.hide()
        if args.preview:
            GLib.timeout_add(550, lambda: (Gtk.main_quit(), False)[1])

    def failed(text):
        spinner.stop(); message.set_text(text)
        window.set_accept_focus(True)
        close.show()

    def close_clicked(*_):
        if process is not None and process.poll() is None:
            dismiss()
        else:
            Gtk.main_quit()
    close.connect('clicked', close_clicked)

    def launch():
        nonlocal process, output_thread
        log.info('Startup cover mapped after %.3fs.', time.monotonic() - visible_at)
        if not args.preview:
            register_startup(Gio, GLib, log, Gtk.main_quit)
        animate(outer.set_opacity, 0, 1, .45)
        if not args.preview:
            env = dict(os.environ, HDF_STARTUP_DIR=temporary.name, PYTHONUNBUFFERED='1')
            try:
                process = subprocess.Popen(['bash', str(ROOT / 'docker/run.sh')], cwd=ROOT,
                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace')
                def record_output():
                    for line in process.stdout:
                        log.info(line.rstrip())
                    process.stdout.close()
                output_thread = threading.Thread(target=record_output, name='launcher-output', daemon=True)
                output_thread.start()
            except OSError as exc:
                log.error('%s', exc); failed('Aplikáciu sa nepodarilo spustiť.')
        return False

    def poll():
        nonlocal timed_out
        if args.preview:
            if time.monotonic() - visible_at > 5:
                dismiss()
            return True
        if process is None:
            return True
        code = process.poll()
        if code is not None:
            if fading or code == 0:
                Gtk.main_quit()
            else:
                failed('Aplikáciu sa nepodarilo spustiť.')
            return False
        if ready.exists():
            dismiss()
        elif not fading and not timed_out and time.monotonic() - visible_at > 90:
            timed_out = True
            failed('Načítanie trvá dlhšie. Môžete odkryť aplikáciu.')
            close.set_label('Zobraziť aplikáciu')
        return True

    GLib.timeout_add(70, launch)
    GLib.timeout_add(80, poll)
    try:
        Gtk.main()
    finally:
        if output_thread is not None:
            output_thread.join(timeout=.5)
        temporary.cleanup(); handler.close(); log.removeHandler(handler); lock.close()
    return 0


if __name__ == '__main__':
    # Add the checkout so the launcher can read the same palette without Qt.
    import sys
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
