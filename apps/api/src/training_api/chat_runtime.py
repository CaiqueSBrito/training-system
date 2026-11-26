"""Composição dos componentes reais do chat; recursos pertencem ao lifespan."""
from contextlib import ExitStack

from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import create_engine


class ChatRuntime:
    def __init__(self, database_url, env_file='.env'):
        # Imports de domínio só na composição; auth/linhagem podem operar sem modelos.
        from google import genai
        from google.genai import types
        from training_generation import GenerationService, GenerationSettings
        from training_ingestion.indexing import IndexingPipeline
        from training_llm import read_settings
        from training_retrieval import LocalReranker

        values = read_settings(env_file)
        calibration = values.get('CHAT_RERANKER_CALIBRATION', '').strip()
        if not calibration:
            raise ValueError('Defina CHAT_RERANKER_CALIBRATION')
        settings = GenerationSettings.from_env(env_file)
        self.resources = ExitStack()
        try:
            self.engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True,
                connect_args={'connect_timeout': 5, 'options': '-c timezone=UTC'})
            self.resources.callback(self.engine.dispose)
            self.processor = IndexingPipeline.from_env(env_file)
            self.resources.callback(self.processor.index.client.close)
            self.processor.model_client.client.close()
            self.processor.model_client.client = genai.Client(api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))
            self.resources.callback(self.processor.model_client.client.close)
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
            tracer_provider=self.provider, progress=progress, admission=admission)

    def close(self):
        self.resources.close()
