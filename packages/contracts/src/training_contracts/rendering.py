"""Transformação pura de Item original em apresentação HTML segura (E11/E12).

Fontes e proveniência usam sempre o original; renderizar uma vez na fronteira de
saída. Entidades já presentes no original são conteúdo e também são escapadas.
"""
import html
import unicodedata
from training_contracts.geracao import validar_item


def render_item(item):
    def escape(value):
        value=''.join(c for c in value if unicodedata.category(c) not in {'Cf','Cs'}
            and (unicodedata.category(c)!='Cc' or c in {'\n','\t'}))
        return html.escape(value,quote=True)
    data=item.model_dump(mode='json')
    for key in ('front','back','stem','explanation'):
        if key in data:
            data[key]=escape(data[key])
    if 'options' in data:
        data['options']=[escape(value) for value in data['options']]
    for citation in data['citations']:
        citation['evidence']=escape(citation['evidence'])
    return validar_item(data)
