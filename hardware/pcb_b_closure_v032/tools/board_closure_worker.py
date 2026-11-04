from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import signal
import sys
import time
import traceback
from profile_masks import digest, environment, execute_profile, load_module, manifest_check, save_json, summary
ROOT = Path(__file__).resolve().parents[1]
HELD = {}
WARMUPS, MEASUREMENTS, SEED = (3, 30, 20261101)
STAT_KEYS = ('roi_counts', 'roi_counts_by_index', 'valid_pixels', 'output_white_pixels')

def announce(*values):
    try:
        print(*values, flush=True)
    except (OSError, ValueError):
        pass

def snapshot(driver):
    values = vars(driver) if driver is not None else {}
    return {'poisoned': bool(values.get('poisoned')), 'active': bool(values.get('_active')), 'dma_safe': bool(values.get('_dma_safe')), 'buffers': len(values.get('_buffers', [])), 'retained_buffers': len(values.get('_retained_buffers', [])), 'last_transfer': deepcopy(values.get('last_transfer', {}))}

def unsafe(value):
    return bool(value['poisoned'] or value['active'] or value['retained_buffers'] or (value['buffers'] and (not value['dma_safe'])))

def safe_idle(value):
    if unsafe(value) or not value['dma_safe'] or value['buffers']:
        raise AssertionError('Driver did not return to safe buffer-free idle')

def check_status(status, packets):
    if status.get('abi') != 3 or status.get('completed_packets') != packets & 65535 or any((status.get(key) for key in ('busy', 'inpacket', 'cfg_error', 'frame_error'))):
        raise AssertionError('Algorithm status/packet counter mismatch')

def memory_sample():
    result = {'rss_kib': None, 'rss_peak_kib': None, 'cma_total_kib': None, 'cma_free_kib': None, 'source': '/proc/self/status VmRSS/VmHWM; /proc/meminfo CmaTotal/CmaFree'}
    for filename, fields in (('/proc/self/status', {'VmRSS': 'rss_kib', 'VmHWM': 'rss_peak_kib'}), ('/proc/meminfo', {'CmaTotal': 'cma_total_kib', 'CmaFree': 'cma_free_kib'})):
        try:
            for line in Path(filename).read_text().splitlines():
                name, _, text = line.partition(':')
                if name in fields:
                    parts = text.split()
                    if len(parts) == 2 and parts[1] == 'kB':
                        result[fields[name]] = int(parts[0])
        except (OSError, ValueError):
            pass
    return result

def cpu_reference(gray, rois, union):
    import numpy as np
    foreground = gray < 135
    mask = np.where(foreground & union, np.uint8(255), np.uint8(0))
    counts = [int(np.count_nonzero(foreground[r['box'][1]:r['box'][3], r['box'][0]:r['box'][2]])) if r['enabled'] else 0 for r in rois]
    return (mask, {'roi_counts': {r['name']: counts[i] for i, r in enumerate(rois) if r['enabled']}, 'roi_counts_by_index': counts, 'valid_pixels': int(gray.size), 'output_white_pixels': int(np.count_nonzero(mask))})

def check_output(mask, stats, ctx):
    import numpy as np
    if not isinstance(mask, np.ndarray) or mask.dtype != np.uint8 or mask.shape != (1900, 2533):
        raise AssertionError('Output shape/dtype differs')
    actual_hash = hashlib.sha256(mask.tobytes(order='C')).hexdigest()
    if actual_hash != ctx['expected_hash'] or not np.array_equal(mask, ctx['expected_mask']):
        raise AssertionError('Output bytes differ from frozen latest17 mask')
    for key in STAT_KEYS:
        if stats[key] != ctx['expected_stats'][key]:
            raise AssertionError('Output statistic differs: ' + key)
    return actual_hash

def check_hardware(result, driver, ctx, before, after):
    actual_hash = check_output(result['mask'], result, ctx)
    transfer = result['driver_transfer']
    if transfer.get('stage') != 'complete' or transfer.get('packet_delta') != 1 or transfer.get('packets_before') != before & 65535 or (transfer.get('packets_after') != after & 65535) or (transfer.get('nbytes') != 4812700) or (transfer.get('received_bytes') != 4812700) or (transfer.get('threshold') != 120) or (transfer.get('bypass') is not False) or (transfer.get('invert') is not True) or (transfer.get('roi_enable') is not True) or (not (transfer.get('reset_tx_status', 0) & 1 and transfer.get('reset_rx_status', 0) & 1)):
        raise AssertionError('DMA completion/length/packet/Halted evidence differs')
    for key in ('tx_status', 'rx_status'):
        value = transfer.get(key, 0)
        if value & 4098 != 4098 or value & 112:
            raise AssertionError('DMA IOC/Idle/error bits differ')
    check_status(result['status'], after)
    state = snapshot(driver)
    safe_idle(state)
    return {'output_raw_sha256': actual_hash, **{k: deepcopy(result[k]) for k in STAT_KEYS}, 'status': result['status'], 'driver_transfer': transfer, 'driver_safety': state, 'timing': result['timing'], 'all_bytes_and_statistics_match': True, 'implementation': result.get('implementation'), 'mask_validation': result.get('mask_validation'), 'output_ownership': result.get('output_ownership'), 'mask_owns_data': bool(result['mask'].flags.owndata), 'mask_base_is_none': result['mask'].base is None, 'mask_c_contiguous': bool(result['mask'].flags.c_contiguous)}

def append_record(stream, row):
    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    stream.flush()

def configure_service(module, driver, rois, candidate=None):
    kwargs = {'width': 2533, 'height': 1900, 'rois': rois, 'threshold': 120, 'invert': True, 'roi_enable': True}
    if candidate is not None:
        kwargs['mask_validation'] = candidate
    service = module.AoiRoiService(driver, **kwargs)
    configuration = service.configure()
    return (service, configuration)

def run_validate(ctx, driver, old_module, new_module, candidate, report, save, stream, run_dir, run_id):
    old, old_config = configure_service(old_module, driver, ctx['rois'])
    HELD['old_service'] = old
    new, new_config = configure_service(new_module, driver, ctx['rois'], candidate)
    HELD['new_service'] = new
    report['configuration_once'] = {'old': old_config, 'new': new_config}
    check_status(driver.read_status(), 0)
    report['records'] = []
    report['samples'] = {}
    rng = random.Random(SEED)
    total_packets = 0
    for index in range(WARMUPS + MEASUREMENTS):
        order = ['cpu', 'old', 'new']
        rng.shuffle(order)
        phase = 'warmup' if index < WARMUPS else 'measured'
        round_record = {'iteration': index, 'phase': phase, 'order': order, 'calls': {}, 'passed': False}
        report['current_attempt'] = {'iteration': index, 'phase': phase, 'order': order}
        save()
        for name in order:
            frame_id = run_id + '_' + name + '_' + str(index)
            report['current_attempt']['implementation'] = name
            HELD['current_mask'] = None
            started = time.perf_counter_ns()
            if name == 'cpu':
                mask, stats = cpu_reference(ctx['gray'], ctx['rois'], ctx['union'])
                elapsed = (time.perf_counter_ns() - started) / 1000000000.0
                actual_hash = check_output(mask, stats, ctx)
                record = {'outer_wall_seconds': elapsed, 'output_raw_sha256': actual_hash, **stats, 'all_bytes_and_statistics_match': True, 'frame_id': frame_id}
            else:
                result = (old if name == 'old' else new).process(ctx['gray'], frame_id=frame_id)
                elapsed = (time.perf_counter_ns() - started) / 1000000000.0
                HELD['last_mask'] = result['mask']
                HELD['current_mask'] = result['mask']
                record = check_hardware(result, driver, ctx, total_packets, total_packets + 1)
                total_packets += 1
                record.update(outer_wall_seconds=elapsed, frame_id=frame_id)
                mask = result['mask']
            if index in (WARMUPS, WARMUPS + MEASUREMENTS - 1):
                label = ('first_measured_' if index == WARMUPS else 'last_measured_') + name
                path = run_dir / (label + '_mask.bin')
                mask.tofile(path)
                report['samples'][label] = {'file': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path), 'iteration': index, 'implementation': name}
            round_record['calls'][name] = record
        round_record['passed'] = True
        report['records'].append(round_record)
        append_record(stream, round_record)
        report['completed_rounds'] = index + 1
        report['validated_hardware_frames'] = total_packets
        report.pop('current_attempt', None)
        save()
        announce('CLOSURE_VALIDATE_ROUND_PASS', index, phase, total_packets)
    measured = report['records'][WARMUPS:]
    report['aggregate'] = {}
    for name in ('cpu', 'old', 'new'):
        data = [row['calls'][name] for row in measured]
        metrics = {'outer_wall_seconds': summary([row['outer_wall_seconds'] for row in data])}
        if name != 'cpu':
            for key in ('input_snapshot_seconds', 'transfer_seconds_single_call', 'stats_read_seconds_single_call', 'transfer_and_stats_seconds_single_call', 'result_validation_and_copy_seconds', 'process_seconds_single_call'):
                metrics[key] = summary([row['timing'][key] for row in data])
        report['aggregate'][name] = metrics
    report['paired_ratios'] = {}
    for numerator, denominator in (('old', 'new'), ('cpu', 'new')):
        ratios = [row['calls'][numerator]['outer_wall_seconds'] / row['calls'][denominator]['outer_wall_seconds'] for row in measured]
        report['paired_ratios'][numerator + '_over_' + denominator] = {'median_of_paired_ratios': summary(ratios)['median'], 'ratio_of_medians': report['aggregate'][numerator]['outer_wall_seconds']['median'] / report['aggregate'][denominator]['outer_wall_seconds']['median'], 'n': MEASUREMENTS}
    report['warmups_excluded'] = WARMUPS
    report['outliers_removed'] = 0
    report['expected_hardware_frames'] = 66
    return total_packets

def run_stability(ctx, driver, new_module, candidate, seconds, report, save, stream, run_dir, run_id):
    service, configuration = configure_service(new_module, driver, ctx['rois'], candidate)
    HELD['new_service'] = service
    report['configuration_once'] = configuration
    check_status(driver.read_status(), 0)
    start = time.monotonic()
    next_checkpoint = 60.0
    frames = 0
    initial_memory = memory_sample()
    report.update(requested_duration_seconds=seconds, validated_hardware_frames=0, errors=0, initial_memory=initial_memory, checkpoints=[], samples={}, loop_started_utc=datetime.now(timezone.utc).isoformat(), memory_note='Samples after each safe transfer; frame-id identity set grows by design. All per-frame records go to JSONL, not an unbounded in-memory list.')
    save()
    first_sample = None
    elapsed_sum = 0.0
    min_time = max_time = None
    while time.monotonic() - start < seconds:
        frame_id = run_id + '_stable_' + str(frames)
        report['current_attempt'] = {'frame_id': frame_id, 'expected_packet_before': frames & 65535}
        HELD['current_mask'] = None
        tick = time.perf_counter_ns()
        result = service.process(ctx['gray'], frame_id=frame_id)
        call_seconds = (time.perf_counter_ns() - tick) / 1000000000.0
        HELD['last_mask'] = result['mask']
        HELD['current_mask'] = result['mask']
        row = check_hardware(result, driver, ctx, frames, frames + 1)
        frames += 1
        elapsed = time.monotonic() - start
        mem = memory_sample()
        row.update(frame_id=frame_id, frame_number=frames, elapsed_monotonic_seconds=elapsed, board_utc=datetime.now(timezone.utc).isoformat(), outer_wall_seconds=call_seconds, memory=mem, passed=True)
        append_record(stream, row)
        if first_sample is None:
            path = run_dir / 'first_stability_mask.bin'
            result['mask'].tofile(path)
            first_sample = {'file': path.name, 'frame_number': frames, 'bytes': path.stat().st_size, 'sha256': digest(path)}
            report['samples']['first'] = first_sample
        elapsed_sum += call_seconds
        min_time = call_seconds if min_time is None else min(min_time, call_seconds)
        max_time = call_seconds if max_time is None else max(max_time, call_seconds)
        report.update(validated_hardware_frames=frames, elapsed_monotonic_seconds=elapsed, current_memory=mem, last_driver_safety=row['driver_safety'], service_seen_frame_ids=len(service._seen_frame_ids), errors=0)
        report.pop('current_attempt', None)
        if elapsed >= next_checkpoint:
            checkpoint = {'frames': frames, 'elapsed_monotonic_seconds': elapsed, 'board_utc': row['board_utc'], 'memory': mem, 'last_packet_count': result['status']['completed_packets'], 'errors': 0, 'service_seen_frame_ids': len(service._seen_frame_ids)}
            report['checkpoints'].append(checkpoint)
            stream.flush()
            os.fsync(stream.fileno())
            save()
            announce('CLOSURE_STABILITY_CHECKPOINT', frames, round(elapsed, 3))
            next_checkpoint = (math.floor(elapsed / 60) + 1) * 60
    elapsed = time.monotonic() - start
    if elapsed < seconds or not frames:
        raise AssertionError('Stability duration/frame target not met')
    path = run_dir / 'last_stability_mask.bin'
    HELD['last_mask'].tofile(path)
    report['samples']['last'] = {'file': path.name, 'frame_number': frames, 'bytes': path.stat().st_size, 'sha256': digest(path)}
    report.update(elapsed_monotonic_seconds=elapsed, loop_completed_utc=datetime.now(timezone.utc).isoformat(), final_memory=memory_sample(), outer_call_seconds={'n': frames, 'mean': elapsed_sum / frames, 'min': min_time, 'max': max_time}, stability_scope='Repeated frozen latest17 B hardware chain with strict checks; no camera or whole-system C stability claim')
    stream.flush()
    os.fsync(stream.fileno())
    return frames

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('profile', 'validate', 'stability'), required=True)
    parser.add_argument('--package', type=Path, default=ROOT.parent / 'pcb_b_roi_v03')
    parser.add_argument('--previous', type=Path, default=ROOT.parent / 'pcb_b_integration_v031')
    parser.add_argument('--candidate')
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('20261101_[a-f0-9]{8}', args.run_id):
        raise ValueError('Expected run ID 20261101_<8 lowercase hex>')
    if args.mode == 'stability' and args.seconds < 1800:
        raise ValueError('B stability requires at least 1800 seconds')
    if args.mode != 'profile' and (not args.candidate):
        raise ValueError('Select a strict candidate after reviewing the microprofile')
    package, previous = (args.package.resolve(), args.previous.resolve())
    manifests = [manifest_check(ROOT, 'new_package_manifest.json'), manifest_check(package, 'package_manifest.json')]
    if args.mode != 'profile':
        manifests.append(manifest_check(previous, 'new_package_manifest.json'))
    if not args.execute:
        print(json.dumps({'state': 'plan_only', 'hardware_executed': False, 'mode': args.mode, 'candidate': args.candidate, 'manifests': manifests, 'environment': environment(), 'validate_rounds': 33 if args.mode == 'validate' else None, 'validate_dma_frames': 66 if args.mode == 'validate' else None, 'stability_minimum_seconds': args.seconds if args.mode == 'stability' else None, 'no_camera': True}, ensure_ascii=False, indent=2))
        return 0
    if platform.system() != 'Linux' or platform.machine().lower() not in ('armv7l', 'armv8l'):
        raise EnvironmentError('Measure on PYNQ-Z2 ARM Linux')
    if args.mode == 'profile':
        execute_profile(package, args.run_id, ROOT)
        return 0
    if os.geteuid() != 0 or not os.environ.get('XILINX_XRT'):
        raise PermissionError('Hardware execution requires root and initialized XRT before PYNQ import')
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    def terminate(_number, _frame):
        raise RuntimeError('Termination requested; determine DMA safety before exit')
    signal.signal(signal.SIGTERM, terminate)
    import numpy as np
    sys.path.insert(0, str(package / 'software'))
    from roi_driver import RoiDriver
    from roi_reference import load_inputs
    import pynq
    gray, _, profiles = load_inputs(package)
    rois = profiles['latest17']['rois']
    if len(rois) != 17 or any((not roi['enabled'] for roi in rois)):
        raise ValueError('Require frozen latest17 profile')
    expected = np.load(package / 'fixtures/mask_latest17.npy', allow_pickle=False)
    stats = json.loads((package / 'fixtures/expected_stats.json').read_text(encoding='utf-8'))['latest17']['stats']
    union = np.zeros(gray.shape, dtype=bool)
    for roi in rois:
        x0, y0, x1, y1 = roi['box']
        union[y0:y1, x0:x1] = True
    ctx = {'gray': gray, 'rois': rois, 'union': union, 'expected_mask': expected, 'expected_stats': stats, 'expected_hash': hashlib.sha256(expected.tobytes()).hexdigest()}
    new_module = load_module(ROOT / 'software/aoi_roi_service.py', 'closure_new_service')
    if args.candidate not in new_module.MASK_VALIDATORS:
        raise ValueError('Unknown strict mask candidate')
    old_module = load_module(previous / 'software/aoi_roi_service.py', 'closure_old_service') if args.mode == 'validate' else None
    run_dir = ROOT / 'evidence' / ('closure_' + args.mode + '_' + args.run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / 'report.json'
    sources = [ROOT / 'software/aoi_roi_service.py', Path(__file__).resolve(), ROOT / 'tools/profile_masks.py', previous / 'software/aoi_roi_service.py', package / 'software/roi_driver.py', package / 'software/transport.py', package / 'release/roi_v0_3/pcb_roi.bit', package / 'release/roi_v0_3/pcb_roi.hwh']
    report = {'schema': 'pcb-b-closure/0.3.2', 'mode': args.mode, 'run_id': args.run_id, 'state': 'starting', 'passed': False, 'run_directory': str(run_dir), 'hardware_executed': True, 'environment': {**environment(), 'pynq': pynq.__version__, 'numpy': np.__version__}, 'manifests': manifests, 'source_hashes': {str(path): digest(path) for path in sources}, 'candidate': args.candidate, 'seed': SEED, 'warmup_count': 3, 'measurement_count': 30, 'configuration': {'width': 2533, 'height': 1900, 'threshold': 120, 'invert': True, 'roi_enable': True, 'rois': rois}, 'input_raw_sha256': hashlib.sha256(gray.tobytes()).hexdigest(), 'expected_output_raw_sha256': ctx['expected_hash'], 'expected_stats': stats, 'timing_scope': 'Full API call includes strict checks, copies, DMA allocation/cache/reset/wait/free and stats; CPU union cached. Hash/golden checks and evidence writes outside timed calls. Not pure PL latency/FPS.', 'no_camera': True, 'initial_memory': memory_sample()}
    save = lambda: save_json(report_path, report)
    save()
    stream = (run_dir / 'records.jsonl').open('x', encoding='utf-8', buffering=1)
    driver = None
    try:
        driver = RoiDriver.from_bitfile(package / 'release/roi_v0_3/pcb_roi.bit', timeout=10.0)
        HELD['driver'] = driver
        driver.recover()
        report['initial_status'] = driver.read_status()
        check_status(report['initial_status'], 0)
        report['state'] = 'running'
        save()
        frames = run_validate(ctx, driver, old_module, new_module, args.candidate, report, save, stream, run_dir, args.run_id) if args.mode == 'validate' else run_stability(ctx, driver, new_module, args.candidate, args.seconds, report, save, stream, run_dir, args.run_id)
        report['final_status'] = driver.read_status()
        check_status(report['final_status'], frames)
        report['final_driver_safety'] = snapshot(driver)
        safe_idle(report['final_driver_safety'])
        report.update(state='complete', passed=True, validated_hardware_frames=frames, completed_utc=datetime.now(timezone.utc).isoformat(), final_memory=memory_sample())
        stream.flush()
        os.fsync(stream.fileno())
        save()
        announce('CLOSURE_COMPLETE', report_path)
        return 0
    except BaseException as exc:
        try:
            state = snapshot(driver)
            must_hold = unsafe(state)
        except BaseException as snapshot_error:
            state = {'snapshot_error': type(snapshot_error).__name__}
            must_hold = driver is not None
        try:
            report.update(state='held_for_recovery' if must_hold else 'failed_stopped', passed=False, errors=1, error=type(exc).__name__ + ': ' + str(exc), traceback=traceback.format_exc(), failure_driver_snapshot=state, failure_memory=memory_sample())
            if HELD.get('current_mask') is not None:
                HELD['current_mask'].tofile(run_dir / 'failed_attempt_mask.bin')
            save()
            announce('CLOSURE_FAILED', report_path, report['state'])
        except BaseException:
            pass
        finally:
            if must_hold:
                while True:
                    try:
                        time.sleep(30)
                    except BaseException:
                        pass
        return 1
    finally:
        try:
            stream.flush()
        except BaseException:
            pass
if __name__ == '__main__':
    raise SystemExit(main())
