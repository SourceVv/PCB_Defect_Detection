from pathlib import Path
import cv2
import numpy as np

def read_image(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'Image not found: {path}')
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'Cannot decode image: {path}')
    return image

def write_image(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(path.suffix or '.png', image)
    if not ok:
        raise ValueError(f'Cannot encode image: {path}')
    data.tofile(path)
