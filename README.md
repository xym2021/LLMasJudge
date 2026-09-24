# 交付导航：多轮旅行规划的交互性与个性化评测（基于 Trip+ 改编）

**选题**：在多轮、需要调用工具的旅行规划中，模型每轮能否选对回应方式（出方案 / 澄清 / 判无解），能否落实新要求而不丢旧要求，方案能否贴合老人、孩子等同行人的画像。

这个选题同时覆盖笔试的"能力评测"（多轮交互、工具使用、约束遵循）和"行为对齐"（澄清时机、回应方式、主动程度）两个方向。

## 文件

```
xhs笔试/
├── 招聘笔试内容.md
├── README.md                        本文件：导航 + 本地运行指南
├── 1_问题定义.md                     要求 1
├── 2_评测集.md                       要求 2：覆盖设计、成对案例、10 张 case 卡片
├── 2_评测集_cases/
│   ├── build_query_10.py             从 Trip+ 抽取 8 个 case，并改编出 0029P、0006P
│   ├── query_10.json                 可直接交给 Trip+ 运行的 10 个 case
│   ├── case_viewer.html              10 个 case 的可视化浏览页（双击打开）
│   ├── build_viewer.py               由 query_10.json 生成 case_viewer.html，含中文意译与标签
│   └── viewer_template.html          浏览页模板
├── trip-plus/                        Trip+ 沙箱工程（已配置 DeepSeek、Kimi、小红书、千问，下载 10 个 case 的数据库）
├── 3_自动评估/
│   ├── Judge_Prompt.md               Judge A（沿用 Trip+）+ Judge B（本方案扩展，可直接运行）
│   └── 评估方案与验证.md              分层与分工、证据不足的处理、调试集/验证集、记录表
```

## 本地运行指南

**不需要 GPU。** 被测模型和 Judge 都走 API，普通 Windows 电脑即可。

### 1. 沙箱工程位置

沙箱工程在本目录下的 `trip-plus/`，已完成以下准备：

- 从 [GitHub](https://github.com/junle-chen/trip-plus) 克隆代码，并安装依赖（`openai` / `httpx` / `pandas` / `regex`）；
- 在 `.env` 中配置好 DeepSeek、阿里云百炼、小红书的 key（该文件已被 git 忽略）；
- 下载好 10 个 case 所需的数据库。

从零搭建的步骤如下：

```powershell
git clone https://github.com/junle-chen/trip-plus.git
cd trip-plus
pip install -r requirements.txt huggingface_hub
copy env.example .env
```

### 2. 下载这 10 个 case 的数据库

不需要下载全量数据库。仓库已自带 `id_0005` 和 `id_0006`，其余 7 个单独下载，每个约 4–20MB。

实测 `hf download` 命令行在本机会报连接错误，改用下面的 Python 接口，直连 Hugging Face 即可成功：

```powershell
python -c "from huggingface_hub import snapshot_download as d; [d('Junle-cs/trip-plus-database',repo_type='dataset',allow_patterns=[f'database/sample/en/id_{i}/**'],local_dir='.') for i in ['0029','0050','0065','0115','0125','0148']]"
```

### 3. 接入模型

在 `.env` 中填写 key 和地址：

```text
DEEPSEEK_LLM_API_KEY=sk-...
DEEPSEEK_LLM_BASE_URL=https://api.deepseek.com
QWEN_LLM_API_KEY=sk-...
QWEN_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
XHS_LLM_API_KEY=ak-...
XHS_LLM_BASE_URL=https://note3-prev-api.askdiandian.com/v1
```

`models_config.json` 中已加入以下条目：

| 配置名 | 用途 | 接入 | 思考 |
|---|---|---|---|
| `deepseek-flash` | 被测 | DeepSeek 官方 API | 关闭（`thinking: disabled`） |
| `kimi-k3` | 被测 | 阿里云百炼 | 关闭（`enable_thinking: false`） |
| `dots3-note-prev` | 被测 | 小红书预览 API | 尽量关闭（仍可能返回少量 reasoning） |
| `qwen3.8-flash` | Judge B | 阿里云百炼 | 由 `run_judge_b.py` 开启，上限 8192 token |

小红书接口文档写的是请求头 `api-key`；实测 OpenAI SDK 默认的 `Authorization: Bearer` 也能通，所以可直接按 OpenAI 兼容方式接入，不必改 Trip+ 客户端代码。

```json
"dots3-note-prev": {
  "model_name": "dots3-note-prev",
  "model_type": "openai",
  "base_url": "${XHS_LLM_BASE_URL}",
  "api_key_env": "XHS_LLM_API_KEY",
  "temperature": 0.0,
  "timeout": 300.0,
  "extra_body": {"enable_thinking": false}
}
```

被测模型必须支持 OpenAI 格式的工具调用（function calling）。DeepSeek、Kimi、小红书三个被测模型都已实测能正确发起工具调用。

### 4. 运行

当前 DeepSeek key 可用的模型 ID 是 `deepseek-v4-pro` 和 `deepseek-flash`，两者都已写入 `models_config.json`，并且都关闭了思考。正式评测使用 `deepseek-flash`。在 `trip-plus/` 目录下运行：

```powershell
$env:PYTHONUTF8="1"
python run.py --model deepseek-flash --test-data "..\2_评测集_cases\query_10.json" `
  --database-dir database/sample/en --workers 2 --output-dir result/run1
python run.py --model kimi-k3 --test-data "..\2_评测集_cases\query_10.json" `
  --database-dir database/sample/en --workers 2 --output-dir result/run1
python run.py --model dots3-note-prev --test-data "..\2_评测集_cases\query_10.json" `
  --database-dir database/sample/en --workers 2 --output-dir result/run1
```

结果分别在 `result/run1/deepseek-flash_en/`、`result/run1/kimi-k3_en/`、`result/run1/dots3-note-prev_en/`。

`PYTHONUTF8` 用来避免 Windows 控制台打印表情符号时报错。

**冒烟测试已通过**（2026-09-24，`deepseek-v4-pro`，只跑 `--rerun-ids mt_single_0006`）：

- Windows 下推理、解析、评分全流程跑通；
- 3 轮用时约 2 分钟，共约 25.6 万 token，其中约 88% 命中缓存；
- 据此估算，全部 36 轮约需 300 万 token、25 分钟左右（2 个 worker 并行时会更快）。

改编的 `mt_single_0006P` 尚未单独验证，建议先跑一次 `--rerun-ids mt_single_0006P` 确认能正确评分。结果在 `result/<输出目录>/<模型>_en/` 下：

| 目录 | 内容 |
|---|---|
| `trajectories/` | 完整对话与工具调用，即"执行记录" |
| `reports/` | 模型每轮最终回复 |
| `evaluation/` | 规则评分明细 |

### 已知卡点

| 卡点 | 说明 |
|---|---|
| Windows | 已实测可用：直接调用 `run.py`，不需要 bash；记得设置 `PYTHONUTF8=1` |
| 数据库下载 | `hf download` 命令行报连接错误，改用上面的 Python 接口 |
| 数据为英文 | 景点、酒店名必须与数据库一致，所以沙箱里保持英文 |
| 体验评分 | Trip+ 默认用 4 个评委，本方案改为只用千问 1 个（`--simulator-model`） |

## 需要你本人完成的部分（不能代做，否则就是编造结果）

| 事项 | 在哪里填 |
|---|---|
| 至少 1 条真实执行记录或对话记录 | `1_问题定义.md` 1.2 节 |
| 跑 6 个预选 case，保存原始输出 | `3_自动评估/评估方案与验证.md` 5.1 节 |
| 在看 Judge 结果**之前**完成人工标注 | 同上 5.2 节 |
| 运行 Judge、对比、归因、在调试样本上修改 | 同上 5.3–5.4 节 |

要求 4 和正文说明文档暂未制作。
