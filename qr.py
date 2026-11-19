"""A small QR encoder, so pairing needs no package that Hermes may not have installed.

Byte mode, error correction level L, versions 1 to 10: up to 271 bytes, which a pairing link
fits with room to spare. `rows(text)` returns the symbol as strings of '1' (dark) and '0', with no
quiet zone, or None when the text is too long.
"""

from __future__ import annotations

# Per version, at level L: error correction codewords per block, then the data codewords of each block.
BLOCKS = {
    1: (7, [19]), 2: (10, [34]), 3: (15, [55]), 4: (20, [80]), 5: (26, [108]),
    6: (18, [68, 68]), 7: (20, [78, 78]), 8: (24, [97, 97]), 9: (30, [116, 116]),
    10: (18, [68, 68, 69, 69]),
}
ALIGNMENT = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}
MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]

EXP = [0] * 512
LOG = [0] * 256
_value = 1
for _i in range(255):
    EXP[_i], LOG[_value] = _value, _i
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _correction(data: list, count: int) -> list:
    """The Reed-Solomon codewords for one block."""
    generator = [1]
    for i in range(count):
        generator = [
            (generator[j] if j < len(generator) else 0)
            ^ (EXP[LOG[generator[j - 1]] + i] if j and generator[j - 1] else 0)
            for j in range(len(generator) + 1)
        ]
    rest = [0] * count
    for byte in data:
        factor = byte ^ rest[0]
        rest = rest[1:] + [0]
        if factor:
            for j in range(count):
                if generator[j + 1]:
                    rest[j] ^= EXP[LOG[generator[j + 1]] + LOG[factor]]
    return rest


def _codewords(payload: bytes, version: int) -> list:
    count, sizes = BLOCKS[version]
    capacity = sum(sizes) * 8
    bits = [0, 1, 0, 0]
    length = 16 if version >= 10 else 8
    bits += [(len(payload) >> i) & 1 for i in range(length - 1, -1, -1)]
    for byte in payload:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    for pad in range(sum(sizes) - len(data)):
        data.append(0xEC if pad % 2 == 0 else 0x11)

    blocks, at = [], 0
    for size in sizes:
        blocks.append(data[at:at + size])
        at += size
    checks = [_correction(block, count) for block in blocks]
    out = []
    for i in range(max(sizes)):
        out += [block[i] for block in blocks if i < len(block)]
    for i in range(count):
        out += [check[i] for check in checks]
    return out


def _penalty(grid: list) -> int:
    size = len(grid)
    score = 0
    lines = grid + [list(column) for column in zip(*grid)]
    for line in lines:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                score += run - 2 if run >= 5 else 0
                run = 1
        score += run - 2 if run >= 5 else 0
        text = "".join(map(str, line))
        for finder in ("10111010000", "00001011101"):
            start = text.find(finder)
            while start != -1:
                score += 40
                start = text.find(finder, start + 1)
    for y in range(size - 1):
        for x in range(size - 1):
            if grid[y][x] == grid[y][x + 1] == grid[y + 1][x] == grid[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, grid)) * 100 // (size * size)
    return score + abs(dark - 50) // 5 * 10


def _draw(codewords: list, version: int, mask: int) -> list:
    size = version * 4 + 17
    grid = [[0] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def put(x: int, y: int, dark) -> None:
        if 0 <= x < size and 0 <= y < size:
            grid[y][x], fixed[y][x] = int(bool(dark)), True

    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                put(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    centres = ALIGNMENT[version]
    for cy in centres:
        for cx in centres:
            if (cx, cy) in ((6, 6), (6, centres[-1]), (centres[-1], 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
    for i in range(8, size - 8):
        put(i, 6, i % 2 == 0)
        put(6, i, i % 2 == 0)

    # Format: level L (01) and the mask, with a BCH code, masked with 0x5412.
    data = (1 << 3) | mask
    rest = data
    for _ in range(10):
        rest = (rest << 1) ^ ((rest >> 9) * 0x537)
    form = ((data << 10) | rest) ^ 0x5412
    bit = lambda value, i: (value >> i) & 1
    for i in range(6):
        put(8, i, bit(form, i))
    put(8, 7, bit(form, 6))
    put(8, 8, bit(form, 7))
    put(7, 8, bit(form, 8))
    for i in range(9, 15):
        put(14 - i, 8, bit(form, i))
    for i in range(8):
        put(size - 1 - i, 8, bit(form, i))
    for i in range(8, 15):
        put(8, size - 15 + i, bit(form, i))
    put(8, size - 8, 1)

    if version >= 7:
        rest = version
        for _ in range(12):
            rest = (rest << 1) ^ ((rest >> 11) * 0x1F25)
        info = (version << 12) | rest
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            put(a, b, bit(info, i))
            put(b, a, bit(info, i))

    bits = [(byte >> i) & 1 for byte in codewords for i in range(7, -1, -1)]
    at = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for step in range(size):
            for x in (right, right - 1):
                y = size - 1 - step if (right + 1) & 2 == 0 else step
                if fixed[y][x]:
                    continue
                value = bits[at] if at < len(bits) else 0
                at += 1
                grid[y][x] = value ^ int(MASKS[mask](x, y))
        right -= 2
    return grid


def rows(text: str, mask: int | None = None):
    payload = text.encode("utf-8")
    version = next(
        (v for v in sorted(BLOCKS) if len(payload) <= (sum(BLOCKS[v][1]) * 8 - (20 if v >= 10 else 12)) // 8),
        None,
    )
    if version is None:
        return None
    codewords = _codewords(payload, version)
    if mask is None:
        grids = [_draw(codewords, version, m) for m in range(8)]
        grid = min(grids, key=_penalty)
    else:
        grid = _draw(codewords, version, mask)
    return ["".join(map(str, row)) for row in grid]
