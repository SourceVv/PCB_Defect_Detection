from uuid import uuid4
import cv2
import numpy as np
from .legacy import HardwareBindings, patch_for

class BoardThreshold:

    def __init__(self, root, driver=None, bindings=None):
        self.hw = HardwareBindings(root, driver, bindings)

    def process(self, image, rows):
        eligible = [(i, r) for i, r in enumerate(rows) if r['box'] is not None and r['enabled'] and r['reviewed'] and r['populated']]
        if not eligible:
            return ({}, None)
        height = (len(eligible) + 7) // 8 * 64
        mosaic = np.zeros((height, 512), np.uint8)
        for index, (_, row) in enumerate(eligible):
            gray = cv2.cvtColor(patch_for(image, row['box']), cv2.COLOR_BGR2GRAY)
            threshold = 115 if row['kind'] in ('U', 'IC', 'SOCKET') else 200
            gray = np.clip(gray.astype(np.float32) * 115 / threshold, 0, 255).astype(np.uint8)
            y, x = (index // 8 * 64, index % 8 * 64)
            mosaic[y:y + 64, x:x + 64] = gray
        result = self.threshold(mosaic, 115)
        mask = result['mask']
        masks = {}
        for index, (row_index, _) in enumerate(eligible):
            y, x = (index // 8 * 64, index % 8 * 64)
            masks[row_index] = mask[y:y + 64, x:x + 64]
        return (masks, dict(frame_id=result['frame_id'], mosaic=mosaic, mask=mask))

    def threshold(self, mosaic, threshold):
        height, width = mosaic.shape
        hw = self.hw
        if hw.fault:
            raise RuntimeError('FPGA会话已停止，请保留错误信息')
        try:
            if hw.driver is None:
                hw.driver = hw.driver_factory(str(hw.bit), timeout=10.0)
                hw.driver.recover()
            state = hw.driver.read_status()
            if state.get('abi') != 3 or any((state.get(k) for k in ('busy', 'inpacket', 'cfg_error', 'frame_error'))):
                raise RuntimeError('FPGA状态异常：' + str(state))
            service = hw.service_factory(hw.driver, width=width, height=height, rois=[dict(name='all_components', box=[0, 0, width, height], enabled=True)], threshold=threshold, invert=False, roi_enable=True)
            service.configure()
            result = service.process(mosaic, frame_id='all_' + uuid4().hex)
            mask = result['mask']
            if mask.shape != mosaic.shape or mask.dtype != np.uint8 or (not np.isin(mask, [0, 255]).all()):
                raise RuntimeError('FPGA返回图像格式无效')
        except BaseException as exc:
            hw.fault = str(exc)
            raise
        return result
