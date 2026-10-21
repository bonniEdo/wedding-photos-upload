from contextlib import asynccontextmanager
from pathlib import Path
import asyncio, copy, json, logging, os, threading, time, hashlib, hmac
import cv2
from PIL import Image
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from core import atomic_json, empty_state, add_photo, similarity, merge_people, undo_merge, resolve_person
from face_engine import EngineConfig, FaceEngine, persist_analysis
from matching import MatchConfig
from face_state import face_id, face_map, latest_operation, revision
from reviews import (build_reviews, face_view, fingerprint, operation_history, reassign_faces,
                     resolve_review, revoke_constraint, undo_correction)
from typing import Literal

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE/'.env')
DATA = Path(os.environ.get('DATA_DIR', str(BASE/'data'))).resolve()
for d in ('jobs', 'originals', 'thumbs', 'previews'): (DATA/d).mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA/'face_index.json'
GROUP_THRESHOLD = float(os.environ.get('GROUP_THRESHOLD', '.50'))
SEARCH_THRESHOLD = float(os.environ.get('SEARCH_THRESHOLD', '.30'))
GROUP_MARGIN = float(os.environ.get('GROUP_MARGIN', '.06'))
FACE_PIPELINE = os.environ.get('FACE_PIPELINE', 'legacy')
GROUP_MODE = os.environ.get('GROUP_MODE', 'legacy')
if FACE_PIPELINE not in ('legacy', 'shadow', 'enhanced') or GROUP_MODE not in ('legacy', 'shadow', 'enhanced'):
    raise ValueError('FACE_PIPELINE and GROUP_MODE must be legacy, shadow or enhanced')
ENGINE_CONFIG = EngineConfig(max_pixels=int(os.environ.get('FACE_MAX_PIXELS', '60000000')),
                             tile_size=int(os.environ.get('FACE_TILE_SIZE', '1024')),
                             overlap=float(os.environ.get('FACE_TILE_OVERLAP', '.25')),
                             max_tiles=int(os.environ.get('FACE_MAX_TILES', '32')),
                             tile_seconds=float(os.environ.get('FACE_TILE_SECONDS', '10')),
                             min_face_pixels=int(os.environ.get('FACE_MIN_PIXELS', '32')),
                             min_sharpness=float(os.environ.get('FACE_MIN_SHARPNESS', '20')))
MATCH_CONFIG = MatchConfig(threshold=float(os.environ.get('GROUP_V2_THRESHOLD', '.60')),
                           margin=float(os.environ.get('GROUP_V2_MARGIN', '.08')),
                           single_threshold=float(os.environ.get('GROUP_V2_SINGLE_THRESHOLD', '.65')),
                           sample_threshold=float(os.environ.get('GROUP_V2_SAMPLE_THRESHOLD', '.70')),
                           sample_margin=float(os.environ.get('GROUP_V2_SAMPLE_MARGIN', '.10')))
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
ADMIN_KEY = hashlib.sha256(('wedding-face-admin:' + ADMIN_PASSWORD).encode()).hexdigest()
MAX_BYTES = 25*1024*1024
state_lock = threading.RLock()
model_lock = threading.Lock()
stop = threading.Event()
state = empty_state()
detector = recognizer = None
engine = None
review_cache_lock = threading.Lock()
review_source = review_catalog = None
lookup_cache_lock = threading.Lock()
lookup_source = lookup_catalog = None
log = logging.getLogger('uvicorn.error')

def analyze(raw, photo_id=None):
    with model_lock:
        result = engine.analyze(raw, photo_id, 'enhanced' if FACE_PIPELINE == 'enhanced' else 'legacy')
        # Query selfies never incur shadow work or create persisted query records.
        if FACE_PIPELINE == 'shadow' and photo_id:
            try:
                shadow = engine.analyze(raw, photo_id, 'enhanced')
                result.metadata['shadow'] = {**shadow.metadata, 'face_count': len(shadow.faces),
                                            'original_bboxes': [f['original_bbox'] for f in shadow.faces]}
            except Exception as error:
                log.exception('Shadow detection failed; keeping legacy analysis')
                result.metadata['shadow'] = {'error': type(error).__name__}
    if photo_id:
        persist_analysis(result, DATA, photo_id)
    return result


def extract(raw, photo_id=None):
    return analyze(raw, photo_id).faces

def manifest(photo_id):
    if len(photo_id)!=64 or any(c not in '0123456789abcdef' for c in photo_id):
        raise HTTPException(404, '找不到照片')
    p = DATA/'jobs'/f'{photo_id}.json'
    if not p.exists(): raise HTTPException(404, '找不到照片')
    return json.loads(p.read_text(encoding='utf8'))

def worker():
    global state
    while not stop.is_set():
        for job_path in sorted((DATA/'jobs').glob('*.json'), key=lambda p: p.stat().st_mtime):
            if stop.is_set(): break
            photo_id = job_path.stem
            with state_lock:
                error = state['errors'].get(photo_id, {})
                if photo_id in state['photos'] or error.get('attempts',0)>=3 or error.get('retry_at',0)>time.time(): continue
            try:
                job = json.loads(job_path.read_text(encoding='utf8'))
                result = analyze((DATA/'originals'/job['file']).read_bytes(), photo_id)
                with state_lock:
                    updated = copy.deepcopy(state)
                    add_photo(updated, photo_id, job['name'], result.faces, GROUP_THRESHOLD, GROUP_MARGIN,
                              mode=GROUP_MODE, match_config=MATCH_CONFIG, analysis=result.metadata)
                    atomic_json(STATE_PATH, updated)
                    state = updated
                log.info('Indexed %s: %s faces (partial=%s)', photo_id[:8], len(result.faces), result.metadata['partial'])
            except Exception as e:
                log.exception('Analysis failed for %s', photo_id[:8])
                with state_lock:
                    attempts = state['errors'].get(photo_id,{}).get('attempts',0)+1
                    state['errors'][photo_id] = {'attempts': attempts, 'retry_at': time.time()+30*attempts, 'message': str(e)}
                    atomic_json(STATE_PATH, state)
        stop.wait(2)


@asynccontextmanager
async def lifespan(app):
    global state, detector, recognizer, engine
    # OS lock is released even after a crash. JSON mode requires one process.
    guard = (DATA/'face-worker.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            guard.seek(0); guard.write(b'0'); guard.flush(); guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if STATE_PATH.exists(): state = json.loads(STATE_PATH.read_text(encoding='utf8'))
        models = BASE/'models'
        detector = cv2.FaceDetectorYN.create(str(models/'face_detection_yunet_2023mar.onnx'), '', (320,320), .9, .3, 5000)
        recognizer = cv2.FaceRecognizerSF.create(str(models/'face_recognition_sface_2021dec.onnx'), '')
        engine = FaceEngine(detector, recognizer, ENGINE_CONFIG)
        stop.clear(); thread = threading.Thread(target=worker, daemon=True); thread.start()
        yield
        stop.set(); await asyncio.to_thread(thread.join)
    finally:
        guard.close()

app = FastAPI(title='Wedding People Albums', lifespan=lifespan)

@app.get('/health')
def health():
    with state_lock:
        ids = {p.stem for p in (DATA/'jobs').glob('*.json')}
        failed = sum(e.get('attempts',0)>=3 for e in state['errors'].values())
        return {'ok': True, 'photos': len(state['photos']), 'people': sum(bool(p['photo_ids']) for p in state['people'].values()),
                'pending': len(ids-set(state['photos']))-failed, 'failed': failed,
                'withoutFaces': sum(not p['faces'] for p in state['photos'].values()),
                'partialPhotos': sum(p.get('analysis', {}).get('partial', False) for p in state['photos'].values()),
                'facePipeline': FACE_PIPELINE, 'groupMode': GROUP_MODE}

@app.get('/people')
def people():
    with state_lock:
        return {'ok': True, 'people': [{'id': pid, 'thumbnail': f'/api/face/thumbs/{p["thumbnail"]}',
                 'count': len(p['photo_ids'])} for pid,p in state['people'].items() if p['photo_ids']]}

@app.get('/people/{person_id}/photos')
def album(person_id: str):
    with state_lock:
        person_id = resolve_person(state, person_id)
        person = state['people'].get(person_id)
        if not person: raise HTTPException(404, '找不到人物相簿')
        return {'ok': True, 'photos': [{'id': pid, 'name': state['photos'][pid]['name'],
                 'url': f'/api/photos/{pid}/image'} for pid in person['photo_ids']]}

@app.get('/thumbs/{filename}')
def thumbnail(filename: str):
    if Path(filename).name != filename or not filename.endswith('.jpg'): raise HTTPException(404)
    p = DATA/'thumbs'/filename
    if not p.is_file(): raise HTTPException(404)
    return FileResponse(p, media_type='image/jpeg')

@app.get('/photo/{photo_id}/image')
def photo_image(photo_id: str):
    job = manifest(photo_id)
    preview = DATA/'previews'/f'{photo_id}.jpg'
    if not preview.exists(): raise HTTPException(409, '照片正在分析中')
    return FileResponse(preview, media_type='image/jpeg')

@app.post('/find')
async def find(file: UploadFile=File(...), threshold: float=SEARCH_THRESHOLD):
    if not 0<=threshold<=1: raise HTTPException(400, 'threshold 必須介於 0 到 1')
    try:
        raw = await file.read(MAX_BYTES+1)
    finally:
        await file.close()
    if len(raw)>MAX_BYTES: raise HTTPException(413, '照片最多 25 MB')
    try: faces = await asyncio.to_thread(extract, raw)
    except Exception: raise HTTPException(400, '無法讀取照片，請換 JPG、PNG 或 HEIC')
    if not faces: raise HTTPException(400, '查不到人臉，請換一張清楚正面照')
    if len(faces)>1: raise HTTPException(400, '這張照片有多張人臉，請用單人照或自拍')
    def compare():
        matches = []
        with state_lock:
            for pid, p in state['photos'].items():
                score = max((similarity(faces[0]['embedding'], f['embedding']) for f in p['faces']), default=-1)
                if score>=threshold:
                    matches.append({'id': pid, 'name': p['name'], 'score': round(score,4), 'url': f'/api/photos/{pid}/image'})
        matches.sort(key=lambda p:p['score'], reverse=True)
        return matches
    return {'ok': True, 'threshold': threshold, 'matches': await asyncio.to_thread(compare)}


def require_admin(x_admin_key: str = Header(default='')):
    if len(ADMIN_PASSWORD) < 12: raise HTTPException(503, '尚未設定管理密碼，請在 .env 設定至少 12 字元的 ADMIN_PASSWORD')
    if not hmac.compare_digest(x_admin_key.encode(), ADMIN_KEY.encode()): raise HTTPException(401, '請先登入管理頁面')

class MergeRequest(BaseModel):
    person_ids: list[str] = Field(min_length=2, max_length=100)
    target_id: str
    revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, min_length=16, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')

class UndoRequest(BaseModel):
    merge_id: str
    revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, min_length=16, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


class VersionedRequest(BaseModel):
    revision: int = Field(ge=0)
    request_id: str = Field(min_length=16, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


class ReviewRequest(VersionedRequest):
    action: Literal['merge', 'different', 'skip', 'approve']
    target_id: str | None = None


class ReassignRequest(VersionedRequest):
    face_ids: list[str] = Field(min_length=1, max_length=100)
    target_id: str | None = None


class CorrectionUndoRequest(VersionedRequest):
    correction_id: str


def mutate(body, name, handler):
    """Receipt, revision, data and history are persisted in ONE index transaction."""
    global state
    payload = body.model_dump(exclude={'revision', 'request_id'})
    request_hash = fingerprint([name, payload])
    with state_lock:
        receipt = state.get('request_receipts', {}).get(body.request_id) if body.request_id else None
        if receipt is not None:
            if receipt['hash'] != request_hash:
                raise HTTPException(409, '同一請求編號不能用於不同操作')
            return {**receipt['response'], 'revision': revision(state), 'replayed': True}
        if body.revision is not None and body.revision != revision(state):
            raise HTTPException(409, '人物資料已更新，請重新整理後再操作')
        updated = copy.deepcopy(state)
        try:
            result = handler(updated)
        except ValueError as error:
            raise HTTPException(409, str(error))
        response = {'ok': True, **result, 'revision': revision(updated)}
        if body.request_id:
            updated.setdefault('request_receipts', {})[body.request_id] = {'hash': request_hash, 'response': response}
        atomic_json(STATE_PATH, updated)
        state = updated
        return response


def review_snapshot():
    global review_source, review_catalog
    with state_lock:
        source = state
    with review_cache_lock:
        if review_source is not source:
            review_catalog = build_reviews(source)
            review_source = source
        return source, review_catalog


def lookup_snapshot():
    global lookup_source, lookup_catalog
    with state_lock:
        source = state
    with lookup_cache_lock:
        if lookup_source is not source:
            lookup_catalog = face_map(source)
            lookup_source = source
        return source, lookup_catalog

@app.get('/admin/people', dependencies=[Depends(require_admin)])
def admin_people():
    with state_lock:
        return {'ok': True, 'people': [{'id': pid, 'thumbnail': f'/api/face/thumbs/{p["thumbnail"]}',
                'count': len(p['photo_ids']), 'merged': p.get('manually_merged', False),
                'provisional': p.get('provisional', False)} for pid,p in state['people'].items() if p['photo_ids']],
                'revision': revision(state), 'operations': operation_history(state),
                'history': [{'id': e['id'], 'created_at': e['created_at'], 'merged_count': len(e['people']),
                             'can_undo': bool(latest_operation(state) and latest_operation(state)['id']==e['id'])}
                            for e in state.get('merge_history', [])]}

@app.post('/admin/merge', dependencies=[Depends(require_admin)])
def admin_merge(body: MergeRequest):
    return mutate(body, 'merge', lambda updated: merge_people(updated, body.person_ids, body.target_id))

@app.post('/admin/undo', dependencies=[Depends(require_admin)])
def admin_undo(body: UndoRequest):
    return mutate(body, 'undo-merge', lambda updated: undo_merge(updated, body.merge_id))


@app.get('/admin/face-reviews', dependencies=[Depends(require_admin)])
def admin_reviews(kind: Literal['all', 'duplicate', 'pending']='all', offset: int=0, limit: int=30):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(400, '無效的分頁參數')
    _, catalog = review_snapshot()
    rows = [r for r in catalog['reviews'] if kind == 'all' or r['kind'] == kind]
    return {'ok': True, 'revision': catalog['revision'], 'reviews': rows[offset:offset+limit],
            'total': len(rows), 'next_offset': offset+limit if offset+limit < len(rows) else None,
            'constraints': catalog['constraints']}


@app.post('/admin/face-reviews/{review_id}/resolve', dependencies=[Depends(require_admin)])
def admin_resolve_review(review_id: str, body: ReviewRequest):
    _, catalog = review_snapshot()
    def apply(updated):
        if catalog['revision'] != revision(updated):
            raise ValueError('複核建議已更新，請重新整理')
        return resolve_review(updated, review_id, body.action, body.target_id, catalog)
    return mutate(body, 'review:'+review_id, apply)


@app.post('/admin/face-constraints/{constraint_id}/revoke', dependencies=[Depends(require_admin)])
def admin_revoke(constraint_id: str, body: VersionedRequest):
    return mutate(body, 'revoke:'+constraint_id, lambda updated: revoke_constraint(updated, constraint_id))


@app.post('/admin/faces/reassign', dependencies=[Depends(require_admin)])
def admin_reassign(body: ReassignRequest):
    return mutate(body, 'reassign', lambda updated: reassign_faces(updated, body.face_ids, body.target_id))


@app.post('/admin/face-corrections/undo', dependencies=[Depends(require_admin)])
def admin_undo_correction(body: CorrectionUndoRequest):
    return mutate(body, 'undo-correction', lambda updated: undo_correction(updated, body.correction_id))


@app.get('/admin/photos/{photo_id}/faces', dependencies=[Depends(require_admin)])
def admin_photo_faces(photo_id: str):
    source, _ = lookup_snapshot()
    photo = source['photos'].get(photo_id)
    if photo is None:
        raise HTTPException(404, '找不到照片')
    preview_size = photo.get('analysis', {}).get('preview_size')
    if preview_size is None and len(photo_id)==64 and all(c in '0123456789abcdef' for c in photo_id):
        try:
            with Image.open(DATA/'previews'/f'{photo_id}.jpg') as image:
                preview_size = list(image.size)
        except OSError:
            pass
    result = []
    for index, face in enumerate(photo['faces']):
        view = face_view(face_id(photo_id, index, face), photo_id, face)
        pending_id = fingerprint(['pending', view['face_id'], face['person_id']])
        needs_review = (not face.get('manual_confirmed') and
                        (source['people'].get(face['person_id'], {}).get('provisional') or
                         face.get('matching', {}).get('reason') in ('low_quality', 'ambiguous', 'same_photo_conflict')))
        view['review_id'] = pending_id if needs_review and pending_id not in source.get('ignored_reviews', {}) else None
        size = view['image_size'] if view['coordinate_space']=='original-exif' else (view['preview_size'] or preview_size)
        view['normalized_bbox'] = ([face['bbox'][0]/size[0], face['bbox'][1]/size[1],
                                    face['bbox'][2]/size[0], face['bbox'][3]/size[1]] if size and min(size)>0 else None)
        result.append(view)
    return {'ok': True, 'revision': revision(source), 'photo_id': photo_id,
            'photo_url': f'/api/photos/{photo_id}/image', 'faces': result}


@app.get('/admin/faces/{key}/crop', dependencies=[Depends(require_admin)])
def admin_face_crop(key: str):
    _, lookup = lookup_snapshot()
    if key not in lookup:
        raise HTTPException(404, '找不到人臉')
    face = lookup[key][2]
    for directory, name in [('face_crops', face.get('crop')), ('thumbs', face.get('thumbnail'))]:
        if name and Path(name).name == name and name.endswith('.jpg'):
            candidate = DATA/directory/name
            if candidate.is_file():
                return FileResponse(candidate, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
    raise HTTPException(404, '沒有可用裁切，請查看原照片')
