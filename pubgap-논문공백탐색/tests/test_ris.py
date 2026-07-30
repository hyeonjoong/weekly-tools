"""RIS 파서 — EndNote/Zotero/Scopus 내보내기 형식.

회귀 배경: 이전 버전은 `detect_format` 이 'ris' 를 돌려주면서도 `parse_ris` 가
아예 정의돼 있지 않아, RIS 파일을 주면 NameError 로 죽었다(사용자에게는 rc 3).
"""

import pytest

from pubgap.records import (
    Article,
    detect_format,
    parse_records,
    parse_ris,
    topics_from_keywords,
)

MESH_RIS = """TY  - JOUR
AU  - Kim, H
AU  - Lee, S
TI  - Slow breathing and sleep quality
JO  - Sleep Med
PY  - 2021
KW  - Sleep
KW  - Heart Rate/*physiology
KW  - Respiration
AN  - 34567890
ER  -

TY  - JOUR
TI  - EEG during sleep onset
T2  - J Sleep Res
Y1  - 2019/03/01/
KW  - *Electroencephalography
KW  - Sleep
DO  - 10.1000/abc
ER  -
"""

AUTHOR_KW_RIS = """TY  - JOUR
TI  - Paper one
JF  - Some Journal
PY  - 2020
KW  - heart rate variability
KW  - slow breathing
AN  - 111
ER  -

TY  - JOUR
TI  - Paper two
JF  - Some Journal
PY  - 2021
KW  - heart rate variability
AN  - 222
ER  -
"""


def test_detect_ris():
    assert detect_format(MESH_RIS) == "ris"
    assert detect_format(AUTHOR_KW_RIS) == "ris"


def test_ris_does_not_crash_via_parse_records():
    # 회귀: 예전엔 NameError: name 'parse_ris' is not defined.
    arts = parse_records(MESH_RIS)
    assert len(arts) == 2
    assert all(isinstance(a, Article) for a in arts)


def test_ris_mesh_mode_fields():
    a, b = parse_ris(MESH_RIS)
    assert a.pmid == "34567890"
    assert a.year == 2021
    assert a.journal == "Sleep Med"
    assert a.title == "Slow breathing and sleep quality"
    # KW 가 MeSH 표기('/*physiology')를 쓰므로 주제로 승격되고 qualifier 는 벗겨진다.
    assert a.mesh == ["Sleep", "Heart Rate", "Respiration"]
    assert a.mesh_major == ["Heart Rate"]
    assert a.pub_types == ["Journal Article"]  # TY: JOUR

    assert b.year == 2019          # Y1 의 '2019/03/01/' 에서 연도 추출
    assert b.journal == "J Sleep Res"  # T2 폴백
    assert b.pmid == "doi:10.1000/abc"  # 숫자 ID 가 없으면 DOI 로 식별
    assert b.mesh_major == ["Electroencephalography"]


def test_ris_author_keywords_stay_keywords():
    arts = parse_ris(AUTHOR_KW_RIS)
    # MeSH 색인 표기가 전혀 없으므로 주제로 올리지 않는다.
    assert all(a.mesh == [] for a in arts)
    assert arts[0].keywords == ["heart rate variability", "slow breathing"]
    # 그러면 코퍼스 단위 폴백이 키워드를 주제로 승격한다.
    promoted, used = topics_from_keywords(arts)
    assert used is True
    assert promoted[0].mesh == ["heart rate variability", "slow breathing"]


def test_ris_mesh_mode_is_corpus_level_not_per_record():
    """한 레코드에만 MeSH 표기가 있어도 코퍼스 전체를 같은 규칙으로 읽어야 한다.

    레코드마다 다르게 해석하면 같은 개념('Sleep')이 어떤 논문에선 주제, 어떤
    논문에선 키워드로 갈려 공동출현 통계가 조용히 깨진다.
    """
    arts = parse_ris(MESH_RIS)
    assert arts[0].mesh and arts[1].mesh  # 둘 다 주제를 가진다
    assert "Sleep" in arts[0].mesh and "Sleep" in arts[1].mesh


def test_ris_missing_er_still_splits_records():
    """ER 이 빠진 내보내기도 다음 TY 를 만나면 레코드를 닫아 복구한다."""
    text = "TY  - JOUR\nTI  - A\nPY  - 2020\nTY  - JOUR\nTI  - B\nPY  - 2021\n"
    arts = parse_ris(text)
    assert [a.title for a in arts] == ["A", "B"]


def test_ris_continuation_lines_are_joined():
    text = (
        "TY  - JOUR\n"
        "TI  - A very long title that the exporter\n"
        "      wrapped onto a second line\n"
        "PY  - 2020\n"
        "ER  -\n"
    )
    a = parse_ris(text)[0]
    assert a.title == "A very long title that the exporter wrapped onto a second line"


def test_ris_variable_spacing_before_dash():
    """도구에 따라 'TY - JOUR'(공백1) 처럼 간격이 흔들린다."""
    text = "TY - JOUR\nTI - Spaced\nPY - 2020\nER -\n"
    assert detect_format(text) == "ris"
    assert parse_ris(text)[0].title == "Spaced"


def test_ris_empty_raises():
    with pytest.raises(ValueError):
        parse_ris("")
    with pytest.raises(ValueError):
        parse_ris("   \n\n")


def test_ris_header_noise_before_first_ty_is_ignored():
    text = "Provider: Some DB\nContent: text/plain\n\nTY  - JOUR\nTI  - A\nER  -\n"
    arts = parse_ris(text)
    assert len(arts) == 1 and arts[0].title == "A"


def test_ris_missing_fields_get_placeholders():
    arts = parse_ris("TY  - JOUR\nER  -\n")
    assert len(arts) == 1
    a = arts[0]
    assert a.pmid == "?" and a.year is None
    assert a.title == "(no title)" and a.journal == "(unknown journal)"


def test_ris_pub_type_from_m3_is_kept():
    text = "TY  - JOUR\nTI  - A\nM3  - Randomized Controlled Trial\nER  -\n"
    a = parse_ris(text)[0]
    assert "Randomized Controlled Trial" in a.pub_types
