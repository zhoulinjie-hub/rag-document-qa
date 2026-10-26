# C2 GitHub Code Review 版本检查报告

- 检查日期：2026-10-26
- 项目路径：`E:\interview-demo`
- 分支：`main`
- 范围：README、部署配置、自动化测试复核、Git 全历史敏感信息检查
- 明确未执行：创建远程仓库、push、公网部署、DashScope 调用

## 1. 结论

C2 公开 Review 版本已在本地准备完成。原有 Git 历史和已经通过验收的 RAG 核心实现得到保留；README 已按实际代码补齐；Docker 与 Compose 配置已通过本地构建和启动检查；测试、依赖和敏感信息检查均未发现阻塞项。

本报告不声称该项目具有生产级吞吐、向量数据库、OCR、ANN 检索或多实例安全能力。当前检索实现仍是 NumPy 余弦相似度线性扫描和排序，索引仍是本地 JSON 文件。

## 2. C2 修改

| 文件 | 修改内容 |
| --- | --- |
| `README.md` | 补充项目介绍、技术栈、Mermaid 架构图、上传与问答时序、API、安装运行、测试、安全设计和已知限制 |
| `.env.example` | 补全无密钥的模型、RAG、安全和容器环境变量示例 |
| `.gitignore` | 排除本地配置、虚拟环境、PDF、全部 `data/`、输出、日志、私钥、证书包和本地数据库 |
| `.dockerignore` | 排除 Git、配置、测试资料、报告、数据、PDF、日志、私钥和本地数据库 |
| `Dockerfile` | Python 3.13 slim、非 root 运行、健康检查和 Uvicorn 启动 |
| `compose.yaml` | 本机回环端口、命名数据卷、只读根文件系统、无新增权限和 capability 丢弃 |
| `rag_service.py` | 仅修正一处字典字段缩进，不改变 RAG 行为 |

C2 主提交：`6b8e8a9 docs: prepare public code review package`。

## 3. README 与实现一致性

- PDF：PyMuPDF 分页提取，只支持可提取文本的 PDF，不包含 OCR。
- 切分：默认按字符 `500` 切分，相邻块重叠 `100`。
- Embedding：DashScope OpenAI 兼容接口，默认 `text-embedding-v4`。
- 检索：查询向量与候选向量通过 NumPy 计算余弦相似度，全量排序后取 Top-K；没有向量数据库或 ANN。
- 索引：`data/rag_index.json` 保存文档元数据、原文块和完整向量，整体 JSON 原子替换。
- 问答：仅有可靠来源时以召回上下文调用 `qwen-plus`；无可靠来源返回 `insufficient_context`。
- 引用：响应和网页展示文件名、页码、相关度与原文。
- 隔离：列表、检索、问答和删除按签名会话的 `owner_id` 过滤。

## 4. 已执行验证

| 检查 | 命令摘要 | 结果 |
| --- | --- | --- |
| pytest | `.venv\Scripts\python.exe -B -m pytest` | `22 passed in 0.83s` |
| Python 编译 | `python -B -m compileall -q ...` | 退出码 0 |
| 本地依赖 | `.venv\Scripts\python.exe -m pip check` | `No broken requirements found.` |
| Compose 解析 | `docker compose config --quiet` | 退出码 0 |
| 镜像构建 | `docker build --pull=false -t interview-demo-rag:c2 .` | 构建成功 |
| 镜像依赖 | 容器内 `python -m pip check` | `No broken requirements found.` |
| 运行用户 | 容器内 `id -un` | `app`，非 root |
| 构建上下文隔离 | 检查镜像中的配置、测试、报告、索引和输出路径 | 均不存在 |
| 容器启动 | 临时绑定 `127.0.0.1:8766` | 容器运行，`/health` 200，`/` 200 |
| Git 完整性 | `git fsck --full --no-dangling` | 退出码 0，无输出 |

Docker 冒烟测试使用占位访问码且未配置模型 Key，只访问健康检查和首页，没有调用 Embedding 或聊天模型。临时容器已停止；本地镜像 `interview-demo-rag:c2` 保留供后续 Review。

## 5. 私人数据排除检查

只检查路径、布尔状态和数量，不读取或输出密钥值。

| 项目 | 本地状态 | Git 公开风险 |
| --- | --- | --- |
| `.env` | 存在、已忽略 | 未跟踪 |
| `.venv` | 存在、已忽略 | 未跟踪 |
| `data/rag_index.json` | 存在、已忽略 | 未跟踪 |
| `data/demo_access_token.txt` | 存在、已忽略 | 未跟踪 |
| PDF | 本地 3 个，全部被忽略 | 未跟踪 |
| 日志 | 本地 1 个，已忽略 | 未跟踪 |
| 风险路径 | 当前 Git 跟踪命中 0 个 | 通过 |

`.env.example` 被有意保留；其中 `DASHSCOPE_API_KEY` 为空，演示访问码是明确占位符，不是实际凭据。

## 6. Git 全历史敏感信息扫描

扫描方法：遍历 `git rev-list --all` 中的每个提交，检查所有文本 blob 和历史路径；只汇总命中数量，不打印内容或作者身份。检查类别包括：

- 常见 API Key、云凭据、GitHub Token 和私钥头特征；
- 电子邮箱、手机号和 Windows 用户目录形式的个人信息；
- `.env`、虚拟环境、PDF、索引、数据、日志、输出、私钥、证书包和本地数据库路径；
- Git 作者元数据是否使用疑似个人身份。

报告提交后的最终扫描覆盖 6 个提交和 24 个不同历史路径，结果为：密钥内容命中文件 0、个人信息内容命中文件 0、风险历史路径 0、当前风险跟踪路径 0、作者元数据风险 0。仓库远程地址数量为 0。

正则扫描不能提供数学意义上的绝对证明；创建 GitHub 远程仓库后，仍建议启用平台 Secret Scanning，并在 push 前再执行一次同类检查。本阶段没有创建远程仓库。

## 7. 命令记录

```powershell
git branch --show-current
git log -1 --oneline
git status --short
git diff --check
.\.venv\Scripts\python.exe -B -m pytest
.\.venv\Scripts\python.exe -B -m compileall -q main.py rag_service.py security.py tests
.\.venv\Scripts\python.exe -m pip check
docker compose config --quiet
docker build --pull=false -t interview-demo-rag:c2 .
docker run --rm --entrypoint python interview-demo-rag:c2 -m pip check
git fsck --full --no-dangling
git rev-list --all
git ls-tree -r --name-only <commit>
git grep -I -E -l <pattern> <commit>
git ls-files
```

敏感信息扫描命令中的实际正则和内部匹配结果未写入报告，避免意外暴露疑似凭据内容。

## 8. 失败、警告与中断

- 项目测试、Compose 解析、镜像构建和容器冒烟测试没有失败。
- Docker 构建阶段出现 pip 以 root 安装到镜像层的标准警告；最终应用进程使用非 root 用户 `app`。
- Git 在 Windows 上提示部分工作区文件未来可能由 LF 转为 CRLF；`git diff --check` 通过，未发现空白错误。
- C2 最终审查期间 Codex 客户端曾出现一次 `403 Forbidden` 连接中断；从磁盘续接后确认提交、文件和工作区未损坏。该错误不是项目 API 或 Docker 测试失败。

## 9. 剩余限制与下一步

- 尚未创建 GitHub 仓库、远程地址或分支保护。
- 尚未 push，尚未运行 GitHub 平台的 Secret Scanning 或 CI。
- 尚未部署公网；容器只在本地回环地址做过冒烟测试。
- 未做负载测试、性能基准、OCR、多 worker 一致性或生产身份系统。
- 真正公开前应更换演示访问码、确认测试 PDF 无隐私，并决定托管平台和费用边界。

本阶段到此暂停，等待用户确认后再进行 GitHub 或部署操作。
