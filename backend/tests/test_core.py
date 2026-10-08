import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'face-service'))
from core import empty_state, add_photo, atomic_json

def face(vector, quality=100):
    return {'embedding':vector,'quality':quality,'thumbnail':'face.jpg','bbox':[0,0,10,10]}
class GroupTests(unittest.TestCase):
    def test_incremental_and_duplicate(self):
        s=empty_state();add_photo(s,'first','a',[face([1,0]),face([0,1])]);self.assertEqual(len(s['people']),2)
        add_photo(s,'later','b',[face([.99,.01],200)]);self.assertEqual(len(s['people']),2)
        self.assertEqual(sorted(len(p['photo_ids']) for p in s['people'].values()),[1,2])
        self.assertFalse(add_photo(s,'later','b',[face([.99,.01])]));self.assertEqual(len(s['photos']),2)
    def test_same_photo_does_not_merge_two_people(self):
        s=empty_state();add_photo(s,'a','a',[face([1,0])]);add_photo(s,'b','b',[face([1,0]),face([1,0])]);self.assertEqual(len(s['people']),2)
    def test_ambiguous_person_kept_separate(self):
        s=empty_state();add_photo(s,'a','a',[face([1,0]),face([.9,.435])]);add_photo(s,'b','b',[face([.97,.24])]);self.assertEqual(len(s['people']),3)
    def test_no_faces_recorded_and_atomic_restart(self):
        s=empty_state();add_photo(s,'a','landscape',[])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'index.json';atomic_json(p,s);restored=json.loads(p.read_text());self.assertIn('a',restored['photos'])
            self.assertFalse(add_photo(restored,'a','landscape',[]));self.assertFalse(list(Path(d).glob('*.tmp')))
if __name__=='__main__':unittest.main()
