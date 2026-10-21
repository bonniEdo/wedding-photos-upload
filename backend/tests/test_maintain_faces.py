import contextlib
import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'face-service'))
from core import add_photo, empty_state, merge_people, undo_merge
from face_state import face_id, face_map
import maintain_faces as maintenance
from reviews import reassign_faces, undo_correction


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / 'data'; self.data.mkdir()
        (self.data / 'originals').mkdir(); (self.data / 'empty').mkdir()
        (self.data / 'originals' / 'photo.jpg').write_bytes(b'private-original-photo')
        self.config = self.root / '.env'; self.config.write_text('SECRET=do-not-print\n')
        self.state = empty_state()
        def face(vector, name):
            return {'embedding': vector, 'bbox': [0, 0, 20, 20], 'thumbnail': name, 'quality': 100}
        add_photo(self.state, 'photo-a', 'a', [face([1, 0, 0], 'a.jpg'), face([0, 1, 0], 'b.jpg')])
        add_photo(self.state, 'photo-b', 'b', [face([0, 0, 1], 'c.jpg')])
        self.ids = list(self.state['people'])
        self.face_ids = list(face_map(self.state))
        self.merged = merge_people(self.state, self.ids[:2], self.ids[0])
        self.corrected = reassign_faces(self.state, [self.face_ids[0]], self.ids[2])
        # Exercise the exact surrogate IDs used by pre-migration indexes/history.
        for photo in self.state['photos'].values():
            for row in photo['faces']:
                row.pop('face_id', None)
        self.write_state()
        self.backup = self.root / 'backup'

    def tearDown(self):
        self.temporary.cleanup()

    def write_state(self):
        (self.data / 'face_index.json').write_text(json.dumps(self.state), encoding='utf8')

    def backup_now(self):
        return maintenance.create_backup(self.data, self.backup, [self.config])

    def rewrite_archive(self, members):
        """Build a self-consistent manifest around malicious tar metadata/content."""
        archive = self.backup / 'snapshot.tar.gz'
        rows = []
        with tarfile.open(archive, 'w:gz') as target:
            for name, content, kind in members:
                member = tarfile.TarInfo(name); member.size = len(content)
                if kind == 'symlink':
                    member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'; member.size = 0
                elif kind == 'hardlink':
                    member.type = tarfile.LNKTYPE; member.linkname = 'data/face_index.json'; member.size = 0
                target.addfile(member, io.BytesIO(content) if member.isfile() else None)
                rows.append({'path': name, 'size': member.size, 'sha256': hashlib.sha256(content).hexdigest()})
        manifest_path = self.backup / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest.update(files=rows, directories=[], archive_sha256=maintenance.digest(archive), index_sha256=None)
        manifest_path.write_text(json.dumps(manifest))

    def test_backup_verification_and_restore_preserve_all_files_and_empty_dirs_securely(self):
        before = (self.data / 'face_index.json').read_bytes()
        result = self.backup_now()
        self.assertTrue(result['ok']); self.assertEqual(result['file_count'], 3)
        self.assertEqual((self.data / 'face_index.json').read_bytes(), before)
        self.assertFalse((self.data / 'face-worker.lock').exists())
        verified = maintenance.verify_backup(self.backup)
        self.assertEqual(verified['audit']['counts']['merges'], 1)
        self.assertEqual(verified['audit']['counts']['operations'], 2)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o700)
        for path in self.backup.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        restored = self.root / 'restore'
        self.assertTrue(maintenance.restore_test(self.backup, restored)['ok'])
        self.assertEqual((restored / 'data' / 'face_index.json').read_bytes(), before)
        self.assertEqual((restored / 'config' / '0' / '.env').read_bytes(), self.config.read_bytes())
        self.assertTrue((restored / 'data' / 'empty').is_dir())
        for path in restored.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_audit_is_read_only_and_reports_aggregate_errors_without_vectors(self):
        before = (self.data / 'face_index.json').read_bytes()
        self.assertTrue(maintenance.audit_data(self.data)['ok'])
        state = copy.deepcopy(self.state)
        state['photos']['photo-a']['faces'][0]['person_id'] = 'unknown-person'
        state['aliases']['cycle-a'] = 'cycle-b'; state['aliases']['cycle-b'] = 'cycle-a'
        state['people'][self.ids[2]]['photo_ids'] = []
        result = maintenance.audit_state(state)
        self.assertFalse(result['ok'])
        self.assertIn('missing_face_person', result['errors'])
        self.assertIn('alias_cycle', result['errors'])
        self.assertIn('person_photo_ids_mismatch', result['errors'])
        self.assertNotIn('embedding', json.dumps(result))
        self.assertEqual((self.data / 'face_index.json').read_bytes(), before)

    def test_existing_worker_lock_is_checked_without_modification(self):
        lock = self.data / 'face-worker.lock'; lock.write_bytes(b'unchanged')
        before = lock.stat().st_mtime_ns
        with lock.open('rb') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(maintenance.MaintenanceError, 'active'):
                self.backup_now()
            with self.assertRaisesRegex(maintenance.MaintenanceError, 'active'):
                with maintenance.offline_lock(self.data):
                    self.fail('Live worker lock must not be accepted')
        self.assertEqual(lock.read_bytes(), b'unchanged'); self.assertEqual(lock.stat().st_mtime_ns, before)
        self.assertTrue(self.backup_now()['ok'])
        with lock.open('rb') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(maintenance.MaintenanceError, 'active'):
                maintenance.migrate(self.data, self.backup)

    def test_source_symlinks_and_special_files_are_refused(self):
        for target, is_directory in [(self.config, False), (self.root, True)]:
            link = self.data / 'unsafe'; link.symlink_to(target, target_is_directory=is_directory)
            with self.assertRaises(maintenance.MaintenanceError):
                self.backup_now()
            link.unlink()
            self.assertFalse(self.backup.exists())
        fifo = self.data / 'pipe'; os.mkfifo(fifo)
        with self.assertRaises(maintenance.MaintenanceError):
            self.backup_now()

    def test_outputs_cannot_overwrite_or_nest_in_sources_or_backup(self):
        for output in (self.data, self.data / 'nested'):
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.create_backup(self.data, output)
        self.backup_now()
        for output in (self.backup, self.backup / 'nested', self.data / 'restored'):
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.restore_test(self.backup, output)
        with self.assertRaises(maintenance.MaintenanceError):
            self.backup_now()

    def test_archive_and_individual_file_corruption_are_refused(self):
        self.backup_now()
        archive = self.backup / 'snapshot.tar.gz'
        archive.write_bytes(archive.read_bytes() + b'corruption')
        with self.assertRaisesRegex(maintenance.MaintenanceError, 'checksum'):
            maintenance.verify_backup(self.backup)
        manifest_path = self.backup / 'manifest.json'
        manifest = json.loads(manifest_path.read_text()); manifest['archive_sha256'] = maintenance.digest(archive)
        manifest['files'][0]['sha256'] = '0' * 64; manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(maintenance.MaintenanceError, 'checksum'):
            maintenance.verify_backup(self.backup)

    def test_safe_extraction_rejects_path_link_and_duplicate_attacks(self):
        self.backup_now()
        for members in (
                [('../escaped', b'bad', 'file')], [('/tmp/escaped', b'bad', 'file')],
                [('data/../escaped', b'bad', 'file')], [('data\\escaped', b'bad', 'file')],
                [('data/link', b'', 'symlink')], [('data/link', b'', 'hardlink')],
                [('data/same', b'bad', 'file'), ('data/same', b'bad', 'file')],
                [('data/file', b'bad', 'file'), ('data/file/nested', b'bad', 'file')]):
            with self.subTest(members=members):
                self.rewrite_archive(members)
                with self.assertRaises(maintenance.MaintenanceError):
                    maintenance.restore_test(self.backup, self.root / 'restore')
                self.assertFalse((self.root / 'restore').exists())
                self.assertFalse((self.root / 'escaped').exists())

    def test_structurally_invalid_archived_index_is_not_a_verified_backup(self):
        self.backup_now()
        invalid = json.dumps({'photos': {'broken': {}}, 'people': {}}).encode()
        self.rewrite_archive([('data/face_index.json', invalid, 'file')])
        manifest_path = self.backup / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['index_sha256'] = hashlib.sha256(invalid).hexdigest(); manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(maintenance.MaintenanceError, 'structural'):
            maintenance.verify_backup(self.backup)

    def test_malformed_manifests_are_rejected_without_restoring(self):
        self.backup_now()
        manifest_path = self.backup / 'manifest.json'
        original = json.loads(manifest_path.read_text())
        for manifest in ([], {**original, 'data_source': []}, {**original, 'files': [None]},
                         {**original, 'directories': [None]}):
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.verify_backup(self.backup)

    def test_unstable_sources_are_refused_and_partial_backup_is_removed(self):
        original_inventory = maintenance.inventory
        calls = 0
        def changing_inventory(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.data / 'originals' / 'photo.jpg').write_bytes(b'changed')
            return original_inventory(*args, **kwargs)
        with mock.patch.object(maintenance, 'inventory', side_effect=changing_inventory):
            with self.assertRaisesRegex(maintenance.MaintenanceError, 'changed'):
                self.backup_now()
        self.assertFalse(self.backup.exists())

    def test_migration_adds_only_identity_receipt_and_revisions_preserving_undo(self):
        self.state['photos'] = dict(reversed(list(self.state['photos'].items())))
        self.state['people'] = dict(sorted(self.state['people'].items(), reverse=True))
        self.write_state()
        self.backup_now()
        before = copy.deepcopy(self.state)
        result = maintenance.migrate(self.data, self.backup)
        self.assertTrue(result['ok']); self.assertEqual(result['added_face_ids'], 3)
        migrated = json.loads((self.data / 'face_index.json').read_text())
        self.assertEqual(list(migrated['photos']), list(before['photos']))
        self.assertEqual(list(migrated['people']), list(before['people']))
        self.assertEqual(migrated['revision'], before['revision'] + 1)
        self.assertEqual(migrated['embedding_revision'], before['embedding_revision'])
        for key in ('people', 'aliases', 'merge_history', 'admin_history'):
            self.assertEqual(migrated[key], before[key])
        for photo_id, photo in before['photos'].items():
            for index, row in enumerate(photo['faces']):
                actual = copy.deepcopy(migrated['photos'][photo_id]['faces'][index])
                self.assertEqual(actual.pop('face_id'), face_id(photo_id, index, row))
                self.assertEqual(actual, row)
        receipt = migrated['face_backfill_maintenance']
        self.assertEqual(receipt['backup_dir'], str(self.backup.resolve()))
        self.assertEqual(receipt['original_index_sha256'], maintenance.verify_backup(self.backup)['manifest']['index_sha256'])
        saved = (self.data / 'face_index.json').read_bytes()
        again = maintenance.migrate(self.data, self.backup)
        self.assertFalse(again['changed']); self.assertEqual(saved, (self.data / 'face_index.json').read_bytes())
        undo_correction(migrated, self.corrected['operation_id'])
        undo_merge(migrated, self.merged['merge_id'])
        self.assertEqual([row['person_id'] for row in migrated['photos']['photo-a']['faces']], self.ids[:2])
        self.assertTrue(maintenance.audit_state(migrated)['ok'])

    def test_migration_refuses_stale_or_other_source_backup_without_writing(self):
        self.backup_now()
        self.state['photos']['photo-a']['name'] = 'updated'; self.write_state()
        before = (self.data / 'face_index.json').read_bytes()
        with self.assertRaisesRegex(maintenance.MaintenanceError, 'match'):
            maintenance.migrate(self.data, self.backup)
        self.assertEqual(before, (self.data / 'face_index.json').read_bytes())
        other = self.root / 'other'; other.mkdir(); (other / 'face_index.json').write_bytes(before)
        with self.assertRaisesRegex(maintenance.MaintenanceError, 'match'):
            maintenance.migrate(other, self.backup)

    def test_history_audit_rejects_broken_positions_face_refs_and_merge_order(self):
        mutations = (
            lambda state: state['merge_history'][0]['changes'][0].__setitem__(1, 99),
            lambda state: state['admin_history'][1]['faces_before'].__setitem__('missing-face', {}),
            lambda state: state['admin_history'][0].__setitem__('id', 'missing-merge'),
            lambda state: state['photos']['photo-a']['faces'][1].__setitem__('person_id', self.ids[2]))
        for mutate in mutations:
            state = copy.deepcopy(self.state); mutate(state)
            self.assertFalse(maintenance.audit_state(state)['ok'])
        state = copy.deepcopy(self.state)
        # Current links remain consistent, but reversing the correction reveals
        # an invalid old merge target at its original positional reference.
        before = state['admin_history'][1]['faces_before'][self.face_ids[0]]
        before['person_id'] = self.ids[2]
        # The merge changes the other face, so corrupt that positional target.
        state['photos']['photo-a']['faces'][1]['person_id'] = self.ids[2]
        state['people'][self.ids[0]]['photo_ids'] = []
        self.assertIn('merge_undo_person_mismatch', maintenance.audit_state(state)['errors'])

    def test_backfill_add_history_requires_an_exact_appended_suffix(self):
        state = copy.deepcopy(self.state)
        photo = state['photos']['photo-a']; row = copy.deepcopy(photo['faces'][0])
        row.update(face_id='new-face', person_id='new-person')
        state['people']['new-person'] = {'photo_ids': ['photo-a'], 'samples': [row['embedding']]}
        photo['faces'].append(row)
        state['admin_history'].append({'id': 'backfill-op', 'kind': 'backfill_add', 'photo_id': 'photo-a',
                                       'before_count': 2, 'face_ids': ['new-face'], 'person_ids': ['new-person'],
                                       'candidate_id': 'candidate', 'created_at': 123})
        self.assertTrue(maintenance.audit_state(state)['ok'])
        state['admin_history'][-1]['face_ids'] = ['wrong-face']
        self.assertIn('backfill_suffix_mismatch', maintenance.audit_state(state)['errors'])

    def test_cli_outputs_no_secret_values_and_returns_nonzero_for_bad_audit(self):
        self.state['people'][self.ids[2]]['photo_ids'] = []; self.write_state()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(maintenance.main(['audit', '--data', str(self.data)]), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report['ok'])
        self.assertNotIn('SECRET', output.getvalue()); self.assertNotIn('embedding', output.getvalue())


if __name__ == '__main__':
    unittest.main()
