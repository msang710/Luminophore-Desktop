"""Transport loss and ordering tests against a CAS compositor simulator."""
import ast
import copy
import json
import logging
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from luminophore_shell.config import ConfigError, config_digest, config_mtime_ns, load_config, load_config_text
from luminophore_shell.hyprland import HyprlandClient, HyprlandError, HyprlandEventListener
from tests.legacy_settings_writer import ConfigSettingsWriter
from luminophore_shell.settings_contract import SettingsApplyRequest, SettingsCompletionUnknown, SettingsResultCategory
from luminophore_shell.settings_coordinator import SettingsApplyCoordinator, SettingsMutationGate
from luminophore_shell.settings_controller import SettingsController, SettingsRuntimeApplyError
from luminophore_shell.settings_schema import settings_values
from luminophore_shell.visual_settings import VisualSettings, parse_visual_settings
from luminophore_shell.production_settings import relevant as production_settings_relevant


class Renderer:
    def __init__(self):
        self.state = dict(schema=1, rendererSchema=1, receiptSchema=1, source="a" * 32,
                          serial="1", token="", revision="1", configured=True, recovery="none",
                          settings=dict(schemaVersion=1, preset="balanced", enabled=True, breathing=True, intensity=.42))
        self.mode = "normal"
        self.mutations = []
        self.queued = None
        self.before_next = None

    def runner(self, command, **kwargs):
        if command[1] == "luminophorevisualsettings":
            self.mutations.append(command[-1])
            schema, preset, enabled, breathing, intensity, source, token, serial = command[-1].split()
            desired = dict(schemaVersion=int(schema), preset=preset,
                           enabled=enabled == "true", breathing=breathing == "true",
                           intensity=float(intensity))
            def commit():
                if source != self.state["source"] or serial != self.state["serial"]:
                    return False
                if desired != self.state["settings"]:
                    self.state["revision"] = str(int(self.state["revision"]) + 1)
                self.state.update(serial=str(int(serial) + 1), token=token, settings=desired)
                return True
            mode, self.mode = self.mode, "normal"
            if mode == "queued":
                self.queued = commit
                raise subprocess.TimeoutExpired(command, 1)
            if self.before_next:
                action, self.before_next = self.before_next, None
                action()
            if not commit():
                return subprocess.CompletedProcess(command, 1, "", "stale receipt")
            if mode == "lost_ack":
                raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.state), "")


class VisualReceiptTests(unittest.TestCase):
    def setUp(self):
        self.renderer = Renderer()
        self.client = HyprlandClient(self.renderer.runner, hyprctl="simulated")
        self.desired = VisualSettings(enabled=False)

    def uncertain(self, mode):
        self.renderer.mode = mode
        with self.assertRaises(SettingsCompletionUnknown):
            self.client.apply_visual_settings(self.desired)

    def test_lost_ack_has_exact_receipt_and_read_only_reconciliation(self):
        self.uncertain("lost_ack")
        self.assertEqual(self.client.reconcile_visual_settings(), "complete")
        self.assertEqual(len(self.renderer.mutations), 1)
        self.assertFalse(self.client._visual_uncertain)

    def test_equal_values_without_own_receipt_never_prove_completion(self):
        self.uncertain("queued")
        self.renderer.state["settings"]["enabled"] = False
        self.assertEqual(self.client.reconcile_visual_settings(), "pending")
        with self.assertRaises(SettingsCompletionUnknown):
            self.client.apply_visual_settings(VisualSettings())
        self.assertEqual(len(self.renderer.mutations), 1)

    def test_explicit_fence_keeps_current_effect_and_rejects_late_original(self):
        self.uncertain("queued")
        self.assertEqual(self.client.reconcile_visual_settings(fence=True), "superseded")
        self.assertFalse(self.renderer.queued())
        self.assertTrue(self.renderer.state["settings"]["enabled"])
        self.assertEqual(len(self.renderer.mutations), 2)

    def test_fence_lost_ack_is_resolved_by_readback(self):
        self.uncertain("queued")
        self.renderer.mode = "lost_ack"
        self.assertEqual(self.client.reconcile_visual_settings(fence=True), "superseded")
        self.assertFalse(self.renderer.queued())

    def test_original_winning_race_with_fence_is_confirmed(self):
        self.uncertain("queued")
        self.renderer.before_next = self.renderer.queued
        self.assertEqual(self.client.reconcile_visual_settings(fence=True), "complete")
        self.assertFalse(self.renderer.state["settings"]["enabled"])

    def test_new_compositor_source_retires_previous_request_without_replay(self):
        self.uncertain("queued")
        self.renderer.state["source"] = "b" * 32
        self.assertEqual(self.client.reconcile_visual_settings(), "superseded")
        self.assertFalse(self.renderer.queued())
        self.assertEqual(len(self.renderer.mutations), 1)

    def test_other_writer_receipt_is_not_our_success(self):
        self.uncertain("lost_ack")
        self.renderer.state["token"] = "other"
        self.assertEqual(self.client.reconcile_visual_settings(), "superseded")

    def test_receipt_parser_rejects_lossy_or_inconsistent_identity(self):
        for changes in ({"serial": 2}, {"serial": "-1"}, {"serial": "0"}, {"serial": str(2**64)},
                        {"source": "bad"}, {"token": '"injected'}, {"receiptSchema": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_visual_settings({**self.renderer.state, **changes})
