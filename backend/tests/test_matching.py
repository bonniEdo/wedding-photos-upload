import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'face-service'))
from core import add_photo, atomic_json, empty_state, merge_people, undo_merge, resolve_person
from matching import MatchConfig, assign_photo, representatives, sample_record, select_representatives


def face(vector, key='f', good=True, sharpness=100):
    return {'face_id': key, 'embedding': vector, 'thumbnail': key+'.jpg', 'bbox': [0, 0, 50, 50],
            'quality': 100, 'quality_metrics': {'sample_eligible': good, 'detector_score': .99,
            'sharpness': sharpness, 'original_face_size': [50, 50]}}


class MatchingTests(unittest.TestCase):
    def seed(self, vectors):
        state = empty_state()
        add_photo(state, 'seed', 'seed', [face(v, str(i)) for i, v in enumerate(vectors)])
        return state

    def test_fixed_candidates_do_not_promote_weak_second_choice(self):
        state = self.seed([[1, 0], [.8, .6]])
        ids = list(state['people'])
        faces = [face([1, 0], 'strong'), face([.98, .2], 'ambiguous')]
        decisions = assign_photo(state['people'], faces)
        self.assertEqual(decisions[0]['person_id'], ids[0])
        self.assertIsNone(decisions[1]['person_id'])
        self.assertEqual(decisions[1]['reason'], 'ambiguous')
        self.assertEqual(len(decisions[1]['candidates']), 2)

    def test_competition_is_order_invariant_and_never_uses_second_person(self):
        state = self.seed([[1, 0], [0, 1]])
        faces = [face([1, 0], 'a'), face([.96, .28], 'b')]
        expected = assign_photo(state['people'], faces)
        actual = list(reversed(assign_photo(state['people'], list(reversed(faces)))))
        self.assertEqual(actual, expected)
        self.assertTrue(all(d['person_id'] is None for d in expected))
        self.assertTrue(all(d['reason'] == 'same_photo_conflict' for d in expected))

    def test_clear_competition_winner_is_deterministic(self):
        state = self.seed([[1, 0]])
        faces = [face([1, 0], 'a'), face([.8, .6], 'b')]
        decisions = assign_photo(state['people'], faces)
        self.assertIsNotNone(decisions[0]['person_id'])
        self.assertIsNone(decisions[1]['person_id'])
        self.assertEqual(decisions[1]['reason'], 'same_photo_conflict')

    def test_same_photo_new_person_never_recruits_another_face(self):
        state = empty_state()
        add_photo(state, 'a', 'a', [face([1, 0], 'a'), face([1, 0], 'b')], mode='enhanced')
        self.assertEqual(len(state['people']), 2)
        self.assertEqual(len({f['person_id'] for f in state['photos']['a']['faces']}), 2)

    def test_low_quality_is_provisional_and_cannot_recruit(self):
        state = empty_state()
        add_photo(state, 'a', 'a', [face([1, 0], good=False)], mode='enhanced')
        person = next(iter(state['people'].values()))
        self.assertTrue(person['provisional'])
        self.assertEqual(person['representatives'], [])
        add_photo(state, 'b', 'b', [face([1, 0], 'b')], mode='enhanced')
        self.assertEqual(len(state['people']), 2)

    def test_weakly_matched_face_does_not_expand_representatives(self):
        state = self.seed([[1, 0]])
        pid = next(iter(state['people']))
        add_photo(state, 'later', 'later', [face([.68, .7332], 'later')], mode='enhanced')
        observation = state['photos']['later']['faces'][0]
        self.assertEqual(observation['person_id'], pid)
        self.assertFalse(observation['matching']['sample_eligible'])
        self.assertEqual(state['people'][pid]['samples'], [[1, 0]])

    def test_shadow_has_legacy_assignments_without_mutating_old_records(self):
        state = self.seed([[1, 0], [0, 1]])
        expected = copy.deepcopy(state)
        old_photo = copy.deepcopy(state['photos']['seed'])
        ids = list(state['people'])
        history = merge_people(state, ids, ids[0])
        merge_snapshot = copy.deepcopy(state['merge_history'])
        aliases = copy.deepcopy(state['aliases'])
        expected = copy.deepcopy(state)
        faces = [face([1, 0], 'query')]
        add_photo(expected, 'later', 'later', faces)
        add_photo(state, 'later', 'later', faces, mode='shadow')
        self.assertEqual(state['people'], expected['people'])
        self.assertEqual(state['photos']['later']['faces'], expected['photos']['later']['faces'])
        self.assertIn('matching_shadow', state['photos']['later'])
        self.assertEqual(state['merge_history'], merge_snapshot)
        self.assertEqual(state['aliases'], aliases)
        self.assertEqual(state['photos']['seed']['faces'][0]['bbox'], old_photo['faces'][0]['bbox'])
        self.assertEqual(resolve_person(state, ids[1]), ids[0])
        undo_merge(state, history['merge_id'])

    def test_quality_and_diversity_replace_first_arrivals(self):
        records = [sample_record(face([1, 0], 'old', sharpness=20), 'g'),
                   sample_record(face([1, 0], 'clear', sharpness=500), 'g'),
                   sample_record(face([.8, .6], 'side'), 'g')]
        chosen = select_representatives(records, 2)
        self.assertEqual({r['face_id'] for r in chosen}, {'clear', 'side'})
        self.assertEqual(records[0]['face_id'], 'old')

    def test_shadow_matching_failure_keeps_legacy_ingestion(self):
        state = self.seed([[1, 0]])
        pid = next(iter(state['people']))
        with patch('core.assign_photo', side_effect=RuntimeError('shadow failed')):
            add_photo(state, 'later', 'later', [face([1, 0], 'later')], mode='shadow')
        self.assertEqual(state['photos']['later']['faces'][0]['person_id'], pid)
        self.assertEqual(state['photos']['later']['matching_shadow']['error'], 'RuntimeError')

    def test_manual_merge_modes_survive_restart_addition_and_undo(self):
        state = empty_state()
        add_photo(state, 'a', 'a', [face([1, 0, 0], 'a')], mode='enhanced')
        add_photo(state, 'b', 'b', [face([0, 1, 0], 'b')], mode='enhanced')
        ids = list(state['people'])
        result = merge_people(state, ids, ids[0])
        snapshot = copy.deepcopy(state['merge_history'])
        self.assertEqual(len({r['source_group'] for r in state['people'][ids[0]]['representatives']}), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'index.json'; atomic_json(path, state)
            state = json.loads(path.read_text())
        add_photo(state, 'c', 'c', [face([0, 1, 0], 'c')], mode='enhanced')
        self.assertEqual(state['photos']['c']['faces'][0]['person_id'], ids[0])
        self.assertEqual(state['people'][ids[0]]['thumbnail'], 'a.jpg')
        self.assertEqual(state['merge_history'], snapshot)
        undo_merge(state, result['merge_id'])
        self.assertEqual(set(state['people']), set(ids))
        self.assertIn('c', state['people'][ids[0]]['photo_ids'])
        self.assertNotIn('c', state['people'][ids[1]]['photo_ids'])
        self.assertEqual(state['people'][ids[0]]['thumbnail'], 'a.jpg')

    def test_duplicate_photo_is_noop_and_metadata_is_preserved(self):
        state = empty_state()
        raw = face([1, 0], 'f'); raw['coordinate_space'] = 'original-exif'
        add_photo(state, 'a', 'a', [raw], mode='enhanced', analysis={'partial': True})
        before = copy.deepcopy(state)
        self.assertFalse(add_photo(state, 'a', 'a', [face([0, 1])], mode='enhanced'))
        self.assertEqual(state, before)
        self.assertEqual(state['photos']['a']['faces'][0]['coordinate_space'], 'original-exif')

    def test_configs_reject_nonfinite_or_relaxed_sample_floor(self):
        for kwargs in ({'threshold': float('nan')}, {'sample_threshold': .1}, {'single_threshold': .1}):
            with self.assertRaises(ValueError):
                MatchConfig(**kwargs)

    def test_choosing_provisional_thumbnail_does_not_hide_trusted_merged_mode(self):
        state = empty_state()
        add_photo(state, 'a', 'a', [face([1, 0], 'bad', good=False)], mode='enhanced')
        add_photo(state, 'b', 'b', [face([0, 1], 'good')], mode='enhanced')
        ids = list(state['people'])
        result = merge_people(state, ids, ids[0])
        self.assertFalse(state['people'][ids[0]]['provisional'])
        add_photo(state, 'c', 'c', [face([0, 1], 'later')], mode='enhanced')
        self.assertEqual(state['photos']['c']['faces'][0]['person_id'], ids[0])
        self.assertEqual(state['people'][ids[0]]['thumbnail'], 'bad.jpg')
        undo_merge(state, result['merge_id'])
        self.assertTrue(state['people'][ids[0]]['provisional'])

    def test_undo_preserves_snapshot_samples_not_present_in_old_observations(self):
        state = self.seed([[1, 0, 0], [0, 1, 0]])
        ids = list(state['people'])
        state['people'][ids[0]]['samples'].append([0, 0, 1])
        snapshot_samples = copy.deepcopy(state['people'][ids[0]]['samples'])
        result = merge_people(state, ids, ids[0])
        undo_merge(state, result['merge_id'])
        self.assertEqual(state['people'][ids[0]]['samples'], snapshot_samples)


if __name__ == '__main__':
    unittest.main()
