from dataclasses import replace
import json
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from luminophore_shell.hyprland import HyprlandClient, HyprlandError
from luminophore_shell.visual_settings import VisualSettings, parse_visual_settings
from luminophore_shell.settings_contract import SettingsCompletionUnknown


def response(**changes):
    return {"schema": 1, "revision": "9007199254740993", "configured": True, "recovery": "none",
            "settings": {"schemaVersion": 1, "preset": "balanced", "enabled": True, "breathing": True, "intensity": 0.42}, **changes}


class VisualSettingsTests(unittest.TestCase):
    def setUp(self):
        mocked = patch("luminophore_shell.hyprland.uuid4", return_value=SimpleNamespace(hex="test-token"))
        mocked.start()
        self.addCleanup(mocked.stop)

    def receipt_response(self, **changes):
        return response(rendererSchema=1, receiptSchema=1, source="a" * 32,
                        serial="9007199254740994", token="test-token", **changes)

    def client(self, reply):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, reply, "")
        return HyprlandClient(runner, hyprctl="test-hyprctl"), calls

    def test_apply_sends_only_semantic_fields_once_and_preserves_exact_revision(self):
        client, calls = self.client(json.dumps(response()))
        result = client.visual_settings(VisualSettings())
        self.assertEqual(result.revision, 9007199254740993)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["test-hyprctl", "luminophorevisualsettings", '1 balanced true true 0.42'])
        self.assertEqual(result.settings, VisualSettings())

    def verified_client(self, replies):
        calls = []
        queue = iter(replies)
        def runner(command, **kwargs):
            calls.append(command)
            reply = next(queue)
            if isinstance(reply, Exception):
                raise reply
            return subprocess.CompletedProcess(command, 0, json.dumps(reply), "")
        return HyprlandClient(runner, hyprctl="test-hyprctl"), calls

    def test_verified_apply_checks_renderer_then_mutates_once_and_reads_back(self):
        state = self.receipt_response()
        client, calls = self.verified_client([{**state, "serial": "9007199254740993", "token": ""}, state, state])
        self.assertEqual(client.apply_visual_settings(VisualSettings()).renderer_schema, 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual("luminophorevisualstate", calls[0][-1])
        self.assertEqual("luminophorevisualsettings", calls[1][1])
        self.assertEqual("luminophorevisualstate", calls[2][-1])

    def test_old_store_only_generation_is_rejected_before_mutation(self):
        client, calls = self.verified_client([response()])
        with self.assertRaises(HyprlandError):
            client.apply_visual_settings(VisualSettings())
        self.assertEqual(len(calls), 1)

    def test_dispatched_failure_and_changed_readback_are_completion_unknown(self):
        state = self.receipt_response()
        timeout = subprocess.TimeoutExpired("test-hyprctl", 1)
        before = {**state, "serial": "9007199254740993", "token": ""}
        for replies in ([before, timeout], [before, state, timeout],
                        [before, state, self.receipt_response(revision="99")],
                        [before, response(rendererSchema=0)]):
            client, calls = self.verified_client(replies)
            with self.subTest(replies=replies), self.assertRaises(SettingsCompletionUnknown):
                client.apply_visual_settings(VisualSettings())
            self.assertEqual(sum("luminophorevisualsettings" in command for command in calls), 1)

    def test_read_state_uses_nonmutating_dispatcher_and_accepts_unconfigured_defaults(self):
        client, calls = self.client(json.dumps(response(revision="0", configured=False)))
        self.assertFalse(client.visual_state().configured)
        self.assertEqual(calls[0][-1], "luminophorevisualstate")
        with self.assertRaises(HyprlandError):
            client.visual_settings(VisualSettings())

    def test_invalid_request_cannot_reach_transport(self):
        client, calls = self.client(json.dumps(response()))
        for change in ({"schema_version": True}, {"enabled": 1}, {"breathing": "yes"}, {"intensity": True},
                       {"intensity": float("nan")}, {"intensity": float("inf")}, {"preset": '";bad'}, {"preset": ""}):
            with self.subTest(change=change), self.assertRaises(HyprlandError):
                client.visual_settings(replace(VisualSettings(), **change))
        self.assertEqual(calls, [])

    def test_recovery_response_requires_complete_default_and_remains_explicit(self):
        data = response(recovery="schema")
        client, calls = self.client(json.dumps(data))
        result = client.visual_settings(VisualSettings(schema_version=0, enabled=False))
        self.assertEqual(result.recovery, "schema")
        self.assertEqual(result.settings, VisualSettings())
        self.assertEqual(len(calls), 1)
        data["settings"]["enabled"] = False
        with self.assertRaises(ValueError):
            parse_visual_settings(data)

    def test_malformed_or_ambiguous_ack_is_not_retried(self):
        for reply in ("nil", "ok", "{}", "[]", json.dumps(response(revision=3)), json.dumps(response(configured=False))):
            client, calls = self.client(reply)
            with self.subTest(reply=reply), self.assertRaises(HyprlandError):
                client.visual_settings(VisualSettings())
            self.assertEqual(len(calls), 1)

    def test_normalized_readback_rejects_invalid_fields(self):
        for change in ({"schemaVersion": True}, {"schemaVersion": 0}, {"preset": "other"}, {"enabled": 1},
                       {"breathing": None}, {"intensity": -1}, {"intensity": 4}, {"intensity": float("nan")}):
            data = response()
            data["settings"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_visual_settings(data)
        for change in ({"schema": True}, {"recovery": []}, {"revision": "-1"}, {"revision": str(2**64)}):
            with self.assertRaises(ValueError):
                parse_visual_settings(response(**change))
