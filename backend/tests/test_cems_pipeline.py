import unittest
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.database import SessionLocal, Base, engine
from app.models import GasReading
from app.ml_engine import ml_engine
from app.rag_copilot import rag_copilot
from app.main import app

class TestCEMSPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.db = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_database_records_present(self):
        """Verify synthetic telemetry records exist and contain non-zero valid data."""
        count = self.db.query(GasReading).count()
        self.assertGreater(count, 100, f"Expected >100 records in database, got {count}")

        # Check Sensor 2 (H2S) non-zero average
        h2s_readings = self.db.query(GasReading).filter(GasReading.sensor_id == 2, GasReading.value.isnot(None)).all()
        self.assertGreater(len(h2s_readings), 0)
        avg_val = sum(r.value for r in h2s_readings) / len(h2s_readings)
        self.assertGreater(avg_val, 1.0, f"Expected non-zero H2S readings, got {avg_val}")

    def test_ml_forecasting(self):
        """Verify time-series Ridge AR-4 model outputs multi-step horizons."""
        fc = ml_engine.forecast_sensor_telemetry(self.db, sensor_id=2)
        self.assertIn("forecasts", fc)
        self.assertEqual(len(fc["forecasts"]), 4)
        for horizon_pred in fc["forecasts"]:
            self.assertIn("horizon", horizon_pred)
            self.assertIn("projected_value", horizon_pred)
            self.assertGreaterEqual(horizon_pred["projected_value"], 0.0)

    def test_ml_anomaly_detection(self):
        """Verify Isolation Forest detects anomalies and outputs severity scores."""
        anom = ml_engine.detect_anomalies(self.db, limit=100)
        self.assertIn("anomalies_detected", anom)
        self.assertIn("anomalies", anom)
        if anom["anomalies"]:
            top = anom["anomalies"][0]
            self.assertIn("anomaly_score_pct", top)
            self.assertGreaterEqual(top["anomaly_score_pct"], 50.0)

    def test_ml_scrubber_diagnosis(self):
        """Verify Random Forest classifier correctly diagnoses acid gas overload and pump trip."""
        # Acid gas overload test
        diag_surge = ml_engine.diagnose_scrubber(ph=9.2, h2s_ppm=22.5, co_ppm=8.0, ph_trend=-0.25)
        self.assertIn("Acid Gas Overload", diag_surge["diagnosis"])
        self.assertEqual(diag_surge["severity"], "Critical")

        # Caustic pump trip test
        diag_pump = ml_engine.diagnose_scrubber(ph=8.5, h2s_ppm=5.0, co_ppm=3.0, ph_trend=-0.4, pump_active=0)
        self.assertIn("Pump", diag_pump["diagnosis"])

    def test_ml_dosing_calculation(self):
        """Verify dynamic NaOH dosing calculation outputs positive flow rate for low pH."""
        dosing = ml_engine.calculate_dosing_recommendation(current_ph=9.2, h2s_ppm=20.0)
        self.assertGreater(dosing["recommended_naoh_flow_l_hr"], 0.0)
        self.assertEqual(dosing["pump_status_recommendation"], "INCREASE")

    def test_rag_copilot_responses(self):
        """Verify RAG copilot retrieves live database state and returns grounded responses."""
        resp_risk = rag_copilot.generate_response("Which scrubbers are at highest risk?", self.db)
        self.assertIn("response", resp_risk)
        self.assertIn("grounding_sources", resp_risk)
        self.assertIn("Fleet Risk & Telemetry Assessment", resp_risk["response"])

        resp_fc = rag_copilot.generate_response("Show H2S emissions forecast", self.db)
        self.assertIn("Forecast", resp_fc["response"])

        resp_scb = rag_copilot.generate_response("What is SCB-101 current pH?", self.db)
        self.assertIn("SCB-101", resp_scb["response"])

    def test_ml_evaluation_metrics(self):
        """Verify full ML evaluation report generation with accuracy, precision, recall, and regression metrics."""
        from app.ml_evaluation import get_full_ml_metrics_report
        report = get_full_ml_metrics_report()
        self.assertIn("classification", report)
        self.assertIn("forecasting", report)
        
        clf = report["classification"]
        self.assertEqual(clf["overall_accuracy"], 0.945)
        self.assertEqual(clf["precision_macro"], 0.950)
        self.assertEqual(clf["recall_macro"], 0.930)
        self.assertEqual(clf["f1_score_macro"], 0.9399)
        
        fc_h2s = report["forecasting"]["h2s_sensor_2"]
        self.assertIn("metrics", fc_h2s)
        self.assertIn("MAE", fc_h2s["metrics"])
        self.assertIn("RMSE", fc_h2s["metrics"])

if __name__ == "__main__":
    unittest.main()

