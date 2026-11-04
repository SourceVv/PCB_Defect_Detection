# 模块接口约定

此文件是A/B/C的共同接口入口。软件接口已有实现，DMA/PL接口仍为待确认草案。更改格式时要同时更新生产方、消费方、测试和配置版本。

## 已有PC软件接口

```python
from pcb_inspection import load_config, detect
result, annotated = detect(image, config, backend="software")
```

| 内容 | 当前约定 |
|---|---|
| 图像 | uint8 BGR或二维灰度 |
| 主配置尺寸 | 2533×1900，图像数组顺序image[y,x] |
| ROI | x0,y0,x1,y1，左闭右开 |
| box | 配置尺寸坐标 |
| original_box与annotated | 调用方输入图像坐标 |
| 图像失败 | image_ok=false，器件返回invalid |
| 缺件 | missing或present，仅表示存在性 |
| 未实现任务 | uncertain及task_not_implemented |
| FPGA后端 | 未实现，不能静默使用软件代替 |

## 待B/C确认的DMA接口

| 内容 | 决策要求 |
|---|---|
| 首个PL算子 | 在反相阈值、面积或SAD等中选择，并与A确认黄金参考 |
| 传输单位 | 整帧、单ROI或分块；当前整灰度帧为4,812,700字节 |
| 数据与打包 | uint8像素如何进入AXI流；TDATA、字节顺序和尾拍TKEEP |
| TLAST | 若采用简单DMA，定义每次数据包结束，不直接套用视频逐行结束 |
| 输入输出大小 | 一次传输的字节数、缓冲区长度、DMA长度位宽 |
| 控制 | 参数寄存器、版本号、启动、完成和错误状态 |
| 异常 | 超时、断流、帧大小不符、无板与定位失败的处理 |
| 测量 | 算子内耗时与完整PS/PL往返耗时分别记录 |

不要在双方尚未确认时将这些草案写成已实现功能。所有实际协议与测试数据由B/C补充到本目录。

## 黄金数据

生成、比对及实际PL输出格式详见`../pcbinspection/docs/hardware_handoff.md`。固定二值掩码与缺件特征是两种不同输出；只实现二值算子时不能宣称四类检测特征全部硬化。
