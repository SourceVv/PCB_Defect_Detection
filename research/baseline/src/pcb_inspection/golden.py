import hashlib
import json
import os
from pathlib import Path
import numpy as np
from .core import _gray, detect, load_config, reference_path
from .io import read_image

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def public_config(config):
    return {k: v for k, v in config.items() if not k.startswith('_')}

def threshold_mask(gray, config):
    mask = np.zeros_like(gray)
    for roi in config['rois'].values():
        if roi.get('active', True) and roi['type'] == 'missing':
            x0, y0, x1, y1 = roi['box']
            region = gray[y0:y1, x0:x1]
            mask[y0:y1, x0:x1] = np.where(255 - region > 120, 255, 0).astype(np.uint8)
    return mask

def generate(config_path, output_dir):
    config_path, output_dir = (Path(config_path).resolve(), Path(output_dir).resolve())
    config = load_config(config_path)
    image = read_image(reference_path(config))
    gray = _gray(image)
    mask = threshold_mask(gray, config)
    result, _ = detect(image, config)
    if not result['image_ok']:
        raise ValueError('Reference image could not be aligned')
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, arr in [('input_gray', gray), ('expected_mask', mask)]:
        np.save(output_dir / f'{name}.npy', arr)
        arr.tofile(output_dir / f'{name}.bin')
    copy = public_config(config)
    copy['alignment'] = dict(copy['alignment'])
    copy['alignment']['reference_image'] = Path(os.path.relpath(reference_path(config), output_dir)).as_posix()
    (output_dir / 'config.json').write_text(json.dumps(copy, ensure_ascii=False, indent=2), encoding='utf-8')
    stats = dict(config_version=config['version'], feature_tolerance=0.0001, source_config_sha256=digest(config_path), source_reference_sha256=digest(reference_path(config)), shape=list(gray.shape), dtype='uint8', byte_order='row-major', mask_contract='enabled missing ROI union; (255-gray)>120 =>255, else0', backend='software', items=result['items'])
    (output_dir / 'expected_stats.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    return stats

def compare_mask(expected, actual):
    if expected.shape != actual.shape or actual.dtype != np.uint8:
        return dict(match=False, shape_expected=list(expected.shape), shape_actual=list(actual.shape), reason='shape_or_dtype_mismatch')
    positions = np.argwhere(expected != actual)
    return dict(match=len(positions) == 0, mismatch_pixels=len(positions), first_yx=positions[0].tolist() if len(positions) else None)

def verify(folder, config_path, actual_mask=None, actual_stats=None):
    folder = Path(folder)
    config = load_config(config_path)
    expected = json.loads((folder / 'expected_stats.json').read_text(encoding='utf-8'))
    errors = []
    if digest(config_path) != expected['source_config_sha256']:
        errors.append('source_config_changed_regenerate_golden')
    if digest(reference_path(config)) != expected['source_reference_sha256']:
        errors.append('source_reference_changed')
    image = read_image(reference_path(config))
    result, _ = detect(image, config)
    expected_by = {it['refdes']: it for it in expected['items']}
    current_by = {it['refdes']: it for it in result['items']}
    if set(expected_by) != set(current_by):
        errors.append('roi_set_mismatch')
    for name, old in expected_by.items():
        now = current_by.get(name, {})
        if now.get('status') != old['status']:
            errors.append(f'status_mismatch:{name}')
        if old['feature_value'] is not None:
            value = now.get('feature_value')
            if value is None or not np.isfinite(value) or abs(value - old['feature_value']) > expected['feature_tolerance']:
                errors.append(f'feature_mismatch:{name}')
        if now.get('area') != old['area']:
            errors.append(f'area_mismatch:{name}')
    for name in ('input_gray', 'expected_mask'):
        arr = np.load(folder / f'{name}.npy', allow_pickle=False)
        raw = np.fromfile(folder / f'{name}.bin', dtype=np.uint8)
        if arr.dtype != np.uint8 or list(arr.shape) != expected['shape'] or (not np.array_equal(arr.ravel(), raw)):
            errors.append(f'binary_array_mismatch:{name}')
    saved_input = np.load(folder / 'input_gray.npy', allow_pickle=False)
    if not np.array_equal(saved_input, _gray(image)):
        errors.append('input_pixels_changed')
    saved_mask = np.load(folder / 'expected_mask.npy', allow_pickle=False)
    software_mask = compare_mask(saved_mask, threshold_mask(_gray(image), config))
    if not software_mask['match']:
        errors.append('software_mask_mismatch')
    hardware_mask = None
    if actual_mask:
        raw = np.fromfile(actual_mask, dtype=np.uint8)
        if raw.size != saved_mask.size:
            hardware_mask = dict(match=False, reason='byte_length_mismatch', actual_bytes=int(raw.size))
        else:
            hardware_mask = compare_mask(saved_mask, raw.reshape(saved_mask.shape))
        if not hardware_mask['match']:
            errors.append('hardware_mask_mismatch')
    hardware_stats = None
    if actual_stats:
        values = json.loads(Path(actual_stats).read_text(encoding='utf-8'))
        hardware_stats = []
        for name, old in expected_by.items():
            if old['task'] != 'missing':
                continue
            now = values.get(name)
            if not isinstance(now, dict) or not {'status', 'feature_value', 'area'} <= now.keys():
                hardware_stats.append(f'missing_fields:{name}')
                continue
            if now['status'] != old['status'] or now['area'] != old['area']:
                hardware_stats.append(f'status_or_area:{name}')
            v = now['feature_value']
            if not isinstance(v, (int, float)) or not np.isfinite(v) or abs(v - old['feature_value']) > expected['feature_tolerance']:
                hardware_stats.append(f'feature:{name}')
        if hardware_stats:
            errors.append('hardware_stats_mismatch')
    return dict(passed=not errors, errors=errors, software_mask=software_mask, hardware_mask=hardware_mask, hardware_stats_errors=hardware_stats, hardware_validated=bool(actual_mask or actual_stats), note='Without --actual-mask/--actual-stats this checks software fixtures only.')
