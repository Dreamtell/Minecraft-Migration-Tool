"""Scanner cache regression tests; run: python tools/test_scanner_cache.py."""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from queue import Queue
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Minecraft\u8fc1\u79fb\u5de5\u5177"))

from core import scanner


class ScannerCacheTests(unittest.TestCase):
    MTIME_NS = 1_700_000_000_000_000_000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scanner-cache-test-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.src = root / "source"
        self.tgt = root / "target"
        for instance in (self.src, self.tgt):
            (instance / "mods").mkdir(parents=True)
        self.write_jar(self.src, "added.jar", "added", "1.0.0")
        self.write_jar(self.src, "shared.jar", "shared", "2.0.0")
        self.write_jar(self.tgt, "shared.jar", "shared", "1.0.0")
        self.write_jar(self.src, "lower.jar", "lower", "1.0.0", loader="Forge")
        self.write_jar(self.tgt, "lower.jar", "lower", "2.0.0", loader="Forge")
        self.write_jar(self.tgt, "target-only.jar", "target_only", "1.0.0")
        for instance in (self.src, self.tgt):
            self.write_jar(instance, "unchanged.jar", "unchanged", "1.0.0")

    def write_jar(self, instance, name, modid, version, loader="Fabric"):
        path = instance / "mods" / name
        with zipfile.ZipFile(path, "w") as jar:
            if loader == "Fabric":
                jar.writestr("fabric.mod.json", json.dumps({"id": modid, "version": version}))
            else:
                jar.writestr("META-INF/mods.toml",
                             f'[[mods]]\nmodId="{modid}"\nversion="{version}"\n')
        os.utime(path, ns=(self.MTIME_NS, self.MTIME_NS))
        return path

    def scan(self, **kwargs):
        return scanner.scan_mod_differences(self.src, self.tgt, **kwargs)

    def cache_path(self, instance):
        return instance / "mods_meta_cache.json"

    def read_cache(self, instance):
        return json.loads(self.cache_path(instance).read_text(encoding="utf-8"))

    def scan_recording_parses(self, **kwargs):
        with mock.patch.object(scanner, "get_mod_metadata", wraps=scanner.get_mod_metadata) as parse:
            result = self.scan(**kwargs)
        return result, [call.args[0] for call in parse.call_args_list]

    def assert_warm_scan(self, expected):
        result, parsed = self.scan_recording_parses()
        self.assertEqual(parsed, [])
        self.assertEqual(sorted(result), sorted(expected))

    def test_cold_then_warm_preserves_diff_classifications(self):
        cold, parsed = self.scan_recording_parses()
        self.assertEqual(len(parsed), 8)
        self.assertEqual({row[0]: row[1] for row in cold}, {
            "added.jar": "\u65b0\u589e",
            "shared.jar": "\u66f4\u65b0",
            "lower.jar": "\u964d\u7ea7",
            "target-only.jar": "\u76ee\u6807\u72ec\u6709",
        })
        self.assert_warm_scan(cold)
        with mock.patch.object(scanner.zipfile, "ZipFile", side_effect=AssertionError("JAR reopened")):
            self.assertEqual(sorted(self.scan()), sorted(cold))

    def test_source_and_target_with_identical_fingerprints_use_separate_caches(self):
        cold = self.scan()
        src_entry = self.read_cache(self.src)["shared.jar"]
        tgt_entry = self.read_cache(self.tgt)["shared.jar"]
        self.assertEqual(src_entry["fingerprint"], tgt_entry["fingerprint"])
        self.assertEqual(src_entry["version"], "2.0.0")
        self.assertEqual(tgt_entry["version"], "1.0.0")
        self.assert_warm_scan(cold)

    def test_size_change_with_same_mtime_invalidates_only_changed_jar(self):
        self.scan()
        path = self.src / "mods" / "shared.jar"
        before = path.stat()
        with zipfile.ZipFile(path, "a") as jar:
            jar.writestr("extra.bin", b"changed size")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertNotEqual(path.stat().st_size, before.st_size)
        result, parsed = self.scan_recording_parses()
        self.assertEqual(parsed, [path])
        self.assert_warm_scan(result)

    def test_nanosecond_change_with_same_float_mtime_and_size_invalidates(self):
        self.scan()
        path = self.src / "mods" / "shared.jar"
        before = path.stat()
        self.write_jar(self.src, path.name, "shared", "3.0.0")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 100))
        after = path.stat()
        self.assertEqual(after.st_size, before.st_size)
        self.assertEqual(after.st_mtime, before.st_mtime)
        self.assertNotEqual(after.st_mtime_ns, before.st_mtime_ns)
        result, parsed = self.scan_recording_parses()
        self.assertEqual(parsed, [path])
        self.assertEqual(next(row for row in result if row[0] == path.name)[6], "3.0.0")
        self.assert_warm_scan(result)

    def test_added_deleted_and_renamed_jars_refresh_inventory(self):
        self.scan()
        (self.src / "mods" / "added.jar").unlink()
        renamed = self.tgt / "mods" / "renamed.jar"
        (self.tgt / "mods" / "target-only.jar").rename(renamed)
        added = self.write_jar(self.src, "new.jar", "new", "1.0.0")
        result, parsed = self.scan_recording_parses()
        self.assertCountEqual(parsed, [added, renamed])
        for instance in (self.src, self.tgt):
            self.assertEqual(set(self.read_cache(instance)),
                             {path.name for path in (instance / "mods").glob("*.jar")})
        self.assertNotIn("added.jar", {row[0] for row in result})
        self.assertIn("new.jar", {row[0] for row in result})
        self.assertIn("renamed.jar", {row[0] for row in result})
        self.assert_warm_scan(result)

    def test_empty_mod_folders_clear_stale_cache(self):
        self.scan()
        for instance in (self.src, self.tgt):
            for path in (instance / "mods").glob("*.jar"):
                path.unlink()
        result, parsed = self.scan_recording_parses()
        self.assertEqual(result, [])
        self.assertEqual(parsed, [])
        self.assertEqual(self.read_cache(self.src), {})
        self.assertEqual(self.read_cache(self.tgt), {})

    def test_invalid_cache_files_are_ignored_and_rebuilt(self):
        expected = self.scan()
        for raw in (b"{", b"[]", b"null", b"false", b'"text"', b"42", b"\xff"):
            with self.subTest(raw=raw):
                self.cache_path(self.src).write_bytes(raw)
                result, parsed = self.scan_recording_parses()
                self.assertCountEqual(parsed, list((self.src / "mods").glob("*.jar")))
                self.assertEqual(sorted(result), sorted(expected))
                self.assertIsInstance(self.read_cache(self.src), dict)
                self.assert_warm_scan(expected)

    def test_malformed_entries_reparse_only_the_affected_jar(self):
        expected = self.scan()
        cache = self.read_cache(self.src)
        good = cache["shared.jar"]
        bad_entries = [None, [], "text", 42, {}, {"fingerprint": good["fingerprint"]}]
        for key in ("modid", "version", "mod_type"):
            bad_entries.append({k: v for k, v in good.items() if k != key})
            for bad_value in ([], {}, 42, True):
                bad_entries.append({**good, key: bad_value})
        for bad_entry in bad_entries:
            with self.subTest(entry=bad_entry):
                self.cache_path(self.src).write_text(
                    json.dumps({**cache, "shared.jar": bad_entry}), encoding="utf-8")
                result, parsed = self.scan_recording_parses()
                self.assertEqual(parsed, [self.src / "mods" / "shared.jar"])
                self.assertEqual(sorted(result), sorted(expected))
                self.assert_warm_scan(expected)

    def test_legacy_second_resolution_fingerprints_reparse_once(self):
        expected = self.scan()
        for instance in (self.src, self.tgt):
            cache = self.read_cache(instance)
            for name, entry in cache.items():
                stat = (instance / "mods" / name).stat()
                entry["fingerprint"] = f"{stat.st_mtime}_{stat.st_size}"
            self.cache_path(instance).write_text(json.dumps(cache), encoding="utf-8")
        result, parsed = self.scan_recording_parses()
        self.assertEqual(len(parsed), 8)
        self.assertEqual(sorted(result), sorted(expected))
        self.assert_warm_scan(expected)

    def test_null_metadata_is_a_valid_cache_hit(self):
        unknown = self.src / "mods" / "unknown.jar"
        with zipfile.ZipFile(unknown, "w"):
            pass
        result, parsed = self.scan_recording_parses()
        self.assertEqual(len(parsed), 9)
        cached = self.read_cache(self.src)[unknown.name]
        self.assertEqual([cached[key] for key in ("modid", "version", "mod_type")],
                         [None, None, None])
        self.assert_warm_scan(result)

    def test_progress_reports_all_hits_and_misses_and_completion(self):
        self.scan()
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                if mixed:
                    path = self.src / "mods" / "shared.jar"
                    stat = path.stat()
                    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 100))
                progress = Queue()
                _, parsed = self.scan_recording_parses(progress_queue=progress, total=8)
                self.assertEqual(len(parsed), 1 if mixed else 0)
                events = [progress.get_nowait() for _ in range(progress.qsize())]
                self.assertIsNone(events[-1])
                self.assertEqual([event[0] for event in events[:-1]], list(range(1, 9)))
                names = [path.name for instance in (self.src, self.tgt)
                         for path in (instance / "mods").glob("*.jar")]
                self.assertEqual(Counter(event[1] for event in events[:-1]), Counter(names))

    def assert_existing_caches_intact(self, old_bytes):
        for instance, content in old_bytes.items():
            self.assertEqual(self.cache_path(instance).read_bytes(), content)
            self.assertEqual(list(instance.glob(".mods_meta_cache.json.*.tmp")), [])

    def test_partial_cache_write_failure_preserves_previous_cache_and_cleans_temp(self):
        expected = self.scan()
        old_bytes = {instance: self.cache_path(instance).read_bytes()
                     for instance in (self.src, self.tgt)}

        def fail_after_partial_write(cache, file, **kwargs):
            file.write("{")
            raise OSError("simulated interrupted write")

        with mock.patch.object(scanner.json, "dump", side_effect=fail_after_partial_write):
            self.assert_warm_scan(expected)
        self.assert_existing_caches_intact(old_bytes)
        self.assert_warm_scan(expected)

    def test_cache_creation_and_replacement_failures_do_not_break_scan(self):
        expected = self.scan()
        old_bytes = {instance: self.cache_path(instance).read_bytes()
                     for instance in (self.src, self.tgt)}
        for target, attribute in ((scanner.tempfile, "NamedTemporaryFile"), (Path, "replace")):
            with self.subTest(attribute=attribute):
                with mock.patch.object(target, attribute, side_effect=PermissionError("read-only")):
                    self.assert_warm_scan(expected)
                self.assert_existing_caches_intact(old_bytes)
        self.assert_warm_scan(expected)

    def test_file_changed_while_parsing_is_not_persisted_until_next_scan(self):
        changing = self.src / "mods" / "shared.jar"
        original_parse = scanner.get_mod_metadata

        def parse_then_modify(path):
            metadata = original_parse(path)
            if path == changing:
                self.write_jar(self.src, changing.name, "shared", "9.0.0")
                os.utime(changing, ns=(self.MTIME_NS, self.MTIME_NS + 100))
            return metadata

        with mock.patch.object(scanner, "get_mod_metadata", side_effect=parse_then_modify):
            self.scan()
        self.assertNotIn(changing.name, self.read_cache(self.src))
        result, parsed = self.scan_recording_parses()
        self.assertEqual(parsed, [changing])
        self.assertEqual(next(row for row in result if row[0] == changing.name)[6], "9.0.0")
        self.assert_warm_scan(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
