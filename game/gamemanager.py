import random
import pygame

from game.types import *
from game.consts import *
from game.wall import Wall
from game.tank import Tank
from game.navmesh import NavMesh
from game.enemy_ai import EnemyAI, AIProfile
from game.debug import DebugOverlay
from game.powerup import POWERUP_TYPES


def cell_center(cell: tuple[int, int]) -> Vector2:
    c, r = cell
    return Vector2(MAZE_OFFSET_X + (c + 0.5) * TILE_SIZE,
                   MAZE_OFFSET_Y + (r + 0.5) * TILE_SIZE)


def generate_maze(extra_openings: int = 8) -> None:
    n = TILE_COUNT
    opened: set[frozenset] = set()
    visited = {(0, 0)}
    stack = [(0, 0)]
    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1))

    while stack:
        c, r = stack[-1]
        nbrs = [(c + dc, r + dr) for dc, dr in dirs
                if 0 <= c + dc < n and 0 <= r + dr < n and (c + dc, r + dr) not in visited]
        if nbrs:
            nxt = random.choice(nbrs)
            opened.add(frozenset(((c, r), nxt)))
            visited.add(nxt)
            stack.append(nxt)
        else:
            stack.pop()

    for _ in range(extra_openings):
        c, r = random.randrange(n), random.randrange(n)
        dc, dr = random.choice(dirs)
        if 0 <= c + dc < n and 0 <= r + dr < n:
            opened.add(frozenset(((c, r), (c + dc, r + dr))))

    T, S = WALL_THICKNESS, TILE_SIZE
    ox, oy = MAZE_OFFSET_X, MAZE_OFFSET_Y
    for r in range(n): # vert
        for c in range(n + 1):
            if c in (0, n) or frozenset(((c - 1, r), (c, r))) not in opened:
                Wall(Vector2(T, S + T), Vector2(ox + c * S - T // 2, oy + r * S - T // 2))
    for c in range(n): # horz
        for r in range(n + 1):
            if r in (0, n) or frozenset(((c, r - 1), (c, r))) not in opened:
                Wall(Vector2(S + T, T), Vector2(ox + c * S - T // 2, oy + r * S - T // 2))


class GameManager:

    def __init__(self, screen: pygame.Surface) -> None:
        enemy_difficulty = random.uniform(0, 1)
        self.screen = screen
        self.enemy_difficulty = enemy_difficulty 
        self.font = pygame.font.SysFont(None, 36)
        self.small_font = pygame.font.SysFont(None, 22)
        self.chinese_font = pygame.font.SysFont("microsoftyahei", 22)
        self.debug = DebugOverlay()
        self.reset()

    def reset(self) -> None:
        Wall.clear()
        generate_maze()
        self.navmesh = NavMesh()

        cells = [(c, r) for c in range(TILE_COUNT) for r in range(TILE_COUNT)]
        player_cell = random.choice(cells)
        self.player = Tank(cell_center(player_cell), PLAYER_COLOR, "player")

        others = sorted((c for c in cells if c != player_cell),
                        key=lambda c: -(abs(c[0] - player_cell[0]) + abs(c[1] - player_cell[1])))
        pool = others[:max(ENEMY_COUNT, len(others) // 2)]
        self.enemies = [Tank(cell_center(c), ENEMY_COLOR, "enemy")
                        for c in random.sample(pool, ENEMY_COUNT)]
        self.enemy_difficulty = random.uniform(0, 1)
        profile = AIProfile.from_difficulty(self.enemy_difficulty)
        self.ais = [EnemyAI(e, profile) for e in self.enemies]
        self.state = "playing" # "playing" | "won" | "lost"
        self.powerups = []
        for _ in range(MAX_POWERUPS):
            self._spawn_powerup()
        self.powerup_timer = random.uniform(*POWERUP_SPAWN_INTERVAL)

    @property
    def all_tanks(self) -> list[Tank]:
        return [self.player, *self.enemies]

    def handle_event(self, event: pygame.event.Event) -> None:
        if self.debug.handle_event(event):
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_r:
            self.reset()
        elif self.state == "playing":
            if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1) or \
               (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE):
                self.player.shoot()

    def _read_player_input(self) -> None:
        keys = pygame.key.get_pressed()
        self.player.move_input = (keys[pygame.K_w] or keys[pygame.K_UP]) - \
                                 (keys[pygame.K_s] or keys[pygame.K_DOWN])
        self.player.turn_input = (keys[pygame.K_d] or keys[pygame.K_RIGHT]) - \
                                 (keys[pygame.K_a] or keys[pygame.K_LEFT])
        self.player.point_gun(Vector2(pygame.mouse.get_pos()))
        if self.player.gunParams.hold_to_fire and (pygame.mouse.get_pressed()[0] or keys[pygame.K_SPACE]):
            self.player.shoot()

    def update(self, dt: float) -> None:
        if self.debug.paused:
            if not self.debug.consume_step():
                return
            dt = 1 / FPS
        record = self.debug.enabled and self.debug.layers["thinking"]
        for ai in self.ais:
            ai.record_debug = record

        if self.state == "playing":
            self._read_player_input()
            for ai in self.ais:
                ai.update(dt, self.all_tanks, Wall.walls, self.navmesh, self.powerups)
            self.navmesh.update(dt)
            self._update_powerups(dt)

        for tank in self.all_tanks:
            tank.update(dt, Wall.walls)

        self._check_hits()

        if self.state == "playing":
            if not self.player.alive:
                self.state = "lost"
            elif not any(e.alive for e in self.enemies):
                self.state = "won"

    def _update_powerups(self, dt: float) -> None:
        self.powerup_timer -= dt
        if self.powerup_timer <= 0:
            self.powerup_timer = random.uniform(*POWERUP_SPAWN_INTERVAL)
            self._spawn_powerup()
        for pu in self.powerups[:]:
            for tank in self.all_tanks:
                if tank.alive and (tank.pos - pu.pos).length() < tank.radius + POWERUP_SIZE / 2:
                    pu.consume(tank)
                    self.powerups.remove(pu)
                    break

    def _spawn_powerup(self) -> None:
        if len(self.powerups) >= MAX_POWERUPS:
            return
        used = {tuple(pu.pos) for pu in self.powerups}
        free = [(c, r) for c in range(TILE_COUNT) for r in range(TILE_COUNT)
                if tuple(cell_center((c, r))) not in used
                and all((tk.pos - cell_center((c, r))).length() > TILE_SIZE * 0.6 for tk in self.all_tanks)]
        if free:
            kind = random.choice(POWERUP_TYPES)
            self.powerups.append(kind(cell_center(random.choice(free))))

    @staticmethod
    def _kill(tank: Tank, owner: Tank) -> None:
        tank.alive = False
        tank.killed_by = owner
        tank.velocity = 0

    def _damage(self, tank: Tank, owner: Tank) -> None:
        if not tank.absorb_hit():
            self._kill(tank, owner)

    def _check_hits(self) -> None:
        for owner in self.all_tanks:
            for beam in owner.beams: 
                if beam.resolved:
                    continue
                beam.resolved = True
                for tank in self.all_tanks:
                    if tank.alive and beam.hits(tank, owner):
                        self._damage(tank, owner)
            for trail in owner.trails: # tesla
                for tank in self.all_tanks:
                    if tank.alive and trail.hits(tank, owner):
                        self._damage(tank, owner)
            for bullet in owner.bullets:
                for tank in self.all_tanks:
                    if not (tank.alive and bullet.alive):
                        continue
                    if (bullet.pos - tank.pos).length() < tank.radius + bullet.radius:
                        self._damage(tank, owner)
                        bullet.alive = False

    def draw(self) -> None:
        self.screen.fill(BG_COLOR)
        for wall in Wall.walls:
            wall.draw(self.screen)
        for pu in self.powerups:
            pu.draw(self.screen)
        for tank in self.all_tanks:
            tank.draw(self.screen)
        self.debug.draw(self.screen, self)

        left = sum(e.alive for e in self.enemies)
        hud = self.small_font.render(f"Enemies {left} | Difficulty {self.enemy_difficulty:.2f}",
                                     True, (160, 160, 170))
        self.screen.blit(hud, (6, 3))
        baojun = [
            ("宝骏", (66, 245, 66)),
            ("tanks", (51, 160, 232))
        ]
        self._render_multicolor_line(self.chinese_font, self.screen, baojun, (6, SCREEN_HEIGHT - 36))

        g = self.player.gunParams
        if self.player.ammo is not None:
            gun = self.small_font.render(f"{g.name} x{self.player.ammo}", True, g.color)
            self.screen.blit(gun, (6, 20))
        if self.player.shielded:
            self.screen.blit(self.small_font.render("SHIELD", True, SHIELD_COLOR), (6, 36))

        if self.state != "playing":
            msg = "YOU WIN!" if self.state == "won" else "YOU DIED"
            text = self.font.render(msg + "  (press R)", True, (255, 255, 255))
            rect = text.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2))
            pygame.draw.rect(self.screen, (0, 0, 0), rect.inflate(30, 20))
            self.screen.blit(text, rect)

    def _render_multicolor_line(self, font, surface, text_chunks, position):
        x, y = position
        
        for text, color in text_chunks:
            text_surface = font.render(text, True, color)
            
            surface.blit(text_surface, (x, y))
            x += text_surface.get_width()