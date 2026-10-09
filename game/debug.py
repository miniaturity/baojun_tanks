# 1 bullets 2 AI thinking 3 aim previews 4 flow fields P pause . single-step

import pygame

from game.types import *
from game.consts import *
from game.wall import Wall
from game.tank import Bullet, make_bullet

PLAYER_COLOR_DBG: tuple[int, int, int] = (90, 255, 120)
ENEMY_COLORS = [(255, 150, 60), (255, 90, 200), (90, 200, 255), (230, 230, 90), (170, 130, 255), (120, 255, 200)]

SAFE = (90, 230, 110)
UNSAFE = (240, 70, 70)
INTENDED = (80, 220, 255)
CHOSEN = (255, 235, 60)
FLOW = (80, 130, 220)


def ctrl_text(ctrl) -> str:
    move, turn = ctrl
    return f"{'FWD' if move > 0 else 'REV' if move < 0 else 'STOP'}/{'R' if turn > 0 else 'L' if turn < 0 else '-'}"


def dashed_line(surf, color, a, b, dash=5, gap=4, width=1) -> None:
    a, b = Vector2(a), Vector2(b)
    d = b - a
    length = d.length()
    if length == 0:
        return
    d /= length
    pos = 0.0
    while pos < length:
        pygame.draw.line(surf, color, a + d * pos, a + d * min(pos + dash, length), width)
        pos += dash + gap


def arrow(surf, color, a, b, width=2) -> None:
    a, b = Vector2(a), Vector2(b)
    pygame.draw.line(surf, color, a, b, width)
    d = b - a
    if d.length_squared() > 0:
        d = d.normalize()
        for side in (150, -150):
            pygame.draw.line(surf, color, b, b + d.rotate(side) * 6, width)


def cross(surf, color, p, size=4, width=2) -> None:
    pygame.draw.line(surf, color, (p[0] - size, p[1] - size), (p[0] + size, p[1] + size), width)
    pygame.draw.line(surf, color, (p[0] - size, p[1] + size), (p[0] + size, p[1] - size), width)


def remaining_path(b: Bullet) -> list[tuple[float, float]]:
    cur = b.position_at(b.age)
    if cur is None:
        return []
    pts = [cur]
    for t0, t1, p, v in b.path:
        if t1 > b.age:
            pts.append((p.x + v.x * (t1 - t0), p.y + v.y * (t1 - t0)))
    return pts


class DebugOverlay:

    def __init__(self) -> None:
        self.enabled = False
        self.paused = False
        self._step = False
        self.layers = {"bullets": True, "thinking": True, "aim": True, "flow": False}
        self._keys = {pygame.K_1: "bullets", pygame.K_2: "thinking", pygame.K_3: "aim", pygame.K_4: "flow"}
        self.font = pygame.font.SysFont(None, 16)
        self.surface = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)

    # input
    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type != pygame.KEYDOWN:
            return False
        if event.key == pygame.K_F3:
            self.enabled = not self.enabled
            if not self.enabled:
                self.paused = False
            return True
        if self.enabled:
            if event.key in self._keys:
                name = self._keys[event.key]
                self.layers[name] = not self.layers[name]
                return True
            if event.key == pygame.K_p:
                self.paused = not self.paused
                return True
            if event.key == pygame.K_PERIOD and self.paused:
                self._step = True
                return True
        return False

    def consume_step(self) -> bool:
        step, self._step = self._step, False
        return step

    # drawing
    def draw(self, screen: pygame.Surface, gm) -> None:
        if not self.enabled:
            return
        s = self.surface
        s.fill((0, 0, 0, 0))

        colors = {gm.player: PLAYER_COLOR_DBG}
        for i, e in enumerate(gm.enemies):
            colors[e] = ENEMY_COLORS[i % len(ENEMY_COLORS)]

        if self.layers["flow"]:
            self._draw_flow(s, gm, colors)
        if self.layers["aim"]:
            self._draw_aim_previews(s, gm, colors)
        if self.layers["bullets"]:
            self._draw_bullets(s, gm, colors)
        if self.layers["thinking"]:
            for i, ai in enumerate(gm.ais):
                self._draw_ai(s, gm, ai, i, colors[ai.tank])
        self._draw_legend(s)

        screen.blit(s, (0, 0))

    def _text(self, s, text, pos, color=(235, 235, 235)) -> pygame.Rect:
        img = self.font.render(text, True, color)
        rect = img.get_rect(topleft=pos)
        s.fill((0, 0, 0, 150), rect.inflate(4, 0))
        s.blit(img, rect)
        return rect

    # flow fields
    def _draw_flow(self, s, gm, colors) -> None:
        seen = set()
        for ai in gm.ais:
            tgt = ai.goal if ai.goal is not None else ai.target
            if tgt is None or tgt in seen:
                continue
            first = not seen
            seen.add(tgt)
            gm.navmesh.draw_debug(s, tgt, stride=2 if len(gm.ais) == 1 else 3,
                                  color=(*colors.get(tgt, (255, 220, 80)), 130), draw_blocked=first)

    # where would a shot fired right now go?
    def _draw_aim_previews(self, s, gm, colors) -> None:
        for tk in gm.all_tanks:
            if not tk.alive:
                continue
            g = tk.gunParams
            for ang in g.spread_angles():
                bl = make_bullet(tk.pos, tk.gun_length, tk.gunDirection.rotate(ang), g, Wall.walls)
                pts = [(bl.pos.x, bl.pos.y)]
                for t0, t1, p, v in bl.path[:4 if g.hitscan else 3]:   # muzzle + first bounces
                    pts.append((p.x + v.x * (t1 - t0), p.y + v.y * (t1 - t0)))
                for a, b in zip(pts, pts[1:]):
                    dashed_line(s, (*colors[tk], 110), a, b, dash=3, gap=5)

    # every bullet's predicted route
    def _draw_bullets(self, s, gm, colors) -> None:
        for owner in gm.all_tanks:
            col = colors[owner]
            for b in owner.bullets:
                if not b.alive or not b.path:
                    continue
                pts = remaining_path(b)
                if len(pts) < 2:
                    continue
                pygame.draw.lines(s, (*col, 210), False, pts, 2)
                for q in pts[1:-1]:                         # bounce points
                    pygame.draw.circle(s, (*col, 255), q, 4, 1)
                if abs(b.path[-1][1] - (b.age + b.lifetime)) < 1e-6:
                    pygame.draw.rect(s, (*col, 255), (pts[-1][0] - 3, pts[-1][1] - 3, 6, 6), 1)   # times out
                else:
                    cross(s, (*col, 255), pts[-1])                                                 # last ricochet
                for k in range(1, 9):                       # a dot every 0.25 s
                    q = b.position_at(b.age + k * 0.25)
                    if q is None:
                        break
                    pygame.draw.circle(s, (255, 255, 255, 170), q, 1)
                self._text(s, f"r{b.ricochet}", (b.pos.x + 6, b.pos.y - 14), col)

    # what the ai is thinking
    def _draw_ai(self, s, gm, ai, idx, col) -> None:
        t, d = ai.tank, ai.dbg
        pos = (t.pos.x, t.pos.y)

        if not t.alive:
            killer = t.killed_by
            who = "?" if killer is None else ("PLAYER" if killer is gm.player else f"E{gm.enemies.index(killer) + 1}")
            cross(s, (*col, 255), pos, 7, 3)
            self._text(s, f"E{idx + 1} killed by {who}", (pos[0] + 10, pos[1] - 6), col)
            return

        # what it can "see": bullets outside this circle are ignored
        if d.reach:
            pygame.draw.circle(s, (*col, 45), pos, int(d.reach), 1)

        # target line: solid green with line of sight, dashed grey without
        tgt = ai.target
        if tgt is not None and tgt.alive:
            if d.sees_target:
                pygame.draw.line(s, (120, 255, 120, 150), pos, tgt.pos, 1)
            else:
                dashed_line(s, (170, 170, 170, 110), pos, tgt.pos)

        # where it is aiming (with lead + error)
        if d.aim_point is not None and tgt is not None:
            pygame.draw.circle(s, (255, 80, 80, 220), d.aim_point, 5, 1)
            cross(s, (255, 80, 80, 220), d.aim_point, 3, 1)

        # planned bank shot
        if d.bank_path:
            pygame.draw.lines(s, (*CHOSEN, 220), False, d.bank_path, 2)
            for q in d.bank_path[1:-1]:
                pygame.draw.circle(s, (*CHOSEN, 255), q, 4, 1)

        # flow-field heading
        if d.heading.length_squared() > 0:
            arrow(s, (90, 160, 255, 230), pos, t.pos + d.heading * 34)

        # predicted bullet positions considered at the last think
        for track in d.tracks:
            for q in track:
                if q:
                    pygame.draw.circle(s, (255, 170, 40, 200), q, 2)

        # candidate maneuvers: green = survives, red = hit (X marks where)
        for ctrl, trace, hit, score in d.candidates:
            if not trace:
                continue
            c = (*(SAFE if hit is None else UNSAFE), 110)
            pygame.draw.lines(s, c, False, [pos] + trace, 1)
            if hit is not None:
                cross(s, (*UNSAFE, 220), trace[-1], 3, 1)
        if d.intended_trace:
            pygame.draw.lines(s, (*INTENDED, 230), False, [pos] + d.intended_trace, 2)
            if d.intended_hit is not None:
                cross(s, (*UNSAFE, 255), d.intended_trace[-1], 5, 2)
        if d.chosen is not None:
            for ctrl, trace, hit, score in d.candidates:
                if ctrl == d.chosen and trace:
                    pygame.draw.lines(s, (*CHOSEN, 255), False, [pos] + trace, 3)

        # label
        if tgt is None:
            tname = "none"
        else:
            tname = "PLAYER" if tgt is gm.player else f"E{gm.enemies.index(tgt) + 1}"
        line1 = f"E{idx + 1}->{tname} {'LOS' if d.sees_target else 'no LOS'}{' HOLD' if d.hold else ''}{' +PU' if ai.goal is not None else ''}{' BANK' if d.bank_path else ''}"
        if ai.dodge_controls is not None:
            line2 = f"DODGE {ctrl_text(ai.dodge_controls)}"
        elif ai.threat_since is not None and d.status == "threat":
            left = max(0.0, ai.profile.reaction_time - (ai.clock - ai.threat_since))
            line2 = f"THREAT, reacting in {left:.2f}s"
        else:
            line2 = d.status
        if not d.shot_safe and ai.fire_timer <= 0:
            line3 = "HOLD FIRE: shot would hit me"
        else:
            line3 = f"fire {max(0.0, ai.fire_timer):.1f}s  aim err {d.aim_error:.0f}deg"

        x = min(max(2, pos[0] + 14), SCREEN_WIDTH - 130)
        y = min(max(18, pos[1] - 44), SCREEN_HEIGHT - 70)
        self._text(s, line1, (x, y), col)
        self._text(s, line2, (x, y + 12), CHOSEN if ai.dodge_controls is not None else (235, 235, 235))
        self._text(s, line3, (x, y + 24), (200, 200, 200))

    def _draw_legend(self, s) -> None:
        on = lambda k: "on" if self.layers[k] else "off"
        self._text(s, f"F3 debug | 1 bullets:{on('bullets')}  2 thinking:{on('thinking')}  "
                      f"3 aim:{on('aim')}  4 flow:{on('flow')} | P pause  . step",
                   (4, SCREEN_HEIGHT - 14))
        self._text(s, "cyan plan | green/red safe/hit try | yellow chosen | orange seen bullets",
                   (4, SCREEN_HEIGHT - 27), (190, 190, 190))
        if self.paused:
            self._text(s, "PAUSED", (SCREEN_WIDTH // 2 - 22, 20), (255, 255, 120))