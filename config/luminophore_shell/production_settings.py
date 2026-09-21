"""Receipt-checked production Lua consumer transactions; never model-only readback."""
import json
from uuid import uuid4
from .lua_settings import owned_values
from .config import _toml_literal
from .settings_contract import SettingsCompletionUnknown
from .hyprland import HyprlandError

PREFIXES = ('compositor.', 'input.', 'touchpad.', 'tablet.', 'tablettool.', 'touchdevice.', 'virtualkeyboard.', 'motion.')


def relevant(changes):
    return any(key == 'devices' or key.startswith(PREFIXES) for key in changes)


def document(before, after):
    lines=[]
    for name, config in [('before', before), ('after', after)]:
        lines.append('['+name+']')
        lines.extend(json.dumps(key)+' = '+_toml_literal(value) for key,value in sorted(owned_values(config).items()))
        for device, fields in sorted(config.devices.items()):
            lines.append('['+name+'_devices.'+json.dumps(device)+']')
            lines.extend(json.dumps(key)+' = '+_toml_literal(value) for key,value in sorted(fields.items()))
    return '\n'.join(lines)+'\n'


class ProductionSettingsRuntime:
    def __init__(self, client):
        self.client=client; self.identifier=uuid4().hex; self.base='0'*64; self.candidate='0'*64

    def _call(self, operation, payload=''):
        wire=' '.join(('1', self.identifier, self.base, self.candidate, operation))
        if payload: wire+=' '+payload
        try:
            raw=self.client._run('luminophoreluasettings', wire, timeout=8)
        except HyprlandError as error:
            raise SettingsCompletionUnknown('production settings receipt unavailable') from error
        try: reply=json.loads(raw)
        except (ValueError,TypeError) as error:
            raise SettingsCompletionUnknown('invalid production settings receipt') from error
        if reply.get('version') != 1 or reply.get('status') != 'ok':
            raise RuntimeError('production settings '+operation+' rejected: '+str(reply.get('error','invalid receipt')))
        if (reply.get('request_id'), reply.get('base'), reply.get('candidate')) != (self.identifier, self.base, self.candidate):
            raise SettingsCompletionUnknown('production settings receipt identity mismatch')
        return reply

    def prepare(self, before, after, base, candidate):
        self.base=base; self.candidate=candidate
        self._call('prepare',document(before,after).encode().hex())

    def apply(self): self._call('apply')
    def verify(self): self._call('verify')
    def restore(self): self._call('restore')
    def confirm(self): self._call('confirm')

    def reconcile(self):
        phase = self._call('status').get('phase')
        if phase == 'prepared':
            # An identity-checked prepare receipt proves apply has not begun.
            self.apply()
        elif phase == 'restored':
            return 'superseded'
        elif phase not in ('applied', 'verified', 'confirmed'):
            return 'pending'
        self.verify()
        self.confirm()
        return 'complete' 
