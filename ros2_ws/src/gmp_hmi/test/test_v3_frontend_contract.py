"""계약 v1.3 상태·결과 이름이 HMI 정적 자산에 연결됐는지 확인한다."""
from pathlib import Path


HMI_JS = Path(__file__).resolve().parents[1] / 'static/hmi.js'


def test_return_material_labels_are_exposed():
    source = HMI_JS.read_text(encoding='utf-8')

    assert "RETURN_MATERIAL:'초과 원료 반환'" in source
    assert "5:'원료 반환'" in source
    assert "6:'원료 반환 실패'" in source
