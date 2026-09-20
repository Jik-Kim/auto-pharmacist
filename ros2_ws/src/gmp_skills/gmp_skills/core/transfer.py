"""티칭한 스테이션 간 이송 경로 검증. ROS·장치 호출은 하지 않는다."""
from dataclasses import dataclass
import math


def vector6(value, name):
    if (not isinstance(value, (list, tuple)) or len(value) != 6
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in value)):
        raise ValueError(f'{name}: 유한한 숫자 6개가 필요하다')
    return tuple(float(v) for v in value)


def _rotation(pose):
    # 두산 posx의 Z-Y-Z Euler 표현. 동등한 각 표현을 직접 빼지 않는다.
    a, b, c = map(math.radians, pose[3:])
    ca, sa, cb, sb, cc, sc = (math.cos(a), math.sin(a), math.cos(b),
                             math.sin(b), math.cos(c), math.sin(c))
    return ((ca*cb*cc-sa*sc, -ca*cb*sc-sa*cc, ca*sb),
            (sa*cb*cc+ca*sc, -sa*cb*sc+ca*cc, sa*sb),
            (-sb*cc, sb*sc, cb))


def pose_matches(actual, target, xyz_mm, rotation_deg):
    actual, target = vector6(actual, '현재 posx'), vector6(target, '목표 posx')
    if max(abs(a-b) for a, b in zip(actual[:3], target[:3])) > xyz_mm:
        return False
    ra, rb = _rotation(actual), _rotation(target)
    cosine = (sum(a*b for row_a, row_b in zip(ra, rb)
                  for a, b in zip(row_a, row_b)) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) <= rotation_deg


def joints_match(actual, target, tolerance_deg):
    # 360도 차이를 지우면 케이블 감김·다른 티칭 구성을 놓치므로 그대로 비교한다.
    return max(abs(a-b) for a, b in zip(vector6(actual, '현재 posj'),
                                      vector6(target, '목표 posj'))) <= tolerance_deg


@dataclass(frozen=True)
class MotionAnchor:
    station: str
    approach: int
    pose: tuple
    joints: tuple


@dataclass(frozen=True)
class TransferRoute:
    source: str
    destination: str
    payload: str
    enabled: bool
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
        if start_from not in ('at_or_above', 'above') or arrival not in ('above', 'at'):
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
            elif enabled and not (name == 'start_at_posj' and start_from == 'above'):
                raise ValueError(f'{key}.{name}: 티칭값이 필요하다')
        points = row.get('waypoints_posj', [])
        if not isinstance(points, list) or (enabled and not points):
            raise ValueError(f'{key}: 도착 {arrival.upper()} 관절각을 포함한 waypoints_posj가 필요하다')
        values['waypoints_posj'] = tuple(vector6(p, 'waypoint') for p in points)
        if values.get('exit_posx'):
            if not pose_matches(values['exit_posx'], values['source_at_posx'], float('inf'), 0.001):
                raise ValueError(f'{key}: 직선 이탈 중 출발 자세를 유지해야 한다')
        routes[key] = TransferRoute(src, dst, payload, enabled, **values)
    return routes


def validate_start(route, anchor, actual_pose, actual_joints, payload,
                   xyz_mm, rotation_deg, joint_deg):
    if not route.enabled:
        raise ValueError(f'{route.source} → {route.destination}: 미티칭/비활성 이송 경로')
    if anchor is None or anchor.station != route.source or anchor.approach not in (0, 1):
        raise ValueError('출발 위치 이력이 불확실하다. 출발점을 다시 확인해야 한다')
    if route.start_from == 'above' and anchor.approach != 0:
        raise ValueError('이 경로는 출발 ABOVE에서만 시작한다. 놓기 후 직선 후퇴가 필요하다')
    if payload != route.payload:
        raise ValueError(f'이송 파지 조건 불일치: 필요={route.payload}, 현재={payload}')
    source_pose = route.source_above_posx if anchor.approach == 0 else route.source_at_posx
    if not pose_matches(actual_pose, source_pose, xyz_mm, rotation_deg):
        raise ValueError('현재 위치가 이송 기준 파지/작업점과 다르다')
    if (not pose_matches(actual_pose, anchor.pose, xyz_mm, rotation_deg)
            or not joints_match(actual_joints, anchor.joints, joint_deg)):
        raise ValueError('출발 자세가 마지막 도착 상태와 다르다. 수동 이동 여부를 확인해야 한다')
    taught = route.start_above_posj if anchor.approach == 0 else route.start_at_posj
    if not joints_match(actual_joints, taught, joint_deg):
        raise ValueError('출발 관절 구성이 티칭값과 다르다')
