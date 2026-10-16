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

`--concurrency` 控制同一份报告内的模型请求并发，取值 1、2、4；批量报告依次执行，避免把两个层级的并发相乘。原文件不会被修改。每份报告以原文件名加“含有对比年度/无对比年度的信息提取报告.docx”输出；`batch_manifest.json` 记录成功与失败，任何失败都会让批处理返回非零退出码。

重新执行同一命令会复用已完成的本地阶段。原始模型响应还可在相同 API 地址和完整请求载荷下跨任务精确复用，复用后重新执行当前版本校验；这不会读取标签答案。`INSPECTION_FRESH_RUN=1` 禁用跨任务响应复用；真正冷测还需使用新的 `--work` 目录。任务键含原文 SHA256、文件名、模型、API 地址、代码、标签规范和分块配置；这些变动会自动新建任务，不会混用不兼容结果。单任务锁阻止同一任务重复执行。失败任务保留已完成分块，修复接口后可以继续。

## 如何实现

1. 按文件魔数识别 OOXML、二进制 DOC、HTML，DOC 使用 antiword 的 DocBook/表格文本导出，已有安全转换缓存时读取 OOXML，必要时在独立 LibreOffice profile 中转换；保留段落与表格原始顺序、合并单元格的有效值、表题的左右幅/主引桥范围、原始行列和稳定证据 ID。页数仅记录 Word 元数据，不伪造精确页码。
2. 全部可读文本按实际序列化字符预算划分；独立模型调用提取当前病害、基本字段、建议、成因、安全影响和历史资料。所有可靠标准病害行先保存到台账，模型复核并只排除非当前病害；表格描述通过列索引恢复，保留位置、尺寸和单位。大台账按原表及病害类型分组，每组关联全部明细，原文统计摘要与典型位置共同进入最终表，避免只选少数代表而丢组。当前与历史明确区分，照片图题不作为重复病害。
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

压缩包使用方式：解压后进入 `code/`，安装 `requirements.txt`，将 `.env.example` 复制为 `.env` 并填入自己的模型配置，再运行上述模块命令。`design/` 保存设计和实测验收记录，`result/` 只包含通过流程校验的样例报告。

完整实测结果和限制见 [VALIDATION.md](VALIDATION.md)，耗时与并发取舍见 [PERFORMANCE.md](PERFORMANCE.md)。运行 `python scripts/validate.py --name 新名称` 可以从独立任务状态重测验证矩阵；`python scripts/benchmark.py --name 新名称` 比较并发 1/2/4，均默认禁用跨任务模型响应缓存。
