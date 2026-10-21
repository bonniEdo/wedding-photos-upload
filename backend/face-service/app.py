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

class UndoRequest(BaseModel):
    merge_id: str

@app.get('/admin/people', dependencies=[Depends(require_admin)])
def admin_people():
    with state_lock:
        return {'ok': True, 'people': [{'id': pid, 'thumbnail': f'/api/face/thumbs/{p["thumbnail"]}',
                'count': len(p['photo_ids']), 'merged': p.get('manually_merged', False), 'provisional': p.get('provisional', False)} for pid,p in state['people'].items() if p['photo_ids']],
                'history': [{'id': e['id'], 'created_at': e['created_at'], 'merged_count': len(e['people']),
                             'can_undo': i==len(state.get('merge_history', []))-1} for i,e in enumerate(state.get('merge_history', []))]}

@app.post('/admin/merge', dependencies=[Depends(require_admin)])
def admin_merge(body: MergeRequest):
    global state
    with state_lock:
        updated = copy.deepcopy(state)
        try: result = merge_people(updated, body.person_ids, body.target_id)
        except ValueError as e: raise HTTPException(409, str(e))
        atomic_json(STATE_PATH, updated)
        state = updated
        return {'ok': True, **result}

@app.post('/admin/undo', dependencies=[Depends(require_admin)])
def admin_undo(body: UndoRequest):
    global state
    with state_lock:
        updated = copy.deepcopy(state)
        try: result = undo_merge(updated, body.merge_id)
        except ValueError as e: raise HTTPException(409, str(e))
        atomic_json(STATE_PATH, updated)
        state = updated
        return {'ok': True, **result}
