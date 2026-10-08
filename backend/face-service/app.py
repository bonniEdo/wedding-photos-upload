from contextlib import asynccontextmanager
from pathlib import Path
import asyncio, copy, io, json, logging, os, threading, time, uuid, hashlib, hmac
import cv2
import numpy as np
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from core import atomic_json, empty_state, add_photo, similarity, merge_people, undo_merge, resolve_person

register_heif_opener()
BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE/'.env')
DATA = Path(os.environ.get('DATA_DIR', str(BASE/'data'))).resolve()
for d in ('jobs', 'originals', 'thumbs', 'previews'): (DATA/d).mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA/'face_index.json'
GROUP_THRESHOLD = float(os.environ.get('GROUP_THRESHOLD', '.50'))
SEARCH_THRESHOLD = float(os.environ.get('SEARCH_THRESHOLD', '.30'))
GROUP_MARGIN = float(os.environ.get('GROUP_MARGIN', '.06'))
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
ADMIN_KEY = hashlib.sha256(('wedding-face-admin:' + ADMIN_PASSWORD).encode()).hexdigest()
MAX_BYTES = 25*1024*1024
state_lock = threading.RLock()
model_lock = threading.Lock()
stop = threading.Event()
state = empty_state()
detector = recognizer = None
log = logging.getLogger('uvicorn.error')

def load_image(raw):
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1600, 1600))
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)

def extract(raw, photo_id=None):
    img = load_image(raw)
    h, w = img.shape[:2]
    if photo_id:
        if not cv2.imwrite(str(DATA/'previews'/f'{photo_id}.jpg'), img): raise RuntimeError('Cannot save preview')
    results = []
    with model_lock:
        detector.setInputSize((w, h)); _, faces = detector.detect(img)
        if faces is None: return results
        for i, face in enumerate(faces):
            aligned = recognizer.alignCrop(img, face)
            feature = recognizer.feature(aligned).flatten().astype(np.float32)
            feature /= max(float(np.linalg.norm(feature)), 1e-12)
            x, y, fw, fh = (float(v) for v in face[:4])
            thumb = None
            if photo_id:
                pad = .25 * max(fw, fh)
                crop = img[max(0,int(y-pad)):min(h,int(y+fh+pad)),
                           max(0,int(x-pad)):min(w,int(x+fw+pad))]
                if crop.size:
                    thumb = f'{photo_id}_{i}.jpg'
                    crop = cv2.resize(crop, (160, 160))
                    if not cv2.imwrite(str(DATA/'thumbs'/thumb), crop): raise RuntimeError('Cannot save thumbnail')
            results.append({'bbox': [x,y,fw,fh], 'embedding': feature.tolist(),
                            'thumbnail': thumb, 'quality': fw*fh*float(face[-1])})
    return results

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
                faces = extract((DATA/'originals'/job['file']).read_bytes(), photo_id)
                with state_lock:
                    updated = copy.deepcopy(state)
                    add_photo(updated, photo_id, job['name'], faces, GROUP_THRESHOLD, GROUP_MARGIN)
                    atomic_json(STATE_PATH, updated)
                    state = updated
                log.info('Indexed %s: %s faces', photo_id[:8], len(faces))
            except Exception as e:
                log.exception('Analysis failed for %s', photo_id[:8])
                with state_lock:
                    attempts = state['errors'].get(photo_id,{}).get('attempts',0)+1
                    state['errors'][photo_id] = {'attempts': attempts, 'retry_at': time.time()+30*attempts, 'message': str(e)}
                    atomic_json(STATE_PATH, state)
        stop.wait(2)

@asynccontextmanager
async def lifespan(app):
    global state, detector, recognizer
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
        return {'ok': True, 'photos': len(state['photos']), 'people': len(state['people']),
                'pending': len(ids-set(state['photos']))-failed, 'failed': failed,
                'withoutFaces': sum(not p['faces'] for p in state['photos'].values())}

@app.get('/people')
def people():
    with state_lock:
        return {'ok': True, 'people': [{'id': pid, 'thumbnail': f'/api/face/thumbs/{p["thumbnail"]}',
                 'count': len(p['photo_ids'])} for pid,p in state['people'].items()]}

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
                'count': len(p['photo_ids']), 'merged': p.get('manually_merged', False)} for pid,p in state['people'].items()],
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
