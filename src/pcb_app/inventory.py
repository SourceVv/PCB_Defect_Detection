import re
import cv2
import numpy as np
PATTERN = re.compile('(?:LED|MIC|SW|TP|CN|JP|IC|SOCKET|ANT|IPEX|[RCLUXDQJF])\\d+[A-Z]?')

def read_labels(image, engine=None):
    if engine is None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError('自动位号识别需要 RapidOCR。请按 README 安装识别环境，或导入已保存的参考清单。') from exc
        engine = RapidOCR()
    height, width = image.shape[:2]
    records = []
    for turn in range(4):
        result = engine(np.ascontiguousarray(np.rot90(image, turn)))
        if result.txts is None:
            continue
        for text, confidence, polygon in zip(result.txts, result.scores, result.boxes):
            name = re.sub('\\s+', '', text).upper()
            if not PATTERN.fullmatch(name) or confidence < 0.7:
                continue
            points = np.asarray(polygon, dtype=float)
            x, y = (points[:, 0].copy(), points[:, 1].copy())
            if turn == 1:
                points[:, 0], points[:, 1] = (width - 1 - y, x)
            elif turn == 2:
                points[:, 0], points[:, 1] = (width - 1 - x, height - 1 - y)
            elif turn == 3:
                points[:, 0], points[:, 1] = (y, height - 1 - x)
            center = points.mean(axis=0)
            old = next((r for r in records if r['name'] == name and np.linalg.norm(np.array(r['center']) - center) < 25), None)
            if old:
                old['votes'] += 1
                old['confidence'] = max(old['confidence'], float(confidence))
            else:
                records.append(dict(name=name, center=center.tolist(), polygon=points.tolist(), confidence=float(confidence), votes=1))
    return records

def body_mask(image, kind, binary=None):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if kind in ('U', 'IC', 'SOCKET'):
        dark = gray < 115 if binary is None else binary == 0
        mask = (dark & (hsv[:, :, 1] < 130)).astype(np.uint8) * 255
    else:
        substrate = (hsv[:, :, 0] >= 32) & (hsv[:, :, 0] <= 100) & (hsv[:, :, 1] > 65)
        dark = gray < 200 if binary is None else binary == 0
        mask = (~substrate & dark).astype(np.uint8) * 255
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

def kind_of(name):
    return re.match('[A-Z]+', name).group()

def associate(image, labels):
    height, width = image.shape[:2]
    rows = []
    for label in labels:
        name, center = (label['name'], np.array(label['center']))
        kind = kind_of(name)
        mask = body_mask(image, kind)
        for other in labels:
            cv2.fillPoly(mask, [np.rint(other['polygon']).astype(np.int32)], 0)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        options = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            if min(w, h) < 7 or area < 40 or area > 0.035 * width * height or (max(w, h) / min(w, h) > 8):
                continue
            distance = np.linalg.norm(np.array([x + w / 2, y + h / 2]) - center)
            reach = max(55, min(160, max(w, h) * 1.3))
            if distance > reach:
                continue
            fill = area / (w * h)
            if fill < 0.35:
                continue
            options.append((distance / max(12, np.sqrt(area)), [x, y, x + w, y + h], fill))
        options.sort(key=lambda item: item[0])
        box = options[0][1] if options else None
        ambiguous = len(options) > 1 and options[1][0] < options[0][0] * 1.25
        rows.append(dict(refdes=name, kind=kind, label_polygon=label['polygon'], box=box, populated=None, reviewed=False, confidence=label['confidence'], association='ambiguous' if ambiguous else 'candidate' if box else 'unresolved', enabled=kind not in ('TP', 'ANT')))
    for row in rows:
        if sum((r['refdes'] == row['refdes'] for r in rows)) > 1:
            row['association'] = 'duplicate_label'
    return rows
