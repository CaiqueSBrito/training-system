"""E11/E12: texto puro, preservando HTML literal; clientes usam textContent.

Remove somente os controles/Cf/Cs já excluídos pelo E11. Não escapa nem
interpreta entidades HTML; original e proveniência mantêm seus dados literais.
"""
import unicodedata
from training_contracts.geracao import validar_item


def clean_text(value):
    """Limpeza de apresentação compartilhada; não interpreta HTML."""
    return ''.join(c for c in value if unicodedata.category(c) not in {'Cf','Cs'}
        and (unicodedata.category(c)!='Cc' or c in {'\n','\t'}))


def render_item(item):
    data=item.model_dump(mode='json')
    for key in ('front','back','stem','explanation'):
        if key in data:
            data[key]=clean_text(data[key])
    if 'options' in data:
        data['options']=[clean_text(value) for value in data['options']]
    for citation in data['citations']:
        citation['evidence']=clean_text(citation['evidence'])
    return validar_item(data)
