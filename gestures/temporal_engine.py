
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from gestures.gesture_classifier import GestureScore


# =====================================================
# RÈGLES DE TRANSITION
# =====================================================

@dataclass
class TransitionRule:
    min_frames: int      # Frames consécutives pour confirmer
    cooldown: int        # Frames bloquées post-confirmation
    freeze_input: bool   # Geler l'InputController pendant la transition


# Clé : (from_state, to_state) ou ("ANY", to_state) ou ("DEFAULT", "DEFAULT")
TRANSITION_RULES: Dict[tuple, TransitionRule] = {
    ("CURSOR",  "PINCH"):  TransitionRule(min_frames=2, cooldown=3, freeze_input=True),
    ("CURSOR",  "SCROLL"): TransitionRule(min_frames=4, cooldown=3, freeze_input=True),
    ("SCROLL",  "CURSOR"): TransitionRule(min_frames=3, cooldown=3, freeze_input=True),
    ("SCROLL",  "PINCH"):  TransitionRule(min_frames=5, cooldown=4, freeze_input=True),
    ("PINCH",   "CURSOR"): TransitionRule(min_frames=2, cooldown=2, freeze_input=True),
    ("PINCH",   "SCROLL"): TransitionRule(min_frames=4, cooldown=3, freeze_input=True),
    ("IDLE",    "CURSOR"): TransitionRule(min_frames=3, cooldown=2, freeze_input=False),
    ("IDLE",    "SCROLL"): TransitionRule(min_frames=4, cooldown=2, freeze_input=False),
    ("IDLE",    "PINCH"):  TransitionRule(min_frames=4, cooldown=3, freeze_input=False),
    ("ANY",     "PAUSED"): TransitionRule(min_frames=5, cooldown=8, freeze_input=True),
    ("PAUSED",  "IDLE"):   TransitionRule(min_frames=5, cooldown=8, freeze_input=True),
    ("DEFAULT", "DEFAULT"):TransitionRule(min_frames=3, cooldown=3, freeze_input=True),
}

# Hystérésis : (seuil_entrée, seuil_sortie) par geste
HYSTERESIS: Dict[str, tuple] = {
    "INDEX":     (0.62, 0.48),
    "SCROLL":    (0.68, 0.52),
    "PINCH":     (0.50, 0.38),
    "FIST":      (0.68, 0.52),
    "OPEN_PALM": (0.68, 0.52),
    "UNKNOWN":   (1.00, 0.00),
}

# Mapping geste → état système
GESTURE_TO_STATE = {
    "INDEX":     "CURSOR",
    "SCROLL":    "SCROLL",
    "PINCH":     "PINCH",
    "FIST":      "PAUSED",
    "OPEN_PALM": "IDLE",
}

# Mapping état → geste (pour l'hystérésis de sortie)
STATE_TO_GESTURE = {v: k for k, v in GESTURE_TO_STATE.items()}


# =====================================================
# SORTIE DU MOTEUR
# =====================================================

@dataclass
class EngineOutput:
    active_state: str           # État confirmé actif
    candidate: Optional[str]    # Geste candidat en confirmation
    is_transitioning: bool      # True → InputController se gèle
    confidence: float           # Confiance du geste brut courant
    candidate_progress: float   # 0.0 → 1.0 (frames_done / min_frames)
    quality: float              # Qualité du tracking
    finger_states: dict         # États par doigt (pour diagnostic)
    finger_scores: dict         # Scores continus par doigt
    pinch_dist: float           # Distance pouce-index normalisée
    raw_gesture: str            # Geste brut avant filtrage temporel


# =====================================================
# MOTEUR TEMPOREL
# =====================================================

class TemporalGestureEngine:
    """
    Moteur de reconnaissance gestuelle temporelle.

    Mécanismes combinés :
    1. Fenêtre EMA glissante par geste (α=0.40).
    2. Hystérésis par geste (seuil_entrée > seuil_sortie).
    3. Règles de transition spécifiques par paire d'états.
    4. État de transition explicite (InputController gelé).
    5. Cooldown post-confirmation (empêche le retour immédiat).
    6. Maintien sur perte de tracking ≤ LOST_FRAMES.
    """

    EMA_ALPHA        = 0.40
    LOST_FRAMES      = 10
    QUALITY_THRESHOLD = 0.25

    def __init__(self):
        self._ema: Dict[str, float] = defaultdict(float)

        self.active_state  = "IDLE"
        self.is_paused     = False

        self._candidate: Optional[str] = None
        self._candidate_frames: int    = 0
        self._cooldown: int            = 0
        self._is_transitioning: bool   = False

        self._lost_frames: int = 0
        self._last_output: Optional[EngineOutput] = None

    # =========================================
    # RÈGLES
    # =========================================

    @staticmethod
    def _get_rule(from_state: str, to_state: str) -> TransitionRule:
        for key in [
            (from_state, to_state),
            ("ANY",      to_state),
        ]:
            if key in TRANSITION_RULES:
                return TRANSITION_RULES[key]
        return TRANSITION_RULES[("DEFAULT", "DEFAULT")]

    # =========================================
    # MISE À JOUR EMA
    # =========================================

    def _update_ema(self, scores: List[GestureScore]):
        seen = set()

        # Si un PINCH net arrive (doigts qui se touchent), faire chuter immédiatement l'inertie de l'INDEX
        # pour que le clic soit instantané sans attendre la décroissance lente de l'index
        is_clear_pinch = any(
            gs.gesture == "PINCH" and gs.confidence >= 0.60 for gs in scores
        )
        if is_clear_pinch and "INDEX" in self._ema:
            self._ema["INDEX"] = min(self._ema["INDEX"], 0.20)

        # Mise à jour avec les scores de la frame courante
        for gs in scores:
            seen.add(gs.gesture)
            prev = self._ema.get(gs.gesture, 0.0)
            # Alpha plus dynamique pour le PINCH (action brève et réactive)
            alpha = 0.60 if gs.gesture == "PINCH" and gs.confidence >= 0.65 else self.EMA_ALPHA
            self._ema[gs.gesture] = (
                prev * (1 - alpha) + gs.confidence * alpha
            )

        # Décroissance naturelle des gestes absents de la frame courante
        for key in self._ema:
            if key not in seen:
                self._ema[key] *= (1 - self.EMA_ALPHA)

    # =========================================
    # MISE À JOUR PRINCIPALE
    # =========================================

    def update(
        self,
        scores: List[GestureScore],
        quality: float,
    ) -> EngineOutput:

        raw_gesture    = scores[0].gesture    if scores else "UNKNOWN"
        raw_confidence = scores[0].confidence if scores else 0.0

        finger_states = scores[0].features.get("finger_states", {})
        finger_scores = scores[0].features.get("finger_scores", {})
        pinch_dist    = scores[0].features.get("pinch_dist", 0.0)

        # ─────────────────────────────────────
        # 1. Perte de tracking
        # ─────────────────────────────────────
        is_lost = quality < self.QUALITY_THRESHOLD or raw_gesture == "UNKNOWN"

        if is_lost:
            self._lost_frames += 1

            # Maintien court
            if self._lost_frames <= self.LOST_FRAMES:
                return self._make(raw_gesture, raw_confidence, quality,
                                  finger_states, finger_scores, pinch_dist)
            # Perte prolongée → IDLE
            self._reset_to_idle()
            return self._make("UNKNOWN", 0.0, quality,
                              finger_states, finger_scores, pinch_dist)

        self._lost_frames = 0

        # ─────────────────────────────────────
        # 2. Mise à jour EMA
        # ─────────────────────────────────────
        self._update_ema(scores)

        # ─────────────────────────────────────
        # 3. Cooldown post-transition
        # ─────────────────────────────────────
        if self._cooldown > 0:
            self._cooldown -= 1
            self._candidate = None
            self._candidate_frames = 0
            self._is_transitioning = False
            return self._make(raw_gesture, raw_confidence, quality,
                              finger_states, finger_scores, pinch_dist)

        # ─────────────────────────────────────
        # 4. Meilleur geste selon EMA
        # ─────────────────────────────────────
        best_gesture = max(self._ema, key=self._ema.get, default="UNKNOWN")
        best_ema     = self._ema.get(best_gesture, 0.0)
        entry_thr, exit_thr = HYSTERESIS.get(best_gesture, (0.65, 0.50))

        # ─────────────────────────────────────
        # 5. Gestion de la pause
        # ─────────────────────────────────────
        if best_gesture == "FIST" and best_ema >= entry_thr and not self.is_paused:
            return self._try_transition(
                "FIST", best_ema, quality, finger_states, finger_scores, pinch_dist, raw_gesture
            )

        if best_gesture == "OPEN_PALM" and best_ema >= entry_thr and self.is_paused:
            return self._try_transition(
                "OPEN_PALM", best_ema, quality, finger_states, finger_scores, pinch_dist, raw_gesture
            )

        if self.is_paused:
            return self._make(raw_gesture, raw_confidence, quality,
                              finger_states, finger_scores, pinch_dist)

        # ─────────────────────────────────────
        # 6. Sortie de l'état actif (hystérésis)
        # ─────────────────────────────────────
        if self.active_state not in ("IDLE", "PAUSED"):
            current_gesture = STATE_TO_GESTURE.get(self.active_state)
            if current_gesture:
                current_ema = self._ema.get(current_gesture, 0.0)
                _, cur_exit = HYSTERESIS.get(current_gesture, (0.65, 0.50))

                # Exception réactivité clic : si on est en CURSOR et qu'un PINCH net est en cours,
                # ne pas laisser l'hystérésis de l'index bloquer ou annuler le clic
                is_pinch_trigger = (
                    self.active_state == "CURSOR"
                    and (best_gesture == "PINCH" or (scores and scores[0].gesture == "PINCH" and pinch_dist < 0.48))
                )

                if current_ema >= cur_exit and not is_pinch_trigger:
                    # L'état actif reste valide : annuler tout candidat en cours
                    self._candidate = None
                    self._candidate_frames = 0
                    self._is_transitioning = False
                    return self._make(raw_gesture, raw_confidence, quality,
                                      finger_states, finger_scores, pinch_dist)

        # ─────────────────────────────────────
        # 7. Entrée dans un nouvel état
        # ─────────────────────────────────────
        target_state = GESTURE_TO_STATE.get(best_gesture, "IDLE")
        if target_state != self.active_state and best_ema >= entry_thr:
            return self._try_transition(
                best_gesture, best_ema, quality, finger_states, finger_scores, pinch_dist, raw_gesture
            )

        # État stable inchangé
        self._candidate = None
        self._candidate_frames = 0
        self._is_transitioning = False

        return self._make(raw_gesture, raw_confidence, quality,
                          finger_states, finger_scores, pinch_dist)

    # =========================================
    # CONFIRMATION DE TRANSITION
    # =========================================

    def _try_transition(
        self,
        gesture: str,
        ema_score: float,
        quality: float,
        finger_states: dict,
        finger_scores: dict,
        pinch_dist: float,
        raw_gesture: str,
    ) -> EngineOutput:

        target_state = GESTURE_TO_STATE.get(gesture, "IDLE")
        rule = self._get_rule(self.active_state, target_state)

        if gesture == self._candidate:
            self._candidate_frames += 1
        else:
            self._candidate = gesture
            self._candidate_frames = 1
            self._is_transitioning = rule.freeze_input

        progress = min(1.0, self._candidate_frames / max(1, rule.min_frames))

        if self._candidate_frames >= rule.min_frames:
            self._confirm(gesture, target_state, rule)

        return self._make(
            raw_gesture, ema_score, quality,
            finger_states, finger_scores, pinch_dist,
            progress=progress,
        )

    def _confirm(self, gesture: str, target_state: str, rule: TransitionRule):
        if gesture == "FIST":
            self.is_paused = True
        elif gesture == "OPEN_PALM":
            self.is_paused = False

        self.active_state      = target_state
        self._candidate        = None
        self._candidate_frames = 0
        self._cooldown         = rule.cooldown
        self._is_transitioning = False

    # =========================================
    # UTILITAIRES
    # =========================================

    def _reset_to_idle(self):
        self.active_state      = "IDLE"
        self.is_paused         = False
        self._candidate        = None
        self._candidate_frames = 0
        self._cooldown         = 0
        self._is_transitioning = False
        self._ema.clear()

    def _make(
        self,
        raw_gesture: str,
        confidence: float,
        quality: float,
        finger_states: dict,
        finger_scores: dict,
        pinch_dist: float,
        progress: float = 0.0,
    ) -> EngineOutput:
        out = EngineOutput(
            active_state=self.active_state,
            candidate=self._candidate,
            is_transitioning=self._is_transitioning,
            confidence=confidence,
            candidate_progress=progress,
            quality=quality,
            finger_states=finger_states,
            finger_scores=finger_scores,
            pinch_dist=pinch_dist,
            raw_gesture=raw_gesture,
        )
        self._last_output = out
        return out

    def reset(self):
        self._reset_to_idle()
        self._lost_frames = 0
        self._last_output = None

