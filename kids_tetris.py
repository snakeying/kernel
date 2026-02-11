"""Kid-friendly Tetris game for ages 5-6."""
import pygame, random, sys

# --- Constants ---
CELL = 40
COLS, ROWS = 10, 20
BOARD_W, BOARD_H = COLS * CELL, ROWS * CELL
SIDE_W = 220
WIN_W, WIN_H = BOARD_W + SIDE_W, BOARD_H
FPS = 60
FALL_INTERVAL = 800  # ms, constant speed

COLORS = {
    "I": (0, 220, 255), "O": (255, 220, 0), "T": (180, 50, 255),
    "S": (50, 255, 50), "Z": (255, 60, 60), "L": (255, 150, 0), "J": (80, 80, 255),
}
BG = (30, 30, 50)
GRID_COLOR = (50, 50, 70)
FLASH_COLOR = (255, 255, 255)

SHAPES = {
    "I": [[1, 1, 1, 1]],
    "O": [[1, 1], [1, 1]],
    "T": [[0, 1, 0], [1, 1, 1]],
    "S": [[0, 1, 1], [1, 1, 0]],
    "Z": [[1, 1, 0], [0, 1, 1]],
    "L": [[1, 0], [1, 0], [1, 1]],
    "J": [[0, 1], [0, 1], [1, 1]],
}

def rotate(shape):
    return [list(row) for row in zip(*shape[::-1])]

class Game:
    def __init__(self):
        self.board = [[None] * COLS for _ in range(ROWS)]
        self.score = 1000
        self.lines = 0
        self.game_over = False
        self.flash_rows = []
        self.flash_timer = 0
        self.last_fall = pygame.time.get_ticks()
        self.last_sec = pygame.time.get_ticks()
        self.spawn()

    def spawn(self):
        self.kind = random.choice(list(SHAPES))
        self.shape = [r[:] for r in SHAPES[self.kind]]
        self.x = COLS // 2 - len(self.shape[0]) // 2
        self.y = 0
        if self.collides(self.shape, self.x, self.y):
            self.game_over = True

    def collides(self, shape, x, y):
        for r, row in enumerate(shape):
            for c, v in enumerate(row):
                if v:
                    nx, ny = x + c, y + r
                    if nx < 0 or nx >= COLS or ny >= ROWS:
                        return True
                    if ny >= 0 and self.board[ny][nx]:
                        return True
        return False

    def lock(self):
        for r, row in enumerate(self.shape):
            for c, v in enumerate(row):
                if v and self.y + r >= 0:
                    self.board[self.y + r][self.x + c] = COLORS[self.kind]
        # Check lines
        full = [r for r in range(ROWS) if all(self.board[r])]
        if full:
            self.flash_rows = full
            self.flash_timer = 300  # ms
            self.lines += len(full)
            self.score += 100 * len(full)
        else:
            self.spawn()

    def clear_rows(self):
        for r in sorted(self.flash_rows, reverse=True):
            del self.board[r]
            self.board.insert(0, [None] * COLS)
        self.flash_rows = []
        self.spawn()

    def move(self, dx, dy):
        if not self.collides(self.shape, self.x + dx, self.y + dy):
            self.x += dx
            self.y += dy
            return True
        return False

    def try_rotate(self):
        new = rotate(self.shape)
        # Try normal, then kick left/right
        for kick in [0, -1, 1, -2, 2]:
            if not self.collides(new, self.x + kick, self.y):
                self.shape = new
                self.x += kick
                return

    def hard_drop(self):
        while not self.collides(self.shape, self.x, self.y + 1):
            self.y += 1
        self.lock()

    def update(self, now):
        if self.game_over:
            return
        # Flash animation
        if self.flash_timer > 0:
            self.flash_timer -= now - self.last_fall
            self.last_fall = now
            if self.flash_timer <= 0:
                self.clear_rows()
            return
        # Score countdown
        if now - self.last_sec >= 1000:
            self.last_sec += 1000
            self.score -= 1
            if self.score <= 0:
                self.score = 0
                self.game_over = True
        # Auto fall
        if now - self.last_fall >= FALL_INTERVAL:
            self.last_fall = now
            if not self.move(0, 1):
                self.lock()

def draw_block(surf, x, y, color):
    rect = pygame.Rect(x, y, CELL, CELL)
    pygame.draw.rect(surf, color, rect)
    pygame.draw.rect(surf, (255, 255, 255), rect, 1)

def draw(surf, game, font_big, font_sm):
    surf.fill(BG)
    # Grid
    for r in range(ROWS):
        for c in range(COLS):
            pygame.draw.rect(surf, GRID_COLOR, (c * CELL, r * CELL, CELL, CELL), 1)
            if game.board[r][c]:
                draw_block(surf, c * CELL, r * CELL, game.board[r][c])
    # Flash
    if game.flash_timer > 0:
        for r in game.flash_rows:
            pygame.draw.rect(surf, FLASH_COLOR, (0, r * CELL, BOARD_W, CELL))
    # Current piece
    if not game.game_over and game.flash_timer <= 0:
        # Ghost
        gy = game.y
        while not game.collides(game.shape, game.x, gy + 1):
            gy += 1
        for r, row in enumerate(game.shape):
            for c, v in enumerate(row):
                if v:
                    gx_px = (game.x + c) * CELL
                    gy_px = (gy + r) * CELL
                    pygame.draw.rect(surf, (*COLORS[game.kind][:3],), (gx_px, gy_px, CELL, CELL), 2)
        # Actual piece
        for r, row in enumerate(game.shape):
            for c, v in enumerate(row):
                if v:
                    draw_block(surf, (game.x + c) * CELL, (game.y + r) * CELL, COLORS[game.kind])
    # Side panel
    px = BOARD_W + 10
    pygame.draw.rect(surf, (40, 40, 60), (BOARD_W, 0, SIDE_W, WIN_H))
    # Score
    surf.blit(font_big.render("SCORE", True, (255, 255, 100)), (px, 10))
    surf.blit(font_big.render(str(game.score), True, (255, 255, 255)), (px, 50))
    # Lines
    surf.blit(font_sm.render(f"Lines: {game.lines}", True, (200, 200, 200)), (px, 100))
    # Controls
    y = 160
    for txt in ["-- Controls --", "Left/Right: Move", "Down: Faster", "Up: Rotate", "Space: Drop!", "R: Restart"]:
        surf.blit(font_sm.render(txt, True, (180, 180, 220)), (px, y))
        y += 28
    # Game over
    if game.game_over:
        overlay = pygame.Surface((BOARD_W, BOARD_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        surf.blit(overlay, (0, 0))
        go = font_big.render("GAME OVER", True, (255, 80, 80))
        surf.blit(go, (BOARD_W // 2 - go.get_width() // 2, BOARD_H // 2 - 40))
        rs = font_sm.render("Press R to restart!", True, (255, 255, 255))
        surf.blit(rs, (BOARD_W // 2 - rs.get_width() // 2, BOARD_H // 2 + 20))

def main():
    pygame.init()
    surf = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("Kids Tetris!")
    clock = pygame.time.Clock()
    font_big = pygame.font.SysFont("arial", 36, bold=True)
    font_sm = pygame.font.SysFont("arial", 20)
    game = Game()

    while True:
        now = pygame.time.get_ticks()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_r:
                    game = Game()
                if not game.game_over and game.flash_timer <= 0:
                    if ev.key == pygame.K_LEFT:
                        game.move(-1, 0)
                    elif ev.key == pygame.K_RIGHT:
                        game.move(1, 0)
                    elif ev.key == pygame.K_DOWN:
                        game.move(0, 1)
                    elif ev.key == pygame.K_UP:
                        game.try_rotate()
                    elif ev.key == pygame.K_SPACE:
                        game.hard_drop()
        game.update(now)
        draw(surf, game, font_big, font_sm)
        pygame.display.flip()
        clock.tick(FPS)

if __name__ == "__main__":
    main()
