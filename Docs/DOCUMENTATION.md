# Documentation Technique — Hand Control (`App_C`)

## 1. Présentation du projet

**Hand Control** est une application Python de contrôle d'ordinateur par reconnaissance des gestes de la main via webcam. Elle s'appuie sur **MediaPipe Hand Landmarker** (Tasks API en mode `LIVE_STREAM`), **OpenCV** pour la capture et l'affichage temps réel, et **PyAutoGUI** pour la simulation des entrées souris.

### Prérequis et lancement

- **Environnement Python** : `env\` (Python avec `mediapipe`, `opencv-python`, `numpy`, `pyautogui`).
- **Modèle MediaPipe** : `hand_landmarker.task` à la racine du projet.
- **Exécution** :

```powershell
.\env\Scripts\python.exe main.py
```

Appuyer sur **Q** ou **Échap (`ESC`)** pour quitter proprement (libération de la webcam et réinitialisation de la souris).

---

## 2. Architecture du système

Le moteur est structuré en un pipeline modulaire à faible latence, sans modèle de deep learning supplémentaire, reposant sur une approche géométrique et temporelle explicable :

```text
Webcam (OpenCV)
   │
   ▼
MediaPipe HandLandmarker (LIVE_STREAM, 1 main)
   │  landmarks (21 pts) + world_landmarks
   ▼
GestureDetector (façade)
   ├── LandmarkProcessor     : coordonnées isotropes, lissage EMA, score de qualité
   ├── FingerStateAnalyzer   : état discret (EXTENDED/BENT/CURLED/UNKNOWN) + score continu [0..1]
   └── GestureClassifier     : scores indépendants par geste + détection d'ambiguïté
   │
   ▼  List[GestureScore] + ProcessedHand
GestureStateManager (façade)
   ├── TemporalGestureEngine    : EMA par geste, hystérésis, confirmation par paire, cooldown
   └── GestureTransitionManager : historique et export CSV (logs/transitions.csv)
   │
   ▼  EngineOutput
   ├── InputController       : commandes souris relatives, clic atomique, défilement, gel en transition
   └── DiagnosticOverlay     : HUD temps réel + curseur viseur au bout de l'index
```

### Arborescence des fichiers

| Fichier | Rôle |
|---|---|
| `main.py` | Boucle capture webcam, appel asynchrone MediaPipe, orchestration du pipeline et fenêtre OpenCV. |
| `gestures/landmark_processor.py` | Normalisation isotrope (`world_landmarks` ou correction `image_aspect`), lissage EMA des 21 points, calcul de `palm_width` et `quality`. |
| `gestures/finger_state_analyzer.py` | Calcul d'un score d'extension `[0.0 → 1.0]` et d'un état (`EXTENDED`, `BENT`, `CURLED`, `UNKNOWN`) pour chaque doigt, invariant paume/dos. |
| `gestures/gesture_classifier.py` | Évaluation indépendante de `INDEX`, `SCROLL`, `PINCH`, `FIST`, `OPEN_PALM` avec score de confiance et détection d'ambiguïté. |
| `gestures/temporal_engine.py` | Filtrage temporel EMA (`α=0.40`), hystérésis entrée/sortie, règles de transition par paire, cooldowns et tolérance aux pertes courtes de tracking. |
| `gestures/transition_manager.py` | Enregistrement en mémoire et export CSV des transitions d'états (`logs/transitions.csv`). |
| `gestures/diagnostic.py` | Affichage HUD (FPS, qualité, geste brut, état actif, candidat + barre de progression, états des doigts, distance de pincement) et viseur sur l'index. |
| `gestures/gesture_detector.py` | Façade rétrocompatible conservant `detect_gesture()` et `get_finger_states()`, plus `classify_all()`. |
| `gestures/gesture_state.py` | Façade rétrocompatible conservant `update()` et `reset()`, plus `update_full()`. |
| `gestures/input_controller.py` | Traduction des états confirmés en actions Windows (déplacement relatif, clic gauche atomique, scroll vertical) avec gel pendant les transitions. |

---

## 3. Gestes, états et règles temporelles

### Correspondance Gestes → États → Actions

| Geste | Critère géométrique principal | État système | Action `InputController` |
|---|---|---|---|
| `INDEX` | Index tendu (`≥ 0.55`), autres doigts repliés/demi-pliés, pas de pincement | `CURSOR` | Déplacement relatif du curseur (mode pavé tactile) |
| `SCROLL` | Index + majeur tendus, annulaire + auriculaire repliés, pas de pincement | `SCROLL` | Défilement vertical proportionnel au mouvement |
| `PINCH` | Pouce et index rapprochés (`pinch_dist < 0.52 × 1.4`), index non replié en poing | `PINCH` | Clic gauche atomique unique (`pyautogui.click`) |
| `FIST` | Tous les doigts repliés (pouce non fortement tendu) | `PAUSED` | Suspension de toutes les commandes |
| `OPEN_PALM` | Tous les doigts tendus (pouce inclus), sans pincement | `IDLE` | Reprise depuis `PAUSED` / aucune action |
| `UNKNOWN` | Qualité `< 0.15` ou aucun geste `≥ 0.52` | Maintien puis `IDLE` | Maintien `≤ 10` frames, puis retour à `IDLE` |

### Règles de transition et hystérésis

- **Hystérésis (`HYSTERESIS`)** : chaque geste possède un seuil d'entrée supérieur à son seuil de sortie afin d'empêcher les oscillations (ex. `INDEX`: entrée `0.62` / sortie `0.48`, `SCROLL`: `0.68` / `0.52`, `PINCH`: `0.60` / `0.46`, `FIST` et `OPEN_PALM`: `0.68` / `0.52`).
- **Confirmation par paire (`TRANSITION_RULES`)** :
  - `CURSOR → PINCH` : `2` frames consécutives, cooldown `4` frames, gel activé.
  - `CURSOR → SCROLL` : `4` frames consécutives, cooldown `3` frames, gel activé.
  - `ANY → PAUSED` (`FIST`) et `PAUSED → IDLE` (`OPEN_PALM`) : `5` frames consécutives, cooldown `8` frames, gel activé.
  - Règle par défaut : `3` frames, cooldown `3` frames, gel activé.
- **Gel d'approche du pincement** : dans `InputController.move_cursor()`, dès que `dist(4, 8) / palm_width < 0.52`, le curseur est figé avant même que l'état `PINCH` soit confirmé, évitant au curseur de glisser hors de la cible au moment du clic.

---

## 4. Problèmes révélés et résolutions

### A. Problèmes de stabilité et d'ergonomie initiaux

1. **Crash au démarrage (`'NoneType' object has no attribute 'hand_landmarks'`)**
   - *Cause* : au lancement, `latest_result` est `None` avant la première inférence asynchrone de MediaPipe.
   - *Correction* : garde explicite `if current_result is not None:` et gestion dédiée du cas `hand_count == 0` dans `main.py`.

2. **Inversion paume / dos de la main sur `FIST` et `OPEN_PALM`**
   - *Cause* : l'ancienne détection utilisait des comparaisons directionnelles ou une seule distance au poignet sensible à l'orientation de la main.
   - *Correction* : `FingerStateAnalyzer` combine trois critères géométriques invariants paume/dos (ratio `tip/wrist` vs `pip/wrist`, ratio `tip/mcp` vs `pip/mcp`, cosinus de flexion au PIP) et, pour le pouce, la projection latérale sur l'axe auriculaire→index et l'éloignement depuis la MCP de l'auriculaire.

3. **Saut du curseur au centre à chaque détection de l'index**
   - *Cause* : le mapping absolu repositionnait le curseur selon la position brute du doigt dans l'image, rendant certaines zones de l'écran difficiles à atteindre.
   - *Correction* : passage à un **déplacement relatif** (type pavé tactile) basé sur le delta `(dx, dy)` lissé, ancré sur la position courante du curseur Windows.

4. **Blocage lors du clic sur les boutons de fenêtre (réduire, agrandir)**
   - *Cause* : le maintien de `mouseDown` pendant plusieurs frames bloquait la boucle modale Windows de gestion des fenêtres.
   - *Correction* : remplacement par un **clic gauche atomique** unique (`pyautogui.click(button="left")`) déclenché une seule fois par entrée dans l'état `PINCH` via `pinch_clicked`.

5. **Dérive du curseur lors du pincement**
   - *Cause* : en rapprochant l'index du pouce pour pincer, le bout de l'index bouge et déplace le curseur hors du bouton visé.
   - *Correction* : ajout du seuil `pinch_freeze_threshold = 0.52` qui fige le curseur dès l'approche du pincement, combiné au gel complet durant `is_transitioning=True`.

6. **Ambiguïtés et oscillations lors des transitions rapides (`INDEX↔SCROLL`, `INDEX→PINCH`, `PINCH→OPEN_PALM`)**
   - *Cause* : classification binaire frame par frame sans mémoire ni état intermédiaire.
   - *Correction* : refonte complète autour de `FingerState.BENT`, d'un classifieur à scores continus, d'un moteur temporel EMA + hystérésis + règles par paire et d'un état explicite `TRANSITION`.

7. **Doubles instanciations et saturation `stdout` dans `main.py`**
   - *Cause* : instanciation en double des contrôleurs et `print()` dans le callback `on_result` à 30+ FPS.
   - *Correction* : instanciation unique dans `main()` et suppression des logs bloquants dans `on_result`.

### B. Bugs corrigés lors de la validation unitaire du nouveau moteur

1. **Double décroissance EMA (`temporal_engine.py`)**
   - *Symptôme* : les tests du moteur temporel restaient bloqués en `IDLE`.
   - *Cause* : `_update_ema()` appliquait `*= (1 - α)` à toutes les clés, puis réappliquait `prev * (1 - α) + conf * α`, plafonnant l'EMA à `0.575` pour une confiance de `0.92` (sous le seuil d'entrée `0.62`).
   - *Correction* : la décroissance naturelle n'est désormais appliquée qu'aux gestes absents de la frame courante.

2. **Contournement de l'hystérésis de sortie (`temporal_engine.py`)**
   - *Cause* : lorsque l'EMA du geste actif restait au-dessus de `cur_exit`, le code sautait la section 6 et entrait quand même en transition dans la section 7.
   - *Correction* : maintien explicite de l'état actif et réinitialisation du candidat tant que `current_ema >= cur_exit`.

3. **Collision `OPEN_PALM` vs `PINCH` (`gesture_classifier.py`)**
   - *Cause* : `_score_open_palm()` ne vérifiait pas `pinch_dist`, obtenant `0.96` lors d'un pincement avec les autres doigts tendus.
   - *Correction* : `_score_open_palm()` retourne `0.0` lorsque `pinch_dist < self.pinch_exit`.

4. **Suivi de `_prev_state` dans la façade `GestureStateManager` (`gesture_state.py`)**
   - *Cause* : `_prev_state` n'était mis à jour que dans `update_full()`, faussant le champ `changed` de `update()`.
   - *Correction* : initialisation dans `__init__` / `reset()` et mise à jour systématique dans `update()`.

### C. Résolution de la difficulté d'exécution du clic (`PINCH`)

L'analyse approfondie a révélé trois goulets d'étranglement qui rendaient le clic extrêmement difficile en conditions réelles :

1. **Pénalité artificielle sur posture naturelle (`gesture_classifier.py`)**
   - *Cause* : `_score_pinch()` multipliait son score par `(0.65 + 0.35 * openness)` où `openness` mesurait l'extension du majeur, annulaire et auriculaire. En contrôle souris naturel (doigts repliés dans la paume), `openness ≈ 0`, plafonnant le score à `0.65`. Même avec un contact franc (`pinch_dist = 0.30`), le score obtenu était `0.38`, inférieur à `MIN_CONFIDENCE = 0.52`. Le geste était donc systématiquement rejeté en `UNKNOWN`.
   - *Correction* : suppression de la pénalité `openness` (un pincement avec les doigts repliés est une posture 100% valide). Courbe continue calibrée sur les distances physiques réelles : `score = 1.0` pour `pinch_dist ≤ 0.30`, pente progressive jusqu'à `self.pinch_exit` (`0.52`).

2. **Conflit FIST vs PINCH (`gesture_classifier.py`)**
   - *Cause* : `_score_fist()` moyennait le repliement des 4 doigts sans vérifier si l'index était réellement replié au fond de la paume. Lors d'un pincement avec les 3 autres doigts repliés, `_score_fist()` obtenait `0.80` et entrait en compétition avec le pincement.
   - *Correction* : `_score_fist()` vérifie strictement que l'index est replié (`fa.scores["index"] <= 0.35`) et `_score_pinch()` exige que l'index aille à la rencontre du pouce (`fa.scores["index"] >= 0.20`), éliminant toute ambiguïté.

3. **Latence d'inertie et blocage d'hystérésis (`temporal_engine.py`)**
   - *Cause* : lorsque l'utilisateur était en `CURSOR`, l'EMA de l'ancien geste `INDEX` mettait 2 frames à redescendre sous son seuil de sortie (`0.48`), bloquant et annulant le candidat `PINCH`. Ensuite, l'EMA du `PINCH` mettait 2 frames supplémentaires à monter au-dessus de `0.60`. Un tap rapide (100–150 ms) était donc relâché avant même d'avoir pu déclencher le clic.
   - *Correction* :
     - Dès l'arrivée d'un pincement net (`confidence ≥ 0.60`), l'inertie résiduelle de `INDEX` est immédiatement ramenée à `0.20`.
     - L'hystérésis de sortie en `CURSOR` ne bloque plus la transition vers `PINCH`.
     - Seuil d'entrée du `PINCH` ajusté à `0.50` (au lieu de `0.60`) avec `α=0.60` pour le pincement, garantissant une confirmation en 2 frames (~60 ms) tout en conservant le gel stable du curseur.

### D. Élimination des faux pincements lors de la fermeture en poing (`FIST`)

L'analyse des logs réels a révélé que lorsque l'utilisateur fermait la main pour faire un `FIST` (pause) depuis `CURSOR` ou `SCROLL`, le système interceptait un faux `PINCH` pendant 1 ou 2 frames (le pouce et l'index se croisant au vol), déclenchant un clic involontaire juste avant la mise en pause.

- **Corrections géométriques (`gesture_classifier.py`)** :
  1. **Signature spatiale index/majeur** : dans un poing, l'extrémité de l'index (8) et celle du majeur (12) sont toutes deux repliées côte à côte dans la paume (`dist(8, 12) < 0.30 * palm_width`). Dans un vrai pincement, l'index est décollé dans l'espace vers le pouce, loin du majeur. Si les deux extrémités sont collées dans la paume, le `PINCH` est désormais strictement rejeté (`score = 0.0`).
  2. **Rétraction basale** : si l'extrémité de l'index est rétractée vers sa base MCP 5 (`tip_to_mcp5 < 0.38 * palm_width`) ou si l'index est recroquevillé (`score < 0.25`), le `PINCH` est rejeté.
- **Règles de transition ciblées (`temporal_engine.py`)** :
  - La transition rapide vers le clic (`2 frames`) n'est autorisée que depuis `CURSOR` (`CURSOR -> PINCH`), état de pointage où un clic est naturellement attendu.
  - Les transitions directes depuis `SCROLL` (`SCROLL -> PINCH`) et `IDLE` (`IDLE -> PINCH`) sont relevées à `4-5 frames`, évitant tout clic parasite lors d'une fermeture de main depuis le défilement.

### E. Pilotage directionnel vectoriel (`CURSOR` & `SCROLL`) contre les sorties de champ

1. **Problème révélé** :
   - L'analyse des logs réels (`transitions.csv`) a révélé de nombreuses sorties de champ de la webcam (`quality = 0.000` $\rightarrow$ `IDLE`) survenant presque exclusivement pendant ou juste après `CURSOR` et `SCROLL`.
   - *Cause* : le déplacement relatif par translation spatiale obligeait l'utilisateur à déplacer son bras dans l'espace pour atteindre les coins de l'écran ou pour scroller en continu. Au fil du mouvement ou de la fatigue, la main franchissait les bordures du cadre de la caméra.

2. **Solutions proposées** :
   - Abandonner la translation spatiale de la main au profit d'un **pilotage directionnel vectoriel (Rate Control / Joystick virtuel)** : la main reste ancrée au centre de l'image et seule l'orientation des doigts guide l'action.
   - Pour `CURSOR` : si l'index pointe en haut-droite, le curseur avance continuellement en haut-droite sans que la main ne bouge.
   - Pour `SCROLL` : si l'index et le majeur sont dressés vers le haut, défilement vers le haut (`TOP`). S'ils sont infléchis/inclinés vers le bas / vers la paume (sans être repliés en `FIST`), défilement vers le bas (`BOTTOM`). Zone intermédiaire neutre pour stopper le défilement.

3. **Solutions appliquées (`input_controller.py`)** :
   - **`move_cursor()`** :
     - Calcul du vecteur d'orientation de l'index $\vec{V} = (\text{Landmark } 8_{\text{TIP}} - \text{Landmark } 5_{\text{MCP}})$.
     - Calibrage automatique d'une position neutre à l'entrée dans l'état `CURSOR`.
     - Zone morte configurable (`joystick_deadzone = 0.16`) : dans cette zone de repos, le curseur est **parfaitement immobile**.
     - Au-delà de la zone morte, la vitesse du curseur est proportionnelle à l'angle d'inclinaison (micro-ajustements fins ou déplacements rapides à travers l'écran).
     - Le gel anticipé lors du pincement (`pinch_freeze_threshold`) reste actif pour verrouiller le curseur sur la cible au moment du clic.
   - **`scroll()`** :
     - Calcul de la hauteur relative moyenne des pointes (8, 12) par rapport aux bases MCP (5, 9).
     - Doigts dressés vers le haut ($rel\_y \le -0.80$) $\rightarrow$ scroll continu vers le haut (`TOP`).
     - Doigts infléchis vers le bas ($rel\_y \ge -0.56$) $\rightarrow$ scroll continu vers le bas (`BOTTOM`).
     - Zone neutre entre $-0.80$ et $-0.56$ $\rightarrow$ défilement arrêté, permettant une lecture confortable sans fatigue.

---

### F. Optimisation de la direction HAUT et couplage tendineux (`CURSOR`)

1. **Problème révélé** :
   - Difficulté constatée pour diriger le curseur vers le haut (`UP`) en mode joystick directionnel, avec parfois une perte ou instabilité du geste `CURSOR`.
   - *Causes anatomiques et géométriques* :
     - **Limite physique de l'index** : En coordonnées caméra ($y=0$ en haut), un index dressé a un vecteur vertical $vy = (y_{tip} - y_{mcp}) / \text{palm\_width} \approx -1.05$ à $-1.15$ (longueur osseuse maximale). En calibrant le point neutre initial à $-1.10$, atteindre la zone morte vers le haut imposait $vy < -1.10 - 0.16 = -1.26$, ce qui est anatomiquement impossible.
     - **Couplage tendineux index/majeur** : Tendre l'index au maximum vers le haut étire les tendons partagés de la main, provoquant une légère extension passive du majeur ($score > 0.38$), ce qui pénalisait ou invalidait le score `CURSOR`.

2. **Solutions appliquées** :
   - **Découplage et ancrage du neutre vertical (`input_controller.py`)** :
     - La référence neutre verticale est ancrée à une posture naturelle de repos ($ny = -0.74$, main inclinée à $\approx 60^\circ$).
     - Dès que l'index est tendu droit vers le haut ($vy \approx -1.05$), le déplacement vers le haut s'enclenche immédiatement ($\Delta y = -0.31 < -\text{deadzone}$).
   - **Amplification et sensibilité du haut** :
     - Zone morte abaissée à `0.12` pour un contrôle plus fluide et immédiat.
     - Facteur d'amplification vers le haut (`joystick_up_boost = 1.45`) pour équilibrer la vitesse verticale par rapport aux mouvements horizontaux beaucoup plus amples.
   - **Tolérance du couplage tendineux (`gesture_classifier.py`)** :
     - Seuil de tolérance du majeur rehaussé de `0.38` à `0.42` dans `_score_index()`, évitant toute confusion ou dégradation du pointage quand le majeur suit légèrement l'index vers le haut.
   - **Feedback visuel en temps réel (`diagnostic.py`)** :
     - Ajout d'une ligne de visée vectorielle dynamique (reliant la base MCP 5 à la pointe TIP 8) affichée directement sur la webcam en mode `CURSOR`, permettant à l'utilisateur de visualiser précisément l'angle d'inclinaison perçu par l'algorithme.

### G. Discrimination stricte entre l'inclinaison directionnelle de l'index (`CURSOR`) et le clic (`PINCH`)

1. **Problème révélé** :
   - Dès que l'index était incliné vers la gauche, vers la droite ou vers le bas pour guider le curseur, le système basculait intempestivement en `PINCH` et déclenchait des clics involontaires en boucle (phénomène corroboré dans `logs/transitions.csv` par des allers-retours continus `CURSOR <-> PINCH`).
   - *Causes géométriques et logiques* :
     - **Seuils de pincement excessivement larges** : `pinch_exit_threshold` était calibré à `0.52` (plus de la moitié de la paume, soit ~4,5 cm de vide entre les doigts). En inclinant l'index dans l'espace, la pointe de l'index se rapprochait géométriquement du pouce au repos (distance relative $pprox 0.30$ à $0.45$), accordant à tort un score `PINCH` entre $0.80$ et $0.92$.
     - **Coupure brutale de l'index** : `_score_index()` contenait un seuil binaire `if pinch_dist < self.pinch_exit: return 0.0`. Dès que l'index s'inclinait, le score `INDEX` était instantanément annihilé à 0.0.
     - **Gel prématuré du déplacement (`InputController`)** : `pinch_freeze_threshold = 0.52` bloquait immédiatement la vitesse du curseur à $(0, 0)$ dès que la distance passait sous 0.52, figeant la souris en plein mouvement.

2. **Solutions appliquées** :
   - **Resserrement biométrique des seuils de pincement (`gesture_classifier.py`)** :
     - `pinch_enter = 0.20`, `pinch_exit = 0.28` (au lieu de 0.42 et 0.52).
     - Le score de pincement tombe strictement à `0.0` dès que la distance dépasse $0.28$ (environ 2,2 cm, ce qui exclut tout doigt en navigation libre).
   - **Garde-fou du pointeur directionnel (`_score_pinch()`)** :
     - Si l'index est tendu en posture de pointage rectiligne ($fa.scores[	ext{"index"}] > 0.65$ et $	ext{dist}(8, 5) > 0.88 \cdot 	ext{palm\_width}$), tout `PINCH` est systématiquement refusé à moins d'un contact physique immédiat ($	ext{pinch\_dist} \le 0.20$).
   - **Déréférencement progressif de l'index (`_score_index()`)** :
     - Le score de l'index n'est mis à 0.0 qu'en cas de contact avéré ($\le 0.20$). En zone de navigation ($> 0.28$), l'index conserve 100% de son amplitude quelle que soit la direction visée.
   - **Abaissement du seuil de gel anti-dérive (`input_controller.py` & `main.py`)** :
     - `pinch_freeze_threshold` abaissé de `0.52` à `0.28`. Le curseur conserve ainsi toute sa fluidité et sa réactivité dans les 8 directions cardinales, et ne se fige qu'au moment précis où les doigts se touchent pour cliquer.

---

## 5. Résumé des améliorations apportées

- **Architecture découplée et extensible** : séparation claire entre traitement géométrique (`LandmarkProcessor`, `FingerStateAnalyzer`), classification (`GestureClassifier`), logique temporelle (`TemporalGestureEngine`, `GestureTransitionManager`), commandes OS (`InputController`) et visualisation (`DiagnosticOverlay`).
- **Pilotage directionnel vectoriel** : élimination définitive des sorties de champ en maintenant la main au centre de la caméra pendant le pointage et le scroll.
- **Rétrocompatibilité préservée** : les anciennes interfaces `GestureDetector` et `GestureStateManager` restent utilisables sans casser d'éventuels scripts existants.
- **Observabilité complète** : HUD temps réel (curseur violet en `TRANSITION`, barre de progression de confirmation, scores par doigt, distance de pincement) et journalisation automatique des transitions dans `logs/transitions.csv`.
- **Tests automatisés** : suite de 18 tests unitaires validant l'ensemble du pipeline géométrique, temporel, anti-poing et directionnel (tous passés avec succès).

---

## 6. Paramètres ajustables après test en conditions réelles

Si certains gestes semblent trop stricts ou trop sensibles avec ta webcam, voici les principaux leviers de réglage :

| Paramètre | Fichier | Valeur actuelle | Effet |
|---|---|---|---|
| `joystick_speed` | `input_controller.py` | `28.0` | Vitesse maximale du curseur en pilotage directionnel |
| `joystick_deadzone` | `input_controller.py` | `0.12` | Rayon de la zone morte neutre où le curseur ne bouge pas |
| `joystick_up_boost` | `input_controller.py` | `1.45` | Facteur d'amplification de la vitesse vers le haut |
| `cursor_sensitivity` | `main.py` / `input_controller.py` | `1.6` | Sensibilité générale du curseur |
| `smoothing` | `main.py` / `input_controller.py` | `0.25` | Lissage du mouvement curseur |
| `scroll_up_threshold` | `input_controller.py` | `-0.78` | Seuil d'inclinaison des 2 doigts vers le haut (Scroll TOP) |
| `scroll_down_threshold` | `input_controller.py` | `-0.56` | Seuil d'inclinaison des 2 doigts vers le bas (Scroll BOTTOM) |
| `scroll_sensitivity` | `input_controller.py` | `3.0` | Vitesse de défilement de la page |
| `pinch_freeze_threshold` | `main.py` / `input_controller.py` | `0.28` | Distance à laquelle le curseur se fige avant clic |
| `EMA_ALPHA` | `gestures/temporal_engine.py` | `0.40` | Réactivité vs lissage temporel des scores |
| `HYSTERESIS` | `gestures/temporal_engine.py` | par geste | Seuils d'entrée et de sortie des états |
| `TRANSITION_RULES` | `gestures/temporal_engine.py` | `2` à `5` frames | Nombre de frames requises pour confirmer un changement |

