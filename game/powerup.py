import pygame
from game.types import *
from game.consts import *
from game.tank import Tank, GunParams, DEFAULT_GUN


class Powerup:
    gun: GunParams = DEFAULT_GUN
    ammo: int | None = None

    @property
    def color(self) -> tuple[int, int, int]:
        return self.gun.color

    def wanted_by(self, tank: Tank) -> bool:
        """AI: is this worth fetching for `tank`?"""
        return True

    def __init__(self, pos: Vector2, icon: pygame.Surface | None = None) -> None:
        self.pos = Vector2(pos)
        self.icon = icon if icon is not None else self.make_icon()

    # NOTE: Override in child for non-gun powerups
    def consume(self, tank: Tank) -> None:
        tank.set_gun(self.gun, self.ammo)

    def make_icon(self) -> pygame.Surface:
        size = POWERUP_SIZE
        s = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size / 2
        pygame.draw.circle(s, (20, 20, 24), (c, c), c)
        pygame.draw.circle(s, self.color, (c, c), c, 2)
        self.draw_symbol(s, c)
        return s

    def draw_symbol(self, s: pygame.Surface, c: float) -> None:
        pass

    def draw(self, screen: pygame.Surface) -> None:
        pulse = 2 + int(2 * (1 + pygame.math.Vector2(1, 0).rotate(pygame.time.get_ticks() * 0.2).x) / 2)
        pygame.draw.circle(screen, self.color, self.pos, POWERUP_SIZE / 2 + pulse, 1)
        screen.blit(self.icon, self.icon.get_rect(center=self.pos))


SHOTGUN = GunParams(
    name="SHOTGUN", count=5, bloom=36, cooldown=0.7, radius=2, ricochets=2,
    color=(255, 160, 60), max_bullets=10, ideal_range=80, effective_range=170)

RAILGUN = GunParams(
    name="RAILGUN", cooldown=1.5, radius=2, ricochets=3, hitscan=True, speed=20000,
    color=(90, 220, 255), ideal_range=220)

MACHINEGUN = GunParams(
    name="MACHINEGUN", cooldown=0.05, bloom=12, radius=2, ricochets=1,
    color=(66, 245, 111), max_bullets=30, ideal_range=110, effective_range=240,
    hold_to_fire=True
)

TESLA = GunParams(
    name="TESLA", cooldown=0.9, radius=3, speed=BULLET_SPEED * 2, ricochets=3, lifetime=3.0,
    color=(170, 110, 255), max_bullets=3, ideal_range=170, trail_duration=5.0)

LASER = GunParams(
    name="LASER", cooldown=0.1, radius=1, ricochets=0, hitscan=True, speed=2000,
    color=(255, 0, 0), max_bullets=5,
)

class ShotgunPowerup(Powerup):
    gun, ammo = SHOTGUN, 8

    def draw_symbol(self, s, c) -> None:
        for ang in (-30, -15, 0, 15, 30):
            q = Vector2(c, c + 6) + Vector2(0, -9).rotate(ang)
            pygame.draw.circle(s, self.gun.color, q, 2)

class LaserPowerup(Powerup):
    gun, ammo = LASER, 8

    def draw_symbol(self, s: pygame.Surface, c: float) -> None:
        pygame.draw.line(s, self.gun.color, (c - 8, c), (c + 8, c), 2)

class RailgunPowerup(Powerup):
    gun, ammo = RAILGUN, 2

    def draw_symbol(self, s, c) -> None:
        pygame.draw.line(s, (255, 255, 255), (c - 8, c), (c + 8, c), 2)
        pygame.draw.polygon(s, self.gun.color, [(c + 9, c), (c + 3, c - 4), (c + 3, c + 4)])
        pygame.draw.circle(s, self.gun.color, (c - 8, c), 2)

class MachinegunPowerup(Powerup):
    gun, ammo = MACHINEGUN, 40

    def draw_symbol(self, s: pygame.Surface, c: float) -> None:
        gap = 5

        for pos in (-gap, 0, gap):
            q = Vector2(c + pos, c)
            pygame.draw.circle(s, self.gun.color, q, 2)


class TeslaPowerup(Powerup):
    gun, ammo = TESLA, 4

    def draw_symbol(self, s, c) -> None:
        bolt = [(c + 3, c - 9), (c - 5, c + 1), (c - 1, c + 1), (c - 3, c + 9), (c + 5, c - 2), (c + 1, c - 2)]
        pygame.draw.polygon(s, self.gun.color, bolt)


class ShieldPowerup(Powerup):
    color: tuple[int, int, int] = SHIELD_COLOR

    def consume(self, tank: Tank) -> None:
        tank.shielded = True

    def wanted_by(self, tank: Tank) -> bool:
        return not tank.shielded

    def draw_symbol(self, s, c) -> None:
        shape = [(c - 7, c - 7), (c + 7, c - 7), (c + 7, c + 1), (c, c + 9), (c - 7, c + 1)]
        pygame.draw.polygon(s, self.color, shape, 2)
        pygame.draw.line(s, self.color, (c, c - 6), (c, c + 5), 1)

POWERUP_TYPES: list[type[Powerup]] = [ShotgunPowerup, RailgunPowerup, MachinegunPowerup, TeslaPowerup, ShieldPowerup, LaserPowerup]