import json
from pathlib import Path

from openai import OpenAI

from settings import load_local_env


ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_FILE = ROOT / "knowledge" / "travel_rules.jsonl"
INDEX_FILE = ROOT / "knowledge" / "travel_rules_index.json"
EMBEDDING_MODEL = "text-embedding-3-small"


def load_documents() -> list[dict]:
    with KNOWLEDGE_FILE.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def main() -> None:
    load_local_env()
    documents = load_documents()
    response = OpenAI().embeddings.create(
        model=EMBEDDING_MODEL,
        input=[f"{item['title']}\n{item['content']}" for item in documents],
    )
    index = [
        {**document, "embedding": embedding.embedding}
        for document, embedding in zip(documents, response.data)
    ]
    with INDEX_FILE.open("w", encoding="utf-8") as file:
        json.dump(index, file, ensure_ascii=False)
    print(f"built {len(index)} travel-rule chunks; tokens={response.usage.total_tokens}")


if __name__ == "__main__":
    main()
