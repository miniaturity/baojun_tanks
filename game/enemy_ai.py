import math
import random
from dataclasses import dataclass, field

from game.types import *
from game.consts import *
from game.tank import Tank, Bullet, DEFAULT_GUN, make_bullet
from game.navmesh import NavMesh

INF = float("inf")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass(frozen=True)
class AIProfile:
    difficulty: float
    aim_turn_speed: float 
    aim_error: float
    lead_factor: float               
    fire_interval: tuple[float, float]  
    reaction_time: float
    dodge_horizon: float
    bounce_awareness: int 
    think_interval: float 
    bank_bounces: int                # max ricochets it will plan a bank shot around (0 = never)

    @classmethod
    def from_difficulty(cls, d: float) -> "AIProfile":
        d = max(0.0, min(1.0, d))
        return cls(
            difficulty=d,
            aim_turn_speed=_lerp(60, 540, d),
            aim_error=_lerp(14, 0.5, d),
            lead_factor=d,
            fire_interval=(_lerp(2.2, 0.5, d), _lerp(3.5, 0.9, d)),
            reaction_time=_lerp(0.45, 0.04, d),
            dodge_horizon=_lerp(0.4, 1.2, d),
            bounce_awareness=int(_lerp(0, 5, d) + 0.5),
            think_interval=_lerp(0.15, 0.05, d),
            # nothing below ~0.35 difficulty, 1 bounce around 0.4-0.6, up to 3 at max
            bank_bounces=int(_lerp(0, 3, max(0.0, (d - 0.25) / 0.75)) + 0.5),
        )


@dataclass
class AIDebug:
    sees_target: bool = False
    hold: bool = False
    heading: Vector2 = field(default_factory=lambda: Vector2(0, 0))
    intended: tuple[int, int] = (0, 0)
    aim_point: Vector2 | None = None
    aim_error: float = 0.0
    shot_safe: bool = True 

    status: str = "idle"
    reach: float = 0.0
    chosen: tuple[int, int] | None = None

    tracks: list = field(default_factory=list)
    intended_trace: list = field(default_factory=list)
    intended_hit: float | None = None
    candidates: list = field(default_factory=list)
    bank_path: list = field(default_factory=list)   # planned bank shot polyline


class EnemyAI:
    SIM_STEP = 0.05 
    DODGE_MARGIN = 4  
    CONTROLS = [(m, t) for m in (1, 0, -1) for t in (-1, 0, 1)] 

    # bank shots
    BANK_STEP = 3                # deg between coarse scan angles
    BANK_PER_FRAME = 8           # shot traces per frame while scanning (keeps frame time flat)
    BANK_TRACK_INTERVAL = 0.1    # s between re-aims while lining up a shot
    BANK_TIMEOUT = 1.5           # s before giving up on a lined-up shot
    BANK_RETRY = (0.6, 1.2)      # s cooldown after a failed scan / timeout

    def __init__(self, tank: Tank, profile: AIProfile) -> None:
        self.tank = tank
        self.profile = profile
        self.clock = 0.0

        self.target: Tank | None = None
        self.retarget_timer = 0.0
        self.goal = None          # powerup being fetched (overrides chasing the target)
        self.goal_timer = 0.0

        self.heading = Vector2(1, 0).rotate(random.uniform(0, 360))
        self.wander_timer = random.uniform(0.5, 2.0)
        self.stuck_time = 0.0

        self.fire_timer = random.uniform(*profile.fire_interval)
        self.aim_offset = 0.0

        self.think_timer = random.uniform(0, profile.think_interval)
        self.threat_since: float | None = None
        self.dodge_controls: tuple[int, int] | None = None

        # bank shot state
        self.bank_angle: float | None = None   # world angle (deg) of the planned shot
        self.bank_gen = None                   # in-progress scan (generator)
        self.bank_cd = random.uniform(0.0, 1.0)
        self.bank_hold = 0.0
        self.bank_track = 0.0
        self._bank_target: Tank | None = None

        self.record_debug = False
        self.dbg = AIDebug()

    @staticmethod
    def has_line_of_sight(a: Vector2, b: Vector2, walls) -> bool:
        return not any(w.rect.clipline(a, b) for w in walls)

    @staticmethod
    def _signed_angle(frm: Vector2, to: Vector2) -> float:
        return (frm.angle_to(to) + 180) % 360 - 180

    def update(self, dt: float, tanks: list[Tank], walls, navmesh: NavMesh, powerups=()) -> None:
        t, p, d = self.tank, self.profile, self.dbg
        if not t.alive:
            return
        self.clock += dt

        self._update_target(dt, tanks, walls)
        self._update_goal(dt, powerups)
        target = self.target
        if target is None:
            t.move_input = t.turn_input = 0
            d.status = "idle"
            return

        sees = self.has_line_of_sight(t.pos, target.pos, walls)
        hold = sees and self.goal is None and (target.pos - t.pos).length() < t.gunParams.ideal_range

        # no line of sight: look for a ricochet that reaches the target
        self._update_bank(dt, sees, walls)
        banking = self.bank_angle is not None
        d.bank_path = self._bank_points(walls) if (self.record_debug and banking) else []

        # follow flow field of target
        nav_goal = self.goal if self.goal is not None else target   # flow field key = whatever we walk to
        navmesh.request(nav_goal, nav_goal.pos)
        flow = navmesh.direction_at(nav_goal, t.pos)
        if flow.length_squared() > 0:
            self.heading = Vector2(flow)
        else:
            self._wander(dt)

        angle = self._signed_angle(t.bodyDirection, self.heading)
        turn = 0 if abs(angle) < 5 else (1 if angle > 0 else -1)
        move = 1 if abs(angle) < 45 else 0
        if hold or banking:       # stand still to line up the shot
            move = turn = 0
        intended = (move, turn)
        desired = Vector2(0, 0) if (hold or banking) else self.heading

        d.sees_target, d.hold, d.intended = sees, hold, intended
        d.heading = Vector2(self.heading)

        # dodge if unsafe
        self.think_timer -= dt
        if self.think_timer <= 0:
            self.think_timer = p.think_interval
            self._think_dodge(tanks, navmesh, intended, desired)

        t.move_input, t.turn_input = self.dodge_controls if self.dodge_controls is not None else intended

        # aim and shoot
        bank_dir = Vector2(1, 0).rotate(self.bank_angle) if banking and self.bank_angle else None
        d.aim_error = self._aim(dt, target, bank_dir)
        self.fire_timer -= dt
        gun = t.gunParams
        dist = (target.pos - t.pos).length()
        # spread guns tolerate a sloppier aim, but are useless from far away
        if (self.fire_timer <= 0 and sees and dist < gun.effective_range
                and d.aim_error < 4 + gun.bloom * 0.25):
            d.shot_safe = self._shot_is_safe(walls)
            if d.shot_safe and t.shoot():
                self.fire_timer = max(gun.cooldown, random.uniform(*p.fire_interval))
                self.aim_offset = random.uniform(-p.aim_error, p.aim_error)
        elif banking and self.fire_timer <= 0 and d.aim_error < 1.0:
            # re-check with the gun's real heading right before pulling the trigger
            max_b = min(p.bank_bounces, gun.ricochets)
            if self._bank_verify(target, walls, max_b):
                d.shot_safe = self._shot_is_safe(walls)
                if d.shot_safe and t.shoot():
                    self.fire_timer = max(gun.cooldown, random.uniform(*p.fire_interval))
                    self.aim_offset = random.uniform(-p.aim_error, p.aim_error)
                    self._bank_reset(0.5)

    #  targeting
    def _update_target(self, dt: float, tanks: list[Tank], walls) -> None:
        t = self.tank
        self.retarget_timer -= dt
        if self.target is not None and not self.target.alive:
            self.target = None
        if self.target is not None and self.retarget_timer > 0:
            return

        best, best_score = None, INF
        for other in tanks:
            if other is t or not other.alive:
                continue # everyone else is fair game
            score = (other.pos - t.pos).length()
            if other.tag == "player":
                score *= ENEMY_PLAYER_PRIORITY
            if self.has_line_of_sight(t.pos, other.pos, walls):
                score *= 0.5 # prefer targets I can see
            if other is self.target:
                score *= 0.7 # avoids dithering
            if score < best_score:
                best, best_score = other, score
        self.target = best
        self.retarget_timer = random.uniform(1.0, 2.0)

    def _update_goal(self, dt: float, powerups) -> None:
        if self.goal is not None and self.goal not in powerups:
            self.goal = None                      # someone grabbed it
        self.goal_timer -= dt
        if self.goal_timer > 0:
            return
        self.goal_timer = random.uniform(1.0, 2.0)
        t = self.tank
        armed = t.gunParams is not DEFAULT_GUN
        best, best_d = None, (120 if armed else 300)   # already armed: only grab what's close
        for pu in powerups:
            if not pu.wanted_by(t):
                continue                              # e.g. a shield when already shielded
            dd = (pu.pos - t.pos).length()
            if dd < best_d:
                best, best_d = pu, dd
        self.goal = best

    def _aim(self, dt: float, target: Tank, direction: Vector2 | None = None) -> float:
        t, p = self.tank, self.profile
        if direction is not None:
            # bank shot: the planned angle already accounts for lead, so no extra error is added
            desired = Vector2(direction)
            self.dbg.aim_point = t.pos + desired * 60
        else:
            dist = (target.pos - t.pos).length()
            aim_point = target.pos + target.velocity_vector * (dist / t.gunParams.speed) * p.lead_factor
            self.dbg.aim_point = aim_point
            desired = aim_point - t.pos
            if desired.length_squared() == 0:
                return 0.0
            desired = desired.normalize().rotate(self.aim_offset)

        err = self._signed_angle(t.gunDirection, desired)
        step = p.aim_turn_speed * dt
        t.gunDirection.rotate_ip(max(-step, min(step, err)))
        return max(0.0, abs(err) - step)

    def _shot_is_safe(self, walls) -> bool:
        """Would any projectile of the volley I'd fire now come back and hit me?"""
        t, g = self.tank, self.tank.gunParams
        reach = t.radius + g.radius + 2
        for ang in g.spread_angles():
            b = make_bullet(t.pos, t.gun_length, t.gunDirection.rotate(ang), g, walls)
            for i, (t0, t1, p, v) in enumerate(b.path):
                if not g.hitscan and i > self.profile.bounce_awareness:
                    break                      # beams: the AI knows exactly how they bounce
                speed2 = v.length_squared()
                if speed2 == 0:
                    continue
                s = max(0.0, min(t1 - t0, (t.pos - p).dot(v) / speed2))    # closest approach on this leg
                if (p + v * s).distance_to(t.pos) < reach:
                    return False
        return True

    #  bank shots
    def _bank_reset(self, cooldown: float = 0.0) -> None:
        self.bank_angle = None
        self.bank_gen = None
        self.bank_hold = 0.0
        self.bank_cd = max(self.bank_cd, cooldown)

    def _update_bank(self, dt: float, sees: bool, walls) -> None:
        """Drives the bank shot state machine: scan -> line up (stand still, track) -> fire."""
        t, p, g, target = self.tank, self.profile, self.tank.gunParams, self.target
        self.bank_cd -= dt
        max_b = min(p.bank_bounces, g.ricochets)

        if target is not self._bank_target:
            self._bank_reset()
            self._bank_target = target
        # direct shot available / nothing to shoot / unreliable gun: no banking
        if (sees or target is None or not target.alive or max_b < 1
                or g.bloom > 10 or self.goal is not None):
            self._bank_reset()
            return

        if self.bank_angle is not None:       # lining up: re-solve around the old angle as things move
            self.bank_hold += dt
            self.bank_track -= dt
            if self.bank_hold > self.BANK_TIMEOUT:
                self._bank_reset(random.uniform(*self.BANK_RETRY))
            elif self.bank_track <= 0:
                self.bank_track = self.BANK_TRACK_INTERVAL
                found = self._bank_refine(self.bank_angle, 6, target, walls, max_b)
                if found:
                    self.bank_angle = found[1]
                else:
                    self._bank_reset(random.uniform(*self.BANK_RETRY))
            return

        if self.bank_gen is None:
            if self.bank_cd > 0 or self.fire_timer > 0.6 or t.shoot_timer > 0.6:
                return                        # not ready to shoot soon anyway
            self.bank_gen = self._bank_scan(target, walls, max_b)
        try:
            next(self.bank_gen)               # a slice of the scan per frame
        except StopIteration:
            self.bank_gen = None
            if self.bank_angle is None:
                self.bank_cd = random.uniform(*self.BANK_RETRY)

    def _bank_bullet(self, angle: float, walls, max_b: int) -> Bullet:
        """Same bullet a real shot would make, but only traced for the bounces we care about."""
        t, g = self.tank, self.tank.gunParams
        b = Bullet(g.radius, t.pos, Vector2(1, 0).rotate(angle) * g.speed,
                   min(g.ricochets, max_b), g.lifetime, g.color)
        b.step(t.gun_length / g.speed, walls)
        b.predict_path(walls)
        return b

    def _bank_points(self, walls) -> list:
        if self.bank_angle is None:
            return []
        b = self._bank_bullet(self.bank_angle, walls, min(self.profile.bank_bounces, self.tank.gunParams.ricochets))
        if not b.path:
            return []
        pts = [(b.path[0][2].x, b.path[0][2].y)]
        for t0, t1, p, v in b.path:
            pts.append((p.x + v.x * (t1 - t0), p.y + v.y * (t1 - t0)))
        return pts

    def _bank_eval(self, angle: float, target: Tank, walls, max_b: int, loose_step: float = 0.0):
        """Fire at `angle`; how close does the shot come to the target on a bounced leg?
        Returns (miss / allowed miss, bounces) for the best leg, or None. Ratio <= 1 is a hit.
        loose_step widens the allowance for the coarse scan, which only samples every few degrees."""
        t, g = self.tank, self.tank.gunParams
        b = self._bank_bullet(angle, walls, max_b)
        tol = max(6.0, target.radius + g.radius - 4)     # a bit tighter than the hitbox (shots jitter +-1.5deg)
        lead = Vector2(0, 0) if g.hitscan else target.velocity_vector * self.profile.lead_factor
        off = t.gun_length / g.speed                     # path times start at the muzzle
        pos0 = target.pos + lead * off

        best, travelled = None, 0.0
        for leg, (t0, t1, p, v) in enumerate(b.path):
            speed = v.length()
            if leg >= 1:                                 # leg 0 is the direct line, which is blocked anyway
                A, B = p - v * t0 - pos0, v - lead       # separation is A + B*t, linear in t
                b2 = B.length_squared()
                ts = t0 if b2 == 0 else -A.dot(B) / b2
                if ts + off > 1.0:                       # don't extrapolate the target's heading past 1 s
                    A, B = p - v * t0 - (target.pos + lead), v
                    b2 = B.length_squared()
                    ts = t0 if b2 == 0 else -A.dot(B) / b2
                ts = max(t0, min(t1, ts))
                miss = (A + B * ts).length()
                allowed = tol
                if loose_step:
                    allowed += 0.5 * math.radians(loose_step) * (travelled + speed * (ts - t0))
                ratio = miss / allowed
                if best is None or ratio < best[0]:
                    best = (ratio, leg)
            travelled += speed * (t1 - t0)
        return best

    def _bank_refine(self, center: float, span: int, target: Tank, walls, max_b: int):
        """1-degree search around `center`; returns (score, angle, bounces) of the best strict hit."""
        best = None
        for k in range(-span, span + 1):
            res = self._bank_eval(center + k, target, walls, max_b)
            if res is not None and res[0] <= 1.0:
                score = res[0] + 0.4 * res[1]            # centred shots and fewer bounces are better
                if best is None or score < best[0]:
                    best = (score, center + k, res[1])
        return best

    def _bank_scan(self, target: Tank, walls, max_b: int):
        """Generator: sweeps all 360 degrees a few traces per frame, then refines the best hits."""
        base = random.uniform(0, self.BANK_STEP)
        cands, n = [], 0
        for i in range(0, 360 // self.BANK_STEP):
            a = base + i * self.BANK_STEP
            res = self._bank_eval(a, target, walls, max_b, self.BANK_STEP)
            if res is not None and res[0] <= 1.0:
                cands.append((res[0] + 0.2 * res[1], a))
            n += 1
            if n % self.BANK_PER_FRAME == 0:
                yield
        cands.sort()
        best = None
        for _, a in cands[:3]:
            found = self._bank_refine(a, self.BANK_STEP, target, walls, max_b)
            if found and (best is None or found[0] < best[0]):
                best = found
            yield
        if best is not None and target.alive:
            self.bank_angle = best[1]
            self.bank_hold = 0.0
            self.bank_track = self.BANK_TRACK_INTERVAL

    def _bank_verify(self, target: Tank, walls, max_b: int) -> bool:
        ang = Vector2(1, 0).angle_to(self.tank.gunDirection)
        res = self._bank_eval(ang, target, walls, max_b)
        return res is not None and res[0] <= 1.0

    #  dodging
    def _think_dodge(self, tanks, navmesh, intended, desired) -> None:
        t, p, d = self.tank, self.profile, self.dbg
        rec = self.record_debug
        step = self.SIM_STEP
        n = max(1, int(p.dodge_horizon / step))
        px, py = t.pos.x, t.pos.y

        reach = t.max_speed * p.dodge_horizon + t.radius + BULLET_RADIUS + 10
        d.reach, d.chosen = reach, None
        d.tracks, d.candidates, d.intended_trace, d.intended_hit = [], [], [], None

        tracks = []
        for tk in tanks:
            for b in tk.bullets:
                if not b.alive:
                    continue
                pts = [b.position_at(b.age + (i + 1) * step, p.bounce_awareness) for i in range(n)]
                if any(q and (q[0] - px) ** 2 + (q[1] - py) ** 2 < reach * reach for q in pts):
                    tracks.append(pts)

        # lingering tesla trails are static hazards: sample points along them as motionless "bullets"
        for tk in tanks:
            for tr in tk.trails:
                for a, b, leg, _, _ in tr.segments():
                    if tk is t and leg == 0:          # own muzzle leg is harmless to me
                        continue
                    k = max(1, int((b - a).length() // 10))
                    for i in range(k + 1):
                        q = a + (b - a) * (i / k)
                        if (q.x - px) ** 2 + (q.y - py) ** 2 < reach * reach:
                            tracks.append([(q.x, q.y)] * n)

        if not tracks:
            self.threat_since = None
            self.dodge_controls = None
            d.status = "clear"
            return
        if rec:
            d.tracks = tracks

        trace = [] if rec else None
        hit, _, _ = self._simulate(intended, tracks, navmesh, n, trace)
        d.intended_trace, d.intended_hit = trace or [], hit
        if hit is None: # what I was going to do anyway is safe
            self.threat_since = None
            self.dodge_controls = None
            d.status = "safe"
            return

        if self.threat_since is None:
            self.threat_since = self.clock
        if self.clock - self.threat_since < p.reaction_time:
            self.dodge_controls = None  # noticed it, but haven't reacted yet
            d.status = "threat"
            return

        best, best_score = None, -INF
        for ctrl in self.CONTROLS:
            trace = [] if rec else None
            hit, ex, ey = self._simulate(ctrl, tracks, navmesh, n, trace)
            if hit is None: # prefer progress toward my goal
                disp = Vector2(ex - px, ey - py)
                score = disp.dot(desired) if desired.length_squared() > 0 else -disp.length()
                score -= 5 * (abs(ctrl[0] - intended[0]) + abs(ctrl[1] - intended[1]))
            else:  # at least die as late as possible
                score = -10000 + hit * 1000
            if rec:
                d.candidates.append((ctrl, trace, hit, score))
            if score > best_score:
                best, best_score = ctrl, score
        self.dodge_controls = best
        d.chosen = best
        d.status = "dodge"

    def _simulate(self, ctrl, tracks, navmesh, n, trace=None):
        t = self.tank
        step = self.SIM_STEP
        move, turn = ctrl
        px, py = t.pos.x, t.pos.y
        dx, dy = t.bodyDirection.x, t.bodyDirection.y
        vel = t.velocity
        a = math.radians(turn * t.turn_speed * step)
        c, s = math.cos(a), math.sin(a)
        accel = move * t.engine_power / t.mass
        thr = t.radius + BULLET_RADIUS + self.DODGE_MARGIN
        thr2 = thr * thr
        free_start = navmesh.is_walkable(px, py)

        for i in range(n):
            dx, dy = dx * c - dy * s, dx * s + dy * c
            vel += (accel - t.drag * vel) * step
            vel = max(-t.max_speed * 0.6, min(t.max_speed, vel))
            nx, ny = px + dx * vel * step, py + dy * vel * step
            if free_start and not navmesh.is_walkable(nx, ny):
                vel = 0.0 # would hit a wall
            else:
                px, py = nx, ny
            if trace is not None:
                trace.append((px, py))
            for pts in tracks:
                q = pts[i]
                if q and (q[0] - px) ** 2 + (q[1] - py) ** 2 < thr2:
                    return (i + 1) * step, px, py
        return None, px, py

    def _wander(self, dt: float) -> None:
        t = self.tank
        self.wander_timer -= dt
        if t.move_input and abs(t.velocity) < 20:
            self.stuck_time += dt
        else:
            self.stuck_time = 0.0
        if self.wander_timer <= 0 or self.stuck_time > 0.4:
            self.heading = Vector2(1, 0).rotate(random.uniform(0, 360))
            self.wander_timer = random.uniform(1.0, 2.5)
            self.stuck_time = 0.0