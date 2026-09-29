"""stations.yaml의 스테이션 간 이송 경로와 실제 출발 자세를 비교한다.

TCP 자세(posx)는 끝점의 XYZ·회전, 관절 자세(posj)는 각 관절각이다.
같은 TCP에도 여러 관절 자세가 가능하므로 양쪽을 따로 확인한다.
이 파일은 값만 검사하며 ROS나 로봇 장치를 호출하지 않는다.
"""
from dataclasses import dataclass
import math


def vector6(value, name):
    """TCP 자세나 6개 관절각을 유한한 숫자 6개로 검증해 반환한다."""
    if (not isinstance(value, (list, tuple)) or len(value) != 6
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in value)):
        raise ValueError(f'{name}: 유한한 숫자 6개가 필요하다')
    return tuple(float(v) for v in value)


def _rotation(pose):
    # 두산 posx의 회전각 3개는 Z-Y-Z Euler 각이다. 같은 회전을 여러 각도
    # 조합으로 표현할 수 있어 각도끼리 빼지 않고 회전 행렬로 변환한다.
    a, b, c = map(math.radians, pose[3:])
    ca, sa, cb, sb, cc, sc = (math.cos(a), math.sin(a), math.cos(b),
                             math.sin(b), math.cos(c), math.sin(c))
    return ((ca*cb*cc-sa*sc, -ca*cb*sc-sa*cc, ca*sb),
            (sa*cb*cc+ca*sc, -sa*cb*sc+ca*cc, sa*sb),
            (-sb*cc, sb*sc, cb))


def pose_matches(actual, target, xyz_mm, rotation_deg):
    """현재 TCP의 XYZ 차이와 실제 회전 차이가 각각 허용오차 이내인지 확인한다."""
    actual, target = vector6(actual, '현재 posx'), vector6(target, '목표 posx')
    if max(abs(a-b) for a, b in zip(actual[:3], target[:3])) > xyz_mm:
        return False
    ra, rb = _rotation(actual), _rotation(target)
    cosine = (sum(a*b for row_a, row_b in zip(ra, rb)
                  for a, b in zip(row_a, row_b)) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) <= rotation_deg


def joints_match(actual, target, tolerance_deg):
    # 관절각 0°와 360°는 TCP 방향이 같아도 케이블 감김은 다를 수 있다.
    # 따라서 360°를 지우지 않고 티칭한 각도와 직접 비교한다.
    return max(abs(a-b) for a, b in zip(vector6(actual, '현재 posj'),
                                      vector6(target, '목표 posj'))) <= tolerance_deg


def wrist_flipped(joints):
    """같은 TCP 를 손목만 뒤집어 만드는 관절 자세 — J4·J6 을 같은 방향으로 180° 돌리고 J5 부호를 바꾼다.

    J4 와 J6 을 반대 방향으로 돌린 해는 J6 이 360° 다르게 감긴 것이라 포함하지 않는다.
    """
    j = vector6(joints, '목표 posj')
    return [(j[0], j[1], j[2], j[3] + turn, -j[4], j[5] + turn) for turn in (180.0, -180.0)]


def joints_match_or_wrist_flipped(actual, target, tolerance_deg):
    # 9/29: 작업대는 J5 < 0, 원료통 쪽은 J5 > 0 으로 티칭돼 있어 붓기를 다녀온 뒤의 movel 연쇄는
    # 같은 TCP 에 손목만 뒤집힌 해로 도착한다(A 계량 자세 [-46.0, 7.6, 84.2, -0.05, 88.2, -225.8] →
    # [-46.0, 7.6, 84.6, 179.95, -87.8, -45.8]). 반환 끝 확인이 이 해를 거부해 배치 안 반환이 2/2 실패했다.
    return joints_match(actual, target, tolerance_deg) or any(
        joints_match(actual, flipped, tolerance_deg) for flipped in wrist_flipped(target))


@dataclass(frozen=True)
class MotionAnchor:
    """마지막으로 확인한 스테이션·AT/ABOVE·TCP·관절각의 출발 기록."""
    station: str
    approach: int
    pose: tuple
    joints: tuple


@dataclass(frozen=True)
class TransferRoute:
    """출발점, 이탈점, 중간 관절점, 필요한 파지물과 도착 방식을 담은 경로."""
    source: str
    destination: str
    payload: str
    enabled: bool
    # 과거 티칭 기록. 이동 출발 조건으로 비교하지 않는다.
    start_at_posj: tuple = ()
    start_above_posj: tuple = ()
    exit_posx: tuple = ()
    exit_posj: tuple = ()
    waypoints_posj: tuple = ()
    source_at_posx: tuple = ()
    source_above_posx: tuple = ()
    start_from: str = 'at_or_above'
    arrival: str = 'above'


def parse_routes(rows, stations, approach_mm=60.0):
    """YAML의 transfers 목록을 검증해 (출발, 도착)별 경로로 만든다."""
    if not isinstance(rows, list):
        raise ValueError('transfers는 목록이어야 한다')
    routes = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('이송 경로는 매핑이어야 한다')
        src, dst = row.get('source'), row.get('destination')
        if src not in stations or dst not in stations or src == dst:
            raise ValueError('이송 출발·도착 스테이션을 확인해야 한다')
        key = (src, dst)
        if key in routes:
            raise ValueError(f'중복 이송 경로: {key}')
        enabled, payload = row.get('enabled'), row.get('payload')
        if type(enabled) is not bool or payload not in ('empty', 'cup'):
            raise ValueError(f'{key}: enabled(bool)와 payload(empty/cup)가 필요하다')
        if row.get('source_pose_key', 'posx') != 'posx':
            raise ValueError(f'{key}: 출발 기준은 station.posx로 통합해야 한다')
        source = stations[src]
        start_from, arrival = row.get('start_from', 'at_or_above'), row.get('arrival', 'above')
        if start_from not in ('at_or_above', 'above', 'exit') or arrival not in ('above', 'at'):
            raise ValueError(f'{key}: start_from/arrival 설정을 확인해야 한다')
        values = {'source_at_posx': tuple(source.offset_z(0)),
                  'source_above_posx': tuple(source.above(approach_mm)),
                  'start_from': start_from, 'arrival': arrival}
        if 'exit_offset_mm' in row:
            if row.get('exit_posx') is not None:
                raise ValueError(f'{key}: exit_offset_mm와 절대 exit_posx는 동시에 지정할 수 없다')
            values['exit_posx'] = tuple(source.offset_z(row['exit_offset_mm']))
        elif row.get('exit_posx') is None:
            exit_key = 'exit_mm'
            if exit_key in source.extra:
                values['exit_posx'] = tuple(source.exit())
        for name in ('start_at_posj', 'start_above_posj', 'exit_posj', 'exit_posx'):
            if name in values:
                continue
            if row.get(name) is not None:
                values[name] = vector6(row.get(name), f'{key}.{name}')
            elif enabled and name == 'exit_posx':
                raise ValueError(f'{key}.{name}: 티칭값이 필요하다')
        points = row.get('waypoints_posj', [])
        if not isinstance(points, list) or (enabled and not points):
            raise ValueError(f'{key}: 도착 {arrival.upper()} 관절각을 포함한 waypoints_posj가 필요하다')
        values['waypoints_posj'] = tuple(vector6(p, 'waypoint') for p in points)
        if values.get('exit_posx'):
            if not pose_matches(values['exit_posx'], values['source_at_posx'], float('inf'), 0.001):
                raise ValueError(f'{key}: 직선 이탈 중 출발 자세를 유지해야 한다')
        routes[key] = TransferRoute(source=src, destination=dst, payload=payload,
                                    enabled=enabled, **values)
    return routes


def validate_start(route, anchor, actual_pose, actual_joints, payload,
                   xyz_mm, rotation_deg, joint_deg):
    """출발 이력의 TCP·관절각과 현재 센서값, 파지를 확인한다."""
    if not route.enabled:
        raise ValueError(f'{route.source} → {route.destination}: 미티칭/비활성 이송 경로')
    if anchor is None or anchor.station != route.source or anchor.approach not in (0, 1):
        raise ValueError('출발 위치 이력이 불확실하다. 출발점을 다시 확인해야 한다')
    if route.start_from in ('above', 'exit') and anchor.approach != 0:
        raise ValueError('이 경로는 출발 ABOVE에서만 시작한다. 놓기 후 직선 후퇴가 필요하다')
    if payload != route.payload:
        raise ValueError(f'이송 파지 조건 불일치: 필요={route.payload}, 현재={payload}')
    if (not pose_matches(actual_pose, anchor.pose, xyz_mm, rotation_deg)
            or not joints_match(actual_joints, anchor.joints, joint_deg)):
        raise ValueError('출발 TCP/관절각이 마지막 도착 상태와 다르다. 수동 이동 여부를 확인해야 한다')


def format_joints(joints):
    """오류·로그에 남길 관절각 문자열 — 실패한 순간의 자세를 나중에 대조할 수 있게 한다."""
    return '[' + ', '.join(f'{float(v):.2f}' for v in joints) + ']'
