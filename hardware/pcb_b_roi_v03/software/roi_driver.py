from __future__ import annotations
from numbers import Integral
from pathlib import Path
from transport import ABIError, BusyError, DMAHardwareError, DMATimeout, DriverError, MetadataError, PoisonedError, ThresholdDriver, TransferValidationError, _integer, _validate_description

class ConfigurationError(DriverError):
    pass

class ROIValidationError(TransferValidationError):

    def __init__(self, message, actual_output=None):
        super().__init__(message)
        self.actual_output = actual_output

def _validate_roi_description(ip_dict):
    mappings = _validate_description(ip_dict)
    desc = ip_dict.get('axi_gpio_cfg')
    if not isinstance(desc, dict) or desc.get('type') != 'xilinx.com:ip:axi_gpio:2.0':
        raise MetadataError('Missing or wrong type: axi_gpio_cfg')
    parameters = {str(k).lower(): v for k, v in desc.get('parameters', {}).items()}
    expected = {'c_is_dual': 1, 'c_gpio_width': 32, 'c_all_inputs': 0, 'c_all_outputs': 1, 'c_dout_default': 0, 'c_gpio2_width': 32, 'c_all_inputs_2': 1, 'c_all_outputs_2': 0, 'c_interrupt_present': 0}
    for name, wanted in expected.items():
        try:
            got = _integer(parameters[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError('Missing/invalid axi_gpio_cfg.' + name) from exc
        if got != wanted:
            raise MetadataError(f'Unexpected axi_gpio_cfg.{name}: {got}, expected {wanted}')
    try:
        base, size = (_integer(desc['phys_addr']), _integer(desc['addr_range']))
    except (KeyError, TypeError, ValueError) as exc:
        raise MetadataError('Invalid axi_gpio_cfg mapping') from exc
    if base < 0 or base % 4 or size < 4096 or (base + size > 1 << 32):
        raise MetadataError('Unsupported axi_gpio_cfg mapping')
    mappings['axi_gpio_cfg'] = (base, size)
    items = list(mappings.items())
    for index, (name, (base, size)) in enumerate(items):
        for other, (base2, size2) in items[index + 1:]:
            if max(base, base2) < min(base + size, base2 + size2):
                raise MetadataError(f'Overlapping mappings: {name}, {other}')
    return mappings

class RoiDriver(ThresholdDriver):
    ROI_COUNT = 17
    INVERT, ROI_ENABLE, CFG_TOGGLE = (1 << 10, 1 << 11, 1 << 12)
    ADDRESS_MASK = 255 << 16

    def __init__(self, dma_mmio, gpio_mmio, cfg_mmio, allocator, **kwargs):
        self._cfg = cfg_mmio
        self._configured = False
        self._geometry = None
        self._rois = []
        self._last_frame_complete = False
        super().__init__(dma_mmio, gpio_mmio, allocator, **kwargs)

    @classmethod
    def from_bitfile(cls, bitfile, *, timeout=10.0, poll_interval=0.001):
        bit = Path(bitfile).expanduser().resolve()
        if bit.suffix.lower() != '.bit' or not bit.is_file() or (not bit.with_suffix('.hwh').is_file()):
            raise MetadataError('Require existing same-name .bit/.hwh pair')
        from pynq import Overlay, MMIO, allocate
        overlay = Overlay(str(bit), download=False)
        mappings = _validate_roi_description(overlay.ip_dict)
        overlay.download()
        driver = cls(MMIO(*mappings['axi_dma_0']), MMIO(*mappings['axi_gpio_ctrl']), MMIO(*mappings['axi_gpio_cfg']), allocate, timeout=timeout, poll_interval=poll_interval)
        driver._overlay = overlay
        return driver

    def read_status(self):
        self._ensure_healthy()
        raw = int(self._gpio.read(self.GPIO_STATUS)) & 4294967295
        result = {'raw': raw, 'abi': raw >> 8 & 255, 'busy': bool(raw & 1), 'inpacket': bool(raw & 2), 'cfg_ack': bool(raw & 4), 'cfg_error': bool(raw & 8), 'frame_error': bool(raw & 16), 'completed_packets': raw >> 16 & 65535}
        if result['abi'] != 3:
            raise ABIError(f'Expected ROI ABI3, status=0x{raw:08x}')
        return result

    def _require_idle_safe(self):
        self._ensure_healthy()
        if self._active:
            raise BusyError('Transfer is active')
        status = self.read_status()
        if status['busy'] or status['inpacket']:
            raise BusyError('Algorithm/configuration is busy')
        if not self._dma_safe:
            raise ConfigurationError('Call recover first to confirm both DMA channels halted')
        if status['cfg_error'] or status['frame_error']:
            raise ConfigurationError('Sticky hardware error; diagnose and recover before reconfiguration')
        return status

    @staticmethod
    def _dimension(value, label):
        if not isinstance(value, Integral) or isinstance(value, bool) or (not 1 <= value <= 65535):
            raise ValueError(label + ' must be an integer from 1 to 65535')
        return int(value)

    @classmethod
    def validate_config(cls, width, height, rois):
        width, height = (cls._dimension(width, 'width'), cls._dimension(height, 'height'))
        if width * height > cls.MAX_BYTES or width * height % 4:
            raise ValueError('Frame bytes must be a multiple of four within Simple DMA limit')
        if not isinstance(rois, (list, tuple)) or len(rois) > cls.ROI_COUNT:
            raise ValueError('Expected at most 17 ROI dictionaries')
        normalized, names = ([], set())
        for index, roi in enumerate(rois):
            if not isinstance(roi, dict):
                raise ValueError('Each ROI is a dictionary')
            name, box = (roi.get('name', str(index)), roi.get('box'))
            enabled = roi.get('enabled', True)
            if not isinstance(name, str) or not name or name in names or (type(enabled) is not bool):
                raise ValueError('ROI names must be unique nonempty strings; enabled must be bool')
            if not isinstance(box, (list, tuple)) or len(box) != 4 or any((not isinstance(v, Integral) or isinstance(v, bool) for v in box)):
                raise ValueError('ROI box must contain four integer coordinates')
            x0, y0, x1, y1 = map(int, box)
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise ValueError('ROI is outside frame or empty: ' + name)
            names.add(name)
            normalized.append({'name': name, 'box': [x0, y0, x1, y1], 'enabled': enabled})
        return (width, height, normalized)

    def _select(self, address):
        self._ctrl_shadow = self._ctrl_shadow & ~self.ADDRESS_MASK | int(address) << 16
        self._gpio.write(self.GPIO_CONTROL, self._ctrl_shadow)

    def _read_selected(self, address):
        self._select(address)
        return int(self._cfg.read(8)) & 4294967295

    def _write_config(self, address, data):
        self._require_idle_safe()
        self._cfg.write(0, int(data) & 4294967295)
        self._ctrl_shadow = (self._ctrl_shadow & ~self.ADDRESS_MASK | int(address) << 16) ^ self.CFG_TOGGLE
        wanted = bool(self._ctrl_shadow & self.CFG_TOGGLE)
        self._gpio.write(self.GPIO_CONTROL, self._ctrl_shadow)

        def acknowledged():
            status = self.read_status()
            if status['cfg_error']:
                raise ConfigurationError(f'Hardware rejected configuration address 0x{address:02x}')
            return status['cfg_ack'] == wanted and (not status['busy'])
        self._poll(acknowledged, self._clock() + self.timeout, 'ROI configuration acknowledgment')
        got = int(self._cfg.read(8)) & 4294967295
        if got != int(data) & 4294967295:
            raise ConfigurationError(f'Configuration readback mismatch at 0x{address:02x}')

    def configure(self, width, height, rois, *, invert=True, roi_enable=True):
        width, height, normalized = self.validate_config(width, height, rois)
        if type(invert) is not bool or type(roi_enable) is not bool:
            raise ValueError('invert/roi_enable must be bool')
        self._require_idle_safe()
        self._configured = self._last_frame_complete = False
        try:
            self._write_config(1, 0)
            self._write_config(0, width | height << 16)
            for index, roi in enumerate(normalized):
                for field, value in enumerate(roi['box']):
                    self._write_config(16 + index * 4 + field, value)
            mask = sum((1 << i for i, r in enumerate(normalized) if r['enabled']))
            self._write_config(1, mask)
            self._geometry = (height, width)
            self._rois = normalized
            self._ctrl_shadow = self._ctrl_shadow & ~(self.INVERT | self.ROI_ENABLE) | (self.INVERT if invert else 0) | (self.ROI_ENABLE if roi_enable else 0)
            self._gpio.write(self.GPIO_CONTROL, self._ctrl_shadow)
            self._configured = True
        except BaseException:
            self._configured = False
            raise

    def _write_control(self, threshold, bypass):
        self._ctrl_shadow = self._ctrl_shadow & ~1023 | int(threshold) | int(bool(bypass)) << 8
        self._gpio.write(self.GPIO_CONTROL, self._ctrl_shadow)

    def recover(self):
        self._last_frame_complete = False
        return super().recover()

    def transfer(self, image, *, threshold=120, bypass=False):
        import numpy as np
        self._ensure_healthy()
        if not self._configured:
            raise ConfigurationError('Configure geometry/ROIs before transfer')
        pixels = np.asarray(image)
        if pixels.dtype != np.uint8 or pixels.ndim not in (1, 2) or pixels.size != self._geometry[0] * self._geometry[1]:
            raise ValueError('Input must be uint8 and match the configured pixel count')
        if pixels.ndim == 2 and pixels.shape != self._geometry:
            raise ValueError('Two-dimensional input must match configured (height,width)')
        status = self.read_status()
        if status['cfg_error'] or status['frame_error']:
            raise ConfigurationError('Sticky hardware error; recover before transfer')
        self._last_frame_complete = False
        flags = self._ctrl_shadow
        output = super().transfer(pixels, threshold=threshold, bypass=bypass)
        self.last_transfer.update(invert=bool(flags & self.INVERT), roi_enable=bool(flags & self.ROI_ENABLE))
        raw = self.last_transfer['algorithm_status']
        if raw & 24:
            raise ROIValidationError(f'Hardware cfg/frame error in completed packet: 0x{raw:08x}', output)
        self._last_frame_complete = True
        return output

    def read_results(self):
        self._require_idle_safe()
        if not self._configured or not self._last_frame_complete:
            raise ConfigurationError('No completed frame for the current configuration')
        before = self.read_status()
        white = self._read_selected(2)
        pixels = self._read_selected(3)
        by_index = [self._read_selected(128 + i) for i in range(self.ROI_COUNT)]
        after = self.read_status()
        if after['completed_packets'] != before['completed_packets'] or any((after[name] for name in ('busy', 'inpacket', 'cfg_error', 'frame_error'))):
            raise ROIValidationError('Statistics changed while reading; driver must be exclusive')
        expected_pixels = self._geometry[0] * self._geometry[1]
        if pixels != expected_pixels or white > expected_pixels:
            raise ROIValidationError('Unexpected committed pixel count/statistics')
        counts = {}
        for i, value in enumerate(by_index):
            if i >= len(self._rois) or not self._rois[i]['enabled']:
                if value:
                    raise ROIValidationError('Disabled ROI has nonzero count')
            else:
                roi = self._rois[i]
                x0, y0, x1, y1 = roi['box']
                if value > (x1 - x0) * (y1 - y0):
                    raise ROIValidationError('ROI foreground count exceeds its area')
                counts[roi['name']] = value
        return {'output_white_pixels': white, 'valid_pixels': pixels, 'roi_counts': counts, 'roi_counts_by_index': by_index, 'status': after}
