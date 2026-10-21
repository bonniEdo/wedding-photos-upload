import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'face-service'))
from backfill_jobs import (BackfillRunner, OriginalError, accept_faces, candidate_basis,
                           claim_job, pause_jobs, recover_jobs, start_jobs, summary)
from backfill_mapping import candidate_fingerprint, photo_signature, validate_candidate
from core import add_photo, empty_state, merge_people, undo_merge
from face_engine import Analysis
from face_state import face_id, latest_operation
from reviews import build_reviews, reassign_faces, resolve_review, undo_correction
from search_index import SearchCache


PHOTO = 'old-photo'
KEY = 'local-models-config-sha256'
RAW = b'local original bytes passed only to the fake analyzer'
SHA = hashlib.sha256(RAW).hexdigest()


class AtomicStore:
    """The service publishes only after its deepcopy has durably committed."""
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.saved = copy.deepcopy(state)
        self.calls = 0
        self.fail_on = set()

    def get(self):
        return self.state

    def commit(self, mutation):
        updated = copy.deepcopy(self.state)
        result = mutation(updated)
        self.calls += 1
        if self.calls in self.fail_on:
            raise OSError('simulated atomic index write failure')
        self.saved = copy.deepcopy(updated)
        self.state = updated
        return result

    def restart(self):
        self.state = json.loads(json.dumps(self.saved))


def old_face(box, vector, name):
    return {'bbox': box, 'embedding': vector, 'thumbnail': name+'.jpg', 'quality': 20.}


def fake_analysis(seed='analysis', partial=False):
    faces = []
    for index, (box, vector) in enumerate((([100., 50., 60., 80.], [0., 0., 0., 1.]),
                                           ([700., 200., 40., 50.], [0., 0., 0., 1.]),
                                           ([850., 200., 40., 50.], [0., 0., -1., 0.]))):
        fid = hashlib.sha256((seed+str(index)).encode()).hexdigest()[:32]
        faces.append({'face_id': fid, 'bbox': box, 'original_bbox': box,
                      'coordinate_space': 'original-exif', 'image_size': [1000, 500],
                      'preview_size': [500, 250], 'embedding': vector,
                      'thumbnail': seed+'_'+str(index)+'.jpg', 'crop': fid+'.jpg',
                      'quality': 200., 'quality_metrics': {'sample_eligible': True, 'sharpness': 50.},
                      'pipeline_version': 'tiled-v1', 'source': 'tile:0:0'})
    pixels = np.zeros((20, 20, 3), dtype=np.uint8)
    return Analysis(faces, {'pipeline': 'enhanced', 'pipeline_version': 'tiled-v1',
                            'image_size': [1000, 500], 'preview_size': [500, 250],
                            'partial': partial, 'partial_reason': 'tile_limit' if partial else None,
                            'tiles_run': 1, 'elapsed_seconds': .01},
                    np.zeros((250, 500, 3), dtype=np.uint8),
                    [(pixels.copy(), pixels.copy()) for _ in faces])


class BackfillJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root/'previews').mkdir()
        Image.new('RGB', (500, 250), 'white').save(self.root/'previews'/f'{PHOTO}.jpg')
        state = empty_state()
        add_photo(state, PHOTO, 'old.jpg', [old_face([50, 25, 30, 40], [1., 0., 0., 0.], 'alpha'),
                                           old_face([130, 25, 30, 40], [0., 1., 0., 0.], 'beta')])
        add_photo(state, 'another', 'another.jpg', [old_face([0, 0, 10, 10], [0., 0., 1., 0.], 'gamma')])
        self.person_ids = list(state['people'])
        self.old_merge = merge_people(state, self.person_ids[1:], self.person_ids[1])
        # A pre-existing correction and merge must survive automatic metadata and append/undo.
        selected = face_id(PHOTO, 0, state['photos'][PHOTO]['faces'][0])
        self.old_correction = reassign_faces(state, [selected])
        state['photos'][PHOTO]['faces'][0].pop('face_id', None)
        state['face_backfill_maintenance'] = {'archive_sha256': 'b'*64, 'created_at': 123.}
        self.store = AtomicStore(state)
        self.analyzed = []
        self.original_reads = []

    def get_original(self, pid):
        self.original_reads.append(pid)
        return RAW, SHA, 'local'

    def analyze(self, raw, seed):
        self.analyzed.append((raw, seed))
        return fake_analysis(seed)

    def runner(self, original=None, analyze=None, key=KEY):
        return BackfillRunner(self.root, key, self.store.get, self.store.commit,
                              original or self.get_original, analyze or self.analyze)

    def queue(self):
        return self.store.commit(lambda state: start_jobs(state, KEY, [PHOTO]))

    def ready(self, analyze=None):
        self.queue()
        self.assertTrue(self.runner(analyze=analyze).step())
        job = self.store.state['backfill']['jobs'][PHOTO]
        self.assertEqual(job['status'], 'ready')
        return job['candidate']

    def accept(self, candidate, ids=None):
        ids = ids if ids is not None else [f['candidate_face_id'] for f in candidate['new_faces']]
        return self.store.commit(lambda state: accept_faces(state, candidate['id'], ids, KEY))

    def undo(self, operation_id):
        return self.store.commit(lambda state: undo_correction(state, operation_id))

    def test_requires_backup_and_only_accepts_existing_unique_photos(self):
        state = copy.deepcopy(self.store.state)
        state.pop('face_backfill_maintenance')
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, '備份'):
            start_jobs(state, KEY, [PHOTO])
        self.assertEqual(state, before)
        for ids in ([], ['missing'], [PHOTO, PHOTO]):
            with self.subTest(ids=ids):
                before = copy.deepcopy(self.store.state)
                with self.assertRaises(ValueError):
                    self.store.commit(lambda updated: start_jobs(updated, KEY, ids))
                self.assertEqual(self.store.state, before)

    def test_claim_is_durable_recovers_after_restart_and_uses_new_run_token(self):
        self.queue()
        claimed = self.store.commit(lambda state: claim_job(state, KEY))
        self.assertEqual(claimed['attempts'], 1)
        self.assertEqual(self.store.saved['backfill']['jobs'][PHOTO]['run_token'], claimed['run_token'])
        self.store.restart()
        self.assertTrue(self.store.commit(lambda state: recover_jobs(state, KEY)))
        job = self.store.state['backfill']['jobs'][PHOTO]
        self.assertEqual(job['status'], 'queued')
        self.assertNotIn('run_token', job)
        self.assertTrue(self.runner().step())
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['status'], 'ready')
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['attempts'], 2)
        self.assertEqual(len(self.analyzed), 1)

    def test_recovery_marks_incompatible_processing_job_stale(self):
        self.queue()
        self.store.commit(lambda state: claim_job(state, KEY))
        self.assertTrue(self.store.commit(lambda state: recover_jobs(state, 'new-model')))
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['status'], 'stale')
        self.assertFalse(self.runner(key='new-model').step())
        self.assertEqual(self.original_reads, [])

    def test_pause_stops_claiming_and_same_queue_start_resumes_without_duplicate(self):
        self.queue()
        self.store.commit(pause_jobs)
        commits = self.store.calls
        self.assertFalse(self.runner().step())
        self.assertEqual(self.store.calls, commits)
        self.assertEqual(self.original_reads, [])
        result = self.queue()
        self.assertEqual(result['queued'], 0)
        self.assertFalse(self.store.state['backfill']['paused'])
        self.assertTrue(self.runner().step())
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['attempts'], 1)

    def test_pause_during_analysis_finishes_current_job_and_keeps_queue_paused(self):
        def analyze(raw, seed):
            self.store.commit(pause_jobs)
            return fake_analysis(seed)
        self.ready(analyze=analyze)
        self.assertTrue(self.store.state['backfill']['paused'])
        self.assertFalse(self.runner().step())

    def test_missing_original_is_unavailable_without_deleting_old_observations(self):
        self.queue()
        original = copy.deepcopy(self.store.state['photos'])
        def missing(pid):
            raise OriginalError('unavailable', '原圖已釋出且沒有雲端來源')
        self.assertTrue(self.runner(original=missing).step())
        job = self.store.state['backfill']['jobs'][PHOTO]
        self.assertEqual(job['status'], 'unavailable')
        self.assertIn('原圖', job['message'])
        self.assertEqual(self.store.state['photos'], original)
        self.assertEqual(self.analyzed, [])

    def test_changed_sha_is_rejected_before_model_and_asset_writes(self):
        self.queue()
        original = copy.deepcopy(self.store.state['photos'])
        self.assertTrue(self.runner(original=lambda pid: (RAW, 'c'*64, 'drive')).step())
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['status'], 'changed')
        self.assertEqual(self.store.state['photos'], original)
        self.assertEqual(self.analyzed, [])
        self.assertFalse((self.root/'face-backfill').exists())

    def test_transient_original_errors_retry_at_most_three_attempts(self):
        reads = []
        def unavailable(pid):
            reads.append(pid)
            raise OriginalError('retryable', '雲端暫時忙碌')
        with patch('backfill_jobs.time.time', return_value=1000.):
            self.queue()
            self.assertTrue(self.runner(original=unavailable).step())
            job = self.store.state['backfill']['jobs'][PHOTO]
            self.assertEqual(job['status'], 'queued')
            self.assertEqual(job['retry_at'], 1030.)
            self.assertFalse(self.runner(original=unavailable).step())
        with patch('backfill_jobs.time.time', return_value=1031.):
            self.assertTrue(self.runner(original=unavailable).step())
            self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['retry_at'], 1091.)
        with patch('backfill_jobs.time.time', return_value=1092.):
            self.assertTrue(self.runner(original=unavailable).step())
            self.assertFalse(self.runner(original=unavailable).step())
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['status'], 'failed')
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['attempts'], 3)
        self.assertEqual(len(reads), 3)

    def test_ready_candidate_partial_state_and_artifacts_survive_restart_without_reanalysis(self):
        candidate = self.ready(analyze=lambda raw, seed: fake_analysis(seed, partial=True))
        self.assertTrue(candidate['analysis']['partial'])
        self.assertEqual(candidate['analysis']['partial_reason'], 'tile_limit')
        path = self.root/'face-backfill'/'candidates'/f'{candidate["id"]}.json'
        self.assertEqual(json.loads(path.read_text()), candidate)
        self.assertEqual(candidate_fingerprint(candidate), candidate['id'])
        self.store.restart()
        self.assertFalse(self.store.commit(lambda state: recover_jobs(state, KEY)))
        self.assertFalse(self.runner().step())
        self.assertEqual(self.queue()['queued'], 0)
        self.assertFalse(self.runner().step())
        self.assertEqual(self.original_reads, [PHOTO])

    def test_automatic_metadata_preserves_heads_corrections_aliases_history_and_cache_key(self):
        before = copy.deepcopy(self.store.state)
        cache = SearchCache()
        snapshot = cache.get(self.store.state)
        matches = snapshot.search([1., 0., 0., 0.])
        candidate = self.ready()
        after = self.store.state
        for key in ('people', 'merge_history', 'aliases', 'admin_history', 'embedding_revision'):
            self.assertEqual(after[key], before[key])
        for old, current in zip(before['photos'][PHOTO]['faces'], after['photos'][PHOTO]['faces']):
            for key, value in old.items():
                self.assertEqual(current[key], value)
        self.assertTrue(after['photos'][PHOTO]['faces'][0]['manual_confirmed'])
        self.assertIn(candidate['id'], after['photos'][PHOTO]['faces'][0]['backfill_metadata'])
        self.assertEqual(len(after['photos'][PHOTO]['faces']), len(before['photos'][PHOTO]['faces']))
        self.assertIs(cache.get(after), snapshot)
        self.assertEqual(cache.get(after).search([1., 0., 0., 0.]), matches)
        candidate_basis(after, after['backfill']['jobs'][PHOTO])

    def test_approved_new_faces_append_only_and_stay_provisional_without_representatives(self):
        candidate = self.ready()
        before = copy.deepcopy(self.store.state)
        old_cache = SearchCache()
        snapshot = old_cache.get(before)
        result = self.accept(candidate)
        after = self.store.state
        self.assertEqual(result['added_count'], 2)
        count = candidate['original_count']
        self.assertEqual(after['photos'][PHOTO]['faces'][:count], before['photos'][PHOTO]['faces'])
        for pid, person in before['people'].items():
            self.assertEqual(after['people'][pid], person)
        for pid, fid in zip(result['person_ids'], result['face_ids']):
            person = after['people'][pid]
            self.assertTrue(person['provisional'])
            self.assertEqual(person['samples'], [])
            self.assertEqual(person['representatives'], [])
            self.assertEqual(person['photo_ids'], [PHOTO])
            observation = next(f for f in after['photos'][PHOTO]['faces'] if f['face_id'] == fid)
            self.assertEqual(observation['person_id'], pid)
            self.assertFalse(observation['matching']['sample_eligible'])
            self.assertEqual(observation['backfill_candidate'], candidate['id'])
        self.assertEqual(after['merge_history'], before['merge_history'])
        self.assertEqual(after['aliases'], before['aliases'])
        self.assertEqual(after['embedding_revision'], before['embedding_revision']+1)
        self.assertIsNot(old_cache.get(after), snapshot)
        self.assertEqual(old_cache.get(after).search([0., 0., 0., 1.])[0]['id'], PHOTO)

    def test_multiple_approval_steps_and_suffix_undo_keep_earlier_history_intact(self):
        candidate = self.ready()
        before = copy.deepcopy(self.store.state)
        ids = [face['candidate_face_id'] for face in candidate['new_faces']]
        first = self.accept(candidate, ids[:1])
        second = self.accept(candidate, ids[1:])
        self.assertEqual(len(self.store.state['photos'][PHOTO]['faces']), candidate['original_count']+2)
        with self.assertRaises(ValueError):
            self.undo(first['operation_id'])
        with self.assertRaises(ValueError):
            self.store.commit(lambda state: undo_merge(state, self.old_merge['merge_id']))
        self.undo(second['operation_id'])
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['accepted'], {ids[0]: ids[0]})
        self.assertEqual(len(self.store.state['photos'][PHOTO]['faces']), candidate['original_count']+1)
        self.undo(first['operation_id'])
        self.assertEqual(self.store.state['photos'][PHOTO]['faces'], before['photos'][PHOTO]['faces'])
        self.assertEqual(self.store.state['admin_history'], before['admin_history'])
        self.assertEqual(self.store.state['merge_history'], before['merge_history'])
        self.assertEqual(self.store.state['aliases'], before['aliases'])
        self.assertEqual(latest_operation(self.store.state)['id'], self.old_correction['operation_id'])
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['accepted'], {})
        for pid in first['person_ids']+second['person_ids']:
            self.assertTrue(self.store.state['people'][pid]['empty'])
        # A reverted proposal is eligible for an explicit later confirmation again.
        self.accept(candidate, ids[:1])

    def test_suffix_undo_invalidates_search_snapshot_and_restores_result_set(self):
        candidate = self.ready()
        cache = SearchCache()
        original = cache.get(self.store.state).search([0., 0., 0., 1.])
        result = self.accept(candidate)
        added = cache.get(self.store.state)
        self.assertNotEqual(added.search([0., 0., 0., 1.]), original)
        self.undo(result['operation_id'])
        self.assertIsNot(cache.get(self.store.state), added)
        self.assertEqual(cache.get(self.store.state).search([0., 0., 0., 1.]), original)

    def test_undo_keeps_later_photos_on_the_confirmed_new_person(self):
        candidate = self.ready()
        selected = candidate['new_faces'][0]['candidate_face_id']
        added = self.accept(candidate, [selected])
        person_id = added['person_ids'][0]
        review = next(item for item in build_reviews(self.store.state)['reviews']
                      if item['kind'] == 'pending' and item['face_ids'] == [selected])
        approved = self.store.commit(lambda state: resolve_review(state, review['id'], 'approve'))
        self.store.commit(lambda state: add_photo(state, 'later', 'later.jpg',
                          [old_face([0, 0, 20, 20], [0., 0., 0., 1.], 'later')]))
        self.assertEqual(self.store.state['photos']['later']['faces'][0]['person_id'], person_id)
        self.undo(approved['operation_id'])
        self.undo(added['operation_id'])
        self.assertEqual(self.store.state['people'][person_id]['photo_ids'], ['later'])
        self.assertFalse(self.store.state['people'][person_id]['empty'])
        self.assertEqual(self.store.state['photos']['later']['faces'][0]['person_id'], person_id)
        self.assertEqual(len(self.store.state['photos'][PHOTO]['faces']), candidate['original_count'])

    def test_undo_rejects_a_non_suffix_without_removing_unrelated_observations(self):
        candidate = self.ready()
        added = self.accept(candidate)
        def append_unrelated(state):
            copied = copy.deepcopy(state['photos'][PHOTO]['faces'][-1])
            copied['face_id'] = 'unrelated-observation'
            state['photos'][PHOTO]['faces'].append(copied)
        self.store.commit(append_unrelated)
        before = copy.deepcopy(self.store.state)
        with self.assertRaisesRegex(ValueError, '尾端'):
            self.undo(added['operation_id'])
        self.assertEqual(self.store.state, before)

    def test_later_old_face_correction_invalidates_remaining_approvals(self):
        candidate = self.ready()
        ids = [face['candidate_face_id'] for face in candidate['new_faces']]
        self.accept(candidate, ids[:1])
        selected = face_id(PHOTO, 0, self.store.state['photos'][PHOTO]['faces'][0])
        self.store.commit(lambda state: reassign_faces(state, [selected], self.person_ids[1]))
        before = copy.deepcopy(self.store.state)
        with self.assertRaisesRegex(ValueError, '更新'):
            self.accept(candidate, ids[1:])
        self.assertEqual(self.store.state, before)

    def test_candidate_or_model_tampering_rejects_accept_without_partial_append(self):
        candidate = self.ready()
        ids = [face['candidate_face_id'] for face in candidate['new_faces']]
        before = copy.deepcopy(self.store.state)
        with self.assertRaises(ValueError):
            self.store.commit(lambda state: accept_faces(state, candidate['id'], ids, 'changed-model'))
        self.assertEqual(self.store.state, before)
        def convert_asset_to_url(state):
            state['backfill']['jobs'][PHOTO]['candidate']['new_faces'][0]['face']['crop'] = '/api/admin/backfill/crop.jpg'
        self.store.commit(convert_asset_to_url)
        before = copy.deepcopy(self.store.state)
        with self.assertRaisesRegex(ValueError, '內容已變更'):
            self.accept(candidate)
        self.assertEqual(self.store.state, before)

    def test_duplicate_unknown_or_already_accepted_ids_reject_without_partial_append(self):
        candidate = self.ready()
        ids = [face['candidate_face_id'] for face in candidate['new_faces']]
        for selected in ([], ['unknown'], [ids[0], ids[0]]):
            with self.subTest(selected=selected):
                before = copy.deepcopy(self.store.state)
                with self.assertRaises(ValueError):
                    self.accept(candidate, selected)
                self.assertEqual(self.store.state, before)
        self.accept(candidate, ids[:1])
        before = copy.deepcopy(self.store.state)
        with self.assertRaises(ValueError):
            self.accept(candidate, ids)
        self.assertEqual(self.store.state, before)

    def test_artifact_failure_does_not_publish_metadata_or_candidate_state(self):
        self.queue()
        before = copy.deepcopy(self.store.state)
        with patch('backfill_jobs.atomic_image', side_effect=OSError('disk full')):
            self.assertTrue(self.runner().step())
        after = self.store.state
        self.assertEqual(after['photos'], before['photos'])
        self.assertEqual(after['people'], before['people'])
        self.assertEqual(after['admin_history'], before['admin_history'])
        self.assertEqual(after['embedding_revision'], before['embedding_revision'])
        job = after['backfill']['jobs'][PHOTO]
        self.assertEqual(job['status'], 'failed')
        self.assertNotIn('candidate', job)
        self.assertNotIn('run_token', job)

    def test_finish_commit_failure_leaves_original_index_intact_and_retryable_after_restart(self):
        self.queue()
        before = copy.deepcopy(self.store.state)
        # Queue commit is first, claim is second, finish must fail exactly once.
        self.store.fail_on.add(self.store.calls+2)
        self.assertTrue(self.runner().step())
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['status'], 'failed')
        self.assertEqual(self.store.state['photos'], before['photos'])
        self.assertEqual(self.store.state['people'], before['people'])
        self.assertEqual(self.store.state['admin_history'], before['admin_history'])
        self.assertEqual(self.store.state['embedding_revision'], before['embedding_revision'])
        self.store.restart()
        self.store.commit(lambda state: start_jobs(state, KEY, [PHOTO], retry=True))
        self.assertTrue(self.runner().step())
        self.assertEqual(self.store.state['backfill']['jobs'][PHOTO]['status'], 'ready')

    def test_failed_claim_commit_leaves_queue_and_does_not_read_original(self):
        self.queue()
        before = copy.deepcopy(self.store.state)
        self.store.fail_on.add(self.store.calls+1)
        with self.assertRaises(OSError):
            self.runner().step()
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.original_reads, [])
        self.store.restart()
        self.assertTrue(self.runner().step())

    def test_stale_old_face_changed_during_analysis_is_not_published(self):
        self.queue()
        def analyze(raw, seed):
            selected = face_id(PHOTO, 0, self.store.state['photos'][PHOTO]['faces'][0])
            self.store.commit(lambda state: reassign_faces(state, [selected], self.person_ids[1]))
            return fake_analysis(seed)
        self.assertTrue(self.runner(analyze=analyze).step())
        job = self.store.state['backfill']['jobs'][PHOTO]
        self.assertEqual(job['status'], 'stale')
        self.assertNotIn('candidate', job)
        self.assertNotIn('backfill_metadata', self.store.state['photos'][PHOTO]['faces'][0])
        self.assertEqual(self.store.state['photos'][PHOTO]['faces'][0]['person_id'], self.person_ids[1])

    def test_replaced_job_run_token_cannot_publish_old_pipeline_results(self):
        self.queue()
        before = copy.deepcopy(self.store.state['photos'])
        def analyze(raw, seed):
            self.store.commit(lambda state: start_jobs(state, 'replacement-model', [PHOTO]))
            return fake_analysis(seed)
        self.assertTrue(self.runner(analyze=analyze).step())
        job = self.store.state['backfill']['jobs'][PHOTO]
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['pipeline_key'], 'replacement-model')
        self.assertNotIn('candidate', job)
        self.assertEqual(self.store.state['photos'], before)

    def test_summary_pages_proposals_without_leaking_embeddings_or_mutating_state(self):
        candidate = self.ready(analyze=lambda raw, seed: fake_analysis(seed, partial=True))
        self.store.commit(lambda state: start_jobs(state, KEY, ['another']))
        before = copy.deepcopy(self.store.state)
        page = summary(self.store.state, limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['counts'], {'ready': 1, 'queued': 1})
        self.assertEqual(page['next_offset'], 1)
        self.assertEqual(page['jobs'][0]['new_faces'], 2)
        self.assertTrue(page['jobs'][0]['partial'])
        self.assertNotIn('embedding', json.dumps(page))
        self.assertEqual(self.store.state, before)
        selected = candidate['new_faces'][0]['candidate_face_id']
        self.accept(candidate, [selected])
        self.assertEqual(summary(self.store.state, limit=1)['jobs'][0]['new_faces'], 1)


if __name__ == '__main__':
    unittest.main()
