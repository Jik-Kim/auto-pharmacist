"""CLI를 별도 프로세스로 실행하고 DB 조회 결과·원본 보존을 확인한다."""
import json
from pathlib import Path
import subprocess
import sys

from gmp_hmi.core.db import CellDB


def test_report_matches_kpi_without_writing(tmp_path):
    package = Path(__file__).parents[1]
    path = tmp_path / 'cell.db'
    db = CellDB(str(path), str(package / 'config/schema.sql'))
    db.start_batch('B1', 100, 'recipe')
    db.finish_batch('B1', 120, 'DONE')
    expected = db.kpis(start=100, end=121)
    db.close()
    original = path.read_bytes()
    result = subprocess.run([sys.executable, str(package / 'tools/report.py'),
                             '--db', str(path), '--start', '100', '--end', '121', '--json'],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected
    assert path.read_bytes() == original
