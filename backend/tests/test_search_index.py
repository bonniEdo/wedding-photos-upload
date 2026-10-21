import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'face-service'))
from core import add_photo, empty_state, merge_people, similarity
from reviews import reassign_faces
from face_state import face_map
from search_index import SearchCache, SearchSnapshot


def scalar(state, query, threshold):
    matches = []
    for pid, photo in state['photos'].items():
        score = max((similarity(query, f['embedding']) for f in photo['faces']), default=-1)
        if score >= threshold:
            matches.append({'id':pid, 'name':photo['name'], 'score':round(score,4), 'url':f'/api/photos/{pid}/image'})
    matches.sort(key=lambda r:r['score'], reverse=True)
    return matches


class SearchTests(unittest.TestCase):
    def fixture(self):
        rng = np.random.default_rng(42)
        photos = {}
        for index in range(150):
            vectors = rng.normal(size=(3,128)).astype(np.float32)
            vectors /= np.linalg.norm(vectors,axis=1,keepdims=True)
            photos[str(index)]={'name':str(index), 'faces':[{'embedding':v.tolist()} for v in vectors]}
        return {'photos':photos}

    def test_exact_matches_and_order_against_scalar_across_queries_and_thresholds(self):
        state = self.fixture(); snapshot = SearchSnapshot.build(state)
        queries = [state['photos'][str(i)]['faces'][0]['embedding'] for i in (0,20,70)]
        for query in queries:
            for threshold in (0., .1, .3, .5, 1.):
                self.assertEqual(snapshot.search(query,threshold), scalar(state,query,threshold))

    def test_threshold_boundary_and_rounded_ties_keep_legacy_semantics(self):
        query=[1.,0.]
        state={'photos':{str(i):{'name':str(i),'faces':[{'embedding':[v,np.sqrt(1-v*v)]}]} for i,v in enumerate([.3,.30000002,.29999998,.30001,.300009])}}
        snapshot=SearchSnapshot.build(state)
        for threshold in (.3,.30000001,0.):
            self.assertEqual(snapshot.search(query,threshold),scalar(state,query,threshold))
        self.assertEqual([m['id'] for m in snapshot.search(query,0.)],list(state['photos']))

    def test_empty_photo_and_zero_vectors(self):
        state={'photos':{'empty':{'name':'empty','faces':[]},'zero':{'name':'zero','faces':[{'embedding':[0.,0.]}]}}}
        snapshot=SearchSnapshot.build(state)
        self.assertEqual(snapshot.search([0,0],0),scalar(state,[0,0],0))
        self.assertEqual(snapshot.search([1,0],.3),[])

    def test_snapshot_copies_vectors_and_has_readonly_arrays(self):
        state=self.fixture(); snapshot=SearchSnapshot.build(state)
        query=state['photos']['0']['faces'][0]['embedding'][:]
        expected=snapshot.search(query)
        state['photos'].clear()
        self.assertEqual(snapshot.search(query),expected)
        with self.assertRaises(ValueError):
            snapshot.groups[128][0][0,0]=1

    def test_cache_updates_on_new_photos_but_reuses_after_merge_and_correction(self):
        state=empty_state()
        face=lambda v:{'embedding':v,'bbox':[0,0,10,10],'quality':100,'thumbnail':'face.jpg'}
        add_photo(state,'a','a',[face([1,0]),face([0,1])])
        cache=SearchCache(); first=cache.get(state)
        updated=copy.deepcopy(state)
        ids=list(updated['people']); merge_people(updated,ids,ids[0])
        self.assertIs(cache.get(updated),first)
        fid=next(iter(face_map(updated))); reassign_faces(updated,[fid])
        self.assertIs(cache.get(updated),first)
        add_photo(updated,'b','b',[face([1,0])])
        self.assertIsNot(cache.get(updated),first)
        self.assertEqual(len(cache.get(updated).search([1,0])),2)

    def test_concurrent_snapshot_searches_are_consistent(self):
        state=self.fixture(); cache=SearchCache(); query=state['photos']['0']['faces'][0]['embedding']
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:cache.get(state).search(query),range(12)))
        self.assertTrue(all(r==results[0] for r in results))

    def test_malformed_faces_are_skipped_without_dropping_valid_dimension(self):
        state={'photos':{'a':{'name':'a','faces':[{'embedding':[1,0]},{'embedding':[float('nan'),0]},{'embedding':[1,0,0]}]}}}
        snapshot=SearchSnapshot.build(state)
        self.assertEqual(snapshot.invalid_faces,1)
        self.assertEqual(snapshot.search([1,0])[0]['id'],'a')


if __name__=='__main__':
    unittest.main()
