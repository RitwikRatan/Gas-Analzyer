import re
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text
from . import models
from .ml_engine import ml_engine

# Curated CEMS & Scrubber Industrial Knowledge Corpus
KNOWLEDGE_CORPUS = [
    {
        "topic": "H2S Regulatory Thresholds & Risks",
        "keywords": ["h2s", "hydrogen sulfide", "limit", "threshold", "ppm", "toxic", "olfactory"],
        "content": (
            "Hydrogen Sulfide (H2S) is a highly toxic, corrosive byproduct of bulk pharmaceutical synthesis. "
            "Under Central Pollution Control Board (CPCB) and OSHA guidelines: "
            "• Safe / Healthy: < 10.0 PPM. "
            "• Warning Threshold: 10.0 to 19.9 PPM (causes olfactory fatigue and respiratory irritation; advisory inspection required). "
            "• Critical / Hazardous: >= 20.0 PPM (immediate evacuation hazard; triggers emergency plant interlocks)."
        )
    },
    {
        "topic": "Wet Scrubber Chemistry & pH Neutralization",
        "keywords": ["ph", "scrubber", "neutralization", "naoh", "caustic", "alkali", "dosing", "acid"],
        "content": (
            "Packed wet scrubbers neutralize acidic H2S exhaust vapors via alkaline counter-current irrigation with Sodium Hydroxide (NaOH): "
            "Chemical reaction: H2S + 2NaOH -> Na2S + 2H2O. "
            "• Optimal Neutralization Band: 10.0 <= pH <= 12.0 maintains 99%+ gas absorption without reagent waste. "
            "• Low Alkali Warning: pH < 9.5 indicates caustic metering pump under-feed, chemical tank depletion, or acid gas process surge. "
            "• Excessive Caustic Hazard: pH > 12.5 causes column scaling, caustic salt precipitation, and chemical waste."
        )
    },
    {
        "topic": "VOC Stack Emissions Compliance",
        "keywords": ["voc", "volatile", "organic", "stack", "emission", "ug/m3", "cap"],
        "content": (
            "Volatile Organic Compounds (VOCs) are monitored at stack vents using Photoionization Detectors (PID). "
            "Statutory limit under environmental consent: maximum 50 ug/m3. "
            "Breaching 50 ug/m3 exposes the facility to statutory closure notices and requires thermal oxidizer or carbon bed inspection."
        )
    },
    {
        "topic": "Edge Hardware & Modbus RTU Gateway",
        "keywords": ["hardware", "modbus", "rs485", "uniphos", "gateway", "raspberry", "buffer", "serial"],
        "content": (
            "The edge telemetry tier uses a Raspberry Pi running sensor_reader.py. "
            "It connects to Uniphos 500DT gas transmitters via RS485 Modbus RTU at 9600 baud (8-N-1, Slave ID 2, holding register 31). "
            "During network dropouts, an in-memory collections.deque FIFO queue buffers readings and drains chronologically upon server reconnection."
        )
    },
    {
        "topic": "Remediation & Emergency Protocols",
        "keywords": ["remediation", "action", "emergency", "fix", "leak", "maintenance", "alarm", "resolve"],
        "content": (
            "Standard Operating Procedure for Scrubber / CEMS Alerts: "
            "1. For pH < 9.5: Check NaOH day-tank level, verify stroke rate of caustic metering pump, ensure recirculation valve is fully open. "
            "2. For H2S > 10 PPM: Check scrubber inlet gas damper, ramp up caustic feed, verify scrubber differential pressure (packing blockage). "
            "3. For offline sensors: Verify RS485 loop power (24V DC), check USB-to-RS485 adapter, and verify Modbus slave ID termination resistor."
        )
    }
]

class IndustrialRAGCopilot:
    """
    Retrieval-Augmented Generation (RAG) Copilot for SenseMinds Industrial CEMS.
    Grounded with:
      - Live PostgreSQL/SQLite telemetry records (latest values, 24h stats)
      - Real-time ML anomaly detection scores and forecasts
      - CEMS statutory manuals and scrubber chemical engineering knowledge corpus
    """

    def __init__(self):
        pass

    def retrieve_domain_knowledge(self, query: str) -> List[Dict[str, str]]:
        """Retrieves top relevant knowledge chunks matching query semantics and keywords."""
        q_tokens = set(re.findall(r"\w+", query.lower()))
        scored_chunks = []
        for doc in KNOWLEDGE_CORPUS:
            score = sum(1 for kw in doc["keywords"] if kw in q_tokens)
            if score > 0:
                scored_chunks.append((score, doc))

        # Sort by relevance
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        return [c[1] for c in scored_chunks[:3]]

    def retrieve_live_telemetry_context(self, db: Session) -> Dict[str, Any]:
        """Fetches live telemetry snapshot from database."""
        # Get latest reading per sensor
        latest_readings = []
        try:
            # Query the latest reading for each distinct sensor
            sensors = db.query(models.GasReading.sensor_id).distinct().all()
            for (s_id,) in sensors:
                latest = (
                    db.query(models.GasReading)
                    .filter(models.GasReading.sensor_id == s_id)
                    .order_by(models.GasReading.timestamp.desc())
                    .first()
                )
                # If latest is None (e.g. offline/calibration), also fetch last known valid value
                last_valid = (
                    db.query(models.GasReading)
                    .filter(models.GasReading.sensor_id == s_id, models.GasReading.value.isnot(None))
                    .order_by(models.GasReading.timestamp.desc())
                    .first()
                )
                if latest:
                    current_val = latest.value if latest.value is not None else (last_valid.value if last_valid else None)
                    latest_readings.append({
                        "sensor_id": latest.sensor_id,
                        "device_name": latest.device_name,
                        "gas_type": latest.gas_type,
                        "current_value": current_val,
                        "unit": latest.unit,
                        "status": latest.status,
                        "timestamp": latest.timestamp.strftime("%Y-%m-%d %H:%M:%S") if latest.timestamp else "N/A"
                    })
        except Exception as e:
            print(f"[RAG Context Error] {e}")

        # Fetch ML Anomaly snapshot
        ml_anomalies = ml_engine.detect_anomalies(db, limit=50)

        # Fetch H2S forecast
        h2s_forecast = ml_engine.forecast_sensor_telemetry(db, sensor_id=2)

        return {
            "sensors": latest_readings,
            "anomalies": ml_anomalies,
            "forecast": h2s_forecast
        }

    def generate_response(self, query: str, db: Session) -> Dict[str, Any]:
        """
        Executes full RAG workflow:
        1. Semantic Knowledge Retrieval
        2. Live Telemetry & ML State Retrieval
        3. Context-Augmented Grounded Response Synthesis
        """
        lower_q = query.lower()

        # Step 1: Retrieve domain knowledge
        matched_docs = self.retrieve_domain_knowledge(lower_q)
        # Step 2: Retrieve real-time telemetry & ML insights
        telemetry = self.retrieve_live_telemetry_context(db)

        # Find sensor mentions in query
        sensor_mentions = []
        for s in telemetry["sensors"]:
            if str(s["sensor_id"]) in lower_q or s["device_name"].lower() in lower_q or s["gas_type"].lower() in lower_q:
                sensor_mentions.append(s)

        # Step 3: Synthesize grounded response
        response_sections = []
        sources = [d["topic"] for d in matched_docs]

        # Case A: High Risk / Highest Risk / Anomalies Query
        if any(w in lower_q for w in ["risk", "unhealthy", "highest", "anomaly", "critical", "warning", "alarm"]):
            anomalies = telemetry["anomalies"].get("anomalies", [])
            total_anom = telemetry["anomalies"].get("anomalies_detected", 0)
            
            response_sections.append(
                f"### 🚨 Fleet Risk & Telemetry Assessment\n"
                f"The SenseMinds ML Engine scanned the active sensor fleet. "
                f"**{total_anom} anomalous event(s)** were identified by the Isolation Forest."
            )

            if anomalies:
                top_items = anomalies[:3]
                response_sections.append("**Top Flagged Anomalies:**")
                for a in top_items:
                    response_sections.append(
                        f"• **{a['device_name']} ({a['gas_type']})**: Value `{a['value']} {a['unit']}` "
                        f"at {a['timestamp']} — Severity: `{a['anomaly_score_pct']}%` ({a['diagnostic_hint']})"
                    )

            # Check H2S specifically
            h2s_sensor = next((s for s in telemetry["sensors"] if s["gas_type"] == "H2S"), None)
            if h2s_sensor:
                response_sections.append(
                    f"\n**H2S Status**: Current `{h2s_sensor['current_value']} {h2s_sensor['unit']}`. "
                    f"Statutory Warning limit is `< 10.0 PPM`; Emergency stop is `>= 20.0 PPM`."
                )

        # Case B: Specific Sensor / Scrubber Query (e.g. SCB-101, Sensor 2, Uniphos, pH)
        elif sensor_mentions:
            response_sections.append("### 📊 Live Telemetry Report")
            for s in sensor_mentions:
                response_sections.append(
                    f"• **{s['device_name']} (Sensor ID: {s['sensor_id']})**:\n"
                    f"  - Parameter: `{s['gas_type']}` | Current Reading: **`{s['current_value']} {s['unit']}`**\n"
                    f"  - Status: `{s['status']}` (Last polled: {s['timestamp']})"
                )

                # Provide ML diagnostics if it's a scrubber or gas sensor
                if s["gas_type"] == "pH":
                    diag = ml_engine.diagnose_scrubber(ph=s["current_value"] or 11.0, h2s_ppm=6.5, co_ppm=4.5)
                    dose = ml_engine.calculate_dosing_recommendation(current_ph=s["current_value"] or 11.0, h2s_ppm=6.5)
                    response_sections.append(
                        f"  - **ML Scrubber Health Diagnosis**: {diag['diagnosis']} ({diag['confidence_pct']}% confidence)\n"
                        f"  - **Recommended Action**: {diag['recommended_action']}\n"
                        f"  - **Recommended NaOH Dosing**: `{dose['recommended_naoh_flow_l_hr']} L/hr` (Command: `{dose['pump_status_recommendation']}`)"
                    )
                elif s["gas_type"] == "H2S":
                    fc = telemetry["forecast"].get("forecasts", [])
                    if fc:
                        fc_summary = ", ".join([f"{f['horizon']}: `{f['projected_value']} PPM`" for f in fc[:2]])
                        response_sections.append(f"  - **ML Time-Series Forecast**: {fc_summary}")

        # Case C: Emissions Forecast / Prediction Query
        elif any(w in lower_q for w in ["forecast", "predict", "projection", "future", "trend"]):
            fc = telemetry["forecast"]
            response_sections.append(
                f"### 📈 Predictive Emissions Forecast ({fc.get('gas_type', 'H2S')} - {fc.get('device_name', 'Uniphos 500DT')})\n"
                f"**Current Baseline**: `{fc.get('current_value')} {fc.get('unit')}`\n"
                f"**Model**: {fc.get('model', 'Ridge Autoregressive Lag Regressor')}"
            )
            for f in fc.get("forecasts", []):
                response_sections.append(f"• **{f['horizon']}**: `{f['projected_value']} {f['unit']}` — *{f['status']}*")
            response_sections.append(
                "\n*Projections are computed using multi-horizon lag features and rolling rate-of-change derivatives.*"
            )

        # Case D: Scrubber Chemistry / Dosing / General Query
        elif matched_docs:
            response_sections.append(f"### 🧪 Industrial Operations Knowledge")
            for doc in matched_docs:
                response_sections.append(f"**{doc['topic']}**:\n{doc['content']}\n")

            # Add live scrubber context
            ph_sensor = next((s for s in telemetry["sensors"] if s["gas_type"] == "pH"), None)
            if ph_sensor:
                response_sections.append(
                    f"**Live Neutralization Reference**: {ph_sensor['device_name']} is currently reading **`{ph_sensor['current_value']} pH`** "
                    f"(Optimal band: 10.0–12.0 pH)."
                )

        # Default Fallback with live fleet summary
        else:
            response_sections.append(
                "### ℹ️ SenseMinds Environmental Intelligence Copilot\n"
                "I am your industrial AI operations assistant for Laurus Labs Unit 1. "
                "I am actively monitoring live Modbus telemetry, CPCB emission ceilings, and wet scrubber neutralization.\n\n"
                "**Live Fleet Summary:**"
            )
            for s in telemetry["sensors"][:4]:
                response_sections.append(f"• **{s['device_name']}**: `{s['current_value']} {s['unit']}` [{s['status']}]")
            response_sections.append("\n*Try asking: 'Which sensors are at highest risk?', 'Show H2S forecast', or 'What is SCB-101 current pH?'*")

        sources.append("PostgreSQL/SQLite Time-Series Database (gas_readings)")
        sources.append("SenseMinds Isolation Forest & Ridge ML Engine")

        return {
            "query": query,
            "response": "\n\n".join(response_sections),
            "grounding_sources": list(set(sources)),
            "live_sensors_inspected": len(telemetry["sensors"])
        }

rag_copilot = IndustrialRAGCopilot()
