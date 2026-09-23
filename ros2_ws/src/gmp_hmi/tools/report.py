#!/usr/bin/env python3
"""기록 DB를 읽기 전용으로 열어 /kpi와 동일한 지표를 출력한다."""
import argparse
import json
from pathlib import Path

from gmp_hmi.core.db import CellDB


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='기록 SQLite 경로')
    parser.add_argument('--start', type=float, help='배치 시작 ROS 시각, 초 (포함)')
    parser.add_argument('--end', type=float, help='배치 시작 ROS 시각, 초 (미만)')
    parser.add_argument('--result')
    parser.add_argument('--query')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.start is not None and args.end is not None and args.start >= args.end:
        parser.error('--end는 --start보다 커야 합니다')
    db = CellDB(str(Path(args.db).expanduser().resolve()), readonly=True)
    try:
        values = db.kpis(start=args.start, end=args.end, result=args.result, query=args.query)
    finally:
        db.close()
    if args.json:
        print(json.dumps(values, ensure_ascii=False, indent=2, allow_nan=False))
        return
    print('지표\t값')
    for key, label, unit in (
        ('batches', '종료 배치', '건'),
        ('batch_success_pct', '계량 검증 완료율', '%'),
        ('unmeasured_done', '미측정 승인 완료', '건'),
        ('run_complete_pct', '실행 완주율', '%'),
        ('auto_recovery_pct', '자동복구율', '%'),
        ('run_time_s', '누적 운전시간', '초'),
        ('forced_interventions', '강제 개입', '회'),
        ('mtbi_s', 'MTBI', '초'),
    ):
        value = values[key]
        print(f'{label}\t' + ('집계 대상 없음' if value is None else f'{value:g} {unit}'))
    print('운전시간은 종료 배치 경과시간의 합입니다. MTBI 정의는 /kpi와 동일합니다.')


if __name__ == '__main__':
    main()
