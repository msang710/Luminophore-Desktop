"""One bounded alpha asset; no GTK badge window or snapshot rendering."""
from __future__ import annotations
import json
import logging
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Gdk, Gio, GdkPixbuf
from ..hyprland import HyprlandClient, HyprlandError
from ..state import read_palette_state
from ..theme import Palette


class SpatialBadgeSurface:
    """Prepare icon data once; compositor owns rendering and grab lifetime."""
    def __init__(self, application, monitor, event, app, provider, catalog_revision):
        self.client = HyprlandClient()
        self.event = event
        self.mask = ''
        self.sent = False
        try:
            resolved = provider.resolve(app, 64, 1, catalog_revision) if provider else None
            source = resolved.source if resolved else app.icon if app else None
            if isinstance(source, Gio.Icon):
                source = Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).lookup_by_gicon(source, 64, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.FORCE_SYMBOLIC)
            file = source.get_file() if source and hasattr(source, 'get_file') else None
            path = file.get_path() if file else None
            if path:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 64, 64, False)
                if not pixbuf.get_has_alpha():
                    pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
                raw, stride = pixbuf.get_pixels(), pixbuf.get_rowstride()
                channels = pixbuf.get_n_channels()
                self.mask = bytes(raw[y*stride+x*channels+channels-1] for y in range(64) for x in range(64)).hex()
        except Exception:
            logging.getLogger('luminophore-shell').exception('badge mask unavailable; using native fallback')
        self.retarget(monitor, event)

    def retarget(self, monitor, event):
        self.event = event
        palette = read_palette_state()[1].get(monitor.get_connector(), Palette('#63D8FF', '#F6BD69'))
        rgb = tuple(int(palette.primary[i:i+2],16)/255 for i in (1,3,5))
        try:
            self.sent = self.client.native_command('grab-badge', generation=str(event.generation), target_epoch=str(event.target_epoch),
                mask='' if self.sent else self.mask, red=rgb[0], green=rgb[1], blue=rgb[2]) == 'true' or self.sent
        except HyprlandError:
            logging.getLogger('luminophore-shell').debug('badge registration unavailable; using native fallback')

    def destroy(self):
        # The compositor consumes and clears the texture on end/cancel. No late
        # IPC cleanup may remove a newer grab's immutable asset.
        self.mask = ''
