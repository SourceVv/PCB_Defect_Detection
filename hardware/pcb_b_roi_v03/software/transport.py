from __future__ import annotations
from numbers import Integral
from pathlib import Path
import time

class DriverError(RuntimeError):
    pass

class MetadataError(DriverError):
    pass

class ABIError(DriverError):
    pass

class BusyError(DriverError):
    pass

class DMATimeout(DriverError):
    pass

class DMAHardwareError(DriverError):
    pass

class TransferValidationError(DriverError):
    pass

class PoisonedError(DriverError):
    pass
_UNSAFE_BUFFER_QUARANTINE = []

def _integer(value):
    if isinstance(value, Integral):
        return int(value)
    try:
        return int(str(value), 0)
    except ValueError:
        return int(str(value), 10)

def _validate_description(ip_dict):
    required = {'axi_dma_0': ('xilinx.com:ip:axi_dma:7.1', {'c_include_sg': 0, 'c_micro_dma': 0, 'c_prmry_is_aclk_async': 0, 'c_sg_include_stscntrl_strm': 0, 'c_include_mm2s': 1, 'c_include_s2mm': 1, 'c_include_mm2s_dre': 0, 'c_include_s2mm_dre': 0, 'c_addr_width': 32, 'c_sg_length_width': 23, 'c_m_axi_mm2s_data_width': 64, 'c_m_axi_s2mm_data_width': 64, 'c_m_axis_mm2s_tdata_width': 32, 'c_s_axis_s2mm_tdata_width': 32}), 'axi_gpio_ctrl': ('xilinx.com:ip:axi_gpio:2.0', {'c_is_dual': 1, 'c_gpio_width': 32, 'c_all_inputs': 0, 'c_all_outputs': 1, 'c_dout_default': 384, 'c_gpio2_width': 32, 'c_all_inputs_2': 1, 'c_all_outputs_2': 0, 'c_interrupt_present': 0})}
    descriptions = {}
    for name, (ip_type, expected) in required.items():
        description = ip_dict.get(name)
        if not isinstance(description, dict) or description.get('type') != ip_type:
            raise MetadataError(f'Missing or wrong IP type: {name}; expected {ip_type}')
        parameters = {str(key).lower(): value for key, value in description.get('parameters', {}).items()}
        for parameter, wanted in expected.items():
            try:
                actual = _integer(parameters[parameter])
            except (KeyError, TypeError, ValueError) as exc:
                raise MetadataError(f'Missing/invalid HWH parameter: {name}.{parameter}') from exc
            if actual != wanted:
                raise MetadataError(f'Unexpected {name}.{parameter}: {actual}, expected {wanted}')
        try:
            base = _integer(description['phys_addr'])
            size = _integer(description['addr_range'])
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError(f'Missing/invalid MMIO mapping: {name}') from exc
        if base < 0 or base % 4 or size < 4096 or (base + size > 1 << 32):
            raise MetadataError(f'Unsupported MMIO range: {name}: base={base}, range={size}')
        descriptions[name] = (base, size)
    return descriptions

class ThresholdDriver:
    TX_CR, TX_SR, TX_ADDR, TX_LEN = (0, 4, 24, 40)
    RX_CR, RX_SR, RX_ADDR, RX_LEN = (48, 52, 72, 88)
    GPIO_CONTROL, GPIO_STATUS = (0, 8)
    RESET, HALTED, IDLE, IOC, DMA_ERRORS = (4, 1, 2, 4096, 112)
    MAX_BYTES = (1 << 23) - 1

    def __init__(self, dma_mmio, gpio_mmio, allocator, *, timeout=2.0, poll_interval=0.001, clock=None, sleep=None):
        if not isinstance(timeout, (int, float)) or not 0 < timeout < float('inf'):
            raise ValueError('timeout must be finite and positive')
        if not isinstance(poll_interval, (int, float)) or not 0 < poll_interval <= timeout:
            raise ValueError('poll_interval must be positive and no larger than timeout')
        self._dma = dma_mmio
        self._gpio = gpio_mmio
        self._allocate = allocator
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._ctrl_shadow = 384
        self._buffers = []
        self._retained_buffers = []
        self._dma_safe = False
        self._active = False
        self.poisoned = False
        self.poison_reason = ''
        self.last_transfer = {}
        self._overlay = None
        self.read_status()

    @classmethod
    def from_bitfile(cls, bitfile, *, timeout=2.0, poll_interval=0.001):
        bit_path = Path(bitfile).expanduser().resolve()
        if bit_path.suffix.lower() != '.bit' or not bit_path.is_file():
            raise MetadataError(f'Missing .bit file: {bit_path}')
        hwh_path = bit_path.with_suffix('.hwh')
        if not hwh_path.is_file():
            raise MetadataError(f'Missing same-name .hwh file: {hwh_path}')
        from pynq import Overlay, MMIO, allocate
        overlay = Overlay(str(bit_path), download=False)
        descriptions = _validate_description(overlay.ip_dict)
        overlay.download()
        driver = cls(MMIO(*descriptions['axi_dma_0']), MMIO(*descriptions['axi_gpio_ctrl']), allocate, timeout=timeout, poll_interval=poll_interval)
        driver._overlay = overlay
        return driver

    @property
    def retained_buffer_count(self):
        return len(self._retained_buffers)

    def _ensure_healthy(self):
        if self.poisoned:
            raise PoisonedError(f'Driver is poisoned; retained buffers must remain alive: {self.poison_reason}')

    def read_status(self):
        self._ensure_healthy()
        raw = int(self._gpio.read(self.GPIO_STATUS)) & 4294967295
        status = {'raw': raw, 'abi': raw >> 8 & 255, 'busy': bool(raw & 1), 'inpacket': bool(raw & 2), 'completed_packets': raw >> 16 & 65535}
        if status['abi'] != 2:
            raise ABIError(f'GPIO status ABI mismatch: raw=0x{raw:08X}, expected ABI=0x02')
        return status

    def _poll(self, predicate, deadline, operation):
        while True:
            value = predicate()
            if value:
                return value
            now = self._clock()
            if now >= deadline:
                raise DMATimeout(f'Timed out waiting for {operation}')
            self._sleep(min(self.poll_interval, deadline - now))

    def _reset_dma(self, deadline):
        self._dma_safe = False
        self._dma.write(self.TX_CR, self.RESET)

        def stopped():
            tx_cr, rx_cr = (int(self._dma.read(self.TX_CR)), int(self._dma.read(self.RX_CR)))
            tx_sr, rx_sr = (int(self._dma.read(self.TX_SR)), int(self._dma.read(self.RX_SR)))
            self.last_transfer.update(reset_tx_status=tx_sr, reset_rx_status=rx_sr)
            return not (tx_cr | rx_cr) & self.RESET and bool(tx_sr & self.HALTED) and bool(rx_sr & self.HALTED)
        self._poll(stopped, deadline, 'DMA reset completion and both Halted flags')
        self._dma_safe = True

    def _write_control(self, threshold, bypass):
        self._ctrl_shadow = int(threshold) | int(bool(bypass)) << 8
        self._gpio.write(self.GPIO_CONTROL, self._ctrl_shadow)

    def _wait_algorithm_clear(self, deadline):

        def cleared():
            status = self.read_status()
            return not (status['busy'] or status['inpacket'] or status['completed_packets'])
        self._poll(cleared, deadline, 'algorithm reset and idle status')

    def _release_safe_buffers(self):
        if self._buffers and (not self._dma_safe):
            raise DriverError('Refusing to free buffers before DMA reset and both Halted flags')
        while self._buffers:
            buffer = self._buffers[0]
            buffer.freebuffer()
            self._buffers.pop(0)

    def _poison(self, reason):
        self.poisoned = True
        self.poison_reason = str(reason)
        self._retained_buffers.extend(self._buffers)
        _UNSAFE_BUFFER_QUARANTINE.extend(self._buffers)
        self._buffers = []
        self.last_transfer['recovery_error'] = self.poison_reason

    def _recover_internal(self):
        deadline = self._clock() + self.timeout
        self._gpio.write(self.GPIO_CONTROL, self._ctrl_shadow | 512)
        self._reset_dma(deadline)
        self._sleep(self.poll_interval)
        self._gpio.write(self.GPIO_CONTROL, self._ctrl_shadow)
        self._wait_algorithm_clear(deadline)
        self._release_safe_buffers()

    def recover(self):
        self._ensure_healthy()
        if self._active:
            raise BusyError('A transfer is already active')
        try:
            self._recover_internal()
        except BaseException as exc:
            self._poison(exc)
            raise PoisonedError('Recovery failed; keep this driver/kernel alive and stop hardware use') from exc

    def _check_buffer(self, buffer, nbytes):
        address = getattr(buffer, 'device_address', None)
        if address is None:
            address = getattr(buffer, 'physical_address', None)
        if not isinstance(address, Integral):
            raise TransferValidationError('Allocator did not provide a physical device address')
        address = int(address)
        if address < 0 or address % 64 or address + nbytes > 1 << 32:
            raise TransferValidationError(f'Buffer must be 64-byte aligned and inside the 32-bit address range: {address}')
        if int(buffer.nbytes) < nbytes:
            raise TransferValidationError('Allocator returned an undersized buffer')
        return address

    def transfer(self, image, *, threshold=128, bypass=False):
        import numpy as np
        self._ensure_healthy()
        if self._active:
            raise BusyError('A transfer is already active')
        if not isinstance(threshold, Integral) or isinstance(threshold, bool) or (not 0 <= threshold <= 255):
            raise ValueError('threshold must be an integer from 0 to 255')
        if bypass not in (False, True):
            raise ValueError('bypass must be False or True')
        pixels = np.asarray(image)
        if pixels.dtype != np.uint8 or pixels.ndim not in (1, 2):
            raise ValueError('image must be a one- or two-dimensional uint8 array')
        nbytes = int(pixels.nbytes)
        if nbytes <= 0 or nbytes > self.MAX_BYTES or nbytes % 4:
            raise ValueError('image length must be a non-zero multiple of 4 within the DMA length limit')
        status_before = self.read_status()
        if status_before['busy'] or status_before['inpacket']:
            raise BusyError('Algorithm is busy or inside a packet; use recover after diagnosing the previous operation')
        started = self._clock()
        deadline = started + self.timeout
        self._active = True
        self.last_transfer = {'stage': 'reset_before', 'nbytes': nbytes, 'threshold': int(threshold), 'bypass': bool(bypass), 'packets_before': status_before['completed_packets']}
        try:
            self._reset_dma(deadline)
            self.last_transfer['stage'] = 'allocate'
            source = self._allocate(shape=(nbytes,), dtype=np.uint8)
            self._buffers.append(source)
            destination = self._allocate(shape=(nbytes,), dtype=np.uint8)
            self._buffers.append(destination)
            source_address = self._check_buffer(source, nbytes)
            destination_address = self._check_buffer(destination, nbytes)
            if max(source_address, destination_address) < min(source_address + nbytes, destination_address + nbytes):
                raise TransferValidationError('Source and destination DMA buffers overlap')
            source[:] = np.ascontiguousarray(pixels).reshape(-1)
            source.flush()
            destination.flush()
            self._write_control(threshold, bypass)
            self.last_transfer['stage'] = 'start'
            self._dma.write(self.TX_SR, 28672)
            self._dma.write(self.RX_SR, 28672)
            self._dma_safe = False
            self._dma.write(self.RX_CR, 1)
            self._dma.write(self.TX_CR, 1)

            def running():
                return not (int(self._dma.read(self.TX_SR)) | int(self._dma.read(self.RX_SR))) & self.HALTED
            self._poll(running, deadline, 'both DMA channels running')
            self._dma.write(self.RX_ADDR, destination_address)
            self._dma.write(self.TX_ADDR, source_address)
            self._dma.write(self.RX_LEN, nbytes)
            self._dma.write(self.TX_LEN, nbytes)
            self.last_transfer['stage'] = 'wait_complete'

            def completed():
                tx_sr, rx_sr = (int(self._dma.read(self.TX_SR)), int(self._dma.read(self.RX_SR)))
                self.last_transfer.update(tx_status=tx_sr, rx_status=rx_sr)
                if (tx_sr | rx_sr) & self.DMA_ERRORS:
                    raise DMAHardwareError(f'DMA error: MM2S=0x{tx_sr:08X}, S2MM=0x{rx_sr:08X}')
                mask = self.IOC | self.IDLE
                return tx_sr & mask == mask and rx_sr & mask == mask
            self._poll(completed, deadline, 'both DMA completion events')
            received = int(self._dma.read(self.RX_LEN))
            status_after = self.read_status()
            packet_delta = status_after['completed_packets'] - status_before['completed_packets'] & 65535
            self.last_transfer.update(received_bytes=received, packets_after=status_after['completed_packets'], packet_delta=packet_delta, algorithm_status=status_after['raw'])
            if received != nbytes:
                raise TransferValidationError(f'Received {received} bytes, expected {nbytes}')
            if status_after['busy'] or status_after['inpacket'] or packet_delta != 1:
                raise TransferValidationError(f'Unexpected algorithm completion state: {status_after}; packet delta={packet_delta}')
            destination.invalidate()
            output = np.array(destination, dtype=np.uint8, copy=True).reshape(pixels.shape)
            completion_tx, completion_rx = (self.last_transfer['tx_status'], self.last_transfer['rx_status'])
            self.last_transfer['stage'] = 'reset_after'
            self._reset_dma(deadline)
            self._release_safe_buffers()
            self.last_transfer.update(stage='complete', tx_status=completion_tx, rx_status=completion_rx, elapsed_seconds=self._clock() - started)
            return output
        except BaseException as exc:
            self.last_transfer['error'] = f'{type(exc).__name__}: {exc}'
            try:
                if not self._dma_safe:
                    self._recover_internal()
                else:
                    self._release_safe_buffers()
            except BaseException as recovery_error:
                self._poison(recovery_error)
                raise PoisonedError('DMA recovery could not establish safe cleanup; buffers retained; stop using hardware') from exc
            raise
        finally:
            self._active = False
