"""Composição dos componentes reais do chat; recursos pertencem ao lifespan."""
from contextlib import ExitStack

from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import create_engine


class ChatRuntime:
    def __init__(self, database_url, env_file='.env', *, api_settings=None):
        # Imports de domínio só na composição; auth/linhagem podem operar sem modelos.
        from google import genai
        from google.genai import types
        from training_generation import GenerationService, GenerationSettings
        from training_ingestion.indexing import IndexingPipeline, QdrantIndex
        from training_llm import GeminiModels, ModelSettings
        from qdrant_client import QdrantClient
        from training_llm import read_settings
        from training_retrieval import LocalReranker

        values = read_settings(env_file)
        calibration = values.get('CHAT_RERANKER_CALIBRATION', '').strip()
        if not calibration:
            raise ValueError('Defina CHAT_RERANKER_CALIBRATION')
        settings = GenerationSettings.from_env(env_file)
        from .config import ApiSettings
        api_settings = api_settings or ApiSettings(database_url=database_url)
        self.resources = ExitStack()
        try:
            self.engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True,
                pool_size=api_settings.chat_pool_size, max_overflow=0, pool_timeout=api_settings.chat_pool_timeout_seconds,
                connect_args={'connect_timeout': 2, 'options': '-c timezone=UTC -c statement_timeout=5000 -c lock_timeout=1000'})
            self.resources.callback(self.engine.dispose)
            model_settings = ModelSettings.from_env(env_file)
            client = QdrantClient(url=model_settings.qdrant_url, cloud_inference=True,
                                  timeout=api_settings.chat_qdrant_timeout_seconds)
            self.resources.callback(client.close)
            model_client = GeminiModels(model_settings)
            model_client.client.close()
            model_client.client = genai.Client(api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=int(settings.timeout_seconds * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
            self.resources.callback(model_client.client.close)
            self.processor = IndexingPipeline(model_client, QdrantIndex(client, model_settings))
            self.provider = TracerProvider()  # local: nenhum exportador Langfuse
            self.resources.callback(self.provider.shutdown)
            self.generation = GenerationService(settings, tracer_provider=self.provider)
            self.reranker = LocalReranker(calibration)
        except BaseException:
            self.resources.close()
            raise

    def factory(self, *, progress):
        from training_generation.chat import ChatPipeline, RequestAdmission, AdmittedQueries
        admission = RequestAdmission(6, 12)
        return ChatPipeline(self.engine, self.generation, self.processor.index,
            AdmittedQueries(self.processor.model_client, admission), self.reranker,
            tracer_provider=self.provider, progress=progress, stage_progress=progress, admission=admission)

    def close(self):
        self.resources.close()
