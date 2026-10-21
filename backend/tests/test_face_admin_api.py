import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'face-service'))
from core import add_photo, atomic_json, empty_state, merge_people
from face_state import face_map, revision
from search_index import SearchCache


class AdminAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(); cls.root=Path(cls.directory.name)
        with patch.dict(os.environ, {'DATA_DIR':str(cls.root),'FACE_PIPELINE':'legacy','GROUP_MODE':'legacy','ADMIN_PASSWORD':'test-only-password'}):
            spec=importlib.util.spec_from_file_location('isolated_face_admin',Path(__file__).resolve().parents[1]/'face-service'/'app.py')
            cls.app=importlib.util.module_from_spec(spec);sys.modules[spec.name]=cls.app;spec.loader.exec_module(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.app.state=empty_state();self.app.search_cache=SearchCache()
        f=lambda v,t:{'embedding':v,'thumbnail':t,'bbox':[10,10,20,20],'quality':100}
        self.photo_id='a'*64
        add_photo(self.app.state,self.photo_id,'a',[f([1,0],'a.jpg'),f([0,1],'b.jpg')])
        self.ids=list(self.app.state['people']); self.fids=list(face_map(self.app.state))
        atomic_json(self.app.STATE_PATH,self.app.state)

    def body(self,request_id='test-request-00001',**kwargs):
        return self.app.ReassignRequest(revision=revision(self.app.state),request_id=request_id,face_ids=[self.fids[0]],**kwargs)

    def test_replay_returns_receipt_before_stale_revision_check_and_survives_restart(self):
        body=self.body(target_id=self.ids[1]); response=self.app.admin_reassign(body)
        after=copy.deepcopy(self.app.state)
        self.app.state=json.loads(self.app.STATE_PATH.read_text())
        retry=self.app.admin_reassign(body)
        self.assertTrue(retry['replayed']);self.assertEqual(retry['operation_id'],response['operation_id'])
        self.assertEqual(self.app.state,after)
        with self.assertRaises(HTTPException) as error:
            self.app.admin_reassign(body.model_copy(update={'target_id':None}))
        self.assertEqual(error.exception.status_code,409)

    def test_stale_revision_does_not_mutate_index(self):
        body=self.body(target_id=self.ids[1]);merge_people(self.app.state,self.ids,self.ids[0])
        before=copy.deepcopy(self.app.state)
        with self.assertRaises(HTTPException) as error:
            self.app.admin_reassign(body)
        self.assertEqual(error.exception.status_code,409);self.assertEqual(self.app.state,before)

    def test_empty_person_keeps_old_album_id_without_inflating_public_count(self):
        self.app.admin_reassign(self.body(target_id=self.ids[1]))
        self.assertIn(self.ids[0],self.app.state['people'])
        self.assertEqual(self.app.album(self.ids[0])['photos'],[])
        self.assertEqual(self.app.health()['people'],len(self.app.people()['people']))

    def test_failed_disk_commit_does_not_publish_people_receipt_or_revision(self):
        body=self.body(target_id=self.ids[1]);before=copy.deepcopy(self.app.state)
        disk=self.app.STATE_PATH.read_bytes()
        with patch.object(self.app,'atomic_json',side_effect=OSError('disk-full')):
            with self.assertRaises(OSError):self.app.admin_reassign(body)
        self.assertEqual(self.app.state,before);self.assertEqual(self.app.STATE_PATH.read_bytes(),disk)
        self.app.admin_reassign(body)
        self.assertIn(body.request_id,self.app.state['request_receipts'])

    def test_old_face_positions_and_normalized_boxes_are_read_only(self):
        for face in self.app.state['photos'][self.photo_id]['faces']:face.pop('face_id')
        Image.new('RGB',(100,50)).save(self.root/'previews'/f'{self.photo_id}.jpg')
        before=copy.deepcopy(self.app.state)
        data=self.app.admin_photo_faces(self.photo_id)
        self.assertEqual(data['faces'][0]['normalized_bbox'],[.1,.2,.2,.4])
        self.assertEqual(self.app.state,before)
        self.assertEqual(data['faces'][0]['face_id'],self.fids[0])

    def test_pending_review_approval_is_versioned_and_undoable(self):
        bad={'embedding':[-1,0],'thumbnail':'bad.jpg','bbox':[0,0,10,10],'quality':1,'quality_metrics':{'sample_eligible':False}}
        add_photo(self.app.state,'bad','bad',[bad],mode='enhanced')
        catalog=self.app.admin_reviews(kind='pending')
        review=catalog['reviews'][0]
        result=self.app.admin_resolve_review(review['id'],self.app.ReviewRequest(action='approve',revision=catalog['revision'],request_id='pending-request-01'))
        self.assertEqual(self.app.admin_reviews(kind='pending')['total'],0)
        self.app.admin_undo_correction(self.app.CorrectionUndoRequest(correction_id=result['operation_id'],revision=result['revision'],request_id='undo-pending-00001'))
        self.assertEqual(self.app.admin_reviews(kind='pending')['total'],1)

    def test_admin_guard_and_invalid_paging(self):
        with self.assertRaises(HTTPException) as error:self.app.require_admin('wrong')
        self.assertEqual(error.exception.status_code,401)
        with self.assertRaises(HTTPException):self.app.admin_reviews(limit=1000)


if __name__=='__main__':unittest.main()
