import json
import tempfile
import unittest
from pathlib import Path

from dashboard_app import parse_run_config, target_snapshot


class DashboardTests(unittest.TestCase):
    def test_snapshot_combines_targets_with_latest_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = root / "people.json"
            history = root / "history.jsonl"
            targets.write_text(
                json.dumps({"accounts": ["one", "two", "three", "four"]}),
                encoding="utf-8",
            )
            entries = [
                {"username": "one", "status": "failed"},
                {"username": "one", "status": "success"},
                {"username": "two", "status": "not_following"},
                {"username": "three", "status": "stopped"},
            ]
            history.write_text(
                "\n".join(json.dumps(entry) for entry in entries) + "\n",
                encoding="utf-8",
            )

            snapshot = target_snapshot(targets, history)
            self.assertEqual(snapshot["total"], 4)
            self.assertEqual(snapshot["completed"], 2)
            self.assertEqual(snapshot["remaining"], 2)
            self.assertEqual(snapshot["unfollowed"], 1)
            self.assertEqual(snapshot["skipped"], 1)
            self.assertEqual(snapshot["errors"], 1)

    def test_run_config_has_guarded_ranges(self):
        config = parse_run_config(
            {
                "mode": "all",
                "batch_size": 10,
                "delay_min": 30,
                "delay_max": 60,
                "cooldown_minutes": 30,
            }
        )
        self.assertEqual(config["mode"], "all")
        with self.assertRaises(ValueError):
            parse_run_config({"mode": "all", "batch_size": 100})
        with self.assertRaises(ValueError):
            parse_run_config({"mode": "all", "delay_min": 60, "delay_max": 30})


if __name__ == "__main__":
    unittest.main()
