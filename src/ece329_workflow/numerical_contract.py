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


def biot_numerical_gap(numerical):
    """Text completeness, not a numerical convergence or physics proof."""
    if not re.search(r'毕奥|Biot|biot_savart', numerical, re.I):
        return None
    if not re.search(r'(?:源|导线|路径)[^。；;\n]{0,18}(?:分段|线段|段数|节点)[^。；;\n]{0,12}\d|\d+\s*(?:个)?(?:源段|段直线|段线段)', numerical):
        return '源路径的分段数和求积规则（例如按每段中点计算贡献）'
    if not re.search(r'中点|梯形|Simpson|辛普森|高斯求积|midpoint', numerical, re.I):
        return '源积分的求积规则，不能只填写场线 RK4 步长'
    if re.search(r'RK4|场线', numerical, re.I):
        if not re.search(r'(?:seed|种子)[^。；;\n]{0,100}(?:=|坐标|位置公式|\()', numerical, re.I):
            return '种子位置的坐标或生成公式；只有种子数量不能复现实验'
        if not re.search(r'零场|I\s*=\s*0|\|B\|\s*<', numerical, re.I):
            return '零场时的处理方式，避免归一化零矢量'
    return None
