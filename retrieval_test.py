import os
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

# 读取 API Key
load_dotenv()

api_key = os.getenv("DASHSCOPE_API_KEY")

if not api_key:
    raise RuntimeError("未找到 DASHSCOPE_API_KEY")

client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# 模拟 PDF 切分后的文本块
chunks = [
    "人工智能是研究如何让机器模拟人类智能的技术。",
    "机器学习是人工智能的重要分支，可以从数据中学习规律。",
    "深度学习利用多层神经网络处理复杂的数据。",
    "西安电子科技大学是一所以电子信息为特色的高校。",
    "Python 是一种广泛应用于人工智能开发的编程语言。"
]

# 用户提出的问题
question = "机器学习和人工智能有什么关系？"

# 获取文本向量
def get_embeddings(texts):
    response = client.embeddings.create(
        model="text-embedding-v4",
        input=texts
    )

    return [
        item.embedding
        for item in sorted(response.data, key=lambda x: x.index)
    ]


# 计算余弦相似度
def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)

    denominator = np.linalg.norm(a) * np.linalg.norm(b)

    if denominator == 0:
        return 0.0

    return float(np.dot(a, b) / denominator)


# 文档和问题一起向量化
embeddings = get_embeddings(chunks + [question])

chunk_vectors = embeddings[:-1]
question_vector = embeddings[-1]

# 计算每个文本块与问题的相似度
results = []

for index, vector in enumerate(chunk_vectors):
    score = cosine_similarity(question_vector, vector)

    results.append({
        "text": chunks[index],
        "score": score
    })

# 按相似度从高到低排序
results.sort(key=lambda x: x["score"], reverse=True)

# 输出最相关的三个文本块
print("\n用户问题：", question)
print("\n最相关的三个文本块：")

for i, result in enumerate(results[:3], start=1):
    print(f"\n第{i}名")
    print("相似度：", round(result["score"], 4))
    print("文本内容：", result["text"])

print("\n向量检索测试完成！")
