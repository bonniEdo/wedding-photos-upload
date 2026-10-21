"""Synthetic local UI/API fixture. Optional real single-face query stays in temp data."""
import argparse
import copy
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'face-service'))
from core import add_photo,atomic_json,empty_state,merge_people
from face_engine import FaceEngine


def create(root,real_query=False):
    for name in ('jobs','originals','previews','thumbs','face_crops'):(root/name).mkdir(parents=True,exist_ok=True)
    q=np.zeros(128,dtype=np.float32);q[0]=1
    if real_query:
        engine=FaceEngine.from_models(Path(__file__).resolve().parents[1]/'models')
        found=False
        for path in sorted((Path(__file__).resolve().parents[1]/'sample_photos').glob('*.jpg')):
            result=engine.analyze(path.read_bytes(),'fixture')
            for _,crop in result.crops:
                ok,encoded=cv2.imencode('.jpg',crop)
                if not ok:continue
                analysis=engine.analyze(encoded.tobytes())
                if len(analysis.faces)==1:
                    (root/'query.jpg').write_bytes(encoded.tobytes());q=np.asarray(analysis.faces[0]['embedding'],dtype=np.float32)
                    found=True;break
            if found:break
        if not found:raise ValueError('No single-face query fixture available')
    basis=np.eye(len(q),dtype=np.float32)
    v=basis[np.argmin(np.abs(q))];v=v-np.dot(v,q)*q;v/=np.linalg.norm(v)
    w=basis[np.argsort(np.abs(q))[1]];w=w-np.dot(w,q)*q-np.dot(w,v)*v;w/=np.linalg.norm(w)
    beta=.9*q+.435*v;beta/=np.linalg.norm(beta)
    f=lambda vector,thumb,x=20:{'embedding':vector.tolist(),'thumbnail':thumb,'bbox':[x,30,70,90],'quality':100}
    photo_ids=[str(i)*64 for i in (1,2,3)]
    state=empty_state();add_photo(state,photo_ids[0],'測試合照',[f(q,'a.jpg'),f(beta,'b.jpg',130)])
    ids=list(state['people']);add_photo(state,photo_ids[1],'測試照片二',[f(w,'c.jpg')]);ids=list(state['people'])
    merged=merge_people(state,ids[1:],ids[1])
    bad=f(-q,'d.jpg');bad['quality_metrics']={'sample_eligible':False}
    add_photo(state,photo_ids[2],'測試待確認照片',[bad],mode='enhanced')
    state.pop('admin_history',None)
    for photo in state['photos'].values():
        for face in photo['faces']:face.pop('face_id',None)
    atomic_json(root/'face_index.json',state)
    palette=['#dc8077','#7a9dce','#8fb69c','#dbb860']
    for name,color in zip(('a','b','c','d'),palette):
        image=Image.new('RGB',(100,100),'#fff9e6');draw=ImageDraw.Draw(image)
        draw.ellipse((12,12,88,88),fill=color);draw.ellipse((32,36,38,42),fill='white');draw.ellipse((62,36,68,42),fill='white')
        draw.arc((35,48,65,72),0,180,fill='white',width=3);image.save(root/'thumbs'/f'{name}.jpg')
    for index,pid in enumerate(photo_ids):
        preview=Image.new('RGB',(256,160),'#fff9e6')
        preview.paste(Image.open(root/'thumbs'/('a.jpg' if index==0 else 'c.jpg' if index==1 else 'd.jpg')),(10,20))
        if index==0:preview.paste(Image.open(root/'thumbs'/'b.jpg'),(125,20))
        preview.save(root/'previews'/f'{pid}.jpg')
        (root/'jobs'/f'{pid}.json').write_text(json.dumps({'file':pid+'.jpg','name':'測試照片'}))
    metadata={'photos':photo_ids,'people':ids,'old_merge_id':merged['merge_id'],'old_merge_history':copy.deepcopy(state['merge_history'])}
    (root/'fixture.json').write_text(json.dumps(metadata))
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--real-query',action='store_true')
    args=parser.parse_args();create(Path(args.directory),args.real_query)
