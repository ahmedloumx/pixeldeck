"""PixelDeck's icon: an IDE window (title bar, line numbers, code) with the desk bot peeking
over its corner, looking up at the code. Drawn on a 32x32 pixel grid, kept minimal so the bot leads, written as SVG rects.

Run: python3 tools/make_icon.py static/app-icon.svg"""
import sys

N = 32
C = {
    "T": "#0f1420",  # tile
    "E": "#1552dc",  # tile ring, the brand blue
    "F": "#2b3448",  # window frame and title bar
    "D": "#0a0d14",  # editor background
    "g": "#3a4356",  # gutter numbers
    "R": "#ff5f56", "Y": "#ffbd2e", "G": "#27c93f",  # window dots
    "1": "#6ea8ff", "2": "#ffcf5c", "3": "#7ee08a", "4": "#ff7ab6", "5": "#8a93a6",  # code
    # the bot, from sprites.js (b blue, B deep blue, y yellow, r red, w white, k dark)
    "b": "#2f62ec", "B": "#1b3a9c", "y": "#f6c445", "r": "#ea4a4f", "w": "#f7f8fa", "k": "#141720",
}
grid = [["." for _ in range(N)] for _ in range(N)]


def put(x, y, c):
    if 0 <= x < N and 0 <= y < N:
        grid[y][x] = c


def rect(x0, y0, x1, y1, c):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            put(x, y, c)


# the tile: stepped corners, a brand blue ring, dark inside
cut = {0: 2, 1: 1}
for y in range(N):
    inset = cut.get(y, cut.get(N - 1 - y, 0))
    for x in range(inset, N - inset):
        grid[y][x] = "T"
for y in range(N):
    inset = cut.get(y, cut.get(N - 1 - y, 0))
    row = [x for x in range(N) if grid[y][x] == "T"]
    if not row:
        continue
    if y in (0, N - 1):
        for x in row:
            grid[y][x] = "E"
    else:
        grid[y][row[0]] = grid[y][row[-1]] = "E"
for y, x in ((1, 1), (1, N - 2), (N - 2, 1), (N - 2, N - 2)):
    grid[y][x] = "E"

# the IDE window, kept plain: a title bar with its dots and three lines of code
rect(3, 5, 25, 23, "F")
rect(4, 8, 24, 22, "D")
for x, c in ((5, "R"), (7, "Y"), (9, "G")):
    rect(x, 6, x, 6, c)
for y, x0, x1, c in ((11, 6, 13, "1"), (14, 8, 16, "5"), (17, 6, 10, "2")):
    rect(x0, y, x1, y, c)
    rect(x0, y + 1, x1, y + 1, c)

# the bot, peeking over the window's bottom right corner, eyes up and to the left
bot = [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybkwbbbkwb.", ".ybwwbbbwwb.",
       ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."]
BX, BY = 18, 17
# a dark outline so the bot stands off the window behind it
for y, row in enumerate(bot):
    for x, c in enumerate(row):
        if c != ".":
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = BX + x + dx, BY + y + dy
                ry, rx = y + dy, x + dx
                inside = 0 <= ry < len(bot) and 0 <= rx < len(row) and bot[ry][rx] != "."
                if not inside and grid[ny][nx] not in (".", "E"):
                    grid[ny][nx] = "k"
for y, row in enumerate(bot):
    for x, c in enumerate(row):
        if c != ".":
            grid[BY + y][BX + x] = c

rects = []
for y, row in enumerate(grid):
    x = 0
    while x < N:
        c = row[x]
        if c == ".":
            x += 1
            continue
        run = x
        while run + 1 < N and row[run + 1] == c:
            run += 1
        rects.append(f'<rect x="{x}" y="{y}" width="{run - x + 1}" height="1" fill="{C[c]}"/>')
        x = run + 1
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {N} {N}" width="512" height="512" shape-rendering="crispEdges">'
       f'<title>PixelDeck</title>{"".join(rects)}</svg>\n')
open(sys.argv[1], "w").write(svg)
print(len(rects), "rects,", len(svg), "bytes")
