"""
infer.py — run the trained model on new skeleton pickle files

Usage:
    # Score a single clip:
    python infer.py --checkpoint ./checkpoints/best_model.pth --clip ./data/new_talk.pkl

    # Score a whole folder and save results to CSV:
    python infer.py --checkpoint ./checkpoints/best_model.pth --folder ./data/new_clips/
"""

import os
import argparse
import pickle
import json
import numpy as np
import pandas as pd
import torch

from dataset import SkeletonDataset, extract_skeleton, SCORE_COLUMNS
from model import STGCN, SkeletonLSTM, body25_edges


def load_model(checkpoint_path, device):
    """Load the trained model from a checkpoint file."""
    ckpt = torch.load(checkpoint_path, map_location=device)
    cfg = ckpt["config"]

    if cfg["model"] == "stgcn":
        model = STGCN(
            in_channels=cfg["n_coords"],
            n_joints=cfg["n_joints"],
            n_outputs=cfg["n_outputs"],
            edges=body25_edges(),
            dropout=0.0,        # disable dropout at inference time
        )
    else:
        model = SkeletonLSTM(
            n_joints=cfg["n_joints"],
            n_coords=cfg["n_coords"],
            n_outputs=cfg["n_outputs"],
            dropout=0.0,
        )

    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    model.to(device)

    print(f"Loaded {cfg['model'].upper()} model from epoch {ckpt['epoch']}")
    print(f"Val loss at save: {ckpt['val_loss']:.4f}")
    print(f"Scoring: {cfg['score_columns']}")

    return model, cfg


def score_clip(pkl_path, model, cfg, device):
    """
    Run inference on a single pickle file.
    Returns a dict of {dimension: score} values.
    """
    use_groups = cfg.get("use_groups", ["pose_keypoints",
                                        "hand_left_keypoints",
                                        "hand_right_keypoints"])

    # Reuse the dataset's preprocessing logic via a lightweight helper instance
    helper = SkeletonDataset.__new__(SkeletonDataset)
    helper.target_frames = cfg["target_frames"]
    helper.use_groups    = use_groups
    helper.augment       = False

    raw      = SkeletonDataset._load_pickle(pkl_path)
    skeleton = extract_skeleton(raw, use_groups)
    skeleton = helper._normalize(skeleton)
    skeleton = helper._resample(skeleton)

    # (C, T, V) → add batch dim → (1, C, T, V)
    tensor = torch.FloatTensor(skeleton).permute(2, 0, 1).unsqueeze(0).to(device)

    with torch.no_grad():
        scores = model(tensor).squeeze(0).cpu().numpy()

    return {col: float(scores[i]) for i, col in enumerate(cfg["score_columns"])}


def interpret_scores(scores):
    """
    Wandelt Rohscores in verständliche Rückmeldungen um.
    Schwellenwert-basierte Interpretation.
    """
    feedback = []
    for dim, score in scores.items():
        if score >= 0.7:
            level = "stark"
        elif score >= 0.4:
            level = "mittel"
        else:
            level = "ausbaufähig"

        tipps = {
            "gesture_variety": {
                "stark":       "Sehr gute Gestenvielfalt — du setzt eine breite Palette an Handbewegungen ein.",
                "mittel":      "Mäßige Gestenvielfalt. Wechsle zwischen zeigenden, offenen und zählenden Gesten.",
                "ausbaufähig": "Wenig Gestik erkannt. Variiere deine Handbewegungen, um Kernpunkte zu betonen.",
            },
            "body_openness": {
                "stark":       "Offene Körperhaltung — Arme weg vom Körper, guter Blickkontakt zum Publikum.",
                "mittel":      "Mäßige Offenheit. Vermeide verschränkte Arme und stehe mehr zum Publikum gewandt.",
                "ausbaufähig": "Geschlossene Haltung erkannt. Öffne den Oberkörper und halte die Arme locker.",
            },
            "movement_energy": {
                "stark":       "Gute Bewegungsenergie — lebendig und engagiert.",
                "mittel":      "Moderate Bewegung. Etwas mehr körperliches Engagement würde helfen.",
                "ausbaufähig": "Geringe Bewegungsenergie. Nutze den Raum und animiere deine Präsenz auf der Bühne.",
            },
            "head_movement": {
                "stark":       "Natürliche Kopfbewegung — gutes Nicken und gerichtetes Engagement.",
                "mittel":      "Etwas Kopfbewegung vorhanden. Mehr Variation würde das Engagement erhöhen.",
                "ausbaufähig": "Kaum Kopfbewegung. Nicke zur Betonung von Punkten und scanne den Raum.",
            },
            "expressiveness": {
                "stark":       "Sehr ausdrucksstark — Gesicht und Körper vermitteln Begeisterung.",
                "mittel":      "Moderate Ausdrucksstärke. Mehr Mimik würde die Wirkung verstärken.",
                "ausbaufähig": "Geringe Ausdrucksstärke. Lass Gesicht und Körper deine Botschaft spiegeln.",
            },
        }

        tipp = tipps.get(dim, {}).get(level, f"{dim}: {level}")
        feedback.append({
            "dimension": dim,
            "score": round(score, 3),
            "level": level,
            "feedback": tipp,
        })

    return feedback


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", required=True, help="Path to best_model.pth")
    p.add_argument("--clip",   default=None, help="Single .pkl file to score")
    p.add_argument("--folder", default=None, help="Folder of .pkl files to score")
    p.add_argument("--output", default="results.csv", help="Output CSV for batch mode")
    args = p.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, cfg = load_model(args.checkpoint, device)

    # ---- Single clip ----
    if args.clip:
        print(f"\nScoring: {args.clip}")
        scores = score_clip(args.clip, model, cfg, device)
        feedback = interpret_scores(scores)

        print("\n" + "="*50)
        print("PRESENTATION ANALYSIS — SKELETON MODULE")
        print("="*50)
        for item in feedback:
            bar = "█" * int(item["score"] * 20) + "░" * (20 - int(item["score"] * 20))
            print(f"\n{item['dimension']}")
            print(f"  Score: {item['score']:.2f}  [{bar}]  ({item['level']})")
            print(f"  {item['feedback']}")

        print("\nRaw scores (for fusion module):")
        print(json.dumps(scores, indent=2))
        return scores

    # ---- Batch folder ----
    if args.folder:
        pkl_files = [f for f in os.listdir(args.folder) if f.endswith(".pickle")]
        print(f"\nScoring {len(pkl_files)} clips in {args.folder}...")

        rows = []
        for fname in pkl_files:
            path = os.path.join(args.folder, fname)
            try:
                scores = score_clip(path, model, cfg, device)
                scores["filename"] = fname
                rows.append(scores)
                print(f"  {fname}: {scores}")
            except Exception as e:
                print(f"  ERROR {fname}: {e}")

        df = pd.DataFrame(rows)
        df.to_csv(args.output, index=False)
        print(f"\nResults saved to {args.output}")

        print("\nSummary statistics:")
        print(df[SCORE_COLUMNS].describe().round(3))


if __name__ == "__main__":
    main()
