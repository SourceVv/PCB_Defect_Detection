from __future__ import annotations
from copy import deepcopy
import hashlib
from numbers import Integral
from pathlib import Path
import sys
import threading
import time
from types import CodeType, FunctionType, MappingProxyType
import numpy as np

class ServiceBusyError(RuntimeError):
    pass

class ServiceStateError(RuntimeError):
    pass

class ResultValidationError(RuntimeError):
    pass

def _mask_array(mask):
    if not isinstance(mask, np.ndarray):
        raise ResultValidationError('Mask validation requires a uint8 ndarray')
    plain = np.asarray(mask)
    if plain.dtype != np.uint8:
        raise ResultValidationError('Mask validation requires a uint8 ndarray')
    return plain

def validate_mask_compare(mask):
    mask = _mask_array(mask)
    if not np.all((mask == 0) | (mask == 255)):
        raise ResultValidationError('Mask contains a value other than 0/255')
    return int(np.count_nonzero(mask))

def validate_mask_sum(mask):
    mask = _mask_array(mask)
    white = int(np.count_nonzero(mask))
    if int(mask.sum(dtype=np.uint64)) != 255 * white:
        raise ResultValidationError('Mask contains a value other than 0/255')
    return white

def validate_mask_addwrap(mask):
    mask = _mask_array(mask)
    wrapped = np.add(mask, np.uint8(1), dtype=np.uint8)
    if not np.all(wrapped <= np.uint8(1)):
        raise ResultValidationError('Mask contains a value other than 0/255')
    return int(np.count_nonzero(mask))

def validate_mask_addreduce(mask):
    mask = _mask_array(mask)
    if not mask.size:
        return 0
    wrapped = np.add(mask, np.uint8(1), dtype=np.uint8)
    if int(wrapped.max()) > 1:
        raise ResultValidationError('Mask contains a value other than 0/255')
    return int(mask.size) - int(wrapped.sum(dtype=np.uint64))

def validate_mask_bytes_count(mask):
    mask = _mask_array(mask)
    raw = mask.tobytes(order='C')
    white, zeros = (raw.count(b'\xff'), raw.count(b'\x00'))
    if white + zeros != mask.size:
        raise ResultValidationError('Mask contains a value other than 0/255')
    return int(white)
MASK_VALIDATORS = MappingProxyType({'compare': validate_mask_compare, 'sum': validate_mask_sum, 'addwrap': validate_mask_addwrap, 'addreduce': validate_mask_addreduce, 'bytes_count': validate_mask_bytes_count})
_FROZEN_SOURCE_HASHES = {'roi_driver': '08982c4f1021ac037227cfc0219f74a4fab7535ee71e713766bb7607a62b3a05', 'transport': 'fbfbd9832b3208d27c10224b5f723a54556cf4ae9a0f9073bcde36baf6daff72'}

def _plain_function(value):
    return value.__func__ if isinstance(value, (staticmethod, classmethod)) else value

def _frozen_driver_proof(driver):
    try:
        roi_module, transport_module = (sys.modules.get('roi_driver'), sys.modules.get('transport'))
        if roi_module is None or transport_module is None:
            return None
        roi_class, transport_class = (roi_module.RoiDriver, transport_module.ThresholdDriver)
        if type(driver) is not roi_class or roi_class.__mro__ != (roi_class, transport_class, object):
            return None
        methods = []
        for module, cls in ((roi_module, roi_class), (transport_module, transport_class)):
            source = Path(module.__file__).read_bytes()
            if hashlib.sha256(source).hexdigest() != _FROZEN_SOURCE_HASHES[module.__name__]:
                return None
            filename = cls.__dict__['__init__'].__code__.co_filename
            compiled = compile(source, filename, 'exec', dont_inherit=True)
            class_code = next((c for c in compiled.co_consts if isinstance(c, CodeType) and c.co_name == cls.__name__))
            for code in class_code.co_consts:
                if not isinstance(code, CodeType):
                    continue
                actual = _plain_function(cls.__dict__.get(code.co_name))
                if not isinstance(actual, FunctionType):
                    if isinstance(cls.__dict__.get(code.co_name), property):
                        continue
                    return None
                if actual.__code__ != code:
                    return None
                methods.append((cls, code.co_name, actual))
        return tuple(methods)
    except (AttributeError, OSError, KeyError, StopIteration, TypeError, ValueError):
        return None

def _proof_still_valid(driver, proof):
    if not proof:
        return False
    values = vars(driver)
    return all((name not in values and _plain_function(cls.__dict__.get(name)) is function for cls, name, function in proof))

def _full_owning_base(mask):
    if type(mask) is not np.ndarray or not mask.flags.c_contiguous or (not mask.flags.writeable):
        return None
    owner = mask
    while owner.base is not None:
        if type(owner.base) is not np.ndarray:
            return None
        owner = owner.base
    if not owner.flags.owndata or not owner.flags.c_contiguous or (not owner.flags.writeable) or (owner.dtype != np.uint8) or (owner.nbytes != mask.nbytes) or (owner.__array_interface__['data'][0] != mask.__array_interface__['data'][0]):
        return None
    return owner

def _integer(value, name, minimum, maximum):
    if not isinstance(value, Integral) or isinstance(value, (bool, np.bool_)) or (not minimum <= value <= maximum):
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return int(value)

def _normalize_config(width, height, rois, threshold, invert, roi_enable):
    width = _integer(width, 'width', 1, 65535)
    height = _integer(height, 'height', 1, 65535)
    if width * height % 4 or width * height > (1 << 23) - 1:
        raise ValueError('width*height must be a multiple of four and <= 2**23-1')
    threshold = _integer(threshold, 'threshold', 0, 255)
    if type(invert) is not bool or type(roi_enable) is not bool:
        raise ValueError('invert and roi_enable must be bool')
    if not isinstance(rois, (list, tuple)) or len(rois) > 17:
        raise ValueError('rois must contain at most 17 ROI dictionaries')
    normalized, names = ([], set())
    for index, roi in enumerate(rois):
        if not isinstance(roi, dict):
            raise ValueError('Each ROI must be a dictionary')
        name = roi.get('name', str(index))
        enabled = roi.get('enabled', True)
        box = roi.get('box')
        if not isinstance(name, str) or not name or name in names or (type(enabled) is not bool):
            raise ValueError('ROI names must be unique/nonempty; enabled must be bool')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError('ROI box must be [x0,y0,x1,y1]')
        x0, y0, x1, y1 = [_integer(v, 'ROI coordinate', 0, 65535) for v in box]
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError('ROI is empty or outside the image: ' + name)
        names.add(name)
        normalized.append({'name': name, 'box': [x0, y0, x1, y1], 'enabled': enabled})
    return {'width': width, 'height': height, 'rois': normalized, 'threshold': threshold, 'invert': invert, 'roi_enable': roi_enable}

def _checked_status(value, label):
    if not isinstance(value, dict):
        raise ResultValidationError(label + ': missing status dictionary')
    try:
        raw = _integer(value['raw'], label + '.raw', 0, 4294967295)
        packet = _integer(value['completed_packets'], label + '.completed_packets', 0, 65535)
        abi = _integer(value['abi'], label + '.abi', 0, 255)
        expected = {'busy': bool(raw & 1), 'inpacket': bool(raw & 2), 'cfg_ack': bool(raw & 4), 'cfg_error': bool(raw & 8), 'frame_error': bool(raw & 16)}
        if abi != 3 or raw >> 8 & 255 != abi or raw >> 16 != packet or raw & 224:
            raise ValueError('unexpected ABI/counter/reserved bits')
        if any((type(value[key]) is not bool or value[key] != item for key, item in expected.items())):
            raise ValueError('raw/decoded flags disagree')
        if any((expected[key] for key in ('busy', 'inpacket', 'cfg_error', 'frame_error'))):
            raise ValueError('hardware is busy or reports an error')
    except (KeyError, TypeError, ValueError) as exc:
        raise ResultValidationError(label + ': invalid or unsafe ABI3 status') from exc
    return deepcopy(value)

class AoiRoiService:

    def __init__(self, driver, *, width, height, rois, threshold=120, invert=True, roi_enable=True, mask_validation='compare'):
        self._config = _normalize_config(width, height, rois, threshold, invert, roi_enable)
        if not isinstance(mask_validation, str) or mask_validation not in MASK_VALIDATORS:
            raise ValueError('mask_validation must name a strict MASK_VALIDATORS implementation')
        self._mask_validation_name = mask_validation
        self._mask_validator = MASK_VALIDATORS[mask_validation]
        for method in ('configure', 'transfer', 'read_results', 'read_status'):
            if not callable(getattr(driver, method, None)):
                raise TypeError('driver must supply ' + method)
        self._driver = driver
        self._lock = threading.Lock()
        self._seen_frame_ids = set()
        self._state = 'new'
        self._failure = None
        self._configuration_seconds = None
        self._ownership_proof = _frozen_driver_proof(driver)

    @property
    def state(self):
        return self._state

    @property
    def failure(self):
        return self._failure

    @property
    def configuration(self):
        return deepcopy(self._config)

    def _enter(self):
        if not self._lock.acquire(blocking=False):
            raise ServiceBusyError('Another configure/process call is active')

    def _require_healthy_driver(self):
        if bool(getattr(self._driver, 'poisoned', False)):
            self._state = 'faulted'
            self._failure = 'Driver is poisoned; retain the process and buffers for manual recovery'
            raise ServiceStateError(self._failure)

    def _mark_fault(self, exc):
        self._state = 'faulted'
        self._failure = type(exc).__name__ + ': ' + str(exc)

    def configure(self):
        self._enter()
        try:
            if self._state != 'new':
                raise ServiceStateError('configure is allowed exactly once on a new service')
            self._require_healthy_driver()
            started = time.perf_counter()
            try:
                before = _checked_status(self._driver.read_status(), 'before configure')
                cfg = self._config
                self._driver.configure(cfg['width'], cfg['height'], deepcopy(cfg['rois']), invert=cfg['invert'], roi_enable=cfg['roi_enable'])
                after = _checked_status(self._driver.read_status(), 'after configure')
                if after['completed_packets'] != before['completed_packets']:
                    raise ResultValidationError('A frame completed during configuration; owner is not exclusive')
                self._configuration_seconds = time.perf_counter() - started
                self._state = 'configured'
                return {'state': self._state, 'configuration': self.configuration, 'status': after, 'configuration_seconds': self._configuration_seconds}
            except BaseException as exc:
                self._mark_fault(exc)
                raise
        finally:
            self._lock.release()

    def _validate_input(self, aligned_gray, frame_id):
        if not isinstance(frame_id, str) or not frame_id.strip() or len(frame_id) > 128:
            raise ValueError('frame_id must be a nonempty string with at most 128 characters')
        cfg = self._config
        if not isinstance(aligned_gray, np.ndarray) or aligned_gray.dtype != np.uint8 or aligned_gray.ndim != 2 or (aligned_gray.shape != (cfg['height'], cfg['width'])) or (aligned_gray.nbytes != cfg['height'] * cfg['width']):
            raise ValueError('aligned_gray must be a uint8 array with exact shape (height,width)')

    def _check_result(self, mask, stats, before, after, previous_transfer):
        cfg = self._config
        snapshot = vars(self._driver)
        if snapshot.get('_dma_safe') is not True or snapshot.get('_active') is not False or snapshot.get('poisoned') is not False or (not isinstance(snapshot.get('_buffers'), (list, tuple))) or snapshot['_buffers'] or (not isinstance(snapshot.get('_retained_buffers'), (list, tuple))) or snapshot['_retained_buffers']:
            raise ResultValidationError('Driver did not return to safe idle with no retained DMA buffers')
        delta = after['completed_packets'] - before['completed_packets'] & 65535
        if delta != 1:
            raise ResultValidationError('Expected exactly one completed packet, got ' + str(delta))
        if not isinstance(stats, dict):
            raise ResultValidationError('Missing frame statistics')
        committed = _checked_status(stats.get('status'), 'committed statistics')
        if committed['raw'] != after['raw'] or committed['cfg_ack'] != before['cfg_ack']:
            raise ResultValidationError('Statistics/status changed across the frame read')
        current = getattr(self._driver, 'last_transfer', None)
        if not isinstance(current, dict) or current is previous_transfer:
            raise ResultValidationError('Driver did not publish a new transfer record')
        checks = {'stage': 'complete', 'nbytes': cfg['width'] * cfg['height'], 'received_bytes': cfg['width'] * cfg['height'], 'packet_delta': 1, 'packets_before': before['completed_packets'], 'packets_after': after['completed_packets'], 'algorithm_status': committed['raw'], 'threshold': cfg['threshold'], 'bypass': False, 'invert': cfg['invert'], 'roi_enable': cfg['roi_enable']}
        if any((current.get(key) != wanted for key, wanted in checks.items())):
            raise ResultValidationError('Driver transfer record does not identify this completed frame')
        if any((type(current.get(key)) is not int or not current[key] & 1 or current[key] & 112 for key in ('reset_tx_status', 'reset_rx_status'))):
            raise ResultValidationError('Missing error-free dual-Halted completion evidence')
        if not isinstance(mask, np.ndarray) or mask.dtype != np.uint8 or mask.shape != (cfg['height'], cfg['width']):
            raise ResultValidationError('Expected a binary uint8 mask with the configured shape')
        observed_white = self._mask_validator(mask)
        nbytes = cfg['width'] * cfg['height']
        try:
            valid = _integer(stats['valid_pixels'], 'valid_pixels', 0, nbytes)
            white = _integer(stats['output_white_pixels'], 'output_white_pixels', 0, nbytes)
            indexed = stats['roi_counts_by_index']
            named = stats['roi_counts']
            if valid != nbytes or white != observed_white:
                raise ValueError('mask/count mismatch')
            if not isinstance(indexed, (list, tuple)) or len(indexed) != 17 or (not isinstance(named, dict)):
                raise ValueError('malformed ROI counts')
            counts = []
            expected_names = set()
            for index, value in enumerate(indexed):
                count = _integer(value, 'ROI count', 0, nbytes)
                counts.append(count)
                if index >= len(cfg['rois']) or not cfg['rois'][index]['enabled']:
                    if count != 0:
                        raise ValueError('disabled ROI has foreground')
                    continue
                roi = cfg['rois'][index]
                x0, y0, x1, y1 = roi['box']
                if count > (x1 - x0) * (y1 - y0):
                    raise ValueError('ROI count exceeds its rectangle')
                expected_names.add(roi['name'])
                if _integer(named[roi['name']], 'named ROI count', 0, nbytes) != count:
                    raise ValueError('named/indexed ROI counts disagree')
            if set(named) != expected_names:
                raise ValueError('ROI names do not match the configured profile')
        except (KeyError, TypeError, ValueError) as exc:
            raise ResultValidationError('Invalid committed mask/ROI counts') from exc
        return {'roi_counts': {name: int(value) for name, value in named.items()}, 'roi_counts_by_index': counts, 'output_white_pixels': white, 'valid_pixels': valid, 'packet_delta': delta, 'driver_transfer': deepcopy(current)}

    def _return_output(self, mask):
        if _proof_still_valid(self._driver, self._ownership_proof):
            if not any((isinstance(value, np.ndarray) and np.shares_memory(value, mask) for value in vars(self._driver).values())):
                owner = _full_owning_base(mask)
                if owner is not None:
                    try:
                        owner.shape = mask.shape
                        return (owner, 'frozen_driver_owning_array' if owner is mask else 'frozen_driver_owning_base')
                    except (AttributeError, TypeError, ValueError):
                        pass
        return (np.array(mask, dtype=np.uint8, order='C', copy=True), 'copy_fallback')

    def process(self, aligned_gray, *, frame_id):
        self._validate_input(aligned_gray, frame_id)
        self._enter()
        try:
            if self._state != 'configured':
                raise ServiceStateError('Service must be configured and healthy before process')
            self._require_healthy_driver()
            if frame_id in self._seen_frame_ids:
                raise ValueError('frame_id has already been used: ' + frame_id)
            started = time.perf_counter()
            snapshot = np.array(aligned_gray, dtype=np.uint8, order='C', copy=True)
            snapshot_seconds = time.perf_counter() - started
            self._seen_frame_ids.add(frame_id)
            try:
                before = _checked_status(self._driver.read_status(), 'before transfer')
                previous_transfer = getattr(self._driver, 'last_transfer', None)
                hardware_started = time.perf_counter()
                mask = self._driver.transfer(snapshot, threshold=self._config['threshold'], bypass=False)
                transfer_seconds = time.perf_counter() - hardware_started
                stats_started = time.perf_counter()
                stats = self._driver.read_results()
                stats_seconds = time.perf_counter() - stats_started
                hardware_seconds = time.perf_counter() - hardware_started
                validation_started = time.perf_counter()
                after = _checked_status(self._driver.read_status(), 'after transfer')
                result = self._check_result(mask, stats, before, after, previous_transfer)
                returned_mask, ownership = self._return_output(mask)
                result.update(schema='pcb-aoi-roi-result/0.3.1', frame_id=frame_id, mask=returned_mask, status=after, geometry=[self._config['height'], self._config['width']], threshold=self._config['threshold'], invert=self._config['invert'], roi_enable=self._config['roi_enable'], implementation='pcb-b-closure/0.3.2', mask_validation=self._mask_validation_name, output_ownership=ownership)
                validation_seconds = time.perf_counter() - validation_started
                result['timing'] = {'configuration_seconds_once': self._configuration_seconds, 'input_snapshot_seconds': snapshot_seconds, 'transfer_seconds_single_call': transfer_seconds, 'stats_read_seconds_single_call': stats_seconds, 'transfer_and_stats_seconds_single_call': hardware_seconds, 'result_validation_and_copy_seconds': validation_seconds, 'process_seconds_single_call': time.perf_counter() - started, 'scope': 'transfer includes DMA allocation/cache/start/wait/stop and statistics reads; single invocation, not pure PL latency or sustained frame rate'}
                return result
            except BaseException as exc:
                self._mark_fault(exc)
                raise
        finally:
            self._lock.release()
