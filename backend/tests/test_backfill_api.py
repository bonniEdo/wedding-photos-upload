"""Isolated P5 API transactions: no real DATA, workers, models or cloud calls."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'face-service'))
from backfill_mapping import build_candidate, photo_signature
from core import add_photo, atomic_json, empty_state, merge_people
from face_state import face_map, revision
from reviews import reassign_faces
from search_index import SearchCache


class BackfillAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(); cls.root = Path(cls.directory.name)
        with patch.dict(os.environ, {'DATA_DIR': str(cls.root), 'FACE_PIPELINE': 'legacy', 'GROUP_MODE': 'legacy',
                                     'ADMIN_PASSWORD': 'backfill-test-only-password'}):
            spec = importlib.util.spec_from_file_location('isolated_backfill_api',
                Path(__file__).resolve().parents[1] / 'face-service' / 'app.py')
            cls.app = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.app; spec.loader.exec_module(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        for name in ('face-backfill', 'face_crops', 'thumbs', 'previews'):
            shutil.rmtree(self.root / name, ignore_errors=True)
        for name in ('thumbs', 'previews', 'face_crops', 'face-backfill/thumbs', 'face-backfill/crops', 'face-backfill/previews'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.app.state = empty_state(); self.app.search_cache = SearchCache()
        self.app.backfill_runner = SimpleNamespace(key='test-key')
        self.photo_id = 'a' * 64; self.second_photo_id = 'b' * 64
        def face(vector, name, box):
            return {'embedding': vector, 'thumbnail': name, 'bbox': box, 'quality': 100,
                    'coordinate_space': 'original-exif', 'image_size': [200, 100], 'preview_size': [200, 100]}
        add_photo(self.app.state, self.photo_id, 'old-photo', [
            face([1, 0, 0], 'old-a.jpg', [10, 10, 20, 20]), face([0, 1, 0], 'old-b.jpg', [80, 10, 20, 20])])
        add_photo(self.app.state, self.second_photo_id, 'other-photo', [face([0, 0, 1], 'old-c.jpg', [10, 10, 20, 20])])
        self.people = list(self.app.state['people']); self.old_fids = list(face_map(self.app.state))
        self.merged = merge_people(self.app.state, self.people[:2], self.people[0])
        self.corrected = reassign_faces(self.app.state, [self.old_fids[0]], self.people[2])
        self.app.state['face_backfill_maintenance'] = {'version': 1, 'archive_sha256': 'c' * 64,
            'original_index_sha256': 'd' * 64, 'backup_dir': str(self.root / 'verified-backup'), 'created_at': 1}
        detections = [face([1, 0, 0], 'analysis-old-a.jpg', [10, 10, 20, 20]),
                      face([0, 1, 0], 'analysis-old-b.jpg', [80, 10, 20, 20]),
                      face([.7, .7, 0], 'candidate-new.jpg', [150, 60, 10, 10])]
        for index, row in enumerate(detections):
            row.update(face_id=f'analysis-face-{index}', crop=f'analysis-crop-{index}.jpg',
                       original_bbox=row['bbox'], quality_metrics={'sample_eligible': True})
        self.candidate = build_candidate(self.app.state, self.photo_id,
            {'faces': detections, 'metadata': {'image_size': [200, 100], 'preview_size': [200, 100], 'partial': False}},
            'e' * 64, 'test-key', [200, 100])
        self.candidate_id = self.candidate['id']
        self.new_face = self.candidate['new_faces'][0]
        self.new_fid = self.new_face['candidate_face_id']
        self.app.state['backfill'] = {'schema_version': 1, 'paused': True, 'pipeline_key': 'test-key', 'jobs': {
            self.photo_id: {'photo_id': self.photo_id, 'status': 'ready', 'pipeline_key': 'test-key', 'attempts': 1,
                'signature': photo_signature(self.app.state, self.photo_id), 'candidate': copy.deepcopy(self.candidate),
                'accepted': {}, 'source': 'local', 'finished_at': 1}}}
        for row in detections:
            Image.new('RGB', (24, 24), 'orange').save(self.root / 'face-backfill' / 'thumbs' / row['thumbnail'])
            Image.new('RGB', (48, 48), 'purple').save(self.root / 'face-backfill' / 'crops' / row['crop'])
        Image.new('RGB', (200, 100), 'gray').save(self.root / 'face-backfill' / 'previews' / f'{self.candidate_id}.jpg')
        atomic_json(self.app.STATE_PATH, self.app.state)

    def accept_body(self, request_id='backfill-accept-0001', **updates):
        values = {'face_ids': [self.new_fid], 'revision': revision(self.app.state), 'request_id': request_id, **updates}
        return self.app.BackfillAcceptRequest(**values)

    def undo(self, operation_id):
        return self.app.admin_undo_correction(self.app.CorrectionUndoRequest(correction_id=operation_id,
            revision=revision(self.app.state), request_id='backfill-undo-00001'))

    def test_candidate_and_status_are_read_only_private_views_without_embeddings(self):
        before = copy.deepcopy(self.app.state); disk = self.app.STATE_PATH.read_bytes()
        status = self.app.admin_backfill_status(offset=0, limit=30)
        self.assertEqual(status['total'], 1); self.assertEqual(status['counts']['ready'], 1)
        view = self.app.admin_backfill_candidate(self.candidate_id)
        self.assertEqual(view['candidate_id'], self.candidate_id)
        self.assertEqual(view['photo_id'], self.photo_id); self.assertEqual(view['mapped_count'], 2)
        self.assertEqual(view['ambiguous_count'], 0); self.assertFalse(view['partial']); self.assertFalse(view['stale'])
        self.assertIn('/admin/', view['photo_url'])
        row = next(face for face in view['faces'] if face['face_id'] == self.new_fid)
        self.assertEqual(row['normalized_bbox'], [.75, .6, .05, .1])
        self.assertFalse(row['accepted']); self.assertIn('/admin/', row['crop_url'])
        serialized = json.dumps(view)
        self.assertNotIn('embedding', serialized); self.assertNotIn('source_sha256', serialized)
        self.assertEqual(self.app.state, before); self.assertEqual(self.app.STATE_PATH.read_bytes(), disk)

    def test_accept_copies_only_approved_assets_and_preserves_original_people_and_history(self):
        before = copy.deepcopy(self.app.state)
        result = self.app.admin_backfill_accept(self.candidate_id, self.accept_body())
        self.assertEqual(result['added_count'], 1)
        after = self.app.state
        self.assertEqual(after['photos'][self.photo_id]['faces'][:2], before['photos'][self.photo_id]['faces'])
        self.assertEqual(after['photos'][self.second_photo_id], before['photos'][self.second_photo_id])
        for pid, person in before['people'].items():
            self.assertEqual(after['people'][pid], person)
        self.assertEqual(after['aliases'], before['aliases']); self.assertEqual(after['merge_history'], before['merge_history'])
        self.assertEqual(after['admin_history'][:-1], before['admin_history'])
        operation = after['admin_history'][-1]
        self.assertEqual(operation['kind'], 'backfill_add'); self.assertEqual(operation['before_count'], 2)
        self.assertEqual(operation['face_ids'], [self.new_fid])
        self.assertEqual(after['embedding_revision'], before['embedding_revision'] + 1)
        self.assertEqual([path.name for path in (self.root / 'thumbs').iterdir()], [self.new_face['face']['thumbnail']])
        self.assertEqual([path.name for path in (self.root / 'face_crops').iterdir()], [self.new_face['face']['crop']])
        new_person = after['people'][result['person_ids'][0]]
        self.assertTrue(new_person['provisional']); self.assertEqual(new_person['samples'], [])
        with self.assertRaises(HTTPException) as failure:
            self.app.admin_undo_correction(self.app.CorrectionUndoRequest(correction_id=self.corrected['operation_id'],
                revision=revision(after), request_id='old-correction-undo-01'))
        self.assertEqual(failure.exception.status_code, 409)
        self.undo(result['operation_id'])
        self.assertEqual(self.app.state['photos'], before['photos'])
        self.assertEqual(self.app.state['aliases'], before['aliases']); self.assertEqual(self.app.state['merge_history'], before['merge_history'])
        self.assertEqual(self.app.state['admin_history'], before['admin_history'])
        self.assertEqual(self.app.state['people'][result['person_ids'][0]]['photo_ids'], [])

    def test_accept_receipt_replays_after_undo_and_restart_without_readding_face(self):
        body = self.accept_body(); result = self.app.admin_backfill_accept(self.candidate_id, body)
        replay = self.app.admin_backfill_accept(self.candidate_id, body)
        self.assertTrue(replay['replayed']); self.assertEqual(replay['operation_id'], result['operation_id'])
        self.undo(result['operation_id'])
        self.app.state = json.loads(self.app.STATE_PATH.read_text())
        before = copy.deepcopy(self.app.state)
        replay = self.app.admin_backfill_accept(self.candidate_id, body)
        self.assertTrue(replay['replayed']); self.assertEqual(self.app.state, before)
        self.assertEqual(len(self.app.state['photos'][self.photo_id]['faces']), 2)
        with self.assertRaises(HTTPException) as failure:
            self.app.admin_backfill_accept(self.candidate_id, body.model_copy(update={'face_ids': []}))
        self.assertEqual(failure.exception.status_code, 409)

    def test_stale_revision_or_corrected_candidate_cannot_publish_state(self):
        body = self.accept_body(revision=0); before = copy.deepcopy(self.app.state)
        with self.assertRaises(HTTPException) as failure:
            self.app.admin_backfill_accept(self.candidate_id, body)
        self.assertEqual(failure.exception.status_code, 409); self.assertEqual(self.app.state, before)
        self.app.state['photos'][self.photo_id]['faces'][0]['manual_confirmed'] = 'changed-after-analysis'
        before = copy.deepcopy(self.app.state)
        self.assertTrue(self.app.admin_backfill_candidate(self.candidate_id)['stale'])
        with self.assertRaises(HTTPException) as failure:
            self.app.admin_backfill_accept(self.candidate_id, self.accept_body(request_id='stale-candidate-0001'))
        self.assertEqual(failure.exception.status_code, 409); self.assertEqual(self.app.state, before)

    def test_candidate_from_previous_pipeline_is_visibly_stale_and_cannot_be_accepted(self):
        self.app.backfill_runner.key = 'new-pipeline-key'
        self.assertTrue(self.app.admin_backfill_candidate(self.candidate_id)['stale'])
        before = copy.deepcopy(self.app.state)
        with self.assertRaises(HTTPException) as failure:
            self.app.admin_backfill_accept(self.candidate_id, self.accept_body())
        self.assertEqual(failure.exception.status_code, 409); self.assertEqual(self.app.state, before)

    def test_asset_name_collision_cannot_overwrite_an_existing_public_thumbnail(self):
        thumbnail = self.root / 'thumbs' / self.new_face['face']['thumbnail']
        Image.new('RGB', (24, 24), 'red').save(thumbnail)
        original = thumbnail.read_bytes(); before = copy.deepcopy(self.app.state)
        with self.assertRaises((HTTPException, OSError, ValueError)):
            self.app.admin_backfill_accept(self.candidate_id, self.accept_body())
        self.assertEqual(thumbnail.read_bytes(), original); self.assertEqual(self.app.state, before)

    def test_missing_or_invalid_approved_asset_does_not_publish_any_state(self):
        for damage in ('missing', 'invalid'):
            with self.subTest(damage=damage):
                crop = self.root / 'face-backfill' / 'crops' / self.new_face['face']['crop']
                if damage == 'missing':
                    crop.unlink()
                else:
                    crop.write_bytes(b'not-a-jpeg')
                before = copy.deepcopy(self.app.state); disk = self.app.STATE_PATH.read_bytes()
                with self.assertRaises((HTTPException, OSError, ValueError)):
                    self.app.admin_backfill_accept(self.candidate_id, self.accept_body())
                self.assertEqual(self.app.state, before); self.assertEqual(self.app.STATE_PATH.read_bytes(), disk)

    def test_atomic_commit_failure_does_not_publish_receipt_or_history(self):
        before = copy.deepcopy(self.app.state); disk = self.app.STATE_PATH.read_bytes()
        body = self.accept_body()
        with patch.object(self.app, 'atomic_json', side_effect=OSError('disk-full')):
            with self.assertRaises(OSError):
                self.app.admin_backfill_accept(self.candidate_id, body)
        self.assertEqual(self.app.state, before); self.assertEqual(self.app.STATE_PATH.read_bytes(), disk)
        result = self.app.admin_backfill_accept(self.candidate_id, body)
        self.assertEqual(result['added_count'], 1)

    def test_start_pause_require_backup_and_revision_and_preserve_histories(self):
        before = copy.deepcopy(self.app.state)
        body = self.app.BackfillStartRequest(photo_ids=[self.second_photo_id], retry=False,
            revision=revision(self.app.state), request_id='start-backfill-0001')
        result = self.app.admin_backfill_start(body)
        self.assertEqual(result['queued'], 1)
        self.assertEqual(self.app.state['backfill']['jobs'][self.second_photo_id]['status'], 'queued')
        replay = self.app.admin_backfill_start(body); self.assertTrue(replay['replayed'])
        self.app.admin_backfill_pause(self.app.VersionedRequest(revision=revision(self.app.state), request_id='pause-backfill-0001'))
        self.assertTrue(self.app.state['backfill']['paused'])
        for key in ('people', 'aliases', 'merge_history', 'admin_history', 'photos'):
            self.assertEqual(self.app.state[key], before[key])
        self.app.state.pop('face_backfill_maintenance')
        with self.assertRaises(HTTPException) as failure:
            self.app.admin_backfill_start(self.app.BackfillStartRequest(revision=revision(self.app.state),
                request_id='unbacked-start-0001'))
        self.assertEqual(failure.exception.status_code, 409)

    def test_admin_auth_dependency_and_pagination_are_enforced(self):
        with self.assertRaises(HTTPException) as failure:
            self.app.require_admin('wrong-key')
        self.assertEqual(failure.exception.status_code, 401)
        for route in self.app.app.routes:
            if route.path.startswith('/admin/face-backfill'):
                self.assertTrue(any(dep.call is self.app.require_admin for dep in route.dependant.dependencies), route.path)
        for kwargs in ({'offset': -1}, {'limit': 101}, {'limit': 0}):
            with self.assertRaises(HTTPException):
                self.app.admin_backfill_status(**kwargs)


if __name__ == '__main__':
    unittest.main()
