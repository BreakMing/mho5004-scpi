# 运行准备与文件地图

技能文件夹只保存技能资源，正常调用时保持只读。脚本依据自身位置读取包内依赖和模板；截图、CSV、绘图、摘要、日志、索引及临时文件均保存到当前工作项目或用户指定的位置。

Agent 根据当前任务确定两个绝对路径：`$SkillRoot` 为本包 `SKILL.md` 所在目录，`$WorkRoot` 为本次工作项目或用户指定的输出父目录。下面的 PowerShell 命令从工作项目执行，先填写：

```powershell
$SkillRoot = "技能文件夹绝对路径"
$WorkRoot = (Get-Location).Path
$TaskId = [guid]::NewGuid().ToString("N")
```

`$TaskId` 每个新任务生成一次；结果不明时保留原标识查证，不借更换标识重新保存。没有可写工作位置才询问用户。脚本不猜当前项目，也没有技能目录下的默认输出；必须传绝对路径，拒绝技能目录、其内部路径及包含整个技能的上级目录。CSV 输出目录须尚不存在；截图归档根目录可复用。

Windows 使用普通绝对路径，脚本和输出路径均不要使用 `\\?\`、`\\.\` 特殊前缀。索引和 Runtime 的 `.tmp` 文件采用排他创建；已有同名临时文件时停止并保留证据，不覆盖它。

## 环境

Python 3.11 或以上；标准库包含 `tomllib`、socket、RPC 编解码及 FTP 支持，无需 VISA 或官方上位机。唯一第三方运行依赖见[requirements.txt](../requirements.txt)，用于 PNG 检查和绘图。

已有依赖可直接使用；确需准备依赖时，在工作项目中建立虚拟环境，不能放进技能目录：

```powershell
python -m venv "$WorkRoot/.venv-mho5004"
& "$WorkRoot/.venv-mho5004/Scripts/python" -m pip install -r "$SkillRoot/requirements.txt"
& "$WorkRoot/.venv-mho5004/Scripts/python" -B "$SkillRoot/scripts/scope_csv.py" --help
```

Linux/macOS 使用该环境的 `bin/python`。选择哪个解释器运行，后续入口也使用同一解释器。CLI 入口会禁用随后导入模块的 Python 缓存；自行编写 Runtime 调用必须用 `python -B`，防止导入模块执行前生成其自身缓存。上述依赖准备不连接仪器；不要在每次任务重复安装。

## 连接

[config/instrument.toml](../config/instrument.toml)只有协议默认值，`host` 留空，没有已确认接线或采集状态。SCPI 经 VXI-11 的 portmapper 111 发现 core 服务，资源名 `inst0`；FTP 默认 3721。FTP 端口不是 SCPI 文本端口。

每次实机调用传 `--host`（Runtime 用 `host=...`）；不把现场地址写回 Skill。`$InstrumentHost` 表示本次已确认的设备 IP 或主机名，首次使用可通过 `$InstrumentHost = Read-Host "本次确认的示波器地址"` 填写；`--ftp-port` 可覆盖默认 FTP 端口。缺少或空白目标地址时，在 DNS/连接前停止；已有 CSV 分析、帮助及 `--reindex` 无需地址。实机前先核对任务所需身份/状态，现场接线和实际倍率复用仍适用的用户确认。FTP 未开且无法远程开启时，请用户在设备 ES 文件浏览器打开“网络→从PC访问→打开”，反馈地址后返回波形界面并保持服务。

## 随包文件与输出

| 位置 | 用途 |
|---|---|
| [操作参考](operations.md) | 采集状态、固定入口、文件核验、测量与异常/交付 |
| [能力边界](capabilities.md) | 当前局部通过、仅回读、拒绝及未验证项 |
| [命令索引](command-index.md) | 历史命令形式、PDF 页及典型响应解释；不等于功能通过清单 |
| [厂商资料说明](../THIRD_PARTY_NOTICES.md) | 手册取得方法及参考版本指纹；PDF 不随包，下载到工作项目/用户资料目录 |
| [脱敏真实CSV样本](../assets/samples/ch1-compensation.csv) | 历史 CH1 补偿方波，仅替换序列号，采样数值保留；供本地示例/搬移核验，不作为当前采集 |
| [三项产物模板](assets.md) | 波形图样式、数据摘要表和截图浏览页，含历史预览及修改方法 |
| `scripts/` | 截图、CSV、解析器、Runtime、文本VXI-11通信及截图下载依赖，全部随包 |
| `$WorkRoot/output/oscilloscope_screenshots/` | 项目中的截图归档，包含按北京时间建的子目录及根目录 HTML/CSV 索引 |
| `$WorkRoot/output/oscilloscope_csv/$TaskId/` | 项目中的单次 CSV 保存或分析结果，包含原件、窗口数据、图片、摘要及相关证据 |
| [manifest.json](../manifest.json) | 交付文件字节数及SHA-256；修改后原清单不再代表修改版 |

厂商 PDF 未确认再分发授权，候选包已移除 PDF，命令页码仍对应资料说明中的参考件。读取测量值或调整参数需先取得适用手册核对语法，不能因包内无 PDF 而猜写入形式；CSV 本地示例无需手册。历史 CSV 的脱敏说明和哈希见[预览来源](../assets/previews/provenance.json)。完整开发来源记录及历史原始响应另行归档，Skill 的正常使用不依赖这些归档。

## 本地示例及复核

```powershell
python -B "$SkillRoot/scripts/scope_csv.py" analyze "$SkillRoot/assets/samples/ch1-compensation.csv" --start-s=-0.000001 --end-s=0.000001 --output "$WorkRoot/output/oscilloscope_csv/$TaskId"
$env:MHO5004_TEST_OUTPUT = "$WorkRoot/output/skill_checks"
python -B -m unittest discover -s "$SkillRoot/tests" -p 'test_*.py' -v
```

分析入口在指定任务目录生成 `original.csv`、`window.csv`、`waveform.png`、`summary.md` 和 `summary.json`。测试将包复制到新的带空格目录，在无关工作目录验证帮助、依赖定位、真实样本、模板、输出隔离与清单；明确指定技能外路径，原技能不改动。测试以审计钩子拒绝网络请求，不模拟仪器；保留生成目录供检查。未设置 `MHO5004_TEST_OUTPUT` 时测试使用系统临时目录。测试不会实机验收截图、CSV保存或参数设置。
