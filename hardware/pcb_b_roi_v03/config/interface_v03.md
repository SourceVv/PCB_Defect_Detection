# B ROI/面积统计接口候选 v0.3

本版独立于已实板通过的 ABI0.2。目标：原灰度整帧输入，PL完成可选反相、严格阈值、最多17个矩形ROI的并集输出和各ROI前景面积。坐标在参考图像坐标系，几何对齐仍由CPU负责；面积不能替代SAD/hcorr/cd40等分类特征。

## 总线与端口

100MHz，同域AXIS32，KEEP4，LAST仅整帧末拍，p0在低字节。内部采用分段寄存计算，理想无反压时最小接收间隔为3个时钟；VALID/READY保证输入暂停时不丢数据。模块 `axis_roi_threshold_stats`，端口与旧核心相同，加 `cfg_data_word[31:0]` 输入、`cfg_result_word[31:0]` 输出。17个ROI上限，模块默认parameter ROI_COUNT=17。

沿用Simple双向DMA（memory64/stream32、addr32、length23、无DRE/SG）和 `axi_gpio_ctrl`（ch1输出ctrl、ch2输入status、默认0x180）。新增 `axi_gpio_cfg`（ch1输出cfg_data，ch2输入cfg_result，两通道32位、默认0、无中断）。软件从HWH解析地址并核对参数及范围，禁止硬编码新增IP基址。

## ctrl_word

bits7:0像素阈值；bit8旁路；bit9电平软复位；bit10反相；bit11输出按ROI并集保留；bit12配置写toggle；bits23:16配置/读取地址；其余保留0。阈值/旁路/反相/ROI输出模式在包首个已接受输入拍锁存；包内变化不影响该包。旁路直接输出原输入有效字节，跳过反相/阈值/ROI，前景统计为0。

## status_word

bits31:16已完成输出包计数，允许16位回绕；bits15:8 ABI=3；bit0 busy（输入包、待输出拍、配置待完成均算忙）；bit1 inpacket；bit2配置ack toggle；bit3 sticky cfg_error；bit4最近已完成帧的frame_error；bits7:5为0。完成计数、提交统计均以输出TLAST真正握手为准。

## 配置写与读取

GPIO_cfg ch1 offset0写数据；GPIO_ctrl ch1 offset0写控制。写步骤：仅在安全空闲时先写data，再设置地址并翻转ctrl.bit12；保持data/addr/toggle直到status.bit2匹配且busy=0。硬件可用一拍pending后采样数据并ack，以给跨GPIO写入留稳定时间。忙时配置请求拒绝、ack该token并置cfg_error，不能修改正在使用的参数。配置命令与首拍同时到达须阻止首拍或拒绝命令，不能造成半包新旧配置。

读步骤：只更新ctrl地址字段，不翻转toggle，再读GPIO_cfg ch2 offset8。选择地址可在空闲时切换。统计是最近完成包的快照，空闲时读取；读取无清零。

| 地址 | 写入 | 读回 |
| --- | --- | --- |
| 0x00 | width低16、height高16 | 同值 |
| 0x01 | ROI enable位图低17位 | 同值 |
| 0x02 | 非法写 | 最近输出白像素数（并集、不重复） |
| 0x03 | 非法写 | 最近有效输入像素总数 |
| 0x10+4*i+0 | ROI i x0低16位 | 同值 |
| 0x10+4*i+1 | ROI i y0低16位 | 同值 |
| 0x10+4*i+2 | ROI i x1低16位 | 同值 |
| 0x10+4*i+3 | ROI i y1低16位 | 同值 |
| 0x80+i | 非法写 | ROI i前景像素数 |

ROI左闭右开(x0,y0,x1,y1)。union计数不重复，单ROI计数允许重叠分别计入。ROI前景统计对每个启用ROI按反相/阈值后的前景独立计数，输出ROI模式关闭时仍统计。旁路模式所有前景统计为0；有效像素总数仍统计。旁路输出本身可能含255，这时0x02的值仍按“未执行前景判定”定义为0。

仅有效KEEP字节推进光栅坐标，按lane0到lane3顺序紧密排列，支持宽2533的跨行beat。合法帧应刚好width*height有效像素；宽高0、提前LAST、多余像素均置frame_error。即使尺寸错误也转发协议和LAST，以便DMA能够停止；软件拒绝错误帧。非法ROI或frame尺寸由软件配置前验证，硬件对无效几何范围不产生越界访问。

软复位清流状态、工作/提交计数、完成包数、frame_error/cfg_error和pending，保留几何/ROI配置；ack对齐当前ctrl toggle。外部复位同时清配置（宽高0、mask0、ROI0）、默认ack0。每包首拍清工作计数，输出LAST握手时提交快照。应覆盖单拍包与前包输出LAST/后包首拍同周期场景。

## 软件契约

`RoiDriver`复用已实板通过的原DMA实现（复制为transport.py，不改其算法），重写read_status为ABI3、from_bitfile核对新增GPIO、_write_control保留配置/模式字段。configure仅在recover后确认DMA安全、算法空闲时执行。参数预验证失败保留此前已生效配置；实际硬件配置开始后失败则configured=false。transfer要求输入uint8形状与已配置geometry匹配、总字节4倍数且<=2^23-1。错误/超时处理沿用原安全恢复与poisoned缓冲保留。read_results在成功/空闲时读取提交的统计。

## 本轮拟验收

先离线单元仿真（全部KEEP、反压、包内参数锁、配置拒绝、尺寸错误、复位、计数提交）与整图向量，再构建同次bit/hwh并审阅时序/DRC。板上动作须以完成的新版本和具体测试清单为依据。

计划8例：32B旁路、64B旁路、8x4重叠ROI/阈值边界、640x480旁路、整图旁路、整图反相阈值（输出ROI关闭）、最新17ROI整图、冻结16ROI整图。比较实际每像素mask、每ROI面积、总白像素、输入像素总数、包数增量、接收长度；本轮不声明全缺陷分类通过。
