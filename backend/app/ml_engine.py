import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.linear_model import Ridge
from sqlalchemy.orm import Session
from . import models

class IndustrialMLEngine:
    """
    Lightweight, production-ready industrial ML engine for SenseMinds CEMS.
    Provides:
      1. Multi-horizon Time-Series Forecasting (Ridge with lag & trend features)
      2. Multivariate Anomaly Detection (Isolation Forest)
      3. Scrubber Fault Diagnosis & Root Cause Classification (Random Forest)
      4. Dynamic Alkaline Neutralization Dosing Recommendation (Titration Physics Model)
    """

    def __init__(self):
        # Isolation Forest instance for anomaly detection
        self.anomaly_detector = IsolationForest(
            n_estimators=100,
            contamination=0.06,
            random_state=42
        )
        self.is_anomaly_fitted = False

        # Diagnostic classifier for scrubber root-cause classification
        self.diagnostic_model = None
        self._init_diagnostic_model()

    def _init_diagnostic_model(self):
        """Initializes and trains the Random Forest root-cause classifier on domain operational rules."""
        # Features: [pH, H2S_ppm, CO_ppm, pH_trend_slope, caustic_pump_active (1/0)]
        X_train = [
            # Normal operations (Class 0: Normal Neutralization)
            [11.0, 4.5, 3.2, 0.0, 1],
            [10.8, 5.2, 3.5, 0.01, 1],
            [11.4, 3.8, 2.9, -0.01, 1],
            [10.5, 6.0, 4.1, 0.0, 1],
            [11.2, 4.0, 3.0, 0.02, 1],
            
            # Acid Gas Overload Surge (Class 1)
            [9.4, 21.5, 8.5, -0.25, 1],
            [9.2, 23.0, 9.2, -0.30, 1],
            [9.1, 22.1, 9.0, -0.28, 1],
            [9.5, 19.8, 7.8, -0.20, 1],

            # Caustic Dosing Pump Under-Feed / Trip (Class 2)
            [8.8, 5.5, 3.4, -0.40, 0],
            [8.2, 6.0, 3.6, -0.45, 0],
            [7.5, 5.0, 3.0, -0.50, 0],
            [9.0, 4.8, 3.1, -0.35, 0],

            # Excessive Caustic Over-Dosing (Class 3)
            [12.8, 3.0, 2.5, 0.35, 1],
            [13.2, 2.8, 2.2, 0.40, 1],
            [12.6, 3.5, 2.8, 0.30, 1],

            # Sensor Fouling / Probe Drift (Class 4)
            [6.0, 4.5, 3.0, 0.0, 1],
            [14.0, 4.5, 3.0, 0.0, 1],
            [1.0, 4.5, 3.0, 0.0, 1]
        ]
        y_train = [0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4]
        
        self.diagnostic_model = RandomForestClassifier(n_estimators=50, random_state=42)
        self.diagnostic_model.fit(X_train, y_train)

    def forecast_sensor_telemetry(self, db: Session, sensor_id: int, steps_ahead: int = 4) -> Dict[str, Any]:
        """
        Fits a fast lag-based Ridge regression model on recent time-series data
        and projects gas concentration / pH into future horizons (+30m, +1h, +4h).
        """
        # Fetch last 100 historical readings for this sensor
        rows = (
            db.query(models.GasReading)
            .filter(models.GasReading.sensor_id == sensor_id, models.GasReading.value.isnot(None))
            .order_by(models.GasReading.timestamp.asc())
            .all()
        )

        if len(rows) < 10:
            return {
                "error": "Insufficient historical data for forecasting (minimum 10 points required).",
                "forecasts": []
            }

        values = [r.value for r in rows]
        latest_reading = rows[-1]
        sensor_type = latest_reading.gas_type
        unit = latest_reading.unit
        device_name = latest_reading.device_name

        # Create autoregressive lag dataset (lags: t-1, t-2, t-3, t-4)
        lags = 4
        X, y = [], []
        for i in range(lags, len(values)):
            X.append(values[i-lags:i])
            y.append(values[i])

        X = np.array(X)
        y = np.array(y)

        model = Ridge(alpha=1.0)
        model.fit(X, y)

        # Iterative multi-step projection
        forecasts = []
        current_window = list(values[-lags:])
        horizons = [
            ("+30 min", 2),
            ("+1 hour", 4),
            ("+2 hours", 8),
            ("+4 hours", 16)
        ]

        # Project up to 16 intervals (4 hours assuming 15m steps)
        projected_series = []
        for step in range(16):
            pred = float(model.predict([current_window[-lags:]])[0])
            # Bound predictions logically
            if sensor_type == "pH":
                pred = max(1.0, min(14.0, pred))
            else:
                pred = max(0.0, pred)
            projected_series.append(round(pred, 2))
            current_window.append(pred)

        for label, step_idx in horizons:
            pred_val = projected_series[step_idx - 1]
            status = "Normal"
            if sensor_type == "H2S":
                if pred_val >= 20.0:
                    status = "Critical (Exceeds 20 PPM emergency threshold)"
                elif pred_val >= 10.0:
                    status = "Warning (Exceeds 10 PPM advisory limit)"
            elif sensor_type == "VOC":
                if pred_val >= 50.0:
                    status = "Critical (Exceeds 50 ug/m3 statutory stack cap)"
            elif sensor_type == "pH":
                if pred_val < 9.5:
                    status = "Warning (Low alkali neutralization risk)"
                elif pred_val > 12.5:
                    status = "Warning (Excessive caustic dosing risk)"

            forecasts.append({
                "horizon": label,
                "projected_value": pred_val,
                "unit": unit,
                "status": status
            })

        return {
            "sensor_id": sensor_id,
            "device_name": device_name,
            "gas_type": sensor_type,
            "current_value": round(values[-1], 2),
            "unit": unit,
            "model": "Ridge Autoregressive Lag Forecaster (AR-4)",
            "forecasts": forecasts
        }

    def detect_anomalies(self, db: Session, limit: int = 150) -> Dict[str, Any]:
        """
        Runs an Isolation Forest on multi-sensor readings to detect chemical upsets,
        rapid transients, or simultaneous parameter drifts.
        """
        # Fetch recent readings for Sensor 1 (CO), Sensor 2 (H2S), Sensor 101 (pH)
        readings = (
            db.query(models.GasReading)
            .filter(models.GasReading.value.isnot(None))
            .order_by(models.GasReading.timestamp.desc())
            .limit(limit)
            .all()
        )

        if len(readings) < 20:
            return {"anomalies_detected": 0, "anomalies": []}

        # Format features for anomaly detection: [value, hour_of_day, sensor_id]
        feature_data = []
        meta_data = []
        for r in readings:
            hour = r.timestamp.hour if r.timestamp else 12
            val = r.value or 0.0
            feature_data.append([val, hour, float(r.sensor_id)])
            meta_data.append(r)

        X = np.array(feature_data)
        self.anomaly_detector.fit(X)
        predictions = self.anomaly_detector.predict(X) # -1: anomaly, 1: normal
        decision_scores = self.anomaly_detector.decision_function(X) # Lower = more anomalous

        anomalies = []
        for i, pred in enumerate(predictions):
            if pred == -1:
                r = meta_data[i]
                # Scale anomaly severity score from 0% to 100%
                raw_score = float(decision_scores[i])
                severity = round(min(100.0, max(50.0, (0.1 - raw_score) * 200)), 1)

                cause_hint = "Statistical outlier detected"
                if r.gas_type == "H2S" and r.value > 15.0:
                    cause_hint = f"Critical H2S emission spike ({r.value} PPM)"
                elif r.gas_type == "pH" and r.value < 9.5:
                    cause_hint = f"Low pH alkali depletion ({r.value} pH)"
                elif r.gas_type == "VOC" and r.value > 45.0:
                    cause_hint = f"High ambient VOC near statutory cap ({r.value} ug/m3)"

                anomalies.append({
                    "id": r.id,
                    "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S") if r.timestamp else "N/A",
                    "sensor_id": r.sensor_id,
                    "device_name": r.device_name,
                    "gas_type": r.gas_type,
                    "value": r.value,
                    "unit": r.unit,
                    "anomaly_score_pct": severity,
                    "diagnostic_hint": cause_hint
                })

        return {
            "total_inspected": len(readings),
            "anomalies_detected": len(anomalies),
            "algorithm": "Isolation Forest (Unsupervised Ensemble)",
            "anomalies": anomalies[:15] # Top 15 anomalies
        }

    def diagnose_scrubber(self, ph: float, h2s_ppm: float, co_ppm: float, ph_trend: float = 0.0, pump_active: int = 1) -> Dict[str, Any]:
        """
        Diagnoses current scrubber state using the trained Random Forest classifier.
        """
        features = [[ph, h2s_ppm, co_ppm, ph_trend, pump_active]]
        pred_class = int(self.diagnostic_model.predict(features)[0])
        probabilities = self.diagnostic_model.predict_proba(features)[0]
        confidence = round(float(np.max(probabilities)) * 100, 1)

        class_map = {
            0: {
                "diagnosis": "Normal Optimal Neutralization",
                "severity": "Healthy",
                "action": "Maintain standard caustic dosing and periodic sensor inspection."
            },
            1: {
                "diagnosis": "Acid Gas Overload Surge (Elevated H2S)",
                "severity": "Critical",
                "action": "Increase NaOH dosing flow rate immediately; verify column recirculation pump."
            },
            2: {
                "diagnosis": "Caustic Dosing Pump Under-Feed / Trip",
                "severity": "Warning",
                "action": "Inspect NaOH metering pump, check chemical storage level, verify check valves."
            },
            3: {
                "diagnosis": "Excessive Caustic Over-Dosing Hazard",
                "severity": "Warning",
                "action": "Throttle caustic injection pump to avoid column scaling and chemical reagent waste."
            },
            4: {
                "diagnosis": "Sensor Probe Drift / Fouling",
                "severity": "Warning",
                "action": "Clean pH probe with dilute acid buffer; calibrate with pH 7.0 and 10.0 standards."
            }
        }

        result = class_map.get(pred_class, class_map[0])
        return {
            "diagnosis": result["diagnosis"],
            "severity": result["severity"],
            "confidence_pct": confidence,
            "recommended_action": result["action"],
            "inputs_evaluated": {
                "ph": ph,
                "h2s_ppm": h2s_ppm,
                "co_ppm": co_ppm,
                "ph_trend_slope": ph_trend,
                "caustic_pump_active": bool(pump_active)
            }
        }

    def calculate_dosing_recommendation(self, current_ph: float, h2s_ppm: float, gas_flow_m3h: float = 1200.0) -> Dict[str, Any]:
        """
        Calculates recommended NaOH (Sodium Hydroxide 20% solution) dosing rate (L/hr)
        based on stoichiometric neutralization balance:
        H2S + 2NaOH -> Na2S + 2H2O
        """
        target_ph = 11.2 # Optimal neutralization target
        
        # Stoichiometric demand
        # 1 ppm H2S = ~1.5 mg/m3 at standard conditions
        h2s_mass_flow_g_hr = (h2s_ppm * 1.5 * gas_flow_m3h) / 1000.0
        # Moles H2S = mass / 34.08 g/mol; Requires 2 moles NaOH (40.0 g/mol) = 80 g NaOH per 34.08 g H2S -> factor 2.35
        naoh_pure_g_hr = h2s_mass_flow_g_hr * 2.35
        # 20% wt NaOH solution density is ~1.22 kg/L -> 244 g pure NaOH per liter
        base_dosing_l_hr = naoh_pure_g_hr / 244.0

        # pH error feedback correction
        ph_delta = target_ph - current_ph
        correction_factor = 1.0 + max(-0.8, min(2.5, ph_delta * 0.45))
        recommended_flow_l_hr = round(max(0.5, base_dosing_l_hr * correction_factor), 2)

        return {
            "current_ph": current_ph,
            "target_ph": target_ph,
            "incoming_h2s_ppm": h2s_ppm,
            "gas_flow_m3h": gas_flow_m3h,
            "recommended_naoh_flow_l_hr": recommended_flow_l_hr,
            "chemical_basis": "H2S + 2NaOH -> Na2S + 2H2O Stoichiometry + pH Titration Feedback",
            "pump_status_recommendation": "INCREASE" if ph_delta > 0.5 else "MAINTAIN" if abs(ph_delta) <= 0.5 else "DECREASE"
        }

# Singleton instance for imports
ml_engine = IndustrialMLEngine()

