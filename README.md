# Word 检测报告分析智能体

项目独立运行，依赖本目录的代码、固化标签规范、模型配置和 requirements.txt；Linux amd64 旧 DOC 读取器已随包提供。训练标签只在离线规范学习和对照评估时读取，推理时不读取输入所对应的标准答案。

## 安装与运行

需要 Python 3.10+。工作区附带 Linux amd64 的 antiword 处理旧 DOC；其他系统请安装原生 antiword，也可使用 LibreOffice 作为转换备用。当前环境的 `python` 已有所需依赖：

```bash
cd /data1/zwd/agent-new
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m bridge_agent serve --port 8510
```

浏览器打开 `http://127.0.0.1:8510`，上传 Word，查看处理阶段，完成后下载 DOCX。远程机器可通过 SSH / VS Code 转发 8510 端口。网页服务默认绑定本机；上传文件与中间状态保存到 `work/uploads/`，页面重新打开仍可查询。

现有 `.env` 的 `INSPECTION_API_BASE_URL`、`INSPECTION_API_KEY`、`INSPECTION_MODEL` 会原样使用。支持标准 Chat Completions function tools；未配置或模型失败会明确报错，不退回规则生成。

单份报告与批量处理：

```bash
python -m bridge_agent run '初赛测试集/待处理.docx' --output result
python -m bridge_agent batch 初赛测试集 --output result/初赛 --work work/competition --concurrency 4
```

### 决赛阶段1：一键后台处理4份报告

在任意目录执行项目内的启动脚本即可；下面以项目根目录为例：

```bash
cd /data1/zwd/agent-new
bash run_batch.sh
```

脚本读取项目 `.env`，同时启动最多 4 份报告；每份内部模型请求默认并发为 1，因此批次内模型请求并发上限为 4。这与下文原模块命令的单报告 `--concurrency` 不同。关闭终端后后台任务继续运行；重复启动会明确拒绝。现有模型配置直接沿用，不需要在脚本中填写密钥。

```dotenv
BATCH_INPUT_DIR=阶段1测试集
BATCH_OUTPUT_DIR=result/阶段1
BATCH_WORK_DIR=work/阶段1
BATCH_WORKERS=4
BATCH_REQUEST_CONCURRENCY=1
```

相对路径以 `agent-new` 为基准，也支持绝对路径；有空格的目录可在 `.env` 中使用双引号。递归读取 `.doc` / `.docx`，忽略 Word 的 `~$` 临时文件。输出目录和工作目录必须在输入目录外；同名文件主干会导致输出重名，预检时拒绝启动，避免覆盖。

```bash
bash run_batch.sh --dry-run   # 只检查目录、文件清单和配置，不调用模型
bash run_batch.sh --status    # 查看成功、运行中、失败、待处理数量
bash stop_batch.sh            # 停止本脚本启动的批次
```

启动时打印实际日志路径；日志和每次批次状态保存在 `work/batch_runner/<批次编号>/`。最近一批的状态为 `work/batch_runner/active.json`，输出目录内的 `batch_manifest.json` 逐份记录处理结果、错误和输出文件。某份失败后继续处理其他文件，全部结束后批次标记为 `succeeded` 或 `failed`。停止使用进程启动标识核对控制器和工作进程，只处理此批次，保留原文、已导出报告、分块检查点和历史日志。

再次执行 `bash run_batch.sh` 会重新扫描目录，并让原有报告流程复用源文件、模型和代码均兼容的检查点；已经完成的任务不重新请求模型，最终 Word 会重新导出。已在服务端处理的请求不保证因客户端停止而取消。工作进程使用后台控制器管理，网页任务列表只显示网页上传任务；本批次进度用 `--status` 或日志查看。

如果系统 `python` 不是项目使用的环境，可先设置 `INSPECTION_PYTHON=/绝对路径/bin/python`；此项为启动脚本使用的 shell 环境变量。

原模块命令 `python -m bridge_agent batch` 的 `--concurrency` 控制同一份报告内的模型请求并发，取值 1、2、4，其批量文件依次执行；要同时处理4份文件请使用上面的 `run_batch.sh`。原文件不会被修改。每份报告以原文件名加“含有对比年度/无对比年度的信息提取报告.docx”输出；`batch_manifest.json` 记录成功与失败。原模块批处理有失败时返回非零退出码，后台脚本的最终状态则由 `--status` 或状态文件查询。

重新执行同一命令会复用已完成的本地阶段。原始模型响应还可在相同 API 地址和完整请求载荷下跨任务精确复用，复用后重新执行当前版本校验；这不会读取标签答案。`INSPECTION_FRESH_RUN=1` 禁用跨任务响应复用；真正冷测还需使用新的 `--work` 目录。任务键含原文 SHA256、文件名、模型、API 地址、代码、标签规范和分块配置；这些变动会自动新建任务，不会混用不兼容结果。单任务锁阻止同一任务重复执行。失败任务保留已完成分块，修复接口后可以继续。

## 如何实现

1. 按文件魔数识别 OOXML、二进制 DOC、HTML，DOC 使用 antiword 的 DocBook/表格文本导出，已有安全转换缓存时读取 OOXML，必要时在独立 LibreOffice profile 中转换；保留段落与表格原始顺序、合并单元格的有效值、表题的左右幅/主引桥范围、原始行列和稳定证据 ID。页数仅记录 Word 元数据，不伪造精确页码。
2. 全部可读文本按实际序列化字符预算划分；独立模型调用提取当前病害、基本字段、建议、成因、安全影响和历史资料。所有可靠标准病害行先保存到台账，模型复核并只排除非当前病害；表格描述通过列索引恢复，保留位置、尺寸和单位。大台账按原表及病害类型分组，每组关联全部明细。系统从其他训练标签学习同一桥梁相近年份的写法，选择逐条、代表位置或数量汇总；当前五份验证标签已排除在粒度学习之外。原文代表位置分别输出，汇总模式才组合原文统计和位置，保留每个分组的全部明细链接。当前与历史明确区分，照片图题不作为重复病害。
3. 对没有在本批中完成历史判断的病害，从原文明确对比区域检索候选，由模型判定同一构件、位置和病害关系。没有资料不认定新增或修复，不将建议当作既成事实。
4. 最终 Agent 自主调用 `search_document`、`read_sources`、`read_findings`，最多 10 轮，最后调用 `submit_report`。模型负责分析，程序负责栏目和版式。长病害表无需再次全文生成。
5. 结构、来源、原文数值、历史引用和完整扫描校验后，按固化的 16 字段、4 列建议表、7 列病害表输出；实际保存并重新打开 DOCX 核对。未完成的报告不会发布为成功。

任务目录中包含 `manifest.json`、`status.json`、`batches/`、`findings.json`、`history/`、`agent_trace.json`、`analysis.json`、`report.json`、`metrics.json` 和每次真实请求的 `calls/`。API 密钥不进入这些文件。内部证据 ID 只在审计结果中保留，最终报告正文不出现开发调试信息。

## 标签学习与评估

`assets/label_spec.json` 为可执行字段规范，`assets/few_shot.json` 为训练数据中的少量写作示例，`assets/layout.docx` 只保留样式和纸张设置，样例正文已删除。全部标签的结构频次见 `artifacts/label_patterns.json`，原报告与标签的文件名和正文身份核验见 `artifacts/training_pairs.json`。

```bash
python -m bridge_agent audit
python -m bridge_agent evaluate 'result/生成报告.docx' '赛题一_训练集标签/对应标签.docx' --output artifacts/comparison.json
```

评估报告列出 12 个核心字段的逐项精确比较、病害描述匹配、数值、历史状态、漏项和多余项。病害匹配使用公开可解释的文本相似度指标，不能冒充官方分数。标签并非完全无噪声：字段标题、表头、序号、少量事实可能不一致，事实冲突时应以输入原文为依据，并在验收记录中说明。

## 当前边界

扫描件和仅存在于图片中的文字未启用 OCR；图像型空文本文件会失败，不输出伪完整报告。文本可读的检测报告会在能取得时保留图片数量（旧 DOC 某些备用路径无法取得图片数），但不声称已经识别图片。没有官方评分脚本时，来源可核验、结构正确和本地标签对照必须分别看待，不能由其中一项推出官方评分。DOCX 可正常读回不等于已经逐页人工检查所有渲染页面。

真实空状态验收曾出现最终 Agent 在 10 轮内未提交的失败；同代码重新从空状态运行成功，首次逐轮诊断未保留，尚不能认定该随机失败的根因已经消除。系统保留失败状态并禁止导出未通过校验的结果；后续验收失败会在临时目录清理前留存诊断。

压缩包使用方式：解压后进入 `code/`，安装 `requirements.txt`，将 `.env.example` 复制为 `.env` 并填入自己的模型配置，再运行上述模块命令。`design/` 保存设计和实测验收记录，`result/` 只包含通过流程校验的样例报告。

六份样例的来源、生成版本和文件哈希见 `artifacts/sample_provenance.json`（压缩包内为 `design/validation/sample_provenance.json`）。其中双凤桥保留早期版本的真实网页上传结果；其余按最终矩阵、无响应缓存长报告及独立解包结果分别登记，没有人工修改答案。

完整实测结果和限制见 [VALIDATION.md](VALIDATION.md)，耗时与并发取舍见 [PERFORMANCE.md](PERFORMANCE.md)。运行 `python scripts/validate.py --name 新名称` 可以从独立任务状态重测验证矩阵；`python scripts/benchmark.py --name 新名称` 比较并发 1/2/4，均默认禁用跨任务模型响应缓存。

目前交付的是已验证能够独立运行的工程版本。长报告的病害粒度和逐项历史对应仍与标准答案存在明显差距，不能据此认定已达到比赛高分或逐份复现标签。16 字段与 3/4/7 列表格是固定格式合同；原始标签本身存在版式变体，未承诺逐像素相同。

汇总模式的统计原文也使用片段编号选择，再由程序原样恢复，避免模型转写丢失尺寸或标点。风格先验可在训练标签仍可用时通过 `python scripts/learn_style.py` 重建，推理不依赖原训练目录。

Qwen 前端失败的当前修复与真实验证边界见 [QWEN_FIX.md](QWEN_FIX.md)。先前交付包与性能表对应原冻结版本，当前源码修复的验证单独记录。

阶段 1 六项失败的修复说明见 [STAGE1_FIX.md](STAGE1_FIX.md)。日期按训练标签规则处理：明确报告/批准日期优先，其次封面落款，再次发出/签发日期；均缺失时取本次检验日期区间最后一天，继承省略的年份/月，不补造日。抽取与最终提交共用校验；antiword 失败会继续尝试 LibreOffice，病害分组支持截断拆分及检查点恢复。
