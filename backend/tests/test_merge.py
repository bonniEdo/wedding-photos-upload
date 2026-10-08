import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'face-service'))
from core import add_photo,empty_state,merge_people,undo_merge,resolve_person,atomic_json

def face(v, thumb, quality=100):return {'embedding':v,'thumbnail':thumb,'quality':quality,'bbox':[0,0,10,10]}
class MergeTests(unittest.TestCase):
    def setUp(self):
        self.state=empty_state();add_photo(self.state,'a','a',[face([1,0,0],'a.jpg'),face([0,1,0],'b.jpg')]);
        self.ids=list(self.state['people']);add_photo(self.state,'b','b',[face([0,0,1],'c.jpg')]);self.third=list(self.state['people'])[-1]
    def test_merge_deduplicates_photo_preserves_samples_and_refs(self):
        s=self.state;result=merge_people(s,self.ids,self.ids[1]);p=s['people'][self.ids[1]]
        self.assertEqual(p['photo_ids'],['a']);self.assertEqual(p['thumbnail'],'b.jpg');self.assertEqual(len(p['samples']),2)
        self.assertEqual(len(s['people']),2);self.assertTrue(all(f['person_id']==self.ids[1] for f in s['photos']['a']['faces']))
        self.assertEqual(resolve_person(s,self.ids[0]),self.ids[1]);self.assertEqual(result['count'],1)
    def test_new_photo_uses_merged_exemplars_and_undo_keeps_new_photo(self):
        s=self.state;r=merge_people(s,self.ids,self.ids[1]);add_photo(s,'later','later',[face([1,0,0],'later.jpg',999)])
        self.assertEqual(s['photos']['later']['faces'][0]['person_id'],self.ids[1]);self.assertEqual(s['people'][self.ids[1]]['thumbnail'],'b.jpg')
        undo_merge(s,r['merge_id']);self.assertEqual(len(s['people']),3)
        self.assertEqual([f['person_id'] for f in s['photos']['a']['faces']],self.ids)
        self.assertIn('later',s['people'][self.ids[1]]['photo_ids']);self.assertNotIn('later',s['people'][self.ids[0]]['photo_ids']);self.assertFalse(s['aliases'])
    def test_nested_merges_only_undo_latest_then_restore(self):
        s=self.state;r1=merge_people(s,self.ids);r2=merge_people(s,[self.ids[0],self.third],self.third)
        self.assertEqual(resolve_person(s,self.ids[1]),self.third)
        with self.assertRaises(ValueError):undo_merge(s,r1['merge_id'])
        undo_merge(s,r2['merge_id']);self.assertEqual(resolve_person(s,self.ids[1]),self.ids[0]);undo_merge(s,r1['merge_id']);self.assertEqual(len(s['people']),3)
    def test_restart_persistence_and_undo(self):
        s=self.state;r=merge_people(s,self.ids)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json';atomic_json(p,s);loaded=json.loads(p.read_text());undo_merge(loaded,r['merge_id']);self.assertEqual(len(loaded['people']),3)
    def test_reject_invalid_selection_without_mutation(self):
        s=self.state;before=copy.deepcopy(s)
        for ids in [[self.ids[0]],[self.ids[0],self.ids[0]],[self.ids[0],'missing']]:
            with self.assertRaises(ValueError):merge_people(s,ids)
        self.assertEqual(s,before)
if __name__=='__main__':unittest.main()
