# ROI v0.3 独立工程构建入口

本目录只处理电脑上的Vivado工程，未连接JTAG或板卡。旧ABI0.2工程与已通过的bit/hwh保持在原目录。本版正式顶层为 `pcb_roi_system_wrapper`，核心为 `axis_roi_threshold_stats_0`。

## 结构

- `axi_dma_0`：原Simple双向DMA，内存64位、流32位、长度23位，无SG/DRE。
- `axi_gpio_ctrl`：原控制/状态通道布局，复位输出0x180，核心状态ABI升为3。
- `axi_gpio_cfg`：新增配置数据输出和结果读取输入，均32位，默认0，无中断。
- `axi_ctrl`：PS控制互连增加第三个从设备连接；DMA数据互连继续接HP0。
- 全部PL接口使用同一个100MHz FCLK0和同域复位。新增GPIO仅接内部算法信号。

## 在本机执行

先确认RTL仿真通过、当前源码已经冻结，再运行：

```powershell
& .\hardware\tcl\run_vivado_build.ps1 -Stage AllInProcess -Jobs 4
```

默认Vivado：`D:\AMDDesignTools\2025.2\Vivado\bin\vivado.bat`。默认板卡库：`D:\codex\board_files`。均可通过参数指定其他位置。脚本只在当前进程及其子进程设置Vivado用户数据目录和板卡库变量。

工程目标为 `build/pcb_roi/pcb_roi.xpr`；创建脚本拒绝覆盖已存在工程。构建发布目标为 `release/roi_v0_3/pcb_roi.bit` 和同名hwh；发布目录存在时也拒绝覆盖。

已有本版独立工程时，只执行构建可使用 `-Stage BuildInProcess`。本环境的 `launch_runs` 运行助手调用Windows WMI时返回Access denied，因此推荐同一Vivado进程中的全局综合、优化、布局布线和bit生成流程。该流程不修改Windows权限或Vivado安装文件，仍执行100MHz时序门禁。每个新Vivado进程都会重新注册板卡库，保持PYNQ-Z2 BoardPart。

## 需要核对的证据

- `evidence/vivado/batch_*/`：真实运行的Vivado日志、journal、构建前后源码SHA256。
- `evidence/vivado/generated/*/`：布线后的时序、资源、DRC、methodology、check_timing、布线与时钟交互报告。失败报告也保留。
- `release/roi_v0_3/timing_gate.txt`：从当前routed设计读取的最差setup/hold余量。
- 同次导出的HWH、BD重建Tcl、IP属性和地址映射。
- 最终生成的SHA256.json：用于核对bit/hwh传输是否一致。

写出bitstream不自动代表时序满足。脚本在setup或hold余量为负、缺少时序路径或缺少配套输出时停止发布。若构建期间任何RTL/Tcl改变，外层脚本报SOURCE_CHANGED并标记已生成发布目录不可用；应重建该版本，不能混用新源码和旧bit。

板级DDR负延时参数来自既有PYNQ-Z2 preset，不在这里擅自改成0。工具警告应在最终审计中按来源记录，单次构建和仿真也不能代替实板DMA与统计对照。
