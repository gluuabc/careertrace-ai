import unittest
import io
import json
from unittest.mock import patch

from app.database.database import create_database_engine, create_session_factory, init_db
from app.database.repository import ProfileRepository
from app.database.retrieval_repository import RetrievalRepository
from app.services.retrieval import HybridRetrievalService, reciprocal_rank_fusion
from app.services.retrieval_eval import mean_reciprocal_rank, recall_at_k
from app.services.embeddings import TitanEmbeddingProvider
from app.services.reranking import AmazonRerankProvider
from app.state.retrieval_schema import HybridRetrievalResult, RetrievalHit, RetrievalLoopState


class FakeEmbeddings:
    model_id = "fake-embedding-v1"
    dimensions = 1024

    def __init__(self):
        self.calls = 0

    def embed(self, text):
        self.calls += 1
        vector = [0.0] * self.dimensions
        lowered = text.casefold()
        if "artificial intelligence" in lowered or " ai" in f" {lowered}":
            vector[1] = 1.0
        elif "kubernetes" in lowered:
            vector[0] = 1.0
        else:
            vector[2] = 1.0
        return vector


class FakeReranker:
    def rerank(self, _query, candidates, *, top_n=10):
        ranked = sorted(candidates, key=lambda item: "semantic" not in item["document"]["title"].casefold())[:top_n]
        for rank, item in enumerate(ranked, start=1):
            item["rerank_rank"] = rank
            item["rerank_score"] = 1.0 / rank
        return ranked


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        init_db(self.engine)
        factory = create_session_factory(self.engine)
        users = ProfileRepository(factory)
        self.user = users.get_or_create_user("Ada", "ada-retrieval@example.com")
        self.other = users.get_or_create_user("Other", "other-retrieval@example.com")
        self.repository = RetrievalRepository(factory)
        self.embeddings = FakeEmbeddings()
        self.service = HybridRetrievalService(self.repository, self.embeddings)

    def tearDown(self):
        self.engine.dispose()

    def test_embedding_is_reused_by_hash_model_and_dimension(self):
        self.service.index_text(corpus_type="project", user_id=self.user["user_id"], source_entity_id="one", source_version="1", title="One", text="A stable paragraph.")
        self.service.index_text(corpus_type="project", user_id=self.user["user_id"], source_entity_id="two", source_version="1", title="Two", text="A stable paragraph.")
        self.assertEqual(self.embeddings.calls, 1)

    def test_private_documents_never_cross_user_boundary(self):
        private = self.service.index_text(corpus_type="resume", user_id=self.other["user_id"], source_entity_id="private", source_version="1", title="Private", text="Kubernetes secret experience")[0]
        public = self.service.index_text(corpus_type="job", user_id=None, source_entity_id="public", source_version="1", title="Public", text="Kubernetes role")[0]
        sparse = self.repository.sparse_search(self.user["user_id"], "Kubernetes", ["resume", "job"])
        identifiers = {item[0]["retrieval_document_id"] for item in sparse}
        self.assertNotIn(private["retrieval_document_id"], identifiers)
        self.assertIn(public["retrieval_document_id"], identifiers)

    def test_hybrid_finds_sparse_technical_and_dense_semantic_matches(self):
        sparse_doc = self.service.index_text(corpus_type="job", user_id=None, source_entity_id="sparse", source_version="1", title="Kubernetes Platform", text="Cluster operations")[0]
        dense_doc = self.service.index_text(corpus_type="job", user_id=None, source_entity_id="dense", source_version="1", title="Semantic role", text="Artificial intelligence research")[0]
        with patch.dict("os.environ", {"BEDROCK_RERANK_ENABLED": "false"}):
            result = self.service.retrieve(user_id=self.user["user_id"], query="Kubernetes AI", corpus_types=["job"], top_k=10)
        ids = [item.retrieval_document_id for item in result.items]
        self.assertIn(sparse_doc["retrieval_document_id"], ids)
        self.assertIn(dense_doc["retrieval_document_id"], ids)
        self.assertGreaterEqual(recall_at_k(ids, {sparse_doc["retrieval_document_id"], dense_doc["retrieval_document_id"]}, 10), 1.0)
        self.assertGreater(mean_reciprocal_rank([ids], [{dense_doc["retrieval_document_id"]}]), 0.0)

    def test_reranker_improves_or_preserves_labeled_top_result(self):
        first = {"retrieval_document_id": "first", "title": "Lexical", "text": "kubernetes", "corpus_type": "job", "metadata": {}, "evidence_ids": []}
        relevant = {"retrieval_document_id": "relevant", "title": "Semantic", "text": "artificial intelligence", "corpus_type": "job", "metadata": {}, "evidence_ids": []}
        fused = reciprocal_rank_fusion([(first, 2.0)], [(relevant, 1.0)])
        before = [item["document"]["retrieval_document_id"] for item in fused]
        reranked = FakeReranker().rerank("AI", [{**item, "rerank_text": item["document"]["text"]} for item in fused])
        after = [item["document"]["retrieval_document_id"] for item in reranked]
        self.assertGreaterEqual(mean_reciprocal_rank([after], [{"relevant"}]), mean_reciprocal_rank([before], [{"relevant"}]))

    def test_titan_embedding_payload_and_dimension_are_bounded(self):
        class Client:
            def invoke_model(self, **kwargs):
                self.kwargs = kwargs
                return {"body": io.BytesIO(json.dumps({"embedding": [0.0] * 1024}).encode())}
        client = Client()
        provider = TitanEmbeddingProvider(client)
        vector = provider.embed("hello")
        payload = json.loads(client.kwargs["body"])
        self.assertEqual(client.kwargs["modelId"], "amazon.titan-embed-text-v2:0")
        self.assertEqual(payload["dimensions"], 1024)
        self.assertEqual(len(vector), 1024)

    def test_amazon_rerank_uses_locked_model_and_region_payload(self):
        class Client:
            def rerank(self, **kwargs):
                self.kwargs = kwargs
                return {"results": [{"index": 1, "relevanceScore": 0.9}]}
        client = Client()
        provider = AmazonRerankProvider(client)
        result = provider.rerank("query", [{"rerank_text": "one"}, {"rerank_text": "two"}], top_n=1)
        config = client.kwargs["rerankingConfiguration"]["bedrockRerankingConfiguration"]
        self.assertEqual(provider.region, "us-west-2")
        self.assertEqual(config["modelConfiguration"]["modelArn"], "arn:aws:bedrock:us-west-2::foundation-model/amazon.rerank-v1:0")
        self.assertEqual(result[0]["rerank_rank"], 1)

    def test_iterative_retrieval_rewrites_once_and_stops_when_sufficient(self):
        class LoopService(HybridRetrievalService):
            def __init__(self): self.queries = []
            def retrieve(self, *, query, **_kwargs):
                self.queries.append(query)
                identifier = "one" if len(self.queries) == 1 else "two"
                hit = RetrievalHit(retrieval_document_id=identifier, corpus_type="job", title=identifier, text_excerpt=identifier, rrf_score=1.0)
                state = RetrievalLoopState(original_query=query, active_query=query, selected_ids=[identifier])
                return HybridRetrievalResult(items=[hit], state=state)
        service = LoopService()
        result = service.retrieve_iteratively(user_id="user", query="first", corpus_types=["job"], desired_count=2, max_iterations=4, query_rewriter=lambda *_: "refined")
        self.assertEqual(service.queries, ["first", "refined"])
        self.assertTrue(result.state.sufficiency["sufficient"])
        self.assertEqual(result.state.query_variants, ["first", "refined"])

    def test_iterative_retrieval_stops_on_no_progress(self):
        class LoopService(HybridRetrievalService):
            def __init__(self): self.calls = 0
            def retrieve(self, *, query, **_kwargs):
                self.calls += 1
                hit = RetrievalHit(retrieval_document_id="same", corpus_type="job", title="same", text_excerpt="same", rrf_score=1.0)
                return HybridRetrievalResult(items=[hit], state=RetrievalLoopState(original_query=query, active_query=query))
        service = LoopService()
        result = service.retrieve_iteratively(user_id="user", query="first", corpus_types=["job"], desired_count=3, max_iterations=5, query_rewriter=lambda active, _items: active + " refined")
        self.assertEqual(service.calls, 2)
        self.assertEqual(result.state.sufficiency["stop_reason"], "no_progress")


if __name__ == "__main__":
    unittest.main()
