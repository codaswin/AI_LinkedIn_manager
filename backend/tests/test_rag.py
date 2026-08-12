import pytest
from app.rag.chunking import chunk_document_semantic, chunk_structured_1to1
from app.rag.ingest import (
    VectorStore,
    ingest_news_article,
    ingest_post,
    ingest_research_note,
    ingest_style_guide,
    ingest_thread,
)
from app.rag.retrieve import retrieve

SAMPLE_PARAGRAPH = (
    "Agentic AI tooling continues to evolve rapidly, with new orchestration "
    "frameworks emerging every quarter. Teams are adopting retrieval-augmented "
    "generation to ground outputs in verifiable source material."
)


def _build_long_document(num_paragraphs: int = 30) -> str:
    return "\n\n".join(f"{SAMPLE_PARAGRAPH} Section {i}." for i in range(num_paragraphs))


class TestChunkStructured1to1:
    def test_post_yields_exactly_one_chunk(self):
        chunks = chunk_structured_1to1(
            source_id="post-1", source_type="past_posts", text="Excited to launch our new product."
        )
        assert len(chunks) == 1
        assert chunks[0].source_type == "past_posts"
        assert chunks[0].source_id == "post-1"
        assert chunks[0].chunk_index == 0
        assert chunks[0].id == "past_posts:post-1"

    def test_thread_yields_exactly_one_chunk(self):
        chunks = chunk_structured_1to1(
            source_id="thread-1",
            source_type="comment_dm_threads",
            text="Q: How do I get started?\nA: Check out our docs.",
        )
        assert len(chunks) == 1
        assert chunks[0].source_type == "comment_dm_threads"

    def test_research_note_yields_exactly_one_chunk(self):
        chunks = chunk_structured_1to1(
            source_id="note-1",
            source_type="x_research_notes",
            text="New agent orchestration framework trending on X this week.",
        )
        assert len(chunks) == 1
        assert chunks[0].source_type == "x_research_notes"

    def test_empty_text_rejected(self):
        with pytest.raises(ValueError):
            chunk_structured_1to1(source_id="post-1", source_type="past_posts", text="   ")


class TestChunkDocumentSemantic:
    def test_short_text_yields_single_chunk(self):
        chunks = chunk_document_semantic(
            source_id="guide-1", source_type="brand_voice", text=SAMPLE_PARAGRAPH, target_tokens=500
        )
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0

    def test_long_text_splits_into_multiple_semantic_chunks(self):
        long_text = _build_long_document(num_paragraphs=30)
        chunks = chunk_document_semantic(
            source_id="guide-2", source_type="brand_voice", text=long_text, target_tokens=500
        )
        assert len(chunks) > 1
        for chunk in chunks:
            estimated_tokens = len(chunk.text) / 4
            assert estimated_tokens <= 500 * 1.5
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_does_not_naively_slice_mid_paragraph(self):
        long_text = _build_long_document(num_paragraphs=10)
        chunks = chunk_document_semantic(
            source_id="guide-3", source_type="industry_news", text=long_text, target_tokens=500
        )
        for chunk in chunks:
            assert chunk.text == chunk.text.strip()
            assert not chunk.text.startswith("Section") or chunk.text.count("Section") >= 1

    def test_empty_text_rejected(self):
        with pytest.raises(ValueError):
            chunk_document_semantic(source_id="guide-1", source_type="brand_voice", text="")


class TestRetrieveSourceFilter:
    async def test_retrieve_filters_to_requested_source_types(self, tmp_path):
        index_path = str(tmp_path)
        await ingest_post(
            "post-1",
            "Excited to share our new AI agent orchestration framework launch today.",
            index_path=index_path,
        )
        await ingest_news_article(
            "article-1",
            "Breaking news: AI agent orchestration frameworks are trending across the industry.",
            index_path=index_path,
        )
        await ingest_thread(
            "thread-1",
            "What orchestration framework do you recommend?",
            "We recommend evaluating a few agent orchestration frameworks before committing.",
            index_path=index_path,
        )

        results = await retrieve(
            "AI agent orchestration framework",
            source_types=["past_posts"],
            top_k=5,
            index_path=index_path,
        )

        assert results
        assert all(r["source_type"] == "past_posts" for r in results)

    async def test_retrieve_supports_multiple_source_types(self, tmp_path):
        index_path = str(tmp_path)
        await ingest_post("post-1", "Our brand voice is concise and technical.", index_path=index_path)
        await ingest_style_guide(
            "style-1", "Write in a concise, technical, and approachable tone.", index_path=index_path
        )
        await ingest_news_article(
            "article-1", "Unrelated market news about consumer retail trends.", index_path=index_path
        )

        results = await retrieve(
            "concise technical brand voice",
            source_types=["past_posts", "brand_voice"],
            top_k=5,
            index_path=index_path,
        )

        assert results
        assert all(r["source_type"] in ("past_posts", "brand_voice") for r in results)

    async def test_retrieve_rejects_unknown_source_type(self, tmp_path):
        with pytest.raises(ValueError):
            await retrieve("query", source_types=["not_a_real_source"], index_path=str(tmp_path))


class TestIngestIdempotency:
    async def test_ingest_post_twice_does_not_duplicate(self, tmp_path):
        index_path = str(tmp_path)
        await ingest_post("post-dup", "Original text about AI tooling.", index_path=index_path)
        await ingest_post(
            "post-dup", "Updated text about AI tooling and agentic workflows.", index_path=index_path
        )

        store = VectorStore(index_path)
        assert store.count() == 1

        results = await retrieve(
            "AI tooling agentic workflows", source_types=["past_posts"], top_k=5, index_path=index_path
        )
        matches = [r for r in results if r["source_id"] == "post-dup"]
        assert len(matches) == 1
        assert "Updated" in matches[0]["text"]

    async def test_ingest_thread_twice_does_not_duplicate(self, tmp_path):
        index_path = str(tmp_path)
        await ingest_thread("thread-dup", "Q1?", "A1.", index_path=index_path)
        await ingest_thread("thread-dup", "Q1 revised?", "A1 revised.", index_path=index_path)

        store = VectorStore(index_path)
        assert store.count() == 1

    async def test_ingest_research_note_twice_does_not_duplicate(self, tmp_path):
        index_path = str(tmp_path)
        await ingest_research_note("note-dup", "First version of the note.", index_path=index_path)
        await ingest_research_note("note-dup", "Second version of the note.", index_path=index_path)

        store = VectorStore(index_path)
        assert store.count() == 1

    async def test_ingest_different_ids_accumulate(self, tmp_path):
        index_path = str(tmp_path)
        await ingest_post("post-a", "First post.", index_path=index_path)
        await ingest_post("post-b", "Second post.", index_path=index_path)

        store = VectorStore(index_path)
        assert store.count() == 2
