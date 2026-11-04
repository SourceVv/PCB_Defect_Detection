# A B C接口与黄金参考

## 当前状态

本仓库只有软件基线。A维护数据、配置与参考算法，B提供RTL/HLS与Vivado工程，C提供PYNQ采集、Overlay、DMA和显示。PL后端尚未实现，禁止用软件结果标注为硬件成绩。

## 分清两个输出

1. **固定二值算子参考**：整幅uint8灰度图，启用的缺件ROI并集内执行`255-gray > 120`输出255，否则0；ROI外为0。
2. **缺件检测特征**：area_inv、cd40、sad、hcorr按位号选择，尚未约定全部由PL承担。二值算子通过不代表四种特征全部实现。

B/C先选一个算子调通DMA，再考虑特征统计。ORB定位与复杂判定可以保留在PS。下面的生成与验证针对正常参考帧，不能替代缺件、边界输入与长时间板端测试。

## 生成并核验

```powershell
.\.venv\Scripts\python.exe -m pcb_inspection make-golden --config configs/roi_config.json --out golden/generated
.\.venv\Scripts\python.exe -m pcb_inspection verify-golden --config configs/roi_config.json --golden golden/generated
```

输出说明：

| 文件 | 内容 |
|---|---|
| input_gray.npy / .bin | 灰度参考输入；uint8、1900行×2533列；bin无头、按行连续，共4,812,700字节 |
| expected_mask.npy / .bin | 上述固定二值算子的期望输出，0/255 |
| expected_stats.json | 全部启用任务的状态和特征；未实现任务为uncertain；含源配置、参考图哈希 |
| config.json | 同版本配置副本，参考路径相对该副本解析 |

所有人用同一Git提交及相同样例，生成后结果应一致；字节图像要求完全相同，特征当前容差为1e-4。若跨平台/OpenCV版本出现误差，要保存实际差异并审查，不要为了通过随意扩大容差。

## 比较PL原始掩码

B把实际PL输出保存为uint8、相同尺寸、相同行顺序的原始文件，如`outputs/pl_mask.bin`：

```powershell
.\.venv\Scripts\python.exe -m pcb_inspection verify-golden --config configs/roi_config.json --golden golden/generated --actual-mask outputs/pl_mask.bin
```

程序检查长度、差异像素数、首个差异的`[y,x]`。没有提供`--actual-mask`或`--actual-stats`时，结果中的`hardware_validated=false`，只证明软件参考一致。

`hardware_validated`表示这次提供了外部输出文件用于比较，**不会证明文件真的由FPGA产生**。实际来源要有板端程序、bit/hwh版本、运行日志及实验记录支持。

## 比较实际特征

若已实现所有约定缺件特征，可用`--actual-stats outputs/pl_stats.json`。格式为位号映射：

```json
{
  "U1": {"status": "present", "feature_value": 0.0, "area": 29069}
}
```

以上只示范结构，实际文件需包含全部14个缺件位号；数值由板端实际测得，不能照抄期望文件。若B只实现一个算子，不应使用全14特征比较来宣称完整实现。

## DMA约定尚待共同确定

- 当前整帧约4.81MB，要明确DMA长度配置，或者选择逐ROI/分块传输。
- 图像内存为uint8；若AXI流为32位，每拍如何打包、最后一拍TKEEP以及TLAST都要写清。
- 简单DMA的一次数据包结束与视频每行结束不是同一约定。
- 开始顺序、缓存一致性、连续物理内存、超时处理需在板端验证。
- 整机性能包含采集、预处理、DMA、PL、判定和显示；同时报告算子时间和完整链路时间。

## 配置改变后

更新配置版本和黄金参考，重新跑测试；记录与旧版结果的差异，再由B/C确认接口。勿直接把旧13位号黄金包接到14位号软件上。
