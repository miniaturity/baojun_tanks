import asyncio
import pygame

from game import *


async def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Tanks")
    clock = pygame.time.Clock()
    manager = GameManager(screen)

    running = True
    while running:
        dt = min(clock.tick(FPS) / 1000, 1 / 30)
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
            ):
                running = False
            manager.handle_event(event)

        manager.update(dt)
        manager.draw()
        pygame.display.flip()

        await asyncio.sleep(0)

    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())