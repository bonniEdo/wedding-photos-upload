"""Offline queue execution. Stop Node and Python writers before using this CLI."""
import argparse
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import threading

from dotenv import load_dotenv
from core import atomic_json
from face_engine import EngineConfig, FaceEngine
from backfill_jobs import BackfillRunner, OriginalError, pipeline_key, start_jobs, pause_jobs, recover_jobs, summary, require_backup
from maintain_faces import audit_state, offline_lock, MaintenanceError

BASE=Path(__file__).resolve().parents[1]


def original_from_cli(data,photo_id,allow_drive=False):
    args=['node',str(BASE/'backfill-source-cli.mjs'),'--data',str(data),'--photo-id',photo_id]
    if allow_drive:args.append('--allow-drive')
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:raw,errors=process.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:process.communicate(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.communicate()
        raise OriginalError('retryable','回填原圖取得逾時')
    records=[]
    for line in errors.decode('utf8',errors='replace').splitlines():
        try:records.append(json.loads(line))
        except ValueError:pass
    record=records[-1] if records else {}
    if process.returncode or not record.get('sha256'):
        raise OriginalError(record.get('code','retryable'),record.get('message','無法取得回填原圖'))
    if len(raw)>int(os.environ.get('FACE_BACKFILL_MAX_BYTES',str(100*1024*1024))):
        raise OriginalError('unavailable','原圖超過回填大小限制')
    return raw,record['sha256'],record.get('source','unknown')


def engine_config():
    return EngineConfig(max_pixels=int(os.environ.get('FACE_MAX_PIXELS','60000000')),
                        tile_size=int(os.environ.get('FACE_TILE_SIZE','1024')),overlap=float(os.environ.get('FACE_TILE_OVERLAP','.25')),
                        max_tiles=int(os.environ.get('FACE_MAX_TILES','32')),tile_seconds=float(os.environ.get('FACE_TILE_SECONDS','10')),
                        min_face_pixels=int(os.environ.get('FACE_MIN_PIXELS','32')),min_sharpness=float(os.environ.get('FACE_MIN_SHARPNESS','20')))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['start','run','status','pause'])
    parser.add_argument('--data',required=True);parser.add_argument('--photo-ids',nargs='+');parser.add_argument('--retry',action='store_true')
    parser.add_argument('--limit',type=int);parser.add_argument('--allow-drive',action='store_true')
    args=parser.parse_args();load_dotenv(BASE/'.env')
    data=Path(args.data).resolve();state_path=data/'face_index.json'
    if not data.is_dir() or not state_path.is_file() or state_path.is_symlink():parser.error('DATA must contain a regular face_index.json')
    if args.limit is not None and args.limit<1:parser.error('limit must be positive')
    stopped=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stopped.set())
    # Establish the same process guard as the worker, even for an index predating its lock file.
    with (data/'face-worker.lock').open('a+b'):pass
    with offline_lock(data):
        state=json.loads(state_path.read_text());report=audit_state(state)
        if not report['ok']:raise MaintenanceError('Index audit failed before backfill')
        if args.command=='status':print(json.dumps({'ok':True,**summary(state)},ensure_ascii=False));return
        require_backup(state)
        def commit(handler):
            nonlocal state
            updated=copy.deepcopy(state);result=handler(updated)
            atomic_json(state_path,updated);state=updated
            return result
        if args.command=='pause':commit(pause_jobs)
        else:
            config=engine_config();key=pipeline_key(BASE/'models',config)
            if args.command=='start':commit(lambda updated:start_jobs(updated,key,args.photo_ids,args.retry))
            else:
                if any(j['status']=='processing' for j in state.get('backfill',{}).get('jobs',{}).values()):commit(lambda updated:recover_jobs(updated,key))
                engine=FaceEngine.from_models(BASE/'models',config)
                runner=BackfillRunner(data,key,lambda:state,commit,lambda pid:original_from_cli(data,pid,args.allow_drive),
                                      lambda raw,seed:engine.analyze(raw,seed,'enhanced'))
                processed=0
                while not stopped.is_set() and (args.limit is None or processed<args.limit) and runner.step():
                    processed+=1
                    status=summary(state,0,1)
                    print(json.dumps({'processed':processed,'counts':status['counts']},ensure_ascii=False),flush=True)
        final=audit_state(state)
        print(json.dumps({'ok':final['ok'],'summary':summary(state,0,1),'audit':final},ensure_ascii=False))
        if not final['ok']:raise MaintenanceError('Index audit failed after backfill')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as error:
        print(json.dumps({'ok':False,'message':str(error)},ensure_ascii=False));raise SystemExit(1)
