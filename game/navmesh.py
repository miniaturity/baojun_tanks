import heapq
import math
import pygame

from game.wall import Wall
from game.types import *
from game.consts import *

CELL_SIZE = 10
GRID_W = SCREEN_WIDTH // CELL_SIZE
GRID_H = SCREEN_HEIGHT // CELL_SIZE

INF = float("inf")
DIAG = math.sqrt(2)
NEIGHBORS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, DIAG), (1, -1, DIAG), (-1, 1, DIAG), (-1, -1, DIAG)]


class FlowField:
    def __init__(self) -> None:
        self.goal_pos = Vector2(0, 0)
        self.built_cell: tuple[int, int] | None = None
        self.since_built = 0.0
        self.idle = 0.0  # second
        self.cost: list[list[float]] = []
        self.mesh: list[list[Vector2]] = [[Vector2(0, 0) for _ in range(GRID_W)]
                                          for _ in range(GRID_H)]


class NavMesh:
    def __init__(self, agent_radius: float = TANK_HEIGHT / 2 + 1,
                 rebuild_interval: float = 0.25, idle_timeout: float = 1.0) -> None:
        self.agent_radius = agent_radius
        self.rebuild_interval = rebuild_interval
        self.idle_timeout = idle_timeout
        self.walkable: list[list[bool]] = []
        self.fields: dict[object, FlowField] = {}
        self._bake()

    @staticmethod
    def world_to_cell(pos: Vector2) -> tuple[int, int]:
        x = max(0, min(GRID_W - 1, int(pos.x // CELL_SIZE)))
        y = max(0, min(GRID_H - 1, int(pos.y // CELL_SIZE)))
        return x, y

    @staticmethod
    def cell_to_world(cell: tuple[int, int]) -> Vector2:
        return Vector2((cell[0] + 0.5) * CELL_SIZE, (cell[1] + 0.5) * CELL_SIZE)

    def is_walkable(self, x: float, y: float) -> bool:
        cx = max(0, min(GRID_W - 1, int(x // CELL_SIZE)))
        cy = max(0, min(GRID_H - 1, int(y // CELL_SIZE)))
        return self.walkable[cy][cx]

    def _bake(self) -> None:
        r = self.agent_radius
        self.walkable = [[True] * GRID_W for _ in range(GRID_H)]
        for wall in Wall.walls:
            x0, y0 = wall.pos.x - r, wall.pos.y - r
            x1, y1 = wall.pos.x + wall.size.x + r, wall.pos.y + wall.size.y + r
            for cy in range(max(0, int(y0 // CELL_SIZE)), min(GRID_H, int(y1 // CELL_SIZE) + 1)):
                if not (y0 < (cy + 0.5) * CELL_SIZE < y1):
                    continue
                row = self.walkable[cy]
                for cx in range(max(0, int(x0 // CELL_SIZE)), min(GRID_W, int(x1 // CELL_SIZE) + 1)):
                    if x0 < (cx + 0.5) * CELL_SIZE < x1:
                        row[cx] = False
        self.fields = {}

    def _can_step(self, x: int, y: int, dx: int, dy: int) -> bool:
        nx, ny = x + dx, y + dy
        if not (0 <= nx < GRID_W and 0 <= ny < GRID_H) or not self.walkable[ny][nx]:
            return False
        if dx and dy:  # no corner cutting
            return self.walkable[y][x + dx] and self.walkable[y + dy][x]
        return True

    def _build(self, f: FlowField) -> None:
        goal = self.world_to_cell(f.goal_pos)
        cost = [[INF] * GRID_W for _ in range(GRID_H)]
        gx, gy = goal
        cost[gy][gx] = 0.0
        heap = [(0.0, gx, gy)]
        while heap: # Dijkstra outward from the goal
            c, x, y = heapq.heappop(heap)
            if c > cost[y][x]:
                continue
            for dx, dy, step in NEIGHBORS:
                nx, ny = x + dx, y + dy
                if 0 <= nx < GRID_W and 0 <= ny < GRID_H and self._can_step(nx, ny, -dx, -dy):
                    nc = c + step
                    if nc < cost[ny][nx]:
                        cost[ny][nx] = nc
                        heapq.heappush(heap, (nc, nx, ny))
        f.cost = cost

        for y in range(GRID_H):  # arrows point downhill
            for x in range(GRID_W):
                here = cost[y][x]
                best, best_d = here, None
                for dx, dy, _ in NEIGHBORS:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < GRID_W and 0 <= ny < GRID_H and cost[ny][nx] < best:
                        if here == INF or self._can_step(x, y, dx, dy):
                            best, best_d = cost[ny][nx], (dx, dy)
                v = f.mesh[y][x]
                if best_d is None:
                    v.update(0, 0)
                else:
                    v.update(best_d[0], best_d[1])
                    v.normalize_ip()
        f.built_cell = goal
        f.since_built = 0.0

    def request(self, key, goal_pos: Vector2) -> None:
        f = self.fields.get(key)
        if f is None:
            f = self.fields[key] = FlowField()
        f.goal_pos.update(goal_pos)
        f.idle = 0.0

    def update(self, dt: float) -> None:
        chosen, chosen_priority = None, -1.0
        for key in list(self.fields):
            f = self.fields[key]
            f.idle += dt
            f.since_built += dt
            if f.idle > self.idle_timeout:
                del self.fields[key]  # nobody is chasing this anymore
                continue
            if self.world_to_cell(f.goal_pos) == f.built_cell:
                continue
            if f.built_cell is None:
                priority = INF # never built = higher prioerity
            elif f.since_built >= self.rebuild_interval:
                priority = f.since_built
            else:
                continue
            if priority > chosen_priority:
                chosen, chosen_priority = f, priority
        if chosen is not None:
            self._build(chosen)  # at most one per frame

    def direction_at(self, key, pos: Vector2) -> Vector2:
        f = self.fields.get(key)
        if f is None or f.built_cell is None:
            return Vector2(0, 0)
        x, y = self.world_to_cell(pos)
        return f.mesh[y][x]

    def draw_debug(self, screen: pygame.Surface, key=None, stride: int = 2,
                   color=(80, 130, 220), draw_blocked: bool = True) -> None:
        f = self.fields.get(key)
        for y in range(0, GRID_H, stride):
            for x in range(0, GRID_W, stride):
                c = self.cell_to_world((x, y))
                if not self.walkable[y][x]:
                    if draw_blocked:
                        pygame.draw.circle(screen, (140, 60, 60, 160), c, 1)
                elif f is not None:
                    v = f.mesh[y][x]
                    if v.length_squared() > 0:
                        pygame.draw.line(screen, color, c, c + v * 8, 1)
                        pygame.draw.circle(screen, color, c + v * 8, 1)