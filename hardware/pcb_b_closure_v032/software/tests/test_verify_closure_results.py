from copy import deepcopy
import csv
import hashlib
import importlib.util
import inspect
import json
import math
from pathlib import Path
import random
import statistics
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
SIZE = 2533 * 1900
RAW = bytes(SIZE)
HASH = hashlib.sha256(RAW).hexdigest()
RUN_ID = '20261101_aabbccdd'
ROIS = [{'name': 'r' + str(i), 'box': [i, i, i + 1, i + 1], 'enabled': True} for i in range(17)]
STATS = {'roi_counts': {r['name']: 0 for r in ROIS}, 'roi_counts_by_index': [0] * 17, 'valid_pixels': SIZE, 'output_white_pixels': 0, 'roi_union_pixels': 17}
CFG = {'width': 2533, 'height': 1900, 'threshold': 120, 'invert': True, 'roi_enable': True, 'rois': ROIS}
CTX = {'configuration': CFG, 'input_hash': 'a' * 64, 'expected_hash': HASH, 'stats': STATS, 'mask_bytes': SIZE, 'mask_raw': RAW}
TIME_KEYS = ('input_snapshot_seconds', 'transfer_seconds_single_call', 'stats_read_seconds_single_call', 'transfer_and_stats_seconds_single_call', 'result_validation_and_copy_seconds', 'process_seconds_single_call')

def stats(values):
    values = list(values)
    ordered = sorted(values)
    p = 0.95 * (len(values) - 1)
    lo, hi = (math.floor(p), math.ceil(p))
    return {'n': len(values), 'unit': 'seconds', 'min': min(values), 'max': max(values), 'mean': statistics.mean(values), 'median': statistics.median(values), 'p95_linear': ordered[lo] + (p - lo) * (ordered[hi] - ordered[lo]), 'std_population': statistics.pstdev(values)}

def status(count):
    return {'raw': (count & 65535) << 16 | 772, 'abi': 3, 'completed_packets': count & 65535, 'busy': False, 'inpacket': False, 'cfg_ack': True, 'cfg_error': False, 'frame_error': False}

def transfer(before):
    return {'stage': 'complete', 'nbytes': SIZE, 'received_bytes': SIZE, 'packet_delta': 1, 'packets_before': before & 65535, 'packets_after': before + 1 & 65535, 'threshold': 120, 'bypass': False, 'invert': True, 'roi_enable': True, 'algorithm_status': status(before + 1)['raw'], 'tx_status': 4098, 'rx_status': 4098, 'reset_tx_status': 1, 'reset_rx_status': 1, 'elapsed_seconds': 0.129}

def safety(last):
    return {'poisoned': False, 'active': False, 'dma_safe': True, 'buffers': 0, 'retained_buffers': 0, 'last_transfer': deepcopy(last)}

def memory(index=0):
    return {'rss_kib': 40000 + index, 'rss_peak_kib': 50000 + index, 'cma_total_kib': 131072, 'cma_free_kib': 65536, 'source': '/proc/self/status VmRSS/VmHWM; /proc/meminfo CmaTotal/CmaFree'}

def configuration():
    return {'state': 'configured', 'configuration': deepcopy(CFG), 'status': status(0), 'configuration_seconds': 0.02}

def common(mode):
    return {'schema': 'pcb-b-closure/0.3.2', 'mode': mode, 'run_id': RUN_ID, 'state': 'complete', 'passed': True, 'candidate': 'sum', 'seed': 20261101, 'warmup_count': 3, 'measurement_count': 30, 'configuration': deepcopy(CFG), 'input_raw_sha256': CTX['input_hash'], 'expected_output_raw_sha256': HASH, 'expected_stats': deepcopy(STATS), 'hardware_executed': True, 'no_camera': True, 'initial_status': status(0), 'initial_memory': memory(), 'errors': 0, 'samples': {}, 'environment': {'machine': 'armv7l', 'platform': 'Linux', 'uid': 0, 'XILINX_XRT': '/usr'}, 'configuration_once': {}, 'manifests': [], 'source_hashes': {}}

def call(name, index, before):
    source = {key: deepcopy(STATS[key]) for key in ('roi_counts', 'roi_counts_by_index', 'valid_pixels', 'output_white_pixels')}
    source.update(output_raw_sha256=HASH, frame_id=RUN_ID + '_' + name + '_' + str(index), outer_wall_seconds={'cpu': 0.23, 'old': 0.44, 'new': 0.25}[name] + index * 1e-05, all_bytes_and_statistics_match=True)
    if name != 'cpu':
        last = transfer(before)
        source.update(driver_transfer=last, driver_safety=safety(last), status=status(before + 1), implementation='pcb-b-closure/0.3.2' if name == 'new' else None, mask_validation='sum' if name == 'new' else None, output_ownership='frozen_driver_owning_base' if name == 'new' else None, mask_owns_data=True, mask_base_is_none=True, mask_c_contiguous=True, timing={'configuration_seconds_once': 0.02, 'input_snapshot_seconds': 0.028, 'transfer_seconds_single_call': 0.13, 'stats_read_seconds_single_call': 0.002, 'transfer_and_stats_seconds_single_call': 0.132, 'result_validation_and_copy_seconds': 0.27 if name == 'old' else 0.07, 'process_seconds_single_call': 0.43 if name == 'old' else 0.24, 'scope': 'synthetic test only; not board timing'})
    return source

class VerifyClosureResultsTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.verifier = __import__('verify_closure_results')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic_closure_pc_audit_')
        self.run = Path(self.tmp.name)
        self.report = None
        self.rows = None
        self.golden = patch.object(self.verifier, '_load_golden', return_value=deepcopy(CTX))
        self.provenance = patch.object(self.verifier, '_check_provenance', return_value=None)
        self.golden.start()
        self.provenance.start()

    def tearDown(self):
        self.provenance.stop()
        self.golden.stop()
        self.tmp.cleanup()

    def audit(self, mode):
        value = self.verifier.verify(mode, self.run, self.run / 'fake_package', ROOT, self.run / 'fake_previous')
        self.assertTrue(value.get('audit_passed', value.get('passed', False)))
        return value

    def rejected(self, mode):
        with self.assertRaises(ValueError):
            self.verifier.verify(mode, self.run, self.run / 'fake_package', ROOT, self.run / 'fake_previous')

    def save(self, mode):
        name = 'profile_report.json' if mode == 'profile' else 'report.json'
        (self.run / name).write_text(json.dumps(self.report), encoding='utf-8')
        if self.rows is not None:
            (self.run / 'records.jsonl').write_text(''.join((json.dumps(r) + '\n' for r in self.rows)), encoding='utf-8')

    def sample(self, label, index, implementation=None):
        path = self.run / (label + '_mask.bin')
        path.write_bytes(RAW)
        value = {'file': path.name, 'bytes': SIZE, 'sha256': HASH}
        value['iteration' if implementation else 'frame_number'] = index
        if implementation:
            value['implementation'] = implementation
        self.report['samples'][label] = value

    def build_validate(self):
        self.report = common('validate')
        self.rows = []
        rng = random.Random(20261101)
        packets = 0
        self.report['configuration_once'] = {'old': configuration(), 'new': configuration()}
        for index in range(33):
            order = ['cpu', 'old', 'new']
            rng.shuffle(order)
            calls = {}
            for name in order:
                calls[name] = call(name, index, packets)
                if name != 'cpu':
                    packets += 1
            self.rows.append({'iteration': index, 'phase': 'warmup' if index < 3 else 'measured', 'order': order, 'calls': calls, 'passed': True})
        self.report.update(records=self.rows, completed_rounds=33, validated_hardware_frames=66, expected_hardware_frames=66, final_status=status(66), final_driver_safety=safety(transfer(65)), warmups_excluded=3, outliers_removed=0, final_memory=memory(33))
        self.report['aggregate'] = {}
        measured = self.rows[3:]
        for name in ('cpu', 'old', 'new'):
            selected = [r['calls'][name] for r in measured]
            metrics = {'outer_wall_seconds': stats((r['outer_wall_seconds'] for r in selected))}
            if name != 'cpu':
                metrics.update({key: stats((r['timing'][key] for r in selected)) for key in TIME_KEYS})
            self.report['aggregate'][name] = metrics
        self.report['paired_ratios'] = {}
        for num, den in [('old', 'new'), ('cpu', 'new')]:
            ratios = [r['calls'][num]['outer_wall_seconds'] / r['calls'][den]['outer_wall_seconds'] for r in measured]
            self.report['paired_ratios'][num + '_over_' + den] = {'n': 30, 'median_of_paired_ratios': statistics.median(ratios), 'ratio_of_medians': self.report['aggregate'][num]['outer_wall_seconds']['median'] / self.report['aggregate'][den]['outer_wall_seconds']['median']}
        for name in ('cpu', 'old', 'new'):
            self.sample('first_measured_' + name, 3, name)
            self.sample('last_measured_' + name, 32, name)
        self.save('validate')

    def build_stability(self):
        self.report = common('stability')
        self.rows = []
        self.report['configuration_once'] = configuration()
        for index in range(30):
            row = call('new', index, index)
            row.update(frame_id=RUN_ID + '_stable_' + str(index), frame_number=index + 1, elapsed_monotonic_seconds=(index + 1) * 60.0, board_utc='2026-11-01T00:00:00+00:00', memory=memory(index + 1), passed=True)
            self.rows.append(row)
        self.report.update(requested_duration_seconds=1800, elapsed_monotonic_seconds=1800.2, validated_hardware_frames=30, service_seen_frame_ids=30, current_memory=memory(30), final_memory=memory(30), last_driver_safety=safety(transfer(29)), final_driver_safety=safety(transfer(29)), final_status=status(30), loop_started_utc='2026-11-01T00:00:00+00:00', loop_completed_utc='2026-11-01T00:30:00+00:00', outer_call_seconds={'n': 30, 'mean': statistics.mean((r['outer_wall_seconds'] for r in self.rows)), 'min': min((r['outer_wall_seconds'] for r in self.rows)), 'max': max((r['outer_wall_seconds'] for r in self.rows))})
        self.report['checkpoints'] = [{'frames': r['frame_number'], 'elapsed_monotonic_seconds': r['elapsed_monotonic_seconds'], 'board_utc': r['board_utc'], 'memory': r['memory'], 'last_packet_count': r['status']['completed_packets'], 'errors': 0, 'service_seen_frame_ids': r['frame_number']} for r in self.rows]
        self.sample('first', 1)
        self.sample('last', 30)
        self.save('stability')

    def build_profile(self):
        candidates = ['compare', 'sum', 'addwrap', 'addreduce', 'bytes_count']
        rng = random.Random(20261101)
        self.report = {'schema': 'pcb-b-strict-mask-microprofile/0.3.2', 'run_id': RUN_ID, 'state': 'complete', 'passed': True, 'hardware_executed': False, 'mask_bytes': SIZE, 'shape': [1900, 2533], 'input_raw_sha256': HASH, 'expected_white_pixels': 0, 'warmups': 3, 'measured': 10, 'input_unchanged': True, 'environment': {'machine': 'armv7l', 'platform': 'Linux', 'uid': 1000}, 'records': [], 'correctness': {name: {'all_256_values_checked': True, 'valid_values': 2, 'invalid_values_rejected': 254, 'large_first_middle_last_corruption_rejected': True} for name in candidates}}
        for index in range(13):
            order = candidates.copy()
            rng.shuffle(order)
            for name in order:
                self.report['records'].append({'iteration': index, 'phase': 'warmup' if index < 3 else 'measured', 'candidate': name, 'order': order, 'seconds': 0.01 * (candidates.index(name) + 1) + index * 1e-05, 'white_pixels': 0})
        self.report['summary'] = {name: stats((r['seconds'] for r in self.report['records'] if r['phase'] == 'measured' and r['candidate'] == name)) for name in candidates}
        self.report['lowest_median_candidate'] = 'compare'
        self.rows = None
        self.save('profile')

    def test_valid_validate_evidence_and_rejects_aggregate_tampering(self):
        self.build_validate()
        self.audit('validate')
        self.report['aggregate']['new']['outer_wall_seconds']['median'] += 0.1
        self.save('validate')
        self.rejected('validate')

    def test_pc_microprofile_cannot_be_presented_as_arm_measurement(self):
        self.build_profile()
        self.report['environment']['machine'] = 'AMD64'
        self.save('profile')
        self.rejected('profile')

    def test_validate_rejects_missing_round_and_wrong_packet_sequence(self):
        self.build_validate()
        self.rows.pop()
        self.report['records'] = self.rows
        self.save('validate')
        self.rejected('validate')
        self.build_validate()
        self.rows[5]['calls']['new']['driver_transfer']['packet_delta'] = 2
        self.save('validate')
        self.rejected('validate')

    def test_validate_rejects_nonowning_output_and_dma_unsafe(self):
        self.build_validate()
        self.rows[9]['calls']['new']['mask_owns_data'] = False
        self.save('validate')
        self.rejected('validate')
        self.build_validate()
        self.rows[8]['calls']['old']['driver_safety']['retained_buffers'] = 1
        self.save('validate')
        self.rejected('validate')

    def test_validate_rejects_changed_first_mask_bytes(self):
        self.build_validate()
        self.audit('validate')
        path = self.run / self.report['samples']['first_measured_new']['file']
        with path.open('r+b') as stream:
            stream.write(b'\x01')
        self.rejected('validate')

    def test_valid_stability_evidence_rejects_short_duration_and_missing_frame(self):
        self.build_stability()
        self.audit('stability')
        self.report['elapsed_monotonic_seconds'] = 1799.9
        self.save('stability')
        self.rejected('stability')
        self.build_stability()
        self.rows.pop(4)
        self.save('stability')
        self.rejected('stability')

    def test_stability_rejects_bad_halted_or_final_poisoned_or_checkpoint(self):
        self.build_stability()
        self.rows[3]['driver_transfer']['reset_rx_status'] = 0
        self.save('stability')
        self.rejected('stability')
        self.build_stability()
        self.report['final_driver_safety']['poisoned'] = True
        self.save('stability')
        self.rejected('stability')
        self.build_stability()
        self.report['checkpoints'][0]['frames'] = 2
        self.save('stability')
        self.rejected('stability')

    def test_valid_profile_65_samples_and_rejects_removed_candidate_or_bad_count(self):
        self.build_profile()
        self.audit('profile')
        self.report['records'].pop()
        self.save('profile')
        self.rejected('profile')
        self.build_profile()
        self.report['records'][0]['white_pixels'] = 1
        self.save('profile')
        self.rejected('profile')

    def test_profile_rejects_incomplete_binary_probe_or_changed_p95(self):
        self.build_profile()
        self.report['correctness']['sum']['invalid_values_rejected'] = 253
        self.save('profile')
        self.rejected('profile')
        self.build_profile()
        self.report['summary']['sum']['p95_linear'] += 0.01
        self.save('profile')
        self.rejected('profile')
if __name__ == '__main__':
    unittest.main(verbosity=2)
