"""Retired legacy module.

Wallpaper selection and palette compilation now cross the explicit
``WallpaperProvider`` / ``AppearanceService`` compatibility boundary.
"""

from __future__ import annotations


class LegacyWallpaperEngineRemoved(RuntimeError):
    pass
