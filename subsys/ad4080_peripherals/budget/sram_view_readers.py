# =============================================================================
# 文件名     : sram_view_readers.py
# 模块名     : sram_view_readers
# 版本       : v1.0
# 功能       : 从供应方 LEF 和 Liberty 原文提取 SRAM 静态验收所需字段。
# 设计说明   : 只解释接收门使用的显式字段。无法辨认的几何或时序视图退出
#              为 UNKNOWN，完整读取后可以确定的冲突由上层报 FAIL。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
from dataclasses import dataclass, field
from decimal import Decimal
import re

from area_gate import decimal_value


# =============================================================================
# 1. 源文件结构和失败路径
# =============================================================================
class MissingEvidence(ValueError):
    """缺证或不支持的输入视图。"""


class ViewConflict(ValueError):
    """可以明确判定的视图矛盾。"""


def evidence(value, message):
    if not value:
        raise MissingEvidence(message)


def agrees(value, message):
    if not value:
        raise ViewConflict(message)


def decimal_fields(values, label):
    return tuple(decimal_value(value, label) for value in values)


class Tokens:
    """游标保留词法层级。遇到缺段或意外结尾，停止解释该视图。"""

    def __init__(self, tokens):
        self.words = tokens
        self.offset = 0

    def peek(self):
        return self.words[self.offset] if self.offset < len(self.words) else None

    def take(self):
        value = self.peek()
        evidence(value is not None, "视图意外结束")
        self.offset += 1
        return value

    def expect(self, word):
        actual = self.take()
        evidence(actual == word, f"语法应为 {word}，实读 {actual}")

    def until(self, terminator):
        # 调用点选择分号或右括号。结尾缺少终止符时由 take 明确中止。
        values = []
        while self.peek() != terminator:
            values.append(self.take())
        self.expect(terminator)
        return values


# =============================================================================
# 2. LEF 宏、PORT/OBS 几何
# =============================================================================
LEF_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\#[^\n]*|[;()]|[^\s;()"]+')


@dataclass
class LefPin:
    direction: tuple
    use: tuple
    shapes: list


@dataclass
class LefMacro:
    dbu: int
    width: Decimal
    height: Decimal
    origin: tuple
    symmetry: set
    pins: dict
    obs: list


def lef_geometry(tokens, label):
    # 按 LAYER/RECT/POLYGON 原语记录真实几何。路径、阵列和 VIA 需要额外
    # 几何换算，不能当成无阻塞或无占用的空白层吞掉。
    layer, shapes = None, []
    while tokens.peek() != "END":
        statement = tokens.take()
        fields = tokens.until(";")
        if statement == "LAYER":
            evidence(fields, f"{label} 的 LAYER 缺层名")
            # EXCEPTPGNET 等层修饰由目标布线器解释，此处核对形状的归属层。
            layer = fields[0]
            continue
        if statement == "CLASS":
            continue
        evidence(statement in {"RECT", "POLYGON"} and layer, f"{label} 有未支持的几何 {statement}")
        if fields[:1] == ["MASK"]:
            # MASK 编号不属于坐标。把它误作 x 会产生虚构的越界或空 PORT。
            evidence(len(fields) >= 2 and fields[1].isdigit(), f"{label} 的 MASK 缺有效编号")
            fields = fields[2:]
        numbers = decimal_fields(fields, f"{label} 坐标")
        if statement == "RECT":
            evidence(len(numbers) == 4, f"{label} 的 RECT 需四个坐标")
            x0, y0, x1, y1 = numbers
            agrees(x0 < x1 and y0 < y1, f"{label} RECT 退化或边界倒置")
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        else:
            evidence(len(numbers) >= 6 and len(numbers) % 2 == 0, f"{label} 的 POLYGON 坐标不足")
            points = list(zip(numbers[::2], numbers[1::2]))
            # 交给实际 DRC 验证多边形形状。在此至少排除面积为零的退化轮廓。
            double_area = sum(x * yy - xx * y for (x, y), (xx, yy) in zip(points, points[1:] + points[:1]))
            agrees(double_area != 0, f"{label} POLYGON 面积为零")
        shapes.append((layer, points))
    tokens.expect("END")
    evidence(shapes, f"{label} 无实际几何")
    return shapes


def lef_pin(tokens, name):
    attributes, shapes = {}, []
    # 真实 pin 可拥有多块 PORT。按同一实际名称聚合，维持每块 PORT 的层归属。
    while tokens.peek() != "END":
        key = tokens.take()
        if key == "PORT":
            shapes.extend(lef_geometry(tokens, f"PIN {name} PORT"))
        else:
            agrees(key not in attributes, f"PIN {name} 的字段 {key} 重复")
            attributes[key] = tokens.until(";")
    tokens.expect("END")
    tokens.expect(name)
    evidence(shapes and attributes.get("DIRECTION") and attributes.get("USE"), f"PIN {name} 缺 DIRECTION、USE 或 PORT 几何")
    return LefPin(tuple(attributes["DIRECTION"]), tuple(attributes["USE"]), shapes)


def read_lef(raw, macro):
    """读取一个 BLOCK MACRO，角点、SIZE 和 ORIGIN 的单位均为 µm。"""
    # 注释先在词法层过滤。名称与被选择的单位原句必须在实际语句中出现。
    words = [token for token in LEF_TOKEN.findall(raw) if not token.startswith("#")]
    unit_values = [words[index + 2] for index in range(len(words) - 3)
                   if words[index:index + 2] == ["DATABASE", "MICRONS"] and words[index + 3] == ";"]
    evidence(len(unit_values) == 1 and unit_values[0].isdigit() and int(unit_values[0]) > 0,
             "LEF 需要显式且唯一的 DATABASE MICRONS 单位")
    matches = [index for index in range(len(words) - 1) if words[index:index + 2] == ["MACRO", macro]]
    agrees(len(matches) == 1, f"LEF 中 {macro} 匹配的 MACRO 数量为 {len(matches)}")
    tokens = Tokens(words[matches[0] + 2:])
    attrs, pins, obs = {}, {}, []
    while tokens.peek() != "END":
        # PIN、OBS 各有 END。保持边界，避免把下一宏的引脚或障碍物算给此宏。
        key = tokens.take()
        if key == "PIN":
            name = tokens.take()
            agrees(name not in pins, f"LEF PIN {name} 重名")
            pins[name] = lef_pin(tokens, name)
        elif key == "OBS":
            obs.extend(lef_geometry(tokens, "OBS"))
        elif key == "FIXEDMASK":
            tokens.expect(";")
        else:
            evidence(key in {"CLASS", "SIZE", "ORIGIN", "SYMMETRY", "FOREIGN", "SOURCE", "EEQ", "SITE", "PROPERTY"},
                     f"LEF 宏内含未支持语句 {key}")
            values = tokens.until(";")
            if key in {"CLASS", "SIZE", "ORIGIN", "SYMMETRY"}:
                agrees(key not in attrs, f"LEF MACRO {macro} 的 {key} 重复")
                attrs[key] = values
    tokens.expect("END")
    tokens.expect(macro)
    # 明确给出阻塞视图是这次宏接收的一部分。缺少 OBS 的文件保留待验。
    evidence(all(attrs.get(key) for key in ("SIZE", "CLASS", "ORIGIN")) and pins and obs,
             "LEF 须提供显式 CLASS/SIZE/ORIGIN、针脚与 OBS 几何")
    agrees(attrs["CLASS"][0] == "BLOCK", f"LEF {macro} 不是 BLOCK 宏")
    size = attrs["SIZE"]
    evidence(len(size) == 3 and size[1] == "BY" and len(attrs["ORIGIN"]) == 2, "LEF SIZE/ORIGIN 语法不可读")
    width, height = decimal_fields(size[::2], "LEF SIZE")
    origin = decimal_fields(attrs["ORIGIN"], "LEF ORIGIN")
    agrees(min(width, height) > 0, "LEF SIZE 须为正")
    symmetries = set(attrs.get("SYMMETRY", []))
    evidence(symmetries <= {"X", "Y", "R90"}, "LEF SYMMETRY 含未支持的朝向")
    # 非零 ORIGIN 需要按选定朝向变换放置锚点。先拒绝解释，避免平移错误。
    evidence(origin == (0, 0), "非零 LEF ORIGIN 需补真实朝向变换后再验")
    return LefMacro(int(unit_values[0]), width, height, origin, symmetries, pins, obs)


# =============================================================================
# 3. Liberty 文本树、标量与表值
# =============================================================================
LIB_TOKEN = re.compile(r'/\*.*?\*/|//[^\n]*|\\[ \t]*\r?\n|"(?:\\.|[^"\\])*"|[(){};,:]|[^\s(){};,:"\\]+', re.S)


@dataclass
class LibGroup:
    kind: str
    args: tuple
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    calls: dict = field(default_factory=dict)

    def groups(self, kind):
        return [child for child in self.children if child.kind == kind]

    def value(self, name):
        evidence(name in self.attrs, f"Liberty {self.kind}{self.args} 缺 {name}")
        return self.attrs[name]


def lib_group(tokens, group):
    # 递归消费完整的括号嵌套，避免在注释、其他宏或其他时序组里误捡同名字段。
    tokens.expect("{")
    while tokens.peek() != "}":
        key, mark = tokens.take(), tokens.take()
        # 冒号是标量，圆括号后可为表行或子实体。保留两者各自所属的作用域。
        if mark == ":":
            agrees(key not in group.attrs, f"Liberty {group.kind}{group.args} 的 {key} 重复")
            group.attrs[key] = " ".join(tokens.until(";"))
        else:
            evidence(mark == "(", f"Liberty 语句 {key} 无法解析")
            args = tuple(value for value in tokens.until(")") if value != ",")
            if tokens.peek() == ";":
                tokens.expect(";")
                group.calls.setdefault(key, []).extend(args)
            else:
                group.children.append(lib_group(tokens, LibGroup(key, args)))
    tokens.expect("}")
    return group


def read_liberty(raw):
    # 字符串作为一个 token。引号内的 A[0]、表值逗号和布尔条件保持所属字段。
    words = [word[1:-1] if word.startswith('"') else word for word in LIB_TOKEN.findall(raw)
             if not word.startswith(("/*", "//", "\\"))]
    tokens = Tokens(words)
    tokens.expect("library")
    tokens.expect("(")
    args = tuple(tokens.until(")"))
    evidence(len(args) == 1, "Liberty library 名称不可读")
    library = lib_group(tokens, LibGroup("library", args))
    evidence(tokens.peek() is None, "Liberty 视图包含未支持的其他顶层语句")
    return library


def named_group(groups, name, label):
    # 要求唯一精确名称，可拒绝截断字宽后缀或从另一工艺库读来重名 pin。
    matches = [group for group in groups if group.args == (name,)]
    agrees(len(matches) == 1, f"{label} {name} 的匹配数为 {len(matches)}")
    return matches[0]


def table_values(group):
    # 只核公开表格内已给出的极值。合法的插值、负载域和模式控制另由 STA 确认。
    rows = group.calls.get("values", [])
    evidence(rows, f"Liberty {group.kind} 缺数值表")
    # 每个字符串是一个行向量。保留每个数值，避免只比较第一个表点。
    values = [item.strip() for row in rows for item in row.split(",")]
    return decimal_fields(values, f"Liberty {group.kind}")
