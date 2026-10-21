import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'face-service'))
from core import add_photo, empty_state, merge_people, undo_merge, resolve_person
from face_state import face_map, revision
from reviews import (build_reviews, operation_history, reassign_faces, resolve_review,
                     revoke_constraint, undo_correction)


def face(vector, thumbnail, quality=100):
    return {'embedding': vector, 'thumbnail': thumbnail, 'quality': quality, 'bbox': [0,0,20,20]}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.state = empty_state()
        add_photo(self.state, 'photo-a', 'a', [face([1,0,0], 'a.jpg'), face([.9,.435,0], 'b.jpg')])
        add_photo(self.state, 'photo-b', 'b', [face([0,0,1], 'c.jpg')])
        self.ids = list(self.state['people'])
        self.faces = list(face_map(self.state))

    def duplicate(self):
        return next(r for r in build_reviews(self.state)['reviews'] if r['kind']=='duplicate')

    def test_get_reviews_is_read_only_and_legacy_ids_are_stable(self):
        for photo in self.state['photos'].values():
            for row in photo['faces']:
                row.pop('face_id', None)
        before = copy.deepcopy(self.state)
        catalog = build_reviews(self.state)
        again = build_reviews(self.state)
        self.assertEqual(catalog, again)
        self.assertEqual(self.state, before)
        self.assertEqual(len(face_map(self.state)), 3)
        self.assertNotIn('embedding', catalog['reviews'][0]['faces'][0])

    def test_move_only_selected_face_rebuilds_source_and_keeps_same_photo_neighbor(self):
        original_neighbor = copy.deepcopy(self.state['photos']['photo-a']['faces'][1])
        old_merge_history = copy.deepcopy(self.state.get('merge_history'))
        result = reassign_faces(self.state, [self.faces[0]], self.ids[2])
        self.assertEqual(result['moved_count'], 1)
        self.assertEqual(self.state['photos']['photo-a']['faces'][1], original_neighbor)
        self.assertEqual(self.state['photos']['photo-a']['faces'][0]['person_id'], self.ids[2])
        self.assertEqual(self.state['people'][self.ids[0]]['photo_ids'], [])
        self.assertEqual(self.state['people'][self.ids[0]]['samples'], [])
        self.assertTrue(self.state['people'][self.ids[0]]['empty'])
        self.assertEqual(self.state.get('merge_history'), old_merge_history)
        self.assertEqual(self.state['people'][self.ids[2]]['photo_ids'], ['photo-a','photo-b'])

    def test_wrong_anchor_removed_but_untraceable_legacy_anchor_is_preserved(self):
        # Put two faces on A by a manual merge, then correct one face without undoing the merge.
        merge_people(self.state, self.ids[:2], self.ids[0])
        self.state['people'][self.ids[0]]['samples'].append([0, .2, .98])
        history = copy.deepcopy(self.state['merge_history'])
        reassign_faces(self.state, [self.faces[0]], self.ids[2])
        source = self.state['people'][self.ids[0]]
        self.assertNotIn([1,0,0], source['samples'])
        self.assertIn([0,.2,.98], source['samples'])
        self.assertEqual(source['thumbnail'], 'b.jpg')
        self.assertFalse(source['thumbnail_locked'])
        self.assertEqual(self.state['merge_history'], history)
        self.assertEqual(resolve_person(self.state, self.ids[1]), self.ids[0])

    def test_global_undo_order_blocks_old_merge_then_restores_old_index_positions(self):
        merged = merge_people(self.state, self.ids[:2], self.ids[0])
        moved = reassign_faces(self.state, [self.faces[1]], self.ids[2])
        with self.assertRaises(ValueError):
            undo_merge(self.state, merged['merge_id'])
        undo_correction(self.state, moved['operation_id'])
        undo_merge(self.state, merged['merge_id'])
        self.assertEqual([f['person_id'] for f in self.state['photos']['photo-a']['faces']], self.ids[:2])
        self.assertEqual(self.state['aliases'], {})

    def test_original_legacy_merge_history_needs_no_migration(self):
        merged = merge_people(self.state, self.ids[:2], self.ids[0])
        self.state.pop('admin_history')
        history = copy.deepcopy(self.state['merge_history'])
        moved = reassign_faces(self.state, [self.faces[1]], self.ids[2])
        self.assertEqual(self.state['merge_history'], history)
        self.assertFalse(next(h for h in operation_history(self.state) if h['id']==merged['merge_id'])['can_undo'])
        undo_correction(self.state, moved['operation_id'])
        undo_merge(self.state, merged['merge_id'])
        self.assertEqual(len(self.state['people']), 3)

    def test_undo_keeps_new_photos_on_the_current_target_and_survives_restart(self):
        moved = reassign_faces(self.state, [self.faces[0]])
        target = moved['person_id']
        add_photo(self.state, 'later', 'later', [face([1,0,0], 'later.jpg')])
        # The explicitly corrected cluster recruits this new photo using legacy matching.
        self.assertEqual(self.state['photos']['later']['faces'][0]['person_id'], target)
        self.state = json.loads(json.dumps(self.state))
        undo_correction(self.state, moved['operation_id'])
        self.assertEqual(self.state['photos']['photo-a']['faces'][0]['person_id'], self.ids[0])
        self.assertIn('later', self.state['people'][target]['photo_ids'])

    def test_different_excludes_suggestion_and_blocks_merge_until_revoked(self):
        review = self.duplicate()
        rejected = resolve_review(self.state, review['id'], 'different')
        self.assertFalse(any(r['kind']=='duplicate' for r in build_reviews(self.state)['reviews']))
        with self.assertRaises(ValueError):
            merge_people(self.state, self.ids[:2], self.ids[0])
        revoked = revoke_constraint(self.state, rejected['constraint_id'])
        self.assertTrue(any(r['kind']=='duplicate' for r in build_reviews(self.state)['reviews']))
        undo_correction(self.state, revoked['operation_id'])
        with self.assertRaises(ValueError):
            merge_people(self.state, self.ids[:2], self.ids[0])
        undo_correction(self.state, rejected['operation_id'])
        merge_people(self.state, self.ids[:2], self.ids[0])

    def test_batch_move_cannot_join_two_explicitly_different_faces(self):
        rejected = resolve_review(self.state, self.duplicate()['id'], 'different')
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            reassign_faces(self.state, self.faces[:2])
        self.assertEqual(self.state, before)
        with self.assertRaises(ValueError):
            reassign_faces(self.state, [self.faces[0]], self.ids[1])

    def test_skip_invalidates_when_that_person_receives_new_evidence(self):
        review = self.duplicate()
        resolve_review(self.state, review['id'], 'skip')
        self.assertFalse(any(r['kind']=='duplicate' for r in build_reviews(self.state)['reviews']))
        add_photo(self.state, 'later', 'later', [face([1,0,0], 'later.jpg')])
        self.assertTrue(any(r['kind']=='duplicate' for r in build_reviews(self.state)['reviews']))

    def test_confirm_low_quality_does_not_turn_bad_embedding_into_a_representative(self):
        bad = face([0,1,0], 'bad.jpg'); bad['quality_metrics']={'sample_eligible':False}
        add_photo(self.state, 'bad', 'bad', [bad], mode='enhanced')
        pending = next(r for r in build_reviews(self.state)['reviews'] if r['kind']=='pending')
        pid = pending['person_ids'][0]
        approved = resolve_review(self.state, pending['id'], 'approve')
        self.assertFalse(self.state['people'][pid]['provisional'])
        self.assertEqual(self.state['people'][pid]['representatives'], [])
        self.assertEqual(self.state['people'][pid]['samples'], [])
        self.assertFalse(any(r['kind']=='pending' for r in build_reviews(self.state)['reviews']))
        undo_correction(self.state, approved['operation_id'])
        self.assertTrue(self.state['people'][pid]['provisional'])
        self.assertEqual(self.state['people'][pid]['samples'], [])
        self.assertEqual(self.state['people'][pid]['representatives'], [])
        # An older enhanced index may still archive the bad vector in samples.
        self.state['people'][pid]['samples'].append([0,1,0])
        add_photo(self.state, 'later-bad', 'later-bad', [face([0,1,0], 'new-bad.jpg')])
        self.assertNotEqual(self.state['photos']['later-bad']['faces'][0]['person_id'], pid)

    def test_empty_original_id_can_receive_faces_again_without_deleting_links(self):
        moved = reassign_faces(self.state, [self.faces[0]], self.ids[2])
        reassign_faces(self.state, [self.faces[0]], self.ids[0])
        self.assertFalse(self.state['people'][self.ids[0]]['empty'])
        self.assertEqual(resolve_person(self.state, self.ids[0]), self.ids[0])
        self.assertTrue(self.state['people'][self.ids[0]]['samples'])

    def test_invalid_request_does_not_change_legacy_records(self):
        before = copy.deepcopy(self.state)
        for ids, target in [(['missing'], None), ([self.faces[0], self.faces[0]], None), ([self.faces[0]], 'missing')]:
            with self.assertRaises(ValueError):
                reassign_faces(self.state, ids, target)
            self.assertEqual(self.state, before)


if __name__ == '__main__':
    unittest.main()
