
import math
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class ProcessedHand:
    """Résultat normalisé et filtré d'une main détectée."""
    points: List[Tuple[float, float, float]]  # 21 points isotropes
    palm_width: float
    quality: float      # 0.0 (perdu) → 1.0 (parfait)
    timestamp: float
    has_world: bool     # True si world_landmarks utilisés


class LandmarkProcessor:
    """
    Normalise, corrige et filtre les landmarks MediaPipe.

    Responsabilités :
    - Conversion isotrope (world_landmarks ou correction aspect ratio).
    - Filtrage EMA pour réduire le bruit haute fréquence.
    - Calcul d'un score de qualité du tracking.
    """

    def __init__(self, smoothing_alpha: float = 0.35):
        self.alpha = smoothing_alpha
        self._prev_points: Optional[List[Tuple]] = None

    # =========================================
    # COORDONNÉES ISOTROPES
    # =========================================

    @staticmethod
    def _to_isotropic(
        landmarks,
        world_landmarks=None,
        image_aspect: float = 1.0,
    ) -> List[Tuple]:
        if world_landmarks is not None:
            return [(p.x, p.y, p.z) for p in world_landmarks]
        if abs(image_aspect - 1.0) < 1e-6:
            return [(p.x, p.y, p.z) for p in landmarks]
        return [(p.x, p.y / image_aspect, p.z) for p in landmarks]

    # =========================================
    # FILTRAGE EMA
    # =========================================

    def _smooth(self, points: List[Tuple]) -> List[Tuple]:
        if self._prev_points is None or len(self._prev_points) != len(points):
            return points
        a = self.alpha
        return [
            (
                self._prev_points[i][0] * (1 - a) + points[i][0] * a,
                self._prev_points[i][1] * (1 - a) + points[i][1] * a,
                self._prev_points[i][2] * (1 - a) + points[i][2] * a,
            )
            for i in range(len(points))
        ]

    # =========================================
    # QUALITÉ DU TRACKING
    # =========================================

    @staticmethod
    def _distance(a: Tuple, b: Tuple) -> float:
        return math.sqrt(
            (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
        )

    def _compute_quality(
        self,
        points: List[Tuple],
        palm_width: float,
    ) -> float:
        # Paume trop petite (main très éloignée ou occultée)
        if palm_width < 5e-4:
            return 0.0

        quality = 1.0

        # Pénalité si la paume est très petite
        if palm_width < 0.03:
            quality *= palm_width / 0.03

        # Pénalité si les landmarks bougent beaucoup frame à frame
        if self._prev_points is not None:
            jitter = sum(
                self._distance(points[i], self._prev_points[i])
                for i in [0, 5, 9, 13, 17]
            ) / 5.0
            if palm_width > 0:
                jitter_ratio = jitter / palm_width
                quality *= max(0.0, 1.0 - jitter_ratio * 1.5)

        return max(0.0, min(1.0, quality))

    # =========================================
    # TRAITEMENT PRINCIPAL
    # =========================================

    def process(
        self,
        landmarks,
        world_landmarks=None,
        image_aspect: float = 1.0,
    ) -> ProcessedHand:

        raw = self._to_isotropic(landmarks, world_landmarks, image_aspect)
        smoothed = self._smooth(raw)

        palm_width = self._distance(smoothed[5], smoothed[17])
        quality = self._compute_quality(smoothed, palm_width)

        self._prev_points = smoothed

        return ProcessedHand(
            points=smoothed,
            palm_width=palm_width,
            quality=quality,
            timestamp=time.monotonic(),
            has_world=(world_landmarks is not None),
        )

    def reset(self):
        self._prev_points = None

