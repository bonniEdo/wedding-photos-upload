"""Durable P5 queue and append-only human decisions, independent of HTTP/process lifetime."""
from dataclasses import asdict
import copy
import hashlib
import json
from pathlib import Path
import time
import uuid

from backfill_mapping import build_candidate, photo_signature, validate_candidate, apply_metadata
from core import atomic_json
from face_engine import atomic_image, DETECTOR_MODEL, RECOGNIZER_MODEL
from face_state import advance, face_id, face_map, revision


def pipeline_key(models, config):
    def digest(path):
        h=hashlib.sha256()
        with Path(path).open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        return h.hexdigest()
    root=Path(__file__).parent
    payload={'schema':1,'config':asdict(config),'models':{name:digest(Path(models)/name) for name in (DETECTOR_MODEL,RECOGNIZER_MODEL)},
             'code':{name:digest(root/name) for name in ('face_engine.py','backfill_mapping.py')}}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def require_backup(state):
    receipt=state.get('face_backfill_maintenance',{})
    if not receipt.get('archive_sha256'):
        raise ValueError('請先停寫、完整備份並執行 maintain_faces.py migrate，才能開始舊照回填')


def start_jobs(state, key, photo_ids=None, retry=False):
    require_backup(state)
    ids=list(state['photos']) if photo_ids is None else photo_ids
    if not ids or len(ids)!=len(set(ids)) or any(pid not in state['photos'] for pid in ids):
        raise ValueError('請選擇存在且不重複的照片')
    backfill=state.setdefault('backfill',{'schema_version':1,'jobs':{},'paused':True})
    queued=0
    for pid in ids:
        previous=backfill['jobs'].get(pid)
        if previous and previous.get('pipeline_key')==key:
            if previous['status'] in ('queued','processing'):continue
            if previous['status']=='ready' and previous.get('signature')==photo_signature(state,pid):continue
            if not retry:continue
        backfill['jobs'][pid]={'photo_id':pid,'pipeline_key':key,'status':'queued','attempts':0,
                                'queued_at':time.time(),'retry_at':0,'signature':photo_signature(state,pid)}
        queued+=1
    backfill.update(paused=False,pipeline_key=key)
    advance(state)
    return {'queued':queued,'revision':revision(state)}


def pause_jobs(state):
    backfill=state.setdefault('backfill',{'schema_version':1,'jobs':{}})
    backfill['paused']=True;advance(state)
    return {'paused':True}


def recover_jobs(state, key):
    changed=False
    for job in state.get('backfill',{}).get('jobs',{}).values():
        if job['status']=='processing':
            job['status']='queued' if job['pipeline_key']==key else 'stale'
            job.pop('run_token',None);changed=True
    if changed:advance(state)
    return changed


def claim_job(state, key):
    backfill=state.get('backfill',{})
    if backfill.get('paused',True):return None
    for pid,job in backfill.get('jobs',{}).items():
        if job['status']!='queued' or job.get('retry_at',0)>time.time() or job['pipeline_key']!=key:continue
        job.update(status='processing',attempts=job.get('attempts',0)+1,started_at=time.time(),
                   signature=photo_signature(state,pid),run_token=uuid.uuid4().hex)
        advance(state)
        return copy.deepcopy(job)
    return None


def candidate_job(state, candidate_id):
    for job in state.get('backfill',{}).get('jobs',{}).values():
        if job.get('candidate',{}).get('id')==candidate_id:return job
    raise ValueError('回填候選已更新，請重新整理')


def candidate_basis(state, job):
    """Allow additional approvals from one candidate, but require its original prefix unchanged."""
    candidate=job['candidate'];pid=candidate['photo_id'];photo=state['photos'][pid]
    original_count=candidate['original_count'];accepted=set(job.get('accepted',{}).values())
    suffix=photo['faces'][original_count:]
    if len(suffix)!=len(accepted) or {face_id(pid,i+original_count,f) for i,f in enumerate(suffix)}!=accepted:
        raise ValueError('照片觀測已更新，請重新回填後再確認')
    basis={**state,'photos':{**state['photos'],pid:{**photo,'faces':photo['faces'][:original_count]}}}
    validate_candidate(basis,candidate)
    return basis


def accept_faces(state, candidate_id, candidate_face_ids, key):
    from reviews import record_operation
    job=candidate_job(state,candidate_id)
    if job['status']!='ready' or job['pipeline_key']!=key:
        raise ValueError('回填管線或資料已更新，請重新回填')
    candidate_basis(state,job)
    candidate=job['candidate'];accepted=job.setdefault('accepted',{})
    proposed={f['candidate_face_id']:f for f in candidate['new_faces']}
    if (not candidate_face_ids or len(candidate_face_ids)>100 or len(set(candidate_face_ids))!=len(candidate_face_ids)
            or any(fid not in proposed or fid in accepted for fid in candidate_face_ids)):
        raise ValueError('請選擇尚未加入的漏臉候選')
    pid=candidate['photo_id'];photo=state['photos'][pid];before_count=len(photo['faces'])
    existing=face_map(state);person_ids=[];added_ids=[]
    for fid in candidate_face_ids:
        if fid in existing:raise ValueError('人臉識別碼重複，請重新回填')
        face=copy.deepcopy(proposed[fid]['face']);person_id=uuid.uuid4().hex
        face.update(face_id=fid,person_id=person_id,backfill_candidate=candidate_id,
                    matching={'reason':'backfill_confirmed_presence','sample_eligible':False,'strategy':'backfill-v1'})
        photo['faces'].append(face)
        state['people'][person_id]={'samples':[],'representatives':[],'photo_ids':[pid],
                                    'thumbnail':face['thumbnail'],'quality':face.get('quality',0),'provisional':True}
        accepted[fid]=fid;person_ids.append(person_id);added_ids.append(fid)
    job['signature']=photo_signature(state,pid)
    result=record_operation(state,'backfill_add',photo_id=pid,before_count=before_count,
                            face_ids=added_ids,person_ids=person_ids,candidate_id=candidate_id)
    state['embedding_revision']=state.get('embedding_revision',0)+1
    return {**result,'added_count':len(added_ids),'face_ids':added_ids,'person_ids':person_ids}


def undo_added_faces(state, entry):
    from reviews import rebuild_corrected_person
    pid=entry['photo_id'];photo=state['photos'][pid];start=entry['before_count']
    rows=photo['faces'][start:]
    if [face_id(pid,start+i,f) for i,f in enumerate(rows)]!=entry['face_ids']:
        raise ValueError('照片觀測已更新，無法移除非尾端的回填人臉')
    removed=copy.deepcopy(rows);del photo['faces'][start:]
    for person_id in entry['person_ids']:
        rebuild_corrected_person(state,person_id,state['people'][person_id],removed)
    job=state.get('backfill',{}).get('jobs',{}).get(pid)
    if job:
        if job.get('candidate',{}).get('id')==entry['candidate_id']:
            for fid in entry['face_ids']:job.get('accepted',{}).pop(fid,None)
        else:job['status']='stale'
        job['signature']=photo_signature(state,pid)


def summary(state, offset=0, limit=30):
    data=state.get('backfill',{});jobs=list(data.get('jobs',{}).values());counts={}
    for job in jobs:counts[job['status']]=counts.get(job['status'],0)+1
    def pending(job):return sum(f['candidate_face_id'] not in job.get('accepted',{}) for f in job.get('candidate',{}).get('new_faces',[]))
    # Surface decisions first; this never changes the persisted photo/person order.
    jobs.sort(key=lambda job:0 if pending(job) else {'processing':1,'failed':2,'changed':2,'unavailable':2,'stale':2,'queued':3}.get(job['status'],4))
    rows=[]
    for job in jobs[offset:offset+limit]:
        candidate=job.get('candidate',{});accepted=job.get('accepted',{})
        rows.append({k:job[k] for k in ('photo_id','status','attempts','message','source','finished_at') if k in job})
        rows[-1]['photo_name']=state['photos'][job['photo_id']].get('name','照片')
        rows[-1].update(candidate_id=candidate.get('id'),new_faces=sum(f['candidate_face_id'] not in accepted for f in candidate.get('new_faces',[])),
                        mapped=len(candidate.get('mapped',[])),ambiguous=len(candidate.get('ambiguous',[])),partial=candidate.get('analysis',{}).get('partial',False))
    receipt=state.get('face_backfill_maintenance',{})
    return {'revision':revision(state),'paused':data.get('paused',True),'counts':counts,'total':len(jobs),
            'pending_faces':sum(pending(job) for job in jobs),
            'jobs':rows,'next_offset':offset+limit if offset+limit<len(jobs) else None,
            'backup':{k:receipt[k] for k in ('archive_sha256','created_at') if k in receipt}}


class OriginalError(Exception):
    def __init__(self,code,message):super().__init__(message);self.code=code


class BackfillRunner:
    def __init__(self,data,key,get_state,commit,get_original,analyze):
        self.data=Path(data);self.key=key;self.get_state=get_state;self.commit=commit
        self.get_original=get_original;self.analyze=analyze

    def step(self):
        # Avoid a no-op deepcopy/disk write on idle polls.
        source=self.get_state()
        if source.get('backfill',{}).get('paused',True) or not any(j['status']=='queued' and j.get('retry_at',0)<=time.time()
                    and j['pipeline_key']==self.key for j in source.get('backfill',{}).get('jobs',{}).values()):return False
        job=self.commit(lambda updated:claim_job(updated,self.key))
        if job is None:return False
        pid=job['photo_id'];source=self.get_state()
        try:
            raw,sha,origin=self.get_original(pid)
            if hashlib.sha256(raw).hexdigest()!=sha:raise OriginalError('changed','原圖校驗不符')
            seed='bf'+hashlib.sha256((pid+self.key+sha).encode()).hexdigest()[:48]
            analysis=self.analyze(raw,seed)
            preview_size=None
            from PIL import Image
            try:
                with Image.open(self.data/'previews'/f'{pid}.jpg') as image:preview_size=list(image.size)
            except OSError:pass
            candidate=build_candidate(source,pid,analysis,sha,self.key,preview_size)
            assets=self.data/'face-backfill'
            if len(analysis.faces)!=len(analysis.crops):raise ValueError('回填裁切不完整')
            for face,(thumb,crop) in zip(analysis.faces,analysis.crops):
                atomic_image(assets/'thumbs'/face['thumbnail'],thumb)
                atomic_image(assets/'crops'/face['crop'],crop)
            atomic_image(assets/'previews'/f'{candidate["id"]}.jpg',analysis.preview)
            (assets/'candidates').mkdir(parents=True,exist_ok=True)
            atomic_json(assets/'candidates'/f'{candidate["id"]}.json',candidate)
            def finish(updated):
                current=updated['backfill']['jobs'][pid]
                if current.get('run_token')!=job['run_token']:return False
                validate_candidate(updated,candidate)
                mapped=apply_metadata(updated,candidate)
                current.update(status='ready',candidate=candidate,accepted={},source=origin,
                               signature=photo_signature(updated,pid),mapped=mapped,finished_at=time.time())
                current.pop('message',None);current.pop('run_token',None);advance(updated)
                return True
            try:self.commit(finish)
            except ValueError as error:raise OriginalError('stale',str(error)) from error
        except Exception as error:
            code=error.code if isinstance(error,OriginalError) else 'failed'
            status=code if code in ('unavailable','changed','stale') else 'failed'
            def fail(updated):
                current=updated['backfill']['jobs'][pid]
                if current.get('run_token')!=job['run_token']:return
                current.update(status=status,message=str(error)[:250],finished_at=time.time())
                if code=='retryable' and current['attempts']<3:current.update(status='queued',retry_at=time.time()+30*current['attempts'])
                current.pop('run_token',None);advance(updated)
            self.commit(fail)
        return True
