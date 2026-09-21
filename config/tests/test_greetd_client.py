from __future__ import annotations

import json
import struct
import unittest

from luminophore_shell.greetd_client import GreetdClient, MAX_FRAME_BYTES, SESSION_ARGV, encode_frame


class MemorySocket:
    def __init__(self, responses: list[bytes]) -> None:
        self.incoming = bytearray().join(responses)
        self.requests: list[dict[str, object]] = []
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, payload: bytes) -> None:
        length = struct.unpack("<I", payload[:4])[0]
        self.requests.append(json.loads(payload[4:4 + length]))

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        payload = bytes(self.incoming[:size])
        del self.incoming[:size]
        return payload

    def close(self) -> None:
        self.closed = True


class SocketFactory:
    def __init__(self, client: MemorySocket) -> None:
        self.client = client

    def __call__(self, _path: str, _timeout: float) -> MemorySocket:
        return self.client


def client_with(responses: list[bytes]) -> tuple[GreetdClient, MemorySocket]:
    memory_socket = MemorySocket(responses)
    return GreetdClient("fake", socket_factory=SocketFactory(memory_socket)), memory_socket


class GreetdClientTests(unittest.TestCase):
    def test_session_command_is_luminophore_only(self) -> None:
        self.assertEqual(SESSION_ARGV, (
            "/usr/bin/uwsm", "start", "-e", "-D", "Luminophore", "luminophore.desktop",
        ))
        self.assertNotIn("Hyprland", SESSION_ARGV)
        self.assertNotIn("hyprland.desktop", SESSION_ARGV)

    def test_secret_authentication_starts_only_fixed_uwsm_session(self) -> None:
        client, memory_socket = client_with([
            encode_frame({
                "type": "auth_message",
                "auth_message_type": "secret",
                "auth_message": "Password:",
            }),
            encode_frame({"type": "success"}),
            encode_frame({"type": "success"}),
        ])

        result = client.authenticate("testuser", "secret")

        self.assertTrue(result.success)
        self.assertEqual(memory_socket.requests[0], {"type": "create_session", "username": "testuser"})
        self.assertEqual(memory_socket.requests[1], {"type": "post_auth_message_response", "response": "secret"})
        self.assertEqual(memory_socket.requests[2], {"type": "start_session", "cmd": list(SESSION_ARGV), "env": []})
        self.assertTrue(memory_socket.closed)

    def test_auth_error_is_cancelled_and_returns_to_idle(self) -> None:
        client, memory_socket = client_with([
            encode_frame({
                "type": "auth_message",
                "auth_message_type": "secret",
                "auth_message": "Password:",
            }),
            encode_frame({
                "type": "error",
                "error_type": "auth_error",
                "description": "authentication failed",
            }),
            encode_frame({"type": "success"}),
        ])

        result = client.authenticate("testuser", "wrong")

        self.assertFalse(result.success)
        self.assertEqual(result.category, "auth_error")
        self.assertEqual(memory_socket.requests[-1], {"type": "cancel_session"})
        self.assertEqual(client.state, "idle")
        self.assertFalse(any(request.get("type") == "start_session" for request in memory_socket.requests))

    def test_oversize_response_fails_closed(self) -> None:
        client, memory_socket = client_with([struct.pack("<I", MAX_FRAME_BYTES + 1)])

        result = client.authenticate("testuser", "secret")

        self.assertFalse(result.success)
        self.assertEqual(result.category, "oversize_frame")
        self.assertEqual(memory_socket.requests, [
            {"type": "create_session", "username": "testuser"},
            {"type": "cancel_session"},
        ])

    def test_informational_messages_are_acknowledged_without_exposing_input(self) -> None:
        client, memory_socket = client_with([
            encode_frame({
                "type": "auth_message",
                "auth_message_type": "info",
                "auth_message": "Informational",
            }),
            encode_frame({
                "type": "auth_message",
                "auth_message_type": "error",
                "auth_message": "Previous attempt failed",
            }),
            encode_frame({
                "type": "auth_message",
                "auth_message_type": "secret",
                "auth_message": "Password:",
            }),
            encode_frame({"type": "success"}),
            encode_frame({"type": "success"}),
        ])

        result = client.authenticate("testuser", "secret")

        self.assertTrue(result.success)
        acknowledgements = memory_socket.requests[1:3]
        self.assertEqual(acknowledgements, [
            {"type": "post_auth_message_response"},
            {"type": "post_auth_message_response"},
        ])

    def test_visible_or_second_secret_prompt_fails_closed(self) -> None:
        for message_type in ("visible", "secret"):
            with self.subTest(message_type=message_type):
                responses = [encode_frame({
                    "type": "auth_message",
                    "auth_message_type": message_type,
                    "auth_message": "Unsupported",
                })]
                if message_type == "secret":
                    responses.append(encode_frame({
                        "type": "auth_message",
                        "auth_message_type": "secret",
                        "auth_message": "Again",
                    }))
                responses.append(encode_frame({"type": "success"}))
                client, memory_socket = client_with(responses)

                result = client.authenticate("testuser", "secret")

                self.assertFalse(result.success)
                self.assertEqual(result.category, "unsupported_prompt")
                self.assertEqual(memory_socket.requests[-1], {"type": "cancel_session"})


if __name__ == "__main__":
    unittest.main()
