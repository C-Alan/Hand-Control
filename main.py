
import cv2
import mediapipe as mp
import os
import threading
import time

import numpy as np

from gestures.gesture_detector   import GestureDetector
from gestures.gesture_state      import GestureStateManager
from gestures.input_controller   import InputController
from gestures.diagnostic         import DiagnosticOverlay

# =====================================================
# CONFIGURATION
# =====================================================

CAMERA_INDEX = 0
MODEL_PATH   = "hand_landmarker.task"
NUM_HANDS    = 1               # 1 main active à la fois (état unique)
WINDOW_NAME  = "Hand Control"
LOG_DIR      = "logs"

# =====================================================
# MEDIAPIPE
# =====================================================

BaseOptions          = mp.tasks.BaseOptions
HandLandmarker       = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode    = mp.tasks.vision.RunningMode

latest_result = None
result_lock   = threading.Lock()


def on_result(result, output_image, timestamp_ms):
    global latest_result
    with result_lock:
        latest_result = result


# =====================================================
# BOUCLE PRINCIPALE
# =====================================================

def main():
    global latest_result

    if not os.path.exists(MODEL_PATH):
        print(f"Erreur : modèle introuvable : {MODEL_PATH}")
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "transitions.csv")

    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.LIVE_STREAM,
        num_hands=NUM_HANDS,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        result_callback=on_result,
    )

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Erreur : impossible d'ouvrir la webcam.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    detector    = GestureDetector()
    state_mgr   = GestureStateManager(log_path=log_path)
    controller  = InputController(
        mirror=False,
        smoothing=0.25,
        cursor_sensitivity=1.6,
        pinch_freeze_threshold=0.52,
    )
    overlay     = DiagnosticOverlay(enabled=True)

    prev_time  = time.perf_counter()

    print("===================================")
    print("       HAND CONTROL STARTED")
    print("===================================")
    print("Appuie sur Q ou ESC pour quitter.")
    print(f"Transitions enregistrées dans : {log_path}")

    try:
        with HandLandmarker.create_from_options(options) as landmarker:

            while True:
                ok, frame = cap.read()
                if not ok:
                    print("Erreur de lecture webcam.")
                    break

                frame = cv2.flip(frame, 1)
                h, w  = frame.shape[:2]

                rgb = np.ascontiguousarray(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                )
                mp_image     = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = time.perf_counter_ns() // 1_000_000

                landmarker.detect_async(mp_image, timestamp_ms)

                with result_lock:
                    current_result = latest_result

                # ── Calcul FPS ──────────────────────────
                now      = time.perf_counter()
                fps      = 1.0 / max(now - prev_time, 1e-6)
                prev_time = now

                # ── Traitement des mains ─────────────────
                hand_count = 0
                engine_out = None

                if current_result is not None:
                    hand_count = len(current_result.hand_landmarks)

                    if hand_count > 0:
                        # On utilise la première main détectée
                        landmarks = current_result.hand_landmarks[0]
                        world_lms = (
                            current_result.hand_world_landmarks[0]
                            if current_result.hand_world_landmarks
                            else None
                        )

                        # Classification complète (liste de GestureScore + ProcessedHand)
                        scores, hand = detector.classify_all(
                            landmarks,
                            world_landmarks=world_lms,
                            image_aspect=w / h,
                        )

                        # Mise à jour du moteur temporel
                        # Injecter la qualité dans les scores pour la façade
                        for s in scores:
                            s.features["quality"] = hand.quality

                        engine_out = state_mgr.update_full(scores, hand.quality)

                        # Exécution des commandes
                        controller.process(
                            state=engine_out.active_state,
                            landmarks=landmarks,
                            frame_width=w,
                            frame_height=h,
                            is_transitioning=engine_out.is_transitioning,
                        )

                        # Réinitialiser pinch_clicked dès que l'état quitte PINCH
                        if engine_out.active_state != "PINCH":
                            controller.pinch_clicked = False

                        # Curseur visuel
                        display_state = (
                            "TRANSITION"
                            if engine_out.is_transitioning
                            else engine_out.active_state
                        )
                        overlay.draw_index_cursor(
                            frame, landmarks, display_state,
                            engine_out.is_transitioning,
                        )

                # ── Perte de main ───────────────────────
                if hand_count == 0:
                    from gestures.gesture_classifier import GestureScore
                    lost_scores = [GestureScore("UNKNOWN", 0.0)]
                    engine_out  = state_mgr.update_full(lost_scores, 0.0)
                    controller.process(
                        state="IDLE",
                        landmarks=None,
                        is_transitioning=False,
                    )

                # ── Diagnostic overlay ──────────────────
                if engine_out is not None:
                    overlay.draw(frame, engine_out, fps, hand_count)
                else:
                    from gestures.temporal_engine import EngineOutput
                    dummy = EngineOutput(
                        active_state="IDLE", candidate=None,
                        is_transitioning=False, confidence=0.0,
                        candidate_progress=0.0, quality=0.0,
                        finger_states={}, finger_scores={},
                        pinch_dist=0.0, raw_gesture="UNKNOWN",
                    )
                    overlay.draw(frame, dummy, fps, 0)

                cv2.imshow(WINDOW_NAME, frame)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

    except Exception as err:
        print(f"Erreur : {err}")
        import traceback
        traceback.print_exc()

    finally:
        controller.close()
        cap.release()
        cv2.destroyAllWindows()
        print("Hand Control arrêté.")
        print(f"Transitions enregistrées dans : {log_path}")


if __name__ == "__main__":
    main()
