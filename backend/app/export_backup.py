import sqlite3
from pathlib import Path

db_file = Path(__file__).resolve().parent.parent / "gas_analyzer.db"
out_file = Path(__file__).resolve().parent.parent / "backup.sql"

conn = sqlite3.connect(db_file)
cursor = conn.cursor()
cursor.execute("SELECT id, timestamp, sensor_id, device_name, gas_type, value, unit, status FROM gas_readings ORDER BY id")
rows = cursor.fetchall()

header = """-- PostgreSQL database dump (Updated with realistic continuous synthetic telemetry data)
SET statement_timeout = 0;
SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS public.gas_readings (
    id integer NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now(),
    sensor_id integer,
    device_name character varying,
    gas_type character varying,
    value double precision,
    unit character varying,
    status character varying,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_gas_readings_id ON public.gas_readings USING btree (id);
CREATE INDEX IF NOT EXISTS ix_gas_readings_sensor_id ON public.gas_readings USING btree (sensor_id);
CREATE INDEX IF NOT EXISTS ix_gas_readings_timestamp ON public.gas_readings USING btree ("timestamp");

INSERT INTO public.gas_readings (id, "timestamp", sensor_id, device_name, gas_type, value, unit, status) VALUES
"""

values = []
for r in rows:
    val_str = f"{r[5]}" if r[5] is not None else "NULL"
    dev_name = r[3].replace("'", "''")
    values.append(f"({r[0]}, '{r[1]}', {r[2]}, '{dev_name}', '{r[4]}', {val_str}, '{r[6]}', '{r[7]}')")

content = header + ",\n".join(values) + ";\n"
content += f"\nSELECT pg_catalog.setval('public.gas_readings_id_seq', {len(rows)}, true);\n"

out_file.write_text(content, encoding="utf-8")
print(f"Exported {len(rows)} records into {out_file.name}!")

