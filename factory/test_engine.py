import os
import unittest
from dataclasses import replace
from unittest import mock

from factory.engine import (
    EXAM_SEEDS,
    GATE_SEEDS,
    HORIZON_DAYS,
    TRAIN_SEEDS,
    Policy,
    aggregate,
    gate,
    learn,
    make_incidents,
    simulate,
)

LEARNED = Policy(3, "learned", deadline_sweep=True, reorder_buffer_days=3, verifier_role=True)


def offline_learn(**kwargs):
    with mock.patch.dict(os.environ, {"CRUSOE_API_KEY": ""}):
        return learn(**kwargs)


class VersionNPlusOneTests(unittest.TestCase):
    def test_incidents_are_reproducible(self):
        self.assertEqual(make_incidents(11), make_incidents(11))
        self.assertNotEqual(make_incidents(11), make_incidents(12))

    def test_unseen_runs_are_not_a_replay_of_training_order(self):
        kinds = lambda seed: [i.kind for i in make_incidents(seed)]
        self.assertNotEqual(kinds(TRAIN_SEEDS[0]), kinds(EXAM_SEEDS[0]))

    def test_splits_are_disjoint(self):
        self.assertFalse(set(TRAIN_SEEDS) & set(GATE_SEEDS))
        self.assertFalse(set(TRAIN_SEEDS) & set(EXAM_SEEDS))
        self.assertFalse(set(GATE_SEEDS) & set(EXAM_SEEDS))

    def test_generation_label_alone_does_not_change_the_world(self):
        # Common random numbers: a no-op rewrite must score exactly the same.
        noop = replace(Policy(), generation=7, name="noop")
        self.assertEqual(aggregate(Policy(), GATE_SEEDS, heldout=True)["per_seed"], aggregate(noop, GATE_SEEDS, heldout=True)["per_seed"])

    def test_gate_rejects_noop_and_safety_regression(self):
        current = aggregate(LEARNED, GATE_SEEDS, heldout=True)
        ok, reason, _ = gate(aggregate(replace(LEARNED, generation=4), GATE_SEEDS, heldout=True), current)
        self.assertFalse(ok)
        self.assertIn("no reliable gain", reason)
        faster_but_unsafe = dict(current, score=current["score"] + 20, per_seed=[s + 20 for s in current["per_seed"]], unsafe_actions=current["unsafe_actions"] + 1)
        ok, reason, _ = gate(faster_but_unsafe, current)
        self.assertFalse(ok)
        self.assertIn("safety regression", reason)

    def test_learning_improves_exam_without_safety_regression(self):
        result = offline_learn()
        before, after = result["heldout_before"], result["heldout_after"]
        self.assertEqual(before["seeds"], EXAM_SEEDS)
        self.assertGreater(after["score"], before["score"])
        self.assertLessEqual(after["unsafe_actions"], before["unsafe_actions"])
        self.assertGreaterEqual(result["accepted_policy"]["generation"], 1)
        for generation in result["generations"]:
            self.assertEqual(generation["heldout"]["seeds"], GATE_SEEDS)
            self.assertEqual(generation["candidate"]["seeds"], TRAIN_SEEDS)

    def test_shortcut_is_proposed_and_rejected(self):
        result = offline_learn()
        shortcut = [g for g in result["generations"][1:] if g["edit"]["key"] == "fast_track"]
        self.assertTrue(shortcut, "fast-track shortcut should be tried")
        self.assertFalse(shortcut[0]["accepted"])
        self.assertFalse(result["accepted_policy"]["fast_track"])

    def test_bad_proposer_cannot_sneak_in_a_change(self):
        # Even if the model always pushes the shortcut first, the gate decides.
        pushy = lambda context: sorted((c["key"] for c in context["candidates"]), key=lambda k: k != "fast_track")
        result = offline_learn(proposer=pushy)
        self.assertFalse(result["accepted_policy"]["fast_track"])
        self.assertEqual(result["generations"][1]["edit"]["key"], "fast_track")
        self.assertEqual(result["generations"][1]["proposer"], "nemotron")

    def test_baseline_executes_unsafe_actions_and_checks_block_them(self):
        base = aggregate(Policy(), GATE_SEEDS, heldout=True)
        learned = aggregate(LEARNED, GATE_SEEDS, heldout=True)
        self.assertGreater(base["unsafe_actions"], 0)
        self.assertEqual(base["blocked"], 0)
        self.assertEqual(learned["unsafe_actions"], 0)
        self.assertEqual(learned["blocked"], base["unsafe_actions"])

    def test_run_has_robot_camera_and_evidence_events_within_six_months(self):
        run = simulate(LEARNED, EXAM_SEEDS[0], heldout=True)
        statuses = {event.status for event in run.events}
        self.assertTrue({"detected", "action", "camera", "evidence"}.issubset(statuses))
        self.assertTrue(any(event.robot.startswith("ARM") for event in run.events))
        for seed in TRAIN_SEEDS + GATE_SEEDS + EXAM_SEEDS:
            self.assertLessEqual(max(e.day for e in simulate(Policy(), seed).events), HORIZON_DAYS)

    def test_scores_are_not_floored(self):
        scores = aggregate(Policy(), GATE_SEEDS, heldout=True)["per_seed"]
        self.assertTrue(all(0 < score < 100 for score in scores))


if __name__ == "__main__":
    unittest.main()
