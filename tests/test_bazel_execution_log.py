from __future__ import annotations

import json
import unittest

from tools.bazel.assert_execution_log import (
    ExecutionLogError,
    iter_records,
    require_remote_action,
)


TARGET = "//src/ptoon:ptoon"
MNEMONIC = "ChapelCompile"
PLATFORM = ("gf.platform", "gloriousflywheel-rbe-linux-x86_64")


def valid_record() -> dict[str, object]:
    return {
        "targetLabel": TARGET,
        "mnemonic": MNEMONIC,
        "runner": "remote",
        "cacheHit": False,
        "status": "",
        "exitCode": 0,
        "remotable": True,
        "cacheable": False,
        "remoteCacheable": False,
        "platform": {
            "properties": [{"name": PLATFORM[0], "value": PLATFORM[1]}]
        },
    }


class ExecutionLogTest(unittest.TestCase):
    def assert_rejected(self, record: dict[str, object]) -> None:
        with self.assertRaises(ExecutionLogError):
            require_remote_action(
                [record],
                target=TARGET,
                mnemonic=MNEMONIC,
                platform_property=PLATFORM,
            )

    def test_accepts_one_uncached_remote_action(self) -> None:
        record = valid_record()
        self.assertIs(
            require_remote_action(
                [record],
                target=TARGET,
                mnemonic=MNEMONIC,
                platform_property=PLATFORM,
            ),
            record,
        )

    def test_parses_newline_delimited_and_concatenated_objects(self) -> None:
        first = {"mnemonic": "Other"}
        second = valid_record()
        payload = json.dumps(first) + "\n" + json.dumps(second)
        self.assertEqual(list(iter_records(payload)), [first, second])

    def test_rejects_remote_cache_hit(self) -> None:
        record = valid_record()
        record["runner"] = "remote cache hit"
        record["cacheHit"] = True
        self.assert_rejected(record)

    def test_rejects_local_runner(self) -> None:
        record = valid_record()
        record["runner"] = "linux-sandbox"
        self.assert_rejected(record)

    def test_rejects_cacheable_environmental_action(self) -> None:
        record = valid_record()
        record["cacheable"] = True
        record["remoteCacheable"] = True
        self.assert_rejected(record)

    def test_rejects_wrong_platform(self) -> None:
        record = valid_record()
        record["platform"] = {"properties": []}
        self.assert_rejected(record)

    def test_rejects_missing_or_duplicate_match(self) -> None:
        for records in ([], [valid_record(), valid_record()]):
            with self.assertRaises(ExecutionLogError):
                require_remote_action(
                    records,
                    target=TARGET,
                    mnemonic=MNEMONIC,
                    platform_property=PLATFORM,
                )

    def test_rejects_malformed_json(self) -> None:
        with self.assertRaises(ExecutionLogError):
            list(iter_records('{"mnemonic":'))


if __name__ == "__main__":
    unittest.main()
