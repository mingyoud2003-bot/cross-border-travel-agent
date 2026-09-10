import json
from pathlib import Path

from openai import OpenAI


ROOT = Path(__file__).resolve().parent.parent

KNOWLEDGE_FILE = (
    ROOT
    / "knowledge"
    / "loyalty_rules.jsonl"
)

INDEX_FILE = (
    ROOT
    / "knowledge"
    / "loyalty_index.json"
)

EMBEDDING_MODEL = "text-embedding-3-small"


def load_documents():
    documents = []

    with open(
        KNOWLEDGE_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        for line in file:
            if line.strip():
                documents.append(
                    json.loads(line)
                )

    return documents


def main():
    client = OpenAI()

    documents = load_documents()

    texts = [
        (
            f"{doc['title']}\n"
            f"{doc['content']}"
        )
        for doc in documents
    ]

    print(
        f"正在为 {len(texts)} 个知识块生成 Embedding..."
    )

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )

    index = []

    for document, embedding_data in zip(
        documents,
        response.data,
    ):
        item = {
            **document,
            "embedding": embedding_data.embedding,
        }

        index.append(item)

    with open(
        INDEX_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            index,
            file,
            ensure_ascii=False,
        )

    print(
        f"索引已保存：{INDEX_FILE}"
    )

    print(
        f"Embedding tokens: "
        f"{response.usage.total_tokens}"
    )


if __name__ == "__main__":
    main()