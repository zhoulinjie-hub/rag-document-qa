import os
from dotenv import load_dotenv
from openai import OpenAI

# 加载 .env 文件中的 API Key
load_dotenv()

api_key = os.getenv("DASHSCOPE_API_KEY")

if not api_key:
    raise RuntimeError("未找到 DASHSCOPE_API_KEY，请检查 .env 文件")

# 初始化阿里云百炼客户端
client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# 准备测试文本
texts = [
    "人工智能是研究如何让机器模拟人类智能的技术。",
    "机器学习是人工智能的重要分支。",
    "今天西安的天气很好。"
]

# 调用 Embedding 模型
response = client.embeddings.create(
    model="text-embedding-v4",
    input=texts
)

# 按原始输入顺序整理向量
embeddings = [
    item.embedding
    for item in sorted(response.data, key=lambda x: x.index)
]

print("向量化成功！")
print("文本数量：", len(texts))
print("向量数量：", len(embeddings))
print("向量维度：", len(embeddings[0]))
print("第一个向量的前10个数值：")
print(embeddings[0][:10])
