"""Pinned real BGE CPU cross-encoder and a separately versioned calibration set."""
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from threading import RLock
import json
import math

MODEL = 'BAAI/bge-reranker-v2-m3'
REVISION = '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
SCORE_DOMAIN = 'sigmoid-logit:0..1:not-calibrated-probability'
_LOCK = RLock()

# These authored pedagogical references are calibration inputs, never retrieval data.
# No cases or documents from evals/datasets/ressalvas are reused.
CALIBRATION_PAIRS = (
    {'query':'Qual é a função da membrana celular?', 'passage':'A membrana celular delimita a célula e controla a entrada e a saída de substâncias.', 'positive':True},
    {'query':'Qual é a função da membrana celular?', 'passage':'A capital da França é Paris. O rio Sena atravessa a cidade.', 'positive':False},
    {'query':'Como se calcula a área de um triângulo?', 'passage':'A área de um triângulo é o produto da base pela altura dividido por dois.', 'positive':True},
    {'query':'Como se calcula a área de um triângulo?', 'passage':'Uma orquestra sinfônica reúne instrumentos de cordas, sopros e percussão.', 'positive':False},
    {'query':'O que causa as estações do ano?', 'passage':'A inclinação do eixo terrestre combinada com a translação em torno do Sol causa as estações do ano.', 'positive':True},
    {'query':'O que causa as estações do ano?', 'passage':'O pão cresce porque a fermentação das leveduras libera dióxido de carbono na massa.', 'positive':False},
    {'query':'O que significa democracia representativa?', 'passage':'Na democracia representativa, os cidadãos elegem representantes para tomar decisões políticas em seu nome.', 'positive':True},
    {'query':'O que significa democracia representativa?', 'passage':'A evaporação converte água líquida em vapor mediante absorção de energia.', 'positive':False},
)


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class CalibrationError(ValueError):
    """Absent, incompatible, corrupted or unsuccessful real calibration."""


@lru_cache(maxsize=1)
def _model():
    # Called under _LOCK. One model across all service instances in a process.
    import torch
    from sentence_transformers import CrossEncoder
    torch.set_num_threads(min(4, torch.get_num_threads()))
    return CrossEncoder(MODEL, revision=REVISION, device='cpu', max_length=512,
                        trust_remote_code=False)


def _predict(pairs):
    if not pairs: return []
    import torch
    with _LOCK:
        scores = _model().predict(pairs,batch_size=2,activation_fn=torch.nn.Sigmoid(),
                                  show_progress_bar=False,convert_to_numpy=True)
    values = [float(s) for s in scores]
    if len(values)!=len(pairs) or not all(math.isfinite(s) and 0<=s<=1 for s in values):
        raise RuntimeError('Invalid cross-encoder output')
    return values


class LocalReranker:
    def __init__(self, calibration_path):
        try:
            manifest=json.loads(Path(calibration_path).read_text(encoding='utf-8'))
            signature=manifest.pop('manifest_sha256')
            valid=(signature==digest(manifest) and manifest['version']==1 and manifest['status']=='Passou'
                and manifest['model']==MODEL and manifest['revision']==REVISION
                and manifest['score_domain']==SCORE_DOMAIN
                and manifest['dataset_sha256']==digest(CALIBRATION_PAIRS)
                and manifest['samples']==list(CALIBRATION_PAIRS)
                and len(manifest['scores'])==len(CALIBRATION_PAIRS)
                and all(type(s) in (int,float) and math.isfinite(s) and 0<=s<=1 for s in manifest['scores'])
                and max(s for s,p in zip(manifest['scores'],CALIBRATION_PAIRS) if not p['positive'])
                    < manifest['cutoff'] < min(s for s,p in zip(manifest['scores'],CALIBRATION_PAIRS) if p['positive']))
            if not valid: raise ValueError
            self.cutoff=float(manifest['cutoff'])
            self.manifest_sha256=signature
            self.manifest=manifest
        except (OSError,ValueError,KeyError,TypeError):
            raise CalibrationError('Artefato de calibração real ausente ou incompatível') from None

    def score(self, queries, chunks):
        """One score per chunk: max sigmoid relevance over real plan queries."""
        if not queries: raise ValueError('Queries required')
        # Bounded batches inside predict; no additional model loaded for each query.
        scores=_predict([(q,c.text) for c in chunks for q in queries])
        return [max(scores[i*len(queries):(i+1)*len(queries)]) for i in range(len(chunks))]


def calibrate(output_path):
    """One real pass; no retry or threshold guess. Separation failure stops readiness."""
    scores=_predict([(p['query'],p['passage']) for p in CALIBRATION_PAIRS])
    min_positive=min(s for s,p in zip(scores,CALIBRATION_PAIRS) if p['positive'])
    max_negative=max(s for s,p in zip(scores,CALIBRATION_PAIRS) if not p['positive'])
    separated=max_negative<min_positive
    manifest={'version':1,'dataset_id':'t018-bge-calibration-pt-v1','dataset_sha256':digest(CALIBRATION_PAIRS),
        'model':MODEL,'revision':REVISION,'score_domain':SCORE_DOMAIN,
        'samples':list(CALIBRATION_PAIRS),'scores':scores,'status':'Passou' if separated else 'Falhou',
        'cutoff':(max_negative+min_positive)/2 if separated else None,
        'method':'midpoint(max_negative,min_positive); disjoint authored references; initial calibration only',
        'runtime':{'sentence-transformers':version('sentence-transformers'),'torch':version('torch'),
                   'device':'cpu','batch_size':2,'max_length':512}}
    manifest['manifest_sha256']=digest(manifest)
    path=Path(output_path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not separated: raise CalibrationError('Calibração real não separou positivos de negativos')
    return manifest
