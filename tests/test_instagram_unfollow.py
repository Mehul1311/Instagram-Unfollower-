import json
import tempfile
import unittest
from pathlib import Path

from instagram_unfollow import extract_usernames, load_completed, load_targets


class UsernameParsingTests(unittest.TestCase):
    def test_mixed_simple_format_is_normalized_and_deduplicated(self):
        payload = [
            "@Alice",
            "alice",
            {"username": "Bob.Example"},
            "https://www.instagram.com/charlie_3/",
            "not a username",
        ]
        self.assertEqual(
            extract_usernames(payload),
            ["alice", "bob.example", "charlie_3"],
        )

    def test_instagram_export_shape(self):
        payload = {
            "relationships_following": [
                {
                    "title": "ignored_when_string_data_exists",
                    "string_list_data": [
                        {
                            "href": "https://www.instagram.com/exported_user/",
                            "value": "exported_user",
                            "timestamp": 1,
                        }
                    ],
                }
            ]
        }
        self.assertEqual(extract_usernames(payload), ["exported_user"])

    def test_load_targets_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people.json"
            path.write_text('\ufeff{"people": ["one", "two"]}', encoding="utf-8")
            self.assertEqual(load_targets(path), ["one", "two"])

    def test_history_only_skips_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            entries = [
                {"username": "Done_User", "status": "success"},
                {"username": "already_gone", "status": "not_following"},
                {"username": "try_again", "status": "failed"},
            ]
            path.write_text(
                "\n".join(json.dumps(entry) for entry in entries) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(load_completed(path), {"done_user", "already_gone"})


if __name__ == "__main__":
    unittest.main()
