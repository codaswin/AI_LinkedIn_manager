# RAG Skill

Ingestion, chunking, embedding, retrieval, and an optional Knowledge Graph layer for KG RAG.

## Chunking
```python
# rag/chunking.py
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    source: str
    metadata: dict


def semantic_chunk(text: str, source: str, target_tokens: int = 500, overlap: int = 50) -> list[Chunk]:
    """Split on paragraph boundaries first, then merge/split to hit the target size."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) > target_tokens * 4:  # ~4 chars/token estimate
            chunks.append(Chunk(text=current, source=source, metadata={}))
            current = current[-overlap * 4:] + para  # keep overlap for continuity
        else:
            current += "\n\n" + para
    if current:
        chunks.append(Chunk(text=current, source=source, metadata={}))
    return chunks


def qa_pair_chunk(pairs: list[dict], source: str) -> list[Chunk]:
    """One chunk per Q&A pair — for structured sources like resolved doubts."""
    return [
        Chunk(text=f"Q: {p['question']}\nA: {p['answer']}", source=source, metadata={"type": "qa_pair"})
        for p in pairs
    ]
```

## Ingestion
```python
# rag/ingest.py
import faiss
import numpy as np
from openai import OpenAI
from app.rag.chunking import semantic_chunk, Chunk
from app.config import settings

client = OpenAI()


def embed(texts: list[str]) -> np.ndarray:
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return np.array([e.embedding for e in resp.data], dtype="float32")


def build_index(chunks: list[Chunk], index_path: str = settings.VECTOR_DB_PATH):
    vectors = embed([c.text for c in chunks])
    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)  # cosine sim via normalized inner product
    faiss.normalize_L2(vectors)
    index.add(vectors)
    faiss.write_index(index, f"{index_path}/index.faiss")

    # store chunk metadata alongside — FAISS only stores vectors
    import json
    with open(f"{index_path}/chunks.json", "w") as f:
        json.dump([{"text": c.text, "source": c.source, "metadata": c.metadata} for c in chunks], f)


def ingest_source(path: str, source_name: str, chunk_strategy=semantic_chunk):
    with open(path) as f:
        text = f.read()
    chunks = chunk_strategy(text, source_name)
    build_index(chunks)
    return len(chunks)
```

## Retrieval (with reranking hook)
```python
# rag/retrieve.py
import json
import faiss
import numpy as np
from app.rag.ingest import embed
from app.config import settings


def load_index(index_path: str = settings.VECTOR_DB_PATH):
    index = faiss.read_index(f"{index_path}/index.faiss")
    with open(f"{index_path}/chunks.json") as f:
        chunks = json.load(f)
    return index, chunks


def retrieve(query: str, top_k: int = 5, rerank: bool = False) -> list[dict]:
    index, chunks = load_index()
    query_vec = embed([query])
    faiss.normalize_L2(query_vec)

    scores, indices = index.search(query_vec, top_k * 3 if rerank else top_k)
    results = [
        {**chunks[i], "score": float(scores[0][rank])}
        for rank, i in enumerate(indices[0]) if i != -1
    ]

    if rerank:
        results = rerank_results(query, results)[:top_k]

    return results


def rerank_results(query: str, results: list[dict]) -> list[dict]:
    """Cross-encoder or LLM-based rerank — only add this if the eval suite shows
    plain cosine similarity underperforms. Don't add complexity preemptively."""
    ...
```

## Knowledge Graph Layer (only if INITIAL.md requests KG RAG)
```python
# rag/kg.py
import networkx as nx


class KnowledgeGraph:
    def __init__(self):
        self.graph = nx.DiGraph()

    def add_entity(self, entity_id: str, entity_type: str, **attrs):
        self.graph.add_node(entity_id, type=entity_type, **attrs)

    def add_relationship(self, source_id: str, target_id: str, relation: str):
        self.graph.add_edge(source_id, target_id, relation=relation)

    def query_related(self, entity_id: str, relation: str | None = None, depth: int = 2) -> list[str]:
        """Traverse from an entity to find related entities within `depth` hops."""
        related = set()
        frontier = {entity_id}
        for _ in range(depth):
            next_frontier = set()
            for node in frontier:
                for neighbor in self.graph.successors(node):
                    edge = self.graph[node][neighbor]
                    if relation is None or edge.get("relation") == relation:
                        related.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
        return list(related)


def hybrid_retrieve(query: str, kg: KnowledgeGraph, seed_entity: str | None, top_k: int = 5) -> list[dict]:
    """Combine vector retrieval with graph-traversal context."""
    vector_results = retrieve(query, top_k=top_k)
    graph_context = kg.query_related(seed_entity) if seed_entity else []
    return {"vector_results": vector_results, "graph_context": graph_context}
```

## Best Practices
- Never use one chunking strategy for every source type — a transcript and a Q&A dataset need different strategies (see `chunking.py`)
- Store enough metadata per chunk to trace it back to its source for citations and eval debugging
- Only add reranking or a KG layer if the eval suite shows plain vector search is insufficient — don't build it preemptively
- Re-run ingestion incrementally where possible; don't rebuild the whole index for one new document once volume grows
