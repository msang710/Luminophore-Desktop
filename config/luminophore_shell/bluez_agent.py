from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .system_controls import PairingDecision, PairingPrompt, PairingPromptBroker, SystemControlError


BLUEZ_SERVICE = "org.bluez"
AGENT_PATH = "/io/github/msang710/LuminophoreShell/BluezAgent"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
DEVICE_IFACE = "org.bluez.Device1"


class PairingRejected(Exception):
    pass


@dataclass
class PendingReply:
    kind: str
    reply: Callable[..., None]
    reject: Callable[[Exception], None]


class BluezAgentCore:
    def __init__(self, prompted: Callable[[PairingPrompt], None]) -> None:
        self.broker = PairingPromptBroker()
        self.prompted = prompted
        self.replies: dict[int, PendingReply] = {}

    def request(
        self,
        address: str,
        name: str,
        kind: str,
        reply: Callable[..., None],
        reject: Callable[[Exception], None],
        passkey: str = "",
    ) -> PairingPrompt:
        prompt = self.broker.begin(address, name, kind, passkey)
        self.replies[prompt.request_id] = PendingReply(kind, reply, reject)
        self.prompted(prompt)
        return prompt

    def resolve(self, request_id: int, decision: PairingDecision) -> None:
        pending = self.replies.pop(request_id, None)
        if pending is None:
            raise SystemControlError("pairing_prompt_expired")
        try:
            accepted = self.broker.respond(request_id, accepted=decision.accepted, pin=decision.pin)
        except Exception:
            pending.reject(PairingRejected("pairing rejected"))
            raise
        if not accepted.accepted:
            pending.reject(PairingRejected("pairing rejected"))
        elif pending.kind == "pin":
            pending.reply(accepted.pin)
        elif pending.kind == "passkey":
            if not accepted.pin.isdigit():
                pending.reject(PairingRejected("invalid passkey"))
            else:
                pending.reply(int(accepted.pin))
        else:
            pending.reply()

    def cancel_all(self) -> None:
        for request_id, pending in tuple(self.replies.items()):
            try:
                self.broker.cancel(request_id)
            except SystemControlError:
                pass
            pending.reject(PairingRejected("pairing canceled"))
        self.replies.clear()


try:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
except ImportError:  # pragma: no cover - exercised only on unsupported hosts
    dbus = None  # type: ignore[assignment]


if dbus is not None:
    class _Rejected(dbus.DBusException):
        _dbus_error_name = "org.bluez.Error.Rejected"


    class BluezAgentObject(dbus.service.Object):
        def __init__(self, bus, core: BluezAgentCore) -> None:
            super().__init__(bus, AGENT_PATH)
            self.bus = bus
            self.core = core

        def _identity(self, device_path: str) -> tuple[str, str]:
            obj = self.bus.get_object(BLUEZ_SERVICE, device_path)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            address = str(props.Get(DEVICE_IFACE, "Address"))
            try:
                name = str(props.Get(DEVICE_IFACE, "Alias"))
            except Exception:
                name = address
            return address, name

        def _request(self, device, kind, reply_handler, error_handler, passkey="") -> None:
            address, name = self._identity(str(device))
            self.core.request(
                address, name, kind, reply_handler,
                lambda _exc: error_handler(_Rejected("Pairing rejected")), passkey,
            )

        @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s", async_callbacks=("reply_handler", "error_handler"))
        def RequestPinCode(self, device, reply_handler, error_handler):
            self._request(device, "pin", reply_handler, error_handler)

        @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u", async_callbacks=("reply_handler", "error_handler"))
        def RequestPasskey(self, device, reply_handler, error_handler):
            self._request(device, "passkey", reply_handler, error_handler)

        @dbus.service.method(AGENT_IFACE, in_signature="ou", out_signature="", async_callbacks=("reply_handler", "error_handler"))
        def RequestConfirmation(self, device, passkey, reply_handler, error_handler):
            self._request(device, "confirm", reply_handler, error_handler, f"{int(passkey):06d}")

        @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="", async_callbacks=("reply_handler", "error_handler"))
        def RequestAuthorization(self, device, reply_handler, error_handler):
            self._request(device, "confirm", reply_handler, error_handler)

        @dbus.service.method(AGENT_IFACE, in_signature="os", out_signature="", async_callbacks=("reply_handler", "error_handler"))
        def AuthorizeService(self, device, _uuid, reply_handler, error_handler):
            self._request(device, "confirm", reply_handler, error_handler)

        @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Cancel(self):
            self.core.cancel_all()

        @dbus.service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Release(self):
            self.core.cancel_all()


class BluezAgent:
    def __init__(self, prompted: Callable[[PairingPrompt], None]) -> None:
        self.core = BluezAgentCore(prompted)
        self.bus = None
        self.object = None
        self.manager = None

    def start(self) -> None:
        if dbus is None:
            raise SystemControlError("python_dbus_unavailable")
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.object = BluezAgentObject(self.bus, self.core)
        manager_object = self.bus.get_object(BLUEZ_SERVICE, "/org/bluez")
        self.manager = dbus.Interface(manager_object, AGENT_MANAGER_IFACE)
        self.manager.RegisterAgent(AGENT_PATH, "KeyboardDisplay")

    def stop(self) -> None:
        self.core.cancel_all()
        if self.manager is not None:
            try:
                self.manager.UnregisterAgent(AGENT_PATH)
            except Exception:
                pass
        if self.object is not None:
            self.object.remove_from_connection()
        self.manager = None
        self.object = None
        self.bus = None

    def pair(self, address: str) -> None:
        if self.bus is None:
            raise SystemControlError("bluez_agent_not_started")
        objects = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE, "/"),
            "org.freedesktop.DBus.ObjectManager",
        ).GetManagedObjects()
        for path, interfaces in objects.items():
            properties = interfaces.get(DEVICE_IFACE, {})
            if str(properties.get("Address", "")).upper() == address.upper():
                dbus.Interface(self.bus.get_object(BLUEZ_SERVICE, path), DEVICE_IFACE).Pair()
                return
        raise SystemControlError("bluetooth_device_not_found")
