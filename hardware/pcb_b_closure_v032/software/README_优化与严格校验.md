# v0.3.2 服务：先测量，再选择等价的严格校验

本目录复制并扩展 v0.3.1 服务。旧服务、冻结 ABI3 硬件与驱动均不修改。目标是减少 Python/NumPy 检查及重复输出复制的成本；没有跳过验证的“性能模式”，也没有修改分类阈值来掩盖原 A 的误报。

## A/C 调用方式

类名和主要参数保持一致：

```python
from aoi_roi_service import AoiRoiService

# driver 仍由上层创建、显式 recover，并保证单调用者独占。
service = AoiRoiService(
    driver, width=2533, height=1900, rois=rois,
    threshold=120, invert=True, roi_enable=True,
    mask_validation="compare",  # 默认原版；板端实测后可静态选择其他严格候选
)
service.configure()
result = service.process(gray, frame_id="capture-000001")
```

图像与 ROI 必须使用同一坐标系，本服务不 warp 图像。旧 `APipelineBridge` 不需要修改，可把工厂注入为 `functools.partial(AoiRoiService, driver, mask_validation="候选名")`。映射框每帧改变时由桥接层新建服务、配置，再处理；固定框的性能/稳定性验证则同一服务配置一次后连续处理。

模块仍可在普通电脑导入，不提前导入 PYNQ。接口继续拒绝错误 dtype/尺寸/阈值、重复帧 ID、并发调用；异常后锁定 faulted，不自动重试或 recover。对未知注入驱动的测试替身，需提供与真实驱动一致的本地安全状态字段。

## 五个候选都证明完全相同的条件

每个公开 helper 接收 uint8 NumPy 数组；发现任何1..254的像素就抛 `ResultValidationError`，成功返回 Python `int` 白点数。`MASK_VALIDATORS` 是只读映射，不允许加入关闭校验的选项。候选选择在服务构造时固定。

| 名称 | 严格二值证明和白点计算 |
| --- | --- |
| `compare` | 原版：所有值都等于0或255，再 `count_nonzero` |
| `sum` | uint64总和必须等于 `255*count_nonzero`；因为每个非零 uint8 最大255，等号意味着每个非零都恰好255 |
| `addwrap` | uint8加1回绕后只允许0或1，再 `count_nonzero(mask)` |
| `addreduce` | uint8加1回绕后的最大值不得超过1，白点数为 `size-sum(wrapped,dtype=uint64)` |
| `bytes_count` | C顺序字节快照中，0字节数与255字节数之和必须恰好等于像素数；255字节数就是白点数 |

`addwrap/addreduce` 的依据：0加1为1，255加1回绕为0，1..254加1则为2..255。因此它们同样能发现128、134、254等中间值，不是只检查最大/最小值的近似方法。`sum` 使用 uint64，防止中间求和采用过窄类型。

不同 ARM/NumPy 版本的逐像素循环、临时数组和整数求和成本不同。普通电脑的快慢不能推断 PYNQ-Z2 的快慢；板端微测应计入“二值证明+白点计算”的完整 helper 调用，并核对返回白点数。默认仍为 `compare`，最终性能候选以同板测量记录及显式构造参数为准。

所有候选返回的白点数都复用来核对硬件 `output_white_pixels`，不再在 helper 之后重复统计一次。

## 输出所有权如何保证

冻结 `RoiDriver.transfer` 已在内部执行 `np.array(destination,copy=True)`，等 DMA 安全停止并释放 CMA 后才返回。因此它给出的像素在独立 NumPy 内存中。随后 `.reshape` 常返回非 owning view；其 `base` 才是拥有全部独立像素的数组。

v032 只在满足完整证明时转交这个拥有者：

1. driver 必须是精确的冻结 `RoiDriver` 类型，不能是子类或未知替身。构造服务时核对 `roi_driver.py` 与 `transport.py` 的固定 SHA256，以及真实加载方法和对应源码编译得到的 code object。
2. 帧级重新核对方法对象身份，防止构造后被实例/类级替换；热路径不读源码或重新计算文件摘要。
3. driver 已安全空闲，`_dma_safe=True`，`_active=False`、`poisoned=False`，活动和保留缓冲区列表均为空；完成记录为本帧，DMA复位后双 Halted，且没有 DMA 错误位。
4. mask 及整个 base 链都是普通 NumPy ndarray，C连续、可写，最终拥有者 `base=None/owndata=True`；mask 必须覆盖拥有者全部字节，首地址和长度完全一致。偏移切片、外部 buffer、CMA/PynqBuffer、Fortran数组、只读数组均不能走这条路径。
5. driver 实例没有直接保存与该输出共享内存的数组别名。冻结源代码同时证明它不会在内部保留该输出。

若是完整 reshape view，只把拥有者的 `shape` 元数据改为 mask 形状后返回，不复制像素。最终交给 C 的对象本身依然 `owndata=True`、C连续、`base=None`，并且不共享已释放的 CMA。无法证明任意条件时，使用原来的独立 NumPy copy，绝不让未知注入驱动借此返回别名。

输入连续快照继续保留，不假定调用者不会在其他线程改写原采集缓冲区。每帧 DMA 分配、缓存维护、停止和安全释放逻辑全部沿用冻结驱动，没有跨帧共享 CMA 或内存池。

## 结果和时间口径

原 mask、计数、状态、帧 ID 和 timing 字段保持兼容。新增：

- `implementation="pcb-b-closure/0.3.2"`：服务实现版本，硬件 ABI 仍为3。
- `mask_validation`：实际使用的严格候选。
- `output_ownership`：`frozen_driver_owning_base` / `frozen_driver_owning_array` / `copy_fallback`。

`result_validation_and_copy_seconds` 仍覆盖后状态、所有检查和输出所有权处理，即使本帧不再复制像素，字段名称也保持兼容。`process_seconds_single_call` 包含输入快照等完整服务工作。分配、缓存、DMA、停止等包含在 transfer 时间中；这些数据不能称为纯 PL 延迟。

## 离线测试

```text
python -B -m unittest discover -s outputs/pcb_b_closure_v032/software/tests -p test_service_optimization.py -v
```

36项测试已经通过：复用 v031 全部21项接口回归，增加256值和混合数组穷举、随机污染、非连续/Fortran/只读/空数组、dtype及子类运算重载检查、每候选的错误白点统计、DMA安全/双Halted，以及精确冻结驱动的零复制拥有者证明。未知驱动、子类、源摘要错误、方法替换、保留别名和非完整 base 都测试了 fallback。

测试使用普通电脑和替身寄存器，不代表本轮真实板端性能或持续运行验收通过。测试借用 sibling `pcb_b_integration_v031` 的原接口用例及 `pcb_b_roi_v03` 的寄存器替身，两个旧工程源文件保持不变。真实性能与30分钟稳定性结论，以随后保存的同板报告为准。
