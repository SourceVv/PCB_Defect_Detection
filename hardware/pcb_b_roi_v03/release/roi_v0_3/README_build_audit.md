# ROI v0.3 本次构建与核对结果

本目录是 2026 年 10 月 31 日真实完成综合、布局、布线和 bitstream 生成的发布结果。使用 Vivado 2025.2，目标器件 `xc7z020clg400-1`，板型 `tul.com.tw:pynq-z2:part0:1.0`，PL 时钟为 100 MHz。

本次电脑端构建和离线元数据核对通过，可以进入板卡验证。本文记录的通过范围不包含板卡执行、PYNQ 实际解析 HWH、DMA 收发及 ROI 统计实测；这些结果应由板上运行记录确认。

## 1. 应当一起使用的两个文件

| 文件 | 大小 | 用途 |
| --- | ---: | --- |
| `pcb_roi.bit` | 4,045,698 字节 | 下载到 FPGA 的电路配置文件 |
| `pcb_roi.hwh` | 351,702 字节 | 告诉 PYNQ 电路有哪些设备、地址和参数 |

文件名必须保持相同前缀，放在同一目录。不要把旧版 `pcb_aoi.bit` 或旧版 HWH 与这里的文件交叉使用。

`SHA256.json` 是供本版驱动读取的两项哈希清单；`SHA256SUMS.txt` 是便于人工查看的相同内容。SHA256 可以理解为文件内容的指纹：电脑端和板卡端的指纹相同，才能确认传输没有丢失、混入旧文件或改动内容。

| 文件 | SHA256 |
| --- | --- |
| `pcb_roi.bit` | `8342645ad0c263cab951957f155708a76abeaa1785432e1de81609f60099e48d` |
| `pcb_roi.hwh` | `db8ff9b8f5e0210642a2f716e704ca17f27feba545a33d7b074cf66ef5128ec1` |

## 2. 哪一份源码生成了本次电路

核心源文件是 `hardware/rtl/axis_roi_threshold_stats.v`，冻结版本的 SHA256 为：

```text
f65df400554246f70438c02f9ca4c2dd79f08d02ee8c6b4bccd9a326671d994f
```

该版本将坐标推进、比较和统计分成流水阶段，在维持 100 MHz 目标和原接口的情况下解决先前的长组合路径。构建启动前、Vivado 退出后以及发布核对时的核心及四份 Tcl 输入指纹一致。发布 bit 与本次 `in_process` 输出逐文件比对一致；HWH 与当前工程的 `hw_handoff` 输出一致。

HWH 记录接口和地址。本次流水优化没有改变这些接口，所以 Vivado 复用已生成且仍有效的 HWH，文件时间仍显示 15:01；bit 内部时间为本次实际生成的 20:46:27。不能仅凭两个文件的修改时间判断它们是否配套；本次以同一当前工程、相同接口参数、源文件指纹和文件内容比对确认配套关系。

详细机器可读结果在 `build_audit.json`。实际运行日志与前后输入指纹保存在：

```text
evidence/vivado/batch_20261031_204156_384/
```

本次布线后报告保存在：

```text
evidence/vivado/generated/20261031_204159_39512/
```

以上路径均相对于 `pcb_b_roi_v03` 工程根目录。

## 3. 时序是否满足 100 MHz

100 MHz 表示每个时钟周期为 10 ns。电路中的信号必须在下一次采样前稳定，同时在采样后保持必要时间。Vivado 用 setup 和 hold 检查这两件事。

| 检查项 | 本次结果 | 解释 |
| --- | ---: | --- |
| Setup WNS | +0.565 ns | 最差建立时间余量为正，满足本次 100 MHz 约束 |
| Setup TNS | 0 ns | 没有建立时间违例累计量 |
| Setup 失败端点数 | 0 | 没有建立时间失败端点 |
| Hold WHS | +0.030 ns | 最差保持时间余量为正 |
| Hold THS | 0 ns | 没有保持时间违例累计量 |
| Hold 失败端点数 | 0 | 没有保持时间失败端点 |
| 脉宽最差余量 | +3.750 ns | 脉宽检查通过 |

`check_timing.rpt` 中 12 类检查均为 0，包括缺少时钟、未约束内部端点、组合环及部分输入/输出延时等。没有通过降低频率、增加自定义 false path 或多周期约束来绕开本次失败路径。

本次最差 setup 路径已经转为配置 GPIO 输出到 ROI 配置寄存器的路径。最终门禁内容见 `timing_gate.txt`。时序通过只说明工具对当前设计及约束的分析通过，实板 DMA 和统计对照仍需完成。

## 4. 布线和资源占用

| 检查项 | 本次结果 |
| --- | ---: |
| 需要布线的网络 | 11,202 |
| 已完整布线的网络 | 11,202 |
| 布线错误 | 0 |
| 未实现的黑盒模块 | 0 |
| LUT | 7,222 / 53,200，13.58% |
| 寄存器 | 7,072 / 106,400，6.65% |
| BRAM Tile | 3 / 140，2.14% |
| DSP | 0 / 220 |

这些数字来自布线后 `route_status.rpt` 与 `utilization.rpt`，不是根据源码估算的结果。

## 5. 告警保留情况

本工程有告警，不能描述为“零告警工程”。本次最终构建日志累计 240 条 Warning、0 条 Critical Warning、0 条 Error；各报告的统计口径不同，不应把它们相加当成同一个总数。

布线后的 DRC 共 16 条 Warning、4 条 Advisory、0 条 Error，分类如下：

| 规则 | 等级 | 数量 |
| --- | --- | ---: |
| PDCN-1569，LUT equation term check | Warning | 3 |
| REQP-1839，RAMB36 async control check | Warning | 8 |
| REQP-1840，RAMB18 async control check | Warning | 4 |
| RTSTAT-10，No routable loads | Warning | 1 |
| REQP-165，writefirst | Advisory | 2 |
| REQP-181，writefirst | Advisory | 2 |

`methodology.rpt` 另外保留 3 条 LUTAR-1 Warning，对应生成的 `axi_mem` 协议转换器 FIFO。本次没有对这些规则设置 waiver，也没有从报告中删去告警。

初次创建工程时，板级 preset 带有四项 DDR DQS_TO_CLK_DELAY 负数，产生 PSU-1～PSU-4 Critical Warning，数值为 -0.051、-0.006、-0.009、-0.033。原始记录在 `evidence/vivado/batch_20261031_145610_117/create_project.log`。保留板级 preset，没有为了消除提示而把负数改成 0。PL 时序通过并不验证 DDR 板级参数，仍要通过板上 DMA 收发检验本次实际运行。

初次日志也记录了 DMA 异步参数和复位极性属于只读参数的设置提示。审计读取最终 HWH，确认 DMA `c_prmry_is_aclk_async=0`、复位 `C_EXT_RESET_HIGH=0`，与本版需求一致。

## 6. 新版地址必须从 HWH 读取

| 实例名 | 基地址 | PS 地址窗口 | 作用 |
| --- | --- | --- | --- |
| `axi_dma_0` | `0x40400000` | 64 KiB | 从内存发送图像并将处理结果写回内存 |
| `axi_gpio_cfg` | `0x41200000` | 64 KiB | 写入 ROI/尺寸等配置数据，读取选择的统计结果 |
| `axi_gpio_ctrl` | `0x41210000` | 64 KiB | 写控制字，读状态字 |

**本版控制 GPIO 的基地址是 `0x41210000`。旧代码若手写 `0x41200000` 作为控制 GPIO 地址，将访问到本版配置 GPIO。** 本版驱动按实例名从 HWH 提取地址，使用时应通过配套驱动，不要复制旧版手写地址。

离线核对已确认三个窗口不重叠，ROI_COUNT=17，两个 GPIO 均为 32 位双通道，DMA 内存侧 64 位、流侧 32 位、长度字段 23 位、无 SG/DRE，PS 使用 GP0 控制、HP0 传输数据，FCLK0=100 MHz。人工提取 HWH 参数后调用软件端 `_validate_roi_description` 已通过；这一步没有加载 PYNQ，也没有访问板卡内存。

## 7. 为什么保留先前失败报告

旧平面实现的 WNS 为 -10.079 ns，第一轮流水实现的 WNS 为 -2.306 ns。两轮都因时序门禁失败而没有发布 bit，不可作为本次可用电路。失败证据仍分别保留在：

```text
evidence/vivado/generated/20261031_150221_14884/
evidence/vivado/generated/20261031_203114_47680/
```

最终发布来自本文记录的第三轮 `f65d` 核心，不是从旧失败结果复制的文件。

## 8. 复现入口

在工程根目录启动 PowerShell。已有本版 Vivado 工程、源码已冻结时，使用：

```powershell
& .\hardware\tcl\run_vivado_build.ps1 -Stage BuildInProcess -Jobs 4
```

该命令对已存在的发布目录有保护，不能直接覆盖本文的发布结果。需要重建时应先有序保存旧发布证据，或在独立的新工程目录中复现。

本环境原先的 `launch_runs` 助手访问 Windows WMI 被拒绝，现使用单个 Vivado 进程完成全局综合、布局布线和生成 bit。没有修改 Windows 安全设置或 Vivado 安装文件。完整脚本入口与目录说明见 `hardware/tcl/README_build.md`。

下一步应把完整工程及本目录配套文件上传到板上，先核对 SHA256，再由本版 Jupyter 验证流程加载电路、运行 DMA、将每幅图像的 17 项统计与软件参考值逐项比较，并保存板上证据。只有这部分实测通过后，才能记录为 ROI v0.3 实板验收通过。
