"""ROS·장치 기동 없이 실제 타이머 메서드의 불량 표본 복구를 검증한다."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from gmp_skills.core.rg2_status import status_fields


@pytest.mark.parametrize('invalid', [{}, None, {'busy': 128, 'offset': 2,
                                                'relative_width': 61.8, 'width': 57.8}])
def test_bad_sample_is_skipped_and_next_valid_sample_published(invalid):
    source = Path(__file__).parents[1] / 'gmp_skills/nodes/rg2_status_driver.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'getStatus']

    class Vendor:
        def getStatus(self):
            self.status = self.next_status

    namespace = {'OnRobotRGNode': Vendor, 'status_fields': status_fields,
                 'OnRobotRGInput': SimpleNamespace}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), namespace)
    node = namespace['Rg2StatusDriver']()
    warnings, published = [], []
    node.get_logger = lambda: SimpleNamespace(
        warning=lambda message, **kwargs: warnings.append(message))
    node.status_pub = SimpleNamespace(publish=published.append)
    node.next_status = invalid
    node.getStatus()
    assert len(warnings) == 1
    assert not published
    node.next_status = {'busy': 0, 'offset': 2, 'relative_width': 61.8, 'width': 57.8}
    node.getStatus()
    assert len(published) == 1
    assert published[0].gwdf == 578
