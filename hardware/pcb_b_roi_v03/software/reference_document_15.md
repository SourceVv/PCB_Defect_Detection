# ABI3软件使用说明

本版新增PL反相、ROI并集筛选和17槽前景面积计数。`transport.py`是已实板通过的ABI2驱动原文件，字节不变；`RoiDriver`继承其DMA缓冲、缓存维护、超时和安全恢复，只扩展新配置/状态接口。

当前RTL使用分段计算来满足100MHz时序，最多每3个周期接受一个32位输入拍。一拍仍包含最多4个有效字节，但不能将其理解为每个时钟都能连续接受一拍。驱动等待DMA完成、算法busy清零及输出包数变化，不依赖固定拍间隔；配置握手也等待ACK，不使用假定的固定延时。

## 应用入口

板上打开 `reference_document_16.ipynb`，共有6个代码格。先保持False运行前两格；准备实际硬件验证时改True，重跑第1→2格后执行3→6格。初始Notebook没有预填通过结果。板卡应使用正确的XRT环境和管理员运行环境；具体硬件加载需按本轮新版本测试计划执行。

底层流程示例：

```python
from roi_driver import RoiDriver
from roi_reference import load_inputs

# 本例会实际加载FPGA；应在独占DMA且获准操作的板端环境执行。
driver = RoiDriver.from_bitfile('release/roi_v0_3/pcb_roi.bit')
driver.recover()  # 确认双DMA通道停止，并清本轮算法包数/统计。
gray, _, profiles = load_inputs()
driver.configure(2533, 1900, profiles['latest17']['rois'],
                 invert=True, roi_enable=True)
output = driver.transfer(gray, threshold=120, bypass=False)
stats = driver.read_results()
print(stats['roi_counts'])
```

输入为uint8二维图时，shape必须精确等于已配置的(height,width)，转置后同字节数也拒绝。允许一维uint8输入，但总像素数必须与geometry精确匹配，并按从左到右、从上到下连续展开。总字节数必须为4的倍数且不超过8,388,607。不要给每行2533字节另加padding。

## configure的规则

`configure(width,height,rois,invert=True,roi_enable=True)`接收最多17个字典，每项形如：

```python
{'name': 'U1', 'box': [2125, 1095, 2360, 1275], 'enabled': True}
```

name必须唯一，box为整数半开坐标，ROI在图内且非空。可以disabled，但仍占一个索引槽。配置前必须recover且算法空闲。参数预验证失败不改此前有效配置；真正开始写硬件后若失败，则configured=false，后续传输被拒绝，需诊断/恢复/重新完整配置。

配置先把enable mask写0，写geometry和各坐标，最后启用目标位图。每个写事务先写cfg数据，再翻转token，等ACK且busy清零，再从cfg输入通道核对读回。控制与数据输出通道从不读回用于恢复shadow。只允许一个调用者独占设备；本驱动没有线程锁。

最新17 ROI顺序中U4是index12，R12/C13/L2/C20随之顺延。旧16 profile没有U4，所以不能把两个profile的同索引直接当作同一器件。所有报告同时保存名字与box。

## 模式与面积

反相阈值120等价于原灰度<135；g=134输出255，g=135输出0。`roi_enable=True`仅保留启用ROI并集；关闭输出ROI筛选时，仍分别计算启用ROI的前景面积。重叠像素在不同ROI中分别计入，在总输出前景中只计一次。

旁路直接输出原输入，并按协议将所有前景统计置0；有效输入像素总数仍统计。因此旁路结果里即使有数值255，output_white_pixels仍为0，该字段在旁路时表示统计未启用。

`read_results()`要求本配置已有一次成功传输且当前安全空闲。它返回output_white_pixels、valid_pixels、按名字的roi_counts、17项roi_counts_by_index和status。它读取最近输出TLAST握手时提交的统计，不清计数。configure/recover/下一次transfer会使软件“上次结果有效”标记失效。

frame_error/cfg_error会拒绝结果；DMA已安全停止的帧错误异常可能附有actual_output用于留证。PoisonedError表示尚不能确认硬件停止，必须保留驱动、缓冲区和内核，先使硬件受控停止再恢复。不要通过删除对象、绕过断言或重复下载来清理。

## 八个验收案例与记录

`board_validation.validate_board()`自动依次运行8例，首错停止：32B旁路、64B旁路、8×4重叠ROI边界、640×480旁路、整图旁路、整图反相阈值、最新17ROI整图、冻结16ROI整图。

每例保存实际BIN、每ROI期望/实测面积、总前景、像素总数、状态与包数。最后一例直接对照A原mask。CPU参考单次耗时、配置耗时、transfer+read_results单次耗时分开记录，写BIN不计入后者；这些调用耗时不能直接证明持续帧率或可靠加速比。

两组冻结profile和标准文件有摘要：fixtures/sha256.json、config/sha256.json。`roi_reference.audit()`会重新算8例并核对冻结输入/输出摘要、三份整图npy、面积统计和原A旧mask。修改ROI配置需要生成独立新版本金标准。

本轮面积统计不能替代SAD、hcorr、cd40或姿态分类。当前17ROI中多种A特征并非area_inv，报告保留source_feature说明这一边界。
