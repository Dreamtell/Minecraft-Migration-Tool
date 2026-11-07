"""Isolated regressions: python tools/test_migration_regressions.py."""
import ast
import json
import os
import queue
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'Minecraft迁移工具'
sys.path.insert(0, str(SOURCE))
os.environ['MCTOOL_LANG'] = 'zh'
from core import migrator as m, scanner, precheck
from core.migration_plan import build_migration_plan, backup_size
from utils import history_store
from utils.file_io import check_writable, atomic_json
from utils.jsonl_channel import JsonlReader


def extract_methods(rel, cls_name, methods, namespace):
    """Exercise UI logic without importing Qt into the Tk/test process."""
    tree = ast.parse((SOURCE / rel).read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    cls.bases, cls.decorator_list = [], []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), rel, 'exec'), namespace)
    return namespace[cls_name]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mctool-regression-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src, self.tgt = self.root / 'source', self.root / 'target'
        self.src.mkdir()
        self.tgt.mkdir()
        self.records = []
        patch = mock.patch.object(history_store, 'save_record', side_effect=lambda data: self.records.append(data))
        patch.start()
        self.addCleanup(patch.stop)
        m._MODID_CACHE.clear()

    def put(self, root, rel, value=b'data'):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def jar(self, root, name, modid='example', version='1.0.0', forge=False):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as z:
            if forge:
                z.writestr('META-INF/mods.toml', '[[mods]]\nmodId="%s"\nversion="%s"\n' % (modid, '$' + '{file.jarVersion}'))
            else:
                z.writestr('fabric.mod.json', json.dumps({'id': modid, 'version': version}))
        return path

    def migrate(self, mods=None, configs=None, world='', extras=None, **kw):
        return m.run_migration(self.src, self.tgt, world, mods or [], configs or [],
                               kw.pop('dry_run', False), kw.pop('overwrite', True),
                               extralist=extras, add_history=kw.pop('add_history', False), **kw)

    def snapshot(self):
        return {p.relative_to(self.tgt).as_posix(): p.read_bytes() if p.is_file() else None
                for p in self.tgt.rglob('*') if '.migrate_backup' not in p.relative_to(self.tgt).parts}

    def test_normal_migrate_and_full_restore(self):
        self.jar(self.src, 'mods/new.jar', version='2.0')
        self.jar(self.tgt, 'mods/old.jar')
        self.put(self.src, 'config/options.cfg', b'new')
        self.put(self.tgt, 'config/options.cfg', b'old')
        self.put(self.src, 'saves/World/level.dat', b'world')
        (self.src / 'saves/World/empty').mkdir()
        (self.tgt / 'config/empty').mkdir()
        self.put(self.src, 'new/deep/file.txt')
        self.put(self.src, 'options.txt', b'new options')
        self.put(self.tgt, 'options.txt', b'old options')
        before = self.snapshot()
        extras = ['new/deep/file.txt', 'options.txt']
        m.do_backup(self.tgt, extra_entries=extras, record_id='normal')
        self.assertTrue(self.migrate(['new.jar'], ['options.cfg'], 'World', extras))
        self.assertFalse((self.tgt / 'mods/old.jar').exists())
        self.assertTrue((self.tgt / 'saves/World/empty').is_dir())
        backup = m.get_record_backup_path(self.tgt, 'normal')
        parts = {k for k, v in m.backup_parts_available(backup).items() if v}
        self.assertIn('saves', parts)
        self.assertTrue(m.do_restore(self.tgt, backup_dir=backup, parts=parts))
        self.assertEqual(before, self.snapshot())

    def test_absent_and_empty_categories_are_available_for_restore(self):
        self.jar(self.tgt, 'mods/old.jar')
        (self.tgt / 'config').mkdir()
        m.do_backup(self.tgt, record_id='empty')
        self.put(self.tgt, 'config/new.txt')
        self.put(self.tgt, 'saves/World/new.txt')
        backup = m.get_record_backup_path(self.tgt, 'empty')
        parts = m.backup_parts_available(backup)
        self.assertTrue(parts['config'])
        self.assertTrue(parts['saves'])
        self.assertTrue(m.do_restore(self.tgt, backup_dir=backup, parts=set(parts)))
        self.assertEqual(list((self.tgt / 'config').iterdir()), [])
        self.assertFalse((self.tgt / 'saves').exists())

    def test_failed_replacement_copy_keeps_old_mod(self):
        self.jar(self.src, 'mods/new.jar', version='2.0')
        old = self.jar(self.tgt, 'mods/old.jar')
        original = old.read_bytes()
        with mock.patch.object(m, 'safe_copy', return_value=(False, 'disk full')):
            self.assertFalse(self.migrate(['new.jar'], add_history=True))
        self.assertEqual(old.read_bytes(), original)
        self.assertFalse((self.tgt / 'mods/new.jar').exists())
        self.assertEqual(self.records[-1]['stats']['mods']['failed'], 1)

    def test_failed_old_cleanup_undoes_already_moved_old_mods(self):
        self.jar(self.src, 'mods/new.jar', version='3.0')
        a = self.jar(self.tgt, 'mods/a.jar')
        b = self.jar(self.tgt, 'mods/b.jar', version='2.0')
        original_move = m.shutil.move
        def move(src, dst):
            if Path(src) == b:
                raise PermissionError('locked old mod')
            return original_move(src, dst)
        with mock.patch.object(m.shutil, 'move', side_effect=move):
            self.assertFalse(self.migrate(['new.jar']))
        self.assertTrue(a.is_file() and b.is_file())
        self.assertFalse((self.tgt / 'mods/new.jar').exists())

    def test_final_mod_switch_failure_restores_same_name_and_other_versions(self):
        self.jar(self.src, 'mods/shared.jar', version='3.0')
        self.jar(self.tgt, 'mods/shared.jar')
        self.jar(self.tgt, 'mods/other.jar', version='2.0')
        before = self.snapshot()
        replace = m.os.replace
        def fail_final(src, dst):
            if Path(src).name == 'new.jar':
                raise OSError('final switch failed')
            return replace(src, dst)
        with mock.patch.object(m.os, 'replace', side_effect=fail_final):
            self.assertFalse(self.migrate(['shared.jar']))
        self.assertEqual(before, self.snapshot())

    def test_each_copy_category_failure_marks_archive_failed(self):
        self.jar(self.src, 'mods/mod.jar')
        self.put(self.src, 'saves/World/level.dat')
        self.put(self.src, 'config/config.txt')
        self.put(self.src, 'extra.txt')
        for category, name in [('mods', 'mod.jar'), ('saves', 'level.dat'), ('config', 'config.txt'), ('extra', 'extra.txt')]:
            with self.subTest(category=category):
                real = m.safe_copy
                def copy(src, *args, **kw):
                    return (False, 'simulated failure') if src.name == name else real(src, *args, **kw)
                with mock.patch.object(m, 'safe_copy', side_effect=copy):
                    self.assertFalse(self.migrate(['mod.jar'], ['config.txt'], 'World', ['extra.txt'], add_history=True))
                rec = self.records[-1]
                self.assertEqual(rec['result'], '有失败')
                self.assertEqual(rec['stats'][category]['failed'], 1)
                self.assertTrue(rec['lists']['failed'])

    def test_cancel_between_save_files_stops_remaining_copies(self):
        for i in range(4):
            self.put(self.src, f'saves/World/{i}.dat')
        event = threading.Event()
        real = m.safe_copy
        def copy(*args, **kw):
            result = real(*args, **kw)
            event.set()
            return result
        with mock.patch.object(m, 'safe_copy', side_effect=copy) as copier:
            self.assertFalse(self.migrate(world='World', check_cancel=event.is_set, add_history=True))
        self.assertEqual(copier.call_count, 1)
        self.assertEqual(self.records[-1]['result'], '已取消')

    def test_cancel_backup_keeps_previous_checkpoint_and_removes_staging(self):
        self.put(self.tgt, 'mods/old.jar')
        m.do_backup(self.tgt, record_id='cancel')
        backup = m.get_record_backup_path(self.tgt, 'cancel')
        previous = (backup / 'manifest.json').read_bytes()
        event = threading.Event()
        real = m.shutil.copy2
        def copy(*args, **kw):
            result = real(*args, **kw)
            event.set()
            return result
        with mock.patch.object(m.shutil, 'copy2', side_effect=copy):
            with self.assertRaises(m.MigrationCancelled):
                m.do_backup(self.tgt, record_id='cancel', check_cancel=event.is_set)
        self.assertEqual(previous, (backup / 'manifest.json').read_bytes())
        self.assertFalse(backup.with_name(backup.name + '.tmp').exists())

    def test_cancel_during_extra_backup_is_not_wrapped_as_error(self):
        self.put(self.tgt, 'extra/a.txt')
        self.put(self.tgt, 'extra/b.txt')
        event = threading.Event()
        real = m.shutil.copy2
        def copy(*args, **kw):
            result = real(*args, **kw)
            event.set()
            return result
        with mock.patch.object(m.shutil, 'copy2', side_effect=copy):
            with self.assertRaises(m.MigrationCancelled):
                m.do_backup(self.tgt, extra_entries=['extra'], record_id='extra', check_cancel=event.is_set)

    def test_invalid_world_names_never_write_outside_saves(self):
        self.put(self.src, 'outside/important.txt', b'new')
        old = self.put(self.tgt, 'outside/important.txt', b'old')
        for world in ('../outside', '.', '..', 'C:/outside', 'a/b', 'a\\b', 'CON', 'world.', 'world '):
            with self.subTest(world=world):
                with self.assertRaises(ValueError):
                    build_migration_plan(self.src, self.tgt, world, [], [])
                self.assertTrue(any(x['level'] == 'fail' for x in precheck._查存档(self.src, self.tgt, world)))
        self.assertEqual(old.read_bytes(), b'old')

    def test_unsafe_extra_paths_rejected_before_backup(self):
        for rel in ('.', './', '..', '../x', '.migrate_backup', '.migrate_backup/rec_test', 'NUL', 'a:b'):
            with self.subTest(rel=rel):
                with self.assertRaises(ValueError):
                    m.do_backup(self.tgt, extra_entries=[rel], record_id='unsafe')
                with self.assertRaises(ValueError):
                    build_migration_plan(self.src, self.tgt, '', [], [], [rel])
        self.assertFalse(m.get_backup_path(self.tgt).exists())

    def test_instance_overlap_rejected(self):
        child = self.src / 'child'
        child.mkdir()
        for a, b in [(self.src, self.src), (self.src, child), (child, self.src)]:
            with self.assertRaises(ValueError):
                build_migration_plan(a, b, '', [], [])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction')
    def test_junction_outside_instance_rejected(self):
        import _winapi
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.tgt / 'config'
        _winapi.CreateJunction(str(outside), str(link))
        try:
            self.put(self.src, 'config/test.txt')
            with self.assertRaises(ValueError):
                build_migration_plan(self.src, self.tgt, '', [], ['test.txt'])
            with self.assertRaises(ValueError):
                m.do_backup(self.tgt, record_id='junction')
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            link.rmdir()

    def test_cancel_during_plan_and_backup_inventory(self):
        for root in (self.src, self.tgt):
            for index in range(24):
                self.put(root, f'saves/World/region/{index}.dat')
        before = self.snapshot()
        for operation in ('plan', 'backup_size'):
            with self.subTest(operation=operation):
                checks = 0

                def cancel():
                    nonlocal checks
                    checks += 1
                    return checks >= 8

                with self.assertRaises(m.MigrationCancelled):
                    if operation == 'plan':
                        build_migration_plan(self.src, self.tgt, 'World', [], [],
                                             check_cancel=cancel)
                    else:
                        backup_size(self.tgt, check_cancel=cancel)
                self.assertEqual(checks, 8)
                self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction')
    def test_nested_source_junction_rejected_during_inventory(self):
        import _winapi
        outside = self.root / 'outside'
        original = self.put(outside, 'private.dat', b'private')
        parent = self.src / 'saves/World/nested'
        parent.mkdir(parents=True)
        link = parent / 'linked'
        _winapi.CreateJunction(str(outside), str(link))
        try:
            with self.assertRaises(ValueError):
                build_migration_plan(self.src, self.tgt, 'World', [], [])
            self.assertEqual(original.read_bytes(), b'private')
            self.assertEqual(list(self.tgt.iterdir()), [])
        finally:
            link.rmdir()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction')
    def test_nested_target_junction_rejected_by_plan_and_backup_estimate(self):
        import _winapi
        outside = self.root / 'outside'
        original = self.put(outside, 'level.dat', b'original')
        self.put(self.src, 'saves/World/nested/linked/level.dat', b'replacement')
        parent = self.tgt / 'saves/World/nested'
        parent.mkdir(parents=True)
        link = parent / 'linked'
        _winapi.CreateJunction(str(outside), str(link))
        try:
            with self.assertRaises(ValueError):
                build_migration_plan(self.src, self.tgt, 'World', [], [])
            with self.assertRaises(ValueError):
                backup_size(self.tgt)
            self.assertEqual(original.read_bytes(), b'original')
            self.assertEqual(list(outside.iterdir()), [original])
        finally:
            link.rmdir()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction')
    def test_target_junction_created_after_plan_is_rechecked_before_copy(self):
        import _winapi
        outside = self.root / 'outside'
        original = self.put(outside, 'level.dat', b'original')
        self.put(self.src, 'saves/World/nested/level.dat', b'replacement')
        plan = build_migration_plan(self.src, self.tgt, 'World', [], [])
        parent = self.tgt / 'saves/World'
        parent.mkdir(parents=True)
        link = parent / 'nested'
        _winapi.CreateJunction(str(outside), str(link))
        try:
            self.assertFalse(self.migrate(world='World', plan=plan))
            self.assertEqual(original.read_bytes(), b'original')
            self.assertEqual(list(outside.iterdir()), [original])
        finally:
            link.rmdir()

    def test_duplicate_modids_choose_newer_before_any_copy(self):
        self.jar(self.src, 'mods/one.jar')
        self.jar(self.src, 'mods/two.jar', version='2.0')
        plan = build_migration_plan(self.src, self.tgt, '', ['one.jar', 'two.jar'], [])
        self.assertEqual([item['src'].name for item in plan['files']], ['two.jar'])
        self.assertEqual(list(self.tgt.iterdir()), [])

    def test_external_jar_counted_and_migrated_without_source_mods(self):
        external = self.jar(self.root, 'external.jar')
        mods = [str(external)]
        plan = build_migration_plan(self.src, self.tgt, '', mods, [])
        self.assertEqual(plan['total_files'], 1)
        self.assertEqual(plan['total_size'], external.stat().st_size)
        self.assertEqual(precheck._查模组清单(self.src, mods), [])
        self.assertTrue(self.migrate(mods, plan=plan))
        self.assertEqual((self.tgt / 'mods/external.jar').read_bytes(), external.read_bytes())

    def test_forge_placeholder_metadata_accepts_path_and_string(self):
        path = self.jar(self.src, 'mods/example-1.2.3.jar', forge=True)
        self.assertEqual(scanner.get_mod_metadata(path), scanner.get_mod_metadata(str(path)))
        self.assertEqual(m._modid_of(path), 'example')

    def test_overlapping_entries_have_one_copy_and_accurate_size(self):
        self.put(self.src, 'config/nested/a.txt', b'12345')
        plan = build_migration_plan(self.src, self.tgt, '', [], ['nested', 'nested/a.txt'], ['config'])
        self.assertEqual(plan['total_files'], 1)
        self.assertEqual(plan['total_size'], 5)
        self.assertTrue(self.migrate(configs=['nested'], extras=['config'], plan=plan))

    def test_backup_size_excludes_assets_and_deduplicates(self):
        self.put(self.tgt, 'config/a.txt', b'123')
        self.put(self.tgt, 'assets/large.bin', b'x' * 1000)
        # config is stored once in the fixed snapshot and once in _extras.
        self.assertEqual(backup_size(self.tgt, ['config', 'config/a.txt']), 6)

    def test_empty_directories_can_migrate_and_rollback(self):
        (self.src / 'empty/nested').mkdir(parents=True)
        m.do_backup(self.tgt, extra_entries=['empty'], record_id='dirs')
        self.assertTrue(self.migrate(extras=['empty']))
        self.assertTrue((self.tgt / 'empty/nested').is_dir())
        self.assertTrue(m.do_restore(self.tgt, backup_dir=m.get_record_backup_path(self.tgt, 'dirs')))
        self.assertFalse((self.tgt / 'empty').exists())

    def test_empty_plan_is_not_success(self):
        self.assertFalse(self.migrate())

    def test_file_directory_conflicts_rejected_before_mutation(self):
        self.put(self.src, 'file.txt')
        (self.tgt / 'file.txt').mkdir()
        with self.assertRaises(ValueError):
            build_migration_plan(self.src, self.tgt, '', [], [], ['file.txt'])
        (self.src / 'folder').mkdir()
        self.put(self.tgt, 'folder')
        with self.assertRaises(ValueError):
            build_migration_plan(self.src, self.tgt, '', [], [], ['folder'])

    def test_dry_run_does_not_change_target_and_respects_skip(self):
        self.put(self.src, 'extra.txt', b'new')
        self.put(self.tgt, 'extra.txt', b'old')
        before = self.snapshot()
        self.assertTrue(self.migrate(extras=['extra.txt'], dry_run=True, extra_overwrite=False, add_history=True))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.records[-1]['stats']['extra']['skipped'], 1)

    def test_partial_failure_can_be_fully_rolled_back(self):
        self.put(self.src, 'config/ok.txt', b'new')
        self.put(self.tgt, 'config/ok.txt', b'old')
        self.put(self.src, 'config/fail.txt')
        before = self.snapshot()
        m.do_backup(self.tgt, record_id='partial')
        real = m.safe_copy
        def copy(src, *args, **kw):
            return (False, 'disk error') if src.name == 'fail.txt' else real(src, *args, **kw)
        with mock.patch.object(m, 'safe_copy', side_effect=copy):
            self.assertFalse(self.migrate(configs=['ok.txt', 'fail.txt']))
        self.assertTrue(m.do_restore(self.tgt, backup_dir=m.get_record_backup_path(self.tgt, 'partial')))
        self.assertEqual(before, self.snapshot())

    def test_probes_and_atomic_copy_preserve_existing_temp_names(self):
        a = self.put(self.tgt, '.mctool_write_test', b'keep')
        b = self.put(self.tgt, '.permission_test', b'keep')
        c = self.put(self.tgt, 'dest.txt.mctmp', b'keep')
        src = self.put(self.src, 'source.txt', b'new')
        check_writable(self.tgt)
        m._atomic_copy_file(src, self.tgt / 'dest.txt')
        for p in (a, b, c):
            self.assertEqual(p.read_bytes(), b'keep')

    def test_restore_failure_keeps_current_tree_and_existing_mctold(self):
        self.put(self.tgt, 'config/old.txt')
        m.do_backup(self.tgt, record_id='restore')
        self.put(self.tgt, 'config/current.txt')
        self.put(self.tgt, 'config.mctold/precious.txt')
        before = self.snapshot()
        with mock.patch.object(m.shutil, 'copytree', side_effect=OSError('disk full')):
            self.assertFalse(m.do_restore(self.tgt, backup_dir=m.get_record_backup_path(self.tgt, 'restore'), parts=['config']))
        self.assertEqual(before, self.snapshot())

    def test_atomic_json_failure_preserves_existing_file(self):
        path = self.put(self.tgt, 'settings.json', b'{"original":true}')
        with self.assertRaises(TypeError):
            atomic_json(path, {'bad': object()})
        self.assertEqual(path.read_bytes(), b'{"original":true}')
        self.assertFalse(list(self.tgt.glob('.mctool-*')))


class OtherRegressions(unittest.TestCase):
    def test_versions(self):
        pairs = [('1.0.1', '1.0-beta', 1), ('1.0', '1.0-rc1', 1),
                 ('1.0.0-1', '1.0.0', -1),
                 ('1.0-beta2', '1.0-beta10', -1), ('1.0.0', '1.0', 0),
                 ('v2.0+build7', '2.0+build8', 0),
                 ('1.20.1-0.14.21', '1.20.1-0.14.20', 1), ('custom', '1.0', None)]
        for a, b, expected in pairs:
            with self.subTest(a=a, b=b):
                self.assertEqual(scanner.compare_versions(a, b), expected)
                self.assertEqual(scanner.compare_versions(b, a), None if expected is None else -expected)

    def test_environment_refreshes_after_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'versions/pack/version.json'
            path.parent.mkdir(parents=True)
            def write(mc, loader):
                path.write_text(json.dumps({'inheritsFrom': mc, 'libraries': [{'name': loader}]}), encoding='utf-8')
            write('1.20.1', 'net.fabricmc:fabric-loader:1')
            self.assertEqual(scanner.detect_instance_env(root)['mc'], '1.20.1')
            write('1.21.1', 'net.neoforged:neoforge:2')
            self.assertEqual(scanner.detect_instance_env(root)['mc'], '1.21.1')
            self.assertEqual(scanner.detect_instance_env(root)['loader'], 'neoforge')

    def test_environment_cache_reuses_metadata_until_inputs_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'mods').mkdir()
            jar = root / 'mods/test.jar'
            jar.write_bytes(b'old')
            with mock.patch.object(scanner, '_env_from_jars', return_value=('', 'fabric', 'test')) as read:
                scanner.detect_instance_env(root)
                scanner.detect_instance_env(root)
                self.assertEqual(read.call_count, 1)
                jar.write_bytes(b'new contents')
                scanner.detect_instance_env(root)
                self.assertEqual(read.call_count, 2)

    def test_jsonl_appends_partial_utf8_and_batch_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ipc.jsonl'
            reader = JsonlReader(path)
            line = json.dumps({'action': 'apply', 'name': '世界'}, ensure_ascii=False).encode('utf-8') + b'\n'
            path.write_bytes(b'{"action":"ready"}\n' + line[:-4])
            self.assertEqual(reader.read(), [{'action': 'ready'}])
            self.assertEqual(reader.read(), [])
            with path.open('ab') as stream:
                stream.write(line[-4:] + b'{"action":"close"}\n')
            self.assertEqual(reader.read(limit=1), [{'action': 'apply', 'name': '世界'}])
            self.assertEqual(reader.read(), [{'action': 'close'}])
            self.assertEqual(reader.read(), [])
            self.assertTrue(path.exists())

    def test_network_failure_and_legacy_negative_cache_recover(self):
        from core import mod_search as search
        with mock.patch.object(search, '_TAG_CACHE', {'example': []}), \
             mock.patch.object(search, '_save_tag_cache'), \
             mock.patch.object(search, 'fetch_categories', side_effect=[None, ['科技']]) as fetch:
            self.assertIsNone(search.fetch_categories_cached('example', modid='example'))
            self.assertEqual(search.fetch_categories_cached('example', modid='example'), ['科技'])
            self.assertEqual(search.fetch_categories_cached('example', modid='example'), ['科技'])
            self.assertEqual(fetch.call_count, 2)

    def test_network_retries_after_cooldown(self):
        from core import mod_search as search
        with mock.patch.object(search, '_NET_DEAD', [True]), \
             mock.patch.object(search, '_NET_RETRY_AT', [30.0]), \
             mock.patch.object(search, '_NET_FAILS', [3]), \
             mock.patch.object(search.time, 'monotonic', side_effect=[29.0, 31.0]), \
             mock.patch.object(search, '_json_get', return_value={'hits': [{'slug': 'example', 'categories': ['technology']}]}) as get:
            self.assertIsNone(search.fetch_categories(modid='example'))
            self.assertEqual(search.fetch_categories(modid='example'), ['科技'])
            self.assertEqual(get.call_count, 1)

    def test_config_non_object_uses_defaults(self):
        from utils import config
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            with mock.patch.object(config, 'CONFIG_FILE', path):
                for value in ('[]', 'null', '1', '"string"'):
                    path.write_text(value, encoding='utf-8')
                    self.assertEqual(config.load_raw_config(), {})

    def test_qt_exit_during_poll_does_not_drop_final_append(self):
        namespace = {'JsonlReader': JsonlReader}
        cls = extract_methods('ui/mw_qt.py', 'QtMixin', ['_poll_qt_host'], namespace)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'results.jsonl'
            path.write_text('{"action":"ready"}\n', encoding='utf-8')
            obj = cls()
            obj.root = types.SimpleNamespace(after=mock.Mock())
            obj._unlock_edit, obj.log = mock.Mock(), mock.Mock()
            proc = types.SimpleNamespace(poll=mock.Mock(return_value=None), returncode=0)
            delivered = []
            def handle(kind, info, message):
                delivered.append(message['action'])
                if message['action'] == 'ready':
                    with path.open('a', encoding='utf-8') as stream:
                        stream.write('{"action":"apply"}\n')
                    proc.poll.return_value = 0
            obj._handle_qt_host = handle
            obj._qt_hosts = {'diff': {'proc': proc, 'res': path}}
            obj._poll_qt_host()
            self.assertIn('diff', obj._qt_hosts)
            obj._poll_qt_host()
            self.assertEqual(delivered, ['ready', 'apply'])
            self.assertEqual(obj._qt_hosts, {})

    def test_progress_cannot_release_worker_or_erase_cancellation(self):
        cls = extract_methods('ui/mw_migration.py', 'MigrationMixin', ['_poll_progress'], {'queue': queue})
        obj = cls()
        obj._migration_cancel = threading.Event()
        obj.progress_window = types.SimpleNamespace(cancelled=True, update_progress=mock.Mock())
        obj.progress_queue = queue.Queue()
        obj.progress_queue.put(None)
        obj._migration_running = True
        obj.root = types.SimpleNamespace(after=mock.Mock(return_value='timer'))
        obj._poll_progress()
        obj._poll_progress()
        self.assertTrue(obj._migration_cancel.is_set())
        self.assertTrue(obj._migration_running)
        self.assertIsNotNone(obj.progress_window)

    def test_prepare_cancel_is_not_an_error_dialog(self):
        namespace = {'build_migration_plan': mock.Mock(side_effect=m.MigrationCancelled()),
                     'MigrationCancelled': m.MigrationCancelled, 'trp': lambda s, *a: s.format(*a)}
        cls = extract_methods('ui/mw_migration.py', 'MigrationMixin', ['_prepare_migration'], namespace)
        obj = cls()
        obj.log, obj._ui_post, obj._complete_migration = mock.Mock(), mock.Mock(), mock.Mock()
        params = dict.fromkeys(['src_path', 'tgt_path', 'world', 'modlist', 'configlist', 'extralist',
                               'overwrite', 'extra_overwrite', 'marker'])
        params['cancel_event'] = threading.Event()
        obj._prepare_migration(params)
        obj._ui_post.assert_called_once_with(obj._complete_migration, False)

    def test_completion_posted_only_after_worker_returns(self):
        entered, release = threading.Event(), threading.Event()
        def run(*args, **kw):
            entered.set()
            release.wait(3)
            return not kw['check_cancel']()
        namespace = {'threading': threading, 'do_backup': mock.Mock(), 'run_migration': run,
                     'prune_legacy_backup': mock.Mock(), 'MigrationCancelled': m.MigrationCancelled,
                     'trp': lambda s, *a: s.format(*a)}
        cls = extract_methods('ui/mw_migration.py', 'MigrationMixin', ['_run_migration_thread'], namespace)
        obj = cls()
        obj.progress_queue = queue.Queue()
        obj._ui_post, obj._complete_migration, obj.log = mock.Mock(), mock.Mock(), mock.Mock()
        cancel = threading.Event()
        worker = threading.Thread(target=obj._run_migration_thread,
                                  args=(None, None, '', [], [], True, True), kwargs={'cancel_event': cancel})
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            cancel.set()
            obj._ui_post.assert_not_called()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        obj._ui_post.assert_called_once_with(obj._complete_migration, False, None)

    def test_english_status_buttons_keep_internal_status_keys(self):
        tree = ast.parse((SOURCE / 'ui/qt_diff_view.py').read_text(encoding='utf-8'))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == '_by_status']
        self.assertEqual(len(calls), 6)
        for call in calls:
            self.assertTrue(all(isinstance(a, ast.Constant) and a.value in ('新增', '更新', '目标独有') for a in call.args))
        cls = extract_methods('ui/qt_diff_view.py', 'QtDiffView', ['_by_status'], {})
        obj = cls()
        obj.store = types.SimpleNamespace(set_checked=mock.Mock())
        obj._update_summary = mock.Mock()
        obj._by_status('新增')
        predicate = obj.store.set_checked.call_args.args[0]
        self.assertTrue(predicate(types.SimpleNamespace(status='新增')))
        self.assertFalse(predicate(types.SimpleNamespace(status='更新')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
