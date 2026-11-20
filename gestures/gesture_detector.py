
"""
Façade de compatibilité.

GestureDetector délègue désormais à la chaîne :
LandmarkProcessor → GestureClassifier

L'interface publique (detect_gesture / get_finger_states) est préservée.
"""

from gestures.landmark_processor import LandmarkProcessor
from gestures.gesture_classifier import GestureClassifier
from gestures.finger_state_analyzer import FingerStateAnalyzer


class GestureDetector:
    """
    Façade publique — interface inchangée pour main.py.
    Délègue à LandmarkProcessor + GestureClassifier.
    """

    def __init__(self):
        self._processor  = LandmarkProcessor(smoothing_alpha=0.35)
        self._classifier = GestureClassifier()
        self._analyzer   = FingerStateAnalyzer()

    def detect_gesture(
        self,
        landmarks,
        world_landmarks=None,
        image_aspect: float = 1.0,
    ) -> str:
        hand = self._processor.process(landmarks, world_landmarks, image_aspect)
        score = self._classifier.best(hand)
        return score.gesture

    def classify_all(self, landmarks, world_landmarks=None, image_aspect: float = 1.0):
        """
        Retourne la liste complète des GestureScore (pour le TemporalGestureEngine).
        """
        hand = self._processor.process(landmarks, world_landmarks, image_aspect)
        return self._classifier.classify(hand), hand

    def get_finger_states(
        self,
        landmarks,
        world_landmarks=None,
        image_aspect: float = 1.0,
    ) -> dict:
        hand = self._processor.process(landmarks, world_landmarks, image_aspect)
        fa = self._analyzer.analyze(hand.points, hand.palm_width, hand.quality)
        return {k: v.value == "EXTENDED" for k, v in fa.states.items()}
