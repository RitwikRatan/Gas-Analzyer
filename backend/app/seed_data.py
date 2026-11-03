import random
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.database import engine, SessionLocal, Base
from app.models import GasReading

def generate_synthetic_data(hours_back: int = 48, step_minutes: int = 15):
    """
    Generates rich, realistic industrial time-series telemetry for Laurus Labs Unit 1.
    Includes normal baselines, periodic diurnal shifts, transient warning spikes, 
    and chemical neutralization events.
    """
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    try:
        # Check current row count
        count = db.query(GasReading).count()
        print(f"Current rows in database: {count}")
        if count > 10:
            print("Clearing old or empty mock rows to populate clean synthetic dataset...")
            db.query(GasReading).delete()
            db.commit()

        readings = []
        now = datetime.now(timezone.utc)
        total_steps = (hours_back * 60) // step_minutes

        print(f"Generating ~{total_steps * 6} realistic time-series points across 6 sensors ({hours_back}h)...")

        for step in range(total_steps, -1, -1):
            t = now - timedelta(minutes=step * step_minutes)
            hour_of_day = t.hour
            minute = t.minute
            
            # Sinusoidal daily fluctuation factor
            cycle_factor = (hour_of_day / 24.0) * 3.14159 * 2
            
            # --- 1. Sensor 1: CO (Carbon Monoxide, PPM) ---
            # Baseline 2.0 - 5.0, shift-change bump around hour 8 and hour 16
            co_base = 3.2 + 1.2 * random.random()
            if 8 <= hour_of_day <= 9 or 16 <= hour_of_day <= 17:
                co_val = co_base + random.uniform(3.5, 7.0) # Occasional bump up to ~11 PPM
            else:
                co_val = co_base + random.uniform(-0.5, 0.8)
            co_val = round(max(0.5, co_val), 2)
            co_status = "ONLINE"

            readings.append(GasReading(
                timestamp=t,
                sensor_id=1,
                device_name="Uniphos Gas Analyzer (CO)",
                gas_type="CO",
                value=co_val,
                unit="PPM",
                status=co_status
            ))

            # --- 2. Sensor 2: H2S (Hydrogen Sulfide, PPM) ---
            # Healthy < 10.0, Warning 10.0-20.0, Critical >= 20.0
            # Introduce an upset episode between 14h and 12h ago, and mild spike 2h ago
            h2s_base = 4.5 + 1.8 * random.random()
            hours_ago = step * step_minutes / 60.0
            
            if 12.0 <= hours_ago <= 13.5:
                # Chemical reaction surge event -> Critical spike
                h2s_val = 19.5 + 4.8 * random.random() # 20.0 - 24.3 PPM (Critical)
            elif 2.0 <= hours_ago <= 3.0:
                # Dosing lag -> Warning bump
                h2s_val = 11.2 + 3.1 * random.random() # 11.2 - 14.3 PPM (Warning)
            else:
                h2s_val = h2s_base + random.uniform(-0.8, 1.2)
            
            h2s_val = round(max(0.8, h2s_val), 2)
            h2s_status = "ONLINE"
            if step % 80 == 0:
                h2s_status = "CALIBRATION"

            readings.append(GasReading(
                timestamp=t,
                sensor_id=2,
                device_name="Uniphos 500DT",
                gas_type="H2S",
                value=h2s_val if h2s_status == "ONLINE" else None,
                unit="PPM",
                status=h2s_status
            ))

            # --- 3. Sensor 3: VOC (Volatile Organic Compounds, ug/m3) ---
            # Statutory stack cap: 50 ug/m3
            voc_base = 26.0 + 8.0 * random.random()
            if 12.0 <= hours_ago <= 13.5:
                voc_val = 48.0 + random.uniform(2.0, 7.5) # Breach ceiling
            else:
                voc_val = voc_base + random.uniform(-2.0, 3.5)
            voc_val = round(max(5.0, voc_val), 2)

            readings.append(GasReading(
                timestamp=t,
                sensor_id=3,
                device_name="PID VOC Stack Monitor",
                gas_type="VOC",
                value=voc_val,
                unit="ug/m3",
                status="ONLINE"
            ))

            # --- 4. Sensor 101: SCB-101 (Scrubber pH) ---
            # Optimal band: 10.0 - 12.0 pH
            # Acid surge dips pH to ~9.3 when H2S surges (13h ago)
            if 12.0 <= hours_ago <= 13.5:
                ph_101 = 9.25 + random.uniform(0.0, 0.4) # Low alkali warning (<9.5)
            elif 13.5 < hours_ago <= 14.5:
                ph_101 = 9.8 + random.uniform(0.1, 0.3)  # Transitioning
            else:
                ph_101 = 11.1 + random.uniform(-0.4, 0.5) # Stable optimal
            ph_101 = round(ph_101, 2)

            readings.append(GasReading(
                timestamp=t,
                sensor_id=101,
                device_name="SCB-101 Neutralization Column",
                gas_type="pH",
                value=ph_101,
                unit="pH",
                status="ONLINE"
            ))

            # --- 5. Sensor 201: SCB-201 (Secondary Scrubber pH) ---
            ph_201 = round(10.8 + random.uniform(-0.3, 0.4), 2)
            readings.append(GasReading(
                timestamp=t,
                sensor_id=201,
                device_name="SCB-201 Secondary Column",
                gas_type="pH",
                value=ph_201,
                unit="pH",
                status="ONLINE"
            ))

            # --- 6. Sensor 301: SCB-301 (Quench Scrubber pH) ---
            # Mild caustic overfeed (~12.6 pH) 22 hours ago
            if 21.0 <= hours_ago <= 22.5:
                ph_301 = 12.65 + random.uniform(0.0, 0.25) # Excessive caustic
            else:
                ph_301 = 10.7 + random.uniform(-0.35, 0.45)
            ph_301 = round(ph_301, 2)

            readings.append(GasReading(
                timestamp=t,
                sensor_id=301,
                device_name="SCB-301 Quench Scrubber",
                gas_type="pH",
                value=ph_301,
                unit="pH",
                status="ONLINE"
            ))

        db.bulk_save_objects(readings)
        db.commit()
        print(f"Successfully populated database with {len(readings)} realistic time-series records!")

    except Exception as e:
        db.rollback()
        print(f"Error seeding synthetic data: {e}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    generate_synthetic_data()

