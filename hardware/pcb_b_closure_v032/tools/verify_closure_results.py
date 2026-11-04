from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
WIDTH, HEIGHT, SEED = (2533, 1900, 20261101)
TIMES = ('input_snapshot_seconds', 'transfer_seconds_single_call', 'stats_read_seconds_single_call', 'transfer_and_stats_seconds_single_call', 'result_validation_and_copy_seconds', 'process_seconds_single_call')
STAT_KEYS = ('roi_counts', 'roi_counts_by_index', 'valid_pixels', 'output_white_pixels')
CANDIDATES = ('compare', 'sum', 'addwrap', 'addreduce', 'bytes_count')

def _require(value, message):
    if not value:
        raise ValueError(message)

def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, 'Duplicate JSON object key: ' + key)
        result[key] = value
    return result

def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_pairs)

def _digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()

def _contained(root, name):
    _require(isinstance(name, str) and bool(name) and (not Path(name).is_absolute()) and ('..' not in Path(name).parts), 'Unsafe relative evidence path')
    path = (root / name).resolve()
    _require(path.is_relative_to(root.resolve()), 'Evidence path escapes run directory')
    return path

def _positive(value, label):
    _require(type(value) in (int, float) and math.isfinite(value) and (value > 0), 'Invalid timing: ' + label)
    return float(value)

def _summary(values):
    values = [_positive(value, 'sample') for value in values]
    _require(bool(values), 'Empty timing series')
    ordered = sorted(values)
    position = (len(values) - 1) * 0.95
    lo, hi = (math.floor(position), math.ceil(position))
    return {'n': len(values), 'unit': 'seconds', 'min': min(values), 'max': max(values), 'mean': statistics.mean(values), 'median': statistics.median(values), 'p95_linear': ordered[lo] + (position - lo) * (ordered[hi] - ordered[lo]), 'std_population': statistics.pstdev(values)}

def _equal(actual, expected, label):
    if isinstance(expected, dict):
        _require(isinstance(actual, dict) and set(actual) == set(expected), 'Object schema differs: ' + label)
        for key, value in expected.items():
            _equal(actual[key], value, label + '.' + key)
    elif type(expected) in (int, float):
        _require(type(actual) in (int, float) and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12), 'Number differs: ' + label)
    else:
        _require(actual == expected, 'Value differs: ' + label)

def _manifest(root, name):
    root = Path(root).resolve()
    path = root / name
    value = _json(path)
    seen = set()
    for item in value['files']:
        _require(item['path'] not in seen, 'Duplicate manifest path')
        seen.add(item['path'])
        source = _contained(root, item['path'])
        _require(source.is_file() and source.stat().st_size == item['bytes'] and (_digest(source) == item['sha256']), 'Local frozen manifest mismatch: ' + str(source))
    return {'sha256': _digest(path), 'files_verified': len(seen), 'name': name}

def _check_provenance(report, package, closure_root, previous, mode):
    package, closure_root = (Path(package).resolve(), Path(closure_root).resolve())
    roots = [('closure', closure_root, 'new_package_manifest.json'), ('frozen', package, 'package_manifest.json')]
    if mode != 'profile':
        _require(previous is not None, 'Hardware evidence requires explicit --previous root')
        previous = Path(previous).resolve()
        roots.append(('previous', previous, 'new_package_manifest.json'))
    manifests = {label: _manifest(root, name) for label, root, name in roots}
    if mode == 'profile':
        source = closure_root / 'software/aoi_roi_service.py'
        _require(report['service_source_sha256'] == _digest(source), 'Profile service source SHA differs')
        text = source.read_text(encoding='utf-8')
        lines = text.splitlines(keepends=True)
        functions = {node.name: node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
        helpers = {name: 'validate_mask_' + name for name in CANDIDATES}
        expected = {name: hashlib.sha256(''.join(lines[functions[function].lineno - 1:functions[function].end_lineno]).encode()).hexdigest() for name, function in helpers.items()}
        _require(report['helper_source_sha256'] == expected, 'Profile helper source SHA differs')
    else:
        reported_manifests = report['manifests']
        _require(len(reported_manifests) == len(roots), 'Wrong source manifest count')
        for entry, (label, root, name) in zip(reported_manifests, roots):
            normalized = str(entry['path']).replace('\\', '/')
            _require(normalized.endswith('/' + root.name + '/' + name) and entry['sha256'] == manifests[label]['sha256'] and (entry['files_verified'] == manifests[label]['files_verified']), 'Recorded/local manifest binding differs')
        sources = [(closure_root, 'software/aoi_roi_service.py'), (closure_root, 'tools/board_closure_worker.py'), (closure_root, 'tools/profile_masks.py'), (previous, 'software/aoi_roi_service.py'), (package, 'software/roi_driver.py'), (package, 'software/transport.py'), (package, 'release/roi_v0_3/pcb_roi.bit'), (package, 'release/roi_v0_3/pcb_roi.hwh')]
        source_hashes = report['source_hashes']
        _require(len(source_hashes) == len(sources), 'Wrong source-hash count')
        for root, rel in sources:
            matches = [value for path, value in source_hashes.items() if str(path).replace('\\', '/').endswith('/' + root.name + '/' + rel)]
            _require(matches == [_digest(root / rel)], 'Recorded/local source SHA differs: ' + rel)
    return manifests

def _load_golden(package):
    import numpy as np
    package = Path(package)
    profile = _json(package / 'config/roi_profiles.json')['latest17']
    rois = profile['rois']
    gray = np.load(package / 'fixtures/input_gray.npy', allow_pickle=False)
    expected = np.load(package / 'fixtures/mask_latest17.npy', allow_pickle=False)
    _require(gray.dtype == expected.dtype == np.uint8 and gray.shape == expected.shape == (HEIGHT, WIDTH) and (len(rois) == 17) and all((roi['enabled'] is True for roi in rois)), 'Invalid frozen latest17 input')
    union = np.zeros(gray.shape, dtype=bool)
    foreground = gray < 135
    counts = []
    for roi in rois:
        x0, y0, x1, y1 = roi['box']
        _require(0 <= x0 < x1 <= WIDTH and 0 <= y0 < y1 <= HEIGHT, 'Frozen ROI outside image')
        union[y0:y1, x0:x1] = True
        counts.append(int(np.count_nonzero(foreground[y0:y1, x0:x1])))
    recomputed = np.where(union & foreground, np.uint8(255), np.uint8(0))
    _require(np.array_equal(expected, recomputed), 'Frozen mask differs from independent gray<135 ROI union')
    raw = expected.tobytes(order='C')
    stats = {'roi_counts': {roi['name']: counts[i] for i, roi in enumerate(rois)}, 'roi_counts_by_index': counts, 'valid_pixels': int(gray.size), 'output_white_pixels': int(np.count_nonzero(expected)), 'roi_union_pixels': int(np.count_nonzero(union))}
    fixed = next((item for item in _json(package / 'fixtures/cases_expected.json') if item['name'] == 'roi_latest17_full'))
    input_hash, expected_hash = (hashlib.sha256(gray.tobytes()).hexdigest(), hashlib.sha256(raw).hexdigest())
    _require(fixed['input_raw_sha256'] == input_hash and fixed['output_raw_sha256'] == expected_hash and (fixed['expected_stats'] == stats) and (fixed['rois'] == rois), 'Frozen case signature differs')
    return {'configuration': {'width': WIDTH, 'height': HEIGHT, 'threshold': 120, 'invert': True, 'roi_enable': True, 'rois': rois}, 'input_hash': input_hash, 'expected_hash': expected_hash, 'stats': stats, 'mask_bytes': len(raw), 'mask_raw': raw}

def _status(status, count):
    _require(isinstance(status, dict) and status.get('abi') == 3 and (status.get('completed_packets') == count & 65535) and all((status.get(key) is False for key in ('busy', 'inpacket', 'cfg_error', 'frame_error'))), 'Algorithm status/packet count mismatch')
    raw = status.get('raw')
    _require(type(raw) is int and 0 <= raw <= 4294967295 and (raw >> 16 == count & 65535) and (raw >> 8 & 255 == 3) and (raw & 251 == 0) and (status.get('cfg_ack') is bool(raw & 4)), 'Raw/decoded status differs')

def _safety(value, transfer=None):
    _require(isinstance(value, dict) and value.get('dma_safe') is True and (value.get('active') is False) and (value.get('poisoned') is False) and (value.get('buffers') == value.get('retained_buffers') == 0), 'Unsafe driver snapshot or retained buffers')
    if transfer is not None:
        _require(value.get('last_transfer') == transfer, 'Safety snapshot identifies a different transfer')

def _output(record, ctx):
    _require(record.get('output_raw_sha256') == ctx['expected_hash'] and record.get('all_bytes_and_statistics_match') is True, 'Mask hash/full-comparison evidence differs')
    for key in STAT_KEYS:
        _require(record.get(key) == ctx['stats'][key], 'Frozen statistic differs: ' + key)
    _positive(record['outer_wall_seconds'], 'outer wall')

def _hardware(record, ctx, before, candidate, new):
    _output(record, ctx)
    transfer = record['driver_transfer']
    after = before + 1
    expected = {'stage': 'complete', 'packet_delta': 1, 'packets_before': before & 65535, 'packets_after': after & 65535, 'nbytes': ctx['mask_bytes'], 'received_bytes': ctx['mask_bytes'], 'threshold': 120, 'bypass': False, 'invert': True, 'roi_enable': True}
    _require(all((transfer.get(key) == value for key, value in expected.items())), 'DMA packet sequence/length/mode/completion differs')
    for key in ('tx_status', 'rx_status'):
        value = transfer.get(key)
        _require(type(value) is int and value & 4098 == 4098 and (not value & 112), 'DMA IOC/Idle/error evidence differs')
    for key in ('reset_tx_status', 'reset_rx_status'):
        value = transfer.get(key)
        _require(type(value) is int and value & 1 and (not value & 112), 'DMA reset dual-Halted/error evidence differs')
    _status(record['status'], after)
    _require(transfer.get('algorithm_status') == record['status']['raw'], 'Transfer/status frame identity differs')
    _safety(record['driver_safety'], transfer)
    _require(all((record.get(key) is True for key in ('mask_owns_data', 'mask_base_is_none', 'mask_c_contiguous'))), 'Output ownership/contiguity evidence differs')
    if new:
        _require(record.get('implementation') == 'pcb-b-closure/0.3.2' and record.get('mask_validation') == candidate and (record.get('output_ownership') == 'frozen_driver_owning_base'), 'New service did not use selected strict validator and proven owning base')
    for key in TIMES:
        _positive(record['timing'][key], key)
    _positive(transfer['elapsed_seconds'], 'driver internal')
    return after

def _records(path):
    with Path(path).open(encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            _require(line.endswith('\n') and bool(line.strip()), 'Truncated/empty JSONL line ' + str(index))
            yield json.loads(line, object_pairs_hook=_pairs)

def _sample(run, sample, ctx, **identity):
    _require(all((sample.get(key) == value for key, value in identity.items())), 'Sample frame/index identity differs')
    path = _contained(run, sample['file'])
    _require(sample.get('bytes') == ctx['mask_bytes'] and path.stat().st_size == ctx['mask_bytes'] and (sample.get('sha256') == ctx['expected_hash']) and (_digest(path) == ctx['expected_hash']) and (path.read_bytes() == ctx['mask_raw']), 'Saved sample mask differs from frozen pixels')
    return str(path)

def _configuration(entry, ctx):
    expected = dict(ctx['configuration'])
    expected['rois'] = [{key: roi[key] for key in ('name', 'box', 'enabled')} for roi in expected['rois']]
    _require(entry.get('state') == 'configured' and entry.get('configuration') == expected, 'Once-only service configuration differs')
    _status(entry['status'], 0)
    _positive(entry['configuration_seconds'], 'configuration')

def _validate(report, run, ctx):
    _require(report.get('warmup_count') == 3 and report.get('measurement_count') == 30 and (report.get('seed') == SEED) and (report.get('completed_rounds') == 33) and (report.get('expected_hardware_frames') == report.get('validated_hardware_frames') == 66), 'Validate target is not exactly 33 rounds / 66 DMA calls')
    for key in ('old', 'new'):
        _configuration(report['configuration_once'][key], ctx)
    records = report['records']
    _require(isinstance(records, list) and len(records) == 33, 'Missing validate report rounds')
    rng, packets, seen, jsonl_count, last = (random.Random(SEED), 0, set(), 0, None)
    for index, row in enumerate(_records(run / 'records.jsonl')):
        _require(index < len(records) and row == records[index], 'JSONL/report round mismatch or extra round')
        order = ['cpu', 'old', 'new']
        rng.shuffle(order)
        _require(row.get('iteration') == index and row.get('phase') == ('warmup' if index < 3 else 'measured') and (row.get('order') == order) and (row.get('passed') is True) and (set(row['calls']) == set(order)), 'Round phase/order/call set differs')
        for name in order:
            call = row['calls'][name]
            frame_id = report['run_id'] + '_' + name + '_' + str(index)
            _require(call.get('frame_id') == frame_id and frame_id not in seen, 'Missing/reused call frame ID')
            seen.add(frame_id)
            if name == 'cpu':
                _output(call, ctx)
            else:
                packets = _hardware(call, ctx, packets, report['candidate'], name == 'new')
                last = call['driver_transfer']
        jsonl_count += 1
    _require(jsonl_count == 33 and packets == 66, 'Missing JSONL rounds or DMA frames')
    _status(report['final_status'], packets)
    _safety(report['final_driver_safety'], last)
    measured = records[3:]
    aggregates = {}
    for name in ('cpu', 'old', 'new'):
        calls = [row['calls'][name] for row in measured]
        metrics = {'outer_wall_seconds': _summary([call['outer_wall_seconds'] for call in calls])}
        if name != 'cpu':
            metrics.update({key: _summary([call['timing'][key] for call in calls]) for key in TIMES})
        aggregates[name] = metrics
    _equal(report['aggregate'], aggregates, 'aggregate')
    paired = {}
    for numerator in ('old', 'cpu'):
        ratios = [row['calls'][numerator]['outer_wall_seconds'] / row['calls']['new']['outer_wall_seconds'] for row in measured]
        paired[numerator + '_over_new'] = {'n': 30, 'median_of_paired_ratios': statistics.median(ratios), 'ratio_of_medians': aggregates[numerator]['outer_wall_seconds']['median'] / aggregates['new']['outer_wall_seconds']['median']}
    _equal(report['paired_ratios'], paired, 'paired ratios')
    _require(report.get('warmups_excluded') == 3 and report.get('outliers_removed') == 0, 'Unexpected sample exclusions')
    expected_samples = {label + '_' + name for label in ('first_measured', 'last_measured') for name in ('cpu', 'old', 'new')}
    _require(set(report['samples']) == expected_samples, 'Expected six validate sample BINs')
    for label, index in (('first_measured', 3), ('last_measured', 32)):
        for name in ('cpu', 'old', 'new'):
            _sample(run, report['samples'][label + '_' + name], ctx, iteration=index, implementation=name)
    return {'rounds': 33, 'dma_frames': 66, 'measured_pairs': 30, 'sample_masks_verified': 6, 'aggregate_recomputed': aggregates, 'paired_ratios_recomputed': paired}

def _memory(value):
    _require(isinstance(value, dict), 'Missing memory sample')
    for key in ('rss_kib', 'rss_peak_kib', 'cma_total_kib', 'cma_free_kib'):
        item = value.get(key)
        _require(item is None or (type(item) is int and item >= 0), 'Invalid RSS/CMA memory value')
    if value.get('rss_kib') is not None and value.get('rss_peak_kib') is not None:
        _require(value['rss_peak_kib'] >= value['rss_kib'], 'RSS peak is less than current RSS')
    if value.get('cma_total_kib') is not None and value.get('cma_free_kib') is not None:
        _require(value['cma_free_kib'] <= value['cma_total_kib'], 'Free CMA exceeds total CMA')

def _utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _require(parsed.tzinfo is not None, 'Wall clock timestamp has no timezone')
    return parsed

def _stability(report, run, ctx):
    target = _positive(report['requested_duration_seconds'], 'requested duration')
    duration = _positive(report['elapsed_monotonic_seconds'], 'completed duration')
    _require(target >= 1800 and duration >= target and (report.get('errors') == 0), 'Stability duration must reach at least 1800 seconds')
    _configuration(report['configuration_once'], ctx)
    _memory(report['initial_memory'])
    _memory(report['final_memory'])
    count, previous_elapsed, previous_utc, peak = (0, 0.0, _utc(report['loop_started_utc']), None)
    next_checkpoint, derived_checkpoints = (60.0, [])
    times, rss, rss_peaks, cma, cma_totals, seen = ([], [], [], [], [], set())
    last = None
    missing_memory = {'rss_kib': 0, 'rss_peak_kib': 0, 'cma_total_kib': 0, 'cma_free_kib': 0}
    for row in _records(run / 'records.jsonl'):
        expected_id = report['run_id'] + '_stable_' + str(count)
        _require(row.get('frame_number') == count + 1 and row.get('frame_id') == expected_id and (expected_id not in seen) and (row.get('passed') is True), 'Stability frame missing/repeated/out of order')
        seen.add(expected_id)
        _hardware(row, ctx, count, report['candidate'], True)
        count += 1
        elapsed = _positive(row['elapsed_monotonic_seconds'], 'frame monotonic')
        wall = _utc(row['board_utc'])
        _require(elapsed > previous_elapsed and elapsed <= duration and (wall >= previous_utc), 'Stability monotonic/wall-clock sequence is decreasing or exceeds final duration')
        memory = row['memory']
        _memory(memory)
        for key in missing_memory:
            missing_memory[key] += memory.get(key) is None
        if memory.get('rss_peak_kib') is not None:
            _require(peak is None or memory['rss_peak_kib'] >= peak, 'RSS high-water mark decreased')
            peak = memory['rss_peak_kib']
            rss_peaks.append(peak)
        if memory.get('rss_kib') is not None:
            rss.append(memory['rss_kib'])
        if memory.get('cma_free_kib') is not None:
            cma.append(memory['cma_free_kib'])
        if memory.get('cma_total_kib') is not None:
            cma_totals.append(memory['cma_total_kib'])
        times.append(row['outer_wall_seconds'])
        if elapsed >= next_checkpoint:
            derived_checkpoints.append({'frames': count, 'elapsed_monotonic_seconds': elapsed, 'board_utc': row['board_utc'], 'memory': memory, 'last_packet_count': row['status']['completed_packets'], 'errors': 0, 'service_seen_frame_ids': count})
            next_checkpoint = (math.floor(elapsed / 60) + 1) * 60
        previous_elapsed, previous_utc, last = (elapsed, wall, row)
    _require(count > 0 and report.get('validated_hardware_frames') == count and (report.get('service_seen_frame_ids') == count), 'Stability record count/seen-ID count differs')
    _require(duration - previous_elapsed <= max(1.0, max(times)), 'Final duration was extended beyond the recorded frame chain')
    _require(_utc(report['loop_completed_utc']) >= previous_utc, 'Completion wall clock precedes final frame')
    _require(report['checkpoints'] == derived_checkpoints, 'Missing/altered minute checkpoints')
    _require(report.get('current_memory') == last['memory'] and report.get('last_driver_safety') == last['driver_safety'], 'Final progress snapshot differs from last JSONL frame')
    _status(report['final_status'], count)
    _safety(report['final_driver_safety'], last['driver_transfer'])
    _equal(report['outer_call_seconds'], {'n': count, 'mean': sum(times) / count, 'min': min(times), 'max': max(times)}, 'stability calls')
    _require(set(report['samples']) == {'first', 'last'}, 'Expected first/last stability sample masks')
    _sample(run, report['samples']['first'], ctx, frame_number=1)
    _sample(run, report['samples']['last'], ctx, frame_number=count)

    def observed(values):
        return {'samples_available': len(values), 'first': values[0], 'last': values[-1], 'min': min(values), 'max': max(values), 'last_minus_first_kib': values[-1] - values[0]} if values else {'samples_available': 0}
    return {'dma_frames': count, 'duration_seconds': duration, 'requested_duration_seconds': target, 'last_frame_monotonic_seconds': previous_elapsed, 'minute_checkpoints_verified': len(derived_checkpoints), 'sample_masks_verified': 2, 'outer_call_seconds_recomputed': {'n': count, 'mean': sum(times) / count, 'min': min(times), 'max': max(times)}, 'memory_observations': {'rss_kib': observed(rss), 'rss_peak_kib': observed(rss_peaks), 'cma_free_kib': observed(cma), 'cma_total_kib': observed(cma_totals), 'missing_field_counts': missing_memory, 'initial': report['initial_memory'], 'final': report['final_memory'], 'service_seen_frame_ids': count, 'interpretation': 'Observed RSS/CMA values only. Frame-ID set grows by design; this audit does not infer leak freedom or whole-system stability.'}}

def _profile(report, ctx):
    _require(report.get('warmups') == 3 and report.get('measured') == 10 and (report.get('hardware_executed') is False) and (report.get('mask_bytes') == ctx['mask_bytes']) and (report.get('shape') == [HEIGHT, WIDTH]) and (report.get('input_raw_sha256') == ctx['expected_hash']) and (report.get('expected_white_pixels') == ctx['stats']['output_white_pixels']) and (report.get('input_unchanged') is True), 'Microprofile input/count/sampling scope differs')
    _require(set(report['correctness']) == set(CANDIDATES), 'Missing microprofile candidate probes')
    for value in report['correctness'].values():
        _require(value == {'all_256_values_checked': True, 'valid_values': 2, 'invalid_values_rejected': 254, 'large_first_middle_last_corruption_rejected': True}, 'Strict candidate probe record differs')
    records = report['records']
    _require(len(records) == 65, 'Microprofile needs 65 calls / 10 measured per candidate')
    rng, offset, values = (random.Random(SEED), 0, {name: [] for name in CANDIDATES})
    for index in range(13):
        order = list(CANDIDATES)
        rng.shuffle(order)
        for name in order:
            row = records[offset]
            offset += 1
            _require(row.get('iteration') == index and row.get('phase') == ('warmup' if index < 3 else 'measured') and (row.get('candidate') == name) and (row.get('order') == order) and (type(row.get('white_pixels')) is int) and (row['white_pixels'] == ctx['stats']['output_white_pixels']), 'Microprofile candidate/order/white count differs')
            seconds = _positive(row['seconds'], 'candidate')
            if index >= 3:
                values[name].append(seconds)
    aggregate = {name: _summary(series) for name, series in values.items()}
    _equal(report['summary'], aggregate, 'microprofile summary')
    best = min(aggregate, key=lambda name: aggregate[name]['median'])
    _require(report['lowest_median_candidate'] == best, 'Selected microprofile minimum differs')
    return {'candidate_count': 5, 'profile_calls': 65, 'measured_samples_per_candidate': 10, 'lowest_median_candidate': best, 'summary_recomputed': aggregate, 'hardware_executed': False}

def verify(mode, run_directory, package, closure_root, previous=None):
    _require(mode in ('profile', 'validate', 'stability'), 'Unknown audit mode')
    run = Path(run_directory).resolve()
    report_path = run / ('profile_report.json' if mode == 'profile' else 'report.json')
    report = _json(report_path)
    schema = 'pcb-b-strict-mask-microprofile/0.3.2' if mode == 'profile' else 'pcb-b-closure/0.3.2'
    _require(report.get('schema') == schema and report.get('state') == 'complete' and (report.get('passed') is True), 'Source evidence is incomplete, failed, or wrong schema')
    env = report.get('environment', {})
    _require(str(env.get('machine', '')).lower() in ('armv7l', 'armv8l') and 'linux' in str(env.get('platform', '')).lower() and (type(env.get('uid')) is int) and (env['uid'] >= 0) and (mode == 'profile' or env['uid'] == 0), 'Recorded environment is not the expected ARM Linux board execution')
    provenance = _check_provenance(report, package, closure_root, previous, mode)
    ctx = _load_golden(package)
    if mode == 'profile':
        result = _profile(report, ctx)
    else:
        _require(report.get('mode') == mode and report.get('hardware_executed') is True and (report.get('no_camera') is True) and (report.get('candidate') in CANDIDATES) and (report.get('configuration') == ctx['configuration']) and (report.get('input_raw_sha256') == ctx['input_hash']) and (report.get('expected_output_raw_sha256') == ctx['expected_hash']) and (report.get('expected_stats') == ctx['stats']), 'Hardware report/golden rule binding differs')
        _status(report['initial_status'], 0)
        result = _validate(report, run, ctx) if mode == 'validate' else _stability(report, run, ctx)
    result.update(schema='pcb-b-closure-independent-audit/0.3.2', passed=True, audit_passed=True, mode=mode, source_run_id=report['run_id'], source_state=report['state'], hardware_executed_by_this_tool=False, verified_utc=datetime.now(timezone.utc).isoformat(), source_report_sha256=_digest(report_path), provenance_verified=provenance, reported_environment=report.get('environment'), scope='Independent audit of saved B-chain evidence; no board operation, camera, C integration or general classifier claim')
    if mode != 'profile':
        result['source_jsonl_sha256'] = _digest(run / 'records.jsonl')
        result['evidence_limits'] = ['Saved sample BINs are independently compared byte-for-byte with frozen pixels. Every other frame is audited through its recorded hash/full-comparison flag/statistics/protocol/safety evidence and frozen executable source bindings; its raw output BIN was not archived separately.']
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('profile', 'validate', 'stability'))
    parser.add_argument('run_directory', type=Path)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--closure-root', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.run_directory / ('independent_' + args.mode + '_audit.json')
    protected = [args.run_directory / 'report.json', args.run_directory / 'profile_report.json', args.run_directory / 'records.jsonl', args.closure_root / 'new_package_manifest.json']
    _require(output.suffix.lower() == '.json' and output.resolve() not in {path.resolve() for path in protected} and (not output.resolve().is_relative_to(args.package.resolve())) and (not output.resolve().is_relative_to((args.closure_root / 'software').resolve())) and (not output.resolve().is_relative_to((args.closure_root / 'tools').resolve())) and (args.previous is None or not output.resolve().is_relative_to(args.previous.resolve())), 'Output must not overwrite original evidence or frozen sources')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = verify(args.mode, args.run_directory, args.package, args.closure_root, args.previous)
    except Exception as exc:
        result = {'passed': False, 'audit_passed': False, 'hardware_executed_by_this_tool': False, 'mode': args.mode, 'error': type(exc).__name__ + ': ' + str(exc)}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('Independent PC audit:', output)
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
