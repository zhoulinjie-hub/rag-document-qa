# FastAPI PDF RAG 示例

该项目把 PDF 上传、文本提取与切分、DashScope 向量化、余弦相似度检索和通义千问回答串成一个完整流程。原有的 `/`、`/upload` 和 `/ask` 接口均被保留。

## 启动

1. 复制 `.env.example` 为 `.env`，填写 `DASHSCOPE_API_KEY`。现有 `.env` 已配置时无需覆盖。
2. 安装依赖并启动：

```powershell
cd "E:\interview-demo"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

打开 `http://127.0.0.1:8000/docs` 可以按顺序调用接口。

## RAG 使用顺序

1. `POST /upload`：上传 PDF，建立并持久化向量索引。
2. `POST /search`：只做相似度检索，便于检查召回文本。
3. `POST /ask`：检索相关文本，再让 `qwen-plus` 基于上下文回答并返回来源。
4. `GET /documents`：查看已建立索引的文档。
5. `DELETE /documents/{document_id}`：删除指定文档的索引。

`POST /ask` 请求示例：

```json
{
  "question": "这份文档的核心结论是什么？",
  "top_k": 3,
  "document_id": null,
  "min_score": null,
  "use_rag": true
}
```

向量索引默认保存在 `data/rag_index.json`。项目不保存上传的 PDF 原文件，只保存文本块、元数据和向量。如果尚未上传文档，`/ask` 会保留原项目的通用大模型问答行为，并在响应中标记 `mode: "general"`。
