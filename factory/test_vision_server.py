import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import server, vision


IMAGE = "data:image/png;base64,AAAA"


class VisionValidationTests(unittest.TestCase):
    def test_rejects_bad_camera_and_non_image_without_network(self):
        with mock.patch.object(vision, "available", side_effect=AssertionError("network")):
            self.assertEqual(vision.detect("bad camera!", "S01", IMAGE)["error"], "bad request")
            self.assertEqual(vision.verify("CAM-01", "S01", "finding", "not-an-image")["error"], "bad request")

    def test_detect_parses_and_clamps_mocked_crusoe_response(self):
        reply = json.dumps({"detections": [{"label": "leak", "box": [-5, 10, 1200, 20], "issue": "visible", "severity": "crit"}], "summary": "leak", "route_to_planner": True})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(vision, "CACHE", Path(tmp)), mock.patch.object(vision, "available", return_value=True), mock.patch.object(vision, "chat", return_value=(reply, {"ok": True})):
            result = vision.detect("CAM-01", "S01", IMAGE)
        self.assertTrue(result["ok"])
        self.assertEqual(result["detections"][0]["box"], [0.0, 10.0, 1000.0, 20.0])
        self.assertTrue(result["route_to_planner"])

    def test_verify_uses_mocked_crusoe_response(self):
        reply = json.dumps({"arm_on_site": True, "note": "arm visible"})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(vision, "CACHE", Path(tmp)), mock.patch.object(vision, "available", return_value=True), mock.patch.object(vision, "chat", return_value=(reply, {"ok": True})):
            result = vision.verify("CAM-01", "S01", "inspect pump", IMAGE)
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["arm_on_site"], True)


class ServerPayloadTests(unittest.TestCase):
    def test_offline_payload_and_ablation_are_deterministic_without_network(self):
        with mock.patch.dict(server.os.environ, {"FACTORY_MODE": "offline", "CRUSOE_API_KEY": ""}), mock.patch.object(server, "latest_evidence", return_value=None), mock.patch.object(server, "available", return_value=False):
            payload = server.build_payload()
            payload["ablation"] = server.ablation(payload)
        self.assertEqual(payload["mode"], "offline")
        self.assertEqual(payload["heldout_before"]["score"], 66.5)
        self.assertEqual(payload["heldout_after"]["score"], 92.8)
        self.assertEqual([row["key"] for row in payload["ablation"][:3]], ["verifier_role", "deadline_sweep", "reorder_buffer"])
        self.assertTrue(all("score_drop" in row for row in payload["ablation"][:3]))


if __name__ == "__main__":
    unittest.main()
