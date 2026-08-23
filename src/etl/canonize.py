"""Canonicalização de rótulos brutos de falha → famílias canônicas.

Carrega as regras auditáveis de label_map.yaml e resolve qualquer rótulo
bruto do dataset. Rótulo desconhecido levanta exceção — nada é descartado
ou classificado silenciosamente.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

LABEL_MAP_PATH = Path(__file__).parent / "label_map.yaml"


class UnknownLabelError(ValueError):
    """Rótulo bruto sem regra de canonicalização — exige curadoria humana."""


@dataclass(frozen=True)
class Family:
    """Uma família canônica de falha (ou de estado operacional), como declarada em label_map.yaml."""

    name: str
    is_fault: bool
    display: str


class LabelCanonizer:
    """Resolve rótulos brutos do dataset (ex.: 'rolamento_inner_2',
    'ddesbalanceado_adxl_0') para a família canônica correspondente, usando
    as regras declaradas em label_map.yaml."""

    def __init__(self, label_map_path: Path = LABEL_MAP_PATH):
        raw = yaml.safe_load(label_map_path.read_text(encoding="utf-8"))

        # Carrega as famílias declaradas no YAML: nome canônico -> objeto Family.
        self.families: dict[str, Family] = {
            name: Family(name=name, is_fault=cfg["is_fault"], display=cfg["display"])
            for name, cfg in raw["families"].items()
        }

        # Carrega as regras de canonicalização: cada uma associa um padrão
        # regex a uma família. A ORDEM da lista importa — em canonize(), a
        # primeira regra que casar com o rótulo é a que vence.
        self._rules: list[tuple[re.Pattern, str]] = []
        for rule in raw["rules"]:
            family = rule["family"]
            if family not in self.families:
                raise ValueError(f"Regra aponta para família inexistente: {family}")
            self._rules.append((re.compile(rule["pattern"]), family))

    def canonize(self, raw_label: str) -> Family:
        """Resolve um rótulo bruto para a família canônica correspondente.

        Percorre as regras na ordem em que foram declaradas e devolve a
        família da primeira que casar. Se nenhuma casar, levanta
        UnknownLabelError — nunca classifica um rótulo desconhecido "no escuro".
        """
        label = raw_label.strip().lower()
        for pattern, family in self._rules:
            if pattern.search(label):
                return self.families[family]
        raise UnknownLabelError(
            f"Rótulo '{raw_label}' não casa com nenhuma regra de label_map.yaml — "
            "adicione uma regra ou corrija o dado."
        )

    def fault_families(self) -> list[str]:
        """Lista só os nomes das famílias que representam falha real (is_fault=True)."""
        return [f.name for f in self.families.values() if f.is_fault]


@lru_cache
def get_canonizer() -> LabelCanonizer:
    """Devolve a instância única do canonizador (carrega label_map.yaml uma vez só)."""
    return LabelCanonizer()
