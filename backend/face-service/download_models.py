from pathlib import Path
from urllib.request import urlretrieve
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
    urlretrieve(url,target)
    print('saved:', target)
