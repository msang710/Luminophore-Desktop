from __future__ import annotations

import subprocess
import unittest

from luminophore_shell.privacy import PrivacyProvider, parse_pw_dump


def node(node_id: int, state: str, **props: str) -> dict[str, object]:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {"state": state, "props": props},
    }


class PrivacyProviderTests(unittest.TestCase):
    def test_only_running_capture_streams_are_reported(self) -> None:
        snapshot = parse_pw_dump([
            node(1, "running", **{
                "media.class": "Stream/Input/Audio", "media.type": "Audio",
                "media.category": "Capture", "application.name": "Browser",
            }),
            node(2, "running", **{
                "media.class": "Stream/Input/Video", "media.type": "Video",
                "media.category": "Capture", "media.role": "Camera", "application.name": "Meet",
            }),
            node(3, "running", **{
                "media.class": "Stream/Output/Video", "media.type": "Video",
                "media.category": "Playback", "media.role": "Screen", "pipewire.access.portal.app_id": "Discord",
            }),
            node(4, "running", **{"media.class": "Audio/Source", "media.type": "Audio", "media.category": "Capture"}),
            node(5, "suspended", **{
                "media.class": "Stream/Input/Audio", "media.type": "Audio", "media.category": "Capture",
            }),
        ])
        self.assertEqual(snapshot.active_kinds, {"microphone", "camera", "screen"})
        self.assertEqual([item.application for item in snapshot.sessions], ["Browser", "Meet", "Discord"])

    def test_ambiguous_video_stream_is_not_called_screen_share(self) -> None:
        snapshot = parse_pw_dump([node(1, "running", **{
            "media.class": "Stream/Input/Video", "media.type": "Video", "media.category": "Capture",
        })])
        self.assertEqual(snapshot.sessions, ())

    def test_backend_failure_is_isolated(self) -> None:
        provider = PrivacyProvider(
            lambda _snapshot: None,
            runner=lambda argv: subprocess.CompletedProcess(argv, 1, "", "unavailable"),
        )
        snapshot = provider.collect()
        self.assertFalse(snapshot.available)
        self.assertEqual(snapshot.error, "backend_failed:pw-dump")


if __name__ == "__main__":
    unittest.main()
