# CSV 波形数据摘要

本表依据本次输入文件生成；时间、电压均采用 CSV 原值。若输入是历史样本，本表也仅描述该历史文件。

## 原始文件

| 项目 | 数据 |
|---|---|
| 型号／序列号 | ${model} |
| 来源通道 | ${source} |
| 原文件点数 | ${original_rows} |
| 文件头采样间隔（s） | ${sample_interval} |
| 原文件时间范围（s） | ${original_start} ～ ${original_end} |
| 原文件电压范围（V） | ${original_vmin} ～ ${original_vmax} |
| 原件 SHA-256 | `${original_sha256}` |

## 本次选取

| 项目 | 数据 |
|---|---|
| 请求起点（s） | ${requested_start} |
| 请求终点（s） | ${requested_end} |
| 实际时间范围（s） | ${selected_start} ～ ${selected_end} |
| 选取点数 | ${selected_rows} |
| 对应原始样点（从 1 起算） | ${first_index} ～ ${last_index} |
| 选取电压范围（V） | ${selected_vmin} ～ ${selected_vmax} |

按原时间列包含两端选点；实际范围以表中结果为准，不保证请求区间全部有数据。未平移零点、重复施加探头倍率、平滑或重采样。完整 CSV 文件不证明全部采集内存、无抽取或绝对精度。

![CSV 波形图](waveform.png)

[原 CSV](original.csv) · [本次窗口数据](window.csv) · [结构化摘要及哈希](summary.json)
