import copy
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np
from transport import ABIError, BusyError, DMAHardwareError, DMATimeout, MetadataError, PoisonedError, ThresholdDriver, TransferValidationError

class FakeClock:

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if seconds <= 0:
            raise AssertionError('Polling must advance a positive, bounded time interval')
        self.now += seconds

class FakeBuffer(np.ndarray):

    def __new__(cls, runtime, nbytes, address):
        obj = np.zeros(nbytes, dtype=np.uint8).view(cls)
        obj.runtime = runtime
        obj.device_address = address
        obj.freed = False
        return obj

    def flush(self):
        self.runtime.events.append(('flush', self.device_address))

    def invalidate(self):
        self.runtime.events.append(('invalidate', self.device_address))

    def freebuffer(self):
        if not self.runtime.dma.safe_to_free():
            raise AssertionError('Buffer freed before reset completion and both Halted flags')
        if self.freed:
            raise AssertionError('Double free')
        self.freed = True
        self.runtime.events.append(('free', self.device_address))

class FakeGPIO:

    def __init__(self, runtime):
        self.runtime = runtime
        self.control = 384
        self.abi = 2
        self.count = 0
        self.flags = 0

    def read(self, offset):
        self.runtime.events.append(('gpio_read', offset))
        if offset != 8:
            raise AssertionError('Driver must not read the output control channel')
        return self.count << 16 | self.abi << 8 | self.flags

    def write(self, offset, value):
        self.runtime.events.append(('gpio_write', offset, value))
        if offset != 0 or not 0 <= value <= 1023:
            raise AssertionError('Control must use complete 32-bit writes with reserved bits clear')
        self.control = value
        if value & 512:
            self.count = 0
            self.flags = 0

class FakeDMA:

    def __init__(self, runtime):
        self.runtime = runtime
        self.regs = {0: 0, 4: 1, 48: 0, 52: 1}
        self.complete = True
        self.error_status = 0
        self.length_delta = 0
        self.packet_delta = 1
        self.finish_busy = False
        self.stuck_after_launch = False
        self.reset_never_completes = False
        self.interrupt_on_launch = False
        self.launched = False
        self.reset_count = 0

    def safe_to_free(self):
        return not (self.regs.get(0, 0) | self.regs.get(48, 0)) & 4 and bool(self.regs.get(4, 0) & 1) and bool(self.regs.get(52, 0) & 1)

    def read(self, offset):
        self.runtime.events.append(('dma_read', offset))
        return self.regs.get(offset, 0)

    def write(self, offset, value):
        self.runtime.events.append(('dma_write', offset, value))
        if offset in (0, 48) and value & 4:
            self.reset_count += 1
            if self.reset_never_completes or (self.stuck_after_launch and self.launched):
                self.regs.update({0: 4, 48: 4, 4: 0, 52: 0})
            else:
                self.regs = {0: 0, 4: 1, 48: 0, 52: 1}
            return
        if offset in (4, 52):
            self.regs[offset] = self.regs.get(offset, 0) & ~value
            return
        self.regs[offset] = value
        if offset in (0, 48):
            self.regs[offset + 4] = 0 if value & 1 else 1
        if offset == 40:
            self.launched = True
            self.runtime.gpio.flags = 3
            if self.interrupt_on_launch:
                raise KeyboardInterrupt('simulated user interruption')
            if self.error_status:
                self.regs[4] = self.error_status
                return
            if not self.complete:
                return
            nbytes = value
            destination = self.runtime.buffers_by_address[self.regs[72]]
            source = self.runtime.buffers_by_address[self.regs[24]]
            control = self.runtime.gpio.control
            if control & 256:
                destination[:] = source
            else:
                destination[:] = np.where(source > control & 255, 255, 0).astype(np.uint8)
            self.regs[4] = self.regs[52] = 4098
            self.regs[88] = nbytes + self.length_delta
            self.runtime.gpio.count = self.runtime.gpio.count + self.packet_delta & 65535
            self.runtime.gpio.flags = 1 if self.finish_busy else 0

class Runtime:

    def __init__(self):
        self.events = []
        self.clock = FakeClock()
        self.gpio = FakeGPIO(self)
        self.dma = FakeDMA(self)
        self.buffers = []
        self.buffers_by_address = {}
        self.next_addresses = None

    def allocate(self, *, shape, dtype):
        if dtype != np.uint8:
            raise AssertionError('Expected uint8 contiguous buffers')
        index = len(self.buffers)
        address = self.next_addresses[index] if self.next_addresses is not None else 1048576 + index * 16777216
        buffer = FakeBuffer(self, shape[0], address)
        self.buffers.append(buffer)
        self.buffers_by_address[address] = buffer
        self.events.append(('allocate', address))
        return buffer

    def driver(self):
        return ThresholdDriver(self.dma, self.gpio, self.allocate, timeout=0.05, poll_interval=0.01, clock=self.clock.monotonic, sleep=self.clock.sleep)

def valid_ip_dict():
    dma_parameters = {'c_include_sg': '0', 'c_micro_dma': '0', 'c_prmry_is_aclk_async': '0', 'c_sg_include_stscntrl_strm': '0', 'c_include_mm2s': '1', 'c_include_s2mm': '1', 'c_include_mm2s_dre': '0', 'c_include_s2mm_dre': '0', 'c_addr_width': '32', 'c_sg_length_width': '23', 'c_m_axi_mm2s_data_width': '64', 'c_m_axi_s2mm_data_width': '64', 'c_m_axis_mm2s_tdata_width': '32', 'c_s_axis_s2mm_tdata_width': '32'}
    gpio_parameters = {'C_IS_DUAL': '1', 'C_GPIO_WIDTH': '32', 'C_ALL_INPUTS': '0', 'C_ALL_OUTPUTS': '1', 'C_DOUT_DEFAULT': '0x00000180', 'C_GPIO2_WIDTH': '32', 'C_ALL_INPUTS_2': '1', 'C_ALL_OUTPUTS_2': '0', 'C_INTERRUPT_PRESENT': '0'}
    return {'axi_dma_0': {'type': 'xilinx.com:ip:axi_dma:7.1', 'parameters': dma_parameters, 'phys_addr': 1077936128, 'addr_range': 65536}, 'axi_gpio_ctrl': {'type': 'xilinx.com:ip:axi_gpio:2.0', 'parameters': gpio_parameters, 'phys_addr': 1092616192, 'addr_range': 65536}}

class TransferTests(unittest.TestCase):

    def setUp(self):
        self.runtime = Runtime()
        self.driver = self.runtime.driver()
        self.image = np.array([0, 127, 128, 129, 255, 1, 2, 3] * 4, dtype=np.uint8)

    def test_success_cache_order_and_independent_result(self):
        result = self.driver.transfer(self.image)
        np.testing.assert_array_equal(result, np.where(self.image > 128, 255, 0).astype(np.uint8))
        self.assertTrue(all((buffer.freed for buffer in self.runtime.buffers)))
        self.assertEqual(self.driver.last_transfer['received_bytes'], 32)
        self.assertEqual(self.driver.last_transfer['packet_delta'], 1)
        self.assertEqual(self.driver.last_transfer['stage'], 'complete')
        events = self.runtime.events
        launch = events.index(('dma_write', 40, 32))
        self.assertLess(events.index(('dma_write', 88, 32)), launch)
        self.assertTrue(all((events.index(('flush', buffer.device_address)) < launch for buffer in self.runtime.buffers)))
        invalidate = events.index(('invalidate', self.runtime.buffers[1].device_address))
        self.assertGreater(invalidate, launch)
        self.assertLess(invalidate, events.index(('free', self.runtime.buffers[1].device_address)))
        self.assertEqual(self.runtime.dma.reset_count, 2)
        self.runtime.buffers[1][:] = 7
        self.assertFalse(np.all(result == 7))

    def test_bypass_shape_and_noncontiguous_input(self):
        image = np.arange(64, dtype=np.uint8).reshape(8, 8)[:, ::2]
        self.assertFalse(image.flags.c_contiguous)
        result = self.driver.transfer(image, threshold=255, bypass=True)
        np.testing.assert_array_equal(result, image)
        self.assertEqual(result.shape, (8, 4))

    def test_count_wrap_and_normal_transfer_preserves_pl_count(self):
        self.runtime.gpio.count = 65535
        self.driver.transfer(self.image, bypass=True)
        self.assertEqual(self.driver.last_transfer['packets_before'], 65535)
        self.assertEqual(self.driver.last_transfer['packets_after'], 0)
        self.driver.transfer(self.image, bypass=True)
        self.assertEqual(self.runtime.gpio.count, 1)
        self.assertFalse(any((event[0] == 'gpio_write' and event[2] & 512 for event in self.runtime.events)))

    def test_explicit_recover_clears_pl_and_holds_reset_around_dma(self):
        self.runtime.gpio.count = 9
        self.driver.recover()
        self.assertEqual(self.runtime.gpio.count, 0)
        self.assertEqual(self.runtime.gpio.control, 384)
        events = self.runtime.events
        self.assertLess(events.index(('gpio_write', 0, 896)), events.index(('dma_write', 0, 4)))
        self.assertGreater(events.index(('gpio_write', 0, 384)), events.index(('dma_write', 0, 4)))

    def test_timeout_recovers_then_can_transfer_again(self):
        self.runtime.dma.complete = False
        with self.assertRaises(DMATimeout):
            self.driver.transfer(self.image)
        self.assertFalse(self.driver.poisoned)
        self.assertEqual(self.driver.retained_buffer_count, 0)
        self.assertTrue(all((buffer.freed for buffer in self.runtime.buffers)))
        self.assertEqual(self.runtime.gpio.flags, 0)
        self.assertLessEqual(self.runtime.clock.now, 0.11)
        self.runtime.dma.complete = True
        np.testing.assert_array_equal(self.driver.transfer(self.image, bypass=True), self.image)

    def test_unstoppable_dma_is_poisoned_and_never_freed(self):
        self.runtime.dma.complete = False
        self.runtime.dma.stuck_after_launch = True
        with self.assertRaises(PoisonedError):
            self.driver.transfer(self.image)
        self.assertTrue(self.driver.poisoned)
        self.assertEqual(self.driver.retained_buffer_count, 2)
        self.assertFalse(any((buffer.freed for buffer in self.runtime.buffers)))
        self.assertTrue(self.runtime.gpio.control & 512)
        self.assertIn('Timed out', self.driver.poison_reason)
        event_count = len(self.runtime.events)
        with self.assertRaises(PoisonedError):
            self.driver.transfer(self.image)
        with self.assertRaises(PoisonedError):
            self.driver.recover()
        with self.assertRaises(PoisonedError):
            self.driver.read_status()
        self.assertEqual(len(self.runtime.events), event_count)

    def test_keyboard_interrupt_runs_cleanup(self):
        self.runtime.dma.interrupt_on_launch = True
        with self.assertRaises(KeyboardInterrupt):
            self.driver.transfer(self.image)
        self.assertFalse(self.driver.poisoned)
        self.assertTrue(all((buffer.freed for buffer in self.runtime.buffers)))
        self.assertEqual(self.runtime.gpio.control & 512, 0)

    def test_interrupt_with_failed_reset_retains_memory(self):
        self.runtime.dma.interrupt_on_launch = True
        self.runtime.dma.stuck_after_launch = True
        with self.assertRaises(PoisonedError):
            self.driver.transfer(self.image)
        self.assertEqual(self.driver.retained_buffer_count, 2)
        self.assertFalse(any((buffer.freed for buffer in self.runtime.buffers)))

    def test_dma_error_retains_error_snapshot_but_cleans_safely(self):
        self.runtime.dma.error_status = 32
        with self.assertRaises(DMAHardwareError):
            self.driver.transfer(self.image)
        self.assertEqual(self.driver.last_transfer['tx_status'], 32)
        self.assertEqual(self.driver.last_transfer['reset_tx_status'], 1)
        self.assertTrue(all((buffer.freed for buffer in self.runtime.buffers)))

    def test_receive_length_mismatch_is_rejected(self):
        self.runtime.dma.length_delta = -4
        with self.assertRaises(TransferValidationError):
            self.driver.transfer(self.image)
        self.assertEqual(self.driver.last_transfer['received_bytes'], 28)
        self.assertTrue(all((buffer.freed for buffer in self.runtime.buffers)))
        self.assertEqual(self.runtime.gpio.count, 0)

    def test_zero_or_two_packets_and_busy_completion_are_rejected(self):
        for packet_delta, busy in ((0, False), (2, False), (1, True)):
            with self.subTest(packet_delta=packet_delta, busy=busy):
                runtime = Runtime()
                runtime.dma.packet_delta, runtime.dma.finish_busy = (packet_delta, busy)
                with self.assertRaises(TransferValidationError):
                    runtime.driver().transfer(self.image)
                self.assertTrue(all((buffer.freed for buffer in runtime.buffers)))

    def test_bad_abi_and_busy_are_rejected_before_dma_or_allocation(self):
        self.runtime.gpio.abi = 3
        with self.assertRaises(ABIError):
            self.driver.transfer(self.image)
        self.runtime.gpio.abi = 2
        self.runtime.gpio.flags = 2
        with self.assertRaises(BusyError):
            self.driver.transfer(self.image)
        self.assertEqual(self.runtime.buffers, [])
        self.assertFalse(any((event[0] == 'dma_write' for event in self.runtime.events)))

    def test_bad_addresses_are_freed_only_while_dma_is_halted(self):
        for addresses in ([1048580, 2097152], [1048576, 1 << 32], [1048576, 1048576], [1048576, -64]):
            with self.subTest(addresses=addresses):
                runtime = Runtime()
                runtime.next_addresses = addresses
                driver = runtime.driver()
                with self.assertRaises(TransferValidationError):
                    driver.transfer(self.image)
                self.assertTrue(all((buffer.freed for buffer in runtime.buffers)))
                self.assertFalse(driver.poisoned)

    def test_payload_and_threshold_validation(self):
        for image in (np.array([], dtype=np.uint8), np.zeros(7, dtype=np.uint8), np.zeros(32, dtype=np.float32), np.zeros((2, 2, 8), dtype=np.uint8)):
            with self.subTest(shape=image.shape, dtype=image.dtype), self.assertRaises(ValueError):
                self.driver.transfer(image)
        for threshold in (-1, 256, 128.0, True):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                self.driver.transfer(self.image, threshold=threshold)
        self.assertEqual(self.runtime.buffers, [])

class FactoryTests(unittest.TestCase):

    def setUp(self):
        self.runtime = Runtime()
        self.overlay_events = []
        self.descriptions = valid_ip_dict()
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.bit = Path(self.directory.name) / 'pcb_threshold.bit'
        self.bit.write_bytes(b'mock-bit-not-a-real-FPGA-file')
        self.bit.with_suffix('.hwh').write_text('mock metadata supplied by FakeOverlay', encoding='utf-8')

    def pynq_module(self):
        parent = self

        class FakeOverlay:

            def __init__(self, bitfile, download):
                parent.overlay_events.append(('parse', bitfile, download))
                self.ip_dict = copy.deepcopy(parent.descriptions)

            def download(self):
                parent.overlay_events.append(('download',))

        def mmio(base, size):
            parent.overlay_events.append(('mmio', base, size))
            return parent.runtime.dma if base == 1077936128 else parent.runtime.gpio
        module = types.ModuleType('pynq')
        module.Overlay, module.MMIO, module.allocate = (FakeOverlay, mmio, self.runtime.allocate)
        return module

    def test_factory_validates_before_download_and_retains_overlay(self):
        with patch.dict(sys.modules, {'pynq': self.pynq_module()}):
            driver = ThresholdDriver.from_bitfile(self.bit)
        self.assertEqual(self.overlay_events[0], ('parse', str(self.bit.resolve()), False))
        self.assertEqual(self.overlay_events[1], ('download',))
        self.assertIsNotNone(driver._overlay)
        self.assertFalse(any((event[0] == 'gpio_write' for event in self.runtime.events)))

    def test_factory_requires_matching_hwh_before_pynq_import(self):
        self.bit.with_suffix('.hwh').unlink()
        with self.assertRaises(MetadataError):
            ThresholdDriver.from_bitfile(self.bit)
        self.assertEqual(self.overlay_events, [])

    def test_factory_rejects_wrong_mode_width_direction_or_default_before_download(self):
        bad_parameters = [('axi_dma_0', 'c_include_sg', '1'), ('axi_dma_0', 'c_m_axis_mm2s_tdata_width', '64'), ('axi_dma_0', 'c_sg_length_width', '14'), ('axi_gpio_ctrl', 'C_ALL_INPUTS_2', '0'), ('axi_gpio_ctrl', 'C_DOUT_DEFAULT', '0x80')]
        for ip, parameter, bad in bad_parameters:
            with self.subTest(ip=ip, parameter=parameter):
                self.descriptions = valid_ip_dict()
                self.descriptions[ip]['parameters'][parameter] = bad
                self.overlay_events.clear()
                with patch.dict(sys.modules, {'pynq': self.pynq_module()}):
                    with self.assertRaises(MetadataError):
                        ThresholdDriver.from_bitfile(self.bit)
                self.assertFalse(any((event[0] == 'download' for event in self.overlay_events)))

    def test_factory_rejects_missing_ip_and_invalid_mapping(self):
        for kind in ('missing_ip', 'wrong_type', 'bad_mapping'):
            with self.subTest(kind=kind):
                self.descriptions = valid_ip_dict()
                if kind == 'missing_ip':
                    del self.descriptions['axi_gpio_ctrl']
                elif kind == 'wrong_type':
                    self.descriptions['axi_gpio_ctrl']['type'] = 'xilinx.com:ip:axi_gpio:1.0'
                else:
                    self.descriptions['axi_dma_0']['phys_addr'] = 1 << 32
                self.overlay_events.clear()
                with patch.dict(sys.modules, {'pynq': self.pynq_module()}):
                    with self.assertRaises(MetadataError):
                        ThresholdDriver.from_bitfile(self.bit)
                self.assertFalse(any((event[0] == 'download' for event in self.overlay_events)))
if __name__ == '__main__':
    unittest.main(verbosity=2)
