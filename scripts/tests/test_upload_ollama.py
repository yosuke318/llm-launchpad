"""upload_ollama.py のテスト。AWS と GitHub には接続しない

    python3 -B -m unittest discover -s scripts/tests
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import upload_ollama as uo  # noqa: E402


class InstallKeysTest(unittest.TestCase):
    def test_version_only(self):
        self.assertEqual(uo.install_keys("0.40.13", also_latest=False), ["ollama/install/0.40.13/ollama-linux-amd64.tar.zst"])

    def test_also_latest(self):
        self.assertEqual(
            uo.install_keys("0.40.13", also_latest=True),
            ["ollama/install/0.40.13/ollama-linux-amd64.tar.zst", "ollama/install/latest/ollama-linux-amd64.tar.zst"],
        )


class MainTest(unittest.TestCase):
    def run_main(self, argv):
        calls = []

        def fake_aws(*args, region=None, profile=None):
            calls.append(args)
            return "123456789012\n" if args[0] == "sts" else ""

        with mock.patch.object(uo, "aws", fake_aws), \
                mock.patch.object(uo, "download") as download, \
                mock.patch.object(uo, "latest_version", return_value="0.40.13"), \
                mock.patch("builtins.print"):
            code = uo.main(argv)
        return code, calls, download

    def test_latest_goes_to_default_bucket_and_latest_key(self):
        code, calls, download = self.run_main([])
        self.assertEqual(code, 0)
        self.assertEqual(download.call_args.args[0], "0.40.13")
        bucket = "s3://llm-launchpad-models-123456789012-ap-northeast-1"
        copies = [(c[2], c[3]) for c in calls if c[0] == "s3"]
        self.assertEqual(copies[0][1], f"{bucket}/ollama/install/0.40.13/ollama-linux-amd64.tar.zst")
        self.assertEqual(
            copies[1],
            (f"{bucket}/ollama/install/0.40.13/ollama-linux-amd64.tar.zst", f"{bucket}/ollama/install/latest/ollama-linux-amd64.tar.zst"),
            "latest の分はアップロードし直さず、S3 の中でコピーする",
        )

    def test_given_version_does_not_touch_latest(self):
        code, calls, _ = self.run_main(["--version", "0.39.0", "--bucket", "my-bucket"])
        self.assertEqual(code, 0)
        self.assertEqual([c[3] for c in calls], ["s3://my-bucket/ollama/install/0.39.0/ollama-linux-amd64.tar.zst"])


if __name__ == "__main__":
    unittest.main()
