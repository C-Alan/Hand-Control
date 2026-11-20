
"""
Façade de compatibilité.

GestureStateManager délègue désormais à TemporalGestureEngine + GestureTransitionManager.
L'interface publique (update / current_state / reset) est préservée.
"""

import os
from gestures.temporal_engine import TemporalGestureEngine, EngineOutput
from gestures.transition_manager import GestureTransitionManager


class GestureStateManager:
    """
    Façade publique — interface inchangée pour main.py.
    Délègue à TemporalGestureEngine + GestureTransitionManager.
    """

    def __init__(
        self,
        confirmation_frames: int = 3,
        lost_frames: int = 8,
        log_path: str = None,
    ):
        self._engine     = TemporalGestureEngine()
        self._transitions = GestureTransitionManager(log_path=log_path)

        # Attributs publics maintenus pour compatibilité
        self.current_state = "IDLE"
        self.is_paused     = False
        self._prev_state   = "IDLE"

    def update(self, gesture: str) -> dict:
        """
        Reçoit le geste brut (str) et retourne un dict compatible avec l'ancien format.

        Note : Cette surcharge accepte soit une str (compatibilité ancienne API),
               soit une liste de GestureScore (nouvelle API depuis main.py refactorisé).
        """
        # Compatibilité : si gesture est une chaîne, construire un score minimal
        from gestures.gesture_classifier import GestureScore
        if isinstance(gesture, str):
            scores = [GestureScore(gesture, 1.0 if gesture != "UNKNOWN" else 0.0)]
            quality = 0.0 if gesture == "UNKNOWN" else 1.0
        else:
            # gesture est déjà une liste de GestureScore
            scores  = gesture
            quality = scores[0].features.get("quality", 1.0) if scores else 0.0

        output: EngineOutput = self._engine.update(scores, quality)
        output = self._transitions.update(output)

        self.current_state = output.active_state
        self.is_paused     = self._engine.is_paused
        changed            = output.active_state != self._prev_state
        self._prev_state   = output.active_state

        return {
            "state":        output.active_state,
            "paused":       self.is_paused,
            "changed":      changed,
            "raw_gesture":  output.raw_gesture,
            "stable":       not output.is_transitioning,
            "output":       output,            # EngineOutput complet pour le diagnostic
        }

    def update_full(self, scores, quality: float) -> EngineOutput:
        """
        Nouvelle API directe utilisée par main.py refactorisé.
        Retourne l'EngineOutput complet.
        """
        output = self._engine.update(scores, quality)
        output = self._transitions.update(output)

        self.current_state = output.active_state
        self.is_paused     = self._engine.is_paused

        self._prev_state = output.active_state
        return output

    def reset(self):
        self._engine.reset()
        self._transitions.reset()
        self.current_state = "IDLE"
        self.is_paused     = False
        self._prev_state   = "IDLE"
