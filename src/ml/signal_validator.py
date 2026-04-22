"""
ML Signal Validator for StockWarren
Uses Random Forest / Gradient Boosting to validate trading signals
Adapted from FutureWarren's ML engine for stock trading
"""

import os
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report
import joblib

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of ML signal validation"""
    approved: bool
    confidence: float        # 0-100
    model_type: str
    features_used: int
    prediction_proba: list
    top_features: list


class SignalValidator:
    """ML-based signal validation to filter false signals"""

    FEATURE_NAMES = [
        # Price features
        "price_change_1bar", "price_change_5bar", "price_change_10bar",
        "price_vs_ema9", "price_vs_ema21", "price_vs_ema50",
        # Indicator features
        "rsi_value", "macd_histogram", "bb_position",
        "volume_ratio", "atr_ratio",
        # Signal features
        "signal_strength", "num_confirmations", "signal_direction",
        # Time features
        "hour_of_day", "day_of_week", "minutes_since_open",
        # Market context
        "daily_range_pct", "gap_pct", "prev_close_change",
    ]

    def __init__(self, model_dir: str = "data/models", model_type: str = "random_forest"):
        self.model_dir = model_dir
        self.model_type = model_type
        self.model = None
        self.scaler = StandardScaler()
        self.is_trained = False
        self.training_history = []
        self.min_training_samples = 50

        os.makedirs(model_dir, exist_ok=True)
        self._load_model()

    def _create_model(self):
        """Create a new ML model"""
        if self.model_type == "random_forest":
            return RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                min_samples_split=5,
                min_samples_leaf=2,
                random_state=42,
                n_jobs=-1,
            )
        elif self.model_type == "gradient_boosting":
            return GradientBoostingClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                min_samples_split=5,
                random_state=42,
            )
        else:
            raise ValueError(f"Unknown model type: {self.model_type}")

    def extract_features(self, df: pd.DataFrame, composite_signal) -> np.ndarray:
        """Extract features from market data and signal for ML prediction"""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df.get("volume", pd.Series([0] * len(df)))

        features = []

        # Price change features
        features.append(close.pct_change(1).iloc[-1] * 100 if len(close) > 1 else 0)
        features.append(close.pct_change(5).iloc[-1] * 100 if len(close) > 5 else 0)
        features.append(close.pct_change(10).iloc[-1] * 100 if len(close) > 10 else 0)

        # Price vs EMAs
        current_price = close.iloc[-1]
        ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        features.append((current_price - ema9) / ema9 * 100 if ema9 != 0 else 0)
        features.append((current_price - ema21) / ema21 * 100 if ema21 != 0 else 0)
        features.append((current_price - ema50) / ema50 * 100 if ema50 != 0 else 0)

        # Indicator values from composite signal
        rsi_val = 50.0
        macd_hist = 0.0
        bb_pos = 0.5
        vol_ratio = 1.0
        atr_ratio = 1.0

        for sig in composite_signal.signals:
            if sig.name == "RSI":
                rsi_val = sig.value
            elif sig.name == "MACD":
                macd_hist = sig.value
            elif sig.name == "Bollinger":
                bb_pos = sig.value
            elif sig.name == "Volume":
                vol_ratio = sig.value
            elif sig.name == "ATR":
                atr_ratio = sig.value

        features.extend([rsi_val, macd_hist, bb_pos, vol_ratio, atr_ratio])

        # Signal features
        features.append(composite_signal.strength)
        features.append(composite_signal.confirmations)
        features.append(composite_signal.direction)

        # Time features
        now = datetime.now()
        features.append(now.hour)
        features.append(now.weekday())
        market_open = now.replace(hour=9, minute=30, second=0)
        minutes_since_open = max(0, (now - market_open).total_seconds() / 60)
        features.append(minutes_since_open)

        # Market context
        daily_range = (high.iloc[-1] - low.iloc[-1]) / close.iloc[-1] * 100
        features.append(daily_range)

        gap_pct = 0
        if len(close) > 1:
            gap_pct = (close.iloc[0] - close.iloc[-2]) / close.iloc[-2] * 100 if close.iloc[-2] != 0 else 0
        features.append(gap_pct)

        prev_close_change = close.pct_change(1).iloc[-2] * 100 if len(close) > 2 else 0
        features.append(prev_close_change)

        return np.array(features).reshape(1, -1)

    def validate_signal(self, df: pd.DataFrame, composite_signal) -> ValidationResult:
        """Validate a trading signal using the ML model"""
        if not self.is_trained:
            return ValidationResult(
                approved=True,
                confidence=composite_signal.strength,
                model_type="none",
                features_used=0,
                prediction_proba=[],
                top_features=[],
            )

        features = self.extract_features(df, composite_signal)
        features_scaled = self.scaler.transform(features)

        prediction = self.model.predict(features_scaled)[0]
        proba = self.model.predict_proba(features_scaled)[0]
        confidence = max(proba) * 100

        # Get feature importances
        top_features = []
        if hasattr(self.model, "feature_importances_"):
            importances = self.model.feature_importances_
            top_idx = np.argsort(importances)[-5:][::-1]
            top_features = [
                (self.FEATURE_NAMES[i], importances[i])
                for i in top_idx
                if i < len(self.FEATURE_NAMES)
            ]

        approved = prediction == 1 and confidence >= 55

        return ValidationResult(
            approved=approved,
            confidence=confidence,
            model_type=self.model_type,
            features_used=features.shape[1],
            prediction_proba=proba.tolist(),
            top_features=top_features,
        )

    def train(self, X: np.ndarray, y: np.ndarray) -> dict:
        """Train or retrain the model with new data"""
        if len(X) < self.min_training_samples:
            logger.warning(f"Not enough training data: {len(X)} < {self.min_training_samples}")
            return {"status": "insufficient_data", "samples": len(X)}

        self.model = self._create_model()
        self.scaler.fit(X)
        X_scaled = self.scaler.transform(X)

        # Cross-validation
        cv_scores = cross_val_score(self.model, X_scaled, y, cv=5, scoring="accuracy")

        # Train on full dataset
        self.model.fit(X_scaled, y)
        self.is_trained = True

        # Save model
        self._save_model()

        result = {
            "status": "trained",
            "samples": len(X),
            "cv_accuracy_mean": cv_scores.mean(),
            "cv_accuracy_std": cv_scores.std(),
            "model_type": self.model_type,
        }

        logger.info(f"Model trained: {result}")
        self.training_history.append(result)
        return result

    def add_training_sample(self, df: pd.DataFrame, composite_signal, outcome: bool):
        """Add a new training sample from a completed trade"""
        features = self.extract_features(df, composite_signal)
        label = 1 if outcome else 0

        sample_file = os.path.join(self.model_dir, "training_data.npz")

        if os.path.exists(sample_file):
            data = np.load(sample_file)
            X = np.vstack([data["X"], features])
            y = np.append(data["y"], label)
        else:
            X = features
            y = np.array([label])

        np.savez(sample_file, X=X, y=y)
        logger.info(f"Training sample added. Total samples: {len(y)}")

        # Auto-retrain when enough new data
        if len(y) >= self.min_training_samples and len(y) % 10 == 0:
            logger.info("Auto-retraining model...")
            self.train(X, y)

    def _save_model(self):
        """Save model and scaler to disk"""
        if self.model is not None:
            model_path = os.path.join(self.model_dir, f"signal_model_{self.model_type}.pkl")
            scaler_path = os.path.join(self.model_dir, "scaler.pkl")
            joblib.dump(self.model, model_path)
            joblib.dump(self.scaler, scaler_path)
            logger.info(f"Model saved to {model_path}")

    def _load_model(self):
        """Load model and scaler from disk"""
        model_path = os.path.join(self.model_dir, f"signal_model_{self.model_type}.pkl")
        scaler_path = os.path.join(self.model_dir, "scaler.pkl")

        if os.path.exists(model_path) and os.path.exists(scaler_path):
            self.model = joblib.load(model_path)
            self.scaler = joblib.load(scaler_path)
            self.is_trained = True
            logger.info(f"Model loaded from {model_path}")
