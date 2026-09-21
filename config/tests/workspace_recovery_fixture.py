"""Real clients in a private compositor, including a fixed-size floating window."""
import json
import subprocess
import sys


def verify_workspace_recovery(ctl, wait, env, test, processes):
    primary = json.loads(ctl('-j', 'monitors'))[0]['name']
    existing = {m['name'] for m in json.loads(ctl('-j', 'monitors'))}
    assert ctl('output', 'create', 'wayland').strip() == 'ok'
    second = wait(lambda: next((m for m in json.loads(ctl('-j', 'monitors')) if m['name'] not in existing), None))
    ctl('dispatch', 'focusmonitor', second['name'])
    program = test/'recovery-clients.py'
    program.write_text('''import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
app=Gtk.Application(application_id='org.luminophore.RecoveryFixture')
def activate(app):
    for title, fixed in [('Recovery tiled',False),('Recovery floating',True)]:
        win=Gtk.ApplicationWindow(application=app,title=title)
        win.set_default_size(320,240)
        win.set_resizable(not fixed)
        win.set_child(Gtk.Label(label=title))
        win.present()
app.connect('activate',activate)
app.run(None)
''')
    client = subprocess.Popen([sys.executable, str(program)], env=env, start_new_session=True,
                              stdout=(test/'recovery-clients.log').open('w'), stderr=subprocess.STDOUT)
    processes.append(client)
    def clients():
        return [w for w in json.loads(ctl('-j','clients')) if w['title'].startswith('Recovery ')]
    before = wait(lambda: (ws if len(ws:=clients()) == 2 and all(w['monitor']==second['id'] for w in ws) else None))
    assert all('grouped' not in w for w in before), before
    for command in ('togglegroup', 'changegroupactive', 'moveintogroup', 'moveoutofgroup',
                    'lockgroups', 'lockactivegroup', 'setignoregrouplock'):
        assert ctl('dispatch', command).strip() != 'ok', command
    for option in ('group:auto_group', 'group:groupbar:enabled', 'general:col.nogroup_border',
                   'binds:ignore_group_lock', 'binds:movefocus_cycles_groupfirst'):
        assert 'no such' in ctl('getoption', option).lower(), option
    floating = next(w for w in before if w['title']=='Recovery floating')
    assert floating['floating'], before
    size = floating['size']
    assert ctl('output','remove',second['name']).strip() == 'ok'
    remaining = wait(lambda: next((m for m in json.loads(ctl('-j','monitors')) if m['name']==primary), None))
    after = wait(lambda: (ws if len(ws:=clients()) == 2 and all(w['monitor']==remaining['id'] and w['workspace']['name']=='luminophore-base-'+primary for w in ws) else None))
    assert next(w for w in after if w['title']=='Recovery floating')['size']==size, (before,after)
    assert ctl('output','create','wayland').strip() == 'ok'
    wait(lambda: len(json.loads(ctl('-j','monitors'))) == 2)
    for monitor in json.loads(ctl('-j','monitors')):
        assert monitor['activeWorkspace']['name']=='luminophore-base-'+monitor['name'], monitor
    assert all(w['workspace']['name']=='luminophore-base-'+primary for w in clients())
    print('workspace recovery: tiled + floating rehomed, floating size preserved, reconnect bases valid',flush=True)
    # Later fixture rounds manage compositor and shell as their final two processes.
    processes.remove(client)
    client.terminate();client.wait(timeout=5)
    extra=next(m for m in json.loads(ctl('-j','monitors')) if m['name']!=primary)
    ctl('output','remove',extra['name'])
