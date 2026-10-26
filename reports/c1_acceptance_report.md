# C1 真实验收报告

日期：2026-10-26
项目：`E:\interview-demo`
本地地址：`http://127.0.0.1:8765`

## 结论

受控的真实 RAG 链路验收通过：一页文字型 PDF 被解析为 2 个文本块，真实调用
`text-embedding-v4` 建立 1024 维向量索引；问题经过 Top-K 检索后由
`qwen-plus` 生成答案，网页显示 2 条第 1 页引用及原文。

本次真实验收严格限制为 1 次有效上传、1 次问答。文档向量化、问题向量化和
聊天补全均只请求 1 次，没有自动重试或充值操作。

## 运行环境与安全检查

- 使用项目解释器：`E:\interview-demo\.venv\Scripts\python.exe`
- `pip check`：通过，未发现损坏依赖。
- 主要版本：FastAPI 0.143.1、Uvicorn 0.54.0、PyMuPDF 1.28.2、
  OpenAI 3.31.0、NumPy 2.5.4。
- DashScope API Key 已配置；检查和日志均未输出密钥内容。
- `.env`、`.venv`、PDF、`data/rag_index.json`、本地日志和临时文件均被
  `.gitignore` 排除。
- 服务仅监听 `127.0.0.1:8765`，未启动公网服务。

## 自动化测试

命令：

```powershell
.\.venv\Scripts\python.exe -B -m pytest
```

结果：`22 passed in 1.05s`。Embedding 与聊天补全在 pytest 中使用 Mock，
没有产生模型调用费用。

覆盖范围包括：

- 会话签名、防篡改、错误访问码、请求限额；
- PDF 解析、文本切分、向量检索、持久化和会话隔离；
- 登录、受保护接口、上传、问答、来源返回和错误信息脱敏；
- 错误扩展名、损坏或无文本 PDF、无相关来源、内部异常和 429 限流。

## 真实端到端结果

测试文档：`output/pdf/c1_public_rag_acceptance.pdf`，1 页、未加密、
可提取 574 个字符，不含个人信息。视觉检查未发现乱码、裁切或重叠。

### 上传与索引

| 项目 | 结果 |
| --- | --- |
| `POST /session` | HTTP 200，552 ms |
| `POST /upload` | HTTP 200，1228 ms |
| 页数 | 1 |
| 文本块 | 2 |
| Embedding 模型 | `text-embedding-v4` |
| 向量维度 | 1024 |
| 复用旧索引 | 否 |
| 外部文档 Embedding | HTTP 200，1 次 |

### Top-K 检索与问答

问题：`Atlas-27 在什么条件下触发人工复核，并要求多久内完成？`

网页端到端耗时：3443 ms。`POST /ask` 返回 HTTP 200；问题 Embedding 和
`qwen-plus` 聊天补全各调用 1 次，外部状态均为 HTTP 200。

原始答案：

> Atlas-27 在温度传感器读数**连续两个采样周期高于 42°C**时触发人工复核；值班人员应在触发后的**15 分钟内开始并完成人工复核**[来源2｜c1_public_rag_acceptance.pdf｜第1页]。

引用结果：

| 来源 | 页码 | 相似度 |
| --- | ---: | ---: |
| 来源 1 | 1 | 0.789 |
| 来源 2 | 1 | 0.758 |

文档仅有 2 个文本块，因此 Top-K=3 最终返回 2 条结果。网页已验证显示答案、
文件名、页码、相似度和展开后的原文。最终页面截图保存在本次 Codex 对话中。

## 真实异常检查

| 场景 | 结果 |
| --- | --- |
| 浏览器错误访问码 | HTTP 401，页面显示“访问码不正确” |
| 未授权读取文档列表 | HTTP 401 |
| 授权后的空文档列表 | HTTP 200 |
| 非 PDF 扩展名 | HTTP 400 |
| 损坏 PDF | HTTP 400 |

接口内部异常、无相关内容和请求限额还通过 Mock 自动化测试覆盖。本轮受控付费
验收受“最多问答 1 次”限制，没有再发起额外的真实无相关问题请求。

## 日志

- Uvicorn 访问与外部 HTTP 状态：`E:\interview-demo\logs\c1-uvicorn.log`
- 结构化验收结果：`E:\interview-demo\logs\c1-real-rag-acceptance.json`

日志包含本地状态码、耗时、答案、引用页码和模型请求状态，不包含 API Key。

## 执行命令

```powershell
# 依赖一致性
.\.venv\Scripts\python.exe -B -m pip check

# 自动化测试
.\.venv\Scripts\python.exe -B -m pytest

# 本地服务
.\.venv\Scripts\python.exe -B -m uvicorn main:app --host 127.0.0.1 --port 8765
```

真实上传使用 PowerShell `Invoke-WebRequest -Form` 请求本机 `/upload`，网页使用
同一隔离会话完成 `/ask`。命令和报告均省略访问码、会话令牌与 API Key。

## 过程中遇到的问题

- 项目最初缺少 pytest，新增 `requirements-dev.txt` 后安装 pytest 9.1.1。
- 第一次异常接口检查使用 `curl.exe` 时被 Windows 拒绝启动；改用 PowerShell
  `Invoke-WebRequest` 后完成检查。
- 内置浏览器和 Edge 扩展均无法直接设置文件输入；没有产生上传请求。最终通过
  本机 API 执行唯一上传，再用短时同源桥接页把同一隔离会话交给浏览器。桥接文件
  已删除并验证不存在。
- 桥接页的内联脚本被 CSP 阻止；改用同源外部脚本。首次生成外部脚本时 PowerShell
  引号解析失败，修正后成功。
- 临时桥接文件的直接删除命令被安全策略拦截；改用补丁删除并验证不存在。
- 完整长页截图超时；当前视口截图成功，并清晰包含文档、答案和两条引用原文。
- 安全策略阻止直接删除二进制临时 PNG；这些文件位于被忽略的 `tmp/`，未纳入提交。

## 已知限制

- 未测公网部署、并发吞吐、OCR、超大 PDF 或长时间稳定性。
- 页面采用安全的纯文本渲染，模型返回的 Markdown `**` 会按原文显示，不执行
  HTML，避免引入 XSS 风险。
- 本轮没有获取 DashScope 账单金额；只验证了请求次数和 HTTP 状态。
