"""Shell participant. The compositor alone orders the joint transaction."""
from .settings_generation import generation_config


class ShellSettingsParticipant:
    def __init__(self, store, initial, apply, verify):
        self.store, self.confirmed = store, initial
        self.apply, self.verify = apply, verify
        self.sequence = 0
        self.identity = None
        self.candidate = None
        self.receipts = {}

    def execute(self, fields):
        version, epoch, sequence, ticket, base, candidate, target, operation = fields
        identity = (epoch, sequence, base, candidate)
        if target != 'shell' or version != '1' or not sequence.isdecimal():
            raise ValueError('invalid Shell command')
        if self.identity != identity:
            if operation != 'prepare' or int(sequence) <= self.sequence or base != self.confirmed.id:
                raise ValueError('stale Shell transaction')
            self.identity, self.sequence = identity, int(sequence)
            self.candidate = None
            self.receipts = {}
        # A duplicate mutation observes the original outcome; it never writes twice.
        receipt_key = (operation, ticket) if operation == 'restore' else operation
        if receipt_key in self.receipts and operation not in ('verify', 'confirm'):
            return self.receipts[receipt_key]
        try:
            if operation == 'prepare':
                self.candidate = self.store.load(candidate)
                generation_config(self.store, self.candidate)
            elif operation == 'apply':
                if self.candidate is None: raise ValueError('not prepared')
                self.apply(generation_config(self.store, self.candidate))
            elif operation == 'verify':
                if self.candidate is None: raise ValueError('not prepared')
                self.verify(generation_config(self.store, self.candidate))
            elif operation == 'restore':
                config = generation_config(self.store, self.confirmed)
                self.apply(config); self.verify(config)
            elif operation == 'confirm':
                if self.candidate is None: raise ValueError('not prepared')
                self.verify(generation_config(self.store, self.candidate))
                self.confirmed = self.candidate
            else: raise ValueError('invalid Shell operation')
            result = 'ok'
        except Exception:
            result = 'failed'
        self.receipts[receipt_key] = result
        return result
