from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List, Union, Dict, Any, Optional
from pydantic import BaseModel
from . import models, schemas
from .database import engine, get_db
from .ml_engine import ml_engine
from .rag_copilot import rag_copilot
from .ml_evaluation import get_full_ml_metrics_report

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="SenseMinds Gas Analyzer & CEMS Telemetry API",
    description="High-concurrency CEMS telemetry ingestion, industrial ML forecasting, and RAG copilot services.",
    version="5.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic schemas for Copilot & ML requests
class CopilotQueryRequest(BaseModel):
    query: str

class ScrubberDiagnoseRequest(BaseModel):
    ph: float
    h2s_ppm: float
    co_ppm: float
    ph_trend: Optional[float] = 0.0
    pump_active: Optional[int] = 1

class DosingCalculationRequest(BaseModel):
    current_ph: float
    h2s_ppm: float
    gas_flow_m3h: Optional[float] = 1200.0

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    print(f"\n--- 422 VALIDATION ERROR ---")
    print(f"Request body: {body}")
    print(f"Validation errors: {exc.errors()}")
    print(f"----------------------------\n")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body.decode("utf-8") if body else ""},
    )

@app.get("/")
def read_root():
    return {
        "platform": "SenseMinds Industrial Environmental Intelligence",
        "system": "Automated CEMS and Scrubber Telemetry Platform",
        "facility": "Laurus Labs Pharmaceutical Unit 1 Operations",
        "documentation": "/docs"
    }

# -------------------------------------------------------------
# 1. Telemetry Ingestion & Retrieval Endpoints
# -------------------------------------------------------------
@app.post("/readings/")
def create_reading(payload: Union[schemas.GasReadingCreate, Dict[str, List[schemas.GasReadingCreate]]], db: Session = Depends(get_db)):
    if isinstance(payload, dict) and "readings" in payload:
        created_readings = []
        for r in payload["readings"]:
            db_reading = models.GasReading(
                sensor_id=r.sensor_id,
                device_name=r.device_name,
                gas_type=r.gas_type,
                value=r.value,
                unit=r.unit,
                status=r.status
            )
            if r.timestamp:
                db_reading.timestamp = r.timestamp
            db.add(db_reading)
            created_readings.append(db_reading)
        db.commit()
        for r in created_readings:
            db.refresh(r)
        return {"status": "success", "count": len(created_readings)}
    else:
        reading = payload
        db_reading = models.GasReading(
            sensor_id=reading.sensor_id,
            device_name=reading.device_name,
            gas_type=reading.gas_type,
            value=reading.value,
            unit=reading.unit,
            status=reading.status
        )
        if reading.timestamp:
            db_reading.timestamp = reading.timestamp
        db.add(db_reading)
        db.commit()
        db.refresh(db_reading)
        return db_reading

@app.get("/readings/", response_model=List[schemas.GasReadingResponse])
def get_readings(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    readings = db.query(models.GasReading).order_by(models.GasReading.timestamp.desc()).offset(skip).limit(limit).all()
    return readings

# -------------------------------------------------------------
# 2. Machine Learning Endpoints
# -------------------------------------------------------------
@app.get("/analytics/forecast/{sensor_id}")
def get_sensor_forecast(sensor_id: int, db: Session = Depends(get_db)):
    """Generates multi-step time-series forecasting (+30m, +1h, +4h) for a sensor."""
    return ml_engine.forecast_sensor_telemetry(db, sensor_id=sensor_id)

@app.get("/analytics/anomalies")
def get_detected_anomalies(limit: int = 100, db: Session = Depends(get_db)):
    """Runs Isolation Forest unsupervised anomaly detection over recent telemetry."""
    return ml_engine.detect_anomalies(db, limit=limit)

@app.post("/analytics/diagnose")
def diagnose_scrubber_condition(payload: ScrubberDiagnoseRequest):
    """Diagnoses root causes for scrubber issues using Random Forest classification."""
    return ml_engine.diagnose_scrubber(
        ph=payload.ph,
        h2s_ppm=payload.h2s_ppm,
        co_ppm=payload.co_ppm,
        ph_trend=payload.ph_trend,
        pump_active=payload.pump_active
    )

@app.post("/analytics/dosing")
def calculate_naoh_dosing(payload: DosingCalculationRequest):
    """Calculates stoichiometric NaOH neutralizing dosing rate in L/hr."""
    return ml_engine.calculate_dosing_recommendation(
        current_ph=payload.current_ph,
        h2s_ppm=payload.h2s_ppm,
        gas_flow_m3h=payload.gas_flow_m3h
    )

@app.get("/analytics/metrics")
def get_model_evaluation_metrics():
    """
    Returns comprehensive evaluation metrics:
    - Classification: Accuracy, Precision, Recall, F1-Score, Confusion Matrix, 5-Fold Cross Validation.
    - Time-Series Forecasting: MAE, RMSE, MSE, R2 Score, MAPE.
    """
    return get_full_ml_metrics_report()

# -------------------------------------------------------------
# 3. RAG Copilot Endpoint
# -------------------------------------------------------------
@app.post("/copilot/query")
def copilot_query(payload: CopilotQueryRequest, db: Session = Depends(get_db)):
    """
    Retrieval-Augmented Generation (RAG) assistant:
    Retrieves live database telemetry, ML state, and CEMS regulatory corpus
    to synthesize actionable, grounded operator responses.
    """
    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query string cannot be empty.")
    
    return rag_copilot.generate_response(payload.query, db)
