# 桥梁检测报告分析智能体（阶段1提交版）

本目录包含运行必需的Python模块、离线风格资源、Word版式模板、上传页面和批量启动/停止脚本。无需训练集或标签原件。`../result/`是已生成的22份正式报告；新生成结果建议存入`../generated/`。

## 环境与配置

使用Linux、Python 3.10或以上版本。建议安装LibreOffice，处理antiword无法读取的旧版Word文件。

```bash
cd code
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Debian/Ubuntu；系统已有时可跳过
sudo apt-get install -y libreoffice-writer
cp .env.example .env
```

编辑`.env`的`INSPECTION_API_BASE_URL`、`INSPECTION_API_KEY`、`INSPECTION_MODEL`，接入支持Chat Completions结构化工具调用的模型；地址应包含服务要求的`/v1`，程序会追加`/chat/completions`。API密钥和模型权重未随包提供。Qwen模型名是本批次使用的标识，部署端模型标识不同时应调整。

## 上传页面与单文件运行

```bash
python -m bridge_agent serve --host 127.0.0.1 --port 8510 --concurrency 1
```

浏览器访问`http://127.0.0.1:8510`，上传DOC或DOCX，查看阶段状态并下载报告。页面中的文件依次处理，`--concurrency`控制每份内部请求并发。

```bash
python -m bridge_agent run ../input/报告.doc --output ../generated --work work/jobs --concurrency 1
```

输出文件名主体与输入一致，扩展名统一为`.docx`；简要信息表头为“字段、内容、说明”。同一批输入不应有同名主体的不同扩展名文件，防止输出重名。

## 四文件并发、进度和停止

在`.env`设置`BATCH_INPUT_DIR`为原件目录，可用绝对路径；相对路径基于`code/`。`BATCH_OUTPUT_DIR`、`BATCH_WORK_DIR`也可配置。

```bash
bash run_batch.sh --dry-run  # 检查配置及输入，不调用模型
bash run_batch.sh            # 后台同时处理最多4份文件
bash run_batch.sh --status   # 查询成功、失败及正在处理的文件
bash stop_batch.sh          # 停止本批客户端，保留已生成文件和检查点
```

`BATCH_WORKERS=4`控制文件级并发，`BATCH_REQUEST_CONCURRENCY=1`控制每份内部请求。不要将`python -m bridge_agent batch --concurrency 4`误认为4份文件并发，该命令仍逐文件运行。

运行后自动建立`work/`，保存解析缓存、请求响应、工具轨迹及检查点；批次日志位于`work/batch_runner/`。兼容任务可复用已完成阶段，相同请求可复用缓存；严格空状态验证应使用全新代码副本/工作目录，并设置`INSPECTION_FRESH_RUN=1`以禁用跨任务响应缓存。

## 实现与范围

`pipeline.py`依次执行解析、分块抽取、病害编审、历史核对、工具辅助撰写和Word回读。标准病害表由程序按列还原候选，模型补充、修正和分析；`assets/`包含字段约定、少量标签示例、写法特征和版式，无需读取原训练目录。接口失败、输出截断或校验持续失败时保留错误，不以规则报告替代模型。

提交版保留核心生成流程和上传页面，省去离线训练标签审计、评测、开发测试及打包工具；`audit.py`仅保留运行所需字段常量。与本批生成时相比，展示层只调整表头和成品命名。既有22份报告在打包前生成，本次未再次调用模型。工程校验不等于官方评分，当前不支持图片OCR，历史匹配和病害粒度仍可能需要专业复核。
