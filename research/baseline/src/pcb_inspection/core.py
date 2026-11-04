from functools import lru_cache
import json
import math
from pathlib import Path
import cv2
import numpy as np
FEATURES = {'area_inv', 'cd40', 'sad', 'hcorr'}

def load_config(path):
    path = Path(path).expanduser().resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    config['_config_dir'] = str(path.parent)
    size = config.get('input_size', [])
    if len(size) != 2 or any((not isinstance(n, int) or n <= 0 for n in size)):
        raise ValueError('input_size must contain positive integer width and height')
    if config.get('alignment', {}).get('method') != 'orb_ransac':
        raise ValueError('Only alignment.method=orb_ransac is implemented')
    reference = config['alignment'].get('reference_image')
    if not reference:
        raise ValueError('alignment.reference_image is required')
    if not config.get('rois'):
        raise ValueError('rois must not be empty')
    for name, rc in config['rois'].items():
        box = rc.get('box', [])
        if len(box) != 4 or any((not isinstance(n, int) for n in box)) or (not (0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1])):
            raise ValueError(f'Invalid ROI box: {name}')
        if rc.get('type') == 'missing':
            if rc.get('feature') not in FEATURES:
                raise ValueError(f'Unsupported feature for {name}')
            if rc.get('op') not in ('<', '>'):
                raise ValueError(f'Unsupported comparison for {name}')
            if not math.isfinite(float(rc['threshold'])):
                raise ValueError(f'Non-finite threshold: {name}')
    return config

def reference_path(config):
    ref = Path(config['alignment']['reference_image'])
    if not ref.is_absolute():
        if '_config_dir' not in config:
            raise ValueError('Use load_config() to resolve relative reference paths')
        ref = Path(config['_config_dir']) / ref
    return ref.resolve()

def _gray(image):
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
        raise ValueError('Expected a non-empty uint8 image')
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raise ValueError('Expected grayscale or BGR image')

@lru_cache(maxsize=8)
def _reference_cached(filename, mtime_ns):
    from .io import read_image
    gray = _gray(read_image(filename))
    orb = cv2.ORB_create(nfeatures=3000, scoreType=cv2.ORB_FAST_SCORE)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    return (gray, keypoints, descriptors)

def _load_ref(config):
    path = reference_path(config)
    if not path.is_file():
        raise FileNotFoundError(f'Reference image not found: {path}')
    result = _reference_cached(str(path), path.stat().st_mtime_ns)
    if result[0].shape[::-1] != tuple(config['input_size']):
        raise ValueError('Reference dimensions do not match input_size')
    return result

def align_homography(gray, config):
    _, kp1, d1 = _load_ref(config)
    orb = cv2.ORB_create(nfeatures=3000, scoreType=cv2.ORB_FAST_SCORE)
    kp2, d2 = orb.detectAndCompute(gray, None)
    if d1 is None or d2 is None or len(kp1) < 20 or (len(kp2) < 20):
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    good = [m for m in matcher.match(d1, d2) if m.distance < 60]
    if len(good) < 8:
        return None
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    cv2.setRNGSeed(0)
    homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if homography is None or mask is None or (not np.isfinite(homography).all()):
        return None
    inliers = int(mask.sum())
    return (homography, inliers) if inliers >= 60 else None

def map_box(homography, box):
    x0, y0, x1, y1 = box
    pts = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]).reshape(-1, 1, 2)
    dst = cv2.perspectiveTransform(pts, homography).reshape(-1, 2)
    if not np.isfinite(dst).all():
        raise ValueError('Non-finite mapped ROI')
    return (math.floor(float(dst[:, 0].min())), math.floor(float(dst[:, 1].min())), math.ceil(float(dst[:, 0].max())), math.ceil(float(dst[:, 1].max())))

def prepare_frame(image, config):
    gray = _gray(image)
    width, height = config['input_size']
    if gray.shape != (height, width):
        gray = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    aligned = align_homography(gray, config)
    meta = {'H': None, 'dx': 0.0, 'dy': 0.0, 'image_ok': False, 'inliers': 0, 'reason': 'alignment_failed'}
    if aligned is None:
        return (gray, meta)
    homography, inliers = aligned
    center = np.array([width / 2, height / 2, 1.0])
    mapped = homography @ center
    if not np.isfinite(mapped).all() or abs(mapped[2]) < 1e-09:
        return (gray, meta)
    dx, dy = mapped[:2] / mapped[2] - center[:2]
    limit = config['alignment'].get('max_shift_px', 160)
    ok = abs(dx) <= limit and abs(dy) <= limit
    meta.update(H=homography if ok else None, dx=float(dx), dy=float(dy), image_ok=bool(ok), inliers=inliers, reason='' if ok else 'board_shift_exceeded')
    return (gray, meta)

def process_reference(frame, config, refdes, homography=None):
    rc = config['rois'][refdes]
    box = map_box(homography, rc['box']) if homography is not None else tuple(rc['box'])
    x0, y0, x1, y1 = box
    h, w = frame.shape
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError('roi_outside_image')
    roi = frame[y0:y1, x0:x1]
    mask = None
    if rc['feature'] == 'area_inv':
        work = 255 - roi if rc.get('invert', True) else roi
        mask = np.where(work > rc.get('threshold_inv', 120), 255, 0).astype(np.uint8)
        feature = float(np.count_nonzero(mask))
    elif rc['feature'] == 'cd40':
        rh, rw = roi.shape
        center = roi[rh // 3:2 * rh // 3, rw // 3:2 * rw // 3]
        feature = float(np.count_nonzero(center < 100)) / max(1, center.size)
    else:
        ref, _, _ = _load_ref(config)
        tx0, ty0, tx1, ty1 = rc['box']
        template = cv2.GaussianBlur(ref[ty0:ty1, tx0:tx1], (5, 5), 0)
        resized = cv2.resize(roi, (template.shape[1], template.shape[0]))
        resized = cv2.GaussianBlur(resized, (5, 5), 0)
        if rc['feature'] == 'sad':
            feature = float(np.abs(resized.astype(np.float32) - template.astype(np.float32)).mean())
        elif rc['feature'] == 'hcorr':
            ha = cv2.calcHist([resized], [0], None, [64], [0, 256]).astype(np.float32)
            hb = cv2.calcHist([template], [0], None, [64], [0, 256]).astype(np.float32)
            feature = float(cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL))
        else:
            raise ValueError('unsupported_feature')
    if not math.isfinite(feature):
        raise ValueError('non_finite_feature')
    return ({'area': int(np.count_nonzero(roi < 100)), 'feat': feature, 'box': box}, mask)

def judge_result(features, rc):
    if rc['type'] != 'missing':
        return 'uncertain'
    value, threshold = (features['feat'], rc['threshold'])
    missing = value > threshold if rc['op'] == '>' else value < threshold
    return 'missing' if missing else 'present'

def detect(image, config, backend='software'):
    if backend != 'software':
        raise NotImplementedError("FPGA backend is not implemented; use backend='software'")
    frame, meta = prepare_frame(image, config)
    items = []
    annotated = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image.copy()
    sx = image.shape[1] / frame.shape[1]
    sy = image.shape[0] / frame.shape[0]
    for name, rc in config['rois'].items():
        if not rc.get('active', True):
            continue
        item = {'refdes': name, 'task': rc['type'], 'status': 'invalid', 'area': None, 'feature': rc.get('feature'), 'feature_value': None, 'threshold': rc.get('threshold'), 'reason': meta['reason'], 'box': None}
        if meta['image_ok']:
            try:
                box = map_box(meta['H'], rc['box'])
                item['box'] = list(box)
                if rc['type'] != 'missing':
                    item.update(status='uncertain', reason='task_not_implemented')
                else:
                    feats, _ = process_reference(frame, config, name, meta['H'])
                    item.update(status=judge_result(feats, rc), area=feats['area'], feature_value=feats['feat'], reason='')
            except ValueError as error:
                item.update(status='invalid', reason=str(error))
        if item['box'] is not None:
            x0, y0, x1, y1 = item['box']
            original_box = [round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy)]
            item['original_box'] = original_box
            a, b, c, d = original_box
            color = (0, 200, 0) if item['status'] == 'present' else (0, 0, 255) if item['status'] == 'missing' else (0, 180, 255)
            cv2.rectangle(annotated, (a, b), (c, d), color, 3)
            cv2.putText(annotated, f"{name}:{item['status']}", (a, max(22, b - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        items.append(item)
    return ({'image_ok': meta['image_ok'], 'reason': meta['reason'], 'dx': meta['dx'], 'dy': meta['dy'], 'inliers': meta['inliers'], 'config_version': config['version'], 'backend': backend, 'coordinate_system': 'box=config_input; original_box=original_input', 'items': items}, annotated)
