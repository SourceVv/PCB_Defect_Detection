from pathlib import Path
ASSETS = Path(__file__).parent / 'assets'
from uuid import uuid4
import csv
import hashlib
import html
import json
import os
import shutil
import sys
import threading
import time
import traceback
import cv2
import numpy as np
APP_VERSION = 'component-workflow/0.2'
MAX_BYTES = 12 * 1024 * 1024

class ImageRejected(ValueError):
    pass

def sha(data):
    return hashlib.sha256(data).hexdigest()

def file_from_widget(value):
    if isinstance(value, dict) and len(value) == 1:
        name, item = next(iter(value.items()))
        name = item.get('metadata', {}).get('name', name)
    elif isinstance(value, (list, tuple)) and len(value) == 1:
        item = value[0]
        name = item['name']
    else:
        raise ImageRejected('请先选择一张照片')
    data = bytes(item['content'])
    if not data or len(data) > MAX_BYTES:
        raise ImageRejected('照片应为非空JPG/PNG文件，且不超过12MB')
    return (Path(str(name).replace('\\', '/')).name, data)

def decode(data):
    if not data or len(data) > MAX_BYTES:
        raise ImageRejected('照片应为非空文件，且不超过12MB')
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None or min(image.shape[:2]) < 160:
        raise ImageRejected('无法读取照片，或照片尺寸太小')
    if image.shape[0] * image.shape[1] > 20000000:
        raise ImageRejected('照片超过2000万像素，请先缩小')
    return image

def jpeg(image, max_width=1000):
    if image.shape[1] > max_width:
        image = cv2.resize(image, (max_width, round(max_width * image.shape[0] / image.shape[1])))
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ImageRejected('图像预览编码失败')
    return encoded.tobytes()

def prepare_board(image):
    original_h, original_w = image.shape[:2]
    scale = min(1.0, 1400 / max(original_h, original_w))
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image.copy()
    h, w = small.shape[:2]
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([30, 45, 25]), np.array([105, 255, 255]))
    closed = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea) if contours else None
    body = np.full((h, w), 255, np.uint8)
    substrate = 'full_frame'
    if contour is not None and cv2.contourArea(contour) >= 0.12 * h * w:
        x, y, bw, bh = cv2.boundingRect(contour)
        x0, y0, x1, y1 = (max(0, x - 8), max(0, y - 8), min(w, x + bw + 8), min(h, y + bh + 8))
        body[:] = 0
        cv2.drawContours(body, [contour], -1, 255, -1)
        substrate = 'green_substrate'
        warm = cv2.inRange(hsv, np.array([0, 35, 65]), np.array([29, 255, 255]))
        warm[body == 0] = 0
        count, labels, stats, _ = cv2.connectedComponentsWithStats(warm)
        large_holes = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] > 0.002 * np.count_nonzero(body)]
        if large_holes:
            body[np.isin(labels, large_holes)] = 0
    else:
        x0, y0, x1, y1 = (0, 0, w, h)
    crop = small[y0:y1, x0:x1]
    factor = 1600 / max(crop.shape[:2])
    width = max(160, int(round(crop.shape[1] * factor / 4)) * 4)
    height = max(160, int(round(crop.shape[0] * factor / 4)) * 4)
    canonical = cv2.resize(crop, (width, height), interpolation=cv2.INTER_AREA)
    mask = cv2.resize(body[y0:y1, x0:x1], (width, height), interpolation=cv2.INTER_NEAREST)
    transform = np.array([[width / (x1 - x0) * scale, 0, -x0 * width / (x1 - x0)], [0, height / (y1 - y0) * scale, -y0 * height / (y1 - y0)], [0, 0, 1.0]], dtype=np.float64)
    return (canonical, mask, {'original_shape': list(image.shape), 'crop_scaled_xyxy': [x0, y0, x1, y1], 'canonical_shape': list(canonical.shape), 'crop_method': substrate, 'original_to_canonical': transform.tolist()})

class ReferenceEngine:

    def __init__(self):
        self.image = self.mask = self.gray = self.descriptors = None
        self.keypoints = None
        self.metadata = None
        self.orb = cv2.ORB_create(nfeatures=6000, scaleFactor=1.15, nlevels=12, edgeThreshold=15)

    def enroll(self, name, data):
        image, mask, crop_meta = prepare_board(decode(data))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        kp, desc = self.orb.detectAndCompute(gray, mask)
        if desc is None or len(kp) < 120:
            raise ImageRejected('参考照片中可用特征太少，请使用清晰、完整的正常PCB照片')
        self.image, self.mask, self.gray = (image, mask, gray)
        self.keypoints, self.descriptors = (kp, desc)
        self.metadata = {'name': str(name), 'source_sha256': sha(data), 'crop': crop_meta, 'keypoints': len(kp), 'app_version': APP_VERSION}
        return self.metadata

    def align(self, data):
        if self.image is None:
            raise ImageRejected('请先登记正常参考板')
        original = decode(data)
        target, target_mask, target_meta = prepare_board(original)
        gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
        kp, desc = self.orb.detectAndCompute(gray, target_mask)
        if desc is None or len(kp) < 30:
            raise ImageRejected('待测照片中可用特征不足，无法可靠定位')
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc, self.descriptors, k=2)
        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance]
        if len(good) < 25:
            raise ImageRejected('与参考板的可靠匹配不足，请确认是同型号板且照片清晰')
        src = np.float32([kp[m.queryIdx].pt for m in good])
        dst = np.float32([self.keypoints[m.trainIdx].pt for m in good])
        cv2.setRNGSeed(0)
        matrix, accepted = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if matrix is None or accepted is None or (not np.isfinite(matrix).all()):
            raise ImageRejected('无法计算可靠的平面对齐变换')
        inliers = accepted.ravel().astype(bool)
        number = int(inliers.sum())
        ratio = number / len(good)
        hull_area = cv2.contourArea(cv2.convexHull(dst[inliers])) if number >= 3 else 0
        spread = hull_area / max(1, np.count_nonzero(self.mask))
        if number < 25 or ratio < 0.25 or spread < 0.12:
            raise ImageRejected(f'定位不可靠：有效匹配{number}个、内点率{ratio:.0%}；请确认板型及清晰度')
        projected = cv2.perspectiveTransform(src[inliers].reshape(-1, 1, 2), matrix).reshape(-1, 2)
        error = np.linalg.norm(projected - dst[inliers], axis=1)
        if float(np.median(error)) > 2.5:
            raise ImageRejected('配准误差过大，请重新拍摄更清晰的平面照片')
        h, w = self.gray.shape
        th, tw = target.shape[:2]
        corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
        polygon = cv2.perspectiveTransform(corners, matrix)
        if not np.isfinite(polygon).all() or not cv2.isContourConvex(polygon) or (not 0.1 * w * h < abs(cv2.contourArea(polygon)) < 10 * w * h):
            raise ImageRejected('图像变换几何异常，已停止检测')
        warped = cv2.warpPerspective(target, matrix, (w, h))
        valid = cv2.warpPerspective(np.full((th, tw), 255, np.uint8), matrix, (w, h), flags=cv2.INTER_NEAREST)
        valid = cv2.erode(valid, np.ones((5, 5), np.uint8))
        coverage = float(np.count_nonzero((valid > 0) & (self.mask > 0)) / max(1, np.count_nonzero(self.mask)))
        if coverage < 0.85:
            raise ImageRejected(f'待测照片仅覆盖参考检查区域的{coverage:.0%}，请拍摄更完整的板子')
        compare_mask = cv2.erode(cv2.bitwise_and(valid, self.mask), np.ones((9, 9), np.uint8))
        original_to_reference = matrix @ np.asarray(target_meta['original_to_canonical'])
        metadata = {'matches': len(good), 'inliers': number, 'inlier_ratio': ratio, 'inlier_spread': float(spread), 'median_reprojection_px': float(np.median(error)), 'coverage': coverage, 'target_crop': target_meta, 'cropped_target_to_reference': matrix.tolist(), 'original_target_to_reference': original_to_reference.tolist()}
        return (original, warped, compare_mask, metadata)
'Component descriptors, not whole-image pixel subtraction.\n\nBinary occupancy is supplied by the FPGA at runtime and NumPy in offline QA.\nHOG and color distributions describe the rectified component body and pads.\n'
import cv2
import numpy as np

def gradient_histogram(gray):
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude, angle = cv2.cartToPolar(gx, gy, angleInDegrees=True)
    bins = np.floor(angle % 180 / 20).astype(np.int32)
    cells = []
    for y in range(0, 64, 8):
        for x in range(0, 64, 8):
            h = np.bincount(bins[y:y + 8, x:x + 8].ravel(), weights=magnitude[y:y + 8, x:x + 8].ravel(), minlength=9)
            cells.append(h / max(1e-06, np.linalg.norm(h)))
    return np.concatenate(cells).astype(np.float32)

def patch_for(image, box):
    x0, y0, x1, y1 = map(int, box)
    crop = image[max(0, y0):min(image.shape[0], y1), max(0, x0):min(image.shape[1], x1)]
    if min(crop.shape[:2]) < 4:
        raise ValueError('元件区域过小或在照片之外')
    return cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA)

def gray_patch(patch):
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    lo, hi = np.percentile(gray, [3, 97])
    return np.clip((gray.astype(np.float32) - lo) * 255 / max(30, hi - lo), 0, 255).astype(np.uint8)

def describe(patch, binary=None):
    gray = gray_patch(patch)
    if binary is None:
        binary = (gray > 100).astype(np.uint8) * 255
    hog = gradient_histogram(gray)
    hog = hog / max(1e-06, np.linalg.norm(hog))
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    green = ((hsv[:, :, 0] >= 30) & (hsv[:, :, 0] <= 105) & (hsv[:, :, 1] > 60)).astype(np.float32)
    neutral = (hsv[:, :, 1] < 70).astype(np.float32)
    occupancy = []
    for channel in [green, neutral, (binary > 0).astype(np.float32)]:
        occupancy.extend(cv2.resize(channel, (4, 4), interpolation=cv2.INTER_AREA).ravel())
    return np.r_[hog, np.array(occupancy, dtype=np.float32) * 0.35].astype(np.float32)

def distances(feature, samples):
    return np.mean((np.asarray(samples) - feature) ** 2, axis=1)

def classify(feature, models):
    scores = {state: float(np.min(distances(feature, features))) for state, features in models.items() if len(features)}
    ranked = sorted(scores, key=scores.get)
    winner = ranked[0]
    margin = (scores[ranked[1]] - scores[winner]) / max(scores[ranked[1]], 1e-09) if len(ranked) > 1 else 0.0
    return (winner, margin, scores)

def classify_shifted(image, box, models):
    scores = {state: float('inf') for state in models}
    for dy in [-3, 0, 3]:
        for dx in [-3, 0, 3]:
            q = np.array(box) + [dx, dy, dx, dy]
            f = describe(patch_for(image, q))
            _, _, s = classify(f, models)
            scores = {state: min(v, s[state]) for state, v in scores.items()}
    ranked = sorted(scores, key=scores.get)
    win = ranked[0]
    margin = (scores[ranked[1]] - scores[win]) / max(scores[ranked[1]], 1e-09)
    return (win, margin, scores)
import cv2, numpy as np

def orientation_feature(patch):
    g = cv2.cvtColor(cv2.resize(patch, (64, 64)), cv2.COLOR_BGR2GRAY)
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(g)
    hog = gradient_histogram(g).reshape(8, 8, 9)
    hog = np.concatenate([hog[:3, :3].ravel(), hog[-3:, -3:].ravel(), hog[:3, -3:].ravel(), hog[-3:, :3].ravel()])
    hog = hog / max(np.linalg.norm(hog), 1e-06)
    return hog
from pathlib import Path
import cv2, numpy as np

def ring_score(gray, center, radius):
    best = (-1, None)
    for r in np.linspace(radius * 0.75, radius * 1.25, 5):
        k = int(np.ceil(r * 1.7))
        yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
        d = np.sqrt(xx * xx + yy * yy) / r
        ring = (d > 0.8) & (d < 1.15)
        other = (d < 0.65) | (d > 1.35) & (d < 1.6)
        kernel = other.astype(np.float32) / other.sum() - ring.astype(np.float32) / ring.sum()
        response = cv2.filter2D(gray.astype(np.float32), -1, kernel)
        x, y = np.rint(center).astype(int)
        search = response[max(0, y - 4):y + 5, max(0, x - 4):x + 5]
        if search.size and search.max() > best[0]:
            best = (float(search.max()), r)
    return best

def marker(im, box, center):
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    return [ring_score(gray, (center[0] * w, center[1] * h), min(w, h) * 0.085), ring_score(gray, ((1 - center[0]) * w, (1 - center[1]) * h), min(w, h) * 0.085)]

class HardwareBindings:

    def __init__(self, root, driver=None, bindings=None):
        self.root = Path(root).resolve()
        hw, sw = (self.root / 'pcb_b_roi_v03', self.root / 'pcb_b_closure_v032')
        self.bit = hw / 'release/roi_v0_3/pcb_roi.bit'
        sources = [hw / 'software/roi_driver.py', hw / 'software/transport.py', sw / 'software/final_roi_api.py', sw / 'software/aoi_roi_service.py']
        for path in [self.bit, self.bit.with_suffix('.hwh')] + sources:
            if not path.is_file():
                raise FileNotFoundError('缺少板端文件：' + str(path))
        if bindings is None:
            for path in (hw / 'software', sw / 'software'):
                sys.path.insert(0, str(path))
            import roi_driver
            import final_roi_api
            for module, path in [(roi_driver, sources[0]), (final_roi_api, sources[2])]:
                if Path(module.__file__).resolve() != path.resolve():
                    raise RuntimeError('内核已加载其他目录的模块：' + module.__name__)
            bindings = (roi_driver.RoiDriver.from_bitfile, final_roi_api.create_service)
        self.driver_factory, self.service_factory = bindings
        self.driver = driver
        self.engine = ReferenceEngine()
        self.lock = threading.Lock()
        self.fault = None
        self.profile_dir = None
        self.last = None
        self.saved_dir = None
        self.source_hashes = {str(path.relative_to(self.root)): sha(path.read_bytes()) for path in [self.bit, self.bit.with_suffix('.hwh')] + sources}
import base64, zlib
'Runtime appended to the standalone notebook by package_app.py.'
STATE_CN = {'normal': '未见已启用缺陷', 'missing': '缺件', 'rotated_180': '焊反（180°）', 'tilted': '歪斜', 'uncertain': '待确认', 'not_calibrated': '未标定', 'not_applicable': '不适用', 'candidate_reverse': '疑似焊反（扩展规则）', 'candidate_normal': '方向外观一致（扩展规则）'}
TYPE_CN = {'U': '芯片/IC', 'C': '电容', 'R': '电阻', 'L': '电感', 'X': '晶振', 'SW': '开关', 'LED': '发光器件', 'MIC': '麦克风'}
DISPLAY_BOXES = {'C14': [635, 293, 674, 315], 'C17': [635, 317, 674, 337]}

def task_cn(task, state):
    if state == 'normal':
        return {'presence': '存在', 'orientation': '方向一致', 'tilt': '未见歪斜'}.get(task, STATE_CN[state])
    return STATE_CN[state]

def component_type(name):
    for prefix in ['MIC', 'LED', 'SW', 'U', 'C', 'R', 'L', 'X']:
        if name.upper().startswith(prefix):
            return TYPE_CN[prefix]
    return '已登记元件'

def plain_json(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: plain_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain_json(v) for v in value]
    return value

def score_choice(scores):
    order = sorted(scores, key=scores.get)
    first = order[0]
    margin = (scores[order[1]] - scores[first]) / max(scores[order[1]], 1e-09) if len(order) > 1 else 0.0
    return (first, float(margin))

class ComponentVision:

    def __init__(self):
        self.model = json.loads((ASSETS / 'component_model.json').read_text(encoding='utf-8'))
        self.model_hash = sha((ASSETS / 'component_model.json').read_bytes())
        atlas = cv2.imdecode(np.frombuffer((ASSETS / 'atlas_reference.png').read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        atlas_mask = cv2.imdecode(np.frombuffer((ASSETS / 'atlas_mask.png').read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        self.atlas = ReferenceEngine()
        self.atlas.image, self.atlas.mask = (atlas, atlas_mask)
        self.atlas.gray = cv2.cvtColor(atlas, cv2.COLOR_BGR2GRAY)
        self.atlas.keypoints, self.atlas.descriptors = self.atlas.orb.detectAndCompute(self.atlas.gray, atlas_mask)
        self.atlas.metadata = {'board_model': self.model['board_model']}
        self.engine = None
        self.reference = None
        self.reference_to_atlas = None
        self.custom = {}
        self.models = {n: {s: np.asarray(v, np.float32) for s, v in states.items()} for n, states in self.model['models'].items()}
        self.orientation_models = {n: {s: np.asarray(v, np.float32) for s, v in states.items()} for n, states in self.model['orientation'].items()}

    def enroll(self, name, data):
        engine = ReferenceEngine()
        metadata = engine.enroll(name, data)
        _, reference, valid, alignment = self.atlas.align(data)
        suspicious = []
        for n in self.model['missing']:
            predicted, margin, _ = classify_shifted(reference, self.model['boxes'][n], self.models[n])
            if predicted == 'missing' and margin > 0.45:
                suspicious.append(n + '可能缺件')
        for n, center in [('U1', (0.8, 0.2)), ('U5', (0.18, 0.17))]:
            values = marker(reference, self.model['bodies'][n], center)
            if values[0][0] < 3.0 and values[1][0] > 3.0:
                suspicious.append(n + '可能焊反')
        if suspicious:
            raise ImageRejected('参考板未通过已知元件检查：' + '、'.join(suspicious) + '。请使用已人工确认的正常板。')
        self.engine, self.reference = (engine, reference)
        self.reference_to_atlas = np.array(alignment['cropped_target_to_reference'])
        self.reference_valid = valid
        for n, states in self.models.items():
            f = describe(patch_for(reference, self.model['boxes'][n]))
            if n in self.model['tilt']:
                f = f[:576]
            states['normal'] = np.vstack([states['normal'], f])
        for n, states in self.orientation_models.items():
            p = patch_for(reference, self.model['bodies'][n])
            states['normal'] = np.vstack([states['normal'], orientation_feature(p)])
            if n not in ['U1', 'U5']:
                states['rotated_180'] = np.vstack([states['rotated_180'], orientation_feature(cv2.rotate(p, cv2.ROTATE_180))])
        self.metadata = dict(metadata, board_model=self.model['board_model'], model_sha256=self.model_hash, reference_alignment=alignment, scope='registered component positions only')
        return self.metadata

    def align(self, data):
        if self.engine is None:
            raise ImageRejected('请先登记正常参考板')
        original, warped, valid, alignment = self.engine.align(data)
        h, w = self.atlas.gray.shape
        aligned = cv2.warpPerspective(warped, self.reference_to_atlas, (w, h))
        valid = cv2.warpPerspective(valid, self.reference_to_atlas, (w, h), flags=cv2.INTER_NEAREST)
        transform = self.reference_to_atlas @ np.array(alignment['original_target_to_reference'])
        alignment = dict(alignment, original_target_to_atlas=transform.tolist())
        return (original, aligned, valid, alignment)

    def prepare_tiles(self, aligned):
        entries = []
        for n in self.model['missing'] + self.model['tilt']:
            box = self.model['boxes'][n]
            shifts = [(0, 0)] if n in self.model['tilt'] else [(dx, dy) for dy in [-3, 0, 3] for dx in [-3, 0, 3]]
            for dx, dy in shifts:
                p = patch_for(aligned, np.asarray(box) + [dx, dy, dx, dy])
                entries.append(dict(refdes=n, patch=p))
        mosaic = np.zeros(((len(entries) + 7) // 8 * 64, 512), np.uint8)
        for i, e in enumerate(entries):
            y, x = (i // 8 * 64, i % 8 * 64)
            mosaic[y:y + 64, x:x + 64] = gray_patch(e['patch'])
            e['tile'] = [x, y, x + 64, y + 64]
        return (mosaic, entries)

    def source_size(self, box, alignment):
        x0, y0, x1, y1 = box
        points = np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]])
        points = cv2.perspectiveTransform(points, np.linalg.inv(np.array(alignment['original_target_to_atlas'])))[0]
        widths = [np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])]
        heights = [np.linalg.norm(points[1] - points[2]), np.linalg.norm(points[3] - points[0])]
        return (float(min(widths + heights)), points)

    def classify_components(self, aligned, valid, alignment, entries, mask):
        scores = {n: {s: float('inf') for s in states} for n, states in self.models.items()}
        for e in entries:
            n = e['refdes']
            x0, y0, x1, y1 = e['tile']
            f = describe(e['patch'], mask[y0:y1, x0:x1])
            if n in self.model['tilt']:
                f = f[:576]
            for s, values in self.models[n].items():
                scores[n][s] = min(scores[n][s], float(np.min(distances(f, values))))
        items = []
        for n in self.model['missing'] + self.model['tilt']:
            box = DISPLAY_BOXES.get(n, self.model['boxes'][n])
            x0, y0, x1, y1 = box
            covered = float(np.mean(valid[y0:y1, x0:x1] > 0))
            source_short, polygon = self.source_size(box, alignment)
            state, margin = score_choice(scores[n])
            applicable = 'tilt' if n in self.model['tilt'] else 'presence'
            reason = '元件轮廓方向特征' if applicable == 'tilt' else '局部形状、颜色分布与二值占比特征'
            if covered < 0.98 or source_short < 10:
                state = 'uncertain'
                reason = '元件区域不完整或原图中的元件细节不足'
            elif margin < 0.1 or min(scores[n].values()) > 0.004:
                state = 'uncertain'
                reason = '与已标定状态的区分不足，需要复核'
            item = dict(refdes=n, component_type=component_type(n), box=box, feature_box=self.model['boxes'][n], status=state, presence=state if applicable == 'presence' else 'not_calibrated', tilt=state if applicable == 'tilt' else 'not_calibrated', orientation='not_applicable', reason=reason, margin=margin, scores=scores[n], source_short_edge=source_short, coverage=covered, original_image_polygon=polygon.tolist(), inferred_orientation=False)
            if n in self.model['bodies'] and state == 'normal':
                self.check_orientation(item, aligned)
            items.append(item)
        for n, spec in self.custom.items():
            box = spec['box']
            short, polygon = self.source_size(box, alignment)
            x0, y0, x1, y1 = box
            covered = float(np.mean(valid[y0:y1, x0:x1] > 0))
            item = dict(refdes=n, component_type=component_type(n), box=box, status='uncertain', presence='not_calibrated', tilt='not_calibrated', orientation='uncertain', reason='自定义方向区域；缺件和歪斜尚未标定', margin=0.0, scores={}, source_short_edge=short, coverage=covered, original_image_polygon=polygon.tolist(), inferred_orientation=True)
            if short >= 20 and covered >= 0.98:
                forward = orientation_feature(patch_for(self.reference, box))
                backward = orientation_feature(cv2.rotate(patch_for(self.reference, box), cv2.ROTATE_180))
                query = orientation_feature(patch_for(aligned, box))
                winner, margin, d = classify(query, {'normal': [forward], 'rotated_180': [backward]})
                asymmetry = float(np.mean((forward - backward) ** 2))
                item.update(margin=margin, orientation_scores=d, reference_asymmetry=asymmetry)
                if margin >= 0.2 and asymmetry > 0.0003:
                    item['orientation'] = 'candidate_reverse' if winner == 'rotated_180' else 'candidate_normal'
                    if winner == 'rotated_180':
                        item['status'] = 'candidate_reverse'
            items.append(item)
        return items

    def check_orientation(self, item, aligned):
        n = item['refdes']
        box = self.model['bodies'][n]
        if n in ['U1', 'U5']:
            center = (0.8, 0.2) if n == 'U1' else (0.18, 0.17)
            values = marker(aligned, box, center)
            expected, opposite = (values[0][0], values[1][0])
            shape_state, shape_margin, shape_scores = classify(orientation_feature(patch_for(aligned, box)), self.orientation_models[n])
            item['orientation_evidence'] = {'expected_mark_response': expected, 'opposite_mark_response': opposite, 'shape_state': shape_state, 'shape_margin': shape_margin, 'method': 'local circular polarity mark with shape disagreement gate, calibrated on s01'}
            if expected < 3.0 and opposite > 3.0:
                if shape_state == 'normal' and shape_margin > 0.18:
                    item.update(orientation='uncertain', status='uncertain', reason='芯片存在，但极性标记与封装方向特征不一致，需要复核')
                else:
                    item.update(orientation='rotated_180', status='rotated_180', reason='极性圆点出现在反向端，正常端标记响应不足')
            elif expected >= 3.5:
                item['orientation'] = 'normal'
            else:
                item.update(orientation='uncertain', status='uncertain', reason='芯片存在，但极性标记不足以确认方向')
        else:
            p = patch_for(aligned, box)
            winner, margin, d = classify(orientation_feature(p), self.orientation_models[n])
            normal = orientation_feature(patch_for(self.reference, box))
            reverse = orientation_feature(cv2.rotate(patch_for(self.reference, box), cv2.ROTATE_180))
            asymmetry = float(np.mean((normal - reverse) ** 2))
            item.update(inferred_orientation=True, orientation_scores=d, orientation_margin=margin, reference_asymmetry=asymmetry)
            if margin >= 0.2 and asymmetry > 0.0003:
                item['orientation'] = 'candidate_reverse' if winner == 'rotated_180' else 'candidate_normal'
                if winner == 'rotated_180':
                    item.update(status='candidate_reverse', reason='参考元件的反向形状更接近；本位号缺少真实焊反验证样本')
                else:
                    item['reason'] += '；方向仅为扩展规则候选，未做真实焊反验证'
            else:
                item.update(orientation='uncertain', status='uncertain', reason='元件存在，但方向标记或形状不够明确')

class ComponentSession(HardwareBindings):

    def __init__(self, root, driver=None, bindings=None):
        super().__init__(root, driver, bindings)
        self.vision = ComponentVision()
        self.engine = None
        self.profile_dir = None

    def register(self, name, data):
        if not self.lock.acquire(False):
            raise RuntimeError('请等待当前任务结束')
        try:
            vision = ComponentVision()
            meta = vision.enroll(name, data)
            if shutil.disk_usage(self.root).free < 30 * 1024 * 1024:
                raise OSError('保存参考板需要至少30MB空间')
            target = self.root / 'component_registered_boards' / ('board_' + uuid4().hex)
            target.mkdir(parents=True, exist_ok=False)
            (target / 'source.bin').write_bytes(data)
            (target / 'profile.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
            self.vision, self.engine, self.profile_dir = (vision, vision.engine, target)
            self.last = self.saved_dir = None
            return meta
        finally:
            self.lock.release()

    def profiles(self):
        return sorted((self.root / 'component_registered_boards').glob('board_*/profile.json'))

    def load_profile(self, path):
        if not self.lock.acquire(False):
            raise RuntimeError('请等待当前任务结束')
        try:
            p = Path(path).resolve()
            base = (self.root / 'component_registered_boards').resolve()
            if not p.is_relative_to(base):
                raise ImageRejected('档案路径无效')
            meta = json.loads(p.read_text(encoding='utf-8'))
            data = (p.parent / 'source.bin').read_bytes()
            if sha(data) != meta['source_sha256']:
                raise ImageRejected('参考文件校验失败')
            if meta.get('model_sha256') != self.vision.model_hash:
                raise ImageRejected('档案模型版本不一致，请重新登记参考板')
            vision = ComponentVision()
            vision.enroll(meta['name'], data)
            if (p.parent / 'custom_components.json').exists():
                for name, spec in json.loads((p.parent / 'custom_components.json').read_text(encoding='utf-8')).items():
                    self.validate_custom(vision, name, spec['box'])
                    vision.custom[name] = spec
            self.vision, self.engine, self.profile_dir = (vision, vision.engine, p.parent)
            self.last = self.saved_dir = None
            return vision.metadata
        finally:
            self.lock.release()

    @staticmethod
    def validate_custom(vision, name, box):
        if vision.reference is None:
            raise ImageRejected('请先登记参考板')
        if not name or len(name) > 32 or any((c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in name)):
            raise ImageRejected('位号请使用字母、数字、下划线，最多32字符')
        if name in vision.model['boxes']:
            raise ImageRejected('此位号已有标定，请使用另一个位号')
        h, w = vision.reference.shape[:2]
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h and (min(x1 - x0, y1 - y0) >= 20)):
            raise ImageRejected('区域必须在参考图范围内，宽高至少20；坐标对应下方参考网格图')

    def add_custom(self, name, box):
        if not self.lock.acquire(False):
            raise RuntimeError('请等待当前任务结束')
        try:
            self.validate_custom(self.vision, name, box)
            self.vision.custom[name] = {'box': list(box), 'task': 'orientation_180', 'validation': 'reference_only'}
            (self.profile_dir / 'custom_components.json').write_text(json.dumps(self.vision.custom, ensure_ascii=False, indent=2), encoding='utf-8')
            self.last = self.saved_dir = None
        finally:
            self.lock.release()

    def inspect(self, name, data):
        if not self.lock.acquire(False):
            raise RuntimeError('已有检测正在进行')
        try:
            self.last = self.saved_dir = None
            if self.fault:
                raise RuntimeError('硬件会话已停止，请保留内核及错误信息')
            started = time.perf_counter()
            original, aligned, valid, alignment = self.vision.align(data)
            mosaic, entries = self.vision.prepare_tiles(aligned)
            h, w = mosaic.shape
            try:
                if self.driver is None:
                    self.driver = self.driver_factory(str(self.bit), timeout=10.0)
                    self.driver.recover()
                status = self.driver.read_status()
                if status.get('abi') != 3 or any((status.get(k) for k in ('busy', 'inpacket', 'cfg_error', 'frame_error'))):
                    raise RuntimeError('FPGA状态异常：' + str(status))
                self.service = self.service_factory(self.driver, width=w, height=h, rois=[{'name': 'components', 'box': [0, 0, w, h], 'enabled': True}], threshold=100, invert=False, roi_enable=True)
                self.service.configure()
                operator = self.service.process(mosaic, frame_id='component_' + uuid4().hex)
                mask = operator['mask']
                if mask.dtype != np.uint8 or mask.shape != mosaic.shape or (not np.isin(mask, [0, 255]).all()):
                    raise RuntimeError('FPGA返回的元件二值特征图格式异常')
            except BaseException as exc:
                self.fault = type(exc).__name__ + ': ' + str(exc)
                raise
            items = self.vision.classify_components(aligned, valid, alignment, entries, mask)
            annotated = original.copy()
            for item in items:
                state = item['status']
                color = (0, 0, 225) if state in ['missing', 'rotated_180', 'tilted', 'candidate_reverse'] else (0, 170, 0) if state == 'normal' else (0, 155, 230)
                points = np.rint(item['original_image_polygon']).astype(np.int32)
                cv2.polylines(annotated, [points], True, color, 2)
                x, y = points.min(axis=0)
                label = item['refdes'] + ('' if state == 'normal' else ': ' + state)
                cv2.putText(annotated, label, (max(0, int(x)), max(16, int(y) - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
            self.last = dict(frame_id=operator['frame_id'], name=str(name), source_bytes=bytes(data), reference=dict(self.vision.metadata), items=items, alignment=alignment, aligned=aligned, reference_image=self.vision.reference.copy(), original_annotated=annotated, operator=operator, mosaic=mosaic, valid=valid, seconds=time.perf_counter() - started, custom_components=dict(self.vision.custom), scope='Only registered component checks; no whole-board PASS', processing={'ARM': 'registration, shape/color features, polarity marks, semantic decisions', 'FPGA': 'threshold normalized component patches; returned mask supplies occupancy features', 'gray_threshold': 100, 'frame_contains': 'component patches, not a difference image'})
            return self.last
        finally:
            self.lock.release()

    def save(self):
        if not self.lock.acquire(False):
            raise RuntimeError('请等待当前任务结束')
        try:
            if self.last is None:
                raise ImageRejected('请先检测')
            if self.saved_dir:
                return self.saved_dir
            if shutil.disk_usage(self.root).free < 30 * 1024 * 1024:
                raise OSError('保存需要至少30MB空间')
            r = self.last
            target = self.root / 'integration_results' / r['frame_id']
            target.mkdir(parents=True, exist_ok=False)
            for key in ['aligned', 'reference_image', 'original_annotated', 'mosaic', 'valid']:
                if not cv2.imwrite(str(target / (key + '.png')), r[key]):
                    raise OSError('图像保存失败')
            np.save(target / 'actual_fpga_mask.npy', r['operator']['mask'], allow_pickle=False)
            (target / 'input_source.bin').write_bytes(r['source_bytes'])
            record = {k: r[k] for k in ['frame_id', 'name', 'reference', 'items', 'alignment', 'seconds', 'scope', 'processing', 'custom_components']}
            record.update(input_sha256=sha(r['source_bytes']), source_sha256=self.source_hashes, model_sha256=self.vision.model_hash, app_version=APP_VERSION, operator={k: v for k, v in r['operator'].items() if k != 'mask'})
            (target / 'result.json').write_text(json.dumps(plain_json(record), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            with (target / 'components.csv').open('w', encoding='utf-8-sig', newline='') as f:
                out = csv.writer(f)
                out.writerow(['位号', '类型', '结论', '缺件检查', '方向检查', '歪斜检查', '说明'])
                for i in r['items']:
                    out.writerow([i['refdes'], i['component_type']] + [task_cn(k, i[k]) for k in ['status', 'presence', 'orientation', 'tilt']] + [i['reason']])
            self.saved_dir = target
            return target
        finally:
            self.lock.release()
PHOTO_REQUIREMENTS = '<b>照片要求</b><br>同型号、同一面；平放拍摄，整块板及待检元件完整入镜。允许图片尺寸、板子位置、缩放和面内旋转不同，程序自动对齐。<br>避免强反光、阴影和遮挡；对焦到小元件。最小待检元件在原图中短边建议至少15像素，芯片圆点、缺口或方向文字必须看得清。不要仅放大模糊照片。<br>JPG/PNG，单张不超过12MB、2000万像素。参考板须人工确认元件齐全、方向正确。'

class ComponentUI:

    def __init__(self, session):
        import ipywidgets as w
        self.w, self.session, self.busy = (w, session, False)
        self.reference_pending = False
        self.reference_upload = w.FileUpload(accept='.jpg,.jpeg,.png', multiple=False, description='选择正常参考板')
        self.register_button = w.Button(description='登记正常板', button_style='info')
        self.profile_list = w.Dropdown(description='已存参考', layout=w.Layout(width='460px'))
        self.load_button = w.Button(description='载入参考')
        self.reference_note = w.HTML()
        self.reference_image = w.Image(format='jpeg', layout=w.Layout(max_width='650px', display='none'))
        self.target_upload = w.FileUpload(accept='.jpg,.jpeg,.png', multiple=False, description='选择待测照片')
        self.inspect_button = w.Button(description='开始元件检测', button_style='primary', icon='search', disabled=True)
        self.save_button = w.Button(description='保存结果', icon='save', disabled=True)
        self.status = w.HTML()
        self.result_image = w.Image(format='jpeg', layout=w.Layout(max_width='100%', display='none'))
        self.table = w.HTML()
        self.component_select = w.Dropdown(description='局部对照', options=[], layout=w.Layout(width='480px'))
        self.component_image = w.Image(format='jpeg', layout=w.Layout(max_width='640px', display='none'))
        self.component_note = w.HTML()
        self.saved = w.HTML()
        self.errors = w.Output()
        self.details = w.Accordion(children=[self.errors])
        self.details.set_title(0, '运行详情')
        self.details.selected_index = None
        self.custom_name = w.Text(description='新位号', placeholder='例如 SOCKET1')
        self.custom_box = w.Text(description='元件区域', placeholder='x0,y0,x1,y1；对应下方网格图', layout=w.Layout(width='480px'))
        self.custom_button = w.Button(description='登记该元件方向', disabled=True)
        self.grid = w.Image(format='jpeg', layout=w.Layout(max_width='100%', display='none'))
        self.custom_panel = w.Accordion(children=[w.VBox([w.HTML('<p>扩展到其他芯片/底座：在正常参考图中登记整个封装区域（含可辨识的方向标记）。系统比较正向与180°反向的形状特征。外观对称或标记模糊时只能待确认；新位号的缺件、歪斜尚未标定，不能由方向一致推断完全合格。坐标来自下面的参考网格图。</p>'), self.custom_name, self.custom_box, self.custom_button, self.grid])])
        self.custom_panel.set_title(0, '扩展其他芯片/底座的方向检查')
        self.custom_panel.selected_index = None
        tips = lambda: w.HTML('<div style="padding:10px;background:#eff6ff;line-height:1.6">' + PHOTO_REQUIREMENTS + '</div>')
        self.widget = w.VBox([w.HTML('<style>.output_scroll{height:auto!important;max-height:none!important;overflow-y:visible!important;box-shadow:none!important}.widget-box{flex-shrink:0}.pcb-component-app{width:100%;min-width:0}</style>'), w.HTML('<h2>PCB 元件缺件、焊反与歪斜检查</h2><p>当前内置板型：XW133-X9-P0_V1.0。先登记正常参考，再上传待测板；不要求固定拍照模板。</p>'), w.HTML('<h3>1．登记正常参考</h3>'), w.HBox([w.VBox([self.reference_upload, self.register_button]), tips()], layout=w.Layout(flex_flow='row wrap')), w.HBox([self.profile_list, self.load_button]), self.reference_note, self.reference_image, self.custom_panel, w.HTML('<h3>2．上传待测板</h3>'), w.HBox([self.target_upload, tips()], layout=w.Layout(flex_flow='row wrap')), w.HBox([self.inspect_button, self.save_button]), self.status, self.result_image, self.table, self.component_select, self.component_note, self.component_image, self.saved, self.details, w.HTML('<p style="color:#555"><b>覆盖范围：</b>16个位号检查缺件（含LED5、LED7、C13、C20、SW2、X1、X2等）；U1、U5检查180°焊反；C14、C17检查歪斜。U2/U3/U4/U6及自定义位号的方向为扩展规则，仍需真实焊反样本验证。其他未登记元件未检测；C8/C25/C32翘起不由单张平面图判定。绿色仅表示已启用项目未见异常，不代表整板合格。</p>')], layout=w.Layout(width='100%'))
        self.widget.add_class('pcb-component-app')
        self.register_button.on_click(self.on_register)
        self.load_button.on_click(self.on_load)
        self.inspect_button.on_click(self.on_inspect)
        self.save_button.on_click(self.on_save)
        self.custom_button.on_click(self.on_custom)
        self.reference_upload.observe(self.reference_changed, names='value')
        self.target_upload.observe(self.target_changed, names='value')
        self.component_select.observe(self.show_component, names='value')
        self.refresh_profiles()
        self.set_busy(False)
        self.message('请上传已人工确认正常的参考板，点击“登记正常板”。')

    def message(self, text, error=False):
        color = '#b91c1c' if error else '#334155'
        self.status.value = '<p style="padding:12px;background:#f1f5f9;color:' + color + '">' + html.escape(str(text)) + '</p>'

    def set_busy(self, value):
        self.busy = value
        fault = self.session.fault is not None
        for c in [self.reference_upload, self.register_button, self.target_upload, self.load_button, self.profile_list, self.custom_name, self.custom_box]:
            c.disabled = value or fault
        ready = self.session.engine is not None and (not self.reference_pending)
        self.inspect_button.disabled = value or fault or (not ready)
        self.custom_button.disabled = value or fault or (not ready)
        self.save_button.disabled = value or fault or self.session.last is None

    def refresh_profiles(self):
        options = []
        for p in self.session.profiles():
            try:
                meta = json.loads(p.read_text(encoding='utf-8'))
                options.append((meta['name'] + ' · ' + p.parent.name[-6:], str(p)))
            except (OSError, ValueError, KeyError):
                continue
        self.profile_list.options = options

    def clear_result(self):
        self.session.last = self.session.saved_dir = None
        self.table.value = self.saved.value = self.component_note.value = ''
        self.component_select.options = []
        self.result_image.layout.display = self.component_image.layout.display = 'none'
        self.result_image.value = self.component_image.value = b''

    def reference_changed(self, change=None):
        if self.busy:
            return
        self.clear_result()
        self.reference_pending = True
        self.set_busy(False)
        self.message('参考照片已更换，请先点击“登记正常板”。')

    def target_changed(self, change=None):
        if self.busy:
            return
        self.clear_result()
        self.set_busy(False)
        if self.session.fault:
            self.message('硬件会话已停止，请保留内核和运行详情。', True)
        else:
            self.message('待测照片已选择，可以开始元件检测。')

    def error(self, exc):
        self.message('硬件会话已停止，请保留当前内核及运行详情。' if self.session.fault else str(exc), True)
        with self.errors:
            traceback.print_exc()
        self.details.selected_index = 0

    def reference_ready(self):
        vision = self.session.vision
        self.reference_note.value = '<p>当前参考：' + html.escape(vision.metadata['name']) + '；已按内置元件图谱对齐。红框名称是真实位号。</p>'
        preview = vision.reference.copy()
        grid = preview.copy()
        for n in vision.model['missing'] + vision.model['tilt']:
            x0, y0, x1, y1 = DISPLAY_BOXES.get(n, vision.model['boxes'][n])
            cv2.rectangle(preview, (x0, y0), (x1, y1), (0, 0, 220), 1)
            cv2.putText(preview, n, (x0, max(16, y0 - 4)), 0, 0.55, (0, 0, 220), 1)
        h, w = grid.shape[:2]
        for x in range(0, w, 100):
            cv2.line(grid, (x, 0), (x, h - 1), (200, 150, 0), 1)
            cv2.putText(grid, str(x), (x + 2, 20), 0, 0.6, (0, 0, 230), 2)
        for y in range(0, h, 100):
            cv2.line(grid, (0, y), (w - 1, y), (200, 150, 0), 1)
            cv2.putText(grid, str(y), (2, y + 18), 0, 0.6, (0, 0, 230), 2)
        self.reference_image.value = jpeg(preview, 1000)
        self.reference_image.layout.display = ''
        self.grid.value = jpeg(grid, 1600)
        self.grid.layout.display = ''
        self.refresh_profiles()

    def on_register(self, button=None):
        if self.busy or self.session.fault:
            return
        self.clear_result()
        self.set_busy(True)
        try:
            name, data = file_from_widget(self.reference_upload.value)
            self.session.register(name, data)
            self.reference_pending = False
            self.reference_ready()
            self.message('正常参考已保存。请确认预览中的位号框对应正确，再上传待测照片。')
        except Exception as exc:
            self.error(exc)
        finally:
            self.set_busy(False)

    def on_load(self, button=None):
        if self.busy or self.session.fault:
            return
        self.clear_result()
        self.set_busy(True)
        try:
            if self.profile_list.value is None:
                raise ImageRejected('没有已保存的参考板')
            self.session.load_profile(self.profile_list.value)
            self.reference_pending = False
            self.reference_ready()
            self.message('已载入正常参考，可以检测。')
        except Exception as exc:
            self.error(exc)
        finally:
            self.set_busy(False)

    def on_custom(self, button=None):
        if self.busy or self.session.fault or self.reference_pending:
            return
        self.set_busy(True)
        try:
            box = [int(x.strip()) for x in self.custom_box.value.replace('，', ',').split(',')]
            if len(box) != 4:
                raise ImageRejected('区域应为四个整数：x0,y0,x1,y1')
            self.session.add_custom(self.custom_name.value.strip(), box)
            self.clear_result()
            self.message('已保存该元件的方向检查区域。结果将标明“扩展规则”，缺件/歪斜未标定。')
        except Exception as exc:
            self.error(exc)
        finally:
            self.set_busy(False)

    def on_inspect(self, button=None):
        if self.busy or self.session.fault:
            return
        if self.reference_pending:
            self.message('请先登记新参考照片。', True)
            return
        self.clear_result()
        self.errors.clear_output()
        self.set_busy(True)
        self.message('正在自动对齐并逐个检查元件，请等待。')
        try:
            name, data = file_from_widget(self.target_upload.value)
            self.result_image.value = jpeg(decode(data))
            self.result_image.layout.display = ''
            result = self.session.inspect(name, data)
            self.result_image.value = jpeg(result['original_annotated'], 1300)
            defects = [i for i in result['items'] if i['status'] in ['missing', 'rotated_180', 'tilted']]
            pending = [i for i in result['items'] if i['status'] in ['uncertain', 'candidate_reverse']]
            self.message(f"{name}：已登记范围内检出{len(defects)}项缺陷，{len(pending)}项待确认；耗时{result['seconds']:.1f}秒。未登记元件不计入结论。")
            rows = []
            for i in result['items']:
                values = [i['refdes'], i['component_type'], task_cn('presence', i['presence']), task_cn('orientation', i['orientation']), task_cn('tilt', i['tilt'])]
                rows.append('<tr>' + ''.join(('<td style="padding:7px;border-bottom:1px solid #ddd">' + html.escape(v) + '</td>' for v in values)) + '</tr>')
            self.table.value = '<table style="width:100%;text-align:left"><tr><th>位号</th><th>元件</th><th>缺件检查</th><th>方向检查</th><th>歪斜检查</th></tr>' + ''.join(rows) + '</table>'
            ordered = defects + pending + [i for i in result['items'] if i not in defects + pending]
            self.component_select.options = [(i['refdes'] + ' · ' + STATE_CN[i['status']], i['refdes']) for i in ordered]
            self.component_select.value = ordered[0]['refdes'] if ordered else None
            self.show_component()
        except Exception as exc:
            self.error(exc)
        finally:
            self.set_busy(False)

    def show_component(self, change=None):
        result = self.session.last
        if result is None or self.component_select.value is None:
            return
        item = next((i for i in result['items'] if i['refdes'] == self.component_select.value))
        panels = []
        for label, image in [('REFERENCE', result['reference_image']), ('INSPECTION', result['aligned'])]:
            x0, y0, x1, y1 = item['box']
            patch = image[max(0, y0 - 8):y1 + 8, max(0, x0 - 8):x1 + 8]
            panel = np.full((250, 300, 3), 245, np.uint8)
            scale = min(280 / patch.shape[1], 210 / patch.shape[0])
            patch = cv2.resize(patch, None, fx=scale, fy=scale)
            panel[35:35 + patch.shape[0], 10:10 + patch.shape[1]] = patch
            cv2.putText(panel, label + ' ' + item['refdes'], (10, 23), 0, 0.6, (30, 30, 30), 1)
            panels.append(panel)
        self.component_image.value = jpeg(np.hstack(panels), 640)
        self.component_image.layout.display = ''
        self.component_note.value = '<p>左：正常参考；右：自动对齐后的待测元件。' + html.escape(item['reason']) + '</p>'

    def on_save(self, button=None):
        if self.busy or self.session.fault:
            return
        self.set_busy(True)
        try:
            self.saved.value = '<p>结果已保存：' + html.escape(str(self.session.save())) + '</p>'
        except Exception as exc:
            self.error(exc)
        finally:
            self.set_busy(False)

    def close(self):
        if self.busy or self.session.lock.locked():
            raise RuntimeError('请等待当前操作完成')
        for b, fn in [(self.register_button, self.on_register), (self.load_button, self.on_load), (self.inspect_button, self.on_inspect), (self.save_button, self.on_save), (self.custom_button, self.on_custom)]:
            b.on_click(fn, remove=True)
        self.widget.close()

def launch(previous=None, existing_driver=None):
    from IPython.display import display
    if previous is not None:
        if previous.session.fault:
            raise RuntimeError('之前的硬件会话异常，请保留当前内核')
        previous.close()
        session = previous.session
    else:
        root = Path(os.environ.get('PCB_PROJECT_ROOT', '/home/xilinx/jupyter_notebooks'))
        session = ComponentSession(root, driver=existing_driver)
    app = ComponentUI(session)
    display(app.widget)
    return app
if __name__ == '__main__':
    component_app = launch(globals().get('component_app'), globals().get('driver'))
