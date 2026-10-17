import json
import unittest
from unittest import mock

from factory import pilot


STATE = {
    "robot": "ROVER-1",
    "goal": "S02 valve",
    "bearing": 12.4,
    "distance": 6.75,
    "rays": {"-90": 2.0, "-45": 3.5, "0": 4.2, "45": 1.8, "90": 5.0},
    "last": ["left", "forward"],
}


class PilotTests(unittest.TestCase):
    def test_mocked_crusoe_response_is_bounded_and_parsed(self):
        reply = json.dumps({"commands": ["forward", "left", "stop", "teleport"], "reason": "turn toward goal"})
        with mock.patch.object(pilot, "available", return_value=True), mock.patch.object(
            pilot, "chat", return_value=(reply, {"ok": True})
        ) as chat:
            result = pilot.pilot(STATE)

        self.assertEqual(result["commands"], ["forward", "left", "stop"])
        self.assertEqual(result["reason"], "turn toward goal")
        self.assertEqual(result["ok"], True)
        self.assertEqual(chat.call_args.kwargs["model"], pilot.PILOT_MODEL)
        self.assertEqual(chat.call_args.kwargs["json_schema"], pilot.SCHEMA)

    def test_bad_state_returns_without_calling_crusoe(self):
        with mock.patch.object(pilot, "available", return_value=True), mock.patch.object(
            pilot, "chat", side_effect=AssertionError("network")
        ):
            result = pilot.pilot({"robot": "ROVER-1", "bearing": "unknown"})

        self.assertEqual(result, {"ok": False, "error": "bad state"})


if __name__ == "__main__":
    unittest.main()
