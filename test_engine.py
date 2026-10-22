import json
import unittest
from unittest.mock import patch

import engine

SOURCE = "local x = 10" + chr(10) + "print('hello ' .. x)" + chr(10)


class EngineTests(unittest.TestCase):
    def test_output_is_obfuscated_and_truthfully_labeled(self):
        with patch("engine._validate_with_compiler", return_value=("not_configured", "not run")):
            result = engine.build_artifact(SOURCE.encode("utf-8"), "hello.lua", "luau")
        self.assertNotEqual(result.output_bytes, SOURCE.encode("utf-8"))
        self.assertGreater(len(result.output_bytes), len(SOURCE))
        self.assertEqual(result.status, "obfuscated")
        self.assertIn(result.mode, ("vm", "source"))
        manifest = json.loads(result.manifest_bytes)
        self.assertEqual(manifest["status"], "obfuscated")
        self.assertNotEqual(manifest["input_sha256"], manifest["output_sha256"])
        self.assertEqual(manifest["output_sha256"], engine.sha256(result.output_bytes))
        self.assertEqual(manifest["integrity"], "passed")

    def test_output_is_deterministic_with_a_seed(self):
        first = engine.build_artifact(SOURCE.encode("utf-8"), "a.lua", "luau", seed=42)
        second = engine.build_artifact(SOURCE.encode("utf-8"), "a.lua", "luau", seed=42)
        self.assertEqual(first.output_bytes, second.output_bytes)
        self.assertEqual(first.mode, second.mode)

    def test_accepts_txt_files(self):
        result = engine.build_artifact(SOURCE.encode("utf-8"), "script.txt", "luau")
        self.assertEqual(result.status, "obfuscated")

    def test_rejects_bad_extension(self):
        with self.assertRaises(engine.BuildError):
            engine.build_artifact(SOURCE.encode("utf-8"), "file.py", "luau")

    def test_rejects_empty_source(self):
        with self.assertRaises(engine.BuildError):
            engine.build_artifact(b"  \n", "file.lua", "luau")

    def test_rejects_unknown_target(self):
        with self.assertRaises(engine.BuildError):
            engine.build_artifact(SOURCE.encode("utf-8"), "file.lua", "python")

    def test_hash_is_sha256(self):
        self.assertEqual(
            engine.sha256(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


if __name__ == "__main__":
    unittest.main()
