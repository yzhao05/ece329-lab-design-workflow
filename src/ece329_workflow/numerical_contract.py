"""Minimum reproducible source and sampling inputs shared by intake/export."""
import math
import re

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
LENGTH_UNIT = r'(?:mm|cm|m|毫米|厘米|米)(?![A-Za-z])'
GEOMETRY_SPECS = (
    ('直导线长度或端点坐标', '直导线', r'长度(?:或端点坐标)?|L'),
    ('圆环半径（米）', '圆环', r'半径|R'),
    ('螺线管半径（米）', '螺线管', r'半径|R'),
    ('螺线管长度（米）', '螺线管', r'长度|L'),
    ('螺线管匝数', '螺线管', r'匝数|N'),
)


def geometry_gap(choices, geometry):
    selected = re.findall(r'(?:路径形状|路径选项|形状选项|允许形状|选项)[^。；;\n]*', choices)
    for label, shape, prop in GEOMETRY_SPECS:
        if not any(shape in item for item in selected):
            continue
        definitions = list(re.finditer(
            rf'{shape}[^。；;\n]{{0,70}}?(?:{prop})\s*(?P<label_unit>（米）|\(m\))?\s*'
            rf'(?:[:：=]|为|是|固定为)?\s*(?P<number>{NUMBER})\s*(?P<unit>{LENGTH_UNIT}|匝)?', geometry, re.I))
        valid = False
        if definitions:
            match = definitions[-1]
            value = float(match.group('number'))
            unit = match.group('unit')
            trailing_unit = re.match(r'[A-Za-z]+|匝', geometry[match.end():].lstrip())
            valid = not trailing_unit and math.isfinite(value) and value > 0 and (
                value.is_integer() and unit in (None, '匝') if '匝数' in label
                else bool(match.group('label_unit') or (unit and unit != '匝')))
        # Two explicit SI endpoints also define finite-wire length, without
        # requiring a redundant length scalar. Coordinates may be negative.
        if shape == '直导线' and not definitions:
            points = re.search(r'直导线[^。；;\n]{0,30}端点[^。；;\n]*', geometry)
            if points and re.search(LENGTH_UNIT, points.group(0)):
                triples = re.findall(r'\(\s*('+NUMBER+r')\s*,\s*('+NUMBER+r')\s*,\s*('+NUMBER+r')\s*\)', points.group(0))
                coordinates = [tuple(float(n) for n in point) for point in triples]
                valid = len(coordinates) == 2 and all(math.isfinite(n) for point in coordinates for n in point) and coordinates[0] != coordinates[1]
        if not valid:
            return label
    return None


def uses_biot_model(numerical):
    return bool(re.search(r'毕奥|Biot|biot_savart', numerical, re.I)
                or (re.search(r'dl\s*(?:cross|×)', numerical, re.I)
                    and re.search(r'mu_?0|μ₀|μ_?0', numerical, re.I)))


def biot_numerical_gap(numerical):
    """Text completeness, not a numerical convergence or physics proof."""
    if not uses_biot_model(numerical):
        return None
    count_patterns = (
        rf'(?:源|导线|路径|圆环|螺线管)[^。；;\n]{{0,25}}?(?:分段|线段|段数|节点)\s*[:：=为]?\s*({NUMBER})',
        rf'(?:源|导线|路径|圆环|螺线管)[^。；;\n]{{0,25}}?(?:分|分为|分成)?\s*(?<![\d.+-])({NUMBER})\s*(?:个)?(?:源段|段|线段)',
        rf'(?<![\d.+-])({NUMBER})\s*(?:个)?(?:源段|段直线|段线段)',
    )
    counts = [float(m.group(1)) for pattern in count_patterns for m in re.finditer(pattern, numerical)]
    lengths = [float(m.group(1)) for m in re.finditer(
        rf'(?:源路径|源线段|电流元|源段|导线)[^。；;\n]{{0,15}}?(?:段长|离散长度|长度步长)\s*(?:设为|为|[:：=])?\s*({NUMBER})\s*{LENGTH_UNIT}', numerical)]
    if any(not math.isfinite(n) or n <= 0 or not n.is_integer() for n in counts):
        return '源路径分段数必须全部为有限正整数，不能用另一条有效分段设置掩盖无效值'
    if any(not math.isfinite(n) or n <= 0 for n in lengths):
        return '源路径段长必须全部为带单位的有限正数'
    if not (any(math.isfinite(n) and n > 0 and n.is_integer() for n in counts)
            or any(math.isfinite(n) and n > 0 for n in lengths)):
        return '源路径的分段数或带单位的源段长（不是场线积分步长）'
    if not re.search(r'中点|梯形|Simpson|辛普森|高斯求积|midpoint', numerical, re.I):
        return '源积分的求积规则，不能只填写场线 RK4 步长'
    if re.search(r'RK4|场线', numerical, re.I):
        if not re.search(r'(?:seed|种子)[^。；;\n]{0,100}(?:=|坐标|位置公式|\()', numerical, re.I):
            return '种子位置的坐标或生成公式；只有种子数量不能复现实验'
        if not re.search(r'零场|I\s*=\s*0|\|B\|\s*<', numerical, re.I):
            return '零场时的处理方式，避免归一化零矢量'
    return None


def numerical_tolerance_defined(value: str) -> bool:
    """A numerical error bound cannot borrow a step size or negative value."""
    patterns = (
        rf'(?:容差|误差(?:上限|阈值)?|收敛(?:阈值|精度)|tolerance)'
        rf'(?:(?!步长|步数|最多|最大|未知|未定)[^\d。；;\n，,+-]){{0,45}}(?P<number>{NUMBER})',
        rf'(?:相对误差|绝对误差|位置偏差|偏差|误差)\s*(?:小于|不超过|低于|<=|<|≤)\s*(?P<number>{NUMBER})',
    )
    bounds = [float(m.group('number')) for p in patterns for m in re.finditer(p, value, re.I)]
    return bool(bounds) and all(math.isfinite(n) and n > 0 for n in bounds)


def numerical_model_gap(numerical: str, formula_ids=()):
    """One shared intake/reference/export check; not a convergence proof."""
    if 'biot_savart' in formula_ids and not uses_biot_model(numerical):
        return '所选毕奥-萨伐尔模型的源积分实现，不能由其他公式的算法替代'
    integrates = bool(re.search(r'RK4|龙格', numerical, re.I)) or any(
        re.search(r'积分|trajectory', clause, re.I)
        and not re.search(r'无需|不(?:进行|需要|使用|做)|无积分', clause)
        for clause in re.split(r'[。；;\n]', numerical)
    )
    if integrates and not numerical_tolerance_defined(numerical):
        return '可检验的正数误差/收敛容差；步长和最大步数不能代替精度验收值'
    return biot_numerical_gap(numerical)
