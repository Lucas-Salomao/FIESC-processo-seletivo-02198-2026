"""Canonicalização: typos reais do dataset, ordem das regras e guardrails."""

import pytest

from src.etl.canonize import LabelCanonizer, UnknownLabelError

canonizer = LabelCanonizer()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # rótulos diretos
        ("rolamento_inner", "rolamento_inner"),
        ("rolamento_outer_4", "rolamento_outer"),
        ("rolamento_ball_carga_3", "rolamento_ball"),
        ("new_rolamento_comb_2", "rolamento_combination"),
        ("desbalanceado_1parafuso", "desbalanceamento"),
        ("desalinhado_2", "desalinhamento"),
        ("new_falta_fase_0", "falta_fase"),
        ("eccentric_rotor_2_pos_2", "eccentric_rotor"),
        ("ventoinha_adxl_0", "ventoinha"),
        ("correia_2", "correia"),
        ("polia", "polia"),
        ("cocked_rotor_pos_2", "cocked_rotor"),
        # typos reais encontrados no banner.csv
        ("ddesbalanceado_adxl_0", "desbalanceamento"),
        ("dedesbalanceado_adxl_1", "desbalanceamento"),
        ("desbanlanceado_carga_3_2", "desbalanceamento"),
        ("desabalanceado_3", "desbalanceamento"),
        ("new_desabanceado_1", "desbalanceamento"),
        ("cockecocked_adxl_0", "cocked_rotor"),
        ("mortor_desligado_novo", "motor_desligado"),
        ("normla_carga_3_3", "normal"),
        # estados operacionais
        ("normal_carga_3_2", "normal"),
        ("new_baseline", "baseline"),
        ("acelerando", "acelerando"),
        ("motor_desligado", "motor_desligado"),
        ("teste", "teste"),
        ("new_tes", "teste"),
        # ordem das regras: falha vence estado
        ("rolamento_outer_novo_teste", "rolamento_outer"),
        ("normal_novo_teste", "normal"),
    ],
)
def test_canonize(raw: str, expected: str):
    assert canonizer.canonize(raw).name == expected


def test_unknown_label_raises():
    with pytest.raises(UnknownLabelError):
        canonizer.canonize("falha_misteriosa_xyz")


def test_fault_flags():
    assert canonizer.canonize("cocked_rotor").is_fault is True
    assert canonizer.canonize("normal").is_fault is False
    assert canonizer.canonize("motor_desligado").is_fault is False
    assert canonizer.canonize("teste").is_fault is False


def test_all_families_have_display():
    for family in canonizer.families.values():
        assert family.display
