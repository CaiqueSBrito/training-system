"""Testes consumer-visible do runner RAGAS real das ressalvas (T-015).

Escritos antes da implementação de `evals/runner_ressalvas.py` (teste primeiro).
Dublês externos são usados exclusivamente na fronteira de rede/LLM (instructor/LiteLLM)
dentro deste arquivo de teste; o pipeline RAGAS 0.4.4 (`llm_factory`, `LiteLLMStructuredLLM`,
`ContextPrecisionWithReference`, `ContextRecall`) e a validação de contratos/manifestos
executam de verdade.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from ragas.llms.litellm_llm import LiteLLMStructuredLLM
from ragas.metrics.collections.context_precision.util import ContextPrecisionOutput
from ragas.metrics.collections.context_recall.util import (
    ContextRecallClassification,
    ContextRecallOutput,
)

from evals.runner_ressalvas import (
    DEFAULT_DATASET_PATH,
    CalibrationManifest,
    DatasetSpec,
    EvalBlockedError,
    EvalConfigError,
    EvalJudgeConfig,
    EvalProviderError,
    EvalThresholds,
    RetrievalCaseRecord,
    RetrievedChunkRef,
    build_evaluation_manifest,
    build_ragas_judge_And_metrics,
    compare_with_baseline,
    execute_isolated_evaluation_or_calibration,
    execute_scope_ingestion_and_retrieval,
    load_dataset_spec,
    main,
    run_judge_calibration,
    run_preflight,
    run_ragas_evaluation,
    validate_calibration_report,
)


class _FakeAsyncCompletions:
    """Fronteira externa dublê para `client.chat.completions.create` do instructor/LiteLLM.

    Permite que `LiteLLMStructuredLLM` e as métricas reais do RAGAS 0.4.4 construam
    os prompts reais, chamem `agenerate(prompt, ResponseModel)` e calculem os scores
    reais do RAGAS sem rede externa.
    """

    def __init__(
        self,
        *,
        high_score: bool = True,
        fail_exc: Exception | None = None,
        discriminate_controls: bool = False,
    ):
        self.high_score = high_score
        self.fail_exc = fail_exc
        self.discriminate_controls = discriminate_controls
        self.calls: list[dict[str, Any]] = []

    async def create(self, *, model: str, messages: list[dict[str, str]], response_model: Any, **kwargs: Any) -> Any:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "response_model": response_model.__name__,
                "kwargs": kwargs,
            }
        )
        if self.fail_exc is not None:
            raise self.fail_exc
        prompt_text = "\n".join(m.get("content", "") for m in messages)
        score_one = self.high_score
        if self.discriminate_controls:
            # Em modo discriminativo para calibração: verifica pelo conteúdo real do corpus
            # (sem nenhum marcador artificial da aplicação) se o campo `context` do prompt JSON
            # do RAGAS corresponde às substrings esperadas da pergunta (`question`).
            spec, _ = load_dataset_spec(DEFAULT_DATASET_PATH)
            for case_spec in spec.cases:
                if case_spec.user_input in prompt_text:
                    # O prompt do RAGAS serializa o input final no último bloco JSON com "context": "..."
                    last_input_section = prompt_text.split(case_spec.user_input)[-1]
                    score_one = all(
                        f"Trecho recuperado verificado para {case_spec.case_id}:" in last_input_section
                        and sub in last_input_section
                        for sub in case_spec.expected_evidence_substrings
                    )
                    break
        if response_model is ContextPrecisionOutput:
            return ContextPrecisionOutput(
                reason="Context evaluated against reference.",
                verdict=1 if score_one else 0,
            )
        if response_model is ContextRecallOutput:
            return ContextRecallOutput(
                classifications=[
                    ContextRecallClassification(
                        statement="Core factual statement from reference.",
                        reason="Verified against retrieved context.",
                        attributed=1 if score_one else 0,
                    )
                ]
            )
        raise AssertionError(f"Modelo de resposta inesperado do RAGAS: {response_model}")


class _FakeAsyncInstructorClient:
    def __init__(self, completions: _FakeAsyncCompletions):
        self.chat = type("_Chat", (), {"completions": completions})()


def _sample_retrieval_records(spec: DatasetSpec) -> list[RetrievalCaseRecord]:
    records: list[RetrievalCaseRecord] = []
    for idx, case in enumerate(spec.cases):
        chunk_text = f"Trecho recuperado verificado para {case.case_id}: " + " ; ".join(
            case.expected_evidence_substrings
        )
        chunk_sha = sha256(chunk_text.encode("utf-8")).hexdigest()
        chunk_refs = tuple(
            RetrievedChunkRef(
                chunk_id=sha256(f"{case.case_id}:{idx}:{p_idx}".encode("utf-8")).hexdigest(),
                document_version_id=f"ver-{case.doc_id}",
                canonical_id=f"can-{case.doc_id}",
                section_path=tuple(sec_path),
                start_offset=0,
                end_offset=len(chunk_text),
                text_sha256=chunk_sha,
                decision_event_id=f"dec-{case.case_id}",
                decision_revision=3,
                input_sha256=sha256(f"in:{chunk_sha}:{p_idx}".encode("utf-8")).hexdigest(),
                span_ids=(f"can-{case.doc_id}:{p_idx}",),
                span_section_paths=(tuple(sec_path),),
                mapping_status="exact",
            )
            for p_idx, sec_path in enumerate(case.expected_section_paths)
        )
        records.append(
            RetrievalCaseRecord(
                case_id=case.case_id,
                doc_id=case.doc_id,
                document_version_id=f"ver-{case.doc_id}",
                canonical_id=f"can-{case.doc_id}",
                retrieved_contexts=tuple(chunk_text for _ in chunk_refs),
                retrieved_chunks=chunk_refs,
            )
        )
    return records


def test_load_canonical_dataset_and_validate_hashes() -> None:
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    assert spec.dataset_id == "ressalvas-recuperacao-segura-v1"
    assert len(dataset_sha) == 64
    assert len(spec.documents) == 3
    assert len(spec.cases) == 6
    assert "faithfulness" in spec.not_applicable_metrics
    assert spec.not_applicable_metrics["faithfulness"].status == "Não se aplica"
    assert spec.security_regression_reference.requirement_id == "SEC-ATQ-001"
    assert len(spec.security_regression_reference.attacks_ids) == 9


def test_invalid_dataset_rejects_tampered_document_hash_and_duplicate_case_ids(tmp_path: Path) -> None:
    raw = json.loads(DEFAULT_DATASET_PATH.read_text(encoding="utf-8"))
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    for doc in raw["documents"]:
        src = DEFAULT_DATASET_PATH.parent / doc["relative_path"]
        (tmp_path / doc["relative_path"]).write_bytes(src.read_bytes())

    # 1. Hash divergente do arquivo markdown real
    bad_hash_data = json.loads(json.dumps(raw))
    bad_hash_data["documents"][0]["sha256"] = "0" * 64
    bad_hash_path = tmp_path / "dataset_bad_hash.json"
    bad_hash_path.write_text(json.dumps(bad_hash_data), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="hash.*divergente|sha256"):
        load_dataset_spec(bad_hash_path)

    # 2. Caso duplicado
    dup_case_data = json.loads(json.dumps(raw))
    dup_case_data["cases"].append(dup_case_data["cases"][0])
    dup_case_path = tmp_path / "dataset_dup_case.json"
    dup_case_path.write_text(json.dumps(dup_case_data), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="duplicado|case_id"):
        load_dataset_spec(dup_case_path)

    # 3. Tentativa de habilitar faithfulness sem geração
    bad_metric_data = json.loads(json.dumps(raw))
    bad_metric_data["applicable_metrics"].append("faithfulness")
    bad_metric_path = tmp_path / "dataset_faithfulness.json"
    bad_metric_path.write_text(json.dumps(bad_metric_data), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="faithfulness|geração"):
        load_dataset_spec(bad_metric_path)

    # 4. Regressão consumer-visible (v1.0.0 -> v1.0.1): expected_evidence_substrings com paráfrase
    # (ex.: "nunca expor chaves de API" em vez do literal "não podem expor chaves de API" presente no doc)
    # é rejeitada deterministicamente por load_dataset_spec sem precisar chamar rede/LLM.
    bad_sub_data = json.loads(json.dumps(raw))
    bad_sub_data["cases"][-1]["expected_evidence_substrings"][0] = "nunca expor chaves de API"
    bad_sub_path = tmp_path / "dataset_non_verbatim_substring.json"
    bad_sub_path.write_text(json.dumps(bad_sub_data), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="expected_evidence_substring.*não existe literalmente"):
        load_dataset_spec(bad_sub_path)


def test_incorrect_judge_config_and_missing_credential_block_cleanly() -> None:
    # Sem EVAL_JUDGE_MODEL
    with pytest.raises(EvalConfigError, match="EVAL_JUDGE_MODEL"):
        EvalJudgeConfig.from_mapping({"EVAL_JUDGE_MODEL": "", "OPENROUTER_API_KEY": "or-secret"})

    # Juiz Gemini proibido para avaliação RAGAS (autoavaliação / ADR-0006)
    with pytest.raises(EvalConfigError, match="openrouter/|Gemini"):
        EvalJudgeConfig.from_mapping(
            {"EVAL_JUDGE_MODEL": "gemini/gemini-3.1-flash-lite", "GEMINI_API_KEY": "gem-secret"}
        )

    # Modelo sem provedor/barra
    with pytest.raises(EvalConfigError, match="EVAL_JUDGE_MODEL"):
        EvalJudgeConfig.from_mapping({"EVAL_JUDGE_MODEL": "gpt-6.1-sol", "OPENROUTER_API_KEY": "or-secret"})

    # EVAL_JUDGE_MODEL válido, mas credencial OPENROUTER_API_KEY ausente -> EvalBlockedError sem expor segredo
    cfg = EvalJudgeConfig.from_mapping(
        {"EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol", "OPENROUTER_API_KEY": ""}
    )
    assert not cfg.has_api_key
    with pytest.raises(EvalBlockedError, match="OPENROUTER_API_KEY"):
        cfg.require_api_key()


def test_real_ragas_metrics_wire_through_llm_factory_and_evaluate_thresholds() -> None:
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": "test-only-openrouter-key-never-logged",
        }
    )
    fake_completions = _FakeAsyncCompletions(high_score=True)
    fake_client = _FakeAsyncInstructorClient(fake_completions)

    llm, metrics = build_ragas_judge_And_metrics(cfg, client=fake_client)
    assert isinstance(llm, LiteLLMStructuredLLM)
    assert llm.is_async is True
    assert set(metrics.keys()) == {"context_precision_with_reference", "context_recall"}

    records = _sample_retrieval_records(spec)
    thresholds = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        min_case_context_precision=0.65,
        min_case_context_recall=0.75,
        approved_by_human=True,
        approval_ref="aprovacao-teste-local",
    )

    report = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        client=fake_client,
        run_id="run-test-high-001",
    )

    # Cada caso roda ContextPrecisionWithReference + ContextRecall pelas classes do RAGAS
    assert len(fake_completions.calls) >= 12
    assert report["status"] == "Passou"
    assert report["global_pipeline_success"] is False
    assert report["not_applicable_metrics"]["faithfulness"]["status"] == "Não se aplica"
    assert report["aggregate_scores"]["context_precision_with_reference"] == pytest.approx(1.0)
    assert report["aggregate_scores"]["context_recall"] == pytest.approx(1.0)
    assert report["deterministic_provenance_hit_rate"] == pytest.approx(1.0)

    # Nenhum texto bruto de documento ou chave pode constar na serialização do relatório
    serialized = json.dumps(report, ensure_ascii=False)
    assert "test-only-openrouter-key-never-logged" not in serialized
    assert "Trecho recuperado verificado" not in serialized
    for case_result in report["case_results"]:
        assert len(case_result["retrieved_context_sha256s"]) >= 1
        assert case_result["scores"]["context_precision_with_reference"] == pytest.approx(1.0)
        assert case_result["scores"]["context_recall"] == pytest.approx(1.0)

    # Score abaixo do limiar deve resultar em status "Falhou"
    low_completions = _FakeAsyncCompletions(high_score=False)
    low_client = _FakeAsyncInstructorClient(low_completions)
    low_report = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        client=low_client,
        run_id="run-test-low-002",
    )
    assert low_report["status"] == "Falhou"
    assert low_report["aggregate_scores"]["context_precision_with_reference"] == pytest.approx(0.0)
    assert low_report["aggregate_scores"]["context_recall"] == pytest.approx(0.0)
    assert low_report["threshold_violations"]


def test_unapproved_thresholds_or_missing_records_block_evaluation() -> None:
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": "test-key",
        }
    )
    records = _sample_retrieval_records(spec)
    unapproved = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        approved_by_human=False,
    )
    with pytest.raises(EvalBlockedError, match="limiares.*aprovados|calibração"):
        run_ragas_evaluation(
            dataset_spec=spec,
            dataset_sha256=dataset_sha,
            judge_config=cfg,
            retrieval_records=records,
            thresholds=unapproved,
            client=_FakeAsyncInstructorClient(_FakeAsyncCompletions()),
        )


def test_baseline_incompatibility_rejected_and_compatible_diff_computed() -> None:
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": "test-key",
        }
    )
    thresholds = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        approved_by_human=True,
        approval_ref="humano-g1",
    )
    records = _sample_retrieval_records(spec)
    client = _FakeAsyncInstructorClient(_FakeAsyncCompletions(high_score=True))

    baseline_report = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        client=client,
        run_id="run-baseline-001",
    )
    candidate_report = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        client=client,
        run_id="run-candidate-002",
    )

    diff = compare_with_baseline(baseline_report, candidate_report, thresholds=thresholds)
    assert diff["status"] == "Passou"
    assert diff["metric_diffs"]["context_precision_with_reference"]["delta"] == pytest.approx(0.0)
    assert diff["metric_diffs"]["context_recall"]["delta"] == pytest.approx(0.0)

    # Incompatibilidade de juiz
    tampered_judge = json.loads(json.dumps(baseline_report))
    tampered_judge["manifest"]["judge_model"] = "openrouter/anthropic/claude-haiku-5.5"
    with pytest.raises(EvalBlockedError, match="incompatível.*judge_model|juiz"):
        compare_with_baseline(tampered_judge, candidate_report, thresholds=thresholds)

    # Incompatibilidade de hash do dataset
    tampered_dataset = json.loads(json.dumps(baseline_report))
    tampered_dataset["manifest"]["dataset_sha256"] = "f" * 64
    with pytest.raises(EvalBlockedError, match="incompatível.*dataset_sha256|dataset"):
        compare_with_baseline(tampered_dataset, candidate_report, thresholds=thresholds)

    # Incompatibilidade de prompts das métricas
    tampered_prompts = json.loads(json.dumps(baseline_report))
    tampered_prompts["manifest"]["metric_prompts_sha256"]["context_recall"] = "e" * 64
    with pytest.raises(EvalBlockedError, match="incompatível.*metric_prompts_sha256|prompt"):
        compare_with_baseline(tampered_prompts, candidate_report, thresholds=thresholds)


def test_provider_failure_is_sanitized_without_secret_or_document_leak() -> None:
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    secret_key = "sk-or-v1-SUPER-SECRET-TOKEN-99999"
    sentinel_text = "CONTEUDO_CONFIDENCIAL_DO_DOCUMENTO_XYZ"
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": secret_key,
        }
    )
    thresholds = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        approved_by_human=True,
        approval_ref="humano-g1",
    )
    records = _sample_retrieval_records(spec)

    class _LeakyProviderException(RuntimeError):
        status_code = 429

    leaky_exc = _LeakyProviderException(
        f"OpenRouter HTTP 429 rate limit for key {secret_key} with payload {sentinel_text}"
    )
    failing_client = _FakeAsyncInstructorClient(_FakeAsyncCompletions(fail_exc=leaky_exc))

    with pytest.raises(EvalProviderError) as exc_info:
        run_ragas_evaluation(
            dataset_spec=spec,
            dataset_sha256=dataset_sha,
            judge_config=cfg,
            retrieval_records=records,
            thresholds=thresholds,
            client=failing_client,
            run_id="run-fail-sanitized",
        )

    err = exc_info.value
    assert err.category == "provider"
    assert err.status_code == 429
    assert err.retryable is True
    assert err.__cause__ is None
    formatted = repr(err) + " | " + str(err)
    assert secret_key not in formatted
    assert sentinel_text not in formatted


def test_cli_help_schema_and_preflight_dry_run_without_llm(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    # 1. --schema exporta JSON Schema válido sem chamar LLM
    rc_schema = main(["--schema"])
    assert rc_schema == 0
    out_schema = capsys.readouterr().out
    parsed_schema = json.loads(out_schema)
    assert "DatasetSpec" in parsed_schema
    assert "EvaluationManifest" in parsed_schema

    # 2. --preflight com env sem OPENROUTER_API_KEY reporta pré-flight e bloqueios claramente, sem score de avaliação
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "GEMINI_API_KEY=rotated-gemini-test-value\n"
        "EVAL_JUDGE_MODEL=openrouter/openai/gpt-6.1-sol\n"
        "OPENROUTER_API_KEY=\n",
        encoding="utf-8",
    )
    out_file = tmp_path / "preflight_report.json"
    rc_pre = main(["--preflight", "--env-file", str(env_file), "--output", str(out_file)])
    assert rc_pre == 0
    out_pre = capsys.readouterr().out
    report = json.loads(out_pre)
    assert out_file.is_file()
    assert report["mode"] == "preflight_dry_run"
    assert report["is_evaluation_score"] is False
    assert report["evaluation_status"] == "Bloqueado"
    assert "OPENROUTER_API_KEY" in " ".join(report["blockers"])
    assert report["credentials_present"]["GEMINI_API_KEY"] is True
    assert report["credentials_present"]["OPENROUTER_API_KEY"] is False
    assert "rotated-gemini-test-value" not in out_pre


def test_execute_scope_ingestion_and_retrieval_against_disposable_postgres_and_qdrant() -> None:
    """Exercita `execute_scope_ingestion_and_retrieval` com Postgres descartável, Qdrant descartável e Docling real."""
    import subprocess
    import time
    from urllib.request import urlopen
    from uuid import uuid4

    from qdrant_client import QdrantClient
    from sqlalchemy import create_engine, text
    from training_ingestion.indexing import IndexingPipeline, QdrantIndex
    from training_lineage import create_lineage_engine, migrate
    from training_llm import InjectionVerdict, ModelSettings

    class _LocalBoundaryModels:
        def __init__(self, settings: ModelSettings) -> None:
            self.settings = settings

        def injection_judge_spec(self) -> dict[str, str]:
            return {
                "provider": "google",
                "model": "fixture-judge",
                "config_version": "cfg-v1",
                "config_sha256": "a" * 64,
                "prompt_version": "prompt-v1",
                "prompt_sha256": "b" * 64,
            }

        def judge_injection(self, value: str) -> InjectionVerdict:
            return InjectionVerdict(injection=False, reason="Material educacional legítimo.")

        def embed_document(self, title: str, section_path: list[str], value: str) -> list[float]:
            # Dublê externo de relevância; a qualidade do embedding real é provada na baseline.
            vector = [0.0] * 3072
            for i, case in enumerate(spec.cases):
                vector[i] = float(sum(sub in value for sub in case.expected_evidence_substrings))
            if not any(vector):
                vector[-1] = 1.0
            return vector

        def embed_query(self, value: str) -> list[float]:
            vector = [0.0] * 3072
            for i, case in enumerate(spec.cases):
                vector[i] = float(value == case.user_input)
            if not any(vector):
                vector[-1] = 1.0
            return vector

    qdrant_name = "test-t015-qdrant-" + uuid4().hex
    subprocess.run(
        [
            "docker",
            "run",
            "--pull=never",
            "--rm",
            "-d",
            "--name",
            qdrant_name,
            "-p",
            "127.0.0.1::6333",
            "-e",
            "QDRANT__TELEMETRY_DISABLED=true",
            "qdrant/qdrant:v1.20.2",
        ],
        check=True,
        capture_output=True,
    )
    admin = create_lineage_engine()
    db_name = "test_t015_" + uuid4().hex
    engine = None
    client = None
    try:
        addr = subprocess.check_output(["docker", "port", qdrant_name, "6333/tcp"], text=True).strip()
        qdrant_url = "http://" + addr
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(qdrant_url + "/readyz", timeout=1) as resp:
                    assert resp.status == 200
                break
            except (OSError, AssertionError):
                if time.monotonic() > deadline:
                    raise RuntimeError("Qdrant descartável T-015 indisponível") from None
                time.sleep(0.2)

        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}" TEMPLATE template0'))
        engine = create_engine(admin.url.set(database=db_name), hide_parameters=True)
        migrate(engine, "head")

        settings = ModelSettings(qdrant_url=qdrant_url, judge_model="gemini/fixture-judge")
        client = QdrantClient(url=qdrant_url, cloud_inference=True)
        index = QdrantIndex(client, settings)
        processor = IndexingPipeline(_LocalBoundaryModels(settings), index)

        spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
        records = execute_scope_ingestion_and_retrieval(
            spec,
            dataset_dir=DEFAULT_DATASET_PATH.parent,
            engine=engine,
            processor=processor,
        )
        assert len(records) == len(spec.cases) == 6
        for rec in records:
            assert len(rec.retrieved_contexts) >= 1
            assert len(rec.retrieved_chunks) == len(rec.retrieved_contexts)
            for chunk_ref in rec.retrieved_chunks:
                assert len(chunk_ref.text_sha256) == 64
                assert len(chunk_ref.input_sha256) == 64
                assert chunk_ref.decision_revision >= 1

        cfg = EvalJudgeConfig.from_mapping(
            {
                "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
                "OPENROUTER_API_KEY": "test-only-key",
            }
        )
        thresholds = EvalThresholds(
            context_precision_with_reference=0.80,
            context_recall=0.85,
            approved_by_human=True,
            approval_ref="aprovacao-teste-integrado",
        )
        eval_report = run_ragas_evaluation(
            dataset_spec=spec,
            dataset_sha256=dataset_sha,
            judge_config=cfg,
            retrieval_records=records,
            thresholds=thresholds,
            client=_FakeAsyncInstructorClient(_FakeAsyncCompletions(high_score=True)),
            run_id="run-e2e-disposable-001",
        )
        assert eval_report["status"] == "Passou", eval_report["threshold_violations"]
        assert len(eval_report["case_results"]) == 6
    finally:
        if client is not None:
            client.close()
        if engine is not None:
            engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin.dispose()
        subprocess.run(["docker", "rm", "-f", qdrant_name], check=True, capture_output=True)


def test_r1_finding_1_deterministic_provenance_coverage_gates_pass_status() -> None:
    """Achado 1 (R1): mesmo com scores LLM = 1.0, ausência de substring esperada, section_path esperado
    ou proveniência canônica válida deve reprovar o caso e o relatório (`status == 'Falhou'`).
    """
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": "test-key",
        }
    )
    thresholds = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        approved_by_human=True,
        approval_ref="humano-r1",
    )
    client = _FakeAsyncInstructorClient(_FakeAsyncCompletions(high_score=True))

    # 1a. Substring de evidência ausente no caso 0
    records_missing_sub = _sample_retrieval_records(spec)
    first = records_missing_sub[0]
    bad_text = "Texto sem as substrings esperadas pelo gabarito."
    bad_sha = sha256(bad_text.encode("utf-8")).hexdigest()
    records_missing_sub[0] = RetrievalCaseRecord(
        case_id=first.case_id,
        doc_id=first.doc_id,
        document_version_id=first.document_version_id,
        canonical_id=first.canonical_id,
        retrieved_contexts=tuple(bad_text for _ in first.retrieved_chunks),
        retrieved_chunks=tuple(
            RetrievedChunkRef(
                chunk_id=c.chunk_id,
                document_version_id=c.document_version_id,
                canonical_id=c.canonical_id,
                section_path=c.section_path,
                start_offset=0,
                end_offset=len(bad_text),
                text_sha256=bad_sha,
                decision_event_id=c.decision_event_id,
                decision_revision=c.decision_revision,
                input_sha256=c.input_sha256,
                span_ids=c.span_ids,
                span_section_paths=c.span_section_paths,
                mapping_status="exact",
            )
            for c in first.retrieved_chunks
        ),
    )
    report_sub = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records_missing_sub,
        thresholds=thresholds,
        client=client,
        run_id="run-r1-missing-sub",
    )
    assert report_sub["aggregate_scores"]["context_precision_with_reference"] == pytest.approx(1.0)
    assert report_sub["aggregate_scores"]["context_recall"] == pytest.approx(1.0)
    assert report_sub["deterministic_provenance_hit_rate"] < 1.0
    assert report_sub["status"] == "Falhou"
    assert any("deterministic_provenance" in v or first.case_id in v for v in report_sub["threshold_violations"])

    # 1b. Section path esperado ausente nos spans/chunks do caso 0
    records_missing_path = _sample_retrieval_records(spec)
    first_p = records_missing_path[0]
    records_missing_path[0] = RetrievalCaseRecord(
        case_id=first_p.case_id,
        doc_id=first_p.doc_id,
        document_version_id=first_p.document_version_id,
        canonical_id=first_p.canonical_id,
        retrieved_contexts=first_p.retrieved_contexts,
        retrieved_chunks=tuple(
            RetrievedChunkRef(
                chunk_id=c.chunk_id,
                document_version_id=c.document_version_id,
                canonical_id=c.canonical_id,
                section_path=("Seção Inexistente",),
                start_offset=c.start_offset,
                end_offset=c.end_offset,
                text_sha256=c.text_sha256,
                decision_event_id=c.decision_event_id,
                decision_revision=c.decision_revision,
                input_sha256=c.input_sha256,
                span_ids=c.span_ids,
                span_section_paths=(("Seção Inexistente",),),
                mapping_status="exact",
            )
            for c in first_p.retrieved_chunks
        ),
    )
    report_path = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records_missing_path,
        thresholds=thresholds,
        client=client,
        run_id="run-r1-missing-path",
    )
    assert report_path["deterministic_provenance_hit_rate"] < 1.0
    assert report_path["status"] == "Falhou"
    assert any("section_path" in v for v in report_path["threshold_violations"])


def test_r1_finding_2_failed_baseline_diff_fails_overall_status_and_cli_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Achado 2 (R1): quando o candidato passa nos limiares absolutos (ex.: 0.90 >= 0.85),
    mas cai além da tolerância em relação à baseline (ex.: baseline 1.00 -> candidato 0.90,
    queda -0.10 > 0.02), o relatório consolidado deve ter `status == 'Falhou'` e o CLI deve sair com 1,
    preservando `candidate_status == 'Passou'` e os manifestos.
    """
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": "test-key",
        }
    )
    thresholds = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        approved_by_human=True,
        approval_ref="humano-r1",
    )
    records = _sample_retrieval_records(spec)

    # Calibração válida para vincular à baseline
    calib_client = _FakeAsyncInstructorClient(
        _FakeAsyncCompletions(high_score=True, discriminate_controls=True)
    )
    calib_report = run_judge_calibration(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        client=calib_client,
        run_id="calib-r1-001",
    )
    assert calib_report["status"] == "Passou"
    calib_file = tmp_path / "calibration.json"
    calib_file.write_text(json.dumps(calib_report), encoding="utf-8")

    # Baseline com score 1.00
    baseline_report = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        calibration_report=calib_report,
        client=_FakeAsyncInstructorClient(_FakeAsyncCompletions(high_score=True)),
        run_id="baseline-100",
    )
    assert baseline_report["status"] == "Passou"
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(baseline_report), encoding="utf-8")

    # Candidato com score 0.90 em recall (9 atribuídos de 10 afirmações -> 0.90 >= 0.85, logo candidate_status == Passou,
    # mas delta = -0.10 < -0.02 -> baseline_diff Falhou!)
    class _SlightlyLowerRecallCompletions(_FakeAsyncCompletions):
        async def create(self, *, model: str, messages: list[dict[str, str]], response_model: Any, **kwargs: Any) -> Any:
            if response_model is ContextPrecisionOutput:
                return ContextPrecisionOutput(reason="ok", verdict=1)
            if response_model is ContextRecallOutput:
                return ContextRecallOutput(
                    classifications=[
                        ContextRecallClassification(statement=f"s{i}", reason="ok", attributed=1 if i < 9 else 0)
                        for i in range(10)
                    ]
                )
            raise AssertionError()

    cand_client = _FakeAsyncInstructorClient(_SlightlyLowerRecallCompletions())
    cand_report = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        calibration_report=calib_report,
        baseline_report=baseline_report,
        client=cand_client,
        run_id="candidate-090",
    )
    assert cand_report["candidate_status"] == "Passou"
    assert cand_report["baseline_diff"]["status"] == "Falhou"
    assert cand_report["status"] == "Falhou"

    # Verifica também via main(["--execute", ...]) que o exit code é 1
    env_file = tmp_path / ".env.exec"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    env_file.write_text(
        "GEMINI_API_KEY=test-gem\n"
        "EVAL_JUDGE_MODEL=openrouter/openai/gpt-6.1-sol\n"
        "OPENROUTER_API_KEY=test-or\n",
        encoding="utf-8",
    )
    out_file = tmp_path / "cli_cand_out.json"
    monkeypatch.setattr(
        "evals.runner_ressalvas.execute_isolated_evaluation_or_calibration",
        lambda **kwargs: cand_report,
    )
    rc = main(
        [
            "--execute",
            "--env-file",
            str(env_file),
            "--approval-ref",
            "humano-r1",
            "--calibration-report",
            str(calib_file),
            "--baseline-report",
            str(baseline_file),
            "--output",
            str(out_file),
        ]
    )
    capsys.readouterr()
    assert rc == 1
    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert written["candidate_status"] == "Passou"
    assert written["baseline_diff"]["status"] == "Falhou"
    assert written["status"] == "Falhou"


def test_r1_finding_3_partial_failure_cleans_up_disposable_resources_and_surfaces_cleanup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Achado 3 (R1): falha parcial no meio da ingestão/recuperação ou antes do bloco principal
    deve limpar integralmente o Qdrant descartável e o banco temporário, nunca usar `except Exception: pass`
    e propagar erro caso o teardown falhe.
    """
    spec, _ = load_dataset_spec(DEFAULT_DATASET_PATH)
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": "test-key",
        }
    )
    thresholds = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        approved_by_human=True,
        approval_ref="humano-r1",
    )

    events: list[str] = []

    class _DummyResource:
        def __init__(self, name: str, *, fail_close: bool = False) -> None:
            self.name = name
            self.fail_close = fail_close

        def close(self) -> None:
            events.append(f"close:{self.name}")
            if self.fail_close:
                raise RuntimeError(f"falha_fechamento_{self.name}")

        def dispose(self) -> None:
            events.append(f"dispose:{self.name}")

    # 3a. Falha parcial após o primeiro documento ingerido: container Qdrant descartável e DB temporário são removidos
    def _fake_run_in_disposable(**kwargs: Any) -> Any:
        events.append("start_qdrant_container")
        events.append("create_temp_db")
        try:
            raise EvalBlockedError("Falha simulada após 1o documento na ingestão")
        finally:
            events.append("drop_temp_db")
            events.append("remove_qdrant_container")

    with pytest.raises(EvalBlockedError, match="Falha simulada"):
        execute_isolated_evaluation_or_calibration(
            mode="calibrate",
            dataset_spec=spec,
            dataset_sha256="a" * 64,
            dataset_dir=DEFAULT_DATASET_PATH.parent,
            env_file=Path(".env"),
            judge_config=cfg,
            thresholds=thresholds,
            sandbox_runner=_fake_run_in_disposable,
        )
    assert events == [
        "start_qdrant_container",
        "create_temp_db",
        "drop_temp_db",
        "remove_qdrant_container",
    ]


def test_r1_finding_3_real_sandbox_teardown_on_partial_ingestion_and_no_silent_except_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica diretamente o gerenciador real de sandbox descartável quando ocorre falha após criar recursos."""
    from evals.runner_ressalvas import _disposable_evaluation_sandbox

    cleanup_steps: list[str] = []

    class _FakeConn:
        def __enter__(self) -> _FakeConn:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execution_options(self, **kwargs: Any) -> _FakeConn:
            return self

        def execute(self, stmt: Any) -> None:
            sql = str(stmt)
            if "CREATE DATABASE" in sql:
                cleanup_steps.append("db_created")
            elif "DROP DATABASE" in sql:
                cleanup_steps.append("db_dropped")

    class _FakeAdmin:
        class url:
            @staticmethod
            def set(database: str) -> str:
                return f"postgresql:///{database}"

        def connect(self) -> _FakeConn:
            return _FakeConn()

        def dispose(self) -> None:
            cleanup_steps.append("admin_disposed")

    class _FakeEngine:
        def dispose(self) -> None:
            cleanup_steps.append("engine_disposed")

    monkeypatch.setattr("evals.runner_ressalvas._start_disposable_qdrant_container", lambda: ("qdrant-test-1", "http://127.0.0.1:6333"))
    monkeypatch.setattr(
        "evals.runner_ressalvas._stop_disposable_qdrant_container",
        lambda name: cleanup_steps.append(f"qdrant_removed:{name}"),
    )
    monkeypatch.setattr("training_lineage.create_lineage_engine", lambda: _FakeAdmin())
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr("training_lineage.migrate", lambda *a, **k: cleanup_steps.append("migrated"))

    with pytest.raises(RuntimeError, match="partial_ingest_boom"):
        with _disposable_evaluation_sandbox(env_file=Path(".env"), init_pipeline=False):
            cleanup_steps.append("inside_sandbox_before_crash")
            raise RuntimeError("partial_ingest_boom")

    assert cleanup_steps == [
        "db_created",
        "migrated",
        "inside_sandbox_before_crash",
        "engine_disposed",
        "db_dropped",
        "admin_disposed",
        "qdrant_removed:qdrant-test-1",
    ]


def test_r1_finding_4_non_finite_or_out_of_domain_scores_rejected() -> None:
    """Achado 4 (R1): scores `inf`, `-inf`, `< 0.0`, `> 1.0` ou `bool` devem ser rejeitados com
    `EvalProviderError('invalid_output')`, sem clamp silencioso.
    """
    from evals.runner_ressalvas import _ascore_case_metrics
    import asyncio

    class _StubMetric:
        def __init__(self, value: Any) -> None:
            self._value = value

        async def ascore(self, **kwargs: Any) -> Any:
            return type("_Res", (), {"value": self._value})()

    for invalid_value in (
        float("inf"),
        float("-inf"),
        float("nan"),
        -0.0001,
        1.0001,
        1.5,
        -1.0,
        True,
        False,
        None,
        "0.9",
    ):
        metrics = {
            "context_precision_with_reference": _StubMetric(invalid_value),
            "context_recall": _StubMetric(0.9),
        }
        with pytest.raises(EvalProviderError) as exc_info:
            asyncio.run(
                _ascore_case_metrics(
                    metrics,
                    user_input="pergunta válida de teste",
                    reference="referência válida de teste",
                    retrieved_contexts=["contexto válido"],
                )
            )
        assert exc_info.value.category == "invalid_output"


def test_r1_finding_5_executable_calibration_controls_and_baseline_manifest_binding() -> None:
    """Achado 5 (R1): `run_judge_calibration` executa controles positivos (contexto correto)
    e controles negativos (contexto deslocado de outro documento) com o juiz RAGAS real.
    Se o juiz não discriminar controles negativos (ex.: retornar 1.0 para contexto deslocado),
    a calibração falha fechada (`status == 'Falhou'`). A criação de baseline exige manifesto
    de calibração aprovado e compatível.
    """
    spec, dataset_sha = load_dataset_spec(DEFAULT_DATASET_PATH)
    cfg = EvalJudgeConfig.from_mapping(
        {
            "EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
            "OPENROUTER_API_KEY": "test-key",
        }
    )
    thresholds = EvalThresholds(
        context_precision_with_reference=0.80,
        context_recall=0.85,
        approved_by_human=True,
        approval_ref="humano-r1",
    )
    records = _sample_retrieval_records(spec)

    # 5a. Juiz leniente que dá 1.0 inclusive para controles negativos deslocados -> calibração REPROVA
    indiscriminate_client = _FakeAsyncInstructorClient(
        _FakeAsyncCompletions(high_score=True, discriminate_controls=False)
    )
    failed_calib = run_judge_calibration(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        client=indiscriminate_client,
        run_id="calib-fail-indiscriminate",
    )
    assert failed_calib["status"] == "Falhou"
    assert failed_calib["negative_controls_passed"] is False

    # Tentar usar calibração reprovada na baseline -> EvalBlockedError
    with pytest.raises(EvalBlockedError, match="(?i)calibração.*Falhou|calibração.*aprovada"):
        run_ragas_evaluation(
            dataset_spec=spec,
            dataset_sha256=dataset_sha,
            judge_config=cfg,
            retrieval_records=records,
            thresholds=thresholds,
            calibration_report=failed_calib,
            require_calibration=True,
            client=indiscriminate_client,
        )

    # 5b. Juiz discriminativo (1.0 nos controles positivos e 0.0 nos controles negativos deslocados) -> calibração PASSA
    discriminating_client = _FakeAsyncInstructorClient(
        _FakeAsyncCompletions(high_score=True, discriminate_controls=True)
    )
    passed_calib = run_judge_calibration(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        client=discriminating_client,
        run_id="calib-pass-001",
    )
    assert passed_calib["status"] == "Passou"
    assert passed_calib["positive_controls_passed"] is True
    assert passed_calib["negative_controls_passed"] is True
    assert len(passed_calib["positive_controls"]) == len(spec.cases)
    assert len(passed_calib["negative_controls"]) == len(spec.cases)

    # 5b.1. Regressão consumer-visible: o RAGAS recebe exatamente os bytes originais do contexto
    # deslocado de outro documento, SEM qualquer marcador/rótulo como [CONTROLE_NEGATIVO_DESLOCADO...]
    discriminating_prompts = [
        "\n".join(m.get("content", "") for m in call["messages"])
        for call in discriminating_client.chat.completions.calls
    ]
    assert not any("CONTROLE_NEGATIVO" in p or "negative_control" in p for p in discriminating_prompts)
    records_by_case = {r.case_id: r for r in records}
    for neg_ctrl in passed_calib["negative_controls"]:
        assert neg_ctrl["control_kind"] == "negative_displaced_document"
        assert neg_ctrl["source_doc_id"] != neg_ctrl["displaced_doc_id"]
        displaced_rec = records_by_case[neg_ctrl["displaced_case_id"]]
        expected_shas = [
            sha256(ctx.encode("utf-8")).hexdigest() for ctx in displaced_rec.retrieved_contexts
        ]
        assert neg_ctrl["displaced_context_sha256s"] == expected_shas
        for raw_ctx in displaced_rec.retrieved_contexts:
            assert any(raw_ctx in p for p in discriminating_prompts)

    # 5c. Baseline vinculada ao manifesto de calibração compatível -> PASSA e grava calibration_manifest_sha256
    baseline_report = run_ragas_evaluation(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=thresholds,
        calibration_report=passed_calib,
        require_calibration=True,
        client=discriminating_client,
        run_id="baseline-bound-001",
    )
    assert baseline_report["status"] == "Passou"
    assert baseline_report["manifest"]["calibration_run_id"] == "calib-pass-001"
    assert len(baseline_report["manifest"]["calibration_manifest_sha256"]) == 64
    assert (
        baseline_report["manifest"]["calibration_protocol_sha256"]
        == passed_calib["manifest"]["calibration_protocol_sha256"]
    )

    # 5d. Adulterar judge_config_sha256 na calibração invalida o vínculo com a baseline
    tampered_calib = json.loads(json.dumps(passed_calib))
    tampered_calib["manifest"]["judge_config_sha256"] = "0" * 64
    with pytest.raises(EvalBlockedError, match="(?i)calibração.*incompatível"):
        run_ragas_evaluation(
            dataset_spec=spec,
            dataset_sha256=dataset_sha,
            judge_config=cfg,
            retrieval_records=records,
            thresholds=thresholds,
            calibration_report=tampered_calib,
            require_calibration=True,
            client=discriminating_client,
        )

    # 5e. Calibração executada com limiares mais frouxos (mesmo approval_ref) deve ser REJEITADA
    # quando a baseline exige os limiares padrão (0.80 / 0.85)
    looser_thresholds = EvalThresholds(
        context_precision_with_reference=0.50,
        context_recall=0.50,
        min_case_context_precision=0.50,
        min_case_context_recall=0.50,
        approved_by_human=True,
        approval_ref="humano-r1",
    )
    looser_calib = run_judge_calibration(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=cfg,
        retrieval_records=records,
        thresholds=looser_thresholds,
        client=discriminating_client,
        run_id="calib-looser-001",
    )
    assert looser_calib["status"] == "Passou"
    assert (
        looser_calib["manifest"]["calibration_protocol_sha256"]
        != passed_calib["manifest"]["calibration_protocol_sha256"]
    )
    with pytest.raises(
        EvalBlockedError,
        match="(?i)calibração.*incompatível.*(protocol_thresholds|calibration_protocol_sha256)",
    ):
        run_ragas_evaluation(
            dataset_spec=spec,
            dataset_sha256=dataset_sha,
            judge_config=cfg,
            retrieval_records=records,
            thresholds=thresholds,
            calibration_report=looser_calib,
            require_calibration=True,
            client=discriminating_client,
        )

    # 5f. Incompatibilidade de ragas_version ou calibration_protocol_sha256 entre baseline e candidato é rejeitada
    tampered_ragas_ver = json.loads(json.dumps(baseline_report))
    tampered_ragas_ver["manifest"]["ragas_version"] = "0.3.99"
    with pytest.raises(EvalBlockedError, match="(?i)Baseline incompatível.*ragas_version"):
        compare_with_baseline(tampered_ragas_ver, baseline_report, thresholds=thresholds)

    tampered_proto_sha = json.loads(json.dumps(baseline_report))
    tampered_proto_sha["manifest"]["calibration_protocol_sha256"] = "1" * 64
    with pytest.raises(EvalBlockedError, match="(?i)Baseline incompatível.*calibration_protocol_sha256"):
        compare_with_baseline(tampered_proto_sha, baseline_report, thresholds=thresholds)


def test_resumable_ingestion_retry_recovers_on_retryable_503_and_fails_on_non_retryable() -> None:
    """Verifica que `_ingest_document_with_resumable_retries` retoma sobre `IngestionStageError`
    com `retryable=True` (ex.: HTTP 503 transitório) e falha imediatamente com `EvalProviderError`
    sanitizado quando `retryable=False`.
    """
    from evals.runner_ressalvas import _ingest_document_with_resumable_retries
    from training_ingestion.protocol import IngestionStageError

    class _FlakyIngestor:
        def __init__(self, *, retryable: bool, succeed_on_attempt: int) -> None:
            self.retryable = retryable
            self.succeed_on_attempt = succeed_on_attempt
            self.calls = 0

        def ingest(self, path: Path, owner_id: str, *, document_id: str) -> str:
            self.calls += 1
            if self.calls < self.succeed_on_attempt:
                raise IngestionStageError("provider", status_code=503 if self.retryable else 400, retryable=self.retryable)
            return f"ok:{document_id}:attempt={self.calls}"

    flaky_ok = _FlakyIngestor(retryable=True, succeed_on_attempt=2)
    res = _ingest_document_with_resumable_retries(
        flaky_ok,
        Path("dummy.md"),
        "owner-1",
        document_id="doc-1",
        max_attempts=3,
        backoff_seconds=0.0,
    )
    assert res == "ok:doc-1:attempt=2"
    assert flaky_ok.calls == 2

    non_retryable = _FlakyIngestor(retryable=False, succeed_on_attempt=2)
    with pytest.raises(EvalProviderError) as exc_info:
        _ingest_document_with_resumable_retries(
            non_retryable,
            Path("dummy.md"),
            "owner-1",
            document_id="doc-1",
            max_attempts=3,
            backoff_seconds=0.0,
        )
    assert non_retryable.calls == 1
    assert exc_info.value.status_code == 400
    assert exc_info.value.retryable is False




