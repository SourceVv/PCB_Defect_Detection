import json
from pathlib import Path
import cv2
import numpy as np
from .legacy import ReferenceEngine, ImageRejected, decode, sha, patch_for, orientation_feature
from .inventory import read_labels, associate, body_mask, kind_of

def geometry(patch, kind, binary=None):
    mask = body_mask(patch, kind, binary)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return dict(occupancy=0.0, aspect=1.0, angle=0.0, fill=0.0)
    contour = max(contours, key=cv2.contourArea)
    rectangle = cv2.minAreaRect(contour)
    w, h = rectangle[1]
    angle = rectangle[2] + (90 if w < h else 0)
    area = cv2.contourArea(contour)
    return dict(occupancy=float(area / mask.size), aspect=float(max(w, h) / max(1, min(w, h))), angle=float(angle % 180), fill=float(area / max(1, w * h)))

class GeneralSession:

    def __init__(self, root, backend=None):
        self.root = Path(root)
        self.engine = None
        self.rows = []
        self.last = None
        self.reference_data = None
        self.backend = backend
        self.calibrated = None

    def setup_calibrated(self, name, data, strict=False):
        from .legacy import ComponentVision
        try:
            vision = ComponentVision()
            vision.enroll(name, data)
            self.calibrated = vision
        except ImageRejected:
            self.calibrated = None
            if strict:
                raise

    def known_inventory(self, engine, data):
        assets = Path(__file__).parent / 'assets'
        source = assets / 'full_reference.png'
        if not source.exists():
            return None
        known = ReferenceEngine()
        known.enroll('inventory_reference', source.read_bytes())
        try:
            _, _, _, alignment = known.align(data)
        except ImageRejected:
            return None
        matrix = np.array(engine.metadata['crop']['original_to_canonical']) @ np.linalg.inv(np.array(alignment['original_target_to_reference'])) @ np.array(known.metadata['crop']['original_to_canonical'])
        rows = json.loads((assets / 'full_inventory.json').read_text(encoding='utf-8'))['rows']
        height, width = engine.image.shape[:2]
        for row in rows:
            x0, y0, x1, y1 = row['box']
            points = cv2.perspectiveTransform(np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]), matrix)[0]
            lower = np.maximum(0, np.floor(points.min(axis=0))).astype(int)
            upper = np.minimum([width, height], np.ceil(points.max(axis=0))).astype(int)
            row['box'] = [int(lower[0]), int(lower[1]), int(upper[0]), int(upper[1])]
            row['kind'] = kind_of(row['refdes'])
        return rows

    def register(self, name, data, labels=None, ocr=None):
        engine = ReferenceEngine()
        engine.enroll(name, data)
        known_rows = self.known_inventory(engine, data) if labels is None and ocr is None else None
        if known_rows is not None:
            self.setup_calibrated(name, data, strict=True)
            self.engine, self.rows, self.reference_data = (engine, known_rows, bytes(data))
            self.last = None
            return self.rows
        raw = decode(data)
        if labels is None:
            config_path = self.root / 'pcb_ocr_connection.json'
            if not config_path.is_file():
                config_path = Path(__file__).parent / 'assets/ocr_connection.json'
            if ocr is None and config_path.is_file():
                from urllib.request import Request, urlopen
                config = json.loads(config_path.read_text(encoding='utf-8'))
                request = Request(config['url'].rstrip('/') + '/labels', data=data, headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/octet-stream'})
                try:
                    with urlopen(request, timeout=180) as response:
                        labels = json.loads(response.read())['labels']
                except Exception as exc:
                    raise ImageRejected('参考位号识别服务未连接。请启动电脑端 OCR 服务，确认电脑与板子连接。') from exc
            else:
                labels = read_labels(raw, ocr)
        matrix = np.array(engine.metadata['crop']['original_to_canonical'])
        mapped = []
        for label in labels:
            points = cv2.perspectiveTransform(np.float32([label['polygon']]), matrix)[0]
            mapped.append(dict(label, polygon=points.tolist(), center=points.mean(axis=0).tolist()))
        rows = associate(engine.image, mapped)
        if not rows:
            raise ImageRejected('未识别到可靠位号。请使用文字更清晰的参考照片。')
        self.engine, self.rows, self.reference_data = (engine, rows, bytes(data))
        self.calibrated = None
        self.last = None
        return rows

    def update(self, index, name, box, populated, enabled=True):
        from .inventory import PATTERN, kind_of
        name = name.strip().upper()
        if not PATTERN.fullmatch(name):
            raise ImageRejected('位号格式应为 U1、C14、LED5 等')
        if any((i != index and row['refdes'] == name for i, row in enumerate(self.rows))):
            raise ImageRejected('存在相同位号，请先修正重复识别')
        h, w = self.engine.image.shape[:2]
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h and (min(x1 - x0, y1 - y0) >= 5)):
            raise ImageRejected('元件框必须在参考图内，宽高至少5像素')
        row = dict(refdes=name, kind=kind_of(name), box=list(box), populated=bool(populated), reviewed=True, enabled=bool(enabled), association='reviewed', confidence=1.0)
        if index == len(self.rows):
            self.rows.append(row)
        else:
            self.rows[index] = row
        self.last = None

    def inspect(self, data):
        if self.engine is None:
            raise ImageRejected('请先登记参考板')
        original, aligned, valid, alignment = self.engine.align(data)
        results = []
        inverse = np.linalg.inv(np.array(alignment['original_target_to_reference']))
        masks, operator = self.backend.process(aligned, self.rows) if self.backend is not None else ({}, None)
        for index, row in enumerate(self.rows):
            item = dict(row, presence='待确认', tilt='不适用', orientation='不适用', reason='参考位号和封装关系尚未确认')
            box = row['box']
            if not row['enabled']:
                item.update(presence='未启用', reason='该位置不参与检测')
            elif not row['reviewed'] or box is None:
                pass
            elif not row['populated']:
                item.update(presence='参考为空焊盘', reason='参考未装件，此位置不报缺件')
            else:
                x0, y0, x1, y1 = box
                polygon = cv2.perspectiveTransform(np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]), inverse)[0]
                short = min((np.linalg.norm(polygon[i] - polygon[(i + 1) % 4]) for i in range(4)))
                reference = patch_for(self.engine.image, box)
                query = patch_for(aligned, box)
                ref, target = (geometry(reference, row['kind']), geometry(query, row['kind'], masks.get(index)))
                ratio = target['occupancy'] / max(0.02, ref['occupancy'])
                item.update(reference_geometry=ref, target_geometry=target, occupancy_ratio=float(ratio), polygon=polygon.tolist())
                if short < 12 or np.mean(valid[y0:y1, x0:x1] > 0) < 0.98 or ref['occupancy'] < 0.15:
                    item['reason'] = '原图细节、区域覆盖或参考封装分割不足'
                elif ratio < 0.25:
                    item.update(presence='疑似缺件', reason='对应区域的封装主体明显不足')
                elif ratio > 0.65 and target['fill'] > 0.45:
                    item.update(presence='存在', reason='检测到对应封装主体')
                else:
                    item['reason'] = '封装主体证据不足，需要复核'
                if item['presence'] == '存在' and row['kind'] == 'C':
                    delta = abs(target['angle'] - ref['angle']) % 180
                    delta = min(delta, 180 - delta)
                    item['angle_delta'] = float(delta)
                    item['tilt'] = '待确认' if min(ref['aspect'], target['aspect']) < 1.3 else '疑似歪斜' if delta > 12 else '未见歪斜'
                if item['presence'] == '存在' and row['kind'] in ('U', 'IC', 'SOCKET'):
                    a = orientation_feature(reference)
                    b = orientation_feature(cv2.rotate(reference, cv2.ROTATE_180))
                    q = orientation_feature(query)
                    direct, reverse = (float(np.mean((q - a) ** 2)), float(np.mean((q - b) ** 2)))
                    asymmetry = float(np.mean((a - b) ** 2))
                    margin = abs(direct - reverse) / max(direct, reverse, 1e-08)
                    item['orientation'] = '方向待确认'
                    if asymmetry > 0.0003 and margin > 0.25:
                        item['orientation'] = '疑似焊反' if reverse < direct else '方向外观一致'
                    item['orientation_evidence'] = dict(direct=direct, reverse=reverse, asymmetry=asymmetry, margin=margin)
            results.append(item)
        if self.calibrated is not None:
            from .legacy import task_cn
            _, cal_aligned, cal_valid, cal_alignment = self.calibrated.align(data)
            mosaic, entries = self.calibrated.prepare_tiles(cal_aligned)
            if self.backend is None:
                binary = np.where(mosaic > 100, 255, 0).astype(np.uint8)
            else:
                calibrated_operator = self.backend.threshold(mosaic, 100)
                binary = calibrated_operator['mask']
                if operator is not None:
                    operator['calibrated_mask'] = binary
                    operator['calibrated_frame_id'] = calibrated_operator['frame_id']
            known = self.calibrated.classify_components(cal_aligned, cal_valid, cal_alignment, entries, binary)
            known = {item['refdes']: item for item in known}
            for item in results:
                source = known.get(item['refdes'])
                if source is None or not item['reviewed'] or (not item['populated']) or (not item['enabled']):
                    continue
                for key in ('presence', 'orientation', 'tilt'):
                    state = source[key]
                    if state not in ('not_applicable', 'not_calibrated'):
                        if key == 'presence' and item['kind'] == 'U' and (item['presence'] == '存在'):
                            continue
                        item[key] = task_cn(key, state)
                item['calibrated_evidence'] = source['reason']
        self.last = dict(items=results, aligned=aligned, original=original, alignment=alignment, backend='FPGA threshold with ARM geometry' if self.backend is not None else 'CPU reference geometry', input_sha256=sha(data), full_inventory_verified=False, operator=operator, coverage=dict(total=len(results), evaluated=sum(('reference_geometry' in i for i in results)), reference_empty=sum((i['presence'] == '参考为空焊盘' for i in results)), disabled=sum((i['presence'] == '未启用' for i in results)), pending=sum((i['presence'] == '待确认' for i in results))))
        return self.last

    def save_profile(self):
        if self.engine is None:
            raise ImageRejected('请先登记参考')
        directory = self.root / 'reference_profiles' / sha(self.reference_data)[:16]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'reference.bin').write_bytes(self.reference_data)
        record = dict(rows=self.rows, metadata=self.engine.metadata, schema=1)
        (directory / 'inventory.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return directory

    def load_profile(self, directory):
        directory = Path(directory)
        record = json.loads((directory / 'inventory.json').read_text(encoding='utf-8'))
        data = (directory / 'reference.bin').read_bytes()
        if sha(data) != record['metadata']['source_sha256']:
            raise ImageRejected('参考图校验失败')
        engine = ReferenceEngine()
        engine.enroll(record['metadata']['name'], data)
        self.engine, self.reference_data, self.rows = (engine, data, record['rows'])
        self.setup_calibrated(record['metadata']['name'], data)
        self.last = None
