import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'face-service'))
from backfill_mapping import (apply_metadata, build_candidate, candidate_fingerprint,
                              photo_signature, validate_candidate)
from core import add_photo, empty_state, merge_people, undo_merge
from face_state import face_id
from reviews import reassign_faces, undo_correction


PHOTO = 'old-photo'
SHA = 'a'*64
PIPELINE = 'models-and-config-v1'


def old_face(box, vector=None, **fields):
    return {'bbox': box, 'embedding': vector or [1., 0., 0.], 'thumbnail': 'old.jpg',
            'quality': 10, **fields}


def detection(box, key='enhanced-face', **fields):
    return {'face_id': key, 'bbox': box, 'original_bbox': box,
            'coordinate_space': 'original-exif', 'embedding': [0., 1., 0.],
            'crop': key+'.jpg', 'thumbnail': 'new_'+key+'.jpg', 'quality': 200,
            'quality_metrics': {'sample_eligible': True, 'sharpness': 25.},
            'pipeline_version': 'yunet-sface-tiled-v1',
            'detector_version': 'yunet', 'recognizer_version': 'sface', 'source': 'tile:0:0',
            **fields}


def analysis(faces, size=(1000, 500), preview=(500, 250), **fields):
    return SimpleNamespace(faces=faces,
                           metadata={'pipeline': 'enhanced', 'pipeline_version': 'tiled-v1',
                                     'image_size': list(size), 'preview_size': list(preview),
                                     'partial': False, 'elapsed_seconds': 1., **fields})


class BackfillMappingTests(unittest.TestCase):
    def setUp(self):
        self.state = empty_state()
        add_photo(self.state, PHOTO, 'old.jpg', [old_face([50, 25, 30, 40])])
        # Exercise pre-P0 files where IDs and geometry metadata did not exist.
        self.state['photos'][PHOTO]['faces'][0].pop('face_id')

    def build(self, faces=None, **kwargs):
        return build_candidate(self.state, PHOTO,
                               analysis(faces or [detection([100, 50, 60, 80])]),
                               SHA, PIPELINE, preview_size=[500, 250], **kwargs)

    def test_preview_scale_maps_without_replacing_original_observation(self):
        before = copy.deepcopy(self.state)
        candidate = self.build()
        self.assertEqual(self.state, before)
        self.assertEqual(len(candidate['mapped']), 1)
        self.assertEqual(candidate['mapped'][0]['match_iou'], 1.)
        self.assertEqual(candidate['new_faces'], [])
        self.assertEqual(candidate['ambiguous'], [])
        self.assertEqual(apply_metadata(self.state, candidate), 1)
        face = self.state['photos'][PHOTO]['faces'][0]
        for key, value in before['photos'][PHOTO]['faces'][0].items():
            self.assertEqual(face[key], value)
        self.assertNotIn('quality_metrics', face)
        self.assertNotIn('crop', face)
        self.assertNotIn('coordinate_space', face)
        metadata = face['backfill_metadata'][candidate['id']]
        self.assertEqual(metadata['original_bbox'], [100, 50, 60, 80])
        self.assertEqual(metadata['coordinate_space'], 'original-exif')
        self.assertEqual(metadata['crop'], 'enhanced-face.jpg')
        self.assertEqual(self.state['people'], before['people'])
        self.assertEqual(self.state['revision'], before['revision'])
        self.assertEqual(self.state['embedding_revision'], before['embedding_revision'])

    def test_exif_oriented_coordinates_use_post_transpose_image_dimensions(self):
        self.state['photos'][PHOTO]['faces'][0]['bbox'] = [10, 40, 15, 20]
        candidate = build_candidate(self.state, PHOTO,
                                    analysis([detection([20, 80, 30, 40])],
                                             size=(180, 300), preview=(90, 150)),
                                    SHA, PIPELINE, preview_size=[90, 150])
        self.assertEqual(len(candidate['mapped']), 1)
        self.assertEqual(candidate['mapped'][0]['metadata']['image_size'], [180, 300])
        self.assertEqual(candidate['mapped'][0]['metadata']['original_bbox'], [20, 80, 30, 40])

    def test_existing_original_bbox_is_used_instead_of_reinterpreting_legacy_bbox(self):
        face = self.state['photos'][PHOTO]['faces'][0]
        face.update(original_bbox=[100, 50, 60, 80], image_size=[1000, 500],
                    coordinate_space='preview-exif', preview_size=[400, 200])
        # A misleading fallback must not override measured coordinates on an observation.
        candidate = build_candidate(self.state, PHOTO, analysis([detection([100, 50, 60, 80])]),
                                    SHA, PIPELINE, preview_size=[100, 50])
        self.assertEqual(candidate['mapped'][0]['match_iou'], 1.)

    def test_observation_preview_size_takes_priority_over_fallback(self):
        face = self.state['photos'][PHOTO]['faces'][0]
        face['preview_size'] = [500, 250]
        candidate = build_candidate(self.state, PHOTO, analysis([detection([100, 50, 60, 80])]),
                                    SHA, PIPELINE, preview_size=[100, 50])
        self.assertEqual(len(candidate['mapped']), 1)

    def test_existing_original_coordinate_and_missing_analysis_global_size(self):
        face = self.state['photos'][PHOTO]['faces'][0]
        face.update(bbox=[100, 50, 60, 80], coordinate_space='original-exif', image_size=[1000, 500])
        incoming = {'faces': [detection([100, 50, 60, 80], image_size=[1000, 500])],
                    'metadata': {'pipeline': 'enhanced'}}
        candidate = build_candidate(self.state, PHOTO, incoming, SHA, PIPELINE)
        self.assertEqual(candidate['mapped'][0]['match_iou'], 1.)
        self.assertEqual(candidate['analysis']['image_size'], [1000, 500])

    def test_one_old_face_between_two_overlapping_detections_is_ambiguous(self):
        candidate = self.build([detection([100, 50, 60, 80], 'left'),
                                detection([108, 50, 60, 80], 'right')])
        self.assertEqual(candidate['mapped'], [])
        self.assertEqual(candidate['new_faces'], [])
        self.assertEqual(candidate['ambiguous'][0]['reason'], 'ambiguous_mapping')
        before = copy.deepcopy(self.state)
        self.assertEqual(apply_metadata(self.state, candidate), 0)
        self.assertEqual(self.state, before)

    def test_two_old_neighbors_competing_for_one_detection_do_not_map(self):
        self.state['photos'][PHOTO]['faces'].append(
            old_face([54, 25, 30, 40], person_id='neighbor'))
        candidate = self.build()
        self.assertEqual(candidate['mapped'], [])
        self.assertEqual(candidate['new_faces'], [])
        self.assertTrue(all(r['reason'] == 'ambiguous_mapping' for r in candidate['ambiguous'][:2]))

    def test_clear_neighbor_mapping_is_one_to_one_and_preserves_old_order(self):
        self.state['photos'][PHOTO]['faces'].append(
            old_face([100, 25, 30, 40], person_id='neighbor'))
        candidate = self.build([detection([200, 50, 60, 80], 'right'),
                                detection([100, 50, 60, 80], 'left')])
        self.assertEqual([(r['original_index'], r['analysis_index']) for r in candidate['mapped']],
                         [(0, 1), (1, 0)])
        before = copy.deepcopy(self.state['photos'][PHOTO]['faces'])
        apply_metadata(self.state, candidate)
        self.assertEqual([f['bbox'] for f in self.state['photos'][PHOTO]['faces']],
                         [f['bbox'] for f in before])
        self.assertEqual([f['person_id'] for f in self.state['photos'][PHOTO]['faces']],
                         [f['person_id'] for f in before])

    def test_unmatched_old_face_is_never_removed_when_analysis_misses_it(self):
        candidate = build_candidate(self.state, PHOTO, analysis([]), SHA, PIPELINE,
                                    preview_size=[500, 250])
        self.assertEqual(candidate['original_count'], 1)
        self.assertEqual(candidate['ambiguous'][0]['reason'], 'unmatched_existing')
        before = copy.deepcopy(self.state)
        apply_metadata(self.state, candidate)
        self.assertEqual(self.state, before)

    def test_separate_new_detection_is_isolated_and_has_deterministic_provenance(self):
        candidate = self.build([detection([100, 50, 60, 80]),
                                detection([700, 200, 40, 50], 'new', person_id='untrusted')])
        self.assertEqual(len(candidate['mapped']), 1)
        self.assertEqual(len(candidate['new_faces']), 1)
        proposed = candidate['new_faces'][0]
        self.assertEqual(proposed['face']['face_id'], proposed['candidate_face_id'])
        self.assertEqual(proposed['face']['bbox'], [700, 200, 40, 50])
        self.assertEqual(proposed['face']['coordinate_space'], 'original-exif')
        self.assertNotIn('person_id', proposed['face'])
        self.assertEqual(proposed['provenance']['source_sha256'], SHA)
        self.assertEqual(proposed['provenance']['analysis_face_id'], 'new')
        apply_metadata(self.state, candidate)
        self.assertEqual(len(self.state['photos'][PHOTO]['faces']), 1)
        self.assertEqual(len(self.state['people']), 1)

    def test_tiny_or_shifted_detection_near_old_face_is_not_a_new_face(self):
        for box in ([110, 70, 10, 10], [159, 50, 60, 80], [165, 50, 60, 80]):
            with self.subTest(box=box):
                candidate = self.build([detection(box)])
                self.assertEqual(candidate['mapped'], [])
                self.assertEqual(candidate['new_faces'], [])
                self.assertTrue(any(r['reason'] == 'overlap_existing' for r in candidate['ambiguous']))

    def test_unknown_old_geometry_blocks_potentially_duplicate_new_faces(self):
        candidate = build_candidate(self.state, PHOTO, analysis([detection([100, 50, 60, 80])]),
                                    SHA, PIPELINE)
        self.assertEqual(candidate['mapped'], [])
        self.assertEqual(candidate['new_faces'], [])
        self.assertTrue(any(r['reason'] == 'existing_geometry_unavailable' for r in candidate['ambiguous']))

    def test_dimensions_or_unknown_coordinate_change_is_not_silently_rescaled(self):
        for fields in ({'image_size': [500, 1000]}, {'coordinate_space': 'raw-camera'}):
            with self.subTest(fields=fields):
                state = copy.deepcopy(self.state)
                state['photos'][PHOTO]['faces'][0].update(fields)
                candidate = build_candidate(state, PHOTO, analysis([detection([100, 50, 60, 80])]),
                                            SHA, PIPELINE, preview_size=[500, 250])
                self.assertEqual(candidate['mapped'], [])
                self.assertEqual(candidate['new_faces'], [])
                self.assertIn(candidate['ambiguous'][0]['reason'],
                              ('image_size_changed', 'unknown_coordinate_space'))

    def test_overlapping_new_detections_without_old_match_require_review(self):
        self.state['photos'][PHOTO]['faces'] = []
        candidate = self.build([detection([100, 50, 60, 80], 'one'),
                                detection([108, 50, 60, 80], 'two')])
        self.assertEqual(candidate['new_faces'], [])
        self.assertTrue(all(r['reason'] == 'overlapping_detections' for r in candidate['ambiguous']))

    def test_partial_analysis_is_explicit_and_does_not_remove_existing_faces(self):
        candidate = build_candidate(self.state, PHOTO,
                                    analysis([], partial=True, partial_reason='tile_limit', tiles_run=1),
                                    SHA, PIPELINE, preview_size=[500, 250])
        self.assertTrue(candidate['analysis']['partial'])
        self.assertEqual(candidate['analysis']['partial_reason'], 'tile_limit')
        self.assertEqual(candidate['analysis']['tiles_run'], 1)
        before = copy.deepcopy(self.state)
        apply_metadata(self.state, candidate)
        self.assertEqual(self.state, before)

    def test_candidate_id_is_deterministic_ignores_runtime_and_global_revision(self):
        first = self.build()
        self.state['revision'] += 5
        incoming = analysis([detection([100, 50, 60, 80])], elapsed_seconds=600.)
        second = build_candidate(self.state, PHOTO, incoming, SHA, PIPELINE,
                                 preview_size=[500, 250])
        self.assertEqual(first['id'], second['id'])
        self.assertNotEqual(first['base_revision'], second['base_revision'])
        self.assertNotIn('elapsed_seconds', second['analysis'])
        for source, pipeline, faces in [(SHA, 'v2', incoming.faces),
                                        ('b'*64, PIPELINE, incoming.faces),
                                        (SHA, PIPELINE, [detection([101, 50, 60, 80])])]:
            changed = build_candidate(self.state, PHOTO, analysis(faces), source, pipeline,
                                      preview_size=[500, 250])
            self.assertNotEqual(first['id'], changed['id'])
        self.assertEqual(json.loads(json.dumps(first)), first)

    def test_metadata_retries_and_restart_do_not_invalidate_original_signature(self):
        candidate = self.build()
        signature = photo_signature(self.state, PHOTO)
        self.assertEqual(apply_metadata(self.state, candidate), 1)
        self.assertEqual(photo_signature(self.state, PHOTO), signature)
        self.state = json.loads(json.dumps(self.state))
        self.assertTrue(validate_candidate(self.state, candidate))
        self.assertEqual(apply_metadata(self.state, candidate), 0)
        second = self.build()
        self.assertEqual(second['id'], candidate['id'])

    def test_null_legacy_id_is_materialized_without_replacing_real_ids(self):
        original = self.state['photos'][PHOTO]['faces'][0]
        original['face_id'] = None
        candidate = self.build()
        expected = face_id(PHOTO, 0, original)
        apply_metadata(self.state, candidate)
        self.assertEqual(original['face_id'], expected)
        self.assertTrue(validate_candidate(self.state, candidate))
        original['face_id'] = 'explicit-persisted-id'
        second = self.build()
        apply_metadata(self.state, second)
        self.assertEqual(original['face_id'], 'explicit-persisted-id')

    def test_metadata_addition_preserves_merge_alias_manual_correction_and_undo(self):
        add_photo(self.state, 'another', 'another.jpg', [old_face([0, 0, 20, 20], [0., 0., 1.])])
        ids = list(self.state['people'])
        merged = merge_people(self.state, ids, ids[0])
        moved = reassign_faces(self.state, [face_id(PHOTO, 0, self.state['photos'][PHOTO]['faces'][0])])
        candidate = self.build()
        before = copy.deepcopy(self.state)
        apply_metadata(self.state, candidate)
        for key in ('people', 'aliases', 'merge_history', 'admin_history', 'revision', 'embedding_revision'):
            self.assertEqual(self.state[key], before[key])
        self.assertTrue(self.state['photos'][PHOTO]['faces'][0]['manual_confirmed'])
        undo_correction(self.state, moved['operation_id'])
        undo_merge(self.state, merged['merge_id'])
        self.assertEqual(self.state['photos'][PHOTO]['faces'][0]['person_id'], ids[0])
        self.assertTrue(all(pid in self.state['people'] for pid in ids))
        self.assertTrue(self.state['people'][moved['person_id']]['empty'])

    def test_changed_old_evidence_rejects_candidate_without_mutation(self):
        candidate = self.build()
        for key, value in [('embedding', [0., 1., 0.]), ('bbox', [51, 25, 30, 40]),
                           ('person_id', 'corrected'), ('manual_confirmed', True),
                           ('coordinate_space', 'original-exif')]:
            with self.subTest(key=key):
                state = copy.deepcopy(self.state)
                state['photos'][PHOTO]['faces'][0][key] = value
                before = copy.deepcopy(state)
                with self.assertRaisesRegex(ValueError, '更新'):
                    apply_metadata(state, candidate)
                self.assertEqual(state, before)
        state = copy.deepcopy(self.state)
        state['photos'][PHOTO]['faces'].append(old_face([300, 100, 20, 20], person_id='new'))
        with self.assertRaises(ValueError):
            validate_candidate(state, candidate)

    def test_changed_detection_content_rejects_integrity_check(self):
        candidate = self.build()
        candidate['mapped'][0]['metadata']['crop'] = 'tampered.jpg'
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, '內容已變更'):
            apply_metadata(self.state, candidate)
        self.assertEqual(self.state, before)

    def test_additive_metadata_conflict_does_not_partially_update_faces(self):
        self.state['photos'][PHOTO]['faces'].append(old_face([100, 25, 30, 40], person_id='neighbor'))
        candidate = self.build([detection([100, 50, 60, 80]), detection([200, 50, 60, 80], 'neighbor')])
        self.state['photos'][PHOTO]['faces'][1]['backfill_metadata'] = {candidate['id']: {'wrong': True}}
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            apply_metadata(self.state, candidate)
        self.assertEqual(self.state, before)

    def test_invalid_input_and_detection_geometry_are_rejected_conservatively(self):
        for source, pipeline in [('not-a-hash', PIPELINE), (SHA, '')]:
            with self.assertRaises(ValueError):
                build_candidate(self.state, PHOTO, analysis([]), source, pipeline)
        with self.assertRaises(ValueError):
            build_candidate(self.state, 'missing', analysis([]), SHA, PIPELINE)
        with self.assertRaises(ValueError):
            self.build([detection([float('nan'), 0, 10, 10])])
        candidate = self.build([detection([0, 0, -1, 10])])
        self.assertEqual(candidate['new_faces'], [])
        self.assertTrue(any(r['reason'] == 'invalid_detection_geometry' for r in candidate['ambiguous']))


if __name__ == '__main__':
    unittest.main()
