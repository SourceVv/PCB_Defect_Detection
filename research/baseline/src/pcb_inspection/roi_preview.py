import math
import cv2
import numpy as np
from .core import map_box, prepare_frame

def preview_rois(image, config):
    _, meta = prepare_frame(image, config)
    if not meta['image_ok']:
        raise ValueError(f"Cannot preview ROIs: {meta['reason']}")
    canvas = image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    width, height = config['input_size']
    sx, sy = (image.shape[1] / width, image.shape[0] / height)
    columns, cell_w, cell_h = (4, 320, 280)
    sheet = np.full((math.ceil(len(config['rois']) / columns) * cell_h, columns * cell_w, 3), 245, np.uint8)
    items = []
    for index, (name, rc) in enumerate(config['rois'].items()):
        box = list(map_box(meta['H'], rc['box']))
        a, b, c, d = original = [round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)]
        active = rc.get('active', True)
        inside = 0 <= a < c <= image.shape[1] and 0 <= b < d <= image.shape[0]
        color = (0, 180, 240) if active else (140, 140, 140)
        if inside:
            cv2.rectangle(canvas, (a, b), (c - 1, d - 1), color, 2)
            cv2.putText(canvas, name, (a, max(20, b - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        row, col = divmod(index, columns)
        ox, oy = (col * cell_w, row * cell_h)
        title = f"{name}  {rc['type']}" + (' [inactive]' if not active else '')
        cv2.putText(sheet, title, (ox + 10, oy + 23), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (25, 25, 25), 1)
        cv2.putText(sheet, str(original), (ox + 10, oy + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (25, 25, 25), 1)
        if inside:
            crop = image[b:d, a:c]
            if crop.ndim == 2:
                crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            scale = min(300 / crop.shape[1], 205 / crop.shape[0])
            crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))), interpolation=cv2.INTER_NEAREST)
            x, y = (ox + (cell_w - crop.shape[1]) // 2, oy + 58)
            sheet[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
        else:
            cv2.putText(sheet, 'OUTSIDE IMAGE', (ox + 10, oy + 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 220), 2)
        items.append(dict(refdes=name, active=active, box=box, original_box=original, inside_image=inside))
    metadata = dict(config_version=config['version'], geometry_only=True, image_ok=True, inliers=meta['inliers'], note='Boxes only. Colors do not represent detection status.', items=items)
    return (metadata, canvas, sheet)
