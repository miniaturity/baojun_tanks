from game.types import *
from game.consts import *
import pygame


class Wall:
    walls: list["Wall"] = []

    def __init__(self, size: Vector2, pos: Vector2) -> None:
        self.size = size
        self.pos = pos  # top-left corner
        self.rect = pygame.Rect(int(pos.x), int(pos.y), int(size.x), int(size.y))
        Wall.walls.append(self)

    @classmethod
    def clear(cls) -> None:
        cls.walls.clear()

    def contact(self, other):
        left, top = self.pos.x, self.pos.y
        right, bottom = left + self.size.x, top + self.size.y
        p = other.pos

        closest_x = max(left, min(p.x, right))
        closest_y = max(top, min(p.y, bottom))
        delta = Vector2(p.x - closest_x, p.y - closest_y)
        dist = delta.length()

        if dist >= other.radius:
            return None
        if dist > 1e-6:
            return delta / dist, other.radius - dist

        gaps = [
            (p.x - left, Vector2(-1, 0)),
            (right - p.x, Vector2(1, 0)),
            (p.y - top, Vector2(0, -1)),
            (bottom - p.y, Vector2(0, 1)),
        ]
        gap, normal = min(gaps, key=lambda g: g[0])
        return normal, gap + other.radius

    def collides_with(self, other) -> bool:
        return self.contact(other) is not None

    def draw(self, screen: pygame.Surface) -> None:
        pygame.draw.rect(screen, WALL_COLOR, self.rect)

        