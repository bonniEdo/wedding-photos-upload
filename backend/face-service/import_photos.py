"""Queue old local photos without rebuilding an existing index."""
import argparse, hashlib, json, mimetypes, os, shutil, time, uuid
from pathlib import Path
from dotenv import load_dotenv
from core import atomic_json
parser=argparse.ArgumentParser(description='把舊照片加入自動分析佇列')
parser.add_argument('folder',type=Path,nargs='?',help='照片資料夾；省略時掃描 DATA_DIR/originals')
parser.add_argument('--skip-drive',action='store_true',help='照片原本已在 Drive，不再備份')
args=parser.parse_args()
base=Path(__file__).resolve().parent.parent
load_dotenv(base/'.env')
data=Path(os.environ.get('DATA_DIR',str(base/'data'))).resolve()
folder=(args.folder or data/'originals').resolve()
if not folder.is_dir():parser.error(f'找不到照片資料夾：{folder}')
for d in ('jobs','originals'): (data/d).mkdir(parents=True,exist_ok=True)
originals=(data/'originals').resolve()
registered=set()
for job_path in (data/'jobs').glob('*.json'):
    job=json.loads(job_path.read_text(encoding='utf8'))
    if job.get('file'):registered.add((originals/job['file']).resolve())
drive_enabled=os.environ.get('DRIVE_ENABLED','true')!='false'
drive_status='disabled' if not drive_enabled else ('done' if args.skip_drive else 'pending')
count=0
reused=0
# Snapshot the input list so copied outputs are not picked up during this run.
for source in sorted(folder.rglob('*')):
    if not source.is_file() or source.suffix.lower() not in {'.jpg','.jpeg','.png','.webp','.heic','.heif'}:continue
    resolved=source.resolve()
    if resolved in registered:continue
    digest=hashlib.sha256()
    with source.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    pid=digest.hexdigest();job_path=data/'jobs'/f'{pid}.json'
    if job_path.exists():continue
    if resolved.is_relative_to(originals):
        filename=resolved.relative_to(originals).as_posix()
        reused+=1
    else:
        filename=pid+source.suffix.lower();target=originals/filename
        temp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            shutil.copyfile(source,temp)
            with temp.open('r+b') as f:os.fsync(f.fileno())
            os.replace(temp,target)
        finally:temp.unlink(missing_ok=True)
    atomic_json(job_path,{'id':pid,'name':source.name,'file':filename,
      'mimeType':mimetypes.guess_type(source.name)[0] or 'image/jpeg',
      'contentHash':pid,'source':'local','createdAt':int(time.time()*1000),
      'driveStatus':drive_status,'attempts':0,'retryAt':0})
    count+=1
print(f'已加入 {count} 張照片，其中 {reused} 張沿用現有原圖。啟動人臉服務後會自動分析。匯入時請先停止 Node，匯入完成再啟動。')
