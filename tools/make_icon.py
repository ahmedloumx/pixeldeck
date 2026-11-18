"""PixelDeck's icon: the blue desk bot with a joystick on its head (red ball, steel stick, a base
with a fire button), standing on the brand tile. Drawn on a 32x32 pixel grid as SVG rects; the
joystick is the same one the bot wears in the app, where someone plays it now and then.

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
    "s": "#9aa3b2", "S": "#4a5262", "a": "#f6c445",  # the joystick: steel stick, dark base, fire button
    "L": "#16203a",  # the glow behind the bot
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

# a soft glow behind the bot and a floor line under its feet
for y in range(6, 27):
    for x in range(6, 26):
        if grid[y][x] == "T" and (x - 15.5) ** 2 / 90 + (y - 15) ** 2 / 110 < 1:
            grid[y][x] = "L"
rect(8, 25, 23, 25, "F")

# the bot, joystick and all, from sprites.js (frame 0)
bot = [".....rr.....", "....rwrr....", "....rrrr....", ".....rr.....", ".....ss.....", "....SSaS....",
       "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybwwbbbwwb.", ".ybwkbbbwkb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.",
       "..bbbbbbbb..", "...B....B...", "..BB....BB.."]
BX, BY = 10, 9
# a dark outline so it stands off the tile
for y, row in enumerate(bot):
    for x, c in enumerate(row):
        if c != ".":
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ry, rx = y + dy, x + dx
                inside = 0 <= ry < len(bot) and 0 <= rx < len(row) and bot[ry][rx] != "."
                if not inside and grid[BY + ry][BX + rx] not in (".", "E"):
                    grid[BY + ry][BX + rx] = "k"
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
