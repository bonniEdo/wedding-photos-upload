from pathlib import Path
from urllib.request import urlopen
import shutil, uuid
BASE = Path(__file__).resolve().parent.parent
MODELS = BASE / 'models'
MODELS.mkdir(exist_ok=True)
files = {
 'face_detection_yunet_2023mar.onnx':'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx',
 'face_recognition_sface_2021dec.onnx':'https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx',
}
for name,url in files.items():
    target=MODELS/name
    if target.exists():
        print('exists:', target); continue
    print('downloading:', name)
    temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
    try:
        with urlopen(url, timeout=120) as response, temp.open('xb') as output:
            shutil.copyfileobj(response, output)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    print('saved:', target)
