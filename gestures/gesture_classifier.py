
import math
from dataclasses import dataclass, field
from typing import Dict, List

from gestures.landmark_processor import ProcessedHand
from gestures.finger_state_analyzer import FingerStateAnalyzer, FingerState, FingerAnalysis


@dataclass
class GestureScore:
    gesture: str
    confidence: float               # 0.0 → 1.0
    features: Dict = field(default_factory=dict)


GESTURES = ("INDEX", "SCROLL", "PINCH", "FIST", "OPEN_PALM")


class GestureClassifier:
    """
    Classifie les gestes avec un score de confiance continu.

    Chaque classifieur est indépendant : aucune priorité implicite.
    L'ambiguïté est détectée lorsque deux scores sont proches.
    """

    # Seuil minimal de confiance pour qu'un geste soit retenu
    MIN_CONFIDENCE = 0.52

    # Différence maximale entre les deux meilleurs scores pour déclarer une ambiguïté
    AMBIGUITY_DELTA = 0.14

    def __init__(
        self,
        pinch_enter_threshold: float = 0.42,
        pinch_exit_threshold: float  = 0.52,
        min_confidence: float        = 0.52,
    ):
        self.pinch_enter  = pinch_enter_threshold
        self.pinch_exit   = pinch_exit_threshold
        self.min_confidence = min_confidence
        self._analyzer = FingerStateAnalyzer()

    # =========================================
    # UTILITAIRES
    # =========================================

    @staticmethod
    def _dist(a, b) -> float:
        return math.sqrt(
            (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
        )

    # =========================================
    # CLASSIFICATEURS INDIVIDUELS
    # =========================================

    def _score_index(self, fa: FingerAnalysis, pinch_dist: float) -> float:
        """
        INDEX : index tendu, autres ≤ BENT, pas d'approche de pincement.
        """
        # Interdit si proche du pincement
        if pinch_dist < self.pinch_exit:
            return 0.0

        if fa.scores["index"] < 0.55:
            return 0.0

        base = fa.scores["index"]

        # Pénalités si d'autres doigts tendent à s'étendre
        # Seuil de tolérance du majeur légèrement augmenté (0.42 au lieu de 0.38) pour tolérer
        # le couplage tendineux naturel qui relève légèrement le majeur quand l'index pointe haut
        penalties = max(0.0, fa.scores["middle"] - 0.42) * 1.8
        penalties += max(0.0, fa.scores["ring"]   - 0.38) * 1.4
        penalties += max(0.0, fa.scores["pinky"]  - 0.42) * 1.0

        return max(0.0, min(1.0, base - penalties))

    def _score_scroll(self, fa: FingerAnalysis, pinch_dist: float) -> float:
        """
        SCROLL : index + majeur tendus, annulaire + auriculaire ≤ BENT.
        """
        if pinch_dist < self.pinch_exit:
            return 0.0

        if fa.scores["index"] < 0.55 or fa.scores["middle"] < 0.50:
            return 0.0

        base = (fa.scores["index"] + fa.scores["middle"]) / 2.0

        penalties = max(0.0, fa.scores["ring"]  - 0.38) * 1.6
        penalties += max(0.0, fa.scores["pinky"] - 0.42) * 1.0

        return max(0.0, min(1.0, base - penalties))

    def _score_pinch(
        self,
        fa: FingerAnalysis,
        pinch_dist: float,
        points: list,
        palm_width: float,
    ) -> float:
        """
        PINCH : pouce et index rapprochés pour cliquer.
        Distingue formellement le pincement d'un poing fermé (FIST) :
        - Dans un FIST, l'index et le majeur sont tous deux repliés côte à côte dans la paume (extrémités 8 et 12 proches).
        - Dans un FIST, l'index est rétracté contre sa base MCP 5 (tip_to_mcp5 petit).
        - Dans un vrai PINCH, l'index est déployé dans l'espace vers le pouce.
        """
        if pinch_dist >= self.pinch_exit:
            return 0.0

        # 1. Rejet si l'index est replié dans la paume aux côtés du majeur (signature du FIST)
        dist_tip8_tip12 = self._dist(points[8], points[12])
        if dist_tip8_tip12 < palm_width * 0.30 and fa.scores.get("middle", 0.0) < 0.35:
            return 0.0

        # 2. Rejet si l'index est replié vers sa base MCP 5
        tip_to_mcp5 = self._dist(points[8], points[5])
        if tip_to_mcp5 < palm_width * 0.38:
            return 0.0

        # 3. L'index ne doit pas être complètement recroquevillé
        if fa.scores.get("index", 0.0) < 0.25:
            return 0.0

        # Score progressif et réactif :
        # - pinch_dist <= 0.30 : contact franc (score 1.0)
        # - de 0.30 à self.pinch_exit (0.52) : décroissance linéaire de 1.0 à 0.40
        if pinch_dist <= 0.30:
            score = 1.0
        else:
            t = (pinch_dist - 0.30) / max(1e-6, self.pinch_exit - 0.30)
            score = 1.0 - t * 0.60
            score = max(0.0, min(1.0, score))

        return score

    def _score_fist(self, fa: FingerAnalysis, pinch_dist: float) -> float:
        """
        FIST : tous les doigts repliés ou demi-pliés.
        """
        # Si l'index est tendu ou demi-tendu pour un pincement, ce n'est pas un poing
        if fa.scores.get("index", 0.0) > 0.35:
            return 0.0

        curl_scores = [
            1.0 - fa.scores["index"],
            1.0 - fa.scores["middle"],
            1.0 - fa.scores["ring"],
            1.0 - fa.scores["pinky"],
        ]
        avg = sum(curl_scores) / len(curl_scores)

        # Pénalité si le pouce est fortement tendu (OPEN_PALM plutôt que FIST)
        if fa.scores["thumb"] > 0.70:
            avg *= 0.6

        return max(0.0, min(1.0, avg))

    def _score_open_palm(self, fa: FingerAnalysis, pinch_dist: float) -> float:
        """
        OPEN_PALM : tous les doigts tendus (pouce inclus), sans pincement.
        """
        if pinch_dist < self.pinch_exit:
            return 0.0

        all_scores = [
            fa.scores["thumb"],
            fa.scores["index"],
            fa.scores["middle"],
            fa.scores["ring"],
            fa.scores["pinky"],
        ]
        return max(0.0, min(1.0, sum(all_scores) / len(all_scores)))

    # =========================================
    # CLASSIFICATION COMPLÈTE
    # =========================================

    def classify(self, hand: ProcessedHand) -> List[GestureScore]:
        """
        Retourne la liste de tous les scores, triés par confiance décroissante.
        Retourne [GestureScore("UNKNOWN", 1.0)] si la qualité est trop basse.
        """
        if hand.quality < 0.15 or hand.palm_width < 1e-3:
            return [GestureScore("UNKNOWN", 1.0, {"reason": "low_quality"})]

        pts = hand.points
        pw  = hand.palm_width
        fa  = self._analyzer.analyze(pts, pw, quality=hand.quality)

        pinch_dist = self._dist(pts[4], pts[8]) / (pw + 1e-9)

        raw_scores = [
            GestureScore("PINCH",     self._score_pinch(fa, pinch_dist, pts, pw),
                         {"pinch_dist": round(pinch_dist, 3)}),
            GestureScore("FIST",      self._score_fist(fa, pinch_dist),
                         {"curl": {k: fa.states[k].value for k in ("index","middle","ring","pinky")}}),
            GestureScore("OPEN_PALM", self._score_open_palm(fa, pinch_dist),
                         {"scores": {k: round(v, 2) for k, v in fa.scores.items()}}),
            GestureScore("INDEX",     self._score_index(fa, pinch_dist),
                         {"index_score": round(fa.scores["index"], 2)}),
            GestureScore("SCROLL",    self._score_scroll(fa, pinch_dist),
                         {"mid_score": round(fa.scores["middle"], 2)}),
        ]

        raw_scores.sort(key=lambda s: s.confidence, reverse=True)

        # Marquer l'ambiguïté si les deux meilleurs scores sont proches
        if (
            len(raw_scores) >= 2
            and raw_scores[0].confidence >= self.min_confidence * 0.85
            and raw_scores[1].confidence >= self.min_confidence * 0.70
            and raw_scores[0].confidence - raw_scores[1].confidence < self.AMBIGUITY_DELTA
        ):
            raw_scores[0].features["ambiguous_with"] = raw_scores[1].gesture

        # Attacher les états des doigts pour le diagnostic
        for gs in raw_scores:
            gs.features["finger_states"] = fa.states
            gs.features["finger_scores"] = fa.scores
            gs.features["pinch_dist"] = pinch_dist

        return raw_scores

    def best(self, hand: ProcessedHand) -> GestureScore:
        """Meilleur score unique, ou UNKNOWN si sous seuil."""
        scores = self.classify(hand)
        if scores and scores[0].confidence >= self.min_confidence:
            return scores[0]
        return GestureScore(
            "UNKNOWN", 0.0,
            {
                "all": [(s.gesture, round(s.confidence, 2)) for s in scores],
                "finger_states": scores[0].features.get("finger_states", {}),
                "finger_scores": scores[0].features.get("finger_scores", {}),
                "pinch_dist": scores[0].features.get("pinch_dist", 0.0),
            }
        )

