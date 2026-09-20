"""The arithmetic around the model, which is where the mistakes hide."""

import datetime as dt

import numpy as np
import pytest

from earshot.classify import Labels, flat_sigmoid, week_of_48
from earshot.config import load

REAL_LABELS = load("/nonexistent").model_dir / "labels.txt"


@pytest.mark.parametrize("date,week", [
    (dt.date(2026, 1, 1), 1),
    (dt.date(2026, 1, 7), 1),
    (dt.date(2026, 1, 8), 2),
    (dt.date(2026, 1, 15), 3),
    (dt.date(2026, 1, 22), 4),
    (dt.date(2026, 1, 31), 4),     # the tail of a long month stays in week 4
    (dt.date(2026, 2, 1), 5),
    (dt.date(2026, 9, 18), 35),
    (dt.date(2026, 12, 31), 48),
])
def test_week_of_48(date, week):
    assert week_of_48(date) == week


def test_week_never_leaves_the_range():
    day = dt.date(2026, 1, 1)
    weeks = {week_of_48(day + dt.timedelta(days=n)) for n in range(366)}
    assert min(weeks) == 1 and max(weeks) == 48
    assert weeks == set(range(1, 49))


def test_flat_sigmoid_matches_birdnets_definition():
    # 1 / (1 + exp(-x)), clipped at +-20 like BirdNET-Analyzer's own.
    assert flat_sigmoid(np.array([0.0]))[0] == pytest.approx(0.5)
    assert flat_sigmoid(np.array([100.0]))[0] == pytest.approx(1.0)
    assert flat_sigmoid(np.array([-100.0]))[0] == pytest.approx(0.0, abs=1e-8)
    assert flat_sigmoid(np.array([2.0]))[0] == pytest.approx(0.880797, abs=1e-5)


def labels_file(tmp_path, lines):
    path = tmp_path / "labels.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf8")
    return path


def test_labels_split_on_the_last_underscore(tmp_path):
    labels = Labels(labels_file(tmp_path, [
        "Haemorhous mexicanus_House Finch",
        "Poecile atricapillus_Black-capped Chickadee",
    ]))
    assert labels.sci == ["Haemorhous mexicanus", "Poecile atricapillus"]
    assert labels.common == ["House Finch", "Black-capped Chickadee"]
    assert labels.index_of_common("House Finch") == 0
    assert labels.index_of_common("Bald Eagle") is None


def test_non_event_classes_are_not_birds(tmp_path):
    labels = Labels(labels_file(tmp_path, [
        "Haemorhous mexicanus_House Finch",
        "Engine_Engine",
        "Human vocal_Human vocal",       # has a space, is not a binomial
        "Power tools_Power tools",
        "Siren_Siren",
        "Zenaida macroura_Mourning Dove",
    ]))
    assert list(labels.is_bird) == [True, False, False, False, False, True]


def test_species_named_like_their_genus_are_kept(tmp_path):
    # Two real classes in v2.4 repeat the binomial on both sides of the
    # underscore. They are crickets, but they are species, and a rule that
    # dropped them would be a rule we do not understand.
    labels = Labels(labels_file(tmp_path, [
        "Gryllus assimilis_Gryllus assimilis",
        "Miogryllus saussurei_Miogryllus saussurei",
        "Noise_Noise",
    ]))
    assert list(labels.is_bird) == [True, True, False]


@pytest.mark.skipif(not REAL_LABELS.is_file(),
                    reason="BirdNET labels not downloaded here")
def test_the_real_label_file_has_6522_classes_and_11_non_events():
    labels = Labels(REAL_LABELS)
    assert len(labels) == 6522
    assert int((~labels.is_bird).sum()) == 11
    assert labels.index_of_common("House Finch") is not None


def test_blank_lines_are_ignored(tmp_path):
    labels = Labels(labels_file(tmp_path, [
        "Haemorhous mexicanus_House Finch", "", "Zenaida macroura_Mourning Dove", ""]))
    assert len(labels) == 2
