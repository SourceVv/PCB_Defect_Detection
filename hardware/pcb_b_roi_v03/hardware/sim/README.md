# ROI/统计 RTL 仿真说明

本目录验证原灰度输入直接进入 `axis_roi_threshold_stats`。所有结果均为纯RTL，不等同于真实DMA或板卡结果。

## 入口

在包根的PowerShell运行，替换本机工具路径即可：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./hardware/sim/run_modelsim.ps1 -Test All -ToolBin 'D:/intelFPGA_pro/21.1/modelsim_ase/win32aloem' -PythonPath 'python'
```

`-Test Unit` 只运行协议与边界单元测试；`-Test Fullframe` 运行三整图独立金标准对照。Python需要NumPy。默认每次创建新的 `sim_runs/UTC时间_进程号`，也可用 `-WorkDirectory` 指定不存在的新目录，保护旧证据。

## 单元测试

参考记分板通过像素线性序号的除法/取模计算坐标，与RTL专用的宽1/2/3分支及并行坐标推进独立。覆盖：

- 宽1、2、3、4、5、9、2533；跨行拍、稀疏KEEP和零KEEP。
- 0、120、134、135、255像素边界；反相/旁路/ROI开关；包内更改模式仍锁定首拍。
- 17路重叠ROI、单路禁用/全部禁用、半开坐标、各ROI独立前景统计。
- 输出反压稳定性；输入LAST到达后，统计保持旧快照直到输出LAST握手。
- 旧包输出LAST与新包首拍同周期的背靠背情况。
- 提前LAST、超长帧、宽0等frame_error；下一合法帧替换错误快照。
- 忙时配置拒绝和ACK、只读地址写入拒绝、配置请求与首拍同周期。
- 软复位保留几何/ROI，清流/统计，并故意丢弃1个尚未输出拍。

实际通过摘要：39个完成包，输入10380拍、输出10379拍；1拍差是复位中止用例中预期丢弃，不能写成协议丢包。

## 三整图对照

`prepare_fullframe_vectors.py` 从本包冻结NPY、独立mask/统计和profile生成MEM；TB从文件读取答案，不在TB里重写大图ROI公式。输入是原始2533×1900灰度，每帧4,812,700字节、1,203,175拍，TLAST仅最后一拍。

| 帧 | 输出模式 | 期望白像素 |
| --- | --- | ---: |
| 1 | 反相、严格阈值120，ROI输出关闭；17ROI仍统计 | 1975978 |
| 2 | 最新17ROI并集；含U4 | 326860 |
| 3 | 冻结16ROI并集；对应A原mask | 314801 |

每帧输出逐拍比较TDATA/TKEEP/TLAST，末尾通过GPIO读回17路面积、总白像素、有效输入像素总数和完成包数。profile的ROI索引顺序分别读取，避免混淆最新索引12的U4与旧配置相同索引的R12。

本机实测三帧输入/输出各3,609,525拍，共14,438,100字节全匹配。随机反压覆盖和全部统计结果见 `evidence/rtl/`。面积统计是二值前景计数，不替代SAD/hcorr/cd40等缺陷判定特征。
