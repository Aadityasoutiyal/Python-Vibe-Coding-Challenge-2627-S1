import random

from direct.showbase.ShowBase import ShowBase
from direct.showbase.ShowBaseGlobal import globalClock
from direct.gui.OnscreenText import OnscreenText
from direct.task import Task

from panda3d.core import (
    AmbientLight,
    DirectionalLight,
    Vec3,
    Vec4,
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    TextNode,
    WindowProperties
)


# =========================================================
# SPACE WARS 3D
# =========================================================

PLAYER_SPEED = 23
BULLET_SPEED = 65
STAR_COUNT = 200


# =========================================================
# 3D MODEL CREATION
# =========================================================

def create_mesh(parent, name, vertices, faces, color):

    data = GeomVertexData(
        name,
        GeomVertexFormat.getV3(),
        Geom.UHStatic
    )

    data.setNumRows(len(vertices))

    writer = GeomVertexWriter(data, "vertex")

    for vertex in vertices:
        writer.addData3(*vertex)

    triangles = GeomTriangles(Geom.UHStatic)

    for a, b, c in faces:
        triangles.addVertices(a, b, c)
        triangles.closePrimitive()

    geom = Geom(data)
    geom.addPrimitive(triangles)

    node = GeomNode(name)
    node.addGeom(geom)

    model = parent.attachNewNode(node)

    model.setColor(*color)
    model.setTwoSided(True)
    model.setLightOff(1)

    return model


def create_box(parent, name, position, scale, color):

    x, y, z = position
    sx, sy, sz = scale

    vertices = [
        (-sx, -sy, -sz),
        (sx, -sy, -sz),
        (sx, sy, -sz),
        (-sx, sy, -sz),

        (-sx, -sy, sz),
        (sx, -sy, sz),
        (sx, sy, sz),
        (-sx, sy, sz)
    ]

    faces = [
        (0, 1, 2), (0, 2, 3),
        (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1),
        (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3),
        (3, 7, 4), (3, 4, 0)
    ]

    model = create_mesh(
        parent,
        name,
        vertices,
        faces,
        color
    )

    model.setPos(x, y, z)

    return model


# =========================================================
# PLAYER SPACESHIP
# =========================================================

def create_player(loader, parent):

    ship = parent.attachNewNode("player_ship")

    vertices = [
        (0, 3.5, 0.2),
        (-0.65, -1.8, 0),
        (0.65, -1.8, 0),
        (0, -1.0, 0.55),
        (0, -1.0, -0.3)
    ]

    faces = [
        (0, 3, 1),
        (0, 2, 3),
        (0, 1, 4),
        (0, 4, 2),
        (1, 3, 4),
        (2, 4, 3)
    ]

    create_mesh(
        ship,
        "ship_body",
        vertices,
        faces,
        (0.65, 0.78, 0.95, 1)
    )

    # Wings

    wing_vertices = [
        (0, 1.4, 0),
        (-3.2, -1.8, 0),
        (-2.4, -2.2, 0),
        (0, -0.8, 0.1),
        (3.2, -1.8, 0),
        (2.4, -2.2, 0)
    ]

    wing_faces = [
        (0, 1, 2),
        (0, 2, 3),
        (0, 3, 5),
        (0, 5, 4)
    ]

    create_mesh(
        ship,
        "ship_wings",
        wing_vertices,
        wing_faces,
        (0.08, 0.32, 0.78, 1)
    )

    # Engine pods

    for x in [-2.25, 2.25]:

        create_box(
            ship,
            "engine",
            (x, -1.1, 0),
            (0.35, 0.8, 0.3),
            (0.08, 0.22, 0.5, 1)
        )

        engine_glow = loader.loadModel("models/misc/sphere")

        engine_glow.reparentTo(ship)
        engine_glow.setScale(0.22, 0.55, 0.22)
        engine_glow.setPos(x, -2.0, 0)
        engine_glow.setColor(0.1, 0.65, 1, 1)
        engine_glow.setLightOff(1)

    # Cockpit

    cockpit = loader.loadModel("models/misc/sphere")

    cockpit.reparentTo(ship)
    cockpit.setScale(0.42, 0.8, 0.25)
    cockpit.setPos(0, 0.65, 0.32)
    cockpit.setColor(0.1, 0.85, 1, 1)
    cockpit.setLightOff(1)

    return ship


# =========================================================
# ENEMY SPACESHIP
# =========================================================

def create_enemy(loader, parent):

    enemy = parent.attachNewNode("enemy")

    vertices = [
        (0, -2.5, 0),
        (-1.8, 1.3, 0),
        (1.8, 1.3, 0),
        (0, 0.8, 0.7),
        (0, 0.8, -0.5)
    ]

    faces = [
        (0, 1, 3),
        (0, 3, 2),
        (0, 4, 1),
        (0, 2, 4),
        (1, 4, 2),
        (1, 2, 3)
    ]

    create_mesh(
        enemy,
        "enemy_body",
        vertices,
        faces,
        (0.85, 0.12, 0.2, 1)
    )

    for x in [-0.9, 0.9]:

        light = loader.loadModel("models/misc/sphere")

        light.reparentTo(enemy)
        light.setScale(0.2)
        light.setPos(x, 0.9, 0.2)
        light.setColor(1, 0.3, 0.05, 1)
        light.setLightOff(1)

    return enemy


# =========================================================
# MAIN GAME
# =========================================================

class SpaceWars(ShowBase):

    def __init__(self):

        super().__init__()

        self.disableMouse()

        self.setBackgroundColor(0.005, 0.008, 0.025, 1)

        properties = WindowProperties()
        properties.setTitle("SPACE WARS 3D")
        properties.setSize(1280, 720)

        self.win.requestProperties(properties)

        # Game state

        self.score = 0
        self.lives = 3
        self.game_over = False

        # Game objects

        self.bullets = []
        self.enemies = []
        self.asteroids = []
        self.stars = []

        # Timers

        self.spawn_timer = 0
        self.shoot_timer = 0

        # Player and HUD attributes

        self.player = None

        self.score_text = None
        self.lives_text = None
        self.title_text = None
        self.help_text = None
        self.message = None

        # Controls

        self.keys = {
            "w": False,
            "a": False,
            "s": False,
            "d": False,
            "space": False
        }

        # Initialize game

        self.setup_lighting()
        self.setup_camera()
        self.setup_background()
        self.setup_player()
        self.setup_hud()
        self.setup_controls()

        self.taskMgr.add(self.update, "game_update")

    # =====================================================
    # LIGHTING
    # =====================================================

    def setup_lighting(self):

        ambient = AmbientLight("ambient")
        ambient.setColor(Vec4(0.4, 0.45, 0.65, 1))

        self.render.setLight(
            self.render.attachNewNode(ambient)
        )

        sun = DirectionalLight("sun")
        sun.setColor(Vec4(0.9, 0.9, 1, 1))

        sun_node = self.render.attachNewNode(sun)
        sun_node.setHpr(-35, -55, 0)

        self.render.setLight(sun_node)

    # =====================================================
    # CAMERA
    # =====================================================

    def setup_camera(self):

        self.camera.setPos(0, -42, 14)
        self.camera.lookAt(0, 12, 0)

        self.camLens.setFov(58)
        self.camLens.setNearFar(0.1, 250)

    # =====================================================
    # BACKGROUND
    # =====================================================

    def setup_background(self):

        # Stars

        for i in range(STAR_COUNT):

            star = self.loader.loadModel("models/misc/sphere")

            star.reparentTo(self.render)

            star.setScale(random.uniform(0.04, 0.1))

            star.setPos(
                random.uniform(-45, 45),
                random.uniform(-25, 110),
                random.uniform(-25, 25)
            )

            star.setColor(
                random.choice([
                    (0.5, 0.7, 1, 1),
                    (0.8, 0.9, 1, 1),
                    (0.3, 0.5, 1, 1)
                ])
            )

            star.setLightOff(1)

            self.stars.append(star)

        # Distant planet

        planet = self.loader.loadModel("models/misc/sphere")

        planet.reparentTo(self.render)
        planet.setScale(8)
        planet.setPos(20, 55, 14)
        planet.setColor(0.12, 0.2, 0.5, 1)

        # Planet atmosphere

        atmosphere = self.loader.loadModel("models/misc/sphere")

        atmosphere.reparentTo(self.render)
        atmosphere.setScale(8.5)
        atmosphere.setPos(20, 55, 14)
        atmosphere.setColor(0.1, 0.4, 1, 0.25)
        atmosphere.setTransparency(True)
        atmosphere.setLightOff(1)

    # =====================================================
    # PLAYER
    # =====================================================

    def setup_player(self):

        self.player = create_player(
            self.loader,
            self.render
        )

        self.player.setPos(0, -12, 0)
        self.player.setScale(1.1)

    # =====================================================
    # HUD
    # =====================================================

    def setup_hud(self):

        self.score_text = OnscreenText(
            text="SCORE: 000000",
            pos=(-1.25, 0.9),
            scale=0.05,
            fg=(0.3, 0.85, 1, 1),
            align=TextNode.ALeft
        )

        self.lives_text = OnscreenText(
            text="LIVES: 3",
            pos=(1.25, 0.9),
            scale=0.05,
            fg=(1, 0.3, 0.4, 1),
            align=TextNode.ARight
        )

        self.title_text = OnscreenText(
            text="S P A C E   W A R S",
            pos=(0, 0.9),
            scale=0.055,
            fg=(0.3, 0.85, 1, 1)
        )

        self.help_text = OnscreenText(
            text="WASD: MOVE     SPACE: SHOOT     ESC: EXIT",
            pos=(0, -0.94),
            scale=0.04,
            fg=(0.7, 0.8, 1, 1)
        )

        self.message = OnscreenText(
            text="",
            pos=(0, 0),
            scale=0.09,
            fg=(1, 0.3, 0.3, 1),
            align=TextNode.ACenter
        )

    def update_hud(self):

        self.score_text.setText(
            f"SCORE: {self.score:06d}"
        )

        self.lives_text.setText(
            f"LIVES: {self.lives}"
        )

    # =====================================================
    # CONTROLS
    # =====================================================

    def setup_controls(self):

        for key in self.keys:

            self.accept(
                key,
                self.set_key,
                [key, True]
            )

            self.accept(
                key + "-up",
                self.set_key,
                [key, False]
            )

        self.accept("arrow_up", self.set_key, ["w", True])
        self.accept("arrow_up-up", self.set_key, ["w", False])

        self.accept("arrow_down", self.set_key, ["s", True])
        self.accept("arrow_down-up", self.set_key, ["s", False])

        self.accept("arrow_left", self.set_key, ["a", True])
        self.accept("arrow_left-up", self.set_key, ["a", False])

        self.accept("arrow_right", self.set_key, ["d", True])
        self.accept("arrow_right-up", self.set_key, ["d", False])

        self.accept("escape", self.userExit)
        self.accept("enter", self.restart_game)

    def set_key(self, key, value):

        self.keys[key] = value

    # =====================================================
    # SHOOTING
    # =====================================================

    def shoot(self):

        bullet = self.loader.loadModel("models/misc/sphere")

        bullet.reparentTo(self.render)

        bullet.setScale(0.13, 0.8, 0.13)

        bullet.setPos(
            self.player.getX(),
            self.player.getY() + 3,
            self.player.getZ()
        )

        bullet.setColor(0.1, 0.8, 1, 1)
        bullet.setLightOff(1)

        self.bullets.append(bullet)

    # =====================================================
    # SPAWN ENEMIES
    # =====================================================

    def spawn_enemy(self):

        enemy = create_enemy(
            self.loader,
            self.render
        )

        enemy.setPos(
            random.uniform(-18, 18),
            random.uniform(48, 65),
            random.uniform(-8, 8)
        )

        enemy.setScale(random.uniform(0.8, 1.3))

        self.enemies.append({
            "node": enemy,
            "speed": random.uniform(9, 14)
        })

    # =====================================================
    # ASTEROIDS
    # =====================================================

    def spawn_asteroid(self):

        asteroid = self.loader.loadModel("models/misc/sphere")

        asteroid.reparentTo(self.render)

        size = random.uniform(0.8, 2)

        asteroid.setScale(size)

        asteroid.setPos(
            random.uniform(-18, 18),
            random.uniform(55, 75),
            random.uniform(-9, 9)
        )

        asteroid.setColor(0.4, 0.43, 0.5, 1)
        asteroid.setLightOff(1)

        self.asteroids.append({
            "node": asteroid,
            "speed": random.uniform(7, 12)
        })

    # =====================================================
    # EXPLOSION EFFECT
    # =====================================================

    def explosion(self, position):

        for i in range(10):

            particle = self.loader.loadModel("models/misc/sphere")

            particle.reparentTo(self.render)

            particle.setScale(random.uniform(0.08, 0.2))
            particle.setPos(position)

            particle.setColor(
                random.uniform(0.8, 1),
                random.uniform(0.15, 0.6),
                0.05,
                1
            )

            particle.setLightOff(1)

            velocity = Vec3(
                random.uniform(-1, 1),
                random.uniform(-1, 1),
                random.uniform(-1, 1)
            ).normalized()

            self.taskMgr.add(
                self.animate_particle,
                "particle_" + str(id(particle)),
                extraArgs=[particle, velocity, 0.45],
                appendTask=True
            )

    def animate_particle(
        self,
        particle,
        velocity,
        lifetime,
        task
    ):

        dt = globalClock.getDt()

        particle.setPos(
            particle.getPos() + velocity * 20 * dt
        )

        particle.setScale(
            max(0.01, particle.getScale().x - dt * 0.4)
        )

        lifetime -= dt

        if lifetime <= 0:

            particle.removeNode()
            return Task.done

        return Task.cont

    # =====================================================
    # GAME OVER
    # =====================================================

    def end_game(self):

        self.game_over = True

        self.message.setText(
            "GAME OVER\n\n"
            f"FINAL SCORE: {self.score}\n\n"
            "PRESS ENTER TO RESTART"
        )

    # =====================================================
    # RESTART
    # =====================================================

    def restart_game(self):

        if not self.game_over:
            return

        # Remove bullets

        for bullet in self.bullets:
            bullet.removeNode()

        # Remove enemies

        for enemy in self.enemies:
            enemy["node"].removeNode()

        # Remove asteroids

        for asteroid in self.asteroids:
            asteroid["node"].removeNode()

        # Clear object lists

        self.bullets.clear()
        self.enemies.clear()
        self.asteroids.clear()

        # Reset score and lives

        self.score = 0
        self.lives = 3

        # Reset timers

        self.spawn_timer = 0
        self.shoot_timer = 0

        # Reset game state

        self.game_over = False

        # Reset player

        self.player.setPos(0, -12, 0)
        self.player.setR(0)

        # Reset controls

        for key in self.keys:
            self.keys[key] = False

        # Remove game over message

        self.message.setText("")

        # Update HUD

        self.update_hud()

    # =====================================================
    # MAIN GAME LOOP
    # =====================================================

    def update(self, task):

        dt = min(globalClock.getDt(), 0.05)

        # Move stars

        for star in self.stars:

            star.setY(star.getY() - 15 * dt)

            if star.getY() < -35:

                star.setY(random.uniform(85, 110))

                star.setX(random.uniform(-45, 45))

                star.setZ(random.uniform(-25, 25))

        if self.game_over:
            return Task.cont

        # Player movement

        dx = int(self.keys["d"]) - int(self.keys["a"])
        dz = int(self.keys["w"]) - int(self.keys["s"])

        self.player.setX(
            max(
                -20,
                min(
                    20,
                    self.player.getX() + dx * PLAYER_SPEED * dt
                )
            )
        )

        self.player.setZ(
            max(
                -10,
                min(
                    10,
                    self.player.getZ() + dz * PLAYER_SPEED * dt
                )
            )
        )

        # Ship banking

        self.player.setR(-dx * 18)

        # Shooting

        self.shoot_timer -= dt

        if self.keys["space"] and self.shoot_timer <= 0:

            self.shoot()

            self.shoot_timer = 0.16

        # Enemy spawning

        self.spawn_timer += dt

        if self.spawn_timer >= 1.2:

            self.spawn_enemy()

            if random.random() < 0.3:
                self.spawn_asteroid()

            self.spawn_timer = 0

        # Move bullets

        for bullet in self.bullets[:]:

            bullet.setY(
                bullet.getY() + BULLET_SPEED * dt
            )

            if bullet.getY() > 100:

                bullet.removeNode()
                self.bullets.remove(bullet)

        # Move enemies

        for enemy_data in self.enemies[:]:

            enemy = enemy_data["node"]

            enemy.setY(
                enemy.getY() - enemy_data["speed"] * dt
            )

            enemy.setH(enemy.getH() + 50 * dt)

            # Player collision

            if (
                enemy.getPos() - self.player.getPos()
            ).length() < 3.5:

                self.explosion(enemy.getPos())

                enemy.removeNode()
                self.enemies.remove(enemy_data)

                self.lives -= 1
                self.update_hud()

                continue

            # Bullet collision

            hit = False

            for bullet in self.bullets[:]:

                if (
                    bullet.getPos() - enemy.getPos()
                ).length() < 2.5:

                    bullet.removeNode()
                    self.bullets.remove(bullet)

                    hit = True
                    break

            if hit:

                self.explosion(enemy.getPos())

                enemy.removeNode()
                self.enemies.remove(enemy_data)

                self.score += 100
                self.update_hud()

            elif enemy.getY() < -22:

                enemy.removeNode()
                self.enemies.remove(enemy_data)

                self.lives -= 1
                self.update_hud()

        # Move asteroids

        for asteroid_data in self.asteroids[:]:

            asteroid = asteroid_data["node"]

            asteroid.setY(
                asteroid.getY() - asteroid_data["speed"] * dt
            )

            asteroid.setH(asteroid.getH() + 35 * dt)
            asteroid.setP(asteroid.getP() + 20 * dt)

            # Player collision

            if (
                asteroid.getPos() - self.player.getPos()
            ).length() < 2.5:

                self.explosion(asteroid.getPos())

                asteroid.removeNode()
                self.asteroids.remove(asteroid_data)

                self.lives -= 1
                self.update_hud()

            elif asteroid.getY() < -22:

                asteroid.removeNode()
                self.asteroids.remove(asteroid_data)

        # Game over check

        if self.lives <= 0:

            self.end_game()

        return Task.cont


# =========================================================
# START GAME
# =========================================================

if __name__ == "__main__":

    game = SpaceWars()

    game.run()