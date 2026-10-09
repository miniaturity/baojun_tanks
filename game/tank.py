from game.types import *
from game.consts import *
from game.wall import Wall
from dataclasses import dataclass, field
import random
import pygame

INF = float("inf")

@dataclass(frozen=True)
class GunParams:
    count: int = 1
    cooldown: float = SHOOT_COOLDOWN
    bloom: int = 0
    texture: pygame.Surface | None = None
    hold_to_fire: bool = False

    radius: float = BULLET_RADIUS
    color: tuple[int, int, int] = BULLET_COLOR
    lifetime: float = BULLET_LIFETIME
    ricochets: int = BULLET_RICOCHETS
    speed: int = BULLET_SPEED

    name: str = "gun"
    hitscan: bool = False # instant beam instead of a flying bullet
    max_bullets: int = MAX_BULLETS_PER_TANK 
    ideal_range: float = 130 # AI: distance it likes to fight from
    effective_range: float = INF  # AI: won't fire from farther than this
    trail_duration: float = 0.0 # > 0: each bullet leaves a lethal trail that lingers this long (s)


    def spread_angles(self) -> list[float]:
        if self.count <= 1:
            return [random.uniform(-self.bloom / 2, self.bloom / 2)]
        return [-self.bloom / 2 + self.bloom * i / (self.count - 1) for i in range(self.count)]


DEFAULT_GUN: GunParams = GunParams()

class Tank:

    def __init__(self, pos: Vector2, color: tuple[int, int, int], tag: str) -> None:
        self.color = color
        self.pos = pos

        self.velocity = 0.0 
        self.acceleration = 0.0
        self.bodyDirection = Vector2(0, -1)
        self.gunDirection = Vector2(0, -1)
        self.tag = tag

        self.mass = 1.0
        self.drag = 4.0
        self.max_speed = 100
        self.engine_power = 400
        self.turn_speed = 180 # deg/s

        self.radius = TANK_HEIGHT / 2 
        self.gun_length = TANK_HEIGHT * 0.8

        self.move_input = 0
        self.turn_input = 0

        self.shoot_timer = 0.0
        self.bullets: list["Bullet"] = []
        self.alive = True
        self.killed_by: "Tank | None" = None

        self.gunParams = DEFAULT_GUN
        self.ammo: int | None = None      # None = unlimited (default gun)
        self.beams: list["Beam"] = []
        self.trails: list["Trail"] = []

        self.shielded = False             # absorbs the next hit
        self.shield_grace = 0.0           # brief immunity right after the shield pops

    def set_gun(self, gun: GunParams, ammo: int | None = None) -> None:
        self.gunParams, self.ammo = gun, ammo

    def absorb_hit(self) -> bool:
        """True if a shield swallowed this hit (the tank survives)."""
        if self.shield_grace > 0:
            return True
        if self.shielded:
            self.shielded = False
            self.shield_grace = SHIELD_GRACE
            return True
        return False

    @property
    def velocity_vector(self) -> Vector2:
        return self.bodyDirection * self.velocity

    def point_gun(self, to: Vector2) -> None:
        dir_to_target = to - self.pos
        if dir_to_target.length_squared() > 0:
            self.gunDirection = dir_to_target.normalize()

    def shoot(self) -> "list[Bullet] | None":
        g = self.gunParams
        if not self.alive or self.shoot_timer > 0:
            return None
        if not g.hitscan and len(self.bullets) + g.count > g.max_bullets:
            return None
        shots = []
        for ang in g.spread_angles():
            if g.count > 0:
                ang += random.uniform(-1.5, 1.5)
            shots.append(make_bullet(self.pos, self.gun_length, self.gunDirection.rotate(ang), g, Wall.walls))
        if g.hitscan:
            self.beams.append(Beam(shots[0], g.color))
        else:
            self.bullets.extend(shots)
            if g.trail_duration > 0:
                self.trails.extend(Trail(b, g.color, g.trail_duration) for b in shots)
        self.shoot_timer = g.cooldown
        if self.ammo is not None:
            self.ammo -= 1
            if self.ammo <= 0:
                self.set_gun(DEFAULT_GUN)
        return shots

    def draw(self, screen: pygame.Surface) -> None:
        for beam in self.beams:
            beam.draw(screen)
        for tr in self.trails:
            tr.draw(screen)
        for b in self.bullets:
            b.draw(screen)
        if not self.alive:
            return

        forward = self.bodyDirection
        right = Vector2(-forward.y, forward.x)
        hl, hw = TANK_HEIGHT / 2, TANK_WIDTH / 2

        corners = [
            self.pos + forward * hl + right * hw,
            self.pos + forward * hl - right * hw,
            self.pos - forward * hl - right * hw,
            self.pos - forward * hl + right * hw,
        ]
        pygame.draw.polygon(screen, self.color, corners)
        original_color = pygame.Color(self.color)
        darkened = original_color.lerp((0, 0, 0), 0.3)
        pygame.draw.line(screen, darkened, corners[0], corners[1], 3)

        pygame.draw.line(screen, (40, 40, 40), self.pos,
                         self.pos + self.gunDirection * self.gun_length, 5)
        pygame.draw.circle(screen, (60, 60, 60), self.pos, hw * 0.6)
        if self.gunParams is not DEFAULT_GUN:      # tint the turret ring with the powerup colour
            pygame.draw.circle(screen, self.gunParams.color, self.pos, hw * 0.6, 2)
        if self.shielded:
            pulse = 1 + int(1.5 * (1 + pygame.math.Vector2(1, 0).rotate(pygame.time.get_ticks() * 0.3).x) / 2)
            pygame.draw.circle(screen, SHIELD_COLOR, self.pos, self.radius + 4 + pulse, 2)
            pygame.draw.circle(screen, tuple(c // 2 for c in SHIELD_COLOR), self.pos, self.radius + 2, 1)
        elif self.shield_grace > 0:     # pop flash
            grow = (1 - self.shield_grace / SHIELD_GRACE)
            pygame.draw.circle(screen, (255, 255, 255), self.pos, self.radius + 5 + 14 * grow, 2)

    def update(self, dt, walls=None) -> None:
        walls = Wall.walls if walls is None else walls
        self.shoot_timer = max(0.0, self.shoot_timer - dt)
        self.shield_grace = max(0.0, self.shield_grace - dt)

        if self.alive:
            self._move(dt, walls)

        # bullets keep flying even if their owner is dead
        for b in self.bullets:
            b.update(dt, walls)
        self.bullets = [b for b in self.bullets if b.alive]
        for beam in self.beams:
            beam.age += dt
        self.beams = [bm for bm in self.beams if bm.age < Beam.DURATION]
        for tr in self.trails:
            tr.update(dt)
        self.trails = [tr for tr in self.trails if tr.alive]

    def _move(self, dt, walls) -> None:
        self.bodyDirection.rotate_ip(self.turn_input * self.turn_speed * dt)

        self.acceleration = self.move_input * self.engine_power
        self.velocity += (self.acceleration / self.mass - self.drag * self.velocity) * dt
        self.velocity = max(-self.max_speed * 0.6, min(self.max_speed, self.velocity))

        self.pos += self.bodyDirection * self.velocity * dt

        for _ in range(2):
            for wall in walls:
                hit = wall.contact(self)
                if hit:
                    normal, depth = hit
                    self.pos += normal * depth


def _ray_vs_rect(px, py, vx, vy, x0, y0, x1, y1):
    tmin, tmax, axis = -INF, INF, 0
    for a, (pa, va, lo, hi) in enumerate(((px, vx, x0, x1), (py, vy, y0, y1))):
        if abs(va) < 1e-9:
            if pa < lo or pa > hi:
                return None
        else:
            t1, t2 = (lo - pa) / va, (hi - pa) / va
            if t1 > t2:
                t1, t2 = t2, t1
            if t1 > tmin:
                tmin, axis = t1, a
            tmax = min(tmax, t2)
            if tmin > tmax:
                return None
    if tmin <= 1e-6: # already inside/touching/moving away
        return None
    return tmin, axis


def nearest_wall_hit(px, py, vx, vy, radius, walls):
    best_t, best_axis = INF, 0
    for w in walls:
        hit = _ray_vs_rect(px, py, vx, vy,
                           w.pos.x - radius, w.pos.y - radius,
                           w.pos.x + w.size.x + radius, w.pos.y + w.size.y + radius)
        if hit and hit[0] < best_t:
            best_t, best_axis = hit
    return None if best_t == INF else (best_t, best_axis)


class Bullet:

    def __init__(self, radius, pos: Vector2, velocity: Vector2, ricochet: int = 5,
                 lifetime: float = BULLET_LIFETIME, color=BULLET_COLOR) -> None:
        self.pos = Vector2(pos)
        self.radius = radius
        self.velocity = Vector2(velocity)
        self.ricochet = ricochet
        self.lifetime = lifetime
        self.color = color
        self.alive = True

        self.age = 0.0
       
        self.path: list[tuple[float, float, Vector2, Vector2]] = []

    def predict_path(self, walls) -> None:
        p, v = Vector2(self.pos), Vector2(self.velocity)
        t = self.age
        t_death = self.age + self.lifetime
        self.path = []

        for _ in range(self.ricochet + 1):
            hit = nearest_wall_hit(p.x, p.y, v.x, v.y, self.radius, walls)
            if hit is None or t + hit[0] >= t_death: # runs out of time first
                self.path.append((t, t_death, Vector2(p), Vector2(v)))
                return
            hit_t, axis = hit
            self.path.append((t, t + hit_t, Vector2(p), Vector2(v)))
            p = p + v * hit_t
            t += hit_t
            if axis == 0:
                v.x = -v.x
            else:
                v.y = -v.y

    def position_at(self, age: float, max_leg: int | None = None):
        for i, (t0, t1, p, v) in enumerate(self.path):
            if max_leg is not None and i > max_leg:
                return None
            if age <= t1:
                dt = max(0.0, age - t0)
                return p.x + v.x * dt, p.y + v.y * dt
        return None

    def update(self, dt, walls=None) -> None:
        walls = Wall.walls if walls is None else walls
        self.age += dt
        self.step(dt, walls)
        self.lifetime -= dt
        if self.lifetime <= 0:
            self.alive = False

    def step(self, dt, walls) -> None:
        for _ in range(16):
            hit = nearest_wall_hit(self.pos.x, self.pos.y, self.velocity.x, self.velocity.y,
                                   self.radius, walls)
            if hit is None or hit[0] > dt:
                self.pos += self.velocity * dt
                return
            hit_t, axis = hit
            self.pos += self.velocity * hit_t
            dt -= hit_t
            if axis == 0:
                self.velocity.x = -self.velocity.x
            else:
                self.velocity.y = -self.velocity.y
            self.ricochet -= 1
            if self.ricochet < 0:
                self.alive = False
                return

    def draw(self, screen: pygame.Surface) -> None:
        pygame.draw.circle(screen, self.color, self.pos, self.radius)


def make_bullet(pos: Vector2, gun_length: float, direction: Vector2, g: GunParams, walls) -> Bullet:
    b = Bullet(g.radius, pos, direction * g.speed, g.ricochets, g.lifetime, g.color)
    b.step(gun_length / g.speed, walls)
    b.predict_path(walls)
    return b


def _seg_dist(a: Vector2, b: Vector2, p: Vector2) -> float:
    ab = b - a
    l2 = ab.length_squared()
    if l2 == 0:
        return a.distance_to(p)
    s = max(0.0, min(1.0, (p - a).dot(ab) / l2))
    return (a + ab * s).distance_to(p)


class Beam:
    DURATION = 0.18

    def __init__(self, bullet: Bullet, color) -> None:
        self.color = color
        self.age = 0.0
        self.resolved = False
        self.pts = [Vector2(bullet.path[0][2])] if bullet.path else [Vector2(bullet.pos)]
        for t0, t1, p, v in bullet.path:
            self.pts.append(p + v * (t1 - t0))

    def hits(self, tank, owner) -> bool:
        r = tank.radius + 2
        for i in range(len(self.pts) - 1):
            if tank is owner and i == 0: # the first leg leaves the muzzle, can't hit the shooter
                continue
            if _seg_dist(self.pts[i], self.pts[i + 1], tank.pos) < r:
                return True
        return False

    def draw(self, screen: pygame.Surface) -> None:
        if len(self.pts) < 2:
            return
        k = max(0.0, 1 - self.age / self.DURATION)
        mix = lambda c, f: tuple(int(bg + (x - bg) * f) for bg, x in zip(BG_COLOR, c))
        pygame.draw.lines(screen, mix(self.color, 0.6 * k), False, self.pts, 3 + int(6 * k))
        pygame.draw.lines(screen, mix((255, 255, 255), k), False, self.pts, max(1, int(3 * k)))


def _mix(c, f):
    return tuple(int(bg + (x - bg) * f) for bg, x in zip(BG_COLOR, c))


class Trail:

    def __init__(self, bullet: Bullet, color, duration: float) -> None:
        self.bullet = bullet
        self.path = bullet.path
        self.color = color
        self.duration = duration
        self.age = 0.0
        self._key = None
        self._segs: list = []

    def _head(self) -> float:
        end = self.path[-1][1] if self.path else 0.0
        return min(self.bullet.age, end)   # bullet.age freezes once the bullet is gone

    @property
    def alive(self) -> bool:
        return self.bullet.alive or self.age - self.duration < self._head()

    def update(self, dt: float) -> None:
        self.age += dt

    def segments(self) -> list:
        key = (self.age, self.bullet.age)
        if key == self._key:
            return self._segs
        tail, head = max(0.0, self.age - self.duration), self._head()
        segs = []
        for leg, (t0, t1, p, v) in enumerate(self.path):
            ta, tb = max(t0, tail), min(t1, head)
            if tb <= ta:
                continue
            segs.append((p + v * (ta - t0), p + v * (tb - t0), leg, ta, tb))
        self._key, self._segs = key, segs
        return segs

    def hits(self, tank, owner) -> bool:
        r = tank.radius + 3
        for a, b, leg, _, _ in self.segments():
            if tank is owner and leg == 0: # first leg leaves the muzzle (same rule as beams)
                continue
            if _seg_dist(a, b, tank.pos) < r:
                return True
        return False

    def draw(self, screen: pygame.Surface) -> None:
        D = self.duration
        for a, b, _, ta, tb in self.segments():
            d = b - a
            length = d.length()
            if length == 0:
                continue
            n = max(1, int(length // 12))
            perp = Vector2(-d.y, d.x) / length
            prev = a
            for i in range(1, n + 1):
                u = i / n
                q = a + d * u
                if i < n:
                    q = q + perp * random.uniform(-2.0, 2.0)      # crackle
                k = 1 - (self.age - (ta + (tb - ta) * u)) / D      # 1 = fresh, 0 = about to vanish
                f = max(0.0, min(1.0, 2 * k))                      # full strength, then fade
                if f > 0:
                    pygame.draw.line(screen, _mix(self.color, 0.55 * f), prev, q, 3 + int(4 * f))
                    pygame.draw.line(screen, _mix((255, 255, 255), 0.9 * f), prev, q, max(1, int(2 * f)))
                prev = q