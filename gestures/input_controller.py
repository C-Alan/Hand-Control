
import pyautogui
import math


class InputController:
    """
    Transforme les états gestuels confirmés en commandes Windows.

    CURSOR     : déplacement relatif de la souris (pavé tactile).
    PINCH      : clic gauche atomique (une seule fois par pincement).
    SCROLL     : défilement vertical.
    IDLE       : aucune commande.
    PAUSED     : commandes suspendues.
    TRANSITION : gel complet (aucune commande, curseur figé).
    """

    def __init__(
        self,
        mirror: bool           = False,
        smoothing: float       = 0.25,
        cursor_margin: float   = 0.05,
        scroll_sensitivity: float = 3.0,
        scroll_deadzone: float = 0.004,
        drag_deadzone: float   = 15.0,
        cursor_sensitivity: float = 1.6,
        pinch_freeze_threshold: float = 0.52,
        # Paramètres du pilotage directionnel vectoriel (Joystick sans déplacement de main)
        joystick_speed: float         = 28.0,   # Vitesse de croisière du curseur (pixels/frame)
        joystick_deadzone: float      = 0.12,   # Rayon de la zone morte neutre (plus réactif)
        joystick_up_boost: float      = 1.45,   # Boost vertical pour compenser l'amplitude anatomique de l'index
        neutral_y_baseline: float     = -0.74,  # Hauteur neutre médiane de repos de l'index
        scroll_up_threshold: float    = -0.78,  # Seuil vers le haut pour défiler en TOP
        scroll_down_threshold: float  = -0.56,  # Seuil vers le bas pour défiler en BOTTOM
    ):
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE    = 0

        self.screen_width, self.screen_height = pyautogui.size()

        self.mirror                  = mirror
        self.smoothing               = smoothing
        self.cursor_margin           = cursor_margin
        self.cursor_sensitivity      = cursor_sensitivity
        self.pinch_freeze_threshold  = pinch_freeze_threshold

        # Paramètres directionnels
        self.joystick_speed          = joystick_speed
        self.joystick_deadzone       = joystick_deadzone
        self.joystick_up_boost       = joystick_up_boost
        self.neutral_y_baseline      = neutral_y_baseline
        self.scroll_up_threshold     = scroll_up_threshold
        self.scroll_down_threshold   = scroll_down_threshold

        self.scroll_sensitivity = scroll_sensitivity
        self.scroll_deadzone    = scroll_deadzone
        self.drag_deadzone      = drag_deadzone

        # États internes
        self.previous_cursor     = None
        self.previous_index_pos  = None
        self.cursor_pos          = None
        self.smoothed_delta      = (0.0, 0.0)
        self.smoothed_speed      = (0.0, 0.0)
        self.directional_neutral = None
        self.previous_scroll_y   = None
        self.pinch_clicked       = False
        self.left_button_pressed = False

    # =========================================
    # UTILITAIRES
    # =========================================

    @staticmethod
    def clamp(value, minimum, maximum):
        return max(minimum, min(value, maximum))

    def smooth_cursor(self, x, y):
        if self.previous_cursor is None:
            self.previous_cursor = (x, y)
            return x, y
        px, py = self.previous_cursor
        a = self.smoothing
        sx = px * (1 - a) + x * a
        sy = py * (1 - a) + y * a
        self.previous_cursor = (sx, sy)
        return sx, sy

    def _raw_to_screen(self, landmark):
        x, y = landmark.x, landmark.y
        if self.mirror:
            x = 1.0 - x
        m = self.cursor_margin
        x = self.clamp((x - m) / (1 - 2 * m), 0.0, 1.0)
        y = self.clamp((y - m) / (1 - 2 * m), 0.0, 1.0)
        return x * (self.screen_width - 1), y * (self.screen_height - 1)

    def map_to_screen(self, landmark, frame_width, frame_height):
        sx, sy = self._raw_to_screen(landmark)
        return self.smooth_cursor(sx, sy)

    # =========================================
    # DÉTECTION D'APPROCHE DU PINCEMENT
    # =========================================

    @staticmethod
    def _is_approaching_pinch(landmarks, frame_width, frame_height, threshold):
        """Fige le curseur dès que le pouce et l'index se rapprochent."""
        aspect = (frame_width / frame_height) if (frame_width and frame_height) else 1.0
        pw = math.hypot(
            (landmarks[5].x - landmarks[17].x) * aspect,
            landmarks[5].y - landmarks[17].y,
        )
        if pw < 1e-6:
            return False
        pd = math.hypot(
            (landmarks[4].x - landmarks[8].x) * aspect,
            landmarks[4].y - landmarks[8].y,
        )
        return (pd / pw) < threshold

    # =========================================
    # DÉPLACEMENT DU CURSEUR (DIRECTIONNEL VECTORIEL)
    # =========================================

    def move_cursor(self, landmarks, frame_width, frame_height):
        """
        Déplace le curseur par pilotage directionnel (joystick vectoriel).
        L'utilisateur incline l'index dans la direction souhaitée (ex. haut-droite).
        La main reste au centre du cadre sans jamais avoir besoin de voyager vers les bords.
        """
        # Gel immédiat si approche du pincement (évite tout dérapage lors du clic)
        if self._is_approaching_pinch(
            landmarks, frame_width, frame_height, self.pinch_freeze_threshold
        ):
            self.directional_neutral = None
            self.smoothed_speed      = (0.0, 0.0)
            return

        tip = landmarks[8]  # Extrémité de l'index
        mcp = landmarks[5]  # Jointure basale de l'index
        aspect = (frame_width / frame_height) if (frame_width and frame_height) else 1.0

        pw = math.hypot(
            (landmarks[5].x - landmarks[17].x) * aspect,
            landmarks[5].y - landmarks[17].y,
        )
        if pw < 1e-6:
            return

        # Vecteur d'orientation de l'index normalisé par la largeur de la paume
        vx = ((tip.x - mcp.x) * aspect) / pw
        vy = (tip.y - mcp.y) / pw
        if self.mirror:
            vx = -vx

        # Calibrage du neutre dès l'entrée en mode CURSOR :
        # - En horizontal (x) : adaptatif autour du centre naturel (clampé à [-0.20, 0.20])
        # - En vertical (y) : ancré sur la hauteur médiane de repos (-0.74) pour laisser
        #   une pleine course symétrique vers le HAUT (jusqu'à -1.15) et vers le BAS (jusqu'à -0.35)
        if self.directional_neutral is None:
            self.directional_neutral = (
                self.clamp(vx, -0.20, 0.20),
                self.clamp(self.neutral_y_baseline, -0.78, -0.70),
            )
            self.smoothed_speed = (0.0, 0.0)
            return

        nx, ny = self.directional_neutral
        dx = vx - nx
        dy = vy - ny

        dist = math.hypot(dx, dy)

        # 1. Zone morte : dans cette zone de repos, le curseur est 100% immobile
        if dist < self.joystick_deadzone:
            self.smoothed_speed = (0.0, 0.0)
            return

        # 2. Pilotage directionnel : vitesse proportionnelle à l'inclinaison
        excess = dist - self.joystick_deadzone
        ux = dx / dist
        uy = dy / dist

        # Vitesse calculée (pixels par frame)
        base_speed = excess * self.joystick_speed * self.cursor_sensitivity
        step_x = ux * base_speed

        # Si l'index pointe vers le HAUT (dy < 0), appliquer le boost compensateur
        # de l'amplitude anatomique réduite en extension
        if dy < 0:
            step_y = uy * base_speed * self.joystick_up_boost
        else:
            step_y = uy * base_speed

        # Lissage pour un glissement fluide
        a = self.smoothing
        sx = self.smoothed_speed[0] * (1 - a) + step_x * a
        sy = self.smoothed_speed[1] * (1 - a) + step_y * a
        self.smoothed_speed = (sx, sy)

        cur_x, cur_y = pyautogui.position()
        target_x = self.clamp(cur_x + sx, 1, self.screen_width - 2)
        target_y = self.clamp(cur_y + sy, 1, self.screen_height - 2)

        pyautogui.moveTo(int(round(target_x)), int(round(target_y)), duration=0)

    # =========================================
    # CLIC / SCROLL DIRECTIONNEL
    # =========================================

    def click_left_button(self):
        if not self.pinch_clicked:
            pyautogui.click(button="left")
            self.pinch_clicked = True

    def press_left_button(self):
        if not self.left_button_pressed:
            pyautogui.mouseDown(button="left")
            self.left_button_pressed = True

    def release_left_button(self):
        if self.left_button_pressed:
            pyautogui.mouseUp(button="left")
            self.left_button_pressed = False

    def scroll(self, landmarks, frame_width=None, frame_height=None):
        """
        Défilement continu par lecture de l'orientation des deux doigts (index + majeur) :
        - Index + majeur tendus vers le haut -> défilement vers le haut (TOP).
        - Index + majeur légèrement infléchis/inclinés vers la paume -> défilement vers le bas (BOTTOM).
        - Position neutre intermédiaire -> défilement arrêté (pause de lecture).
        """
        aspect = (frame_width / frame_height) if (frame_width and frame_height) else 1.0
        pw = math.hypot(
            (landmarks[5].x - landmarks[17].x) * aspect,
            landmarks[5].y - landmarks[17].y,
        )
        if pw < 1e-6:
            return

        # Hauteur relative moyenne des pointes (8, 12) par rapport aux jointures MCP (5, 9)
        tip_y = (landmarks[8].y + landmarks[12].y) / 2.0
        mcp_y = (landmarks[5].y + landmarks[9].y) / 2.0
        rel_y = (tip_y - mcp_y) / pw

        # 1. Doigts dressés vers le haut -> Défilement vers le haut (TOP)
        if rel_y <= self.scroll_up_threshold:
            excess = abs(rel_y - self.scroll_up_threshold)
            amount = int(max(1, excess * self.scroll_sensitivity * 45))
            pyautogui.scroll(amount)

        # 2. Doigts infléchis vers la paume / vers le bas -> Défilement vers le bas (BOTTOM)
        elif rel_y >= self.scroll_down_threshold:
            excess = abs(rel_y - self.scroll_down_threshold)
            amount = int(-max(1, excess * self.scroll_sensitivity * 45))
            pyautogui.scroll(amount)

        # 3. Zone neutre : aucun scroll (permet de lire la page tranquillement)

    # =========================================
    # TRAITEMENT DES COMMANDES
    # =========================================

    def _reset_movement(self):
        self.previous_cursor     = None
        self.previous_index_pos  = None
        self.directional_neutral = None
        self.smoothed_delta      = (0.0, 0.0)
        self.smoothed_speed      = (0.0, 0.0)
        self.previous_scroll_y   = None

    def process(
        self,
        state: str,
        landmarks=None,
        frame_width=None,
        frame_height=None,
        is_transitioning: bool = False,
    ):
        """
        Exécute les commandes selon l'état gestuel confirmé.

        is_transitioning=True → gel complet (aucune commande, curseur figé).
        """

        # ── Gel pendant une transition ─────────────
        if is_transitioning:
            self.previous_index_pos  = None
            self.directional_neutral = None
            self.smoothed_delta      = (0.0, 0.0)
            self.smoothed_speed      = (0.0, 0.0)
            return

        # ── Perte de main ou pause ──────────────────
        if state in ("PAUSED", "TRANSITION") or landmarks is None:
            self.release_left_button()
            self.pinch_clicked = False
            self._reset_movement()
            return

        # ── CURSOR ──────────────────────────────────
        if state == "CURSOR":
            self.release_left_button()
            self.pinch_clicked       = False
            self.previous_scroll_y   = None
            self.move_cursor(landmarks, frame_width, frame_height)

        # ── PINCH ───────────────────────────────────
        elif state == "PINCH":
            self.previous_index_pos  = None
            self.directional_neutral = None
            self.smoothed_delta      = (0.0, 0.0)
            self.smoothed_speed      = (0.0, 0.0)
            if not self.pinch_clicked:
                self.click_left_button()
            self.previous_scroll_y = None

        # ── SCROLL ──────────────────────────────────
        elif state == "SCROLL":
            self.release_left_button()
            self.pinch_clicked       = False
            self.previous_cursor     = None
            self.previous_index_pos  = None
            self.directional_neutral = None
            self.smoothed_delta      = (0.0, 0.0)
            self.smoothed_speed      = (0.0, 0.0)
            self.scroll(landmarks, frame_width, frame_height)

        # ── IDLE ────────────────────────────────────
        elif state == "IDLE":
            self.release_left_button()
            self.pinch_clicked = False
            self._reset_movement()

    # =========================================
    # ARRÊT SÉCURISÉ
    # =========================================

    def reset(self):
        self.release_left_button()
        self.pinch_clicked  = False
        self.cursor_pos     = None
        self._reset_movement()

    def close(self):
        self.reset()
