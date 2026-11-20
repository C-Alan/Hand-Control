
import cv2

from gestures.temporal_engine import EngineOutput
from gestures.finger_state_analyzer import FingerState


STATE_COLORS = {
    "CURSOR":     (0,   220, 220),
    "SCROLL":     (0,   165, 255),
    "PINCH":      (0,   220,   0),
    "PAUSED":     (0,     0, 220),
    "IDLE":       (160, 160, 160),
    "TRANSITION": (160,  60, 240),
    "UNKNOWN":    (100, 100, 100),
}

FINGER_ABBREV = {
    "thumb":  "T",
    "index":  "I",
    "middle": "M",
    "ring":   "R",
    "pinky":  "P",
}

STATE_ABBREV = {
    FingerState.EXTENDED: "EXT",
    FingerState.BENT:     "BNT",
    FingerState.CURLED:   "CRL",
    FingerState.UNKNOWN:  "???",
}


class DiagnosticOverlay:
    """
    Affichage temps réel des informations de diagnostic sur le frame OpenCV.
    """

    FONT = cv2.FONT_HERSHEY_SIMPLEX

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    # =========================================
    # TEXTE AVEC CONTOUR (LISIBILITÉ)
    # =========================================

    def _text(self, frame, text, pos, scale=0.58, color=(255, 255, 255), thickness=1):
        cv2.putText(frame, text, pos, self.FONT, scale,
                    (0, 0, 0), thickness + 2, cv2.LINE_AA)
        cv2.putText(frame, text, pos, self.FONT, scale,
                    color, thickness, cv2.LINE_AA)

    # =========================================
    # BARRE DE PROGRESSION
    # =========================================

    def _progress_bar(self, frame, x, y, width, progress, color):
        cv2.rectangle(frame, (x, y), (x + width, y + 8), (60, 60, 60), -1)
        filled = int(width * max(0.0, min(1.0, progress)))
        if filled > 0:
            cv2.rectangle(frame, (x, y), (x + filled, y + 8), color, -1)

    # =========================================
    # CURSEUR VISUEL AU BOUT DE L'INDEX
    # =========================================

    def draw_index_cursor(self, frame, landmarks, state: str, is_transitioning: bool):
        """Dessine le viseur coloré au landmark 8 (extrémité de l'index) et le vecteur de visée."""
        h, w = frame.shape[:2]
        tip = landmarks[8]
        cx = int(tip.x * w)
        cy = int(tip.y * h)

        if is_transitioning:
            color = STATE_COLORS["TRANSITION"]
        else:
            color = STATE_COLORS.get(state, (0, 220, 220))

        # En mode pointage, tracer le segment de visée depuis la base MCP 5 vers la pointe 8
        if state == "CURSOR" and not is_transitioning:
            mcp = landmarks[5]
            mx = int(mcp.x * w)
            my = int(mcp.y * h)
            cv2.line(frame, (mx, my), (cx, cy), color, 2, cv2.LINE_AA)

        cv2.circle(frame, (cx, cy), 13, color, 2, cv2.LINE_AA)
        cv2.circle(frame, (cx, cy),  4, color, -1, cv2.LINE_AA)
        return frame

    # =========================================
    # OVERLAY PRINCIPAL
    # =========================================

    def draw(
        self,
        frame,
        output: EngineOutput,
        fps: float,
        hand_count: int,
    ):
        if not self.enabled:
            return frame

        h, w = frame.shape[:2]
        x0 = 16

        # ── Fond semi-transparent ──────────────────
        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (480, 185), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

        # ── Ligne 1 : FPS / Mains / Qualité ────────
        q = output.quality
        q_color = (0, 200, 0) if q > 0.65 else (0, 165, 255) if q > 0.35 else (0, 0, 220)
        self._text(
            frame,
            f"FPS: {fps:4.1f}   Hands: {hand_count}   Quality: {q:.2f}",
            (x0, 36), 0.60, (0, 220, 220),
        )

        # ── Ligne 2 : Geste brut + confiance ───────
        self._text(
            frame,
            f"Raw: {output.raw_gesture:<10}  Conf: {output.confidence:.2f}",
            (x0, 65), 0.58, (220, 220, 100),
        )

        # ── Ligne 3 : État actif ────────────────────
        state_display = "TRANSITION" if output.is_transitioning else output.active_state
        sc = STATE_COLORS.get(state_display, (200, 200, 200))
        self._text(frame, f"State: {state_display}", (x0, 97), 0.72, sc, thickness=2)

        # ── Ligne 4 : Candidat + barre de progression
        if output.candidate:
            tc = STATE_COLORS.get(
                "TRANSITION" if output.is_transitioning else output.active_state,
                (200, 200, 200),
            )
            self._text(
                frame,
                f"Candidate: {output.candidate}",
                (x0, 125), 0.56, (180, 100, 255),
            )
            self._progress_bar(frame, x0, 133, 200, output.candidate_progress, (160, 60, 240))
        else:
            self._text(frame, "Candidate: —", (x0, 125), 0.56, (120, 120, 120))

        # ── Ligne 5 : États des doigts ──────────────
        parts = []
        for name, abbrev in FINGER_ABBREV.items():
            state = output.finger_states.get(name)
            if isinstance(state, FingerState):
                label = STATE_ABBREV.get(state, "???")
            elif isinstance(state, bool):
                label = "EXT" if state else "CRL"
            else:
                label = "???"
            score = output.finger_scores.get(name, 0.0)
            parts.append(f"{abbrev}:{label}({score:.1f})")
        self._text(frame, "  ".join(parts), (x0, 157), 0.46, (200, 200, 200))

        # ── Ligne 6 : Distance de pincement ─────────
        pd = output.pinch_dist
        pd_color = (0, 220, 0) if pd < 0.42 else (200, 200, 200)
        self._text(frame, f"Pinch: {pd:.3f}", (x0, 180), 0.52, pd_color)

        # ── Pied de page ────────────────────────────
        self._text(
            frame,
            "Q / ESC : quitter",
            (x0, h - 18), 0.50, (140, 140, 140),
        )

        return frame

