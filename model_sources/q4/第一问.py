#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第一问：前向楔形交集、区域直径及直径圆覆盖检验（仅标准库）。

使用：python3 第一问.py          显示自建算例
      python3 第一问.py --test   运行内置核验
复用：result = localize([(0, 0, 45.4), (1000, 0, 134.3)])

输入坐标单位为米；角度从正东逆时针，自动归一化；默认误差界为 ±1°。
只计算闭楔形交集，不加入 1800 米圆域、接收圆盘或距离 >5 米等信息。
三角函数先用双精度计算，再转为 Fraction；后续几何判断精确针对这些
有理系数，不是对理想三角函数的任意精度认证。不会用容差改变可行性。
不连接模拟器，不写结果文件。适合少量测点；n 条测向记录需 O(n^3)
次算术操作，Fraction 大整数的位运算成本另计。
"""

import argparse
import math
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations

Point = tuple[Fraction, Fraction]
HalfPlane = tuple[Fraction, Fraction, Fraction]  # (a,b,c) 表示 a*x+b*y <= c。


# 第一部分：规定输出包含哪些内容，便于其他问题调用同一套定位算法。
@dataclass
class LocalizationResult:
    """坐标和平方量保留 Fraction；直径、半径属性用于浮点显示。"""

    status: str  # 空集、无界、点、线段、多边形
    vertices: tuple[Point, ...] = ()  # 多边形逆时针；线段两个端点；点一个顶点。
    feasible_point: Point | None = None  # 满足所有测向约束的一个位置。
    recession_direction: Point | None = None  # 从可行点沿此非零方向无限延伸仍可行。
    farthest_pair: tuple[Point, Point] | None = None  # 距离最远的两个顶点。
    diameter_sq: Fraction | None = None  # 区域直径的平方，保留有理数精度。
    center: Point | None = None  # 最远点对的中点，即待检验直径圆的圆心。
    radius_sq: Fraction | None = None  # 待检验直径圆半径的平方。
    circle_covers: bool | None = None  # 空集/无界返回 None，不能误读为有限圆。
    notes: list[str] = field(default_factory=list)  # 近乎平行等数值敏感情形的提示。

    @property
    def diameter(self):
        return math.inf if self.status == "无界" else (
            None if self.diameter_sq is None else math.sqrt(float(self.diameter_sq)))

    @property
    def radius(self):
        return None if self.radius_sq is None else math.sqrt(float(self.radius_sq))


# 第二部分：检查输入、统一角度，把每次测向转换为两个半平面。
def _to_fraction(value, name):
    """保留输入的十进制书写值；不接受字符串、布尔值、NaN 或无穷。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise ValueError(f"{name}须为 int、float 或 Fraction 数值。")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name}须为有限数值。")
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _unit_direction(angle_deg):
    """象限/余角对称用精确符号和交换实现，保留平行、反向和跨界关系。"""
    angle_deg %= 360
    quadrant, offset_deg = divmod(angle_deg, 90)
    folded_deg = min(offset_deg, 90 - offset_deg)
    angle_rad = math.radians(float(folded_deg))
    sin_value = Fraction.from_float(math.sin(angle_rad)) if folded_deg else Fraction(0)
    cos_value = Fraction.from_float(math.cos(angle_rad)) if folded_deg else Fraction(1)
    if float(folded_deg) == 45.0:
        sin_value = cos_value  # sin45°=cos45° 的精确结构，不依赖舍入是否碰巧一致。
    if offset_deg > 45:
        sin_value, cos_value = cos_value, sin_value
    return ((cos_value, sin_value), (-sin_value, cos_value),
            (-cos_value, -sin_value), (sin_value, -cos_value))[int(quadrant)]


def build_halfplanes(measurements, error_deg=1):
    """每个 (x_i,y_i,theta_i) 生成两条闭半平面；含前向性而非双向直线。"""
    error_deg = _to_fraction(error_deg, "误差界")
    if not 0 < error_deg < 90:
        raise ValueError("误差界须满足 0 < 误差界 < 90（度）。")
    try:
        measurements = list(measurements)
    except TypeError as exc:
        raise ValueError("测量须为 (x,y,theta) 三元组的可迭代对象。") from exc
    if not measurements:
        raise ValueError("至少提供一个检测点及其示向度。")
    constraints = []
    for index, record in enumerate(measurements, 1):
        if not isinstance(record, (tuple, list)) or len(record) != 3:
            raise ValueError(f"第 {index} 条测量须为 (x,y,theta) 三元组。")
        x, y, angle_deg = [_to_fraction(v, f"第 {index} 条测量") for v in record]
        lower_direction = _unit_direction(angle_deg - error_deg)
        upper_direction = _unit_direction(angle_deg + error_deg)
        if lower_direction[0] * upper_direction[1] - lower_direction[1] * upper_direction[0] <= 0:
            raise ValueError("双精度下无法保持这组楔形的正开角；请采用更高精度三角函数。")
        # 候选点在下边界射线左侧、上边界射线右侧；开角 <180° 确保前向。
        for a, b in ((lower_direction[1], -lower_direction[0]), (-upper_direction[1], upper_direction[0])):
            constraints.append((a, b, a * x + b * y))
    return tuple(sorted(set(constraints)))  # 完全重复测量不提供新约束。


# 第三部分：通用几何运算。这里不使用真实源坐标，只检查候选点和约束。
def _satisfies(p, constraints):
    return all(a * p[0] + b * p[1] <= c for a, b, c in constraints)


def _squared_distance(p, q):
    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex_hull(points):
    """单调链；去掉重复点和共线内点，自动保留点/线段退化结果。"""
    points = sorted(set(points))
    if len(points) <= 1:
        return tuple(points)
    hull = []
    for sequence in (points, reversed(points)):
        chain = []
        for p in sequence:
            while len(chain) >= 2 and _cross(chain[-2], chain[-1], p) <= 0:
                chain.pop()
            chain.append(p)
        hull.extend(chain[:-1])
    return tuple(hull)


# 第四部分：求交集，先判空与无界，再算顶点、直径和直径圆覆盖。
def _intersect_halfplanes(constraints):
    """基础几何引擎；测试也直接输入手算半平面，避免仅自证构造公式。"""
    constraints = tuple(tuple(_to_fraction(v, "半平面系数") for v in h) for h in constraints)
    if any(a == b == 0 and c < 0 for a, b, c in constraints):
        return LocalizationResult("空集")
    constraints = tuple(sorted(set(h for h in constraints if h[0] or h[1])))
    candidates = {(Fraction(0), Fraction(0))}
    intersections, notes = set(), []
    for a, b, c in constraints:
        norm_sq = a * a + b * b
        candidates.add((a * c / norm_sq, b * c / norm_sq))  # 原点到边界的垂足。
    has_near_parallel = False
    for (a, b, c), (d, e, f) in combinations(constraints, 2):
        det = a * e - b * d
        if not det:  # 真正平行，绝不拿“大数”替代无交点。
            continue
        intersections.add(((c * e - b * f) / det, (a * f - c * d) / det))
        # 正弦平方是无量纲量；这里只提示，不据此改变几何判断。
        sin_sq = det ** 2 / ((a * a + b * b) * (d * d + e * e))
        has_near_parallel |= sin_sq < Fraction(1, 10**16)
    if has_near_parallel:
        notes.append("存在近乎平行的边界（夹角正弦 <1e-8）；交点及退化分类可能对输入/三角舍入敏感。")
    candidates.update(intersections)
    feasible_point = next((p for p in sorted(candidates) if _satisfies(p, constraints)), None)
    # 非空闭凸多面体中距原点最近的点：原点、某边界垂足或非平行边界交点。
    if feasible_point is None:
        return LocalizationResult("空集", notes=notes)
    directions = {
        (Fraction(1), Fraction(0)), (Fraction(-1), Fraction(0)),
        (Fraction(0), Fraction(1)), (Fraction(0), Fraction(-1)),
    }
    for a, b, _ in constraints:
        directions.update({(-b, a), (b, -a)})
    # 二维非零衰退锥若非全平面，至少含一条约束边界的切向射线。
    recession_direction = next((d for d in sorted(directions)
                  if all(a * d[0] + b * d[1] <= 0 for a, b, _ in constraints)), None)
    if recession_direction is not None:
        return LocalizationResult(
            "无界", feasible_point=feasible_point,
            recession_direction=recession_direction, notes=notes,
        )
    vertices = _convex_hull(p for p in intersections if _satisfies(p, constraints))
    if not vertices:
        raise ArithmeticError("有界非空集合应至少存在一个顶点；请检查几何引擎。")
    farthest_pair = max(((p, q) for p in vertices for q in vertices),
                  key=lambda pair: _squared_distance(*pair))
    a, b = farthest_pair
    diameter_sq = _squared_distance(a, b)
    center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    radius_sq = diameter_sq / 4
    circle_covers = all(_squared_distance(p, center) <= radius_sq for p in vertices)
    status = "点" if len(vertices) == 1 else "线段" if len(vertices) == 2 else "多边形"
    return LocalizationResult(status, vertices, feasible_point, None, farthest_pair,
                diameter_sq, center, radius_sq, circle_covers, notes)


# 第五部分：对外调用入口。一般只需要把自己的测量传给这个函数。
def localize(measurements, error_deg=1):
    """输入检测点和读数，返回交集分类、顶点、直径及直径圆覆盖判断。

    measurements：若干个 (x, y, theta) 三元组，坐标单位米、角度单位度。
    error_deg：测角误差的最大绝对值，本题固定为 1 度。
    返回 LocalizationResult；例如 result.diameter 表示区域直径。

    真源坐标不是输入。若半径 D/2 的圆覆盖区域，它必须包含最远点对，
    从三角不等式取等号可知圆心只能是该点对中点。圆盘凸，检查顶点即足够。
    空集不报告 D=0；无界返回 D=inf 且不构造有限圆；点的 D=0。
    """
    return _intersect_halfplanes(build_halfplanes(measurements, error_deg))


# 第六部分：自建算例和数学核验。它们用于检查算法，不是题目给定数据。
def _main_example():
    return [(0, 0, 45.4), (1000, 0, 134.3)]


def _triangle_example():
    """近似边长36米的等边三角形；真源(0,0)，最小覆盖圆半径12√3>20。"""
    sqrt3 = math.sqrt(3)
    return [(-918, -6 * sqrt3, 1), (468, -456 * sqrt3, 121),
            (450, 462 * sqrt3, 241)]


def run_tests():
    """独立手算几何、实际楔形及结构不变性核验；不代表模拟器已验证。"""
    passed = []

    def check(name, condition):
        if not condition:
            raise AssertionError(f"核验失败：{name}")
        passed.append(name)
        print(f"通过：{name}")

    r = localize(_main_example())
    check("主例：四边形，已知真源(500,500)保留且直径圆覆盖",
         len(r.vertices) == 4
         and _satisfies((Fraction(500), Fraction(500)), build_halfplanes(_main_example()))
         and r.circle_covers)
    check("独立3-4-5矩形：D=5，中点圆覆盖全部顶点",
         (lambda r: r.diameter_sq == 25 and r.circle_covers)(
             _intersect_halfplanes([(-1, 0, 0), (1, 0, 3), (0, -1, 0), (0, 1, 4)])))
    r = localize([(0, 0, 45), (1000, 0, 135)])
    check("对称楔形：解析D=1000*tan(2°)",
         math.isclose(r.diameter, 1000 * math.tan(math.radians(2)), rel_tol=1e-12))
    check("背离楔形：空集且没有伪造直径",
         (lambda r: r.status == "空集" and r.diameter is None)(localize([(0, 0, 180), (10, 0, 0)])))
    check("单次测量：无界；重复测量仍无界",
         localize([(0, 0, 0)]).status == localize([(0, 0, 0)] * 3).status == "无界")
    check("同点反向楔形：仅公共顶点，D=0",
         (lambda r: r.status == "点" and r.diameter_sq == 0 and r.circle_covers)(
             localize([(0, 0, 0), (0, 0, 180)])))
    check("共享边界退化线段：[0,10]，D=10",
         (lambda r: r.status == "线段" and r.diameter_sq == 100 and r.circle_covers)(
             localize([(0, 0, 1), (0, 0, 359), (10, 0, 180)])))
    check("平行边界：平移同向楔形仍无界",
         localize([(0, 0, 0), (0, 1, 0)]).status == "无界")
    check("无顶点的平行带仍非空无界（垂足提供可行见证）",
         _intersect_halfplanes([(1, 0, 2), (-1, 0, -1)]).status == "无界")
    check("矛盾平行半平面为空", _intersect_halfplanes([(1, 0, 0), (-1, 0, -1)]).status == "空集")
    r = _intersect_halfplanes([(-1, 0, 0), (1, 0, 1), (0, -1, 0), (1, Fraction(1, 10**12), 1)])
    check("近乎平行但确实有界：巨大三角形不被误判为平行",
         r.status == "多边形" and r.diameter_sq == 10**24 + 1 and bool(r.notes))
    r = _intersect_halfplanes([(0, -1, 0), (Fraction(1, 10**12), -1, 1)])
    check("近乎平行无界区域：不添加虚构大边框", r.status == "无界" and bool(r.notes))
    h = build_halfplanes([(0, 0, 359.9)])
    check("0/360跨界及前向性：前方点保留，背后点排除",
         h == build_halfplanes([(0, 0, -0.1)]) and _satisfies((Fraction(100), Fraction(0)), h)
         and not _satisfies((Fraction(-100), Fraction(0)), h))
    check("误差±1°端点采用闭边界",
         _satisfies((Fraction(100), Fraction(0)), build_halfplanes([(0, 0, 1), (200, 0, 179)])))
    baseline = localize(_main_example())
    check("有界主例完全重复测量：顶点和直径不变",
         localize(_main_example() * 2).vertices == baseline.vertices)
    updated_measurements = _main_example() + [(500, 0, 90.5)]
    updated_result = localize(updated_measurements)
    check("新增合法测量：新区域包含于旧区域，直径不增且真源保留",
         updated_result.status == "多边形" and updated_result.diameter_sq <= baseline.diameter_sq
         and all(_satisfies(v, build_halfplanes(_main_example())) for v in updated_result.vertices)
         and _satisfies((Fraction(500), Fraction(500)), build_halfplanes(updated_measurements)))
    transformed = [(123 - y, -456 + x, t + 90) for x, y, t in _main_example()]
    r = localize(transformed)
    check("精确90°旋转和平移不变性：平方直径及全部顶点",
         r.diameter_sq == baseline.diameter_sq and
         set(r.vertices) == {(123 - y, -456 + x) for x, y in baseline.vertices})
    t = math.radians(37)
    rotated = [(x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t), a + 37)
            for x, y, a in _main_example()]
    check("一般37°旋转：双精度输入下直径近似不变",
         math.isclose(localize(rotated).diameter, baseline.diameter, rel_tol=1e-12))
    r = localize(_triangle_example())
    true_source = (Fraction(0), Fraction(0))
    check("实际楔形三角形反例：D约36米，D/2圆不能覆盖",
         len(r.vertices) == 3 and not r.circle_covers and math.isclose(r.diameter, 36, abs_tol=1e-9)
         and _satisfies(true_source, build_halfplanes(_triangle_example()))
         and all(5 < math.hypot(float(true_source[0]) - x, float(true_source[1]) - y) < 1000
                 for x, y, _ in _triangle_example()))
    invalid_cases = [([], 1), ([(0, 0)], 1), ([(True, 0, 0)], 1),
          ([(0, 0, math.nan)], 1), ([(0, math.inf, 0)], 1), ([(0, 0, 0)], 90),
          ([(0, 0, 45)], 1e-30)]
    for measurements, error in invalid_cases:
        try:
            localize(measurements, error)
        except ValueError:
            continue
        raise AssertionError("非法输入未被拒绝。")
    check("输入校验：缺项、非有限数值、非法误差界及三角舍入塌缩", True)
    print(f"\n共 {len(passed)} 项核验通过；容差只用于浮点解析值比对，不参与几何分类。")


def _show_example(name, measurements):
    r = localize(measurements)
    format_point = lambda p: tuple(round(float(v), 9) for v in p)
    print(f"\n{name}\n输入：{measurements}\n交集类型：{r.status}")
    if r.diameter_sq is not None:
        print("顶点：", [format_point(p) for p in r.vertices])
        print("最远点对：", [format_point(p) for p in r.farthest_pair])
        print(f"直径 D = {r.diameter:.9f} 米；圆半径 D/2 = {r.radius:.9f} 米")
        print("圆心：", format_point(r.center), "；直径圆覆盖：", "是" if r.circle_covers else "否")
    elif r.status == "无界":
        print("直径为无穷；无界方向见 result.recession_direction，没有有限覆盖圆。")
    else:
        print("没有满足全部观测约束的位置，直径不适用。")
    for note in r.notes:
        print("提示：", note)


# 第七部分：直接运行文件时，按命令行参数选择显示算例或执行测试。
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="运行内置数学与几何核验")
    if parser.parse_args().test:
        run_tests()
    else:
        _show_example("自建主例（真源500,500仅用于另外核验）", _main_example())
        _show_example("直径圆不能覆盖的实际楔形反例", _triangle_example())
        print("\n输入自己的测量请调用 localize；运行 --test 可查看全部核验。")
        print("数值声明：几何判断对有理系数精确；三角函数仍为双精度近似。")
