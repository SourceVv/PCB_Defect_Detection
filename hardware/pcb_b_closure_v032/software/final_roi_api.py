from pathlib import Path
import hashlib
import importlib.util
import sys
SOURCE_SHA256 = '579bd7213c003dc835c88bd1407a2881582f11a1ecdf4f5c2fcb92ad5a20d867'
SELECTED_MASK_VALIDATION = 'bytes_count'
_MODULE_NAME = 'pcb_b_final_roi_service_v032'
_SOURCE = Path(__file__).resolve().with_name('aoi_roi_service.py')

def _service_class():
    module = sys.modules.get(_MODULE_NAME)
    if module is None:
        if hashlib.sha256(_SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
            raise RuntimeError('v032 service source differs from the board-tested release')
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SOURCE)
        if spec is None or spec.loader is None:
            raise RuntimeError('Cannot load the board-tested v032 service')
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(_MODULE_NAME, None)
            raise
    elif Path(module.__file__).resolve() != _SOURCE:
        raise RuntimeError('Version-specific module name already belongs to another source')
    return module.AoiRoiService

def create_service(driver, **configuration):
    if 'mask_validation' in configuration:
        raise ValueError('Final entry fixes mask_validation=bytes_count')
    return _service_class()(driver, mask_validation=SELECTED_MASK_VALIDATION, **configuration)
