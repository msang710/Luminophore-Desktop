"""Owned desktop policy, expressed as TOML data instead of executable modules."""


def native_defaults(primary='DP-2', left='DP-2', right='DP-1'):
    game = 'name:luminophore-base-' + primary
    gaming = '^(steam_app.*|gamescope)$'
    rules = []
    def rule(match, **effects):
        rules.append({'match': match, 'effects': effects})
    rule({'title': '^LUMINOPHORE Window Glow Demo$'}, float=True, center=True, size=[720, 480], opacity='1.0 override')
    rule({'float': True}, center=True, persistent_size=True)
    rule({'title': r'^([Pp]icture[-\s]?[Ii]n[-\s]?[Pp]icture)(.*)$'}, float=True, keep_aspect_ratio=True,
         size=['max(monitor_w, monitor_h)*0.25', 'min(monitor_w, monitor_h)*0.25'], pin=True)
    rule({'content': 'game'}, workspace=game)
    rule({'xdg_tag': '^(.*game.*)$'}, workspace=game, fullscreen_state=2, content='game', sync_fullscreen=True)
    rule({'class': gaming}, workspace=game)
    rule({'class': '^(steam)$', 'title': '^(Friends List)$'}, float=True)
    rule({'class': '^(steam)$', 'title': r'^(Launching\.{3})$'}, float=True, center=True, workspace=game)
    rule({'class': gaming, 'title': '^(.+)$', 'initial_title': r'negative:^(.*\\home\\.*)$'}, content='game', decorate=False,
         fullscreen_state=2, size=['monitor_w', 'monitor_h'], sync_fullscreen=True)
    rule({'class': '^(steam_app.*)$', 'initial_title': '^$'}, center=True, float=True, fullscreen=False, fullscreen_state=0, workspace=game)
    rule({'class': '^(spotify)$'}, workspace='special:luminophore-spotify silent', no_initial_focus=True)
    rule({'class': r'^(.*\.exe)$', 'float': True}, monitor=primary, center=True, fullscreen_state=0)
    rule({'class': '^(.*[Ll]auncher.*)$'}, float=True, monitor=primary)
    rule({'class': '^(vesktop|discord)$'}, monitor=primary)
    rule({'class': '^(.*[Cc]alc.*)$'}, float=True, size=['max(monitor_w, monitor_h)*0.17', 'min(monitor_w, monitor_h)*0.43'])
    rule({'class': r'^(org\.kde\.keditfiletype)$'}, float=True)
    rule({'class': r'^(org\.kde\.ark)$'}, size=['max(monitor_w, monitor_h)*0.40', 'min(monitor_w, monitor_h)*0.40'])
    rule({'class': '^(.*satty.*)$', 'title': '^(Satty)$'}, min_size=['max(monitor_w, monitor_h)*0.35', 'min(monitor_w, monitor_h)*0.35'], float=True)
    rule({'class': r'^(org\.kde\.dolphin)$', 'title': r'^(Moving.*|Create New.*|Extract.*|Compress.*|Copying.*|Progress.*|Configure.*|Properties.*|Choose\sApplication.*)$'}, float=True)
    rule({'class': '^steam_app_1818450$'}, float=False, fullscreen=True)
    for expression in ('^(firefox|zen)$', '^(kitty|ghostty|[Kk]onsole|Alacritty|gnome-terminal|xfce[0-9]?-terminal)$',
                       r'^(mpv|org.kde.haruna|.*plex.*|org\.kde\.gwenview|.*vlc.*)$'):
        rule({'class': expression}, opacity='1.0 override')
    for match in ({'class': '^(kvantummanager|qt[56]ct|nwg-look)$'},
                  {'class': '^(org.pulseaudio.pavucontrol|blueman-manager|nm-applet|nm-connection-editor)$'},
                  {'title': '^(Winetricks.*|Protontricks.*)$'},
                  {'title': '^(Open|Authentication Required|Add Folder to Workspace|Choose Files|Save As|Confirm to replace files|File Operation Progress)$'},
                  {'initial_title': '^(Open File)$'}, {'class': '^([Xx]dg-desktop-portal-gtk)$'},
                  {'title': '^(File Upload|Choose wallpaper|Library)(.*)$'}, {'class': '^(.*dialog.*)$'},
                  {'title': '^(.*dialog.*)$'}, {'class': '^(hyprland-share-picker)$'}):
        rule(match, float=True)
    rule({'class': '.*'}, suppress_event='maximize')
    rule({'class': '^$', 'title': '^$', 'xwayland': True, 'float': True, 'fullscreen': False, 'pin': False}, no_focus=True)
    return {
        'palette': {'primary': 'ff545a92', 'surface_container': 'fffbf8ff', 'secondary': 'ff5c5d72', 'error': 'ffba1a1a'},
        'applications': {'terminal': 'ghostty', 'files': 'dolphin', 'browser': 'google-chrome-stable',
                         'editor': 'gnome-text-editor --new-window', 'calculator': 'gnome-calculator', 'mission_center': 'missioncenter'},
        'environment': {'XMODIFIERS': '@im=fcitx', 'QT_IM_MODULE': 'fcitx'},
        'startup': [], 'shutdown': [],
        'gestures': [{'fingers': 3, 'direction': direction, 'action': action}
                     for direction, action in (('down', 'close'), ('up', 'fullscreen'), ('left', 'float'))],
        'window_rules': rules,
        'layer_rules': [
            {'name': 'luminophore-shell-blur', 'match': {'namespace': '^luminophore-shell-.*$'}, 'effects': {'blur': True, 'ignore_alpha': 0.08}},
            {'name': 'luminophore-native-bloom-no-blur', 'match': {'namespace': '^luminophore-native-bloom(-top)?$'}, 'effects': {'blur': False}},
            {'name': 'luminophore-window-glow-demo-no-blur', 'match': {'namespace': '^luminophore-window-glow-demo(-top)?$'}, 'effects': {'blur': False}},
        ],
        'workspace_rules': [{'workspace': 'name:luminophore-base-' + output, 'monitor': output, 'persistent': True}
                            for output in dict.fromkeys((left, right))],
    }


def greeter_defaults():
    return {'schema_version': 1,
            'compositor': {'background_color': 'ff0a0d12', 'blur_enabled': True, 'blur_size': 5, 'blur_passes': 1},
            'motion': {'enabled': False},
            'native': {'profile': 'greeter', 'layer_rules': [{'name': 'luminophore-greeter-blur', 'match': {'namespace': '^luminophore-shell-login$'},
                                       'effects': {'blur': True, 'ignore_alpha': 0.08}}]}}


def write_defaults(destination):
    from pathlib import Path
    from .settings_store import FILES
    from .settings_migration import _render
    from .legacy_defaults import LEGACY_DEFAULTS
    import tomllib
    destination = Path(destination)
    desktop = tomllib.loads(Path(__file__).with_name('config.toml').read_text())
    desktop['schema_version'] = 1
    desktop['native'] = native_defaults()
    for key, value in LEGACY_DEFAULTS.items():
        section, field = key.split('.')
        desktop.setdefault(section, {}).setdefault(field, value)
    for profile, settings in (('desktop', desktop), ('greeter', greeter_defaults())):
        root = destination/profile
        root.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            (root/name).write_text(_render(settings if name == 'settings.toml' else {'schema_version': 1}))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    write_defaults(parser.parse_args().output)
